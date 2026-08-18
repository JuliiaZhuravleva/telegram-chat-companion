"""Chunk indexer -- background task that keeps `chat_chunks` current (S4).

Two jobs, deliberately in one worker because they are two halves of one
pipeline: turn saved messages into chunks, then give the chunks vectors.

**Why this is not part of `EmbeddingBackfillWorker`.** That worker exists to
*repair* rows whose embedding call failed at write time; here embedding is the
normal path, not the exception, and it carries a `task_type` that worker has no
notion of. Sharing them would mean one queue where a chat_memory repair waits
behind two thousand backfill chunks.

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
        for row in await settings_repo.list_all():
            chat_id = int(row["chat_id"])
            try:
                config = await self._chat_config.get_config(chat_id)
            except Exception:
                logger.exception("Chunk indexer could not resolve chat config", chat_id=chat_id)
                continue
            if not config.save_messages:
                continue
            try:
                written = await self._index_chat(chat_id, row["chat_title"])
            except Exception:
                # One unhealthy chat must not stall every other chat's index,
                # and the next pass retries it from the same watermark.
                logger.exception("Chunk indexing failed for chat", chat_id=chat_id)
                continue
            chats_indexed += 1
            chunks_written += written

        embedded = await self._embed_pending()

        if chunks_written or embedded:
            logger.info(
                "Chunk indexer pass complete",
                chats=chats_indexed,
                chunks_written=chunks_written,
                embedded=embedded,
            )
        return {"chats": chats_indexed, "chunks": chunks_written, "embedded": embedded}

    async def _index_chat(self, chat_id: int, chat_title: str | None) -> int:
        messages_repo = MessageRepository(self._pool)
        chunks_repo = ChunkRepository(self._pool)

        watermarks = await chunks_repo.watermarks(chat_id)
        written = 0

        for thread_id in await messages_repo.get_thread_ids(chat_id):
            after = watermarks.get(thread_id, 0)
            rows = await messages_repo.get_for_chunking(
                chat_id,
                thread_id=thread_id,
                after_message_id=after,
                limit=self._config.messages_per_pass,
            )
            if not rows:
                continue

            messages = source_messages(rows)
            if not messages:
                continue

            sessions = _closed_sessions(
                split_sessions(messages),
                batch_full=len(rows) >= self._config.messages_per_pass,
            )
            indexable = [message for session in sessions for message in session]
            if not indexable:
                continue

            chunks = build_chunks(
                indexable, chat_id=chat_id, thread_id=thread_id, chat_title=chat_title
            )
            written += await chunks_repo.insert_many(chunks)

        return written

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

        for row in pending:
            chunk_id = int(row["id"])
            try:
                result = await self._ai_router.generate_embedding(
                    row["content"],
                    chat_id=int(row["chat_id"]),
                    task_type=INDEX_TASK_TYPE,
                )
            except AIProviderError as exc:
                # Almost always the whole provider being down, which fails
                # every row in the batch equally; the next pass retries.
                logger.warning("Chunk embedding failed", chunk_id=chunk_id, error=str(exc))
                self._record_failure(chunk_id)
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
                self._record_failure(chunk_id)
                continue

            await repo.update_embedding(
                chunk_id,
                result.embedding,
                model=result.model,
                task_type=INDEX_TASK_TYPE,
            )
            self._failures.pop(chunk_id, None)
            embedded += 1

        return embedded

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
    last = session[-1].created_at
    now = datetime.now(UTC)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last <= SESSION_PAUSE
