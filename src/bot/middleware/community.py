"""Greet a newcomer on their first message (community mode, `greet_on_first_message`).

For chats where most people who join only read: the greeting their join left
pending is delivered as a reply to the first thing they say, within
`GREETING_WINDOW_DAYS`. Registered last on `dp.message`, after the whitelist.

An inner middleware runs only when some handler matched, so a first message
the bot has no handler for (a document, a GIF) does not trigger it; the
greeting stays pending for the next one.

Never blocks the message: whatever goes wrong is logged, and the handler runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from src.models.chat_config import ChatConfig
from src.services.community import CommunityService
from src.utils.senders import classify_sender, is_membership_notice

logger = structlog.get_logger(__name__)


def awaits_greeting_check(message: Message, chat_config: ChatConfig | None) -> bool:
    """Whether this message is one a pending greeting may be claimed by.

    Only a person speaking as themselves: not a bot, not a channel or an
    anonymous admin (no one to greet), not a service line.
    """
    if chat_config is None or not chat_config.enabled:
        return False
    if not (chat_config.community_enabled and chat_config.greet_on_first_message):
        return False
    if message.chat.type not in ("group", "supergroup"):
        return False
    user = message.from_user
    if user is None or user.is_bot:
        return False
    return classify_sender(message).kind is None and not is_membership_notice(message)


class CommunityGreetingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_config: ChatConfig | None = data.get("chat_config")
        if isinstance(event, Message) and awaits_greeting_check(event, chat_config):
            assert chat_config is not None  # narrowed by the check above
            try:
                container: AsyncContainer = data["dishka_container"]
                community = await container.get(CommunityService)
                await community.greet_first_message(chat_config, event)
            except Exception:
                logger.warning(
                    "community_first_message_greeting_failed",
                    chat_id=event.chat.id,
                    exc_info=True,
                )
        return await handler(event, data)
