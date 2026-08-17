"""Command handlers: /start, /help, /summary, /remember, /kb."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from html import escape as html_escape
from html import unescape
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.admin_kb import kb_undo_keyboard, kb_view_keyboard
from src.bot.keyboards.help import help_keyboard
from src.bot.utils import resolve_display_name, safe_edit_text
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.knowledge import KnowledgeRepository
from src.database.repositories.messages import MessageRepository
from src.models.chat_config import ChatConfig
from src.services.ai.router import AIRouter
from src.services.knowledge.capture import (
    CAPTURE_TZ,
    CaptureNote,
    ParsedCapture,
    build_capture,
    collapse_whitespace,
    fact_predicate,
    split_directives,
)
from src.services.modules.summary import SummaryService
from src.services.text.formatter import markdown_to_html
from src.services.text.prompt_builder import MAX_FACT_CHARS
from src.utils import parse_admin_ids, parse_user_id_list
from src.utils.telegram import typing_indicator

router = Router(name="commands")
logger = structlog.get_logger(__name__)

_KB_PAGE_SIZE_DM = 5
_KB_PAGE_SIZE_GROUP = 8

# `/kb` renders one line per fact and KB-08 makes long facts ordinary (a captured
# quote is verbatim message text). Telegram rejects a body over 4096 characters
# outright, so an uncapped list of five captured facts does not degrade -- it
# makes `/kb` permanently unusable for that chat. Same shape as
# `_MAX_ALERT_PROBLEMS` in `src/bot/commands.py`: cap the item, keep the list.
_KB_LINE_MAX_CHARS = 200
_TELEGRAM_MESSAGE_LIMIT = 4096
_HTML_TAG = re.compile(r"<[^>]+>")

# How much of a captured fact the confirmation echoes back. The stored fact is
# never truncated; this bounds only the message that reports it, because that
# message is sent *after* the row is committed and a rejected send is
# indistinguishable from a failed save.
_CONFIRMATION_PREVIEW_CHARS = 500

_REMEMBER_NOTHING_TO_SAVE = {
    "ru": (
        "↩️ Нечего сохранять. Ответьте этой командой на сообщение — "
        "или напишите текст сразу: /remember у нас созвон по вторникам"
    ),
    "en": (
        "↩️ Nothing to save. Reply to a message with this command — "
        "or write the text inline: /remember we sync on Tuesdays"
    ),
}
_REMEMBER_OWN_MESSAGE = {
    "ru": (
        "🚫 Это моё собственное сообщение, а не чей-то факт. "
        "Сохраните первоисточник — или напишите текст факта прямо в команде."
    ),
    "en": (
        "🚫 That is my own message, not someone's fact. "
        "Save the original — or write the fact inline in the command."
    ),
}
_REMEMBER_DM_NOTICE = {
    "ru": (
        "📚 /remember работает в групповом чате: факт сохраняется в базу того чата, "
        "где его записали, и отвечает бот тоже по базе этого чата. "
        "В личке сохранять некуда."
    ),
    "en": (
        "📚 /remember works in a group chat: a fact is stored in the knowledge base of "
        "the chat it was written in, and that is the base the bot answers from. "
        "There is nowhere to store it in a DM."
    ),
}
_REMEMBER_LOOKUP_FAILED = {
    "ru": "⚠️ Не смог сейчас проверить это сообщение. Попробуйте ещё раз через минуту.",
    "en": "⚠️ Could not check that message right now. Please try again in a minute.",
}
_REMEMBER_WRITE_FAILED = {
    "ru": "⚠️ Не удалось сохранить факт. Попробуйте ещё раз — если повторится, напишите админу.",
    "en": "⚠️ Could not save the fact. Try again — if it keeps failing, tell the admin.",
}
_REMEMBER_SAVED = {"ru": "✅ Сохранено: {text}", "en": "✅ Saved: {text}"}
_REMEMBER_ALREADY_SAVED = {
    "ru": "ℹ️ Это уже сохранено (#{fact_id}), второй раз не записываю: {text}",
    "en": "ℹ️ Already saved (#{fact_id}), not storing it twice: {text}",
}
# The same capture arriving again after its fact was undone. Saying "already
# saved" here would be a false statement about the data -- the fact is gone, and
# it is not being restored. Sending the command again as a NEW message does save
# it, because identity is per capture.
_REMEMBER_WAS_REMOVED = {
    "ru": (
        "ℹ️ Этот факт уже сохраняли и потом убрали (#{fact_id}) — сам не восстанавливаю. "
        "Отправьте команду заново, если он снова нужен."
    ),
    "en": (
        "ℹ️ This fact was saved and then removed (#{fact_id}) — I will not restore it by "
        "myself. Send the command again if you need it back."
    ),
}
_REMEMBER_TOPIC_LINE = {"ru": "🗂 Тема: {topic}", "en": "🗂 Topic: {topic}"}
_REMEMBER_EXPIRY_LINE = {
    "ru": "⏳ Действует до {date} включительно",
    "en": "⏳ Valid through {date}",
}
# NOT "in a few minutes": `EmbeddingBackfillWorker` sleeps 180s at startup and
# then runs hourly, so a promise of minutes would be a copy that is usually
# wrong (plan KB-04's warning).
_REMEMBER_NO_EMBED_LINE = {
    "ru": (
        "⚠️ Поиск по смыслу для этого факта включится в течение часа — "
        "до тех пор он виден только в /kb."
    ),
    "en": (
        "⚠️ Semantic search for this fact will come online within the hour — "
        "until then it is only visible in /kb."
    ),
}
_REMEMBER_NOTE_TOPIC_REJECTED = {
    "ru": (
        "⚠️ Тему «{topic}» не принял (буквы, цифры, «-», «_», «:», до 32 символов) — "
        "факт сохранён без темы."
    ),
    "en": (
        "⚠️ Topic “{topic}” was not accepted (letters, digits, “-”, “_”, “:”, up to 32 "
        "characters) — the fact was saved without a topic."
    ),
}
_REMEMBER_NOTE_EXPIRY_UNPARSED = {
    "ru": (
        "⚠️ Срок «{value}» не распознал — сохранил без срока. "
        "Понимаю «до 05.09», «до 5 сентября», «до 2026-09-05»."
    ),
    "en": (
        "⚠️ Could not read the deadline “{value}” — saved without one. "
        "I understand “until 05.09”, “until 5 September”, “until 2026-09-05”."
    ),
}
_REMEMBER_NOTE_EXPIRY_PAST = {
    "ru": "⚠️ Дата «{value}» уже прошла — сохранил без срока, иначе факт исчез бы сразу.",
    "en": "⚠️ “{value}” is already past — saved without a deadline, or it would vanish at once.",
}
_REMEMBER_NOTE_LONG_FACT = {
    "ru": "ℹ️ Факт длиннее {limit} символов — в ответах бота он будет обрезан.",
    "en": "ℹ️ The fact is longer than {limit} characters — the bot will truncate it in answers.",
}
# Rendered because the choice is invisible otherwise: the confirmation echoes the
# highlighted words, and nothing else would tell the user the rest of the message
# was deliberately left out.
_REMEMBER_NOTE_QUOTE = {
    "ru": "ℹ️ Сохранил выделенный фрагмент, а не всё сообщение.",
    "en": "ℹ️ Saved the highlighted fragment, not the whole message.",
}
# The short line of last resort when the full confirmation cannot be delivered at
# all. It must stay tiny: it exists for the case where length is the problem.
_REMEMBER_SAVED_TERSE = {"ru": "✅ Сохранено (#{fact_id})", "en": "✅ Saved (#{fact_id})"}
_KB_UNDO_DONE = {"ru": "↩️ Убрал этот факт.", "en": "↩️ Removed that fact."}
_KB_UNDO_ALREADY = {"ru": "Этот факт уже убран.", "en": "That fact is already removed."}
_KB_UNDO_NOT_FOUND = {
    "ru": "Этого факта здесь больше нет.",
    "en": "That fact is no longer here.",
}
_KB_UNDO_SUPERSEDED = {
    "ru": "Этот факт уже заменён более новой записью — убирать нечего.",
    "en": "This fact was already replaced by a newer entry — nothing to remove.",
}
_KB_UNDO_DONE_STALE_CARD = {
    "ru": "↩️ Факт убран, но обновить это сообщение не смог — кнопка выше уже не работает.",
    "en": "↩️ The fact was removed, but I could not update this message — the button above is dead.",
}
_KB_UNDO_NOT_YOURS = {
    "ru": "Эту кнопку нажимает тот, кто сохранил факт.",
    "en": "Only whoever saved the fact can press this.",
}
_KB_DISABLED = {
    "ru": "📚 База знаний отключена для этого чата. Включите её в админ-панели.",
    "en": "📚 The knowledge base is disabled for this chat. Enable it from the admin panel.",
}
_REMEMBER_NOT_ALLOWED = {
    "ru": "🚫 Сохранять факты могут только организаторы или админ бота.",
    "en": "🚫 Only organizers or the bot admin can save facts.",
}

# Names only the surface that exists. The old wording offered "or from the admin
# panel", which has never been able to add a fact — the panel toggles `kb_enabled`
# and manages organizers.
_KB_EMPTY_DM = {
    "ru": "📚 База знаний пока пуста. Организаторы наполняют её командой /remember в самом чате.",
    "en": "📚 The knowledge base is empty. Organizers fill it with /remember in the chat itself.",
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
    return user_id in set(parse_user_id_list(settings.get("kb_organizer_ids")))


def _format_kb_date(value: Any) -> str:
    """A calendar date as a reader in the bot's display timezone would name it.

    `updated_at` comes back from Postgres in UTC, so formatting it raw showed the
    previous day for anything written after 20:00 local — "обновлено 16.08" on a
    fact saved at 23:30 on the 16th reads as a wrong record, not as a timezone.
    """
    if isinstance(value, datetime):
        moment: datetime = value.astimezone(CAPTURE_TZ) if value.tzinfo is not None else value
        return moment.strftime("%d.%m.%Y")
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
# /remember — manual Knowledge Base capture (A4, ADR-0003; S2/KB-07..KB-09)
# ---------------------------------------------------------------------------


async def _resolve_kb_authority(
    user_id: int | None,
    chat_id: int,
    bot_config_repo: BotConfigRepository,
    chat_settings_repo: ChatSettingsRepository,
) -> int | None:
    """KB write rank for this user, or None if they have none.

    4 = bot admin, 3 = organizer. Rank 2 (every Telegram chat administrator,
    plan §4.4) lands with the S3 management surface, which is also where the
    private-chat whitelist that makes rank 2 usable is decided.
    """
    if user_id is None:
        return None
    admin_ids_raw = await bot_config_repo.get("admin_ids")
    if user_id in parse_admin_ids(admin_ids_raw):
        return 4
    if await _is_kb_organizer(user_id, chat_id, chat_settings_repo):
        return 3
    return None


class CaptureOutcome(StrEnum):
    """Why `_resolve_captured_text` has no text to hand back.

    `REFUSED_OWN` and `LOOKUP_FAILED` used to be one value, which made a
    transient database blip indistinguishable from a permanent, deliberate
    refusal: the user replying to a transcription during a pool timeout was told
    to "save the original" — advice that is simply wrong for a fact the bot
    *would* have captured a second later, and that leaves them with no reason to
    retry.
    """

    OK = "ok"
    NOTHING = "nothing"
    REFUSED_OWN = "refused_own"
    LOOKUP_FAILED = "lookup_failed"


async def _resolve_captured_text(
    message: Message,
    bot_id: int | None,
    message_repo: MessageRepository,
) -> tuple[str | None, int | None, bool, CaptureOutcome]:
    """What text a bare `/remember` on a reply should save (KB-08).

    Returns ``(text, source_message_id, from_quote, outcome)``.

    Read straight off the message rather than through
    `extract_reply_context()`: that helper truncates a reply to 500 characters
    and a quote to 300, silently and with no signal to the caller, which is
    right for a prompt and wrong for a write path -- it would store a
    quietly-shortened fact under a confirmation saying it was saved.

    A manually-highlighted quote wins over the full message: highlighting *is*
    the user pointing at the part they mean.

    The one case that needs the database: a reply to one of the bot's own
    messages. A transcription is a relay of someone else's speech, so
    `/remember` on it should store the speech -- not the rendered
    "🎙 Расшифровка от Имя:" header, which would file the bot's own formatting
    as a curated chat fact. Recognition is `chat_messages.transcribed_message_id`
    (migration 028), never a text match on the header, for the reason that
    column exists: a user can make the bot echo any string. Any other message of
    the bot's own is refused outright -- laundering the model's output into the
    authoritative fact base is exactly the loop a knowledge base must not have.
    """
    rpl = message.reply_to_message
    if rpl is None:
        return None, None, False, CaptureOutcome.NOTHING

    full_text = rpl.text or rpl.caption or None

    # A *manually* highlighted fragment wins: highlighting is the user pointing
    # at the part they mean. An automatic quote (`is_manual` false) does NOT win:
    # it is a server-chosen excerpt, and preferring it would silently store less
    # than the message said.
    #
    # Note what this deliberately does not do: a reply to a message in ANOTHER
    # chat arrives as `external_reply` + `quote` with **no** `reply_to_message`,
    # so it returns at the guard above and never reaches here. Cross-chat capture
    # is out of scope — the provenance columns point at a message id in *this*
    # chat, and storing one from elsewhere would make `source_message_id` a
    # dangling reference.
    quote_text = message.quote.text if message.quote is not None else None
    if quote_text and message.quote.is_manual:  # type: ignore[union-attr]
        return quote_text, rpl.message_id, True, CaptureOutcome.OK

    # FAIL CLOSED on an unknown bot id. `bot_id` arrives as an aiogram kwarg from
    # `dp["bot_id"]`; if that key is ever missing (a new entrypoint, a test, a
    # refactor of main.py) the identity test silently became False and the bot's
    # OWN generated answer could be stored as a curated chat fact -- the exact
    # feedback loop this branch exists to prevent, reachable by omission rather
    # than by attack. `message.bot.id` is derived from the token and costs no API
    # call; `is_bot` is the last-resort fallback, because refusing to capture some
    # *other* bot's message is a small loss and laundering our own output is not.
    effective_bot_id = bot_id if bot_id is not None else (message.bot.id if message.bot else None)
    if effective_bot_id is not None:
        from_bot_itself = rpl.from_user is not None and rpl.from_user.id == effective_bot_id
    else:
        from_bot_itself = rpl.from_user is not None and bool(rpl.from_user.is_bot)

    if from_bot_itself:
        try:
            transcription = await message_repo.get_transcription_source(
                message.chat.id, rpl.message_id
            )
        except Exception as exc:
            # NOT the same answer as "this is my own message". We do not know
            # whether it was a transcription, so the honest reply is "could not
            # check, try again" -- telling them to find the original is wrong
            # advice for a fact the bot would have captured a second later.
            logger.warning(
                "kb_capture_transcription_lookup_failed",
                chat_id=message.chat.id,
                reply_to_message_id=rpl.message_id,
                error_type=type(exc).__name__,
                error=str(exc),
                exc_info=True,
            )
            return None, None, False, CaptureOutcome.LOOKUP_FAILED
        if transcription is not None and transcription["transcript"]:
            # Attributed to the *audio* message, not to the transcription the
            # bot posted: that is the message a maintainer would go looking for.
            return (
                str(transcription["transcript"]),
                transcription["source_message_id"],
                False,
                CaptureOutcome.OK,
            )
        return None, None, False, CaptureOutcome.REFUSED_OWN

    return full_text, rpl.message_id, False, CaptureOutcome.OK


def _render_capture_confirmation(
    capture: ParsedCapture,
    *,
    lang: str,
    fact_id: int,
    created: bool,
    embedded: bool,
    was_removed: bool = False,
) -> str:
    """Confirmation body, as explicit HTML with every dynamic part escaped.

    Deliberately not built with `markdown_to_html`: that helper *interprets*
    Markdown after escaping, so captured text containing `**` or `_` comes back
    with crossing tags that Telegram rejects — and the reply happens after the
    row is committed, so a rejected confirmation reads to the user as a save
    that did not happen. They then retype it, which under append-only is a
    second fact.
    """
    if created:
        template = _REMEMBER_SAVED
    elif was_removed:
        template = _REMEMBER_WAS_REMOVED
    else:
        template = _REMEMBER_ALREADY_SAVED
    # The echoed text is a PREVIEW, capped. The stored fact keeps its full length
    # (KB-08 captures a replied-to message whole, on purpose), but echoing 4000
    # characters back — grown further by html_escape — produces a confirmation
    # Telegram refuses, and the refusal lands after the row is committed.
    preview = capture.fact_text
    if len(preview) > _CONFIRMATION_PREVIEW_CHARS:
        preview = preview[:_CONFIRMATION_PREVIEW_CHARS].rstrip() + "…"
    lines = [
        template[lang].format(text=html_escape(preview), fact_id=fact_id),
    ]
    if capture.topic:
        lines.append(_REMEMBER_TOPIC_LINE[lang].format(topic=html_escape(capture.topic)))
    if capture.expires_at is not None:
        lines.append(
            _REMEMBER_EXPIRY_LINE[lang].format(date=capture.expires_at.strftime("%d.%m.%Y"))
        )
    if CaptureNote.TOPIC_REJECTED in capture.notes:
        lines.append(
            _REMEMBER_NOTE_TOPIC_REJECTED[lang].format(
                topic=html_escape(capture.rejected_topic or "")
            )
        )
    if CaptureNote.EXPIRY_UNPARSED in capture.notes:
        lines.append(
            _REMEMBER_NOTE_EXPIRY_UNPARSED[lang].format(
                value=html_escape(capture.unparsed_expiry or "")
            )
        )
    if CaptureNote.EXPIRY_IN_PAST in capture.notes:
        lines.append(
            _REMEMBER_NOTE_EXPIRY_PAST[lang].format(
                value=html_escape(capture.unparsed_expiry or "")
            )
        )
    if CaptureNote.QUOTE_CAPTURED in capture.notes:
        lines.append(_REMEMBER_NOTE_QUOTE[lang])
    if CaptureNote.LONG_FACT in capture.notes:
        lines.append(_REMEMBER_NOTE_LONG_FACT[lang].format(limit=MAX_FACT_CHARS))
    if not embedded:
        lines.append(_REMEMBER_NO_EMBED_LINE[lang])
    return "\n".join(lines)


async def _reply_html_with_fallback(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    *,
    last_resort: str,
) -> None:
    """Send a confirmation, and never let a rejected message turn a save into silence.

    The row is already committed by the time this runs, so "the user sees
    nothing" is the one outcome that must not happen: they conclude the command
    failed and retype it, which under append-only is a second fact.

    Three attempts, each addressing a different rejection. HTML first. Then plain
    text, for a body whose markup Telegram would not parse. Then `last_resort` --
    a short line that names the fact id and nothing else -- because the second
    attempt sends the *same long body* and a length rejection would kill it too.
    KB-08 captures a replied-to message in full, deliberately unbounded, so a
    confirmation long enough to be refused is reachable from ordinary use rather
    than from an attack.
    """
    try:
        await message.reply(text, parse_mode="HTML", reply_markup=reply_markup)
        return
    except TelegramBadRequest as exc:
        logger.warning(
            "kb_capture_confirmation_html_rejected",
            chat_id=message.chat.id,
            error=str(exc),
        )
    try:
        await message.reply(text, parse_mode=None, reply_markup=reply_markup)
        return
    except TelegramBadRequest as exc:
        logger.warning(
            "kb_capture_confirmation_plain_rejected",
            chat_id=message.chat.id,
            error=str(exc),
        )
    try:
        await message.reply(last_resort, parse_mode=None, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        # Nothing left to try: log at error level so a committed write that the
        # chat never saw is findable afterwards rather than merely lost.
        logger.error(
            "kb_capture_confirmation_undeliverable",
            chat_id=message.chat.id,
            error=str(exc),
            exc_info=True,
        )


@router.message(Command("remember"), F.chat.type == "private")
async def handle_remember_dm(message: Message, chat_config: ChatConfig) -> None:
    """`/remember` in a DM: explain where facts live instead of writing one here.

    Without this the command would write a fact keyed to the *private* chat's
    id, where no group retrieval can ever reach it — and `/kb` in the same DM
    would list it back, so the loop looks like it worked. A chat-type filter
    rather than a guard in the body, per the consumed-update rule: a matched
    handler consumes the update whatever it then decides.
    """
    lang = chat_config.language if chat_config.language in _REMEMBER_DM_NOTICE else "ru"
    await message.answer(_REMEMBER_DM_NOTICE[lang])


@router.message(Command("remember"), F.chat.type.in_({"group", "supergroup"}))
async def handle_remember(
    message: Message,
    chat_config: ChatConfig,
    knowledge_repo: FromDishka[KnowledgeRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    ai_router: FromDishka[AIRouter],
    bot: Bot,
    message_repo: FromDishka[MessageRepository],
    command: CommandObject | None = None,
    message_thread_id: int | None = None,
    bot_id: int | None = None,
) -> None:
    """Save a chat fact: typed text, a replied-to message, or a highlighted quote.

    Grammar (KB-09), each part optional and each anchored:

        /remember [#тема] <текст> [до <дата>]
        /remember [#тема] [до <дата>]          ← as a reply: saves the quoted text

    Guard order is `kb_enabled` → authority → content, which is the inverse of
    Phase 1's. Before, every member of every chat who typed `/remember` without
    a reply got a lecture about replies — including in chats with the knowledge
    base switched off, and including members with no right to write facts.

    Append-only (KB-07): a second `/remember` about the same subject adds a fact
    instead of superseding the first. The Phase-1 constant predicate made
    `(chat_id, subject)` the effective key, so "add another detail" silently
    retired the previous fact. `append_fact` cannot supersede at all.
    """
    lang = chat_config.language if chat_config.language in _REMEMBER_NOTHING_TO_SAVE else "ru"

    if not chat_config.kb_enabled:
        await message.reply(_KB_DISABLED[lang])
        return

    user_id = message.from_user.id if message.from_user else None
    authority_level = await _resolve_kb_authority(
        user_id, message.chat.id, bot_config_repo, chat_settings_repo
    )
    if authority_level is None:
        await message.reply(_REMEMBER_NOT_ALLOWED[lang])
        return

    directives = split_directives(command.args or "" if command is not None else "")
    body = directives.body
    from_quote = False
    source_message_id: int | None = message.message_id

    if not body:
        captured, captured_source_id, from_quote, outcome = await _resolve_captured_text(
            message, bot_id, message_repo
        )
        if outcome is CaptureOutcome.REFUSED_OWN:
            await message.reply(_REMEMBER_OWN_MESSAGE[lang])
            return
        if outcome is CaptureOutcome.LOOKUP_FAILED:
            await message.reply(_REMEMBER_LOOKUP_FAILED[lang])
            return
        if not captured or not collapse_whitespace(captured):
            await message.reply(_REMEMBER_NOTHING_TO_SAVE[lang])
            return
        body = captured
        source_message_id = captured_source_id
    elif message.reply_to_message is not None:
        # Typed text wins as the fact, but the message being replied to is still
        # the best provenance pointer we have for where it came from.
        source_message_id = message.reply_to_message.message_id

    capture = build_capture(
        body=body,
        topic_raw=directives.topic_raw,
        expiry_raw=directives.expiry_raw,
        expiry_clause=directives.expiry_clause,
        topic_prefix=directives.topic_prefix,
        today=datetime.now(CAPTURE_TZ).date(),
        from_quote=from_quote,
        long_fact_chars=MAX_FACT_CHARS,
    )

    embedding: list[float] | None = None
    try:
        async with typing_indicator(bot, message.chat.id, message_thread_id):
            embedding_result = await ai_router.generate_embedding(
                capture.fact_text, chat_id=message.chat.id
            )
        embedding = embedding_result.embedding
    except Exception as exc:
        # The fact is still stored; `EmbeddingBackfillWorker` carries
        # `chat_facts` as a source (S1/KB-04) and retries it. Logged WITH the
        # error: without it, "the KB stopped indexing" and "one provider blip"
        # look identical in the logs, and the difference is what decides whether
        # anyone needs to act.
        logger.warning(
            "kb_remember_embedding_failed",
            chat_id=message.chat.id,
            error_type=type(exc).__name__,
            error=str(exc),
            exc_info=True,
        )

    # The write is wrapped because this is a MESSAGE handler: the global error
    # handler can only answer callbacks (`src/bot/errors.py`), so an exception
    # escaping here reaches the user as pure silence — they retype, and under
    # append-only that is a second fact. `append_fact` genuinely can raise: it
    # re-raises a unique violation whose conflicting row it cannot find, rather
    # than reporting a save that did not happen.
    try:
        fact_id, created = await knowledge_repo.append_fact(
            chat_id=message.chat.id,
            subject=capture.subject,
            predicate=fact_predicate(message.message_id),
            value=capture.value,
            fact_text=capture.fact_text,
            source="manual",
            topic=capture.topic,
            embedding=embedding,
            source_message_id=source_message_id,
            source_user_id=user_id,
            authority_level=authority_level,
            confidence=None,
            expires_at=capture.expires_at,
        )
    except Exception as exc:
        logger.error(
            "kb_fact_capture_write_failed",
            chat_id=message.chat.id,
            user_id=user_id,
            command_message_id=message.message_id,
            error_type=type(exc).__name__,
            error=str(exc),
            exc_info=True,
        )
        await message.reply(_REMEMBER_WRITE_FAILED[lang])
        return

    logger.info(
        "kb_fact_captured",
        chat_id=message.chat.id,
        fact_id=fact_id,
        created=created,
        topic=capture.topic,
        expires_at=capture.expires_at.isoformat() if capture.expires_at else None,
        notes=[n.value for n in capture.notes],
        embedded=embedding is not None,
        authority_level=authority_level,
        chars=len(capture.fact_text),
    )

    # `created=False` means this exact capture already owns a row. Whether that
    # row is still live decides the copy: claiming "already saved" for a fact the
    # user undid would be a false statement about the data.
    was_removed = False
    if not created:
        existing = await knowledge_repo.get_by_id(fact_id, chat_id=message.chat.id)
        was_removed = bool(existing and existing.get("status") == "rejected")

    text = _render_capture_confirmation(
        capture,
        lang=lang,
        fact_id=fact_id,
        created=created,
        embedded=embedding is not None,
        was_removed=was_removed,
    )
    # No undo button unless this command actually wrote the row: on a redelivery
    # the button belongs to the original confirmation, and on an undone fact
    # there is nothing left to undo.
    keyboard = (
        kb_undo_keyboard(lang, fact_id=fact_id, owner_id=user_id) if created and user_id else None
    )
    await _reply_html_with_fallback(
        message, text, keyboard, last_resort=_REMEMBER_SAVED_TERSE[lang].format(fact_id=fact_id)
    )


@router.callback_query(F.data.startswith("kb_undo:"))
async def handle_kb_undo(
    callback: CallbackQuery,
    chat_config: ChatConfig,
    knowledge_repo: FromDishka[KnowledgeRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
) -> None:
    """Retire the fact this confirmation announced (first caller of `reject_fact`).

    Append-only capture removes the only correction the bot had: re-issuing
    `/remember` about the same subject used to overwrite the old value. It was an
    accident of the constant predicate rather than a feature, and losing it would
    otherwise leave the chat with facts nobody can retire until the S3 management
    surface lands — while KB-08 makes writing them easier. So capture ships with
    its own undo.

    This is the project's first *write* button in a group chat, where any member
    can press any inline button. Two independent checks, because either alone is
    insufficient: the presser must be the person who saved the fact (owner id
    travels in the callback data), **and** they must still hold KB authority at
    press time — rights can be revoked between the save and the press.
    """
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    parts = (callback.data or "").split(":")
    lang = chat_config.language if chat_config.language in _KB_UNDO_DONE else "ru"
    try:
        fact_id = int(parts[1])
        owner_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    presser = callback.from_user.id if callback.from_user else None
    if presser != owner_id:
        await callback.answer(_KB_UNDO_NOT_YOURS[lang], show_alert=True)
        return

    chat_id = callback.message.chat.id
    if not chat_config.kb_enabled:
        await callback.answer(_KB_DISABLED[lang], show_alert=True)
        return

    if await _resolve_kb_authority(presser, chat_id, bot_config_repo, chat_settings_repo) is None:
        await callback.answer(_REMEMBER_NOT_ALLOWED[lang], show_alert=True)
        return

    removed = await knowledge_repo.reject_fact(fact_id, chat_id=chat_id, rejected_by=presser)
    if not removed:
        # `reject_fact` returns False for three different reasons, and they are
        # not the same message: the row is gone from this chat, it was already
        # retired, or a later write superseded it (reachable once S3's "rewrite"
        # action exists). Naming the row's real state costs one read on a path
        # that is already a no-op.
        row = await knowledge_repo.get_by_id(fact_id, chat_id=chat_id)
        if row is None:
            await callback.answer(_KB_UNDO_NOT_FOUND[lang], show_alert=True)
        elif row.get("status") == "superseded":
            await callback.answer(_KB_UNDO_SUPERSEDED[lang], show_alert=True)
        else:
            await callback.answer(_KB_UNDO_ALREADY[lang], show_alert=True)
        return

    logger.info("kb_fact_undone", chat_id=chat_id, fact_id=fact_id, rejected_by=presser)
    # The removal is already committed. If the message cannot be edited — older
    # than Telegram's edit window, or the bot lost the right — the generic "try
    # again" alert the dispatcher would produce is actively WRONG advice: trying
    # again cannot re-remove what is already gone, and the stale card would keep
    # showing an undo button for a retired fact. Say what actually happened.
    try:
        await safe_edit_text(
            callback.message, _KB_UNDO_DONE[lang], parse_mode=None, reply_markup=None
        )
    except TelegramBadRequest as exc:
        logger.warning(
            "kb_fact_undone_edit_failed",
            chat_id=chat_id,
            fact_id=fact_id,
            error=str(exc),
        )
        await callback.answer(_KB_UNDO_DONE_STALE_CARD[lang], show_alert=True)
        return
    await callback.answer(_KB_UNDO_DONE[lang])


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


def _fact_line(fact: dict[str, Any], lang: str) -> str:
    """One fact as one line, capped, with its deadline if it has one.

    Renders `fact_text` — the same column the model is shown — rather than
    `{subject} — {predicate}: {value}`. Three reasons, all introduced by S2:
    `predicate` now carries a generated identity that means nothing to a reader;
    a captured quote has no natural subject/value split, so `subject` is a
    derived head and printing it next to the text it was derived from reads as a
    duplicated sentence; and rendering the same column everywhere is what keeps
    one fact from saying different things in `/kb`, in the group list and in the
    prompt.

    The deadline is rendered because S2 is the first slice that *writes* one. Not
    rendering it would make an expiring fact indistinguishable from a permanent
    one right up to the day it silently vanishes from every surface.
    """
    text = collapse_whitespace(str(fact.get("fact_text") or fact.get("value") or ""))
    if len(text) > _KB_LINE_MAX_CHARS:
        text = text[:_KB_LINE_MAX_CHARS].rstrip() + "…"
    line = html_escape(text)
    expires_at = fact.get("expires_at")
    if isinstance(expires_at, datetime):
        until = "до" if lang == "ru" else "until"
        line = f"{line} ⏳ {until} {expires_at.strftime('%d.%m.%Y')}"
    return line


def _visible_len(text: str) -> int:
    """Length as Telegram counts it: after tags are stripped and entities decoded.

    The 4096-character limit applies to the *parsed* text, not to the HTML we
    send, and escaping inflates the difference a lot — 200 stored `&`s become
    1000 characters of `&amp;`. Budgeting on the raw string made `/kb` truncate
    pages that would have fitted comfortably.
    """
    return len(unescape(_HTML_TAG.sub("", text)))


def _fit_message(
    blocks: list[list[str]], lang: str, *, header: list[str], footer: list[str]
) -> str:
    """Assemble a body Telegram will accept, dropping whole facts if it must.

    Takes one **block per fact** (the DM view renders two lines per fact: the text
    and its provenance) rather than a flat line list. That is what makes the
    "… и ещё N" count honest: counting lines reported 5 dropped when 4 facts were
    dropped, and would have split a fact from its own provenance line.

    A rejected `sendMessage` is not a degraded list — it is no list at all, and
    the exception reaches the user as silence (the global error handler can only
    answer callbacks). Cheaper to say "and N more" than to break `/kb` for a chat
    whose facts happen to be long.
    """
    reserve = 80  # room for the footer and the "… and N more" line
    budget = _TELEGRAM_MESSAGE_LIMIT - reserve
    used = sum(_visible_len(line) + 1 for line in header + footer)

    kept: list[list[str]] = []
    for block in blocks:
        cost = sum(_visible_len(line) + 1 for line in block)
        if used + cost > budget:
            break
        kept.append(block)
        used += cost

    lines = [*header, *(line for block in kept for line in block)]
    dropped = len(blocks) - len(kept)
    if dropped:
        lines.append(f"… и ещё {dropped}" if lang == "ru" else f"… and {dropped} more")
    lines.extend(footer)
    return "\n".join(lines)


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

    title = "📚 <b>База знаний чата</b>" if lang == "ru" else "📚 <b>Chat Knowledge Base</b>"
    header = [title, ""]
    cache: dict[int, str] = {}
    current_topic: str | None = None
    # One block per fact, so a fact is never separated from its own provenance
    # line by the length cap, and "… и ещё N" counts facts.
    blocks: list[list[str]] = []
    for fact in page_facts:
        block: list[str] = []
        topic = fact.get("topic") or _KB_TOPIC_GENERAL[lang]
        if topic != current_topic:
            block.append(f"<b>{html_escape(str(topic))}</b>")
            current_topic = topic
        author = await _resolve_author_label(bot, chat_id, fact.get("source_user_id"), lang, cache)
        date_str = _format_kb_date(fact.get("updated_at"))
        updated_label = "обновлено" if lang == "ru" else "updated"
        block.append(f"• {_fact_line(fact, lang)}")
        block.append(f"  <i>{updated_label} {date_str}, {html_escape(author)}</i>")
        blocks.append(block)

    footer = ["", f"◀️ {page + 1}/{total_pages} ▶️"] if total_pages > 1 else []

    # Built as explicit HTML rather than through `markdown_to_html`. That helper
    # *interprets* Markdown after escaping, so a fact containing `**` or a lone
    # `_` — ordinary in captured text — comes back with crossing tags that
    # Telegram rejects, taking the whole page down for one bad fact. A display
    # name can do the same.
    keyboard = (
        kb_view_keyboard(lang, page=page, total_pages=total_pages) if total_pages > 1 else None
    )
    body = _fit_message(blocks, lang, header=header, footer=footer)
    return body.strip(), keyboard


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
    # Facts are raw user input; the bot-wide default parse_mode is HTML, so
    # unescaped '&'/'<' would break rendering or inject markup. `_fact_line`
    # escapes and caps.
    blocks = [[f"• {_fact_line(fact, lang)}"] for fact in page_facts]
    footer = [f"◀️ {page + 1}/{total_pages} ▶️"] if total_pages > 1 else []

    keyboard = (
        kb_view_keyboard(lang, page=page, total_pages=total_pages) if total_pages > 1 else None
    )
    return _fit_message(blocks, lang, header=[header], footer=footer), keyboard


@router.message(Command("kb"), F.chat.type == "private")
async def handle_kb_view_dm(
    message: Message,
    chat_config: ChatConfig,
    knowledge_repo: FromDishka[KnowledgeRepository],
) -> None:
    """``/kb`` in DM: bold-title, topic-sectioned, paginated (5/page)."""
    lang = chat_config.language if chat_config.language in _KB_EMPTY_DM else "ru"

    if not chat_config.kb_enabled:
        await message.answer(_KB_DISABLED[lang])
        return

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

    if not chat_config.kb_enabled:
        await message.answer(_KB_DISABLED[lang])
        return

    facts = await knowledge_repo.get_active_facts(message.chat.id)

    if not facts:
        await message.answer(_KB_EMPTY_GROUP[lang])
        return

    text, keyboard = _render_kb_group(facts, lang, 0)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("kb_view:"))
async def handle_kb_view_page(
    callback: CallbackQuery,
    chat_config: ChatConfig,
    knowledge_repo: FromDishka[KnowledgeRepository],
) -> None:
    """Paginate an existing ``/kb`` view in place (public — no admin gating).

    Re-fetches and re-slices the same way the initial command did; renders
    group vs. DM the same way based on the callback's own chat type (the
    message being paginated always lives in the chat it was first sent in).

    Gated on ``kb_enabled`` like the two command handlers: the buttons live on
    an already-sent message and outlive the toggle, so without this check
    disabling the KB left every existing ``/kb`` message working as a fully
    functional reader — including the DM variant's provenance. Answered as an
    alert rather than a silent ``callback.answer()`` so the press has a visible
    result.
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

    if not chat_config.kb_enabled:
        await callback.answer(_KB_DISABLED[lang], show_alert=True)
        return

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
