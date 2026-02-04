"""Access control middleware — whitelist + admin detection."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from dishka import AsyncContainer

from src.database.repositories.bot_config import BotConfigRepository
from src.models.chat_config import ChatConfig
from src.services.abuse.notifications import AbuseNotificationService

logger = structlog.get_logger(__name__)

# Cooldown: max 1 notification per chat every 30 minutes
_NOTIFY_COOLDOWN_SECONDS = 1800


class AccessControlMiddleware(BaseMiddleware):
    """Check whitelist, detect admins, log unauthorized access.

    Injects ``is_admin: bool`` into handler data.
    Gates on ``chat_config.enabled`` — if the chat is not enabled,
    the handler is not called (unless it's an admin DM).

    Depends on ``ChatConfigMiddleware`` running first (to set ``chat_config``).
    """

    def __init__(self) -> None:
        super().__init__()
        self._last_notify: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id, chat_type, user_id = _extract_event_info(event)
        if chat_id is None:
            return await handler(event, data)

        chat_config: ChatConfig | None = data.get("chat_config")

        # Determine admin status
        is_admin = await self._is_admin(data, user_id)
        data["is_admin"] = is_admin

        # Check if chat is enabled
        if chat_config and not chat_config.enabled:
            # Admin DMs bypass the enabled check
            if is_admin and chat_type == "private":
                return await handler(event, data)

            logger.debug(
                "Chat not enabled, skipping",
                chat_id=chat_id,
                user_id=user_id,
            )

            # Notify admins about unauthorized access (with cooldown)
            await self._notify_unauthorized(
                data, event, chat_id, user_id,
            )

            return None

        return await handler(event, data)

    @staticmethod
    async def _is_admin(data: dict[str, Any], user_id: int | None) -> bool:
        """Check if user is a bot admin via bot_config.admin_ids."""
        if user_id is None:
            return False

        container: AsyncContainer = data["dishka_container"]
        bot_config_repo = await container.get(BotConfigRepository)

        admin_ids_str = await bot_config_repo.get("admin_ids")
        if not admin_ids_str:
            return False

        try:
            admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
        except ValueError:
            return False

        return user_id in admin_ids

    async def _notify_unauthorized(
        self,
        data: dict[str, Any],
        event: TelegramObject,
        chat_id: int,
        user_id: int | None,
    ) -> None:
        """Send admin notification about unauthorized access (with cooldown)."""
        now = time.monotonic()
        last = self._last_notify.get(chat_id, 0.0)
        if now - last < _NOTIFY_COOLDOWN_SECONDS:
            return

        try:
            container: AsyncContainer = data["dishka_container"]
            notifier = await container.get(AbuseNotificationService)
            bot = data.get("bot")

            # Extract username and chat title from the event
            username: str | None = None
            chat_title: str | None = None
            if isinstance(event, Message):
                if event.from_user:
                    username = event.from_user.username or event.from_user.first_name
                if event.chat:
                    chat_title = event.chat.title or event.chat.full_name
            elif isinstance(event, CallbackQuery) and event.from_user:
                username = event.from_user.username or event.from_user.first_name

            await notifier.notify_unauthorized(
                chat_id=chat_id,
                chat_title=chat_title,
                user_id=user_id,
                username=username,
                bot=bot,
            )
            self._last_notify[chat_id] = now
        except Exception:
            logger.warning("Failed to send unauthorized notification", exc_info=True)


def _extract_event_info(
    event: TelegramObject,
) -> tuple[int | None, str | None, int | None]:
    """Extract (chat_id, chat_type, user_id) from Message or CallbackQuery."""
    if isinstance(event, Message) and event.chat is not None:
        user_id = event.from_user.id if event.from_user else None
        return event.chat.id, event.chat.type, user_id
    if isinstance(event, CallbackQuery):
        user_id = event.from_user.id if event.from_user else None
        if event.message and event.message.chat:
            return event.message.chat.id, event.message.chat.type, user_id
    return None, None, None
