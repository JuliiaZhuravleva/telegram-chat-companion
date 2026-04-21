"""Inline keyboards for the admin panel.

All callback_data follows the pattern: ``adm_{action}:{lang}:{params...}``
Language is embedded in callback_data for stateless operation.
"""

from __future__ import annotations

from typing import cast

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.utils.telegram import build_chat_url

# ---------------------------------------------------------------------------
# i18n labels
# ---------------------------------------------------------------------------

_L: dict[str, dict[str, str]] = {
    "whitelist": {"ru": "📋 Whitelist", "en": "📋 Whitelist"},
    "rules": {"ru": "📏 Правила", "en": "📏 Rules"},
    "stickers": {"ru": "🎨 Стикеры", "en": "🎨 Stickers"},
    "defaults": {"ru": "⚙️ Настройки", "en": "⚙️ Default Settings"},
    "statistics": {"ru": "📊 Статистика", "en": "📊 Statistics"},
    "language": {"ru": "🌐 Язык / Language", "en": "🌐 Language / Язык"},
    "costs": {"ru": "💰 Расходы", "en": "💰 Costs"},
    "health": {"ru": "💚 Здоровье", "en": "💚 Health"},
    "notifications": {"ru": "🔔 Уведомления", "en": "🔔 Notifications"},
    "close": {"ru": "✖️ Закрыть", "en": "✖️ Close"},
    "back": {"ru": "◀️ Назад", "en": "◀️ Back"},
    "russian": {"ru": "🇷🇺 Русский", "en": "🇷🇺 Русский"},
    "english": {"ru": "🇬🇧 English", "en": "🇬🇧 English"},
    "wl_confirm_yes": {"ru": "✅ Да, удалить", "en": "✅ Yes, remove"},
    "wl_confirm_no": {"ru": "✖ Отмена", "en": "✖ Cancel"},
}


def _t(key: str, lang: str) -> str:
    """Get translated label."""
    return _L.get(key, {}).get(lang, _L.get(key, {}).get("ru", key))


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------


def main_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Admin panel main menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t("whitelist", lang),
                    callback_data=f"adm_wl:{lang}",
                ),
                InlineKeyboardButton(
                    text=_t("rules", lang),
                    callback_data=f"adm_rules:{lang}:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("stickers", lang),
                    callback_data=f"adm_stk:{lang}:0",
                ),
                InlineKeyboardButton(
                    text=_t("defaults", lang),
                    callback_data=f"adm_defs:{lang}:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("statistics", lang),
                    callback_data=f"adm_stats:{lang}:24h",
                ),
                InlineKeyboardButton(
                    text=_t("costs", lang),
                    callback_data=f"adm_costs:{lang}:24h",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("health", lang),
                    callback_data=f"adm_health:{lang}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("notifications", lang),
                    callback_data=f"adm_notif:{lang}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("language", lang),
                    callback_data=f"adm_lang:{lang}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("close", lang),
                    callback_data=f"adm_close:{lang}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Language selector
# ---------------------------------------------------------------------------


def language_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Language selection menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t("russian", lang),
                    callback_data=f"adm_lang_set:{lang}:ru",
                ),
                InlineKeyboardButton(
                    text=_t("english", lang),
                    callback_data=f"adm_lang_set:{lang}:en",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("back", lang),
                    callback_data=f"adm_menu:{lang}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Whitelist menu
# ---------------------------------------------------------------------------


def whitelist_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Whitelist management menu."""
    chats_label = {"ru": "💬 Чаты", "en": "💬 Chats"}
    pending_label = {"ru": "⏳ Ожидают", "en": "⏳ Pending"}
    rejected_label = {"ru": "🚫 Отклонённые", "en": "🚫 Rejected"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=chats_label.get(lang, "Chats"),
                    callback_data=f"adm_wl_chats:{lang}:0",
                ),
                InlineKeyboardButton(
                    text=pending_label.get(lang, "Pending"),
                    callback_data=f"adm_wl_pending:{lang}:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=rejected_label.get(lang, "Rejected"),
                    callback_data=f"adm_wl_rejected:{lang}:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("back", lang),
                    callback_data=f"adm_menu:{lang}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Statistics period selector
# ---------------------------------------------------------------------------


def health_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Health status view with refresh and back."""
    refresh_label = {"ru": "🔄 Обновить", "en": "🔄 Refresh"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=refresh_label.get(lang, "Refresh"),
                    callback_data=f"adm_health:{lang}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("back", lang),
                    callback_data=f"adm_menu:{lang}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Statistics period selector
# ---------------------------------------------------------------------------


def stats_keyboard(lang: str, current_period: str = "24h") -> InlineKeyboardMarkup:
    """Statistics period selector."""
    periods = ["1h", "24h", "7d"]
    row: list[InlineKeyboardButton] = []
    for period in periods:
        label = f"[{period}]" if period == current_period else period
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"adm_stats:{lang}:{period}",
            )
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            row,
            [
                InlineKeyboardButton(
                    text=_t("back", lang),
                    callback_data=f"adm_menu:{lang}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Costs period selector
# ---------------------------------------------------------------------------


def costs_keyboard(lang: str, current_period: str = "24h") -> InlineKeyboardMarkup:
    """Costs period selector with optional verify button."""
    periods = ["1h", "24h", "7d"]
    row: list[InlineKeyboardButton] = []
    for period in periods:
        label = f"[{period}]" if period == current_period else period
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"adm_costs:{lang}:{period}",
            )
        )
    verify_label = {"ru": "🔍 Сверить (OpenAI)", "en": "🔍 Verify (OpenAI)"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            row,
            [
                InlineKeyboardButton(
                    text=verify_label.get(lang, "Verify (OpenAI)"),
                    callback_data=f"adm_costs_verify:{lang}:{current_period}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("back", lang),
                    callback_data=f"adm_menu:{lang}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Unauthorized access approve/reject (sent in admin notifications)
# ---------------------------------------------------------------------------


def access_keyboard(lang: str, attempt_id: int) -> InlineKeyboardMarkup:
    """Approve/reject buttons for unauthorized access notification."""
    approve_label = {"ru": "✅ Одобрить", "en": "✅ Approve"}
    reject_label = {"ru": "❌ Отклонить", "en": "❌ Reject"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=approve_label.get(lang, "Approve"),
                    callback_data=f"adm_approve:{lang}:{attempt_id}",
                ),
                InlineKeyboardButton(
                    text=reject_label.get(lang, "Reject"),
                    callback_data=f"adm_reject:{lang}:{attempt_id}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Whitelisted chats list (paginated)
# ---------------------------------------------------------------------------


def chats_list_keyboard(
    lang: str,
    chats: list[dict[str, object]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Paginated list of whitelisted chats with Remove buttons."""
    from html import escape

    rows: list[list[InlineKeyboardButton]] = []
    for chat in chats:
        title = str(chat.get("chat_title") or chat.get("chat_id", "?"))
        ctype = chat.get("chat_type", "")
        label = f"{escape(title)}"
        if ctype:
            label += f" ({ctype})"
        # Truncate label to fit Telegram button limits
        if len(label) > 40:
            label = label[:37] + "..."
        chat_id_int = cast(int, chat["chat_id"])
        url = build_chat_url(chat_id_int, str(ctype))
        if url:
            title_btn = InlineKeyboardButton(text=label, url=url)
        else:
            # Non-linkable chat (old-style group) — use noop to avoid
            # refreshing the message with identical content.
            title_btn = InlineKeyboardButton(text=label, callback_data="noop")
        rows.append(
            [
                title_btn,
                InlineKeyboardButton(
                    text="❌",
                    callback_data=f"adm_wl_rm_ask:{lang}:{chat_id_int}:{page}",
                ),
            ]
        )

    # Pagination row
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀",
                    callback_data=f"adm_wl_chats:{lang}:{page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data="noop",
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶",
                    callback_data=f"adm_wl_chats:{lang}:{page + 1}",
                )
            )
        rows.append(nav)

    # Back button
    rows.append(
        [
            InlineKeyboardButton(
                text=_t("back", lang),
                callback_data=f"adm_wl:{lang}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Whitelist remove — confirmation
# ---------------------------------------------------------------------------


def confirm_remove_chat_keyboard(
    lang: str,
    chat_id: int,
    page: int,
) -> InlineKeyboardMarkup:
    """Yes/Cancel row for confirming chat removal from whitelist."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t("wl_confirm_yes", lang),
                    callback_data=f"adm_wl_rm:{lang}:{chat_id}:{page}",
                ),
                InlineKeyboardButton(
                    text=_t("wl_confirm_no", lang),
                    callback_data=f"adm_wl_chats:{lang}:{page}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Pending access requests list (paginated)
# ---------------------------------------------------------------------------


def rejected_list_keyboard(
    lang: str,
    attempts: list[dict[str, object]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Paginated list of rejected attempts with Restore/Delete per item."""
    restore_label = {"ru": "🔄 Вернуть", "en": "🔄 Restore"}
    rows: list[list[InlineKeyboardButton]] = []
    for attempt in attempts:
        aid = attempt["id"]
        rows.append(
            [
                InlineKeyboardButton(
                    text=restore_label.get(lang, "Restore"),
                    callback_data=f"adm_wl_restore:{lang}:{aid}:{page}",
                ),
                InlineKeyboardButton(
                    text="🗑",
                    callback_data=f"adm_wl_del_ask:{lang}:{aid}:{page}",
                ),
            ]
        )

    # Pagination row
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀",
                    callback_data=f"adm_wl_rejected:{lang}:{page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data="noop",
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶",
                    callback_data=f"adm_wl_rejected:{lang}:{page + 1}",
                )
            )
        rows.append(nav)

    # Back button
    rows.append(
        [
            InlineKeyboardButton(
                text=_t("back", lang),
                callback_data=f"adm_wl:{lang}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_delete_attempt_keyboard(
    lang: str,
    attempt_id: int,
    page: int,
) -> InlineKeyboardMarkup:
    """Yes/Cancel row for confirming hard-delete of a rejected attempt."""
    yes_label = {"ru": "🗑 Да, удалить", "en": "🗑 Yes, delete"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=yes_label.get(lang, "Yes, delete"),
                    callback_data=f"adm_wl_del:{lang}:{attempt_id}:{page}",
                ),
                InlineKeyboardButton(
                    text=_t("wl_confirm_no", lang),
                    callback_data=f"adm_wl_rejected:{lang}:{page}",
                ),
            ],
        ]
    )


def pending_list_keyboard(
    lang: str,
    attempts: list[dict[str, object]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Paginated list of pending requests with Approve/Reject per item."""
    rows: list[list[InlineKeyboardButton]] = []
    for attempt in attempts:
        aid = attempt["id"]
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅",
                    callback_data=f"adm_wl_apr:{lang}:{aid}:{page}",
                ),
                InlineKeyboardButton(
                    text="❌",
                    callback_data=f"adm_wl_rej:{lang}:{aid}:{page}",
                ),
            ]
        )

    # Pagination row
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀",
                    callback_data=f"adm_wl_pending:{lang}:{page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data="noop",
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶",
                    callback_data=f"adm_wl_pending:{lang}:{page + 1}",
                )
            )
        rows.append(nav)

    # Back button
    rows.append(
        [
            InlineKeyboardButton(
                text=_t("back", lang),
                callback_data=f"adm_wl:{lang}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Notification status indicators (replace buttons after action)
# ---------------------------------------------------------------------------


def approved_notification_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Status indicator after approval (replaces approve/reject buttons)."""
    label = {"ru": "✅ Одобрено", "en": "✅ Approved"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label.get(lang, "✅ Approved"), callback_data="noop")],
        ]
    )


def rejected_notification_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Status indicator after rejection (replaces approve/reject buttons)."""
    label = {"ru": "❌ Отклонено", "en": "❌ Rejected"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label.get(lang, "❌ Rejected"), callback_data="noop")],
        ]
    )


# ---------------------------------------------------------------------------
# Notification settings menu
# ---------------------------------------------------------------------------

_STICKER_MODE_LABELS: dict[str, dict[str, str]] = {
    "off": {"ru": "выкл", "en": "off"},
    "on": {"ru": "вкл", "en": "on"},
    "detailed": {"ru": "подробно", "en": "detailed"},
}

_NOTIF_TYPE_LABELS: dict[str, dict[str, str]] = {
    "unauthorized": {"ru": "Неавторизованный доступ", "en": "Unauthorized access"},
    "jailbreak": {"ru": "Jailbreak", "en": "Jailbreak"},
    "blacklist": {"ru": "Blacklist", "en": "Blacklist"},
    "ai_fallback": {"ru": "AI Fallback", "en": "AI Fallback"},
}

_NOTIF_TYPE_ICONS: dict[str, str] = {
    "unauthorized": "\U0001f512",
    "jailbreak": "\u26a0\ufe0f",
    "blacklist": "\U0001f6ab",
    "ai_fallback": "\U0001f504",
}


def notifications_keyboard(lang: str, settings: dict[str, object]) -> InlineKeyboardMarkup:
    """Notification settings menu with toggles for each type."""
    rows: list[list[InlineKeyboardButton]] = []

    # Sticker notification mode (cycles: off → on → detailed)
    sticker_mode = str(settings.get("sticker", "on"))
    sticker_label = _STICKER_MODE_LABELS.get(sticker_mode, {}).get(lang, sticker_mode)
    sticker_text = {
        "ru": f"\U0001f5bc Стикеры: {sticker_label}",
        "en": f"\U0001f5bc Stickers: {sticker_label}",
    }
    rows.append(
        [
            InlineKeyboardButton(
                text=sticker_text.get(lang, sticker_text["en"]),
                callback_data=f"adm_nstk:{lang}",
            ),
        ]
    )

    # Boolean notification toggles
    for ntype in ("unauthorized", "jailbreak", "blacklist", "ai_fallback"):
        enabled = bool(settings.get(ntype, True))
        icon = _NOTIF_TYPE_ICONS.get(ntype, "")
        label = _NOTIF_TYPE_LABELS.get(ntype, {}).get(lang, ntype)
        status = "\u2705" if enabled else "\u26ab"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {label}: {status}",
                    callback_data=f"adm_ntog:{lang}:{ntype}",
                ),
            ]
        )

    # Back button
    rows.append(
        [
            InlineKeyboardButton(
                text=_t("back", lang),
                callback_data=f"adm_menu:{lang}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
