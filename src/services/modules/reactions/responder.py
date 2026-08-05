"""Sets the bot's own reaction on a message (R-5; ADR-0004 Decision 4).

Wraps `Bot.set_message_reaction()`, which already enforces "as non-premium
users, bots can set up to one reaction per message" and auto-redirects
media-group reactions to the first non-deleted message in the group
(verified: `aiogram.client.bot.Bot.set_message_reaction` docstring) -- this
module does not reimplement either constraint, just calls it and handles the
result.
"""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ReactionTypeEmoji

logger = structlog.get_logger(__name__)


async def set_reaction(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    emoji: str,
) -> bool:
    """Set a single standard-emoji reaction on a message.

    Never raises a Telegram API error into the caller -- a chat's restricted
    `available_reactions` set, a non-reactable service message, the bot having
    been removed from the chat, or a transient network/server error all degrade
    to a logged warning and `False` (mirrors `modules/sticker`'s
    swallow-and-log shape).

    Catches `TelegramAPIError`, not `TelegramBadRequest`: the two are siblings,
    not parent and child. `TelegramForbiddenError` (bot kicked or blocked --
    precisely the case R-D1 exists to surface), `TelegramNetworkError`,
    `TelegramServerError` and `TelegramRetryAfter` all descend from
    `TelegramAPIError` directly, so a BadRequest-only catch let every one of
    them through while the docstring promised otherwise.

    Non-Telegram exceptions still propagate: those are bugs, and swallowing
    them here would hide them from the caller as well.
    """
    try:
        return await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except TelegramAPIError as exc:
        # `error` carries Telegram's own description, which is the only thing
        # that separates "restricted available_reactions" from "bot is not a
        # member of the chat" -- both arrive here, and both used to log the
        # same sentence.
        logger.warning(
            "Failed to set reaction",
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False
