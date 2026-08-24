"""Retention cleanup — background task.

Periodically prunes the append-only tables so they stop growing without bound.
This is the Python counterpart of the reference n8n bot's ``periodic_cleanup()``
(internal/n8n-reference/data/data-lifecycle.md).

Pattern follows StickerSetSyncScheduler / HealthChecker: a process-lifetime
background task owned by main(), not by Dishka.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

import asyncpg
import structlog

from src.config import MaintenanceSettings
from src.database.repositories.maintenance import MaintenanceRepository

logger = structlog.get_logger(__name__)

_INITIAL_DELAY = 120  # let startup settle before the first pass


class RetentionCleaner:
    """Background task that enforces the configured retention windows."""

    def __init__(self, pool: asyncpg.Pool, config: MaintenanceSettings) -> None:
        self._pool = pool
        self._config = config
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the cleanup loop (no-op when maintenance is disabled)."""
        if not self._config.enabled:
            logger.info("Retention cleanup disabled by config")
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Retention cleaner started",
            interval_seconds=self._config.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the cleanup loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Retention cleaner stopped")

    async def run_once(self) -> dict[str, int]:
        """Run a single cleanup pass. Returns rows deleted per table.

        Each table is independent: a failure on one is logged and the rest
        still run, so one bad window can't stall the whole sweep.
        """
        repo = MaintenanceRepository(self._pool)
        deleted: dict[str, int] = {}

        for table, window in self._windows().items():
            try:
                count = await repo.delete_older_than(table, window)
            except Exception:
                logger.exception("Retention cleanup failed for table", table=table)
                continue
            if count:
                deleted[table] = count

        if deleted:
            logger.info("Retention cleanup complete", deleted=deleted)
        return deleted

    def _windows(self) -> dict[str, timedelta]:
        """Configured retention windows, skipping any set to None (= keep forever).

        chat_memory (RAG long-term memory) is deliberately NOT listed here, and is
        not in RETENTION_TABLES either. ADR-0011 establishes a data-preservation
        invariant: memory rows must not be irrecoverably deleted without first
        persisting a high-level summary, which this slice does not build.
        Unbounded growth is an accepted, temporary trade-off until the S5/S6
        decommission work (docs/plans/rag-revision-2026-08.md).
        """
        config = self._config
        raw: dict[str, timedelta | None] = {
            "user_activity": (
                timedelta(hours=config.user_activity_hours)
                if config.user_activity_hours is not None
                else None
            ),
            "chat_messages": (
                timedelta(days=config.chat_messages_days)
                if config.chat_messages_days is not None
                else None
            ),
            "response_log": (
                timedelta(days=config.response_log_days)
                if config.response_log_days is not None
                else None
            ),
            "unauthorized_attempts": (
                timedelta(days=config.unauthorized_attempts_days)
                if config.unauthorized_attempts_days is not None
                else None
            ),
            "abuse_blocked_log": (
                timedelta(days=config.abuse_blocked_log_days)
                if config.abuse_blocked_log_days is not None
                else None
            ),
            "message_reactions": (
                timedelta(days=config.reactions_days) if config.reactions_days is not None else None
            ),
            "decision_log": (
                timedelta(days=config.decision_log_days)
                if config.decision_log_days is not None
                else None
            ),
            "retrieval_log": (
                timedelta(days=config.retrieval_log_days)
                if config.retrieval_log_days is not None
                else None
            ),
            "ai_failure_log": (
                timedelta(days=config.ai_failure_log_days)
                if config.ai_failure_log_days is not None
                else None
            ),
        }
        return {table: window for table, window in raw.items() if window is not None}

    async def _run_loop(self) -> None:
        await asyncio.sleep(_INITIAL_DELAY)

        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("Retention cleanup pass failed")

            await asyncio.sleep(self._config.interval_seconds)
