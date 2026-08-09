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
that wraps it. Unlike retention, there is no retry cap or backoff --
embeddings (gemini-embedding-001) are free, so an outage just means the
backlog is retried again next pass with zero cost pressure to bound
attempts.
"""

from __future__ import annotations

import asyncio
import contextlib

import asyncpg
import structlog

from src.config import EmbeddingBackfillSettings
from src.database.repositories.memory import MemoryRepository
from src.services.ai.router import AIRouter
from src.services.rag.memory import EXPECTED_EMBEDDING_DIMENSIONS

logger = structlog.get_logger(__name__)

_INITIAL_DELAY = 180  # let startup settle before the first pass


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

    async def run_once(self) -> dict[str, int]:
        """Run a single backfill pass. Returns counts of filled/still_pending rows.

        Each row is independent: a provider failure or a wrong-dimension
        result on one row leaves it NULL (retried next pass, no limit —
        embeddings are free) and does not stop the rest of the batch.
        """
        repo = MemoryRepository(self._pool)
        pending = await repo.get_pending_embeddings(limit=self._config.batch_limit)

        filled = 0
        still_pending = 0

        for row in pending:
            memory_id = row["id"]
            try:
                embedding_result = await self._ai_router.generate_embedding(
                    row["content"], chat_id=row["chat_id"]
                )
            except Exception:
                logger.warning(
                    "Embedding backfill: provider still failing, leaving pending",
                    memory_id=memory_id,
                )
                still_pending += 1
                continue

            actual_dimensions = len(embedding_result.embedding)
            if actual_dimensions != EXPECTED_EMBEDDING_DIMENSIONS:
                logger.warning(
                    "Embedding backfill: unexpected dimensionality, leaving pending",
                    memory_id=memory_id,
                    expected=EXPECTED_EMBEDDING_DIMENSIONS,
                    actual=actual_dimensions,
                    provider=embedding_result.provider,
                    model=embedding_result.model,
                )
                still_pending += 1
                continue

            try:
                await repo.update_embedding(memory_id, embedding_result.embedding)
            except Exception:
                logger.exception(
                    "Embedding backfill: failed to persist backfilled embedding",
                    memory_id=memory_id,
                )
                still_pending += 1
                continue

            filled += 1

        if filled or still_pending:
            logger.info(
                "Embedding backfill pass complete",
                filled=filled,
                still_pending=still_pending,
            )

        return {"filled": filled, "still_pending": still_pending}

    async def _run_loop(self) -> None:
        await asyncio.sleep(_INITIAL_DELAY)

        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("Embedding backfill pass failed")

            await asyncio.sleep(self._config.interval_seconds)
