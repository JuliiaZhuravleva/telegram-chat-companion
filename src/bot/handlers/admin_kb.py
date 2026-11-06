"""Knowledge Base admin sub-router (A4).

Handles:
- ``adm_kb:*``      — chat picker (KB is per-chat, ADR-0003)
- ``adm_kb_menu:*``  — per-chat submenu (organizers + kb_enabled toggle)
- ``adm_kb_toggle:*`` — flip kb_enabled
- ``adm_kb_orgs:*``  — paginated organizer list
- ``adm_kb_org_add:*`` / message reply — add organizer: Telegram's own person
  picker (``request_users`` → a ``users_shared`` service message), or, as the
  fallback, a forwarded message or a plain @username
- ``adm_kb_org_list:*`` — B-2 participant picker, paginated by message count
- ``adm_kb_org_pick:*`` — B-2 picker selection (adds the tapped participant)
- ``adm_kb_org_rm:*`` — remove organizer

See docs/design/kb-copy-register.md (G2) for copy and
docs/decisions/ADR-0003-chat-facts-data-model.md (G1) for the schema.
"""

from __future__ import annotations

import json
import re
from html import escape as html_escape
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Message,
    MessageOriginHiddenUser,
    MessageOriginUser,
    ReplyKeyboardRemove,
    SharedUser,
)
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.admin_kb import (
    ORG_CANCEL_LABEL,
    ORG_PICK_LABEL,
    ORG_PICK_REQUEST_ID,
    kb_chat_picker_keyboard,
    kb_menu_keyboard,
    kb_org_add_prompt_keyboard,
    kb_organizer_picker_keyboard,
    kb_organizers_keyboard,
)
from src.bot.nav import parse_origin
from src.bot.states.admin import AdminStates
from src.bot.utils import check_admin_direct, resolve_display_name, safe_edit_text
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.messages import MessageRepository
from src.services.chat_config import ChatConfigService
from src.utils import parse_user_id_list

logger = structlog.get_logger(__name__)

router = Router(name="admin_kb")

_PER_PAGE = 10
# B-2 picker page size: Julia proposed a top-5 default in the review transcript.
_PICKER_PER_PAGE = 5

_KB_MENU_TITLE = {
    "ru": "📚 База знаний",
    "en": "📚 Knowledge Base",
}
_KB_PICKER_TITLE = {
    "ru": "📚 Выберите чат для управления Базой знаний:",
    "en": "📚 Pick a chat to manage its Knowledge Base:",
}
_KB_NO_CHATS = {
    "ru": "Нет чатов в whitelist.",
    "en": "No whitelisted chats.",
}
_ORGS_TITLE = {
    "ru": "👥 Организаторы чата",
    "en": "👥 Chat organizers",
}
_ORGS_EMPTY = {
    "ru": "Пока не назначено ни одного организатора. Добавьте через кнопку ниже.",
    "en": "No organizers assigned yet. Add one with the button below.",
}
_TOGGLE_ON = {"ru": "Сбор фактов включён", "en": "Fact collection enabled"}
_TOGGLE_OFF = {"ru": "Сбор фактов выключен", "en": "Fact collection disabled"}
_ADD_PROMPT = {
    "ru": "👥 <b>Новый организатор</b>\n\n"
    "Нажмите «🔍 Найти в Telegram» внизу и выберите человека. "
    "Ещё можно переслать его сообщение или написать @username.",
    "en": "👥 <b>New organizer</b>\n\n"
    "Tap «🔍 Find in Telegram» below and pick the person. "
    "You can also forward their message or send their @username.",
}
_ADD_CANCELLED = {
    "ru": "Добавление организатора отменено.",
    "en": "Adding an organizer cancelled.",
}
# The pick arrived but the prompt it answers is gone (cleared by /cancel, a
# pick from the participant list, a restart — MemoryStorage) — the keyboard
# outlives it behind the input's keyboard icon. Nothing is written: without
# the state there is no chat to write to.
_PICK_STALE = {
    "ru": "Этот выбор уже закрыт, никто не добавлен. "
    "Откройте «👥 Организаторы» и нажмите «➕ Добавить» ещё раз.",
    "en": "This pick is closed, nobody was added. "
    "Open «👥 Chat organizers» and tap «➕ Add» again.",
}
# The picker's label arrived as TEXT: only a client that cannot show the
# picker sends it that way (a request_users button is never sent as text).
_PICK_UNSUPPORTED = {
    "ru": "Это приложение Telegram не открывает поиск. "
    "Перешлите сообщение этого человека или напишите его @username.",
    "en": "This Telegram app can't open the search. "
    "Forward a message from the person, or send their @username.",
}
_PICK_NOT_IN_CHAT = {
    "ru": "⚠️ Сейчас этого человека в чате нет.",
    "en": "⚠️ This person is not in the chat right now.",
}
_ADD_SUCCESS = {
    "ru": "✅ {name} добавлен(а) в организаторы.",
    "en": "✅ {name} added as organizer.",
}
_ADD_NOT_FOUND = {
    "ru": "🤔 Не нашёл такого участника в этом чате.",
    "en": "🤔 Couldn't find that member in this chat.",
}
_ADD_FORWARD_HIDDEN = {
    "ru": "🔒 У отправителя пересланного сообщения включена приватность форвардов — "
    "Telegram не показывает боту, кто это. Попросите переслать ещё раз после "
    "отключения этой настройки, или отправьте его @username.",
    "en": "🔒 The forwarded message's sender has forward privacy enabled — Telegram "
    "doesn't tell the bot who they are. Ask them to disable that setting and "
    "forward again, or send their @username instead.",
}
_ADD_NOT_IN_CHAT = {
    "ru": "🤔 Знаю такой @username, но не видел его в этом чате.",
    "en": "🤔 I know that @username, but haven't seen them in this chat.",
}
# The add flow ends on every answer of the reader (it clears state on entry),
# so each answer also takes the picker keyboard away.
_NO_KEYBOARD = ReplyKeyboardRemove()
_NOT_ADMIN = {"ru": "Нет доступа.", "en": "Access denied."}

# B-2: participant picker, alternative to the forward/@username prompt above.
_PICKER_TITLE = {
    "ru": "👥 Выберите организатора из участников чата (по числу сообщений):",
    "en": "👥 Pick an organizer from chat participants (by message count):",
}
_PICKER_EMPTY = {
    "ru": "🤔 Не нашёл ни одного участника с сообщениями в этом чате.",
    "en": "🤔 Couldn't find any participants with messages in this chat.",
}

# B-1 stage 2: chat-scoped username resolution has no lookup path via the Bot
# API (get_chat_member requires a numeric id), so a plain @username reply is
# only resolvable if the bot has already recorded that username posting in
# this chat's message history. Telegram username format: letters/digits/
# underscores, 5-32 chars (leading "@" stripped before matching).
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


def _extract_username(text: str | None) -> str | None:
    """Parse a plain `@username` (or bare username) reply into a valid handle.

    Returns None if the text doesn't look like a single Telegram username —
    those replies fall back to the generic not_found copy, same as before.
    """
    if text is None:
        return None
    candidate = text.strip().lstrip("@")
    return candidate if _USERNAME_RE.match(candidate) else None


def _is_private(callback: CallbackQuery) -> bool:
    return isinstance(callback.message, Message) and callback.message.chat.type == "private"


def _parse_organizer_ids(raw: Any) -> list[int]:
    """Defensively parse kb_organizer_ids. Thin alias over the shared parser.

    Kept as a name because this module reads it at several call sites; the parse
    itself lives in `src/utils` so the `/remember` authority gate and this screen
    cannot disagree about who an organizer is.
    """
    return parse_user_id_list(raw)


async def _add_organizer(
    chat_settings_repo: ChatSettingsRepository, chat_id: int, user_id: int
) -> None:
    """Append ``user_id`` to the chat's organizers; a repeat is a no-op."""
    row = await chat_settings_repo.get(chat_id)
    organizer_ids = _parse_organizer_ids(row.get("kb_organizer_ids") if row else None)
    if user_id not in organizer_ids:
        organizer_ids.append(user_id)
        await chat_settings_repo.set_field(chat_id, "kb_organizer_ids", json.dumps(organizer_ids))


def _shared_user_label(user: SharedUser) -> str:
    """``First Last (@nick)``, falling back to whichever part exists, then the id."""
    name = " ".join(part for part in (user.first_name, user.last_name) if part)
    if user.username:
        return f"{name} (@{user.username})" if name else f"@{user.username}"
    return name or str(user.user_id)


async def _left_the_chat(bot: Bot | None, chat_id: int, user_id: int) -> bool:
    """True only when Telegram SAYS the person is not in the chat.

    Telegram's picker offers anyone — contacts, recent chats, global search —
    so the pick can be someone who never was in this chat. That is worth a
    line in the answer, not a refusal: the organizer list is an authority
    list, and someone about to be invited may be on it on purpose. Any API
    failure answers False: an unverifiable membership is not reported as
    absence.
    """
    if bot is None:
        return False
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError as exc:
        logger.warning(
            "Organizer pick: membership check failed",
            chat_id=chat_id,
            user_id=user_id,
            error=str(exc),
        )
        return False
    return member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)


async def _render_chat_picker(
    callback: CallbackQuery, admin_repo: AdminRepository, lang: str, page: int
) -> None:
    chats, total = await admin_repo.get_enabled_chats_page(page, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    if page >= total_pages:
        page = max(0, total_pages - 1)
        chats, total = await admin_repo.get_enabled_chats_page(page, _PER_PAGE)

    text = _KB_PICKER_TITLE[lang] if total else f"{_KB_MENU_TITLE[lang]}\n\n{_KB_NO_CHATS[lang]}"
    keyboard = kb_chat_picker_keyboard(chats, lang=lang, page=page, total=total, per_page=_PER_PAGE)

    if isinstance(callback.message, Message):
        await safe_edit_text(callback.message, text, reply_markup=keyboard)


async def _effective_kb_enabled(
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_id: int,
) -> bool:
    """Resolve kb_enabled the way the running bot does (3-layer merge subset).

    The raw column is nullable: NULL means "defer to bot_config's
    default_kb_enabled". Displaying/toggling the raw value would misrepresent
    a chat that is effectively ON via the global default (review finding).
    """
    row = await chat_settings_repo.get(chat_id)
    raw = row.get("kb_enabled") if row else None
    if raw is not None:
        return bool(raw)
    return bool(await bot_config_repo.get("default_kb_enabled"))


async def _render_kb_menu(
    callback: CallbackQuery,
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    lang: str,
    chat_id: int,
    origin: str = "",
) -> None:
    kb_enabled = await _effective_kb_enabled(chat_settings_repo, bot_config_repo, chat_id)

    if isinstance(callback.message, Message):
        await safe_edit_text(
            callback.message,
            _KB_MENU_TITLE[lang],
            reply_markup=kb_menu_keyboard(
                lang, chat_id=chat_id, kb_enabled=kb_enabled, origin=origin
            ),
        )


async def _render_organizers(
    callback: CallbackQuery,
    chat_settings_repo: ChatSettingsRepository,
    lang: str,
    chat_id: int,
    page: int,
    origin: str = "",
) -> None:
    row = await chat_settings_repo.get(chat_id)
    organizer_ids = _parse_organizer_ids(row.get("kb_organizer_ids") if row else None)

    organizers: list[dict[str, object]] = []
    for user_id in organizer_ids:
        fallback = ("участник " if lang == "ru" else "member ") + str(user_id)
        display_name = await resolve_display_name(callback.bot, chat_id, user_id, fallback)
        organizers.append({"user_id": user_id, "display_name": display_name})

    total = len(organizers)
    start = page * _PER_PAGE
    page_items = organizers[start : start + _PER_PAGE]

    text = _ORGS_TITLE[lang] if total else f"{_ORGS_TITLE[lang]}\n\n{_ORGS_EMPTY[lang]}"
    keyboard = kb_organizers_keyboard(
        page_items,
        lang=lang,
        chat_id=chat_id,
        page=page,
        total=total,
        per_page=_PER_PAGE,
        origin=origin,
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(callback.message, text, reply_markup=keyboard)


async def _render_organizer_picker(
    callback: CallbackQuery,
    message_repo: MessageRepository,
    lang: str,
    chat_id: int,
    page: int,
    origin: str = "",
) -> None:
    """Render the B-2 participant picker page (candidates ranked by activity)."""
    candidates, total = await message_repo.get_top_active_users(chat_id, page, _PICKER_PER_PAGE)
    text = _PICKER_TITLE[lang] if total else _PICKER_EMPTY[lang]
    keyboard = kb_organizer_picker_keyboard(
        candidates,
        lang=lang,
        chat_id=chat_id,
        page=page,
        total=total,
        per_page=_PICKER_PER_PAGE,
        origin=origin,
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(callback.message, text, reply_markup=keyboard)


# ── Chat picker ───────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_kb:"))
async def handle_kb_picker(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Show the whitelisted-chat picker (KB is per-chat)."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    page = int(parts[2]) if len(parts) > 2 else 0

    await callback.answer()
    await _render_chat_picker(callback, admin_repo, lang, page)


# ── Per-chat submenu ─────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_kb_menu:"))
async def handle_kb_menu(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Show the per-chat KB submenu (organizers + toggle)."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        chat_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return

    await callback.answer()
    await _render_kb_menu(
        callback, chat_settings_repo, bot_config_repo, lang, chat_id, parse_origin(parts, 3)
    )


@router.callback_query(F.data.startswith("adm_kb_toggle:"))
async def handle_kb_toggle(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
) -> None:
    """Flip kb_enabled for a chat.

    Invalidates ``ChatConfigService``'s cache after the write (E-1), keeping
    the "write settings -> drop the cache" invariant uniform across every
    write path (``chat_events.py`` already did this).

    Note what this does NOT fix, contrary to what E-1 originally claimed:
    there is no cross-update staleness to fix today, because
    ``ChatConfigService`` is Dishka ``Scope.REQUEST`` (``src/di.py``), so each
    update builds a fresh service with an empty cache and the 60s TTL never
    spans two updates. Measured 2026-08-06: two messages 14s apart both ran
    ``ensure_exists()``, i.e. the cache was cold both times. This call is
    therefore defensive, and becomes load-bearing the moment the service
    moves to ``Scope.APP`` -- see TD-046, which must land together with the
    two still-missing invalidations in ``admin.py`` (approve / whitelist
    removal). Mirrors ``admin_chat_panel.py``'s ``handle_chat_panel_toggle``.
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        chat_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return

    # Flip the EFFECTIVE value (raw column may be NULL = inherited global
    # default): a chat that is ON via default_kb_enabled must turn OFF on the
    # first tap, not "enable" what was already effectively enabled.
    effective = await _effective_kb_enabled(chat_settings_repo, bot_config_repo, chat_id)
    new_value = not effective
    await chat_settings_repo.set_field(chat_id, "kb_enabled", new_value)
    chat_config_service.invalidate(chat_id)

    await callback.answer(_TOGGLE_ON[lang] if new_value else _TOGGLE_OFF[lang])
    await _render_kb_menu(
        callback, chat_settings_repo, bot_config_repo, lang, chat_id, parse_origin(parts, 3)
    )


# ── Organizers ────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_kb_orgs:"))
async def handle_kb_organizers(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Show the paginated organizer list for a chat."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        chat_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return

    await callback.answer()
    await _render_organizers(
        callback, chat_settings_repo, lang, chat_id, page, parse_origin(parts, 4)
    )


@router.callback_query(F.data.startswith("adm_kb_org_rm:"))
async def handle_kb_organizer_remove(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Remove an organizer (single tap, no confirm — low-blast-radius edit)."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        chat_id = int(parts[2])
        user_id = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return

    row = await chat_settings_repo.get(chat_id)
    organizer_ids = _parse_organizer_ids(row.get("kb_organizer_ids") if row else None)
    if user_id in organizer_ids:
        organizer_ids.remove(user_id)
        await chat_settings_repo.set_field(chat_id, "kb_organizer_ids", json.dumps(organizer_ids))

    await callback.answer()
    await _render_organizers(callback, chat_settings_repo, lang, chat_id, 0, parse_origin(parts, 4))


@router.callback_query(F.data.startswith("adm_kb_org_add:"))
async def handle_kb_organizer_add_prompt(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
) -> None:
    """Send the add-organizer prompt with Telegram's person picker under the input.

    A NEW message, not an edit: a reply keyboard can only be attached by
    sending (the edit methods take inline markup only).
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        chat_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return

    # Every way to finish this add answers with plain text, no inline
    # keyboard, so there is no Back target (origin) to keep in state.
    await state.set_state(AdminStates.awaiting_kb_organizer)
    await state.update_data(kb_chat_id=chat_id, kb_lang=lang)

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _ADD_PROMPT[lang], reply_markup=kb_org_add_prompt_keyboard(lang)
        )


@router.callback_query(F.data.startswith("adm_kb_org_list:"))
async def handle_kb_organizer_list(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
    message_repo: FromDishka[MessageRepository],
) -> None:
    """Show the B-2 participant picker: add an organizer by tapping a name
    instead of guessing a forward/@username."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        chat_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return

    await callback.answer()
    await _render_organizer_picker(
        callback, message_repo, lang, chat_id, page, parse_origin(parts, 4)
    )


@router.callback_query(F.data.startswith("adm_kb_org_pick:"))
async def handle_kb_organizer_pick(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
) -> None:
    """Add the tapped participant as organizer (B-2 picker selection).

    Single tap, no confirm -- mirrors ``adm_kb_org_rm``'s low-blast-radius
    convention (also easy to undo via that same remove button). Clears the
    ``awaiting_kb_organizer`` FSM state set by the add-prompt screen, so a
    stray text message sent afterwards isn't misread as a username reply.
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(_NOT_ADMIN["en"], show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        chat_id = int(parts[2])
        user_id = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return

    await state.clear()
    await _add_organizer(chat_settings_repo, chat_id, user_id)

    await callback.answer()
    await _render_organizers(callback, chat_settings_repo, lang, chat_id, 0, parse_origin(parts, 4))


@router.message(F.users_shared, F.chat.type == "private")
async def handle_kb_organizer_shared(
    message: Message,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    admin_repo: FromDishka[AdminRepository],
    state: FSMContext,
) -> None:
    """The admin picked a person with «🔍 Найти в Telegram» (``request_users``).

    Telegram delivers the pick as a service message from the admin carrying
    ``users_shared``. Matched in ANY state, ahead of the forward/@username
    reader: that reader's filter would take this message too (no text, no
    forward) and answer "not found". Out of the prompt's state it is a pick
    from a keyboard that outlived its prompt, answered as stale.

    Every answer removes the keyboard. Mirrors sweet-home's sitter pick and
    nutricore's invite (``KeyboardButtonRequestUsers``).
    """
    shared = message.users_shared
    if shared is None or not await check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        logger.warning("Organizer pick ignored: not an admin")
        return

    current = await state.get_state()
    data = await state.get_data()
    chat_id = data.get("kb_chat_id") if current == AdminStates.awaiting_kb_organizer else None
    lang = (
        _get_lang(data.get("kb_lang"))
        if chat_id is not None
        else _get_lang(await admin_repo.get_admin_language(bot_config_repo))
    )

    if shared.request_id != ORG_PICK_REQUEST_ID or not shared.users or chat_id is None:
        logger.info(
            "Organizer pick is stale",
            request_id=shared.request_id,
            has_state=chat_id is not None,
        )
        await message.answer(_PICK_STALE[lang], reply_markup=ReplyKeyboardRemove())
        return

    await state.clear()
    person = shared.users[0]
    await _add_organizer(chat_settings_repo, chat_id, person.user_id)
    logger.info("Organizer added via Telegram picker", chat_id=chat_id, user_id=person.user_id)

    text = _ADD_SUCCESS[lang].format(name=html_escape(_shared_user_label(person)))
    if await _left_the_chat(message.bot, chat_id, person.user_id):
        text = f"{text}\n{_PICK_NOT_IN_CHAT[lang]}"
    await message.answer(text, reply_markup=ReplyKeyboardRemove())


@router.message(
    AdminStates.awaiting_kb_organizer,
    F.chat.type == "private",
    F.forward_origin.is_(None),
    F.text.in_(set(ORG_CANCEL_LABEL.values())),
)
async def handle_kb_organizer_cancel_label(message: Message, state: FSMContext) -> None:
    """«✖️ Отмена» under the prompt. A label is user text, so it is routed
    before the reader below, which would look it up as a username."""
    lang = _get_lang((await state.get_data()).get("kb_lang"))
    await state.clear()
    await message.answer(_ADD_CANCELLED[lang], reply_markup=ReplyKeyboardRemove())


@router.message(
    AdminStates.awaiting_kb_organizer,
    F.chat.type == "private",
    F.forward_origin.is_(None),
    F.text.in_(set(ORG_PICK_LABEL.values())),
)
async def handle_kb_organizer_pick_label_as_text(message: Message, state: FSMContext) -> None:
    """The picker's label arrived as text: this client cannot show the picker.
    The prompt stays open for the fallback (forward or @username)."""
    lang = _get_lang((await state.get_data()).get("kb_lang"))
    await message.answer(_PICK_UNSUPPORTED[lang])


@router.message(
    AdminStates.awaiting_kb_organizer,
    F.chat.type == "private",
    # A slash guard that does NOT break the forward path (TD-049). This
    # handler serves two inputs: a forwarded message, and a typed `@username`.
    # A bare `~F.text.startswith("/")` -- the pattern used elsewhere -- would
    # also reject a FORWARDED message whose own text happens to start with a
    # slash, and that is a legitimate organizer add: measured, forwarding a
    # message reading "/kb" currently adds the organizer, and the bare guard
    # sends it to the /kb command handler instead while leaving the state set.
    # So the guard applies only when the admin is TYPING. Captions count too:
    # aiogram reads a command from `text or caption`, so `/help` under a photo
    # is a real command that a text-only guard lets through.
    F.forward_origin.is_not(None) | (~F.text.startswith("/") & ~F.caption.startswith("/")),
)
async def handle_kb_organizer_add_reply(
    message: Message,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    message_repo: FromDishka[MessageRepository],
    state: FSMContext,
) -> None:
    """Resolve a forwarded message or a plain `@username` into a new organizer.

    Two independent resolution paths (B-1):
    - Forward: uses `forward_origin` (not the legacy `forward_from`, which
      Telegram stopped populating — that was the original dead-end bug).
      `MessageOriginHiddenUser` means the sender has forward privacy on;
      that's a distinct, actionable case from "no forward at all", so it
      gets its own copy rather than the generic not_found.
    - Plain `@username`: resolved via this chat's message history (the Bot
      API has no username lookup). Only works for usernames the bot has
      already seen post in this chat; distinguishes "never seen this
      username anywhere" from "seen it, just not in this chat".
    """
    # Read the context, then clear BEFORE the authority check. Hoisted
    # deliberately (TD-049): with the clear below the early return, an admin
    # removed from `bot_config.admin_ids` between opening this prompt and
    # answering it left the state set for the lifetime of the process --
    # `MemoryStorage` has no TTL, so only a restart cleared it.
    data = await state.get_data()
    chat_id = data.get("kb_chat_id")
    lang = _get_lang(data.get("kb_lang"))
    await state.clear()

    if not await check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        return

    if chat_id is None:
        return

    user_id: int
    display_name: str

    origin = message.forward_origin
    if origin is not None:
        if isinstance(origin, MessageOriginHiddenUser):
            await message.reply(_ADD_FORWARD_HIDDEN[lang], reply_markup=_NO_KEYBOARD)
            return
        if not isinstance(origin, MessageOriginUser):
            # Forwarded from a chat/channel, not a user -- not organizer material.
            await message.reply(_ADD_NOT_FOUND[lang], reply_markup=_NO_KEYBOARD)
            return
        forward_user = origin.sender_user
        user_id = forward_user.id
        display_name = (
            f"@{forward_user.username}"
            if forward_user.username
            else (forward_user.first_name or str(user_id))
        )
    else:
        username = _extract_username(message.text)
        if username is None:
            await message.reply(_ADD_NOT_FOUND[lang], reply_markup=_NO_KEYBOARD)
            return
        found = await message_repo.find_by_username(chat_id, username)
        if found is None:
            known_elsewhere = await message_repo.username_seen_elsewhere(chat_id, username)
            await message.reply(
                _ADD_NOT_IN_CHAT[lang] if known_elsewhere else _ADD_NOT_FOUND[lang],
                reply_markup=_NO_KEYBOARD,
            )
            return
        user_id = found["user_id"]
        display_name = f"@{username}"

    await _add_organizer(chat_settings_repo, chat_id, user_id)
    await message.reply(
        _ADD_SUCCESS[lang].format(name=html_escape(display_name)),
        reply_markup=ReplyKeyboardRemove(),
    )
