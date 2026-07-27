"""Shared bot-layer helpers.

`resolve_display_name` is the single lookup point for "user id → human label"
(CLAUDE.md ADR: handlers may call the Bot API as a fallback, but at 3+ call
sites the logic must be shared — the full TelegramAPIService extraction is
tracked for Phase 3).
"""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

logger = structlog.get_logger(__name__)


async def resolve_display_name(
    bot: Bot | None,
    chat_id: int,
    user_id: int,
    fallback: str,
) -> str:
    """Resolve a chat member's display label (@username > first_name > fallback)."""
    if bot is None:
        return fallback
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        user = member.user
        return f"@{user.username}" if user.username else (user.first_name or fallback)
    except Exception as exc:
        logger.warning(
            "display_name_resolve_failed",
            chat_id=chat_id,
            user_id=user_id,
            error=str(exc),
        )
        return fallback


async def safe_edit_text(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    """edit_text that tolerates re-rendering identical content.

    Telegram raises TelegramBadRequest("message is not modified") when the
    new text+markup equal the current ones (double-tap on a refresh button,
    re-opening the same page). Only that error is suppressed.
    """
    try:
        if parse_mode is not None:
            await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
