"""Inline keyboards for the admin panel.

All callback_data follows the pattern: ``adm_{action}:{lang}:{params...}``
Language is embedded in callback_data for stateless operation.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ---------------------------------------------------------------------------
# i18n labels
# ---------------------------------------------------------------------------

_L: dict[str, dict[str, str]] = {
    "whitelist": {"ru": "Whitelist", "en": "Whitelist"},
    "stickers": {"ru": "Стикеры", "en": "Stickers"},
    "defaults": {"ru": "Настройки", "en": "Default Settings"},
    "statistics": {"ru": "Статистика", "en": "Statistics"},
    "language": {"ru": "Язык / Language", "en": "Language / Язык"},
    "close": {"ru": "Закрыть", "en": "Close"},
    "back": {"ru": "Назад", "en": "Back"},
    "russian": {"ru": "Русский", "en": "Русский"},
    "english": {"ru": "English", "en": "English"},
}


def _t(key: str, lang: str) -> str:
    """Get translated label."""
    return _L.get(key, {}).get(lang, _L.get(key, {}).get("ru", key))


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Admin panel main menu."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=_t("whitelist", lang),
                callback_data=f"adm_wl:{lang}",
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
    ])


# ---------------------------------------------------------------------------
# Language selector
# ---------------------------------------------------------------------------

def language_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Language selection menu."""
    return InlineKeyboardMarkup(inline_keyboard=[
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
    ])


# ---------------------------------------------------------------------------
# Whitelist menu
# ---------------------------------------------------------------------------

def whitelist_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Whitelist management menu."""
    chats_label = {"ru": "Чаты", "en": "Chats"}
    pending_label = {"ru": "Ожидают", "en": "Pending"}
    return InlineKeyboardMarkup(inline_keyboard=[
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
                text=_t("back", lang),
                callback_data=f"adm_menu:{lang}",
            ),
        ],
    ])


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
    return InlineKeyboardMarkup(inline_keyboard=[
        row,
        [
            InlineKeyboardButton(
                text=_t("back", lang),
                callback_data=f"adm_menu:{lang}",
            ),
        ],
    ])


# ---------------------------------------------------------------------------
# Unauthorized access approve/reject (sent in admin notifications)
# ---------------------------------------------------------------------------

def access_keyboard(
    lang: str, attempt_id: int
) -> InlineKeyboardMarkup:
    """Approve/reject buttons for unauthorized access notification."""
    approve_label = {"ru": "Одобрить", "en": "Approve"}
    reject_label = {"ru": "Отклонить", "en": "Reject"}
    return InlineKeyboardMarkup(inline_keyboard=[
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
    ])


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
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"adm_wl_chats:{lang}:{page}",
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=f"adm_wl_rm:{lang}:{chat['chat_id']}:{page}",
            ),
        ])

    # Pagination row
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="◀",
                callback_data=f"adm_wl_chats:{lang}:{page - 1}",
            ))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="noop",
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="▶",
                callback_data=f"adm_wl_chats:{lang}:{page + 1}",
            ))
        rows.append(nav)

    # Back button
    rows.append([
        InlineKeyboardButton(
            text=_t("back", lang),
            callback_data=f"adm_wl:{lang}",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Pending access requests list (paginated)
# ---------------------------------------------------------------------------

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
        rows.append([
            InlineKeyboardButton(
                text="✅",
                callback_data=f"adm_wl_apr:{lang}:{aid}:{page}",
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=f"adm_wl_rej:{lang}:{aid}:{page}",
            ),
        ])

    # Pagination row
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="◀",
                callback_data=f"adm_wl_pending:{lang}:{page - 1}",
            ))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="noop",
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="▶",
                callback_data=f"adm_wl_pending:{lang}:{page + 1}",
            ))
        rows.append(nav)

    # Back button
    rows.append([
        InlineKeyboardButton(
            text=_t("back", lang),
            callback_data=f"adm_wl:{lang}",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Notification status indicators (replace buttons after action)
# ---------------------------------------------------------------------------

def approved_notification_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Status indicator after approval (replaces approve/reject buttons)."""
    label = {"ru": "✅ Одобрено", "en": "✅ Approved"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label.get(lang, "✅ Approved"), callback_data="noop")],
    ])


def rejected_notification_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Status indicator after rejection (replaces approve/reject buttons)."""
    label = {"ru": "❌ Отклонено", "en": "❌ Rejected"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label.get(lang, "❌ Rejected"), callback_data="noop")],
    ])
