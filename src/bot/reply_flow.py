"""Shared reply mechanics for the text and photo handlers.

`handlers/media.py` grew as a partial copy of `handlers/message.py`, and the
copy drifted: three things present in the text path never reached it — the
relevancy gate, the spend-limit warning and the AI-chosen sticker. The first
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


async def _send_one(
    *,
    message: Message,
    html_text: str,
    reply_to_message_id: int | None,
    language: str,
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

    Returns the sent `Message`, or None if it could not be delivered at all --
    callers must still run their post-send bookkeeping in that case, because
    the AI call has already been made and its cost has to be recorded whether
    or not the text reached the chat.
    """
    try:
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

    Only the first piece quotes the source. Telegram renders consecutive
    messages from the same sender as one block, and quoting the same message
    three times reads as three separate answers to it.

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
        quote = reply_to_message_id if index == 0 else None
        try:
            delivered = await _send_one(
                message=message,
                html_text=part,
                reply_to_message_id=quote,
                language=language,
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
    chat_config: ChatConfig,
    pipeline: TextProcessingPipeline,
    spend_limit_svc: SpendLimitService,
) -> None:
    """Everything that must happen after a reply is sent, in the right order.

    Order is load-bearing: ``post_send`` is what writes the cost row, so the
    spend check has to run after it or it reads a stale total and the warning
    is always one message late.

    The photo path previously did only ``post_send``, so an AI-chosen sticker
    was computed and dropped, and the daily spend warning never fired for a
    photo reply — a limit that silently does not apply to part of the traffic
    is worse than no limit, because it still reads as enforced.

    ``sent_message_id`` is ``None`` when the answer could not be delivered at
    all. That case must still reach ``post_send``: it writes the cost row, and
    the money was spent whether or not the text arrived. Skipping it would
    make an undeliverable answer invisible to ``SpendLimitService`` and to
    /costs — under-reporting real spend, which is the one direction a spend
    limit must never fail in.
    """
    if result.sticker_file_id:
        try:
            await message.answer_sticker(result.sticker_file_id)
        except Exception as exc:
            logger.warning(
                "Failed to send AI-chosen sticker",
                sticker_file_id=result.sticker_file_id,
                error_type=type(exc).__name__,
            )

    await pipeline.post_send(result, bot_message_id=sent_message_id)

    warning = await spend_limit_svc.get_warning_if_exceeded(chat_config.language)
    if warning:
        try:
            await message.answer(warning)
        except Exception as exc:
            logger.warning(
                "Failed to send spend limit warning",
                chat_id=message.chat.id,
                error_type=type(exc).__name__,
            )
