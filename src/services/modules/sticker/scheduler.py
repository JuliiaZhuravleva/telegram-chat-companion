"""Sticker set sync scheduler — background task.

Periodically refreshes sticker set metadata from Telegram API
for sets that are stale or missing from the cache.

Pattern follows HealthChecker in src/services/health/checker.py.
"""

from __future__ import annotations

import asyncio
import contextlib

import asyncpg
import structlog
from aiogram import Bot

from src.database.repositories.stickers import StickerRepository

logger = structlog.get_logger(__name__)

_INTERVAL = 86400  # 24 hours
_INITIAL_DELAY = 300  # 5 min after startup
_BATCH_LIMIT = 10
_STALENESS_DAYS = 30
_DEBUG_RETENTION_DAYS = 7  # Default retention for debug artifacts


class StickerSetSyncScheduler:
    """Background task that syncs sticker set metadata from Telegram.

    Not managed by Dishka — lives for the entire bot process.
    Receives pool and bot directly from main().
    """

    def __init__(self, pool: asyncpg.Pool, bot: Bot) -> None:
        self._pool = pool
        self._bot = bot
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the sync background loop."""
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Sticker set sync scheduler started")

    async def stop(self) -> None:
        """Stop the sync background loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Sticker set sync scheduler stopped")

    async def _run_loop(self) -> None:
        """Sync stale sets periodically."""
        await asyncio.sleep(_INITIAL_DELAY)

        while True:
            try:
                await self._sync_batch()
            except Exception:
                logger.exception("Sticker set sync failed")

            await asyncio.sleep(_INTERVAL)

    async def _sync_batch(self) -> None:
        """Fetch stale/missing sets and update metadata from Telegram API."""
        repo = StickerRepository(self._pool)

        stale = await repo.get_stale_sets(
            staleness_days=_STALENESS_DAYS,
            limit=_BATCH_LIMIT,
        )

        if not stale:
            return

        synced = 0
        for row in stale:
            set_name = row["set_name"]
            try:
                tg_set = await self._bot.get_sticker_set(set_name)
                await repo.upsert_sticker_set(
                    set_name=tg_set.name,
                    set_title=tg_set.title,
                    total_count=len(tg_set.stickers),
                    thumbnail_file_id=(tg_set.thumbnail.file_id if tg_set.thumbnail else None),
                    is_animated=tg_set.sticker_type == "custom_emoji"
                    or any(s.is_animated for s in tg_set.stickers[:1]),
                    is_video=any(s.is_video for s in tg_set.stickers[:1]),
                )
                synced += 1
            except Exception:
                logger.warning(
                    "Failed to sync sticker set",
                    set_name=set_name,
                )

        if synced:
            logger.info(
                "Sticker set sync complete",
                synced=synced,
                total=len(stale),
            )

        # Clean up old debug artifacts (once per day during sync)
        try:
            deleted = await repo.cleanup_old_debug_artifacts(_DEBUG_RETENTION_DAYS)
            if deleted > 0:
                logger.info("Cleaned up old debug artifacts", deleted=deleted)
        except Exception:
            logger.exception("Failed to cleanup debug artifacts")
