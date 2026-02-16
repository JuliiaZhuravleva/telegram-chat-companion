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
            f"Chat: {chat_id}\n"
            f"User: {escape(username) if username else user_id}\n"
            f"Pattern: {escape(pattern_description) if pattern_description else 'unknown'}\n"
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

        if not await self._is_notification_enabled("blacklist"):
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = (
            f"🚫 Blacklist triggered\n"
            f"Chat: {chat_id}\n"
            f"User: {escape(username) if username else user_id}\n"
            f"Content: {escape(content[:50])}\n"
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
        chat_type: str | None = None,
        chat_username: str | None = None,
        user_id: int | None,
        user_first_name: str | None = None,
        user_last_name: str | None = None,
        user_username: str | None = None,
        message_text: str | None = None,
        bot: Any = None,
        reply_markup: InlineKeyboardMarkup | None = None,
        # Legacy compat
        username: str | None = None,
    ) -> None:
        """Notify admins about unauthorized access attempt."""
        if bot is None:
            return

        if not await self._is_notification_enabled("unauthorized"):
            return

        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = self._format_unauthorized(
            chat_id=chat_id,
            chat_title=chat_title,
            chat_type=chat_type,
            chat_username=chat_username,
            user_id=user_id,
            user_first_name=user_first_name or username,
            user_last_name=user_last_name,
            user_username=user_username,
            message_text=message_text,
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    admin_id, text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            except Exception:
                logger.warning("Failed to notify admin", admin_id=admin_id)

    @staticmethod
    def _format_unauthorized(
        *,
        chat_id: int,
        chat_title: str | None,
        chat_type: str | None,
        chat_username: str | None,
        user_id: int | None,
        user_first_name: str | None,
        user_last_name: str | None,
        user_username: str | None,
        message_text: str | None,
    ) -> str:
        """Build detailed HTML notification for unauthorized access."""
        lines: list[str] = ["🔒 <b>Unauthorized access</b>", ""]

        # -- Chat info --
        chat_display = escape(chat_title) if chat_title else str(chat_id)
        if chat_type:
            chat_display += f" ({escape(chat_type)})"
        lines.append(f"<b>Chat:</b> {chat_display}")
        lines.append(f"<b>Chat ID:</b> <code>{chat_id}</code>")
        if chat_username:
            lines.append(
                f"<b>Chat link:</b> https://t.me/{escape(chat_username)}"
            )

        lines.append("")

        # -- User info --
        full_name = escape(user_first_name) if user_first_name else ""
        if user_last_name:
            full_name += f" {escape(user_last_name)}"
        full_name = full_name.strip() or "unknown"

        if user_id:
            lines.append(
                f"<b>User:</b> <a href=\"tg://user?id={user_id}\">{full_name}</a>"
            )
            lines.append(f"<b>User ID:</b> <code>{user_id}</code>")
        else:
            lines.append(f"<b>User:</b> {full_name}")

        if user_username:
            lines.append(f"<b>Username:</b> @{escape(user_username)}")

        # -- Message preview --
        if message_text:
            lines.append(f"\n<b>Message:</b> {escape(message_text[:100])}")

        return "\n".join(lines)

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
            f"Chat: {chat_id}\n"
            f"Primary: {primary_provider} (failed)\n"
            f"Fallback: {fallback_provider}\n"
            f"Error: {error[:100]}"
        )

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode=None)
            except Exception:
                logger.warning("Failed to notify admin", admin_id=admin_id)
