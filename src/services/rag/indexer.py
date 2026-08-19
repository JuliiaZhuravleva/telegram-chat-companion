"""Chunk indexer -- background task that keeps `chat_chunks` current (S4).

Two jobs, deliberately in one worker because they are two halves of one
pipeline: turn saved messages into chunks, then give the chunks vectors.

**Why this is not part of `EmbeddingBackfillWorker`.** That worker exists to
*repair* rows whose embedding call failed at write time; here embedding is the
normal path, not the exception, and it carries a `task_type` that worker has no
notion of. Sharing them would mean one queue where a chat_memory repair waits
behind two thousand backfill chunks.

**Chat-wide, not per thread (measured 2026-08-19).** The plan sessioned by
`(chat_id, thread_id)` on the assumption that `chat_messages.message_thread_id`
identifies a forum topic. On production it does not: every chat averages
2.0-2.7 messages per distinct value and ~70% of messages carry none, because
Telegram sets it on ordinary reply chains in a supergroup too -- the largest
chat has 3737 of them, none of which is a topic. Sessioning by it would cut a
conversation into two-message fragments and separate a reply from the message
it answers. The `thread_id` column stays in `chat_chunks`, written as NULL,
for when a real forum appears and there is a way to recognise one.

**Debounce by design, rather than a cleanup pass.** A session is only chunked
once it can no longer grow -- its last message older than `SESSION_PAUSE`.
Chunking an open session would produce a chunk whose `(msg_from, msg_to)`
changes as the conversation continues, and the natural key would then admit
both the stale and the extended version. Waiting removes the class of bug
instead of collecting its output.

**The watermark is derived, not stored.** `MAX(msg_to)` per chat and thread
comes from the index itself, so there is no state that can disagree with what
was actually written; a crash mid-backfill resumes exactly where the rows
stop, and a manually deleted chunk is simply rebuilt.

**Asymmetric embeddings (measured 2026-08-19).** Chunks are embedded with
`task_type=RETRIEVAL_DOCUMENT`. Omitting `task_type` on gemini-embedding-001
returns a byte-identical vector to `RETRIEVAL_QUERY` -- verified by comparing
both responses for one text -- which means the query side already asks for
`RETRIEVAL_QUERY` implicitly and needs no change at S5, and it also means
`chat_memory`'s existing vectors *are* a `RETRIEVAL_QUERY` index that must
never be flipped. On one realistic chunk/question pair the asymmetric pairing
raised the true match from 0.6713 to 0.6922 while an unrelated question moved
0.5169 -> 0.5242, i.e. a slightly wider gap; one pair is not an eval, so this
is a reason to prefer it, not a measured win.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import asyncpg
import structlog

from src.config import ChunkIndexerSettings
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.chunks import ChunkRepository
from src.database.repositories.messages import MessageRepository
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter
from src.services.chat_config import ChatConfigService
from src.services.rag.chunker import SESSION_PAUSE, build_chunks, source_messages, split_sessions
from src.services.rag.memory import EXPECTED_EMBEDDING_DIMENSIONS
from src.services.rag.models import SourceMessage

logger = structlog.get_logger(__name__)

_INITIAL_DELAY = 240  # after the other workers; nothing here is user-facing

# See the module docstring: this is the half of the asymmetry that has to be
# declared, because the query half is the API's default.
INDEX_TASK_TYPE = "RETRIEVAL_DOCUMENT"

# Consecutive embedding failures after which a chunk is parked, so one row the
# provider always rejects cannot block a FIFO queue for ever. Same reasoning
# and same number as EmbeddingBackfillWorker; parking is per-process, so a
# restart gives every parked row another chance.
_MAX_ATTEMPTS = 3


class ChatChunkIndexer:
    """Background task: `chat_messages` -> `chat_chunks` -> embeddings.

    Process-lifetime, not Dishka-managed (CLAUDE.md's "Process-lifetime
    singletons via dp[]" ADR), and not in `dp[]` either -- no handler calls it.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        ai_router: AIRouter,
        chat_config: ChatConfigService,
        config: ChunkIndexerSettings,
    ) -> None:
        self._pool = pool
        self._ai_router = ai_router
        self._chat_config = chat_config
        self._config = config
        self._task: asyncio.Task[None] | None = None
        self._failures: dict[int, int] = {}
        self._parked: set[int] = set()
        # Consecutive passes that had rows to embed and embedded none. Reset on
        # any progress; see `_embed_pending`.
        self._stalled_passes = 0

    async def start(self) -> None:
        """Start the indexing loop (no-op when disabled by config)."""
        if not self._config.enabled:
            logger.info("Chunk indexer disabled by config")
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Chunk indexer started",
            interval_seconds=self._config.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the indexing loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Chunk indexer stopped")

    async def run_once(self) -> dict[str, int]:
        """One pass: chunk what closed, embed what is pending.

        Returns counters rather than logging only, so the backfill script and
        the tests can assert on progress instead of on log lines.
        """
        chunks_written = 0
        chats_indexed = 0

        settings_repo = ChatSettingsRepository(self._pool)
        messages_repo = MessageRepository(self._pool)
        # The list of chats comes from the table this worker actually reads;
        # titles come from settings, when there are any. Enumerating settings
        # conflated the two, and a chat whose settings row had moved away from
        # its messages -- what a group->supergroup upgrade does -- stopped
        # being indexed for ever (TD-104; see `MessageRepository.list_chat_ids`
        # for the mechanism and the deadline). A chat with settings but no
        # messages loses nothing by being absent here: it would produce no
        # chunk either way.
        titles = {int(row["chat_id"]): row["chat_title"] for row in await settings_repo.list_all()}
        for chat_id in await messages_repo.list_chat_ids():
            try:
                # The gate is `save_messages`, not the whitelist: a chat
                # disabled after years of saved history still owns that
                # history, and freezing its index at the moment of a whitelist
                # change would be a silent, unrecoverable edit to the bot's
                # memory. For a chat with no settings row the merge resolves
                # to the global default, which is the same answer the messages
                # themselves were written under.
                config = await self._chat_config.get_config(chat_id)
            except Exception:
                logger.exception("Chunk indexer could not resolve chat config", chat_id=chat_id)
                continue
            if not config.save_messages:
                continue
            if chat_id not in titles:
                # An id with messages and no settings row is a chat whose row
                # was re-keyed away from it -- the TD-104 case. Say so, every
                # pass, because it is the one chat whose gate is NOT the
                # owner's own setting (that travelled to the new id) but the
                # global default, and there is no admin screen that shows it.
                # TD-113 covers making it governable; until then this line is
                # the only way to notice it exists.
                logger.info(
                    "Indexing a chat with no settings row -- gated by the global default",
                    chat_id=chat_id,
                    save_messages=config.save_messages,
                )
            try:
                written = await self._index_chat(chat_id, titles.get(chat_id))
            except Exception:
                # One unhealthy chat must not stall every other chat's index,
                # and the next pass retries it from the same watermark.
                logger.exception("Chunk indexing failed for chat", chat_id=chat_id)
                continue
            chats_indexed += 1
            chunks_written += written

        embedded = await self._embed_pending()

        # Gated on there being something to say -- but a *stalled* pass has
        # something to say, and used to be indistinguishable from a
        # caught-up one (both log nothing at all).
        if chunks_written or embedded or self._stalled_passes:
            logger.info(
                "Chunk indexer pass complete",
                chats=chats_indexed,
                chunks_written=chunks_written,
                embedded=embedded,
                stalled_passes=self._stalled_passes,
            )
        return {"chats": chats_indexed, "chunks": chunks_written, "embedded": embedded}

    async def _index_chat(self, chat_id: int, chat_title: str | None) -> int:
        messages_repo = MessageRepository(self._pool)
        chunks_repo = ChunkRepository(self._pool)

        rows = await messages_repo.get_for_chunking(
            chat_id,
            after_message_id=await chunks_repo.watermark(chat_id),
            limit=self._config.messages_per_pass,
        )
        if not rows:
            return 0

        messages = source_messages(rows)
        if not messages:
            return 0

        sessions = _closed_sessions(
            split_sessions(messages),
            batch_full=len(rows) >= self._config.messages_per_pass,
        )
        indexable = [message for session in sessions for message in session]
        if not indexable:
            return 0

        chunks = build_chunks(indexable, chat_id=chat_id, thread_id=None, chat_title=chat_title)
        return await chunks_repo.insert_many(chunks)

    async def _embed_pending(self) -> int:
        """Give vectors to chunks that have none, oldest first.

        No transaction is held across an API call: rows are read, the provider
        is called, and each result is written on its own. A pass that dies
        halfway leaves the rest pending, which is the same state it started in.
        """
        repo = ChunkRepository(self._pool)
        pending = await repo.get_pending_embeddings(
            self._config.embed_per_pass, exclude_ids=sorted(self._parked)
        )
        embedded = 0
        failed: list[int] = []

        for row in pending:
            chunk_id = int(row["id"])
            try:
                result = await self._ai_router.generate_embedding(
                    row["content"],
                    chat_id=int(row["chat_id"]),
                    task_type=INDEX_TASK_TYPE,
                )
            except AIProviderError as exc:
                logger.warning(
                    "Chunk embedding failed",
                    chunk_id=chunk_id,
                    error=str(exc),
                    retriable=exc.retriable,
                )
                if not exc.retriable:
                    failed.append(chunk_id)
                continue

            if len(result.embedding) != EXPECTED_EMBEDDING_DIMENSIONS:
                # A wrong-width vector cannot be stored (the column is
                # vector(768)) and would raise on every retry -- park it after
                # the usual attempts rather than fail the pass.
                logger.error(
                    "Chunk embedding has unexpected dimensions",
                    chunk_id=chunk_id,
                    dimensions=len(result.embedding),
                )
                failed.append(chunk_id)
                continue

            await repo.update_embedding(
                chunk_id,
                result.embedding,
                model=result.model,
                task_type=INDEX_TASK_TYPE,
            )
            self._failures.pop(chunk_id, None)
            embedded += 1

        self._account_for_failures(failed, batch=len(pending))

        # A pass that had work and completed none of it is the shape a
        # sustained quota outage now takes: nothing is parked (correctly --
        # the rows are healthy), so the queue never drains and never shrinks.
        # Without this counter the only trace is one WARNING per row, which
        # looks exactly like a transient blip repeated, and the pass summary
        # below is gated on progress so it does not fire at all.
        if pending and embedded == 0:
            self._stalled_passes += 1
            logger.warning(
                "Chunk indexer embedded nothing this pass",
                pending_in_batch=len(pending),
                consecutive_stalled_passes=self._stalled_passes,
                parked=len(self._parked),
            )
        else:
            self._stalled_passes = 0
        return embedded

    def _account_for_failures(self, failed: list[int], *, batch: int) -> None:
        """Count failures toward parking -- unless they were passing conditions.

        Parking exists for a row the provider will never accept, so that one
        such row cannot sit at the head of a FIFO queue for ever. What must
        never be parked is a healthy row that happened to be in flight during
        an outage.

        The first version of this told the two apart by counting: charge a
        failure unless *every* row in the batch failed. That reasoning assumed
        an outage is all-or-nothing, and a per-minute quota is not -- it trips
        partway through, so the earlier rows succeed and the later ones fail,
        `len(failed) < batch`, and every healthy row behind the limit is
        charged. Measured 2026-08-19 backfilling a 2841-chunk corpus on the
        free tier: **58 healthy chunks parked** across two runs, every one
        behind a rate limit -- neither run produced a failure of any other
        kind. Parking is in-process state and `chat_chunks` is exempt from
        retention, so those rows stay unembedded until someone restarts the
        bot, with nothing raised and nothing logged beyond a per-chunk warning.

        (An earlier version of this note said 1859. That was the *pending*
        count at the moment the backfill stalled -- a different quantity, and
        the stall was the day's quota running out, not the parking. The number
        reached three docstrings and a public document before being
        re-measured.)

        Now the provider answers the question instead of the arithmetic:
        `AIProviderError.retriable` is set by `RateLimitError` and propagated
        through the router's fallback, and a retriable failure never reaches
        this function. A wrong-width vector and a permanently-refused input
        still do, so the starvation guard keeps working.

        The whole-batch check below is kept as a second net for a provider
        failure that arrives without the flag: it costs nothing and it is the
        one shape that is unambiguous.
        """
        if not failed:
            return
        if batch > 1 and len(failed) == batch:
            logger.warning(
                "Every chunk in the pass failed to embed -- treating as an outage, not as bad rows",
                batch=batch,
            )
            return
        for chunk_id in failed:
            self._record_failure(chunk_id)

    def _record_failure(self, chunk_id: int) -> None:
        attempts = self._failures.get(chunk_id, 0) + 1
        self._failures[chunk_id] = attempts
        if attempts >= _MAX_ATTEMPTS:
            self._parked.add(chunk_id)
            logger.warning(
                "Chunk parked after repeated embedding failures",
                chunk_id=chunk_id,
                attempts=attempts,
            )

    async def _run_loop(self) -> None:
        await asyncio.sleep(_INITIAL_DELAY)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Chunk indexer pass failed")
            await asyncio.sleep(self._config.interval_seconds)


def _closed_sessions(
    sessions: list[list[SourceMessage]],
    *,
    batch_full: bool,
) -> list[list[SourceMessage]]:
    """Drop the trailing session while it can still grow.

    Two ways it can grow: the conversation is simply still going (its last
    message is younger than `SESSION_PAUSE`), or this batch hit its row limit
    and the rest of the session is in the database, unread.

    The one exception keeps the indexer from live-locking: when a single
    session fills the whole batch there is no earlier session to fall back on,
    and skipping it would mean skipping it for ever. It is indexed as far as
    the batch goes; the next pass resumes after the last chunk written, so the
    content is complete and only the seam is placed by the batch boundary
    instead of by a pause.
    """
    if not sessions:
        return []
    last = sessions[-1]
    still_open = _is_open(last)
    if not still_open and not batch_full:
        return sessions
    if len(sessions) > 1:
        return sessions[:-1]
    return sessions if batch_full else []


def _is_open(session: list[SourceMessage]) -> bool:
    """Whether the session can still grow, judged by its LATEST moment.

    `session[-1]` is the highest `message_id`, not the newest message, and the
    two disagree on 1.8% of adjacent production pairs (PR #52). Reading the
    last row's timestamp therefore gets it wrong in both directions: one stale
    row makes a minute-old conversation look closed, so the debounce this
    function exists for is defeated and the chunk seam lands mid-sentence; one
    row dated in the future defers the chat's tail on every pass for as long as
    it holds the highest id. `split_sessions` already measures gaps against the
    latest moment seen -- this is the same rule, so the two agree on where a
    session ends.
    """
    now = datetime.now(UTC)
    latest = max(
        moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
        for moment in (message.created_at for message in session)
    )
    return now - latest <= SESSION_PAUSE
