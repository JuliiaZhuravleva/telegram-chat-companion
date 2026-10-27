"""Middleware that saves messages to the database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.messages import MessageRepository
from src.models.chat_config import ChatConfig
from src.utils.senders import classify_sender, is_channel_post, is_membership_notice

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
        if not isinstance(event, Message) or event.chat is None or is_membership_notice(event):
            return await handler(event, data)

        chat_config: ChatConfig | None = data.get("chat_config")

        # Before the save_messages gate: which channel a group belongs to is
        # chat metadata, needed by rendering whether or not history is kept.
        if is_channel_post(event):
            try:
                await self._bind_linked_channel(event, chat_config, data)
            except Exception:
                logger.warning("Failed to record linked channel", exc_info=True)

        # Check if message saving is enabled for this chat
        if chat_config and not chat_config.save_messages:
            return await handler(event, data)

        try:
            await self._save_message(event, data)
        except Exception:
            logger.warning("Failed to save message", exc_info=True)

        return await handler(event, data)

    @staticmethod
    async def _bind_linked_channel(
        message: Message, chat_config: ChatConfig | None, data: dict[str, Any]
    ) -> None:
        """Learn the discussion group's channel from the first post it carries.

        Telegram auto-forwards a channel's posts ONLY into its linked discussion
        group, so `sender_chat` of such a post is that channel by definition --
        no API call, no admin question. An already-recorded different channel
        is reported, never overwritten (`set_linked_channel`).
        """
        channel = message.sender_chat
        if channel is None:
            return
        linked = chat_config.linked_channel_id if chat_config else None
        if linked == channel.id:
            return
        if linked is None:
            container: AsyncContainer = data["dishka_container"]
            repo = await container.get(ChatSettingsRepository)
            linked = await repo.set_linked_channel(message.chat.id, channel.id, channel.title)
            if linked is None:
                return  # no chat_settings row yet; the next post retries
            if linked == channel.id:
                logger.info(
                    "Linked channel recorded", chat_id=message.chat.id, channel_id=channel.id
                )
                return
        logger.warning(
            "Channel post from a channel other than the recorded linked one",
            chat_id=message.chat.id,
            channel_id=channel.id,
            linked_channel_id=linked,
        )

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

        # `message.quote` (aiogram's TextQuote) is the fragment the user
        # manually highlighted on `reply_to_message` before hitting reply --
        # same field Q-1 reads on the live path (`extract_reply_context()` in
        # `src/bot/handlers/message.py`). Persisted as-is (untruncated): the
        # 300-char prompt-budget cap is a rendering concern applied where the
        # text is consumed, not at storage time. `is_manual` is an Optional
        # bool in aiogram's model (unset means "not manual"); normalize to a
        # concrete bool so a NULL in the DB unambiguously means "no quote".
        quote_text: str | None = None
        quote_is_manual: bool | None = None
        if message.quote is not None:
            quote_text = message.quote.text
            quote_is_manual = bool(message.quote.is_manual)

        sender = classify_sender(message)
        await msg_repo.save(
            chat_id=message.chat.id,
            message_id=message.message_id,
            message_type=msg_type,
            user_id=message.from_user.id if message.from_user else None,
            username=message.from_user.username if message.from_user else None,
            first_name=message.from_user.first_name if message.from_user else None,
            content=message.text or message.caption,
            reply_to_message_id=(
                message.reply_to_message.message_id if message.reply_to_message else None
            ),
            is_bot_message=bool(message.from_user and message.from_user.is_bot),
            sticker_file_id=message.sticker.file_id if message.sticker else None,
            sticker_file_unique_id=(message.sticker.file_unique_id if message.sticker else None),
            sticker_set_name=(message.sticker.set_name if message.sticker else None),
            sticker_emoji=message.sticker.emoji if message.sticker else None,
            message_thread_id=message_thread_id,
            quote_text=quote_text,
            quote_is_manual=quote_is_manual,
            sender_kind=sender.kind,
            sender_chat_id=sender.chat_id,
            sender_chat_title=sender.chat_title,
            author_signature=sender.signature,
        )
