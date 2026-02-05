"""Admin filter for aiogram handlers."""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import Message
from dishka import AsyncContainer

from src.database.repositories.bot_config import BotConfigRepository


class IsAdmin(BaseFilter):
    """Filter that checks if the current user is a bot admin.

    Queries ``bot_config.admin_ids`` directly so it works regardless
    of whether ``AccessControlMiddleware`` has run yet (filters are
    evaluated before inner middleware in aiogram 3.x).
    """

    async def __call__(self, message: Message, **kwargs: Any) -> bool:
        # Fast path: middleware already resolved admin status
        if "is_admin" in kwargs:
            return bool(kwargs["is_admin"])

        user_id = message.from_user.id if message.from_user else None
        if user_id is None:
            return False

        container: AsyncContainer | None = kwargs.get("dishka_container")
        if container is None:
            return False

        bot_config_repo = await container.get(BotConfigRepository)
        admin_ids_raw = await bot_config_repo.get("admin_ids")
        if not admin_ids_raw:
            return False

        try:
            if isinstance(admin_ids_raw, str):
                admin_ids = [
                    int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()
                ]
            elif isinstance(admin_ids_raw, list):
                admin_ids = [int(x) for x in admin_ids_raw]
            else:
                return False
        except (ValueError, TypeError):
            return False

        return user_id in admin_ids
