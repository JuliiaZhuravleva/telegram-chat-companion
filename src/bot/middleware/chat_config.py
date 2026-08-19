"""Middleware that injects resolved ChatConfig into every message handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, MessageReactionUpdated, TelegramObject
from dishka import AsyncContainer

from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

logger = structlog.get_logger()


class ChatConfigMiddleware(BaseMiddleware):
    """Resolve per-chat config and inject it as ``chat_config`` handler kwarg.

    Resolves ``ChatConfigService`` from the Dishka container (already in
    ``data["dishka_container"]`` by the time this inner middleware runs).

    Note: ``enabled`` gating has been moved to ``AccessControlMiddleware``
    so admin DMs can bypass the whitelist check.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = _extract_chat_id(event)
        if chat_id is None:
            return await handler(event, data)

        logger.info(
            "Incoming event",
            chat_id=chat_id,
            event_type=type(event).__name__,
        )

        container: AsyncContainer = data["dishka_container"]
        config_service = await container.get(ChatConfigService)

        # Check if config is cached (avoid DB write when cached)
        was_cached = config_service.is_cached(chat_id)
        chat_config = await config_service.get_config(chat_id)
        data["chat_config"] = chat_config

        # On cache miss, update chat_title/chat_type/is_forum from the event
        if not was_cached:
            chat_title, chat_type, is_forum = _extract_chat_info(event)
            if chat_title:
                try:
                    repo = await container.get(ChatSettingsRepository)
                    await repo.ensure_exists(chat_id, chat_title, chat_type, is_forum)
                except Exception as exc:
                    # Now also on the message_reaction path, where a chat can
                    # first be seen via a reaction rather than a message -- a
                    # cause specific to that path would otherwise be invisible.
                    logger.warning(
                        "Failed to update chat metadata (title/type/is_forum)",
                        chat_id=chat_id,
                        error=str(exc),
                        exc_info=True,
                    )

        return await handler(event, data)


def _extract_chat_id(event: TelegramObject) -> int | None:
    """Extract chat_id from Message, CallbackQuery, or MessageReactionUpdated."""
    if isinstance(event, Message) and event.chat is not None:
        return event.chat.id
    if isinstance(event, CallbackQuery) and event.message and event.message.chat:
        return event.message.chat.id
    if isinstance(event, MessageReactionUpdated):
        return event.chat.id
    return None


def _extract_chat_info(event: TelegramObject) -> tuple[str | None, str, bool | None]:
    """Extract chat_title, chat_type and is_forum from event.

    is_forum is coerced with bool(): Telegram omits the field for non-forum
    chats, so on an event that carries a real Chat object, None *means* "not a
    forum" — writing False (not None) is what lets a chat that turned forum
    mode off get corrected instead of keeping a stale True (TD-102).
    """
    if isinstance(event, Message) and event.chat is not None:
        title = event.chat.title or event.chat.full_name
        return title, event.chat.type or "group", bool(event.chat.is_forum)
    if isinstance(event, MessageReactionUpdated):
        title = event.chat.title or event.chat.full_name
        return title, event.chat.type or "group", bool(event.chat.is_forum)
    return None, "group", None
