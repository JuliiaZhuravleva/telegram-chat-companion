"""Admin notification sender for abuse events."""

from __future__ import annotations

from typing import Any

import structlog

from src.database.repositories.bot_config import BotConfigRepository

logger = structlog.get_logger(__name__)


class AbuseNotificationService:
    """Send notifications to admin about abuse events.

    Notification types: jailbreak, blacklist, ai_fallback, unauthorized.
    Runs in parallel with main response — never blocks user.

    Admin IDs are resolved from bot_config at call time (not at construction),
    so changes to admin_ids take effect immediately.
    """

    def __init__(self, bot_config_repo: BotConfigRepository) -> None:
        self._bot_config_repo = bot_config_repo

    async def _get_admin_ids(self) -> list[int]:
        """Resolve admin IDs from bot_config table."""
        raw = await self._bot_config_repo.get("admin_ids")
        if not raw:
            return []
        try:
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            logger.warning("Invalid admin_ids format in bot_config", raw=raw)
            return []

    async def notify_jailbreak(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str | None,
        pattern_description: str | None,
        severity: int | None,
        bot: Any = None,
    ) -> None:
        """Notify admins about a jailbreak attempt."""
        if bot is None:
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = (
            f"⚠️ Jailbreak attempt\n"
            f"Chat: {chat_id}\n"
            f"User: {username or user_id}\n"
            f"Pattern: {pattern_description or 'unknown'}\n"
            f"Severity: {severity or 'N/A'}"
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                logger.warning("Failed to notify admin", admin_id=admin_id)

    async def notify_blacklist(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str | None,
        content: str,
        timeout_hours: float,
        bot: Any = None,
    ) -> None:
        """Notify admins about a blacklist trigger."""
        if bot is None:
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = (
            f"🚫 Blacklist triggered\n"
            f"Chat: {chat_id}\n"
            f"User: {username or user_id}\n"
            f"Content: {content[:50]}\n"
            f"Timeout: {timeout_hours:.1f}h"
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                logger.warning("Failed to notify admin", admin_id=admin_id)

    async def notify_unauthorized(
        self,
        *,
        chat_id: int,
        chat_title: str | None,
        user_id: int | None,
        username: str | None,
        bot: Any = None,
    ) -> None:
        """Notify admins about unauthorized access attempt."""
        if bot is None:
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = (
            f"🔒 Unauthorized access\n"
            f"Chat: {chat_title or chat_id}\n"
            f"User: {username or user_id or 'unknown'}"
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                logger.warning("Failed to notify admin", admin_id=admin_id)

    async def notify_ai_fallback(
        self,
        *,
        chat_id: int,
        primary_provider: str,
        fallback_provider: str,
        error: str,
        bot: Any = None,
    ) -> None:
        """Notify admins when AI primary provider failed and fallback was used."""
        if bot is None:
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = (
            f"🔄 AI fallback activated\n"
            f"Chat: {chat_id}\n"
            f"Primary: {primary_provider} (failed)\n"
            f"Fallback: {fallback_provider}\n"
            f"Error: {error[:100]}"
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                logger.warning("Failed to notify admin", admin_id=admin_id)
