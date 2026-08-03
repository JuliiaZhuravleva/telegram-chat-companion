"""Shared bot-layer helpers.

`resolve_display_name` is the single lookup point for "user id → human label"
(CLAUDE.md ADR: handlers may call the Bot API as a fallback, but at 3+ call
sites the logic must be shared — the full TelegramAPIService extraction is
tracked for Phase 3).
"""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from src.database.repositories.bot_config import BotConfigRepository
from src.utils import parse_admin_ids

logger = structlog.get_logger(__name__)

_ADMIN_STATUSES = frozenset({ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR})


async def check_admin_direct(bot_config_repo: BotConfigRepository, user_id: int | None) -> bool:
    """Whether `user_id` is a bot admin, read straight from `bot_config`.

    For handlers that already receive a `BotConfigRepository` via `FromDishka`
    and so cannot use the `IsAdmin` filter's middleware-injected `is_admin`.

    Previously copied byte-for-byte into admin_kb.py, admin_sticker.py and
    admin_reactions.py -- three copies across 17 call sites, which is past the
    "extract after 3 repetitions" threshold in CLAUDE.md's ADR. Changing how
    admin identity is resolved (a dedicated table, an audit log, rate limiting)
    had to be done three times with nothing enforcing they stayed in step.

    NOT to be merged with the `_check_admin` family: those come in two mutually
    incompatible shapes (a sync read of middleware-injected `data["is_admin"]`
    in admin.py/rules.py, and an async Dishka-container lookup in
    admin_sticker.py). Folding them together is its own change.
    """
    if user_id is None:
        return False
    admin_ids_raw = await bot_config_repo.get("admin_ids")
    return user_id in parse_admin_ids(admin_ids_raw)


async def is_bot_chat_admin(bot: Bot, chat_id: int, bot_id: int) -> bool:
    """Live-check whether the bot is an administrator in a chat.

    ADR-0004 Decision 5 (R-D1): called live at two moments -- when an admin
    toggles ``reactions_enabled`` on, and when the admin panel renders the
    module's status line -- never cached. Telegram gives no notification
    when a bot is demoted, so a persisted ``bot_is_admin`` column would go
    stale silently and recreate the exact failure this check exists to
    prevent ("The bot must be an administrator" -- without it,
    `message_reaction` updates simply stop arriving, no error raised).
    """
    try:
        member = await bot.get_chat_member(chat_id, bot_id)
    except Exception as exc:
        logger.warning(
            "bot_admin_check_failed",
            chat_id=chat_id,
            error=str(exc),
        )
        return False
    return member.status in _ADMIN_STATUSES


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
