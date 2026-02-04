"""Middleware that tracks user activity in the database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from src.database.repositories.activity import ActivityRepository

logger = structlog.get_logger(__name__)


class ActivityTrackerMiddleware(BaseMiddleware):
    """Insert into ``user_activity`` for every message.

    Runs after access control — only tracks authorized users.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        try:
            container: AsyncContainer = data["dishka_container"]
            activity_repo = await container.get(ActivityRepository)
            await activity_repo.track(
                chat_id=event.chat.id,
                user_id=event.from_user.id,
                username=event.from_user.username,
                first_name=event.from_user.first_name,
            )
        except Exception:
            logger.warning("Failed to track activity", exc_info=True)

        return await handler(event, data)
