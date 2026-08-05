"""Shared bot-layer helpers.

`resolve_display_name` is the single lookup point for "user id → human label"
(CLAUDE.md ADR: handlers may call the Bot API as a fallback, but at 3+ call
sites the logic must be shared — the full TelegramAPIService extraction is
tracked for Phase 3).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

import structlog
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from src.database.repositories.bot_config import BotConfigRepository
from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.text.prompt_builder import REPLY_QUOTE_MAX_CHARS, REPLY_TEXT_MAX_CHARS
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


@dataclass(frozen=True)
class ReplyContext:
    """Extracted context about the message being replied to.

    `quote_text` / `quote_is_manual` come from `Message.quote` (aiogram's
    `TextQuote`) -- the fragment of `reply_to_message` the user highlighted
    before hitting reply. This is distinct from `text`, which is the replied-
    to message in full. `quote_is_manual` distinguishes a selection the user
    made by hand from a quote Telegram's server attaches automatically;
    "the user is replying to a specific fragment" (the owner's ask) means
    the former only -- callers must gate on it before treating `quote_text`
    as the user's intended focus.
    """

    author: str | None = None
    text: str | None = None
    is_bot: bool = False
    quote_text: str | None = None
    quote_is_manual: bool = False


def extract_reply_context(message: Message) -> ReplyContext:
    """Extract reply-to-message context, including a manually-selected quote.

    Shared by `handle_text_message` and `handle_photo_message`, which
    previously duplicated this extraction inline.
    """
    if not message.reply_to_message:
        return ReplyContext()

    rpl = message.reply_to_message
    author: str | None = None
    is_bot = False
    if rpl.from_user:
        author = rpl.from_user.first_name
        is_bot = rpl.from_user.is_bot
    text = (rpl.text or rpl.caption or "")[:REPLY_TEXT_MAX_CHARS]

    quote_text: str | None = None
    quote_is_manual = False
    if message.quote is not None:
        quote_is_manual = bool(message.quote.is_manual)
        quote_text = message.quote.text[:REPLY_QUOTE_MAX_CHARS]

    return ReplyContext(
        author=author,
        text=text,
        is_bot=is_bot,
        quote_text=quote_text,
        quote_is_manual=quote_is_manual,
    )


def should_respond(
    message: Message,
    config: ChatConfig,
    bot_id: int | None = None,
) -> tuple[bool, TriggerType]:
    """Determine if the bot should respond to this message.

    Returns:
        Tuple of (should_respond, trigger_type).
    """
    text = (message.text or message.caption or "").strip()
    text_lower = text.lower()

    # Check for trigger words (word-boundary matching)
    for trigger in config.trigger_words:
        pattern = rf"(?:^|\s){re.escape(trigger.lower())}"
        if re.search(pattern, text_lower):
            return True, TriggerType.TRIGGER

    # Check if this is a reply to the bot's message
    if message.reply_to_message:
        reply_from = message.reply_to_message.from_user
        reply_from_id = reply_from.id if reply_from else None
        is_match = bot_id is not None and reply_from_id == bot_id
        logger.debug(
            "Reply trigger evaluation",
            chat_id=message.chat.id,
            reply_from_id=reply_from_id,
            bot_id=bot_id,
            is_match=is_match,
        )
        if is_match:
            return True, TriggerType.REPLY

    # Random response chance
    if random.random() < config.random_response_chance:
        return True, TriggerType.RANDOM

    return False, TriggerType.NONE
