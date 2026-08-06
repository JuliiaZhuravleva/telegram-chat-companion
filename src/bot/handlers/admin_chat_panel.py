"""Chat settings panel sub-router (B-1, ADR-0006; inherited-marker: B-2).

Handles:
- ``adm_pnl:*``       — chat picker (own dedicated picker, Decision 4)
- ``adm_pnl_menu:*``  — per-chat panel render (``render_chat_panel``, Decision 1)
- ``adm_pnl_tgl:*``   — generic bool-field toggle for fields with no existing
  dedicated UI (Decision 3). The three KB/Reactions fields are link-only
  (Decision 2) and are rejected here -- their write path stays
  admin_kb.py's/admin_reactions.py's own toggle handlers, never duplicated.

``render_chat_panel`` is a pure ``(text, keyboard)`` function, parameterized
by ``chat_id`` alone -- no ``CallbackQuery``/permission check inside, so a
future in-chat entry point (PRD Цель 2) can call it verbatim with a
different guard at its own call site. The permission check
(``check_admin_direct`` + private-chat) happens once per callback handler,
before ``render_chat_panel`` is invoked -- same split KB/Reactions already
use for their own ``_render_*`` helpers.

See docs/decisions/ADR-0006-chat-settings-panel-architecture.md.
"""

from __future__ import annotations

from html import escape
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.keyboards.admin_chat_panel import chat_panel_keyboard, chat_panel_picker_keyboard
from src.bot.settings_fields import FieldType, field_by_code
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
_TOGGLE_ON = {"ru": "Включено", "en": "Enabled"}
_TOGGLE_OFF = {"ru": "Выключено", "en": "Disabled"}

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
    """Direct-read effective bool, bypassing ``ChatConfigService``'s cache.

    Used only for the KB/Reactions link rows (Decision 2): their own toggle
    handlers (admin_kb.py/admin_reactions.py) don't self-invalidate the
    shared cache yet (E-1, not landed), so a cached read here could show
    stale state right after a tap on the dedicated submenu -- exactly the
    delayed-toggle bug the PRD documents. Mirrors admin_kb.py's
    ``_effective_kb_enabled`` / admin_reactions.py's ``_resolve_toggles``.
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
    new_value = not getattr(config, field.key)
    await chat_settings_repo.set_field(chat_id, field.key, new_value)
    chat_config_service.invalidate(chat_id)

    await callback.answer(_TOGGLE_ON[lang] if new_value else _TOGGLE_OFF[lang])
    await _render_and_show_panel(
        callback, chat_settings_repo, bot_config_repo, chat_config_service, lang, chat_id
    )
