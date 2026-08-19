"""Embedding backfill worker — background task (S2-10).

``RAGMemoryService.store()`` no longer drops a memory outright when
embedding generation fails (S2-1's honest no-fallback for embeddings means
any Gemini outage hits this path): it persists the content with
``embedding = NULL`` instead (see ``src/services/rag/memory.py``), so the
data-preservation invariant (S2-11 -- memory rows must not be lost) survives
a provider outage. This worker is what turns that NULL back into a real
vector once the provider recovers.

Pattern follows RetentionCleaner (src/services/maintenance/cleanup.py): a
process-lifetime background task owned by main(), not by Dishka (see
CLAUDE.md's "Process-lifetime singletons via dp[], not Dishka" ADR), with a
``run_once()`` that is unit-testable on its own and a start()/stop() loop
that wraps it. Embeddings (gemini-embedding-001) are free, so there is no
*cost* pressure to bound attempts and a transient outage is simply retried
next pass.

There is still a cap, for a different reason: the queue is FIFO. A row that
fails deterministically -- content the model always rejects, a lasting
wrong-dimension response -- would otherwise sit at the head of every pass
forever, and once ``batch_limit`` of them accumulate nothing written later
is ever reached (ADR-0011 keeps this table out of retention, so they cannot
age out either). After ``_MAX_ATTEMPTS`` consecutive failures a row is
*parked*: excluded from the query so the backlog moves on, and named in a
warning so it is visible rather than silently abandoned. Parking is
per-process state, not a DB column -- deliberately, because this table is
retired in S5-S6 and a migration for a stopgap is not worth a one-way door.
A restart therefore gives every parked row one more chance, which is the
behaviour you want if the cause was environmental.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg
import structlog

from src.config import EmbeddingBackfillSettings
from src.database.repositories.knowledge import KnowledgeRepository
from src.database.repositories.memory import MemoryRepository
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter
from src.services.rag.memory import EXPECTED_EMBEDDING_DIMENSIONS

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _Source:
    """One table this worker repairs.

    Added when `chat_facts` joined `chat_memory` here (plan KB-04): a manual
    KB fact whose embedding call failed is invisible to retrieval forever,
    which is the same defect this worker already existed to fix — so it got a
    second source rather than a second worker.
    """

    name: str
    text_field: str
    fetch: Callable[..., Awaitable[list[asyncpg.Record]]]
    update: Callable[[int, list[float]], Awaitable[None]]


_INITIAL_DELAY = 180  # let startup settle before the first pass

# Consecutive failures after which a row is parked (see module docstring).
# Small on purpose: the point is to stop one bad row blocking the queue.
#
# The second half of that sentence used to read "and a genuine provider outage
# fails every row in the batch equally, so parking during an outage costs only
# the next restart's retry". Both halves were wrong, and measuring beat
# reasoning here (2026-08-19): a per-minute quota does NOT fail every row --
# it trips partway through, so the earlier rows succeed and the healthy rows
# behind the limit are the ones charged. And "only the next restart" is a long
# time in a process that runs for weeks, over tables that are exempt from
# retention. Rows parked that way stay `embedding IS NULL`, invisible to
# retrieval, with nothing raised.
#
# So a failure is only charged when the provider says it is not worth
# retrying; see `_process`.
_MAX_ATTEMPTS = 3


class EmbeddingBackfillWorker:
    """Background task that retries embeddings for pending chat_memory rows.

    Not managed by Dishka — lives for the entire bot process. Receives pool
    and the AI router directly from main(), mirroring RetentionCleaner and
    StickerSetSyncScheduler.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        ai_router: AIRouter,
        config: EmbeddingBackfillSettings,
    ) -> None:
        self._pool = pool
        self._ai_router = ai_router
        self._config = config
        self._task: asyncio.Task[None] | None = None
        # (source, row_id) -> consecutive failures; a row graduates to
        # `_parked` at `_MAX_ATTEMPTS` and is then excluded from the query.
        # Keyed by SOURCE as well as id: `chat_memory` row 5 and `chat_facts`
        # row 5 are different rows, and a bare-int key would park one because
        # the other failed.
        self._failures: dict[tuple[str, int], int] = {}
        self._parked: set[tuple[str, int]] = set()

    def _sources(self) -> tuple[_Source, ...]:
        memory = MemoryRepository(self._pool)
        knowledge = KnowledgeRepository(self._pool)
        return (
            _Source(
                "chat_memory", "content", memory.get_pending_embeddings, memory.update_embedding
            ),
            _Source(
                "chat_facts",
                "fact_text",
                knowledge.get_pending_embeddings,
                knowledge.update_embedding,
            ),
        )

    def _parked_ids(self, source: str) -> list[int]:
        return sorted(row_id for src, row_id in self._parked if src == source)

    async def start(self) -> None:
        """Start the backfill loop (no-op when disabled by config)."""
        if not self._config.enabled:
            logger.info("Embedding backfill worker disabled by config")
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Embedding backfill worker started",
            interval_seconds=self._config.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the backfill loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Embedding backfill worker stopped")

    def _record_failure(self, source: str, row_id: int) -> None:
        """Count a failed attempt and park the row once it hits the cap."""
        key = (source, row_id)
        attempts = self._failures.get(key, 0) + 1
        self._failures[key] = attempts
        if attempts >= _MAX_ATTEMPTS:
            self._parked.add(key)
            self._failures.pop(key, None)
            logger.warning(
                "Embedding backfill: parking row after repeated failures — it will "
                "no longer be retried until the process restarts, so the rest of "
                "the backlog can proceed",
                source=source,
                row_id=row_id,
                attempts=attempts,
                parked_total=len(self._parked),
            )

    async def run_once(self) -> dict[str, int]:
        """Run a single backfill pass. Returns counts of filled/still_pending rows.

        Each row is independent: a provider failure or a wrong-dimension
        result on one row leaves it NULL and does not stop the rest of the
        batch. After `_MAX_ATTEMPTS` consecutive failures the row is parked
        (see module docstring) so a deterministically-failing row cannot hold
        the head of a FIFO queue forever.
        """
        filled = 0
        still_pending = 0

        for source in self._sources():
            # `batch_limit` is applied PER SOURCE, not shared. A shared budget
            # would let a long `chat_memory` backlog starve `chat_facts`
            # indefinitely, and a stranded fact is the more visible failure:
            # the user can see it in /kb while the bot can never retrieve it.
            #
            # Each source is also isolated: `_run_loop` only catches around the
            # WHOLE pass, so before this guard a failing fetch on the first
            # source aborted the pass and the second never ran — permanently
            # starving `chat_facts`, which is ordered second and is precisely
            # the source added to fix stranded facts.
            try:
                pending = await source.fetch(
                    limit=self._config.batch_limit,
                    exclude_ids=self._parked_ids(source.name),
                )
                source_filled, source_pending = await self._process(source, pending)
            except Exception:
                logger.exception(
                    "Embedding backfill: source failed, continuing with the others",
                    source=source.name,
                )
                continue
            filled += source_filled
            still_pending += source_pending

        if filled or still_pending:
            logger.info(
                "Embedding backfill pass complete",
                filled=filled,
                still_pending=still_pending,
                parked=len(self._parked),
            )

        return {"filled": filled, "still_pending": still_pending}

    async def _process(self, source: _Source, pending: list[asyncpg.Record]) -> tuple[int, int]:
        """Embed and persist one source's batch. Returns (filled, still_pending)."""
        filled = 0
        still_pending = 0

        for row in pending:
            row_id = row["id"]
            try:
                embedding_result = await self._ai_router.generate_embedding(
                    row[source.text_field], chat_id=row["chat_id"]
                )
            except AIProviderError as exc:
                # A retriable failure is a statement about the provider, not
                # about this row: charging it would park a healthy row for the
                # life of the process. `RateLimitError` sets the flag and the
                # router carries it through its fallback (`_is_retriable`).
                logger.warning(
                    "Embedding backfill: provider still failing, leaving pending",
                    source=source.name,
                    row_id=row_id,
                    error=str(exc),
                    retriable=exc.retriable,
                )
                if not exc.retriable:
                    self._record_failure(source.name, row_id)
                still_pending += 1
                continue
            except Exception:
                # Anything that is not a provider error is this row's problem
                # until proven otherwise -- that is what parking exists for.
                logger.warning(
                    "Embedding backfill: provider still failing, leaving pending",
                    source=source.name,
                    row_id=row_id,
                )
                self._record_failure(source.name, row_id)
                still_pending += 1
                continue

            actual_dimensions = len(embedding_result.embedding)
            if actual_dimensions != EXPECTED_EMBEDDING_DIMENSIONS:
                logger.warning(
                    "Embedding backfill: unexpected dimensionality, leaving pending",
                    source=source.name,
                    row_id=row_id,
                    expected=EXPECTED_EMBEDDING_DIMENSIONS,
                    actual=actual_dimensions,
                    provider=embedding_result.provider,
                    model=embedding_result.model,
                )
                self._record_failure(source.name, row_id)
                still_pending += 1
                continue

            try:
                await source.update(row_id, embedding_result.embedding)
            except Exception:
                logger.exception(
                    "Embedding backfill: failed to persist backfilled embedding",
                    source=source.name,
                    row_id=row_id,
                )
                self._record_failure(source.name, row_id)
                still_pending += 1
                continue

            self._failures.pop((source.name, row_id), None)
            filled += 1

        return filled, still_pending

    async def _run_loop(self) -> None:
        await asyncio.sleep(_INITIAL_DELAY)

        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("Embedding backfill pass failed")

            await asyncio.sleep(self._config.interval_seconds)
