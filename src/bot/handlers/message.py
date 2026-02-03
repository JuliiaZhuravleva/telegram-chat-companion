"""
Main message handler - routes messages and determines when to respond.
"""

import random

import structlog
from aiogram import F, Router
from aiogram.types import Message

from src.models.chat_config import ChatConfig

router = Router(name="messages")
logger = structlog.get_logger()


def should_respond(message: Message, config: ChatConfig) -> tuple[bool, str]:
    """
    Determine if the bot should respond to this message.

    Args:
        message: The incoming Telegram message.
        config: Resolved per-chat configuration.

    Returns:
        Tuple of (should_respond, trigger_type)
        trigger_type can be: "trigger", "reply", "random", "none"
    """
    text = (message.text or message.caption or "").lower()

    # Check for trigger words
    for trigger in config.trigger_words:
        if trigger.lower() in text:
            return True, "trigger"

    # Check if this is a reply to the bot's message
    if message.reply_to_message and message.reply_to_message.from_user:
        # We'll need to check if the reply is to our bot
        # For now, we'll implement this when we have the bot info
        pass

    # Random response chance
    if random.random() < config.random_response_chance:
        return True, "random"

    return False, "none"


@router.message(F.text)
async def handle_text_message(message: Message, chat_config: ChatConfig) -> None:
    """Handle incoming text messages."""
    should_reply, trigger_type = should_respond(message, chat_config)

    if not should_reply:
        return

    logger.info(
        "Processing message",
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else None,
        trigger_type=trigger_type,
    )

    # Placeholder until AI response flow is wired up
    await message.reply("Got it! AI responses coming soon.")
