"""Inline keyboards for rules management in the admin panel.

Callback data follows abbreviated pattern to stay within Telegram's 64-byte limit:
    ``ar_{action}:{lang}:{params...}``
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ---------------------------------------------------------------------------
# i18n labels
# ---------------------------------------------------------------------------

_L: dict[str, dict[str, str]] = {
    "back": {"ru": "Назад", "en": "Back"},
    "add_rule": {"ru": "Добавить правило", "en": "Add rule"},
    "no_rules": {"ru": "Нет правил", "en": "No rules"},
    "select_chat": {"ru": "Выберите чат:", "en": "Select a chat:"},
    "select_type": {"ru": "Тип правила:", "en": "Rule type:"},
    "keyword_trigger": {"ru": "По ключевым словам", "en": "Keyword trigger"},
    "user_specific": {"ru": "По пользователю", "en": "User-specific"},
    "sticker_flood": {"ru": "Стикер-флуд", "en": "Sticker flood"},
    "spam_detect": {"ru": "Спам-детект", "en": "Spam detect"},
    "delete_confirm": {"ru": "Удалить?", "en": "Delete?"},
    "yes": {"ru": "Да", "en": "Yes"},
    "no": {"ru": "Нет", "en": "No"},
    "send_config": {
        "ru": "Отправьте JSON-конфиг правила:",
        "en": "Send rule JSON config:",
    },
}


def _t(key: str, lang: str) -> str:
    """Get translated label."""
    return _L.get(key, {}).get(lang, _L.get(key, {}).get("ru", key))


# ---------------------------------------------------------------------------
# Chat selection for rules (which chat to manage rules for)
# ---------------------------------------------------------------------------

def rules_chat_list_keyboard(
    lang: str,
    chats: list[dict[str, Any]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Paginated list of enabled chats to manage rules for."""
    rows: list[list[InlineKeyboardButton]] = []
    for chat in chats:
        title = str(chat.get("chat_title") or chat.get("chat_id", "?"))
        label = escape(title)
        if len(label) > 40:
            label = label[:37] + "..."
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"ar_list:{lang}:{chat['chat_id']}:0",
            ),
        ])

    # Pagination
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="◀",
                callback_data=f"adm_rules:{lang}:{page - 1}",
            ))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="noop",
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="▶",
                callback_data=f"adm_rules:{lang}:{page + 1}",
            ))
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(
            text=_t("back", lang),
            callback_data=f"adm_menu:{lang}",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Rules list for a specific chat (paginated)
# ---------------------------------------------------------------------------

def rules_list_keyboard(
    lang: str,
    rules: list[dict[str, Any]],
    page: int,
    total_pages: int,
    chat_id: int,
) -> InlineKeyboardMarkup:
    """Paginated list of rules for a chat with toggle/delete controls."""
    rows: list[list[InlineKeyboardButton]] = []
    for rule in rules:
        rid = rule["id"]
        cfg = rule.get("config", {})
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except (json.JSONDecodeError, ValueError):
                cfg = {}
        name = cfg.get("name", f"#{rid}")
        rtype = str(rule.get("rule_type", ""))[:10]
        enabled = rule.get("enabled", True)
        status_icon = "✅" if enabled else "⏸"

        label = f"{status_icon} {escape(name)} ({rtype})"
        if len(label) > 35:
            label = label[:32] + "..."

        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"ar_view:{lang}:{rid}",
            ),
            InlineKeyboardButton(
                text="🔄",
                callback_data=f"ar_tog:{lang}:{rid}:{chat_id}:{page}",
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"ar_del:{lang}:{rid}:{chat_id}:{page}",
            ),
        ])

    # Pagination
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="◀",
                callback_data=f"ar_list:{lang}:{chat_id}:{page - 1}",
            ))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="noop",
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="▶",
                callback_data=f"ar_list:{lang}:{chat_id}:{page + 1}",
            ))
        rows.append(nav)

    # Add rule + back
    rows.append([
        InlineKeyboardButton(
            text=_t("add_rule", lang),
            callback_data=f"ar_add:{lang}:{chat_id}",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text=_t("back", lang),
            callback_data=f"adm_rules:{lang}:0",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Rule type selection
# ---------------------------------------------------------------------------

def rule_type_keyboard(lang: str, chat_id: int) -> InlineKeyboardMarkup:
    """Select rule type when creating a new rule."""
    types = [
        ("keyword_trigger", _t("keyword_trigger", lang)),
        ("user_specific", _t("user_specific", lang)),
        ("sticker_flood", _t("sticker_flood", lang)),
        ("spam_detect", _t("spam_detect", lang)),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for rtype, label in types:
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"ar_type:{lang}:{chat_id}:{rtype}",
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text=_t("back", lang),
            callback_data=f"ar_list:{lang}:{chat_id}:0",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Rule detail view
# ---------------------------------------------------------------------------

def rule_detail_keyboard(
    lang: str, rule_id: int, chat_id: int
) -> InlineKeyboardMarkup:
    """View a single rule with toggle/delete/back."""
    toggle_label = {"ru": "Вкл/Выкл", "en": "Toggle"}
    delete_label = {"ru": "Удалить", "en": "Delete"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=toggle_label.get(lang, "Toggle"),
                callback_data=f"ar_tog:{lang}:{rule_id}:{chat_id}:0",
            ),
            InlineKeyboardButton(
                text=delete_label.get(lang, "Delete"),
                callback_data=f"ar_del:{lang}:{rule_id}:{chat_id}:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=_t("back", lang),
                callback_data=f"ar_list:{lang}:{chat_id}:0",
            ),
        ],
    ])
