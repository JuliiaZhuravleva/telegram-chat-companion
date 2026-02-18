"""Command handlers: /start, /help, /summary."""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.help import help_keyboard
from src.models.chat_config import ChatConfig
from src.services.modules.summary import SummaryService
from src.services.text.formatter import markdown_to_html

router = Router(name="commands")
logger = structlog.get_logger(__name__)

# --- i18n ---

_HELP_TEXT = {
    "ru": (
        "🤖 **Чат-компаньон**\n\n"
        "Я — участник чата, а не просто бот-команда.\n"
        "Обращайтесь по триггер-слову или отвечайте на мои сообщения.\n\n"
        "**Возможности:**\n"
        "{features}\n\n"
        "**Как пользоваться:**\n"
        "- Напишите триггер-слово ({triggers}) в сообщении\n"
        "- Ответьте на моё сообщение\n"
        "- Иногда я отвечаю случайно 😉"
    ),
    "en": (
        "🤖 **Chat Companion**\n\n"
        "I'm a chat participant, not just a command bot.\n"
        "Mention a trigger word or reply to my messages.\n\n"
        "**Features:**\n"
        "{features}\n\n"
        "**How to use:**\n"
        "- Write a trigger word ({triggers}) in your message\n"
        "- Reply to one of my messages\n"
        "- Sometimes I reply randomly 😉"
    ),
}

_FEATURES = {
    "ru": {
        "chat": "💬 Общение — отвечаю на сообщения с помощью ИИ",
        "voice": "🎤 Транскрибация голосовых сообщений",
        "video_notes": "📹 Транскрибация видеосообщений",
        "summary": "📋 Саммари чата (/summary)",
        "memory": "🧠 Память — запоминаю контекст разговора",
    },
    "en": {
        "chat": "💬 Chat — I respond to messages using AI",
        "voice": "🎤 Voice message transcription",
        "video_notes": "📹 Video note transcription",
        "summary": "📋 Chat summary (/summary)",
        "memory": "🧠 Memory — I remember conversation context",
    },
}

_START_TEXT = {
    "ru": "Привет! Я чат-компаньон. Напишите /help для списка возможностей.",
    "en": "Hello! I'm a chat companion. Type /help to see what I can do.",
}


def _build_feature_list(config: ChatConfig, language: str) -> str:
    """Build dynamic feature list based on enabled features."""
    lang = language if language in _FEATURES else "ru"
    features = _FEATURES[lang]
    lines: list[str] = []

    lines.append(f"• {features['chat']}")

    if config.transcribe_voice:
        lines.append(f"• {features['voice']}")
    if config.transcribe_video_notes:
        lines.append(f"• {features['video_notes']}")
    if config.save_messages:
        lines.append(f"• {features['summary']}")
    if config.rag_enabled:
        lines.append(f"• {features['memory']}")

    return "\n".join(lines)


@router.message(Command("start"))
async def handle_start(message: Message, chat_config: ChatConfig) -> None:
    """Handle /start command."""
    lang = chat_config.language if chat_config.language in _START_TEXT else "ru"
    await message.answer(_START_TEXT[lang])


@router.message(Command("help"))
async def handle_help(message: Message, chat_config: ChatConfig) -> None:
    """Handle /help command — dynamic feature list with summary buttons."""
    lang = chat_config.language if chat_config.language in _HELP_TEXT else "ru"
    user_id = message.from_user.id if message.from_user else 0

    features = _build_feature_list(chat_config, lang)
    triggers = ", ".join(chat_config.trigger_words)

    text = _HELP_TEXT[lang].format(features=features, triggers=triggers)
    html = markdown_to_html(text)

    keyboard = help_keyboard(
        user_id,
        save_messages=chat_config.save_messages,
        language=lang,
        chat_type=message.chat.type,
    )

    await message.answer(html, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("summary"), F.chat.type.in_({"group", "supergroup"}))
async def handle_summary(
    message: Message,
    chat_config: ChatConfig,
    summary_service: FromDishka[SummaryService],
    message_thread_id: int | None = None,
) -> None:
    """Handle /summary command — generate chat summary.

    In forum chats, summarizes only messages from the current topic.
    """
    if not chat_config.save_messages:
        lang = chat_config.language
        if lang == "ru":
            await message.answer("Сохранение сообщений отключено для этого чата.")
        else:
            await message.answer("Message saving is disabled for this chat.")
        return

    lang = chat_config.language
    processing = "⏳ Генерирую саммари..." if lang == "ru" else "⏳ Generating summary..."
    placeholder = await message.answer(processing)

    # Topic-filtered summary in forum chats
    html = await summary_service.generate(
        message.chat.id,
        language=lang,
        message_thread_id=message_thread_id,
    )

    if html:
        try:
            await placeholder.edit_text(html, parse_mode="HTML")
        except Exception:
            # If edit fails (e.g., message too long), send as new message
            await placeholder.delete()
            await message.answer(html, parse_mode="HTML")
    else:
        fail_msg = "Не удалось создать саммари." if lang == "ru" else "Failed to generate summary."
        await placeholder.edit_text(fail_msg)
