"""Main message handler — routes messages and delegates to TextProcessingPipeline."""

from __future__ import annotations

import random
import re
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.costs.spend_limit import SpendLimitService
from src.services.relevancy.gate import RelevancyGate
from src.services.text.pipeline import TextProcessingPipeline
from src.utils.telegram import typing_indicator

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


@router.message(F.text)
async def handle_text_message(
    message: Message,
    chat_config: ChatConfig,
    pipeline: FromDishka[TextProcessingPipeline],
    relevancy_gate: FromDishka[RelevancyGate],
    spend_limit_svc: FromDishka[SpendLimitService],
    bot: Bot,
    message_thread_id: int | None = None,
    **kwargs: Any,
) -> None:
    """Handle incoming text messages through the AI pipeline."""
    bot_id: int | None = kwargs.get("bot_id")
    if bot_id is None:
        bot_info = await bot.me()
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

    # "Typing" indicator: shown for mention/trigger/reply, NOT for unsolicited
    # random replies — even after the relevancy gate approves a RANDOM trigger,
    # pipeline.process() can still suppress the reply (blacklist/cooldown/abuse
    # checks), and announcing a reply that never arrives would be a lie (Q1).
    # Intrusiveness of "typing" before an unrequested reply is the other half
    # of the owner's rationale.
    async with typing_indicator(
        bot,
        message.chat.id,
        message_thread_id,
        enabled=trigger_type != TriggerType.RANDOM,
    ):
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
