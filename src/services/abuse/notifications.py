"""Admin notification sender for abuse events."""

from __future__ import annotations

import contextlib
import json
from html import escape
from typing import Any

import structlog
from aiogram.types import InlineKeyboardMarkup

from src.database.repositories.bot_config import BotConfigRepository
from src.utils import parse_admin_ids

logger = structlog.get_logger(__name__)

_NOTIFICATION_DEFAULTS: dict[str, Any] = {
    "sticker": "on",
    "unauthorized": True,
    "jailbreak": True,
    "blacklist": True,
    "ai_fallback": True,
}


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
        return parse_admin_ids(raw)

    async def _is_notification_enabled(self, notification_type: str) -> bool:
        """Check if a notification type is enabled in admin_settings."""
        raw = await self._bot_config_repo.get("admin_settings")
        notifications: dict[str, Any] = {}
        if raw:
            with contextlib.suppress(json.JSONDecodeError, AttributeError):
                data = json.loads(raw) if isinstance(raw, str) else raw
                notifications = data.get("notifications") or {}
        merged = {**_NOTIFICATION_DEFAULTS, **notifications}
        return bool(merged.get(notification_type, True))

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

        if not await self._is_notification_enabled("jailbreak"):
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = (
            f"⚠️ Jailbreak attempt\n"
            f"Chat: <code>{chat_id}</code>\n"
            f"User: {escape(username) if username else f'<code>{user_id}</code>'}\n"
            f"Pattern: {escape(pattern_description) if pattern_description else 'unknown'}\n"
            f"Severity: {severity or 'N/A'}"
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception as exc:
                logger.warning(
                    "Failed to notify admin",
                    admin_id=admin_id,
                    error_type=type(exc).__name__,
                )

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

        if not await self._is_notification_enabled("blacklist"):
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = (
            f"🚫 Blacklist triggered\n"
            f"Chat: <code>{chat_id}</code>\n"
            f"User: {escape(username) if username else f'<code>{user_id}</code>'}\n"
            f"Content: {escape(content[:50])}\n"
            f"Timeout: {timeout_hours:.1f}h"
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception as exc:
                logger.warning(
                    "Failed to notify admin",
                    admin_id=admin_id,
                    error_type=type(exc).__name__,
                )

    async def notify_unauthorized(
        self,
        *,
        chat_id: int,
        text: str,
        bot: Any = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        """Deliver an access card to every bot admin. Returns whether anyone got it.

        The card is built by the caller (``src/bot/access_requests.py``, from
        ``screens/cards.py``) in the admin's language; this service never
        imports the screen layer.

        The return value is load-bearing, not decoration: there are three ways
        this sends nothing at all — no bot object, the `unauthorized`
        notification type switched off, an empty `admin_ids`. A caller that
        logged "queued and notified" off the DB row alone would assert an
        outcome that did not happen, which is the very defect TD-025 is about.
        """
        if bot is None:
            return False

        if not await self._is_notification_enabled("unauthorized"):
            return False

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return False

        sent = False
        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                sent = True
            except Exception as exc:
                logger.warning(
                    "Failed to notify admin",
                    admin_id=admin_id,
                    chat_id=chat_id,
                    error_type=type(exc).__name__,
                )
        return sent

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

        if not await self._is_notification_enabled("ai_fallback"):
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = (
            f"🔄 AI fallback activated\n"
            f"Chat: <code>{chat_id}</code>\n"
            f"Primary: {primary_provider} (failed)\n"
            f"Fallback: {fallback_provider}\n"
            f"Error: {error[:100]}"
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode=None)
            except Exception as exc:
                logger.warning(
                    "Failed to notify admin",
                    admin_id=admin_id,
                    error_type=type(exc).__name__,
                )
