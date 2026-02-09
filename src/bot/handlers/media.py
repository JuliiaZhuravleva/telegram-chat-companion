"""Media handlers — voice, photo, and sticker message processing."""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.bot.handlers.message import should_respond
from src.database.repositories.messages import MessageRepository
from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.modules.image import ImageAnalysisService
from src.services.modules.sticker import StickerLearningService
from src.services.modules.voice import VoiceTranscriptionService
from src.services.text.pipeline import TextProcessingPipeline
from src.utils.telegram import TelegramFileError, download_telegram_file

router = Router(name="media")
logger = structlog.get_logger(__name__)


# ── Voice / Video Note ────────────────────────────────────────────────


@router.message(F.voice | F.video_note)
async def handle_voice_message(
    message: Message,
    chat_config: ChatConfig,
    voice_service: FromDishka[VoiceTranscriptionService],
    bot: Bot,
    message_thread_id: int | None = None,
) -> None:
    """Handle voice messages and video notes via Whisper transcription."""
    # Determine type
    is_voice = message.voice is not None
    media = message.voice if is_voice else message.video_note

    if media is None:
        return

    # Check per-chat toggle
    if is_voice and not chat_config.transcribe_voice:
        return
    if not is_voice and not chat_config.transcribe_video_notes:
        return

    # Download audio
    try:
        audio_data = await download_telegram_file(bot, media.file_id)
    except TelegramFileError:
        logger.warning(
            "Failed to download voice file",
            chat_id=message.chat.id,
            file_id=media.file_id,
        )
        return

    # Send typing action in parallel with transcription (route to correct topic)
    user = message.from_user
    user_name = (user.first_name if user else None) or "Unknown"
    message_type = "voice" if is_voice else "video_note"

    typing_task = asyncio.create_task(
        bot.send_chat_action(
            message.chat.id,
            ChatAction.TYPING,
            message_thread_id=message_thread_id,
        )
    )

    result = await voice_service.transcribe(
        audio_data=audio_data,
        chat_id=message.chat.id,
        message_id=message.message_id,
        user_first_name=user_name,
        message_type=message_type,
        language=chat_config.language,
    )

    # Ensure typing task completes (ignore errors)
    with contextlib.suppress(Exception):
        await typing_task

    if result is None:
        return

    # Send formatted reply (reply routes to same topic via reply_to_message_id)
    reply_text = VoiceTranscriptionService.format_reply(user_name, result.text)
    await message.reply(reply_text, parse_mode="Markdown")


# ── Photo ─────────────────────────────────────────────────────────────


@router.message(F.photo)
async def handle_photo_message(
    message: Message,
    chat_config: ChatConfig,
    image_service: FromDishka[ImageAnalysisService],
    pipeline: FromDishka[TextProcessingPipeline],
    message_repo: FromDishka[MessageRepository],
    bot: Bot,
    message_thread_id: int | None = None,
) -> None:
    """Handle photo messages — analyze and optionally respond."""
    if not chat_config.image_analysis_enabled:
        return

    if not message.photo:
        return

    # Select highest resolution (last in array)
    photo = message.photo[-1]

    # Download image
    try:
        image_data = await download_telegram_file(bot, photo.file_id)
    except TelegramFileError:
        logger.warning(
            "Failed to download photo",
            chat_id=message.chat.id,
            file_id=photo.file_id,
        )
        return

    # Analyze image
    description = await image_service.analyze(image_data)

    logger.info(
        "Photo analysis result",
        chat_id=message.chat.id,
        has_description=description is not None,
        has_caption=bool(message.caption),
    )

    if description is None:
        return

    caption = message.caption

    if caption:
        # photo_with_text: decide whether to respond via text pipeline
        bot_info = await bot.me()
        respond, trigger_type = should_respond(message, chat_config, bot_info.id)

        if not respond:
            # Still save the description to message history
            await _update_message_content(
                message_repo, message.chat.id, message.message_id, description
            )
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

        result = await pipeline.process(
            chat_id=message.chat.id,
            user_id=user_id,
            user_name=user_name,
            message_text=caption,
            trigger_type=trigger_type,
            config=chat_config,
            reply_author=reply_author,
            reply_text=reply_text,
            reply_is_bot=reply_is_bot,
            image_context=description,
            message_thread_id=message_thread_id,
        )

        if not result.should_respond or not result.html_text:
            return

        reply_to = (
            message.message_id if trigger_type != TriggerType.RANDOM else None
        )

        # Send to correct topic in forum chats
        # Note: message.answer() inherits message_thread_id from the original message
        sent = await message.answer(
            result.html_text,
            parse_mode="HTML",
            reply_to_message_id=reply_to,
        )

        await pipeline.post_send(result, bot_message_id=sent.message_id)
    else:
        # photo_only: save description to message history, no response
        await _update_message_content(
            message_repo, message.chat.id, message.message_id, description
        )


async def _update_message_content(
    message_repo: MessageRepository,
    chat_id: int,
    message_id: int,
    description: str,
) -> None:
    """Update a message's content with the image description.

    The message was already saved by MessageSaverMiddleware.
    We update its content field with the vision analysis result.
    """
    try:
        await message_repo.save(
            chat_id=chat_id,
            message_id=message_id,
            message_type="photo",
            content=f"[Image: {description}]",
        )
    except Exception:
        logger.warning(
            "Failed to update message with image description",
            chat_id=chat_id,
            message_id=message_id,
        )


# ── Sticker ───────────────────────────────────────────────────────────


@router.message(F.sticker)
async def handle_sticker_message(
    message: Message,
    chat_config: ChatConfig,
    sticker_service: FromDishka[StickerLearningService],
    message_repo: FromDishka[MessageRepository],
    bot: Bot,
) -> None:
    """Handle sticker messages — learn via Vision API (static only)."""
    if not chat_config.sticker_learning_enabled:
        return

    sticker = message.sticker
    if sticker is None:
        return

    # Phase 2: skip animated/video stickers (no tgs-renderer)
    if sticker.is_animated or sticker.is_video:
        logger.debug(
            "Skipping animated/video sticker",
            file_unique_id=sticker.file_unique_id,
        )
        return

    # Download sticker image
    try:
        image_data = await download_telegram_file(bot, sticker.file_id)
    except TelegramFileError:
        logger.warning(
            "Failed to download sticker",
            chat_id=message.chat.id,
            file_id=sticker.file_id,
        )
        return

    # Get preceding messages for usage context
    preceding: list[str] = []
    try:
        recent = await message_repo.get_recent(
            message.chat.id, limit=3, exclude_bot=True
        )
        preceding = [
            r["content"]
            for r in reversed(recent)
            if r["content"] and r["message_id"] != message.message_id
        ]
    except Exception:
        pass

    # Learn sticker (silent — no response to chat)
    await sticker_service.learn(
        sticker=sticker,
        image_data=image_data,
        preceding_messages=preceding or None,
    )
