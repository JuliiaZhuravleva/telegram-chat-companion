"""Command handlers: /start, /help, /summary, /remember, /kb."""

from __future__ import annotations

import json
import re
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
    "ru": "📋 {command} доступен только в групповых чатах.",
    "en": "📋 {command} is only available in group chats.",
}

# /summary <n> — E-1: optional message-count argument.
_SUMMARY_DEFAULT_COUNT = 100
_SUMMARY_MIN_COUNT = 20
_SUMMARY_MAX_COUNT = 1000

# /summary500 — E-2: the same summary at a fixed count, one word to type.
# Deliberately a plain constant rather than a parsed suffix: aiogram matches
# Command("summary500") exactly, so it never collides with Command("summary").
_SUMMARY500_COUNT = 500

_SUMMARY_TOO_FEW = {
    "ru": (
        "📋 Столько сообщений можно прочитать и самому. "
        "Минимум для /summary — {min} (максимум {max})."
    ),
    "en": (
        "📋 That few messages you can just read yourself. "
        "/summary needs at least {min} (up to {max})."
    ),
}
# The angle brackets are HTML entities on purpose: the bot's default parse_mode
# is HTML, so a literal "<число …>" makes Telegram reject the whole sendMessage
# with 'Unsupported start tag "число"' and the user gets no reply at all — the
# validation message silently fails exactly when it is needed. Verified against
# the live Bot API; a unit test cannot see it, because message.reply is mocked.
_SUMMARY_INVALID_COUNT = {
    "ru": "🤔 Не понял количество сообщений. Формат: /summary &lt;число от {min} до {max}&gt;.",
    "en": "🤔 Couldn't parse the message count. Format: /summary &lt;number from {min} to {max}&gt;.",
}

_SUMMARY_COUNT_ARG_RE = re.compile(r"\d+")


def _parse_summary_count(message_text: str | None) -> int | None:
    """Parse the optional ``/summary <n>`` argument.

    Returns ``None`` when no argument was given (caller applies the default).
    Raises ``ValueError`` for anything that isn't a plain non-negative
    integer (garbage input) — the caller turns that into a validation reply.
    Range checks (min/max) are the caller's responsibility.
    """
    raw = (message_text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    if not _SUMMARY_COUNT_ARG_RE.fullmatch(arg):
        raise ValueError(arg)
    return int(arg)


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


async def _reject_if_saving_disabled(message: Message, chat_config: ChatConfig, lang: str) -> bool:
    """Answer and return True when this chat keeps no messages to summarize."""
    if chat_config.save_messages:
        return False
    if lang == "ru":
        await message.answer("Сохранение сообщений отключено для этого чата.")
    else:
        await message.answer("Message saving is disabled for this chat.")
    return True


async def _deliver_summary(
    message: Message,
    summary_service: SummaryService,
    *,
    count: int,
    lang: str,
    message_thread_id: int | None,
) -> None:
    """Render a summary of ``count`` messages, editing a placeholder in place.

    Shared by /summary and /summary500 so the two cannot drift apart on
    placeholder copy, forum-topic filtering or the too-long-to-edit fallback.
    """
    processing = "⏳ Генерирую саммари..." if lang == "ru" else "⏳ Generating summary..."
    placeholder = await message.answer(processing)

    # Topic-filtered summary in forum chats
    html = await summary_service.generate(
        message.chat.id,
        count=count,
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


@router.message(Command("summary"), F.chat.type.in_({"group", "supergroup"}))
async def handle_summary(
    message: Message,
    chat_config: ChatConfig,
    summary_service: FromDishka[SummaryService],
    message_thread_id: int | None = None,
) -> None:
    """Handle /summary command — generate chat summary.

    Accepts an optional message-count argument: ``/summary <n>`` (default
    ``_SUMMARY_DEFAULT_COUNT``, min ``_SUMMARY_MIN_COUNT``, max
    ``_SUMMARY_MAX_COUNT``). In forum chats, summarizes only messages from
    the current topic, regardless of the requested count.
    """
    lang = chat_config.language if chat_config.language in _SUMMARY_INVALID_COUNT else "ru"

    if await _reject_if_saving_disabled(message, chat_config, lang):
        return

    try:
        requested_count = _parse_summary_count(message.text)
    except ValueError:
        await message.reply(
            _SUMMARY_INVALID_COUNT[lang].format(min=_SUMMARY_MIN_COUNT, max=_SUMMARY_MAX_COUNT)
        )
        return

    if requested_count is None:
        count = _SUMMARY_DEFAULT_COUNT
    elif requested_count < _SUMMARY_MIN_COUNT:
        await message.reply(
            _SUMMARY_TOO_FEW[lang].format(min=_SUMMARY_MIN_COUNT, max=_SUMMARY_MAX_COUNT)
        )
        return
    elif requested_count > _SUMMARY_MAX_COUNT:
        await message.reply(
            _SUMMARY_INVALID_COUNT[lang].format(min=_SUMMARY_MIN_COUNT, max=_SUMMARY_MAX_COUNT)
        )
        return
    else:
        count = requested_count

    await _deliver_summary(
        message,
        summary_service,
        count=count,
        lang=lang,
        message_thread_id=message_thread_id,
    )


@router.message(Command("summary500"), F.chat.type.in_({"group", "supergroup"}))
async def handle_summary500(
    message: Message,
    chat_config: ChatConfig,
    summary_service: FromDishka[SummaryService],
    message_thread_id: int | None = None,
) -> None:
    """Handle /summary500 — the /summary 500 shortcut as its own command (E-2).

    Takes no argument: anything typed after the command is ignored, because
    ``/summary500 300`` would be asking two different counts at once and the
    parameterized form (``/summary 300``) already covers that.
    """
    lang = chat_config.language if chat_config.language in _SUMMARY_INVALID_COUNT else "ru"

    if await _reject_if_saving_disabled(message, chat_config, lang):
        return

    await _deliver_summary(
        message,
        summary_service,
        count=_SUMMARY500_COUNT,
        lang=lang,
        message_thread_id=message_thread_id,
    )


@router.message(Command("summary"), F.chat.type == "private")
async def handle_summary_dm(message: Message, chat_config: ChatConfig) -> None:
    """Handle /summary in a private (DM) chat — inform user it's group-only."""
    lang = chat_config.language if chat_config.language in _SUMMARY_DM_TEXT else "ru"
    await message.answer(_SUMMARY_DM_TEXT[lang].format(command="/summary"))


@router.message(Command("summary500"), F.chat.type == "private")
async def handle_summary500_dm(message: Message, chat_config: ChatConfig) -> None:
    """Handle /summary500 in a DM — same group-only notice as /summary.

    Without this the update matches no handler at all and the command is a
    silent no-op in DMs, which reads as the bot being broken.
    """
    lang = chat_config.language if chat_config.language in _SUMMARY_DM_TEXT else "ru"
    await message.answer(_SUMMARY_DM_TEXT[lang].format(command="/summary500"))


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
