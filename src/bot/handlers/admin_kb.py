"""Knowledge Base admin sub-router (A4).

Handles:
- ``adm_kb:*``      — chat picker (KB is per-chat, ADR-0003)
- ``adm_kb_menu:*``  — per-chat submenu (organizers + kb_enabled toggle)
- ``adm_kb_toggle:*`` — flip kb_enabled
- ``adm_kb_orgs:*``  — paginated organizer list
- ``adm_kb_org_add:*`` / message reply — add organizer (forward a message)
- ``adm_kb_org_rm:*`` — remove organizer

See docs/design/kb-copy-register.md (G2) for copy and
docs/decisions/ADR-0003-chat-facts-data-model.md (G1) for the schema.
"""

from __future__ import annotations

import json
from html import escape as html_escape
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.admin_kb import (
    kb_chat_picker_keyboard,
    kb_menu_keyboard,
    kb_organizers_keyboard,
)
from src.bot.states.admin import AdminStates
from src.bot.utils import resolve_display_name, safe_edit_text
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.utils import parse_admin_ids

logger = structlog.get_logger(__name__)

router = Router(name="admin_kb")

_PER_PAGE = 10

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
    "ru": "Перешлите сообщение от нового организатора или отправьте его @username.",
    "en": "Forward a message from the new organizer, or send their @username.",
}
_ADD_SUCCESS = {
    "ru": "✅ {name} добавлен(а) в организаторы.",
    "en": "✅ {name} added as organizer.",
}
_ADD_NOT_FOUND = {
    "ru": "🤔 Не нашёл такого участника в этом чате.",
    "en": "🤔 Couldn't find that member in this chat.",
}
_NOT_ADMIN = {"ru": "Нет доступа.", "en": "Access denied."}


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


def _is_private(callback: CallbackQuery) -> bool:
    return isinstance(callback.message, Message) and callback.message.chat.type == "private"


async def _check_admin_direct(bot_config_repo: BotConfigRepository, user_id: int | None) -> bool:
    """Check bot-admin status directly (mirrors admin_sticker.py's helper)."""
    if user_id is None:
        return False
    admin_ids_raw = await bot_config_repo.get("admin_ids")
    return user_id in parse_admin_ids(admin_ids_raw)


def _parse_organizer_ids(raw: Any) -> list[int]:
    """Defensively parse kb_organizer_ids (asyncpg may return list or JSON str)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [int(v) for v in raw]


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
) -> None:
    kb_enabled = await _effective_kb_enabled(chat_settings_repo, bot_config_repo, chat_id)

    if isinstance(callback.message, Message):
        await safe_edit_text(
            callback.message,
            _KB_MENU_TITLE[lang],
            reply_markup=kb_menu_keyboard(lang, chat_id=chat_id, kb_enabled=kb_enabled),
        )


async def _render_organizers(
    callback: CallbackQuery,
    chat_settings_repo: ChatSettingsRepository,
    lang: str,
    chat_id: int,
    page: int,
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
        page_items, lang=lang, chat_id=chat_id, page=page, total=total, per_page=_PER_PAGE
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
    if not await _check_admin_direct(
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
    if not await _check_admin_direct(
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
    await _render_kb_menu(callback, chat_settings_repo, bot_config_repo, lang, chat_id)


@router.callback_query(F.data.startswith("adm_kb_toggle:"))
async def handle_kb_toggle(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Flip kb_enabled for a chat."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await _check_admin_direct(
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

    await callback.answer(_TOGGLE_ON[lang] if new_value else _TOGGLE_OFF[lang])
    await _render_kb_menu(callback, chat_settings_repo, bot_config_repo, lang, chat_id)


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
    if not await _check_admin_direct(
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
    await _render_organizers(callback, chat_settings_repo, lang, chat_id, page)


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
    if not await _check_admin_direct(
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
    await _render_organizers(callback, chat_settings_repo, lang, chat_id, 0)


@router.callback_query(F.data.startswith("adm_kb_org_add:"))
async def handle_kb_organizer_add_prompt(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
) -> None:
    """Prompt the admin to forward a message / send @username for the new organizer."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await _check_admin_direct(
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

    await state.set_state(AdminStates.awaiting_kb_organizer)
    await state.update_data(kb_chat_id=chat_id, kb_lang=lang)

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(_ADD_PROMPT[lang])


@router.message(AdminStates.awaiting_kb_organizer, F.chat.type == "private")
async def handle_kb_organizer_add_reply(
    message: Message,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
) -> None:
    """Resolve the forwarded message (or @username) into a new organizer.

    Phase 1 scope: only forwarded-message resolution is reliable (gives a
    concrete user id directly). A plain ``@username`` with no forward has no
    lookup path yet (no chat-scoped username index) and falls through to the
    same not_found copy — flagged as a follow-up, not silently faked.
    """
    if not await _check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        return

    data = await state.get_data()
    chat_id = data.get("kb_chat_id")
    lang = _get_lang(data.get("kb_lang"))
    await state.clear()

    if chat_id is None:
        return

    forward_user = getattr(message, "forward_from", None)
    if forward_user is None:
        await message.reply(_ADD_NOT_FOUND[lang])
        return

    user_id = forward_user.id
    display_name = (
        f"@{forward_user.username}"
        if forward_user.username
        else (forward_user.first_name or str(user_id))
    )

    row = await chat_settings_repo.get(chat_id)
    organizer_ids = _parse_organizer_ids(row.get("kb_organizer_ids") if row else None)
    if user_id not in organizer_ids:
        organizer_ids.append(user_id)
        await chat_settings_repo.set_field(chat_id, "kb_organizer_ids", json.dumps(organizer_ids))

    await message.reply(_ADD_SUCCESS[lang].format(name=html_escape(display_name)))
