"""Chat settings panel sub-router (B-1, ADR-0006; inherited-marker: B-2).

Handles:
- ``adm_pnl:*``       — chat picker (own dedicated picker, Decision 4)
- ``adm_pnl_menu:*``  — per-chat panel render (``render_chat_panel``, Decision 1)
- ``adm_pnl_tgl:*``   — generic bool-field toggle for fields with no existing
  dedicated UI (Decision 3). The three KB/Reactions fields are link-only
  (Decision 2) and are rejected here -- their write path stays
  admin_kb.py's/admin_reactions.py's own toggle handlers, never duplicated.
- ``adm_pnl_tol:*``   — dedicated single-field FSM edit flow for
  ``tolerance_level`` (ADR-0008 Decision 10). Independent of F-1's still-
  deferred generic non-BOOL editing; reuses
  ``AdminStates.awaiting_setting_value`` (grep-verified unused elsewhere).

``render_chat_panel`` is a pure ``(text, keyboard)`` function, parameterized
by ``chat_id`` alone -- no ``CallbackQuery``/permission check inside, so a
future in-chat entry point (PRD Цель 2) can call it verbatim with a
different guard at its own call site. The permission check
(``check_admin_direct`` + private-chat) happens once per callback handler,
before ``render_chat_panel`` is invoked -- same split KB/Reactions already
use for their own ``_render_*`` helpers.

See docs/decisions/ADR-0006-chat-settings-panel-architecture.md and
docs/decisions/ADR-0008-sticker-explicitness-tolerance.md (``adm_pnl_tol:``).
"""

from __future__ import annotations

from html import escape
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.admin_chat_panel import chat_panel_keyboard, chat_panel_picker_keyboard
from src.bot.settings_fields import FieldType, field_by_code
from src.bot.states.admin import AdminStates
from src.bot.utils import check_admin_direct, safe_edit_text
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

logger = structlog.get_logger(__name__)

router = Router(name="admin_chat_panel")

_PER_PAGE = 10

_PANEL_TITLE = {"ru": "⚙️ Настройки чата", "en": "⚙️ Chat settings"}
_PICKER_TITLE = {
    "ru": "⚙️ Выберите чат для настройки:",
    "en": "⚙️ Pick a chat to configure:",
}
_NO_CHATS = {"ru": "Нет чатов в whitelist.", "en": "No whitelisted chats."}
_NOT_ADMIN = {"ru": "Нет доступа.", "en": "Access denied."}
_INVALID_FIELD = {"ru": "Некорректное поле.", "en": "Invalid field."}
_NO_ROW = {
    "ru": "Чат не найден в настройках — настройка не изменена.",
    "en": "Chat not found in settings — nothing was changed.",
}
_TOGGLE_ON = {"ru": "Включено", "en": "Enabled"}
_TOGGLE_OFF = {"ru": "Выключено", "en": "Disabled"}
_TOLERANCE_PROMPT = {
    "ru": "Введите новый уровень приличия стикеров (0.0–1.0, где 1.0 — без ограничений):",
    "en": "Enter a new sticker tolerance level (0.0–1.0, where 1.0 = no restriction):",
}
_TOLERANCE_INVALID = {
    "ru": "Нужно число от 0.0 до 1.0. Попробуйте ещё раз.",
    "en": "Enter a number between 0.0 and 1.0. Try again.",
}
_TOLERANCE_SAVED = {
    "ru": "Уровень приличия установлен: {value}",
    "en": "Tolerance level set to {value}",
}

# Decision 2: these three fields render as a link to the existing KB/
# Reactions sub-panels; the generic toggle handler must refuse them even
# though the A-1 registry marks them as ordinary FieldType.BOOL entries
# (the registry is generic across consumers, it doesn't decide this).
_LINK_ONLY_KEYS = frozenset({"kb_enabled", "reactions_enabled", "reactions_history_enabled"})


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


def _is_private(callback: CallbackQuery) -> bool:
    return isinstance(callback.message, Message) and callback.message.chat.type == "private"


async def _fresh_effective(
    row: dict[str, Any] | None,
    bot_config_repo: BotConfigRepository,
    key: str,
    fallback: bool,
) -> bool:
    """Direct-read effective bool for the KB/Reactions link rows (Decision 2).

    Reads the raw row + global default instead of going through
    ``ChatConfigService``, mirroring admin_kb.py's ``_effective_kb_enabled``
    and admin_reactions.py's ``_resolve_toggles`` so all three render the
    same value by the same rule. Note this is not a cache workaround: the
    service is ``Scope.REQUEST`` (``src/di.py``), so nothing it caches
    outlives one update anyway -- see TD-046.
    """
    raw = row.get(key) if row else None
    if raw is not None:
        return bool(raw)
    default = await bot_config_repo.get(f"default_{key}")
    return bool(default) if default is not None else fallback


async def render_chat_panel(
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    lang: str,
    chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    """Render the panel's ``(text, keyboard)`` for a chat (ADR-0006 Decision 1).

    ``row`` (the raw ``chat_settings`` columns) is threaded into
    ``chat_panel_keyboard`` alongside the effective ``config`` so it can show
    the "inherited from default" marker (B-2) -- the effective value alone
    can't distinguish an explicit override from an inherited default.
    """
    row = await chat_settings_repo.get(chat_id)
    config = await chat_config_service.get_config(chat_id)
    kb_status = await _fresh_effective(row, bot_config_repo, "kb_enabled", False)
    reactions_status = (
        await _fresh_effective(row, bot_config_repo, "reactions_enabled", False),
        await _fresh_effective(row, bot_config_repo, "reactions_history_enabled", True),
    )

    title = row.get("chat_title") if row else None
    label = escape(str(title)) if title else str(chat_id)
    text = f"{_PANEL_TITLE[lang]}\n\n{label} <code>{chat_id}</code>"

    keyboard = chat_panel_keyboard(
        lang,
        chat_id=chat_id,
        config=config,
        row=row,
        kb_status=kb_status,
        reactions_status=reactions_status,
    )
    return text, keyboard


async def _render_picker(
    callback: CallbackQuery, admin_repo: AdminRepository, lang: str, page: int
) -> None:
    chats, total = await admin_repo.get_enabled_chats_page(page, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    if page >= total_pages:
        page = max(0, total_pages - 1)
        chats, total = await admin_repo.get_enabled_chats_page(page, _PER_PAGE)

    text = _PICKER_TITLE[lang] if total else f"{_PANEL_TITLE[lang]}\n\n{_NO_CHATS[lang]}"
    keyboard = chat_panel_picker_keyboard(
        chats, lang=lang, page=page, total=total, per_page=_PER_PAGE
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(callback.message, text, reply_markup=keyboard)


async def _render_and_show_panel(
    callback: CallbackQuery,
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    lang: str,
    chat_id: int,
) -> None:
    text, keyboard = await render_chat_panel(
        chat_settings_repo, bot_config_repo, chat_config_service, lang, chat_id
    )
    if isinstance(callback.message, Message):
        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")


# ── Chat picker ───────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_pnl:"))
async def handle_chat_panel_picker(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Show the whitelisted-chat picker (ADR-0006 Decision 4)."""
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
    await _render_picker(callback, admin_repo, lang, page)


# ── Per-chat panel ────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_pnl_menu:"))
async def handle_chat_panel_menu(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
) -> None:
    """Show the per-chat settings panel."""
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
    await _render_and_show_panel(
        callback, chat_settings_repo, bot_config_repo, chat_config_service, lang, chat_id
    )


@router.callback_query(F.data.startswith("adm_pnl_tgl:"))
async def handle_chat_panel_toggle(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
) -> None:
    """Flip one bool field's effective value (ADR-0006 Decision 3).

    Reuses ``ChatConfigService.get_config()`` for the effective value instead
    of a per-field helper (avoids ~20 near-identical ones) and self-
    invalidates the cache after writing -- this is B-1's own job, distinct
    from E-1's retrofit of the *existing* KB/Reactions toggle handlers
    (Decision 2), whose fields this handler explicitly refuses.
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
        code = parts[3]
    except (ValueError, IndexError):
        await callback.answer("Invalid data", show_alert=True)
        return

    field = field_by_code(code)
    if field is None or field.type is not FieldType.BOOL or field.key in _LINK_ONLY_KEYS:
        await callback.answer(_INVALID_FIELD[lang], show_alert=True)
        return

    config = await chat_config_service.get_config(chat_id)
    effective = bool(getattr(config, field.key))
    # The negation happens inside the UPDATE, so a double-tap cannot collapse
    # two flips into one; `effective` only supplies the starting point for a
    # NULL ("inherited") column. A None result means no chat_settings row
    # matched -- report the failure instead of a toast that claims otherwise.
    new_value = await chat_settings_repo.toggle_bool_field(chat_id, field.key, effective)
    if new_value is None:
        logger.warning(
            "Toggle affected no chat_settings row",
            chat_id=chat_id,
            field=field.key,
        )
        await callback.answer(_NO_ROW[lang], show_alert=True)
        return
    chat_config_service.invalidate(chat_id)

    await callback.answer(_TOGGLE_ON[lang] if new_value else _TOGGLE_OFF[lang])
    await _render_and_show_panel(
        callback, chat_settings_repo, bot_config_repo, chat_config_service, lang, chat_id
    )


# ── tolerance_level (ADR-0008 Decision 10) ──────────────────────────────────


@router.callback_query(F.data.startswith("adm_pnl_tol:"))
async def handle_chat_panel_tolerance_prompt(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
) -> None:
    """Prompt for a new ``tolerance_level`` value (ADR-0008 Decision 10).

    A dedicated, single-field FSM flow independent of F-1 (generic non-BOOL
    settings editing, deferred to a separate iteration) -- reuses
    ``AdminStates.awaiting_setting_value``, a scaffold state that was
    declared and never wired to a handler before this.
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

    await state.set_state(AdminStates.awaiting_setting_value)
    await state.update_data(tol_chat_id=chat_id, tol_lang=lang)

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(_TOLERANCE_PROMPT[lang])


@router.message(AdminStates.awaiting_setting_value, F.chat.type == "private")
async def handle_chat_panel_tolerance_input(
    message: Message,
    state: FSMContext,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    chat_config_service: FromDishka[ChatConfigService],
) -> None:
    """Validate and persist the new ``tolerance_level`` (ADR-0008 Decision 10).

    Reject-not-clamp, same posture as Decision 4's Vision-score validation:
    non-numeric or out-of-``[0.0, 1.0]`` input re-prompts (state stays set)
    instead of silently no-opping or clamping into range.
    """
    data = await state.get_data()
    chat_id = data.get("tol_chat_id")
    lang = _get_lang(data.get("tol_lang"))
    if chat_id is None:
        await state.clear()
        return

    if not await check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        await state.clear()
        return

    raw = (message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        await message.reply(_TOLERANCE_INVALID[lang])
        return
    if not 0.0 <= value <= 1.0:
        await message.reply(_TOLERANCE_INVALID[lang])
        return

    await state.clear()
    await chat_settings_repo.set_field(chat_id, "tolerance_level", value)
    chat_config_service.invalidate(chat_id)

    await message.reply(_TOLERANCE_SAVED[lang].format(value=f"{value:g}"))
    text, keyboard = await render_chat_panel(
        chat_settings_repo, bot_config_repo, chat_config_service, lang, chat_id
    )
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
