"""Middleware that extracts message_thread_id from forum topics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = structlog.get_logger(__name__)


class TopicMiddleware(BaseMiddleware):
    """Extract and validate message_thread_id from incoming events.

    Injects ``message_thread_id: int | None`` into handler data.

    Extraction sources:
    - Message: message.message_thread_id
    - CallbackQuery: callback.message.message_thread_id

    Validates the value is an integer (defense-in-depth for SQL safety).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message_thread_id = _extract_thread_id(event)
        data["message_thread_id"] = message_thread_id

        if message_thread_id is not None:
            logger.debug(
                "Forum topic detected",
                message_thread_id=message_thread_id,
                event_type=type(event).__name__,
            )

        return await handler(event, data)


def _extract_thread_id(event: TelegramObject) -> int | None:
    """Extract and validate message_thread_id from event.

    Returns None if:
    - Event has no message_thread_id attribute
    - Value is not an integer
    - Chat is not a forum
    """
    chat: Any = None
    raw_mti: Any = None

    if isinstance(event, Message):
        chat = event.chat
        raw_mti = getattr(event, "message_thread_id", None)
    elif isinstance(event, CallbackQuery) and event.message:
        chat = getattr(event.message, "chat", None)
        raw_mti = getattr(event.message, "message_thread_id", None)

    # Only treat as forum topic if chat is actually a forum supergroup
    if chat is not None and not getattr(chat, "is_forum", False):
        return None

    # Strict integer validation (defense-in-depth)
    if isinstance(raw_mti, int):
        return raw_mti

    return None
