"""Admin panel handlers — DM-based admin interface.

Entry points:
- ``/admin`` or ``/settings`` in private chat (IsAdmin) → main menu
- Callback queries with ``adm_`` prefix → menu navigation

All callbacks embed language in callback_data for stateless operation:
    ``adm_{action}:{lang}:{param1}:{param2}:...``
"""

from __future__ import annotations

from datetime import timedelta
from html import escape
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.keyboards.admin import (
    approved_notification_keyboard,
    chats_list_keyboard,
    costs_keyboard,
    health_keyboard,
    language_keyboard,
    main_menu_keyboard,
    pending_list_keyboard,
    rejected_notification_keyboard,
    stats_keyboard,
    whitelist_menu_keyboard,
)
from src.config import Settings
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.response_log import ResponseLogRepository

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

_HEALTH_TITLE: dict[str, str] = {
    "ru": "<b>Состояние бота</b>",
    "en": "<b>Bot Health</b>",
}

_HEALTH_NO_DATA: dict[str, str] = {
    "ru": "Нет данных о проверках.",
    "en": "No health check data available.",
}

_WL_CHATS_TITLE: dict[str, str] = {
    "ru": "<b>Чаты в whitelist</b>",
    "en": "<b>Whitelisted chats</b>",
}

_WL_PENDING_TITLE: dict[str, str] = {
    "ru": "<b>Ожидают одобрения</b>",
    "en": "<b>Pending requests</b>",
}

_WL_NO_CHATS: dict[str, str] = {
    "ru": "Нет чатов в whitelist.",
    "en": "No whitelisted chats.",
}

_WL_NO_PENDING: dict[str, str] = {
    "ru": "Нет ожидающих запросов.",
    "en": "No pending requests.",
}

_WL_APPROVED: dict[str, str] = {
    "ru": "✅ Одобрено",
    "en": "✅ Approved",
}

_WL_REJECTED: dict[str, str] = {
    "ru": "❌ Отклонено",
    "en": "❌ Rejected",
}

_WL_REMOVED: dict[str, str] = {
    "ru": "Чат удалён из whitelist.",
    "en": "Chat removed from whitelist.",
}

_WL_ALREADY_HANDLED: dict[str, str] = {
    "ru": "Запрос уже обработан.",
    "en": "Request already handled.",
}

_PER_PAGE = 5


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
# Callback: AI costs
# ---------------------------------------------------------------------------

_TASK_TYPE_LABELS: dict[str, dict[str, str]] = {
    "text": {"ru": "Текст", "en": "Text"},
    "embedding": {"ru": "Эмбеддинги", "en": "Embeddings"},
    "vision": {"ru": "Зрение", "en": "Vision"},
    "transcription": {"ru": "Транскрипция", "en": "Transcription"},
}


@router.callback_query(F.data.startswith("adm_costs:"))
async def handle_costs(
    callback: CallbackQuery,
    response_log_repo: FromDishka[ResponseLogRepository],
    **kwargs: Any,
) -> None:
    """Show AI cost breakdown for a given period."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    # Don't match adm_costs_verify
    action = parts[0] if parts else ""
    if action != "adm_costs":
        return

    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    period = parts[2] if len(parts) > 2 else "24h"
    if period not in _INTERVAL_MAP:
        period = "24h"

    await callback.answer()

    interval = _INTERVAL_MAP[period]
    period_label = _PERIOD_LABELS[period][lang]

    total_cost = await response_log_repo.get_total_cost(interval)
    by_task = await response_log_repo.get_cost_by_task_type(interval)
    by_model = await response_log_repo.get_cost_by_model(interval)

    if lang == "ru":
        lines = [f"<b>Расходы на AI</b> ({period_label})\n"]
        lines.append(f"<b>Итого:</b> ${total_cost:.4f}\n")

        if by_task:
            lines.append("<b>По типу:</b>")
            for row in by_task:
                task = row["task_type"] or "text"
                label = _TASK_TYPE_LABELS.get(task, {}).get(lang, task)
                cost = row["total_cost"]
                count = row["call_count"]
                lines.append(f"  {label}: ${cost:.4f} ({count} выз.)")
            lines.append("")

        if by_model:
            lines.append("<b>По модели:</b>")
            for row in by_model[:8]:
                model = row["model"] or "unknown"
                cost = row["total_cost"]
                count = row["call_count"]
                lines.append(f"  {escape(model)}: ${cost:.4f} ({count}x)")
    else:
        lines = [f"<b>AI Costs</b> ({period_label})\n"]
        lines.append(f"<b>Total:</b> ${total_cost:.4f}\n")

        if by_task:
            lines.append("<b>By type:</b>")
            for row in by_task:
                task = row["task_type"] or "text"
                label = _TASK_TYPE_LABELS.get(task, {}).get(lang, task)
                cost = row["total_cost"]
                count = row["call_count"]
                lines.append(f"  {label}: ${cost:.4f} ({count} calls)")
            lines.append("")

        if by_model:
            lines.append("<b>By model:</b>")
            for row in by_model[:8]:
                model = row["model"] or "unknown"
                cost = row["total_cost"]
                count = row["call_count"]
                lines.append(f"  {escape(model)}: ${cost:.4f} ({count}x)")

    text = "\n".join(lines)

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=costs_keyboard(lang, period),
        )


# ---------------------------------------------------------------------------
# Callback: cost verification (OpenAI billing API)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_costs_verify:"))
async def handle_costs_verify(
    callback: CallbackQuery,
    response_log_repo: FromDishka[ResponseLogRepository],
    settings: FromDishka[Settings],
    **kwargs: Any,
) -> None:
    """Cross-check our calculated costs with OpenAI billing API."""
    from src.services.ai.billing import OpenAIBillingClient

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
    days = {"1h": 1, "24h": 1, "7d": 7}[period]

    # Our calculated cost (OpenAI only)
    by_provider = await response_log_repo.get_cost_by_provider(interval)
    our_openai = next(
        (r["total_cost"] for r in by_provider if r["provider"] == "openai"),
        0,
    )

    api_key = settings.openai_api_key or ""
    if not api_key:
        if lang == "ru":
            err_text = "<b>Сверка расходов</b>\n\nOpenAI API ключ не настроен."
        else:
            err_text = "<b>Cost Verification</b>\n\nOpenAI API key not configured."
        msg = callback.message
        if isinstance(msg, Message):
            await msg.edit_text(
                err_text,
                parse_mode="HTML",
                reply_markup=costs_keyboard(lang, period),
            )
        return

    client = OpenAIBillingClient(api_key)
    try:
        report = await client.get_costs(days=days)
    finally:
        await client.close()

    if report.error:
        if lang == "ru":
            text = f"<b>Сверка расходов</b>\n\n{escape(report.error)}"
        else:
            text = f"<b>Cost Verification</b>\n\n{escape(report.error)}"
    else:
        openai_total = report.total_usd
        delta = openai_total - our_openai if our_openai else openai_total

        if lang == "ru":
            lines = [
                "<b>Сверка расходов (OpenAI)</b>\n",
                f"OpenAI отчёт: ${openai_total:.4f}",
                f"Наш расчёт: ${our_openai:.4f}",
                f"Разница: ${delta:.4f}",
            ]
        else:
            lines = [
                "<b>Cost Verification (OpenAI)</b>\n",
                f"OpenAI reported: ${openai_total:.4f}",
                f"Our calculation: ${our_openai:.4f}",
                f"Delta: ${delta:.4f}",
            ]
        text = "\n".join(lines)

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=costs_keyboard(lang, period),
        )


# ---------------------------------------------------------------------------
# Callback: health status
# ---------------------------------------------------------------------------


def _format_health_status(row: dict[str, Any], lang: str) -> str:
    """Format a health_log row for display in admin panel."""
    import json as _json

    status = str(row.get("status", "unknown")).upper()
    status_emoji = {
        "HEALTHY": "\u2705",
        "WARNING": "\u26a0\ufe0f",
        "CRITICAL": "\U0001f6a8",
        "SKIPPED": "\u23ed\ufe0f",
    }
    emoji = status_emoji.get(status, "\u2753")

    checked_at = row.get("checked_at")
    time_str = (
        checked_at.strftime("%Y-%m-%d %H:%M UTC") if checked_at else "?"
    )

    db_ok = row.get("db_ok", True)
    db_icon = "\u2705" if db_ok else "\u274c"
    messages_30m = row.get("messages_30m", 0)
    fallbacks_15m = row.get("fallbacks_15m", 0)

    if lang == "ru":
        lines = [
            f"{emoji} <b>Состояние бота</b>",
            f"<b>Статус:</b> {status}",
            f"<b>Время:</b> {time_str}",
            "",
            f"{db_icon} База данных",
            f"\U0001f4ac Сообщений (30м): {messages_30m}",
            f"\U0001f504 Фоллбэков (15м): {fallbacks_15m}",
        ]
    else:
        lines = [
            f"{emoji} <b>Bot Health</b>",
            f"<b>Status:</b> {status}",
            f"<b>Time:</b> {time_str}",
            "",
            f"{db_icon} Database",
            f"\U0001f4ac Messages (30m): {messages_30m}",
            f"\U0001f504 Fallbacks (15m): {fallbacks_15m}",
        ]

    # Show issues if any
    raw_issues = row.get("issues", [])
    if isinstance(raw_issues, str):
        try:
            raw_issues = _json.loads(raw_issues)
        except (ValueError, TypeError):
            raw_issues = []

    if raw_issues:
        lines.append("")
        lines.append("<b>Issues:</b>" if lang == "en" else "<b>Проблемы:</b>")
        for issue in raw_issues:
            sev = str(issue.get("severity", "warning"))
            icon = "\U0001f525" if sev == "critical" else "\u26a0\ufe0f"
            lines.append(f"  {icon} {issue.get('message', '')}")

    return "\n".join(lines)


@router.callback_query(F.data.startswith("adm_health:"))
async def handle_health(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Show latest health check result."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)

    await callback.answer()

    latest = await admin_repo.get_latest_health_check()
    if latest is None:
        text = f"{_HEALTH_TITLE[lang]}\n\n{_HEALTH_NO_DATA[lang]}"
    else:
        text = _format_health_status(latest, lang)

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=health_keyboard(lang),
        )


# ---------------------------------------------------------------------------
# Callback: whitelisted chats list (paginated)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_wl_chats:"))
async def handle_wl_chats(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Show paginated list of whitelisted chats."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0

    await callback.answer()
    await _render_wl_chats(callback, admin_repo, lang, page)


async def _render_wl_chats(
    callback: CallbackQuery,
    admin_repo: AdminRepository,
    lang: str,
    page: int,
) -> None:
    """Render whitelisted chats list (shared by chats view and remove)."""
    chats, total = await admin_repo.get_enabled_chats_page(page, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    # Clamp page to valid range
    if page >= total_pages:
        page = max(0, total_pages - 1)
        chats, total = await admin_repo.get_enabled_chats_page(page, _PER_PAGE)
        total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    if total == 0:
        text = f"{_WL_CHATS_TITLE[lang]}\n\n{_WL_NO_CHATS[lang]}"
    else:
        lines = [f"{_WL_CHATS_TITLE[lang]} ({total})\n"]
        offset = page * _PER_PAGE
        for i, chat in enumerate(chats, start=offset + 1):
            title = escape(str(chat.get("chat_title") or chat.get("chat_id", "?")))
            ctype = chat.get("chat_type", "")
            entry = f"{i}. {title}"
            if ctype:
                entry += f" <i>({escape(str(ctype))})</i>"
            lines.append(entry)
        text = "\n".join(lines)

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=chats_list_keyboard(lang, chats, page, total_pages),
        )


# ---------------------------------------------------------------------------
# Callback: remove chat from whitelist
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_wl_rm:"))
async def handle_wl_remove(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Remove a chat from the whitelist."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        chat_id = int(parts[2]) if len(parts) > 2 else None
        page = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        await callback.answer("Invalid data", show_alert=True)
        return

    if chat_id is None:
        await callback.answer("Invalid chat ID", show_alert=True)
        return

    await chat_settings_repo.set_field(chat_id, "enabled", False)
    await callback.answer(_WL_REMOVED[lang])

    # Re-render the chat list
    await _render_wl_chats(callback, admin_repo, lang, page)


# ---------------------------------------------------------------------------
# Callback: pending access requests (paginated)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_wl_pending:"))
async def handle_wl_pending(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Show paginated list of pending access requests."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        page = 0

    await callback.answer()
    await _render_wl_pending(callback, admin_repo, lang, page)


async def _render_wl_pending(
    callback: CallbackQuery,
    admin_repo: AdminRepository,
    lang: str,
    page: int,
) -> None:
    """Render pending requests list (shared by pending view and approve/reject)."""
    attempts, total = await admin_repo.get_pending_attempts_page(page, _PER_PAGE)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    # Clamp page to valid range
    if page >= total_pages:
        page = max(0, total_pages - 1)
        attempts, total = await admin_repo.get_pending_attempts_page(page, _PER_PAGE)
        total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    if total == 0:
        text = f"{_WL_PENDING_TITLE[lang]}\n\n{_WL_NO_PENDING[lang]}"
    else:
        lines = [f"{_WL_PENDING_TITLE[lang]} ({total})\n"]
        offset = page * _PER_PAGE
        for i, attempt in enumerate(attempts, start=offset + 1):
            title = escape(str(attempt.get("chat_title") or attempt.get("chat_id", "?")))
            ctype = attempt.get("chat_type", "")
            user = escape(str(attempt.get("user_first_name") or ""))
            uname = attempt.get("user_username")
            user_display = f"@{escape(str(uname))}" if uname else (user or "?")

            entry = f"{i}. {title}"
            if ctype:
                entry += f" ({escape(str(ctype))})"
            entry += f"\n    👤 {user_display}"

            msg_text = attempt.get("message_text")
            if msg_text:
                entry += f"\n    💬 {escape(str(msg_text)[:50])}"
            lines.append(entry)
        text = "\n".join(lines)

    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=pending_list_keyboard(lang, attempts, page, total_pages),
        )


# ---------------------------------------------------------------------------
# Helpers: approve / reject logic
# ---------------------------------------------------------------------------


async def _do_approve(
    admin_repo: AdminRepository,
    chat_settings_repo: ChatSettingsRepository,
    attempt_id: int,
) -> dict[str, Any] | None:
    """Approve attempt: update status + enable chat. Returns attempt or None."""
    attempt = await admin_repo.get_attempt(attempt_id)
    if not attempt or attempt.get("status") != "pending":
        return None
    await admin_repo.update_attempt_status(attempt_id, "approved")
    chat_id = attempt["chat_id"]
    await chat_settings_repo.upsert(chat_id, enabled=True)
    return attempt


async def _do_reject(
    admin_repo: AdminRepository,
    attempt_id: int,
) -> dict[str, Any] | None:
    """Reject attempt: update status. Returns attempt or None."""
    attempt = await admin_repo.get_attempt(attempt_id)
    if not attempt or attempt.get("status") != "pending":
        return None
    await admin_repo.update_attempt_status(attempt_id, "rejected")
    return attempt


# ---------------------------------------------------------------------------
# Callback: approve/reject from notification messages
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_approve:"))
async def handle_approve_notification(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Approve access from inline notification message."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        attempt_id = int(parts[2]) if len(parts) > 2 else None
    except ValueError:
        attempt_id = None

    if attempt_id is None:
        await callback.answer("Invalid data", show_alert=True)
        return

    attempt = await _do_approve(admin_repo, chat_settings_repo, attempt_id)
    if attempt is None:
        await callback.answer(_WL_ALREADY_HANDLED[lang], show_alert=True)
        return

    await callback.answer(_WL_APPROVED[lang])

    # Replace buttons with status indicator
    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_reply_markup(
            reply_markup=approved_notification_keyboard(lang),
        )


@router.callback_query(F.data.startswith("adm_reject:"))
async def handle_reject_notification(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Reject access from inline notification message."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        attempt_id = int(parts[2]) if len(parts) > 2 else None
    except ValueError:
        attempt_id = None

    if attempt_id is None:
        await callback.answer("Invalid data", show_alert=True)
        return

    attempt = await _do_reject(admin_repo, attempt_id)
    if attempt is None:
        await callback.answer(_WL_ALREADY_HANDLED[lang], show_alert=True)
        return

    await callback.answer(_WL_REJECTED[lang])

    # Replace buttons with status indicator
    msg = callback.message
    if isinstance(msg, Message):
        await msg.edit_reply_markup(
            reply_markup=rejected_notification_keyboard(lang),
        )


# ---------------------------------------------------------------------------
# Callback: approve/reject from admin panel pending list
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_wl_apr:"))
async def handle_wl_approve(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    **kwargs: Any,
) -> None:
    """Approve access from admin panel pending list."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        attempt_id = int(parts[2]) if len(parts) > 2 else None
        page = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        await callback.answer("Invalid data", show_alert=True)
        return

    if attempt_id is None:
        await callback.answer("Invalid data", show_alert=True)
        return

    attempt = await _do_approve(admin_repo, chat_settings_repo, attempt_id)
    if attempt is None:
        await callback.answer(_WL_ALREADY_HANDLED[lang], show_alert=True)
        return

    await callback.answer(_WL_APPROVED[lang])

    # Re-render pending list
    await _render_wl_pending(callback, admin_repo, lang, page)


@router.callback_query(F.data.startswith("adm_wl_rej:"))
async def handle_wl_reject(
    callback: CallbackQuery,
    admin_repo: FromDishka[AdminRepository],
    **kwargs: Any,
) -> None:
    """Reject access from admin panel pending list."""
    if not _check_admin(kwargs) or not _is_private(callback):
        await callback.answer(_NOT_ADMIN.get("en", ""), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    try:
        attempt_id = int(parts[2]) if len(parts) > 2 else None
        page = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        await callback.answer("Invalid data", show_alert=True)
        return

    if attempt_id is None:
        await callback.answer("Invalid data", show_alert=True)
        return

    attempt = await _do_reject(admin_repo, attempt_id)
    if attempt is None:
        await callback.answer(_WL_ALREADY_HANDLED[lang], show_alert=True)
        return

    await callback.answer(_WL_REJECTED[lang])

    # Re-render pending list
    await _render_wl_pending(callback, admin_repo, lang, page)


# ---------------------------------------------------------------------------
# Callback: noop (used for status indicator buttons)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "noop")
async def handle_noop(callback: CallbackQuery, **_kwargs: Any) -> None:
    """No-op handler for status indicator buttons."""
    await callback.answer()


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
