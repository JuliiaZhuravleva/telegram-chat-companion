"""Admin panel handlers — DM-based admin interface.

Entry points:
- ``/admin`` or ``/settings`` in private chat (IsAdmin) → main menu
- Callback queries with ``adm_`` prefix → menu navigation

All callbacks embed language in callback_data for stateless operation:
    ``adm_{action}:{lang}:{param1}:{param2}:...``
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.keyboards.admin import (
    language_keyboard,
    main_menu_keyboard,
    stats_keyboard,
    whitelist_menu_keyboard,
)
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository

logger = structlog.get_logger(__name__)

router = Router(name="admin")

# ---------------------------------------------------------------------------
# i18n texts
# ---------------------------------------------------------------------------

_MENU_TITLE: dict[str, str] = {
    "ru": "<b>Панель администратора</b>",
    "en": "<b>Admin Panel</b>",
}

_LANG_TITLE: dict[str, str] = {
    "ru": "<b>Выберите язык интерфейса</b>",
    "en": "<b>Select interface language</b>",
}

_LANG_SAVED: dict[str, str] = {
    "ru": "Язык установлен: Русский",
    "en": "Language set: English",
}

_WL_TITLE: dict[str, str] = {
    "ru": "<b>Управление доступом</b>",
    "en": "<b>Access Management</b>",
}

_STATS_TITLE: dict[str, str] = {
    "ru": "<b>Статистика бота</b>",
    "en": "<b>Bot Statistics</b>",
}

_NOT_ADMIN: dict[str, str] = {
    "ru": "У вас нет доступа.",
    "en": "Access denied.",
}

_PERIOD_LABELS: dict[str, dict[str, str]] = {
    "1h": {"ru": "за 1 час", "en": "last 1 hour"},
    "24h": {"ru": "за 24 часа", "en": "last 24 hours"},
    "7d": {"ru": "за 7 дней", "en": "last 7 days"},
}

_INTERVAL_MAP: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}

_PLACEHOLDER: dict[str, str] = {
    "ru": "Функция будет доступна позже.",
    "en": "Feature coming soon.",
}


def _get_lang(lang: str | None) -> str:
    """Normalize language code to ru/en."""
    return lang if lang in ("ru", "en") else "ru"


# ---------------------------------------------------------------------------
# Helper: admin check for callbacks
# ---------------------------------------------------------------------------

def _check_admin(data: dict[str, Any]) -> bool:
    """Check if current user is admin (from middleware-injected data)."""
    return bool(data.get("is_admin", False))


def _is_private(callback: CallbackQuery) -> bool:
    """Check that the callback originates from a private chat."""
    msg = callback.message
    if isinstance(msg, Message):
        return msg.chat.type == "private"
    return False


# ---------------------------------------------------------------------------
# Commands: /admin, /settings
# ---------------------------------------------------------------------------

@router.message(Command("admin", "settings"), IsAdmin())
async def handle_admin_command(
    message: Message,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Show admin panel main menu (private chat only)."""
    if message.chat.type != "private":
        return

    lang = _get_lang(await admin_repo.get_admin_language(bot_config_repo))
    await message.answer(
        _MENU_TITLE[lang],
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(lang),
    )


# ---------------------------------------------------------------------------
# Callback: main menu
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm_menu:"))
async def handle_menu(callback: CallbackQuery, **kwargs: Any) -> None:
    """Show or return to main menu."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", "Access denied."), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)

    await callback.answer()

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            _MENU_TITLE[lang],
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(lang),
        )


# ---------------------------------------------------------------------------
# Callback: language selector
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm_lang:"))
async def handle_language_menu(callback: CallbackQuery, **kwargs: Any) -> None:
    """Show language selection menu."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)

    # Don't match adm_lang_set
    action = parts[0] if parts else ""
    if action != "adm_lang":
        return

    await callback.answer()

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            _LANG_TITLE[lang],
            parse_mode="HTML",
            reply_markup=language_keyboard(lang),
        )


@router.callback_query(F.data.startswith("adm_lang_set:"))
async def handle_language_set(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """Save admin language preference."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    new_lang = _get_lang(parts[2] if len(parts) > 2 else None)

    await admin_repo.set_admin_language(bot_config_repo, new_lang)
    await callback.answer(_LANG_SAVED[new_lang])

    # Refresh menu in new language
    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            _MENU_TITLE[new_lang],
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(new_lang),
        )


# ---------------------------------------------------------------------------
# Callback: close
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm_close:"))
async def handle_close(callback: CallbackQuery, **kwargs: Any) -> None:
    """Close admin panel (delete message)."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    await callback.answer()
    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.delete()
        except Exception:
            logger.warning("Failed to delete admin panel message")


# ---------------------------------------------------------------------------
# Callback: whitelist menu (placeholder — full impl in Stage 3.1.2)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm_wl:"))
async def handle_whitelist_menu(callback: CallbackQuery, **kwargs: Any) -> None:
    """Show whitelist management menu."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)

    # Don't match adm_wl_chats, adm_wl_pending, etc.
    action = parts[0] if parts else ""
    if action != "adm_wl":
        return

    await callback.answer()

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            _WL_TITLE[lang],
            parse_mode="HTML",
            reply_markup=whitelist_menu_keyboard(lang),
        )


# ---------------------------------------------------------------------------
# Callback: statistics
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm_stats:"))
async def handle_stats(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Show bot statistics for a given period."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    period = parts[2] if len(parts) > 2 else "24h"
    if period not in _INTERVAL_MAP:
        period = "24h"

    await callback.answer()

    interval = _INTERVAL_MAP[period]
    period_label = _PERIOD_LABELS[period][lang]

    messages = await admin_repo.get_message_count(interval)
    responses = await admin_repo.get_response_count(interval)
    unauth = await admin_repo.get_unauth_count(interval)
    active_chats = await admin_repo.get_active_chats_count(interval)
    enabled_chats = await admin_repo.get_enabled_chats_count()

    if lang == "ru":
        text = (
            f"<b>Статистика бота</b> ({period_label})\n\n"
            f"Чатов в whitelist: {enabled_chats}\n"
            f"Активных чатов: {active_chats}\n"
            f"Сообщений: {messages}\n"
            f"Ответов бота: {responses}\n"
            f"Неавторизованных: {unauth}"
        )
    else:
        text = (
            f"<b>Bot Statistics</b> ({period_label})\n\n"
            f"Whitelisted chats: {enabled_chats}\n"
            f"Active chats: {active_chats}\n"
            f"Messages: {messages}\n"
            f"Bot responses: {responses}\n"
            f"Unauthorized: {unauth}"
        )

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=stats_keyboard(lang, period),
        )


# ---------------------------------------------------------------------------
# Placeholders for future stages (prevent "unhandled callback" warnings)
# ---------------------------------------------------------------------------


async def _placeholder_callback(callback: CallbackQuery, **kwargs: Any) -> None:
    """Generic placeholder: show alert and keep the current screen."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    await callback.answer(_PLACEHOLDER[lang], show_alert=True)


@router.callback_query(F.data.startswith("adm_stk:"))
async def handle_stickers_placeholder(
    callback: CallbackQuery, **kwargs: Any
) -> None:
    """Sticker management — placeholder for Stage 3.1.5."""
    await _placeholder_callback(callback, **kwargs)


@router.callback_query(F.data.startswith("adm_defs:"))
async def handle_defaults_placeholder(
    callback: CallbackQuery, **kwargs: Any
) -> None:
    """Default settings — placeholder for Stage 3.1.4."""
    await _placeholder_callback(callback, **kwargs)


@router.callback_query(F.data.startswith("adm_wl_chats:"))
async def handle_wl_chats_placeholder(
    callback: CallbackQuery, **kwargs: Any
) -> None:
    """Whitelist chats list — placeholder for Stage 3.1.2."""
    await _placeholder_callback(callback, **kwargs)


@router.callback_query(F.data.startswith("adm_wl_pending:"))
async def handle_wl_pending_placeholder(
    callback: CallbackQuery, **kwargs: Any
) -> None:
    """Pending access requests — placeholder for Stage 3.1.2."""
    await _placeholder_callback(callback, **kwargs)
