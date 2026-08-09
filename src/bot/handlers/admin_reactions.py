"""Reactions admin sub-router (R-D1).

Handles:
- ``adm_react:*``        — chat picker (reactions config is per-chat, ADR-0004)
- ``adm_react_menu:*``   — per-chat submenu: live admin-rights status line
  (ADR-0004 Decision 5) + ``reactions_enabled`` / ``reactions_history_enabled``
  toggles
- ``adm_react_toggle:*`` — flip one of the two toggles; toggling
  ``reactions_enabled`` ON also runs the live admin-rights check immediately
  and surfaces a popup warning if the bot isn't an admin (Decision 5(a):
  "active check when enabling the module")

See docs/decisions/ADR-0004-reactions-data-model.md Decision 5 for why this
is a live Bot API check, never a cached column, and Decision 3 for why the
two toggles stay independent.
"""

from __future__ import annotations

from typing import Any

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.admin_reactions import (
    reactions_chat_picker_keyboard,
    reactions_menu_keyboard,
)
from src.bot.nav import parse_origin
from src.bot.settings_fields import field_by_code
from src.bot.utils import check_admin_direct, is_bot_chat_admin, safe_edit_text
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

logger = structlog.get_logger(__name__)

router = Router(name="admin_reactions")

_PER_PAGE = 10

_MENU_TITLE = {"ru": "😀 Реакции", "en": "😀 Reactions"}
_PICKER_TITLE = {
    "ru": "😀 Выберите чат для управления реакциями:",
    "en": "😀 Pick a chat to manage reactions for:",
}
_NO_CHATS = {"ru": "Нет чатов в whitelist.", "en": "No whitelisted chats."}
_TOGGLE_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "reactions_enabled": {
        "on": {"ru": "Модуль реакций включён", "en": "Reactions module enabled"},
        "off": {"ru": "Модуль реакций выключен", "en": "Reactions module disabled"},
    },
    "reactions_history_enabled": {
        "on": {"ru": "Хранение истории включено", "en": "History recording enabled"},
        "off": {"ru": "Хранение истории выключено", "en": "History recording disabled"},
    },
}
_NOT_ADMIN_WARNING = {
    "ru": "⚠️ Бот не администратор в этом чате — обновления о реакциях не "
    "будут приходить (Telegram не показывает отдельную ошибку). Выдайте "
    "боту права администратора.",
    "en": "⚠️ The bot isn't an administrator in this chat — reaction "
    "updates won't arrive (Telegram raises no separate error for this). "
    "Grant the bot admin rights.",
}
_ADMIN_STATUS_OK = {
    "ru": "✅ Бот — администратор в этом чате.",
    "en": "✅ The bot is an administrator in this chat.",
}
_ADMIN_STATUS_MISSING = {
    "ru": "⚠️ Бот НЕ администратор — реакции работать не будут.",
    "en": "⚠️ The bot is NOT an administrator — reactions won't work.",
}
_NOT_ADMIN = {"ru": "Нет доступа.", "en": "Access denied."}
_INVALID_FIELD = {"ru": "Некорректное поле.", "en": "Invalid field."}

_TOGGLE_FIELDS = frozenset({"reactions_enabled", "reactions_history_enabled"})


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


def _is_private(callback: CallbackQuery) -> bool:
    return isinstance(callback.message, Message) and callback.message.chat.type == "private"


def _effective(row: dict[str, Any] | None, field: str, default: bool) -> bool:
    raw = row.get(field) if row else None
    return bool(raw) if raw is not None else default


async def _resolve_toggles(
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_id: int,
) -> tuple[bool, bool]:
    """Resolve effective reactions_enabled/reactions_history_enabled.

    Mirrors admin_kb.py's ``_effective_kb_enabled``: the raw ``chat_settings``
    column is nullable (NULL = "not overridden"), falling back to the
    ``bot_config`` ``default_*`` global layer, then the ``ChatConfig``
    dataclass default (False / True respectively, ADR-0004 Decision 3) if
    neither layer sets it.
    """
    row = await chat_settings_repo.get(chat_id)
    enabled_default = await bot_config_repo.get("default_reactions_enabled")
    history_default = await bot_config_repo.get("default_reactions_history_enabled")
    reactions_enabled = _effective(
        row,
        "reactions_enabled",
        bool(enabled_default) if enabled_default is not None else False,
    )
    reactions_history_enabled = _effective(
        row,
        "reactions_history_enabled",
        bool(history_default) if history_default is not None else True,
    )
    return reactions_enabled, reactions_history_enabled


async def _resolve_bot_id(callback: CallbackQuery, bot_id_hint: int | None) -> int | None:
    """Reuse the ``dp["bot_id"]`` process-lifetime singleton when available.

    Per ADR-0004 Decision 5: "bot_id is already available as a dp[]
    singleton -- reuse it rather than threading the bot's own id through
    another path." Falls back to ``bot.me()`` (cached by aiogram after the
    first call) for callers/tests that don't thread ``bot_id`` through kwargs.
    """
    if bot_id_hint is not None:
        return bot_id_hint
    if callback.bot is not None:
        return (await callback.bot.me()).id
    return None


async def _render_chat_picker(
    callback: CallbackQuery, admin_repo: AdminRepository, lang: str, page: int
) -> None:
    chats, total = await admin_repo.get_enabled_chats_page(page, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    if page >= total_pages:
        page = max(0, total_pages - 1)
        chats, total = await admin_repo.get_enabled_chats_page(page, _PER_PAGE)

    text = _PICKER_TITLE[lang] if total else f"{_MENU_TITLE[lang]}\n\n{_NO_CHATS[lang]}"
    keyboard = reactions_chat_picker_keyboard(
        chats, lang=lang, page=page, total=total, per_page=_PER_PAGE
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(callback.message, text, reply_markup=keyboard)


async def _render_menu(
    callback: CallbackQuery,
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    lang: str,
    chat_id: int,
    bot_id_hint: int | None = None,
    origin: str = "",
) -> None:
    reactions_enabled, reactions_history_enabled = await _resolve_toggles(
        chat_settings_repo, bot_config_repo, chat_id
    )

    # ADR-0004 Decision 5: live check, not a cached column -- one Bot API
    # round-trip per render, acceptable for an on-demand admin view, not a
    # hot path.
    is_admin = False
    if callback.bot is not None:
        bot_id = await _resolve_bot_id(callback, bot_id_hint)
        if bot_id is not None:
            is_admin = await is_bot_chat_admin(callback.bot, chat_id, bot_id)

    status_line = _ADMIN_STATUS_OK[lang] if is_admin else _ADMIN_STATUS_MISSING[lang]
    text = f"{_MENU_TITLE[lang]}\n\n{status_line}"
    keyboard = reactions_menu_keyboard(
        lang,
        origin=origin,
        chat_id=chat_id,
        reactions_enabled=reactions_enabled,
        reactions_history_enabled=reactions_history_enabled,
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(callback.message, text, reply_markup=keyboard)


# ── Chat picker ───────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_react:"))
async def handle_reactions_picker(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Show the whitelisted-chat picker (reactions config is per-chat)."""
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


@router.callback_query(F.data.startswith("adm_react_menu:"))
async def handle_reactions_menu(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """Show the per-chat reactions submenu (admin-rights status + toggles)."""
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
    await _render_menu(
        callback,
        chat_settings_repo,
        bot_config_repo,
        lang,
        chat_id,
        kwargs.get("bot_id"),
        parse_origin(parts, 3),
    )


@router.callback_query(F.data.startswith("adm_react_toggle:"))
async def handle_reactions_toggle(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
    **kwargs: Any,
) -> None:
    """Flip one of the two reactions toggles (ADR-0004 Decision 3: independent).

    Toggling ``reactions_enabled`` ON runs the live admin-rights check
    immediately (Decision 5(a)) and pops an alert right away if the bot
    isn't an admin -- the menu's status line (Decision 5(b)) repeats the
    same fact on every subsequent render, so the warning isn't a one-time
    popup only.

    Invalidates ``ChatConfigService``'s cache right after the write (E-1),
    before either the not-admin-warning branch or the normal confirmation
    branch, since the write has already committed by that point either way.
    As in ``admin_kb.py``'s ``handle_kb_toggle``, this is defensive rather
    than a fix for observed staleness: the service is ``Scope.REQUEST``, so
    no cache survives an update today (TD-046).
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
        raw_field = parts[3]
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return
    # The keyboard now sends the registry's short code (rx/rh) to leave room
    # for the origin token in the 64-byte payload; the full column name is
    # still accepted so a keyboard rendered before that change keeps working.
    spec = field_by_code(raw_field)
    field = raw_field if raw_field in _TOGGLE_FIELDS else (spec.key if spec else "")
    origin = parse_origin(parts, 4)
    if field not in _TOGGLE_FIELDS:
        await callback.answer(_INVALID_FIELD[lang], show_alert=True)
        return

    reactions_enabled, reactions_history_enabled = await _resolve_toggles(
        chat_settings_repo, bot_config_repo, chat_id
    )
    current = reactions_enabled if field == "reactions_enabled" else reactions_history_enabled
    new_value = not current
    await chat_settings_repo.set_field(chat_id, field, new_value)
    chat_config_service.invalidate(chat_id)

    bot_id_hint = kwargs.get("bot_id")
    if field == "reactions_enabled" and new_value and callback.bot is not None:
        bot_id = await _resolve_bot_id(callback, bot_id_hint)
        if bot_id is not None and not await is_bot_chat_admin(callback.bot, chat_id, bot_id):
            await callback.answer(_NOT_ADMIN_WARNING[lang], show_alert=True)
            await _render_menu(
                callback, chat_settings_repo, bot_config_repo, lang, chat_id, bot_id_hint, origin
            )
            return

    toggle_copy = _TOGGLE_LABELS[field]["on" if new_value else "off"]
    await callback.answer(toggle_copy[lang])
    await _render_menu(
        callback, chat_settings_repo, bot_config_repo, lang, chat_id, bot_id_hint, origin
    )
