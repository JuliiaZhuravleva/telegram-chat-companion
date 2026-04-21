"""Access control middleware — whitelist + admin detection."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from dishka import AsyncContainer

from src.bot.keyboards.admin import access_keyboard
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.models.chat_config import ChatConfig
from src.services.abuse.notifications import AbuseNotificationService
from src.utils import parse_admin_ids

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

            logger.info(
                "Chat not enabled, skipping",
                chat_id=chat_id,
                user_id=user_id,
            )

            # Notify admins about unauthorized access (with cooldown)
            await self._notify_unauthorized(
                data,
                event,
                chat_id,
                user_id,
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

        admin_ids_raw = await bot_config_repo.get("admin_ids")
        return user_id in parse_admin_ids(admin_ids_raw)

    async def _notify_unauthorized(
        self,
        data: dict[str, Any],
        event: TelegramObject,
        chat_id: int,
        user_id: int | None,
    ) -> None:
        """Send admin notification about unauthorized access (with cooldown).

        Skips notification AND DB logging entirely if the chat has a previous
        rejected attempt (admin-imposed blacklist). Admin must explicitly
        restore or delete the rejection via the admin panel to lift the block.
        """
        now = time.monotonic()
        last = self._last_notify.get(chat_id)
        if last is not None and now - last < _NOTIFY_COOLDOWN_SECONDS:
            return

        # Prune expired entries to prevent unbounded memory growth
        if len(self._last_notify) > 1000:
            self._last_notify = {
                k: v for k, v in self._last_notify.items() if now - v < _NOTIFY_COOLDOWN_SECONDS
            }

        try:
            container: AsyncContainer = data["dishka_container"]
            notifier = await container.get(AbuseNotificationService)
            admin_repo = await container.get(AdminRepository)
            bot = data.get("bot")

            # Blacklist check: skip if chat has a prior rejected attempt.
            # Still bump the cooldown so we don't hit the DB on every message.
            if await admin_repo.has_rejected_attempt(chat_id):
                self._last_notify[chat_id] = now
                logger.info(
                    "Suppressing notification — chat has prior rejection",
                    chat_id=chat_id,
                )
                return

            # Extract all available info from the event
            user_first_name: str | None = None
            user_last_name: str | None = None
            user_username: str | None = None
            chat_title: str | None = None
            chat_type_str: str | None = None
            chat_username: str | None = None
            message_text: str | None = None

            if isinstance(event, Message):
                if event.from_user:
                    user_first_name = event.from_user.first_name
                    user_last_name = event.from_user.last_name
                    user_username = event.from_user.username
                if event.chat:
                    chat_title = event.chat.title or event.chat.full_name
                    chat_type_str = event.chat.type
                    chat_username = event.chat.username
                message_text = (event.text or event.caption or "")[:100] or None
            elif isinstance(event, CallbackQuery) and event.from_user:
                user_first_name = event.from_user.first_name
                user_last_name = event.from_user.last_name
                user_username = event.from_user.username

            # Log to DB and build inline keyboard for approve/reject
            keyboard = None
            try:
                bot_config_repo = await container.get(BotConfigRepository)
                attempt_id = await admin_repo.log_unauthorized(
                    chat_id=chat_id,
                    chat_title=chat_title,
                    chat_type=chat_type_str,
                    user_id=user_id,
                    user_first_name=user_first_name,
                    user_username=user_username,
                    message_text=message_text,
                )
                lang = await admin_repo.get_admin_language(bot_config_repo)
                keyboard = access_keyboard(lang, attempt_id)
            except Exception:
                logger.warning("Failed to log unauthorized attempt to DB", exc_info=True)

            await notifier.notify_unauthorized(
                chat_id=chat_id,
                chat_title=chat_title,
                chat_type=chat_type_str,
                chat_username=chat_username,
                user_id=user_id,
                user_first_name=user_first_name,
                user_last_name=user_last_name,
                user_username=user_username,
                message_text=message_text,
                bot=bot,
                reply_markup=keyboard,
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
