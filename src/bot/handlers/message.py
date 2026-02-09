"""Main message handler — routes messages and delegates to TextProcessingPipeline."""

from __future__ import annotations

import random
import re

import structlog
from aiogram import F, Router
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
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
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and bot_id
        and message.reply_to_message.from_user.id == bot_id
    ):
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
    message_thread_id: int | None = None,
) -> None:
    """Handle incoming text messages through the AI pipeline."""
    bot_info = await message.bot.me() if message.bot else None
    bot_id = bot_info.id if bot_info else None

    should_reply, trigger_type = should_respond(message, chat_config, bot_id)

    if not should_reply:
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

    # Post-send tasks (non-blocking)
    await pipeline.post_send(result, bot_message_id=sent.message_id)
