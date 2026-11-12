"""Shared reply mechanics for the text and photo handlers.

`handlers/media.py` grew as a partial copy of `handlers/message.py`, and the
copy drifted: three things present in the text path never reached it — the
relevancy gate, the spend-limit check and the AI-chosen sticker. The first
was a live defect (random replies to captioned photos bypassed the relevance
check entirely), the other two were silent feature gaps.

The lesson recorded in TD-028 is that patching media.py would fix the symptom
and leave the mechanism: two copies of "decide → reply → bookkeeping" drift
again the next time either is touched. So the parts that actually drifted live
here, called by both handlers, and there is one place to change them.

Deliberately NOT a full merge of the two handlers. They differ in ways that
matter — a photo must be downloaded and described before anything can be
decided, and it has a no-caption branch with no equivalent in text. Forcing
those into one function would trade duplication for a flag-driven mega-handler
on the bot's most critical path.
"""

from __future__ import annotations

import re
from html import escape as html_escape
from html import unescape

import structlog
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.abuse.checker import AntiAbuseChecker
from src.services.costs.spend_limit import SpendLimitService
from src.services.modules.reactions.responder import set_reaction
from src.services.modules.reactions.selector import ReactionSelector
from src.services.relevancy.gate import GateDecision, RelevancyGate
from src.services.text.pipeline import PipelineResult, TextProcessingPipeline
from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT, parsed_length, split_html

logger = structlog.get_logger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Shown in place of the quote when the message being answered is already gone.
# Keys must match ChatConfig.language values; both are plain text with no HTML
# special characters, so they need no escaping when prepended to html_text.
_SOURCE_DELETED_NOTE: dict[str, str] = {
    "ru": "⚠️ Исходное сообщение удалено.",
    "en": "⚠️ The original message was deleted.",
}

# Telegram returns no machine-readable code for "the message you want to quote
# is gone" -- only prose, and the wording has changed across Bot API versions.
# Matching text is unpleasant but it is the only signal available; an unmatched
# TelegramBadRequest is deliberately re-raised rather than silently treated as
# a deletion, so a genuine formatting error cannot hide behind this note.
_REPLY_TARGET_GONE_MARKERS = (
    "message to be replied not found",
    "message to reply not found",
    "replied message not found",
    "reply message not found",
)

# The rejection this module exists to prevent. `split_html` should make it
# unreachable, so reaching it means our arithmetic disagrees with Telegram's --
# which is exactly when a last-resort truncation beats another re-raise into
# silence. Logged at warning so the disagreement is findable rather than
# absorbed.
_TOO_LONG_MARKERS = ("message is too long", "message_too_long", "text is too long")


def in_reply_thread(message: Message) -> bool:
    """Whether ``message`` sits in a thread that only a reply can reach.

    A comment under a channel post (in the channel's discussion group) and a
    reply chain in an ordinary supergroup are both threads Telegram keeps
    together by the reply link alone: the Bot API's ``message_thread_id`` is
    honoured for forum topics only, and aiogram's ``Message.answer`` passes it
    only when ``is_topic_message``. A message sent there WITHOUT a quote lands
    in the group's main feed -- under a blog post it is simply not there.

    Forums are excluded on purpose: ``answer()`` already routes into the topic,
    and inside a topic Telegram sets ``reply_to_message`` to the topic's root
    even when nobody replied to anything.

    Reads the raw ``message_thread_id`` off the message, not TopicMiddleware's
    kwarg -- that one is nulled for every non-forum chat, which is exactly the
    case this asks about.
    """
    # `is True`, not truthiness: Telegram sends `is_forum` only for forums, and
    # anything but a real True (None, absent) means an ordinary group.
    if message.chat.is_forum is True:
        return False
    return message.reply_to_message is not None or message.message_thread_id is not None


def answer_quote_target(message: Message, trigger_type: TriggerType) -> int | None:
    """The message an answer to ``message`` quotes, or None for no quote.

    An invited answer (mention, trigger word, reply) always quotes. An
    unsolicited RANDOM one stays unquoted in the open feed -- quoting a message
    nobody addressed to the bot reads as pointing at its author -- but quotes
    inside a reply thread, because there an unquoted message leaves the thread
    (owner's decision, 2026-11-12).
    """
    if trigger_type != TriggerType.RANDOM or in_reply_thread(message):
        return message.message_id
    return None


async def _send_one(
    *,
    message: Message,
    html_text: str,
    reply_to_message_id: int | None,
    language: str,
    continuation: bool = False,
) -> Message | None:
    """Send `html_text`, quoting `reply_to_message_id`, surviving its deletion.

    The bot decides to answer, then spends real time on it -- for a voice note,
    Whisper plus the relevancy judge plus generation is easily tens of seconds.
    The author can delete their message inside that window, and Telegram then
    rejects the send outright because the quote target no longer exists. The
    reply was already paid for, so dropping it is the worst of the options.

    Instead the reply is re-sent unquoted with a one-line note at the top, so
    the chat can see what happened rather than reading an answer that appears
    to address nobody.

    A `continuation` (part two onwards of a split answer) quotes the source
    for threading only, so it asks Telegram to drop the quote rather than
    reject the send: the deletion note belongs on the head of the answer, and
    repeating it on every later piece would be noise.

    Returns the sent `Message`, or None if it could not be delivered at all --
    callers must still run their post-send bookkeeping in that case, because
    the AI call has already been made and its cost has to be recorded whether
    or not the text reached the chat.
    """
    try:
        if continuation and reply_to_message_id is not None:
            return await message.answer(
                html_text,
                parse_mode="HTML",
                reply_to_message_id=reply_to_message_id,
                allow_sending_without_reply=True,
            )
        return await message.answer(
            html_text,
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
        )
    except TelegramBadRequest as exc:
        detail = str(exc).lower()
        if reply_to_message_id is None or not any(
            marker in detail for marker in _REPLY_TARGET_GONE_MARKERS
        ):
            raise
        logger.info(
            "Reply target vanished before the answer was sent; sending unquoted",
            chat_id=message.chat.id,
            reply_to_message_id=reply_to_message_id,
        )

    note = _SOURCE_DELETED_NOTE.get(language, _SOURCE_DELETED_NOTE["en"])
    try:
        return await message.answer(f"{note}\n\n{html_text}", parse_mode="HTML")
    except Exception as exc:
        logger.warning(
            "Failed to send the answer even without a quote",
            chat_id=message.chat.id,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None


async def _send_truncated(
    *,
    message: Message,
    html_text: str,
    reply_to_message_id: int | None,
) -> Message | None:
    """Last resort when Telegram still calls a split piece too long.

    Deliberately crude: strip the markup, cut hard, say so. A piece that got
    here is already a bug in `split_html`, and the only goal left is that the
    chat sees the words instead of nothing.
    """
    plain = unescape(_HTML_TAG_RE.sub("", html_text))[: TELEGRAM_MESSAGE_LIMIT - 40]
    try:
        return await message.answer(
            f"{html_escape(plain)}\n\n…",
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as exc:
        logger.warning(
            "Truncated fallback could not be delivered either",
            chat_id=message.chat.id,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None


async def send_html_parts(
    *,
    message: Message,
    parts: list[str],
    reply_to_message_id: int | None,
    language: str,
    already_delivered: bool = False,
) -> list[Message]:
    """Send pre-split pieces as consecutive messages; return the ones that landed.

    **Every piece quotes the source**, not only the first. It used to be the
    first alone (quoting one message three times looked like three answers),
    and that was a delivery defect, not a style choice: in a channel's
    discussion group a reply is the ONLY thing that puts a message into a
    post's comment thread -- `sendMessage` has no thread parameter outside
    forums. An unquoted continuation went to the group's main feed, so under
    the post readers saw "Расшифровка (1/2)" and never part two (found
    2026-11-12 on a nine-minute voice post). The "(n/m)" header already says
    the pieces belong together.

    Stops at the first piece that cannot be delivered rather than sending the
    tail on its own: a reply whose opening is missing is harder to read than a
    reply that is visibly cut short, and the caller is told how far it got.

    **Once anything has reached the chat, a later failure degrades instead of
    raising.** Raising there was a real defect: `TelegramRetryAfter` is a
    *sibling* of `TelegramBadRequest`, not a subclass, so a throttled part two
    unwound the whole handler with part one already visible -- and the caller's
    post-send bookkeeping never ran. For a transcription that means a bot
    message the chat can read with no migration-028 routing row behind it, so
    a reply to those words is answered as if the bot had spoken them. Nothing
    reaches the user either way (the global error handler answers only
    CallbackQuery), so raising bought nothing and cost the bookkeeping.

    `already_delivered` lets a caller say that something is on screen *before*
    this call: `ProgressNotice.finish` edits the placeholder into part one and
    then passes `parts[1:]` here, so a failure on this call's own first element
    is still a partial delivery. Keying the behaviour on `index == 0` instead
    would miss exactly that case.
    """
    sent: list[Message] = []
    for index, part in enumerate(parts):
        quote = reply_to_message_id
        try:
            delivered = await _send_one(
                message=message,
                html_text=part,
                reply_to_message_id=quote,
                language=language,
                continuation=index > 0 or already_delivered,
            )
        except Exception as exc:
            too_long = isinstance(exc, TelegramBadRequest) and any(
                marker in str(exc).lower() for marker in _TOO_LONG_MARKERS
            )
            if not too_long:
                if not (sent or already_delivered):
                    # Nothing is on screen yet, so unwinding leaves no
                    # half-finished state and the caller's own error handling
                    # (or the global one) is still the right place for this.
                    raise
                logger.warning(
                    "A continuation could not be delivered; keeping what landed",
                    chat_id=message.chat.id,
                    part_index=index + 1,
                    part_count=len(parts),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                break
            logger.warning(
                "Telegram rejected a split piece as too long; truncating",
                chat_id=message.chat.id,
                part_index=index,
                part_count=len(parts),
                parsed_length=parsed_length(part),
            )
            delivered = await _send_truncated(
                message=message, html_text=part, reply_to_message_id=quote
            )
        if delivered is None:
            break
        sent.append(delivered)
    return sent


async def send_quoted_reply_all(
    *,
    message: Message,
    html_text: str,
    reply_to_message_id: int | None,
    language: str,
) -> list[Message]:
    """`send_quoted_reply`, but returning every message the answer occupies."""
    return await send_html_parts(
        message=message,
        parts=split_html(html_text),
        reply_to_message_id=reply_to_message_id,
        language=language,
    )


async def send_quoted_reply(
    *,
    message: Message,
    html_text: str,
    reply_to_message_id: int | None,
    language: str,
) -> Message | None:
    """Send an answer, splitting it if Telegram would reject it as too long.

    Returns the FIRST message of the answer, or None if nothing was delivered
    -- the contract callers already rely on for their post-send bookkeeping,
    which must run whether or not the text reached the chat because the AI
    call has been paid for either way.
    """
    sent = await send_quoted_reply_all(
        message=message,
        html_text=html_text,
        reply_to_message_id=reply_to_message_id,
        language=language,
    )
    return sent[0] if sent else None


async def react_to_silence(
    message: Message,
    config: ChatConfig,
    gate_decision: GateDecision,
    abuse_checker: AntiAbuseChecker,
) -> None:
    """R-5: tier-3 silence -> optionally react instead of a text reply.

    ``gate_decision.suggested_emoji`` is only ever populated on the tier-3
    ``llm_judge`` path (ADR-0004 Decision 4) — every other tier leaves it
    ``None``, so this is a no-op there. Gated on ``reactions_enabled`` only,
    never ``reactions_history_enabled`` (Decision 3): R-5 needs no history at
    all, ``llm_judge`` decides live, per-call.

    Anti-abuse: this path returns before ``TextProcessingPipeline.process()``,
    so the pipeline's Stage 1 abuse gate never runs for it. Setting a reaction
    is an outbound, user-visible action, so it must respect the same "be quiet
    toward this user" signal a text reply would. The cooldown is probed
    read-only and *after* the cheap guards, so a message the bot was never
    going to react to costs no query at all.
    """
    if not config.reactions_enabled or message.bot is None:
        return

    emoji = ReactionSelector.select(gate_decision.suggested_emoji)
    if emoji is None:
        return

    user = message.from_user
    if user is not None and await abuse_checker.is_in_cooldown(message.chat.id, user.id):
        logger.debug(
            "Skipping silence reaction: user in cooldown",
            chat_id=message.chat.id,
            user_id=user.id,
        )
        return

    try:
        await set_reaction(
            message.bot,
            chat_id=message.chat.id,
            message_id=message.message_id,
            emoji=emoji,
        )
    except Exception as exc:
        # `set_reaction` swallows TelegramAPIError; this is a last-resort net
        # for anything else (e.g. a programming error after a signature change)
        # so a suppressed text reply never turns into an unhandled crash.
        logger.warning(
            "Failed to set reaction on silence",
            chat_id=message.chat.id,
            message_id=message.message_id,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def relevancy_allows_reply(
    *,
    message: Message,
    chat_config: ChatConfig,
    trigger_type: TriggerType,
    message_text: str,
    relevancy_gate: RelevancyGate,
    abuse_checker: AntiAbuseChecker,
) -> bool:
    """Whether an unprompted (RANDOM) reply has earned the right to be sent.

    Returns ``True`` unchanged for every non-RANDOM trigger — a mention, a
    trigger word or a direct reply is an explicit invitation and is never
    gated.

    The photo handler did not call this at all, so the bot could interject on
    any captioned photo regardless of relevance. That was the defect, not the
    duplication.
    """
    if trigger_type != TriggerType.RANDOM:
        return True

    gate_decision = await relevancy_gate.evaluate(
        chat_id=message.chat.id,
        message_text=message_text,
        config=chat_config,
        message_id=message.message_id,
        user_id=message.from_user.id if message.from_user else None,
    )
    if gate_decision.should_respond:
        return True

    await react_to_silence(message, chat_config, gate_decision, abuse_checker)
    return False


async def finish_reply(
    *,
    message: Message,
    result: PipelineResult,
    sent_message_id: int | None,
    reply_to_message_id: int | None,
    chat_config: ChatConfig,
    pipeline: TextProcessingPipeline,
    spend_limit_svc: SpendLimitService,
) -> None:
    """Everything that must happen after a reply is sent, in the right order.

    Order is load-bearing: ``post_send`` is what writes the cost row, so the
    spend check has to run after it or it reads a stale total and the card
    is always one message late.

    The spend check tells the bot admins in their DM, once per UTC day
    (``SpendLimitService.notify_admins_if_exceeded``). It used to post a
    warning into this chat under every reply for the rest of the day — to
    members who can do nothing about it.

    The photo path previously did only ``post_send``, so an AI-chosen sticker
    was computed and dropped, and the daily spend check never ran for a
    photo reply — a limit that silently does not apply to part of the traffic
    is worse than no limit, because it still reads as enforced.

    ``sent_message_id`` is ``None`` when the answer could not be delivered at
    all. That case must still reach ``post_send``: it writes the cost row, and
    the money was spent whether or not the text arrived. Skipping it would
    make an undeliverable answer invisible to ``SpendLimitService`` and to
    /costs — under-reporting real spend, which is the one direction a spend
    limit must never fail in.

    ``reply_to_message_id`` is the message the text answer quoted, and the
    sticker quotes it too, for the reason every split piece does (see
    `send_html_parts`): in a channel's comment thread an unquoted send lands
    in the group's main feed, not under the post. ``allow_sending_without_reply``
    makes a source deleted in the meantime cost the quote, not the sticker.
    """
    if result.sticker_file_id:
        try:
            await message.answer_sticker(
                result.sticker_file_id,
                reply_to_message_id=reply_to_message_id,
                allow_sending_without_reply=True,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send AI-chosen sticker",
                sticker_file_id=result.sticker_file_id,
                error_type=type(exc).__name__,
            )

    await pipeline.post_send(result, bot_message_id=sent_message_id)

    try:
        await spend_limit_svc.notify_admins_if_exceeded(message.bot)
    except Exception as exc:
        # The service never raises; this guards the reply path against a
        # stand-in that does (and against the next refactor of the service).
        logger.warning(
            "Spend limit check failed",
            chat_id=chat_config.chat_id,
            error_type=type(exc).__name__,
        )
