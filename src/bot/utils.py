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
from aiogram.client.default import Default
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.messages import MessageRepository
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
    parse_mode: str | None | Default = Default("parse_mode"),
) -> None:
    """edit_text that tolerates re-rendering identical content.

    Telegram raises TelegramBadRequest("message is not modified") when the
    new text+markup equal the current ones (double-tap on a refresh button,
    re-opening the same page). Only that error is suppressed.

    The default is aiogram's own `Default("parse_mode")` sentinel — the same
    value `edit_text` declares — rather than `None`, so the three states stay
    distinguishable:

    * omitted        → inherit the bot-wide default (HTML). Nine callers rely on
                       this and their text contains real markup.
    * `parse_mode=None` → genuinely NO parse mode. Previously this was
                       indistinguishable from "omitted" and therefore re-enabled
                       HTML, so a caller asking for plain text got the opposite —
                       the project's documented escaping trap, one step removed.
    * an explicit mode → that mode.
    """
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
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

    `addresses_bot` is the trigger-detection half: whether this reply should
    count as the user speaking TO the bot. It is NOT the same question as
    `is_bot` -- see `replied_to_transcription` below, where the message came
    from the bot and the reply is nonetheless aimed at another human.
    """

    author: str | None = None
    text: str | None = None
    is_bot: bool = False
    quote_text: str | None = None
    quote_is_manual: bool = False
    addresses_bot: bool = False
    replied_to_transcription: bool = False


async def extract_reply_context(
    message: Message,
    bot_id: int | None,
    message_repo: MessageRepository,
) -> ReplyContext:
    """Extract reply-to-message context, including a manually-selected quote.

    Shared by `handle_text_message`, `handle_photo_message` and the voice
    path, which previously duplicated this extraction inline.

    Async because of one question it must answer: is the replied-to message a
    **transcription** the bot posted? That is a relay of someone else's speech,
    not the bot talking, so replying to one is replying to that person. The
    context is rewritten to their name and their words with `is_bot=False`, and
    `addresses_bot` stays False -- the reply is then decided like any other
    chat message instead of compelling an answer. Every other message from the
    bot sets `addresses_bot=True`.

    The answer comes from `chat_messages.transcribed_message_id` (migration
    028), NOT from matching the rendered header. A text marker could not work:
    a user can ask the bot to echo the header back, and that ordinary AI reply
    would then be indistinguishable from a transcription -- silencing the bot
    in that thread and handing the prompt an attacker-chosen author name for
    words that person never said. The column is written by one code path and
    is unreachable from the chat.

    The DB is only consulted for a reply to one of the bot's OWN messages, so
    ordinary traffic costs no extra query. The lookup never raises: on failure
    the context degrades to "an ordinary bot message" (`addresses_bot=True`),
    which is the pre-fix behaviour -- annoying, not silent, and logged.
    """
    if not message.reply_to_message:
        return ReplyContext()

    rpl = message.reply_to_message
    author: str | None = None
    is_bot = False
    if rpl.from_user:
        author = rpl.from_user.first_name
        is_bot = rpl.from_user.is_bot
    raw_text = rpl.text or rpl.caption or ""

    from_bot_itself = (
        bot_id is not None and rpl.from_user is not None and rpl.from_user.id == bot_id
    )

    transcription = None
    if from_bot_itself:
        try:
            transcription = await message_repo.get_transcription_source(
                message.chat.id, rpl.message_id
            )
        except Exception as exc:
            logger.warning(
                "Transcription lookup failed; treating as an ordinary bot message",
                chat_id=message.chat.id,
                reply_to_message_id=rpl.message_id,
                error=str(exc),
                exc_info=True,
            )

    quote_text: str | None = None
    quote_is_manual = False
    if message.quote is not None:
        quote_is_manual = bool(message.quote.is_manual)
        quote_text = message.quote.text[:REPLY_QUOTE_MAX_CHARS]

    body = raw_text[:REPLY_TEXT_MAX_CHARS]

    if transcription is not None:
        # The transcript and the speaker come from the source row; either can
        # be NULL -- retention can prune the audio's row, and a chat with
        # `save_messages` off never stored the speaker's name to begin with.
        if transcription["source_first_name"] is None or transcription["transcript"] is None:
            # Silent quality loss otherwise: the model gets the rendered header
            # as if it were speech, and nobody can tell whether this happens
            # once a year or on every voice note -- which is exactly what you
            # need to know to judge whether the retention window is too short
            # for how long people take to reply.
            logger.info(
                "Transcription source row is gone; falling back to the rendered message",
                chat_id=message.chat.id,
                transcription_message_id=rpl.message_id,
                source_message_id=transcription["source_message_id"],
            )
        # NOT `or author`: `author` here is whoever sent the message being
        # replied to -- which for a transcription is the BOT. Falling back to
        # it told the model "the user is replying to a message from Companion"
        # above the words someone else actually spoke, i.e. the exact
        # misattribution this whole mechanism exists to prevent, reachable with
        # no attacker at all. None renders as "unknown", which is true.
        author = transcription["source_first_name"]
        if transcription["transcript"] is not None:
            body = transcription["transcript"][:REPLY_TEXT_MAX_CHARS]
        is_bot = False
        # The highlighted fragment was selected against the FULL transcription
        # message -- header included -- while `body` is now just the spoken
        # part. Keeping both would tell the model "they highlighted
        # 'Расшифровка от Иван'" next to an original that contains no such
        # words. Only a quote that survives as a substring of the speech is
        # still meaningful; anything else is dropped.
        #
        # Compared against the TRUNCATED body on purpose: the prompt is shown
        # `body`, so a fragment living past the 500-char cut would otherwise
        # pass this check and still be missing from the "full original message"
        # the model is given -- the same contradiction, one step later.
        if quote_text and quote_text not in body:
            quote_text = None
            quote_is_manual = False

    return ReplyContext(
        author=author,
        text=body,
        is_bot=is_bot,
        quote_text=quote_text,
        quote_is_manual=quote_is_manual,
        addresses_bot=from_bot_itself and transcription is None,
        replied_to_transcription=transcription is not None,
    )


def should_respond(
    message: Message,
    config: ChatConfig,
    *,
    reply_ctx: ReplyContext,
    text: str | None = None,
) -> tuple[bool, TriggerType]:
    """Determine if the bot should respond to this message.

    Args:
        reply_ctx: the context from `extract_reply_context`. **Required**, and
            deliberately so: it carries `addresses_bot`, which is the entire
            reply decision. It used to be optional with a "compute it myself"
            fallback -- but that fallback cannot exist now the lookup is async,
            and an optional argument whose absence silently disables reply
            detection is precisely the shape that ships broken. `bot_id` is
            gone from this signature for the same reason: it is already folded
            into `addresses_bot`, so there is no second place to get it wrong.
        text: content to scan for trigger words instead of the message's own
            text/caption. The voice path passes the Whisper transcript, which
            is the message's real content but exists nowhere on `Message`.

    Returns:
        Tuple of (should_respond, trigger_type).
    """
    scanned = (text if text is not None else (message.text or message.caption or "")).strip()
    text_lower = scanned.lower()

    # Check for trigger words (word-boundary matching)
    for trigger in config.trigger_words:
        pattern = rf"(?:^|\s){re.escape(trigger.lower())}"
        if re.search(pattern, text_lower):
            return True, TriggerType.TRIGGER

    # Check if this is a reply that addresses the bot. A reply to one of the
    # bot's transcriptions is excluded there (see extract_reply_context) and
    # falls through to the ordinary random-chance path below.
    if message.reply_to_message:
        reply_from = message.reply_to_message.from_user
        logger.debug(
            "Reply trigger evaluation",
            chat_id=message.chat.id,
            reply_from_id=reply_from.id if reply_from else None,
            is_match=reply_ctx.addresses_bot,
            replied_to_transcription=reply_ctx.replied_to_transcription,
        )
        if reply_ctx.addresses_bot:
            return True, TriggerType.REPLY

    # Random response chance
    if random.random() < config.random_response_chance:
        return True, TriggerType.RANDOM

    return False, TriggerType.NONE
