"""Middleware that evaluates custom rules on every message.

Runs after ``MessageSaverMiddleware`` (needs message in DB for stateful rules)
and before handlers (rule actions appear before AI response).
Respects ``chat_config.rules_enabled`` toggle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from src.models.chat_config import ChatConfig
from src.services.rules.engine import RuleEngine
from src.services.rules.executor import RuleActionExecutor

logger = structlog.get_logger(__name__)


class RulesMiddleware(BaseMiddleware):
    """Evaluate custom rules for every message.

    Rules never block the handler chain — errors are logged and swallowed.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.chat is None:
            return await handler(event, data)

        chat_config: ChatConfig | None = data.get("chat_config")
        if not chat_config or not chat_config.rules_enabled:
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return await handler(event, data)

        try:
            container: AsyncContainer = data["dishka_container"]
            engine = await container.get(RuleEngine)
            executor = await container.get(RuleActionExecutor)
            bot: Bot = data["bot"]

            actions = await engine.evaluate(
                chat_id=event.chat.id,
                user_id=user_id,
                message=event,
                rules_mode=chat_config.rules_mode,
            )

            for action in actions:
                await executor.execute(action, message=event, bot=bot)

        except Exception:
            logger.warning("Rules evaluation failed", exc_info=True)

        # Always continue to handler
        return await handler(event, data)
