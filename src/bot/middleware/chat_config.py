"""Middleware that injects resolved ChatConfig into every message handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from src.services.chat_config import ChatConfigService

logger = structlog.get_logger()


class ChatConfigMiddleware(BaseMiddleware):
    """Resolve per-chat config and inject it as ``chat_config`` handler kwarg.

    Messages from non-enabled chats are silently dropped.
    """

    def __init__(self, config_service: ChatConfigService) -> None:
        self._config_service = config_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.chat is None:
            return await handler(event, data)

        chat_config = await self._config_service.get_config(event.chat.id)
        data["chat_config"] = chat_config

        if not chat_config.enabled:
            logger.debug(
                "Skipping disabled chat",
                chat_id=event.chat.id,
            )
            return None

        return await handler(event, data)
