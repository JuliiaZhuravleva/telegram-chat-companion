"""Main message handler — routes messages and delegates to TextProcessingPipeline."""

from __future__ import annotations

from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.bot.reply_flow import finish_reply, relevancy_allows_reply
from src.bot.utils import extract_reply_context, should_respond
from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.abuse.checker import AntiAbuseChecker
from src.services.costs.spend_limit import SpendLimitService
from src.services.relevancy.gate import RelevancyGate
from src.services.text.pipeline import TextProcessingPipeline
from src.utils.telegram import typing_indicator

router = Router(name="messages")
logger = structlog.get_logger()


@router.message(F.text)
async def handle_text_message(
    message: Message,
    chat_config: ChatConfig,
    pipeline: FromDishka[TextProcessingPipeline],
    relevancy_gate: FromDishka[RelevancyGate],
    spend_limit_svc: FromDishka[SpendLimitService],
    abuse_checker: FromDishka[AntiAbuseChecker],
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

    # Relevancy gate: filter random triggers for natural participation.
    # Shared with the photo path (src/bot/reply_flow.py) — it used to be
    # inline here and absent there, which is exactly how it went missing.
    if not await relevancy_allows_reply(
        message=message,
        chat_config=chat_config,
        trigger_type=trigger_type,
        message_text=message.text or "",
        relevancy_gate=relevancy_gate,
        abuse_checker=abuse_checker,
    ):
        return

    user = message.from_user
    user_id = user.id if user else 0
    user_name = (user.first_name if user else None) or "Unknown"

    # Extract reply context (full message + manually-highlighted quote, if any)
    reply_ctx = extract_reply_context(message)

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
            reply_author=reply_ctx.author,
            reply_text=reply_ctx.text,
            reply_is_bot=reply_ctx.is_bot,
            reply_quote_text=reply_ctx.quote_text,
            reply_quote_is_manual=reply_ctx.quote_is_manual,
            message_thread_id=message_thread_id,
            message_id=message.message_id,
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

    # Sticker -> post_send -> spend warning, shared with the photo path.
    await finish_reply(
        message=message,
        result=result,
        sent_message_id=sent.message_id,
        chat_config=chat_config,
        pipeline=pipeline,
        spend_limit_svc=spend_limit_svc,
    )
