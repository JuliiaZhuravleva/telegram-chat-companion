"""Middleware that injects resolved ChatConfig into every message handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from dishka import AsyncContainer

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

        container: AsyncContainer = data["dishka_container"]
        config_service = await container.get(ChatConfigService)
        chat_config = await config_service.get_config(chat_id)
        data["chat_config"] = chat_config

        return await handler(event, data)


def _extract_chat_id(event: TelegramObject) -> int | None:
    """Extract chat_id from Message or CallbackQuery."""
    if isinstance(event, Message) and event.chat is not None:
        return event.chat.id
    if isinstance(event, CallbackQuery) and event.message and event.message.chat:
        return event.message.chat.id
    return None
