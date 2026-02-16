"""Middleware that saves messages to the database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from src.database.repositories.messages import MessageRepository
from src.models.chat_config import ChatConfig

logger = structlog.get_logger(__name__)


class MessageSaverMiddleware(BaseMiddleware):
    """Insert messages into ``chat_messages``.

    Respects ``chat_config.save_messages`` toggle.
    Runs after ``ChatConfigMiddleware`` (needs ``chat_config`` in data).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.chat is None:
            return await handler(event, data)

        # Check if message saving is enabled for this chat
        chat_config: ChatConfig | None = data.get("chat_config")
        if chat_config and not chat_config.save_messages:
            return await handler(event, data)

        try:
            await self._save_message(event, data)
        except Exception:
            logger.warning("Failed to save message", exc_info=True)

        return await handler(event, data)

    @staticmethod
    async def _save_message(message: Message, data: dict[str, Any]) -> None:
        """Save a message to the database."""
        container: AsyncContainer = data["dishka_container"]
        msg_repo = await container.get(MessageRepository)

        # Extract message_thread_id from middleware data (TopicMiddleware)
        message_thread_id: int | None = data.get("message_thread_id")

        # Determine message type
        if message.sticker:
            msg_type = "sticker"
        elif message.voice:
            msg_type = "voice"
        elif message.video_note:
            msg_type = "video_note"
        elif message.photo:
            msg_type = "photo"
        else:
            msg_type = "text"

        await msg_repo.save(
            chat_id=message.chat.id,
            message_id=message.message_id,
            message_type=msg_type,
            user_id=message.from_user.id if message.from_user else None,
            username=message.from_user.username if message.from_user else None,
            first_name=message.from_user.first_name if message.from_user else None,
            content=message.text or message.caption,
            reply_to_message_id=(
                message.reply_to_message.message_id
                if message.reply_to_message
                else None
            ),
            is_bot_message=bool(message.from_user and message.from_user.is_bot),
            sticker_file_id=message.sticker.file_id if message.sticker else None,
            sticker_file_unique_id=(
                message.sticker.file_unique_id if message.sticker else None
            ),
            sticker_set_name=(
                message.sticker.set_name if message.sticker else None
            ),
            sticker_emoji=message.sticker.emoji if message.sticker else None,
            message_thread_id=message_thread_id,
        )
