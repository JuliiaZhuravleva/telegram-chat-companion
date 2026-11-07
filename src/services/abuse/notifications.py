"""Admin notification sender: the access card for a chat that is not allowed yet."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import structlog
from aiogram.types import InlineKeyboardMarkup

from src.database.repositories.bot_config import BotConfigRepository
from src.utils import parse_admin_ids

logger = structlog.get_logger(__name__)

# Only the type this service sends. The stored settings may still carry other
# keys (``jailbreak``, ``blacklist``, ``ai_fallback``) from senders that were
# removed; they are left where they are and read by nothing here.
_NOTIFICATION_DEFAULTS: dict[str, Any] = {
    "unauthorized": True,
}


class AbuseNotificationService:
    """Deliver the access card to the bot's admins.

    One notification type: ``unauthorized``. Runs in parallel with the main
    response — never blocks the user.

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
