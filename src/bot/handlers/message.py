"""Main message handler — routes messages and delegates to TextProcessingPipeline."""

from __future__ import annotations

import random
import re
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.abuse.checker import AntiAbuseChecker
from src.services.costs.spend_limit import SpendLimitService
from src.services.modules.reactions.responder import set_reaction
from src.services.modules.reactions.selector import ReactionSelector
from src.services.relevancy.gate import GateDecision, RelevancyGate
from src.services.text.pipeline import TextProcessingPipeline

router = Router(name="messages")
logger = structlog.get_logger()


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


async def _react_to_silence(
    message: Message,
    config: ChatConfig,
    gate_decision: GateDecision,
    abuse_checker: AntiAbuseChecker,
) -> None:
    """R-5: tier-3 silence -> optionally react instead of a text reply.

    `gate_decision.suggested_emoji` is only ever populated on the tier-3
    `llm_judge` path (ADR-0004 Decision 4) -- every other tier leaves it
    `None`, so this is a no-op there. Gated on `reactions_enabled` only,
    never `reactions_history_enabled` (Decision 3): R-5 needs no history at
    all, `llm_judge` decides live, per-call.

    Anti-abuse: this path returns before `TextProcessingPipeline.process()`,
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


@router.message(F.text)
async def handle_text_message(
    message: Message,
    chat_config: ChatConfig,
    pipeline: FromDishka[TextProcessingPipeline],
    relevancy_gate: FromDishka[RelevancyGate],
    spend_limit_svc: FromDishka[SpendLimitService],
    abuse_checker: FromDishka[AntiAbuseChecker],
    message_thread_id: int | None = None,
    **kwargs: Any,
) -> None:
    """Handle incoming text messages through the AI pipeline."""
    bot_id: int | None = kwargs.get("bot_id")
    if bot_id is None:
        bot_info = await message.bot.me() if message.bot else None
        bot_id = bot_info.id if bot_info else None

    should_reply, trigger_type = should_respond(message, chat_config, bot_id)

    if not should_reply:
        return

    # Relevancy gate: filter random triggers for natural participation
    if trigger_type == TriggerType.RANDOM:
        gate_decision = await relevancy_gate.evaluate(
            chat_id=message.chat.id,
            message_text=message.text or "",
            config=chat_config,
        )
        if not gate_decision.should_respond:
            await _react_to_silence(message, chat_config, gate_decision, abuse_checker)
            return

    user = message.from_user
    user_id = user.id if user else 0
    user_name = (user.first_name if user else None) or "Unknown"

    # Extract reply context
    reply_author: str | None = None
    reply_text: str | None = None
    reply_is_bot = False
    if message.reply_to_message:
        rpl = message.reply_to_message
        if rpl.from_user:
            reply_author = rpl.from_user.first_name
            reply_is_bot = rpl.from_user.is_bot
        reply_text = (rpl.text or rpl.caption or "")[:500]

    logger.info(
        "Processing message",
        chat_id=message.chat.id,
        user_id=user_id,
        trigger_type=trigger_type.value,
        message_thread_id=message_thread_id,
    )

    result = await pipeline.process(
        chat_id=message.chat.id,
        user_id=user_id,
        user_name=user_name,
        message_text=message.text or "",
        trigger_type=trigger_type,
        config=chat_config,
        reply_author=reply_author,
        reply_text=reply_text,
        reply_is_bot=reply_is_bot,
        message_thread_id=message_thread_id,
    )

    if not result.should_respond or not result.html_text:
        logger.info(
            "Pipeline suppressed response",
            chat_id=message.chat.id,
            trigger_type=trigger_type.value,
            response_type=result.response_type.value if result.response_type else None,
            has_text=bool(result.html_text),
        )
        return

    # Random responses don't quote the triggering message
    reply_to = message.message_id if trigger_type != TriggerType.RANDOM else None

    # Send to correct topic in forum chats
    # Note: message.answer() inherits message_thread_id from the original message
    sent = await message.answer(
        result.html_text,
        parse_mode="HTML",
        reply_to_message_id=reply_to,
    )

    # Send sticker if AI chose one
    if result.sticker_file_id:
        try:
            await message.answer_sticker(result.sticker_file_id)
        except Exception:
            logger.warning(
                "Failed to send AI-chosen sticker",
                sticker_file_id=result.sticker_file_id,
            )

    # Post-send tasks (non-blocking)
    await pipeline.post_send(result, bot_message_id=sent.message_id)

    # Check daily spend limit AFTER usage is logged (post_send writes the row)
    warning = await spend_limit_svc.get_warning_if_exceeded(chat_config.language)
    if warning:
        try:
            await message.answer(warning)
        except Exception:
            logger.warning("Failed to send spend limit warning", chat_id=message.chat.id)
