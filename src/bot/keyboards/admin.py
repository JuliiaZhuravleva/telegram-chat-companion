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
    "defaults": {"ru": "🌍 Глобальные настройки", "en": "🌍 Global settings"},
    "statistics": {"ru": "📊 Статистика", "en": "📊 Statistics"},
    "language": {"ru": "🌐 Язык / Language", "en": "🌐 Language / Язык"},
    "costs": {"ru": "💰 Расходы", "en": "💰 Costs"},
    "health": {"ru": "💚 Здоровье", "en": "💚 Health"},
    "notifications": {"ru": "🔔 Уведомления", "en": "🔔 Notifications"},
    "kb": {"ru": "📚 База знаний", "en": "📚 Knowledge Base"},
    "reactions": {"ru": "😀 Реакции", "en": "😀 Reactions"},
    "chat_panel": {"ru": "⚙️ Настройки чата", "en": "⚙️ Chat settings"},
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
                    callback_data=f"adm_stk_sets:{lang}:0",
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
                    text=_t("kb", lang),
                    callback_data=f"adm_kb:{lang}:0",
                ),
                InlineKeyboardButton(
                    text=_t("reactions", lang),
                    callback_data=f"adm_react:{lang}:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("chat_panel", lang),
                    callback_data=f"adm_pnl:{lang}:0",
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
# Shared: numbered list-item buttons
# ---------------------------------------------------------------------------
#
# ``pending_list_keyboard``, ``rejected_list_keyboard``, and ``chats_list_keyboard``
# all render one row of action buttons per list item, on a page whose message body
# numbers items via ``enumerate(items, start=start_index + 1)`` (``start_index`` is
# ``page * _PER_PAGE`` — see ``_render_wl_pending`` / ``_render_wl_rejected`` /
# ``_render_wl_chats`` in ``handlers/admin.py``). Every action button in all three
# keyboards must go through ``_numbered_button`` below so the "button number ==
# body item number" rule lives in exactly one place instead of drifting per list.


def _numbered_button(index: int, label: str, callback_data: str) -> InlineKeyboardButton:
    """Build an inline button whose visible label is prefixed with its item number.

    ``index`` is the 1-based display number (i.e. ``enumerate(items,
    start=start_index + 1)``), matching the number printed for the same item in
    the message body above the keyboard.
    """
    return InlineKeyboardButton(text=f"{index} {label}", callback_data=callback_data)


# ---------------------------------------------------------------------------
# Whitelisted chats list (paginated)
# ---------------------------------------------------------------------------


def chats_list_keyboard(
    lang: str,
    chats: list[dict[str, object]],
    page: int,
    total_pages: int,
    start_index: int,
) -> InlineKeyboardMarkup:
    """Paginated list of whitelisted chats with Remove buttons.

    ``start_index`` is the 0-based offset of the first item on this page
    (``page * per_page``), so the number on each Remove button lines up with
    the numbering rendered in the message body (see ``_numbered_button``).
    """
    from html import escape

    rows: list[list[InlineKeyboardButton]] = []
    for i, chat in enumerate(chats, start=start_index + 1):
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
                _numbered_button(i, "❌", f"adm_wl_rm_ask:{lang}:{chat_id_int}:{page}"),
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
    start_index: int,
) -> InlineKeyboardMarkup:
    """Paginated list of rejected attempts with Restore/Delete per item.

    ``start_index`` is the 0-based offset of the first item on this page
    (``page * per_page``), so the number on each Restore/Delete button lines
    up with the numbering rendered in the message body (see
    ``_numbered_button``).
    """
    restore_label = {"ru": "🔄 Вернуть", "en": "🔄 Restore"}
    rows: list[list[InlineKeyboardButton]] = []
    for i, attempt in enumerate(attempts, start=start_index + 1):
        aid = attempt["id"]
        rows.append(
            [
                _numbered_button(
                    i,
                    restore_label.get(lang, "Restore"),
                    f"adm_wl_restore:{lang}:{aid}:{page}",
                ),
                _numbered_button(i, "🗑", f"adm_wl_del_ask:{lang}:{aid}:{page}"),
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
    start_index: int,
) -> InlineKeyboardMarkup:
    """Paginated list of pending requests with Approve/Reject per item.

    ``start_index`` is the 0-based offset of the first item on this page
    (i.e. ``page * per_page``), so the numbers on the Approve/Reject buttons
    line up with the numbering rendered in the message body
    (``enumerate(attempts, start=offset + 1)`` in ``handlers/admin.py``; see
    ``_numbered_button``).
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i, attempt in enumerate(attempts, start=start_index + 1):
        aid = attempt["id"]
        rows.append(
            [
                _numbered_button(i, "✅", f"adm_wl_apr:{lang}:{aid}:{page}"),
                _numbered_button(i, "❌", f"adm_wl_rej:{lang}:{aid}:{page}"),
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


def wl_approved_keyboard(lang: str, chat_id: int, page: int) -> InlineKeyboardMarkup:
    """Post-approve screen for the pending list (D-1): settings link + back.

    ``handle_wl_approve`` (``adm_wl_apr:``) shows this instead of immediately
    re-rendering the pending list: the just-approved attempt no longer
    matches ``get_pending_attempts_page`` (status flipped), so it would
    simply vanish from a re-render -- there is no row left to attach a
    "⚙️ Chat settings" button next to. ``page`` routes "back" to the pending
    list at the page the admin came from.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t("chat_panel", lang),
                    callback_data=f"adm_pnl_menu:{lang}:{chat_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t("back", lang),
                    callback_data=f"adm_wl_pending:{lang}:{page}",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Notification status indicators (replace buttons after action)
# ---------------------------------------------------------------------------


def approved_notification_keyboard(lang: str, chat_id: int) -> InlineKeyboardMarkup:
    """Status indicator + settings-panel link after approval (D-1).

    Replaces the approve/reject buttons on the DM notification. ``chat_id``
    is already known at approve time, so the settings button links straight
    to the per-chat panel (ADR-0006 Decision 4) -- no picker step needed.
    """
    label = {"ru": "✅ Одобрено", "en": "✅ Approved"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label.get(lang, "✅ Approved"), callback_data="noop"),
                InlineKeyboardButton(
                    text=_t("chat_panel", lang),
                    callback_data=f"adm_pnl_menu:{lang}:{chat_id}",
                ),
            ],
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
