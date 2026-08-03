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
from aiogram.exceptions import TelegramBadRequest
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

    Never raises into the caller -- a chat's restricted `available_reactions`
    set or a non-reactable service message degrades to a logged warning, not
    a crash (mirrors `modules/sticker`'s swallow-and-log shape).
    """
    try:
        return await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except TelegramBadRequest:
        logger.warning(
            "Failed to set reaction (restricted available_reactions or non-reactable message)",
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
        )
        return False
