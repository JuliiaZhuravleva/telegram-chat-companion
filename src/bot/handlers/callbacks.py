"""Callback query handlers for inline keyboards."""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.help import summary_keyboard
from src.models.chat_config import ChatConfig
from src.services.modules.summary import SummaryService

router = Router(name="callbacks")
logger = structlog.get_logger(__name__)

_NOT_YOUR_BUTTON = {
    "ru": "Эта кнопка не для вас.",
    "en": "This button is not for you.",
}


def _check_owner(callback: CallbackQuery, owner_id: int) -> bool:
    """Check if the callback sender is the button owner (0 = open to all)."""
    if owner_id == 0:
        return True
    return callback.from_user is not None and callback.from_user.id == owner_id


@router.callback_query(F.data.startswith("help_summary:"))
async def handle_summary_callback(
    callback: CallbackQuery,
    chat_config: ChatConfig,
    summary_service: FromDishka[SummaryService],
) -> None:
    """Handle summary button press from help keyboard."""
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Invalid callback data.")
        return

    try:
        owner_id = int(parts[1])
        count = int(parts[2])
    except ValueError:
        await callback.answer("Invalid callback data.")
        return

    # Clamp count to allowed values
    count = max(1, min(count, 1000))
    lang = chat_config.language if chat_config.language in _NOT_YOUR_BUTTON else "ru"

    if not _check_owner(callback, owner_id):
        await callback.answer(_NOT_YOUR_BUTTON[lang], show_alert=True)
        return

    processing = "⏳ Генерирую..." if lang == "ru" else "⏳ Generating..."
    await callback.answer(processing)

    html = await summary_service.generate(
        callback.message.chat.id if callback.message else 0,
        count=count,
        language=lang,
    )

    if not html:
        return

    user_id = callback.from_user.id if callback.from_user else 0
    keyboard = summary_keyboard(user_id, count, language=lang)

    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.edit_text(
                html,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            logger.warning("Failed to edit summary message")


@router.callback_query(F.data.startswith("help_close:"))
async def handle_close_callback(
    callback: CallbackQuery,
    chat_config: ChatConfig,
) -> None:
    """Handle close button press — delete the help message."""
    parts = (callback.data or "").split(":")
    if len(parts) != 2:
        await callback.answer("Invalid callback data.")
        return

    try:
        owner_id = int(parts[1])
    except ValueError:
        await callback.answer("Invalid callback data.")
        return

    lang = chat_config.language if chat_config.language in _NOT_YOUR_BUTTON else "ru"

    if not _check_owner(callback, owner_id):
        await callback.answer(_NOT_YOUR_BUTTON[lang], show_alert=True)
        return

    closed = "Закрыто" if lang == "ru" else "Closed"
    await callback.answer(closed)

    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.delete()
        except Exception:
            logger.warning("Failed to delete help message")
