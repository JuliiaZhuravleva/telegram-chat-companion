"""Command handlers: /start, /help, /summary, /remember, /kb."""

from __future__ import annotations

import json
from datetime import datetime
from html import escape as html_escape
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.admin_kb import kb_view_keyboard
from src.bot.keyboards.help import help_keyboard
from src.bot.utils import resolve_display_name, safe_edit_text
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.knowledge import KnowledgeRepository
from src.models.chat_config import ChatConfig
from src.services.ai.router import AIRouter
from src.services.modules.summary import SummaryService
from src.services.text.formatter import markdown_to_html
from src.utils import parse_admin_ids
from src.utils.telegram import typing_indicator

router = Router(name="commands")
logger = structlog.get_logger(__name__)

_KB_PREDICATE_MANUAL = "факт"
_KB_PAGE_SIZE_DM = 5
_KB_PAGE_SIZE_GROUP = 8

_REMEMBER_NO_REPLY = {
    "ru": "↩️ Используйте /remember в ответ на сообщение, которое нужно сохранить.",
    "en": "↩️ Use /remember as a reply to the message you want to save.",
}
_REMEMBER_MALFORMED = {
    "ru": "🤔 Не смог распознать факт. Формат: `/remember тема: значение` (в ответ на сообщение).",
    "en": "🤔 Couldn't parse that. Format: `/remember topic: value` (as a reply to a message).",
}
_REMEMBER_SUCCESS = {
    "ru": "✅ Сохранено: **{subject}** — {value}",
    "en": "✅ Saved: **{subject}** — {value}",
}
_REMEMBER_SUCCESS_NO_EMBED = {
    "ru": (
        "✅ Сохранено: **{subject}** — {value}\n"
        "⚠️ Семантический поиск для этого факта сейчас недоступен — "
        "он виден только через /kb."
    ),
    "en": (
        "✅ Saved: **{subject}** — {value}\n"
        "⚠️ Semantic search is unavailable for this fact right now — "
        "it is only visible via /kb."
    ),
}
_REMEMBER_KB_DISABLED = {
    "ru": "📚 База знаний отключена для этого чата. Включите её в админ-панели.",
    "en": "📚 The knowledge base is disabled for this chat. Enable it from the admin panel.",
}
_REMEMBER_NOT_ALLOWED = {
    "ru": "🚫 Сохранять факты могут только организаторы или админ бота.",
    "en": "🚫 Only organizers or the bot admin can save facts.",
}

_KB_EMPTY_DM = {
    "ru": "📚 База знаний пока пуста. Организаторы могут добавить факты через /remember или из админ-панели.",
    "en": "📚 The knowledge base is empty. Organizers can add facts via /remember or from the admin panel.",
}
_KB_EMPTY_GROUP = {
    "ru": "📚 База знаний этого чата пока пуста.",
    "en": "📚 This chat's knowledge base is empty.",
}
_KB_TOPIC_GENERAL = {"ru": "Общее", "en": "General"}


async def _is_kb_organizer(
    user_id: int | None,
    chat_id: int,
    chat_settings_repo: ChatSettingsRepository,
) -> bool:
    """Manual /remember gate, organizer half (rank 3).

    Bot-admin status (rank 4) is checked by the caller, which already holds
    the fetched admin_ids — this function deliberately does not re-query it.
    """
    if user_id is None:
        return False

    settings = await chat_settings_repo.get(chat_id)
    if not settings:
        return False
    raw = settings.get("kb_organizer_ids")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = []
    if not isinstance(raw, list):
        return False
    return user_id in {int(v) for v in raw}


def _format_kb_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    return "?"


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

_SUMMARY_DM_TEXT = {
    "ru": "📋 /summary доступен только в групповых чатах.",
    "en": "📋 /summary is only available in group chats.",
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


@router.message(Command("summary"), F.chat.type == "private")
async def handle_summary_dm(message: Message, chat_config: ChatConfig) -> None:
    """Handle /summary in a private (DM) chat — inform user it's group-only."""
    lang = chat_config.language if chat_config.language in _SUMMARY_DM_TEXT else "ru"
    await message.answer(_SUMMARY_DM_TEXT[lang])


# ---------------------------------------------------------------------------
# /remember — manual Knowledge Base fact save (A4, ADR-0003, Phase 1: manual MVP)
# ---------------------------------------------------------------------------


@router.message(Command("remember"))
async def handle_remember(
    message: Message,
    chat_config: ChatConfig,
    knowledge_repo: FromDishka[KnowledgeRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    ai_router: FromDishka[AIRouter],
    bot: Bot,
    message_thread_id: int | None = None,
) -> None:
    """Manually save a fact via reply: ``/remember <subject>: <value>``.

    Phase 1 has no extraction/reconciliation (PH2 scope) — this inserts
    directly as an ``active`` fact (ADR-0003's Phase-1 scope note).
    """
    lang = chat_config.language if chat_config.language in _REMEMBER_MALFORMED else "ru"

    if not message.reply_to_message:
        await message.reply(_REMEMBER_NO_REPLY[lang])
        return

    if not chat_config.kb_enabled:
        await message.reply(_REMEMBER_KB_DISABLED[lang])
        return

    user_id = message.from_user.id if message.from_user else None
    admin_ids_raw = await bot_config_repo.get("admin_ids")
    is_bot_admin = user_id is not None and user_id in parse_admin_ids(admin_ids_raw)

    if not is_bot_admin and not await _is_kb_organizer(
        user_id, message.chat.id, chat_settings_repo
    ):
        await message.reply(_REMEMBER_NOT_ALLOWED[lang])
        return

    args = message.text or ""
    args = args.split(maxsplit=1)[1] if " " in args else ""
    if ":" not in args:
        await message.reply(markdown_to_html(_REMEMBER_MALFORMED[lang]), parse_mode="HTML")
        return

    subject, _, value = args.partition(":")
    subject = subject.strip()
    value = value.strip()
    if not subject or not value:
        await message.reply(markdown_to_html(_REMEMBER_MALFORMED[lang]), parse_mode="HTML")
        return

    fact_text = f"{subject}: {value}"

    embedding: list[float] | None = None
    try:
        async with typing_indicator(bot, message.chat.id, message_thread_id):
            embedding_result = await ai_router.generate_embedding(fact_text)
        embedding = embedding_result.embedding
    except Exception:
        logger.warning("kb_remember_embedding_failed", chat_id=message.chat.id)

    authority_level = 4 if is_bot_admin else 3

    await knowledge_repo.upsert_fact(
        chat_id=message.chat.id,
        subject=subject,
        predicate=_KB_PREDICATE_MANUAL,
        value=value,
        fact_text=fact_text,
        source="manual",
        embedding=embedding,
        source_message_id=message.reply_to_message.message_id,
        source_user_id=user_id,
        authority_level=authority_level,
        confidence=None,
    )

    template = _REMEMBER_SUCCESS if embedding is not None else _REMEMBER_SUCCESS_NO_EMBED
    text = template[lang].format(subject=subject, value=value)
    await message.reply(markdown_to_html(text), parse_mode="HTML")


# ---------------------------------------------------------------------------
# /kb — view active Knowledge Base facts (public, group=terse / DM=sectioned)
# ---------------------------------------------------------------------------


async def _resolve_author_label(
    bot: Bot | None, chat_id: int, user_id: int | None, lang: str, cache: dict[int, str]
) -> str:
    fallback = "участник" if lang == "ru" else "member"
    if user_id is None:
        return fallback
    if user_id in cache:
        return cache[user_id]
    label = await resolve_display_name(bot, chat_id, user_id, fallback)
    cache[user_id] = label
    return label


def _slice_page(
    facts: list[dict[str, Any]], page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    """Slice the (already-fetched, in-memory) active-facts list for a page.

    Repository-level LIMIT/OFFSET isn't needed at Phase-1 volumes (a handful
    of manually-entered facts per chat, per ADR-0003's budget rationale).
    """
    total_pages = max(1, (len(facts) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    return facts[start : start + page_size], total_pages


async def _render_kb_dm(
    bot: Bot | None, chat_id: int, facts: list[dict[str, Any]], lang: str, page: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    page_facts, total_pages = _slice_page(facts, page, _KB_PAGE_SIZE_DM)

    title = "📚 **База знаний чата**" if lang == "ru" else "📚 **Chat Knowledge Base**"
    lines = [title, ""]
    cache: dict[int, str] = {}
    current_topic: str | None = None
    for fact in page_facts:
        topic = fact.get("topic") or _KB_TOPIC_GENERAL[lang]
        if topic != current_topic:
            lines.append(f"**{topic}**")
            current_topic = topic
        author = await _resolve_author_label(bot, chat_id, fact.get("source_user_id"), lang, cache)
        date_str = _format_kb_date(fact.get("updated_at"))
        updated_label = "обновлено" if lang == "ru" else "updated"
        lines.append(f"• {fact['subject']} — {fact['predicate']}: {fact['value']}")
        lines.append(f"  _{updated_label} {date_str}, {author}_")

    lines.append("")
    if total_pages > 1:
        lines.append(f"◀️ {page + 1}/{total_pages} ▶️")

    html = markdown_to_html("\n".join(lines).strip())
    keyboard = (
        kb_view_keyboard(lang, page=page, total_pages=total_pages) if total_pages > 1 else None
    )
    return html, keyboard


def _render_kb_group(
    facts: list[dict[str, Any]], lang: str, page: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    page_facts, total_pages = _slice_page(facts, page, _KB_PAGE_SIZE_GROUP)
    total = len(facts)

    header = (
        f"📚 База знаний ({total} факт(а/ов)):"
        if lang == "ru"
        else f"📚 Knowledge base ({total} facts):"
    )
    lines = [header]
    for fact in page_facts:
        # Facts are raw user input; the bot-wide default parse_mode is HTML,
        # so unescaped '&'/'<' would break rendering or inject markup.
        lines.append(f"• {html_escape(fact['subject'])} — {html_escape(fact['value'])}")

    if total_pages > 1:
        lines.append(f"◀️ {page + 1}/{total_pages} ▶️")

    keyboard = (
        kb_view_keyboard(lang, page=page, total_pages=total_pages) if total_pages > 1 else None
    )
    return "\n".join(lines), keyboard


@router.message(Command("kb"), F.chat.type == "private")
async def handle_kb_view_dm(
    message: Message,
    chat_config: ChatConfig,
    knowledge_repo: FromDishka[KnowledgeRepository],
) -> None:
    """``/kb`` in DM: bold-title, topic-sectioned, paginated (5/page)."""
    lang = chat_config.language if chat_config.language in _KB_EMPTY_DM else "ru"
    facts = await knowledge_repo.get_active_facts(message.chat.id)

    if not facts:
        await message.answer(_KB_EMPTY_DM[lang])
        return

    html, keyboard = await _render_kb_dm(message.bot, message.chat.id, facts, lang, 0)
    await message.answer(html, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("kb"), F.chat.type.in_({"group", "supergroup"}))
async def handle_kb_view_group(
    message: Message,
    chat_config: ChatConfig,
    knowledge_repo: FromDishka[KnowledgeRepository],
) -> None:
    """``/kb`` in group: terse flat list, no provenance, paginated (8/page)."""
    lang = chat_config.language if chat_config.language in _KB_EMPTY_GROUP else "ru"
    facts = await knowledge_repo.get_active_facts(message.chat.id)

    if not facts:
        await message.answer(_KB_EMPTY_GROUP[lang])
        return

    text, keyboard = _render_kb_group(facts, lang, 0)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("kb_view:"))
async def handle_kb_view_page(
    callback: CallbackQuery,
    knowledge_repo: FromDishka[KnowledgeRepository],
) -> None:
    """Paginate an existing ``/kb`` view in place (public — no admin gating).

    Re-fetches and re-slices the same way the initial command did; renders
    group vs. DM the same way based on the callback's own chat type (the
    message being paginated always lives in the chat it was first sent in).
    """
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    parts = (callback.data or "").split(":")
    lang = parts[1] if len(parts) > 1 and parts[1] in ("ru", "en") else "ru"
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0

    chat_id = callback.message.chat.id
    facts = await knowledge_repo.get_active_facts(chat_id)
    if not facts:
        await callback.answer()
        return

    if callback.message.chat.type == "private":
        html, keyboard = await _render_kb_dm(callback.bot, chat_id, facts, lang, page)
        await safe_edit_text(callback.message, html, parse_mode="HTML", reply_markup=keyboard)
    else:
        text, keyboard = _render_kb_group(facts, lang, page)
        await safe_edit_text(callback.message, text, reply_markup=keyboard)

    await callback.answer()
