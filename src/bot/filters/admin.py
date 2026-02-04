"""Admin filter for aiogram handlers."""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import Message


class IsAdmin(BaseFilter):
    """Filter that checks if the current user is a bot admin.

    Depends on ``AccessControlMiddleware`` having injected ``is_admin``
    into handler data.
    """

    async def __call__(self, message: Message, **kwargs: Any) -> bool:
        return bool(kwargs.get("is_admin", False))
