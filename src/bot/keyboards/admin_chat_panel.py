"""Inline keyboards for the chat settings panel (B-1, ADR-0006).

Callback data pattern: ``adm_pnl_{action}:{lang}:{params...}``

Per ADR-0006 Decision 4, the panel gets its own dedicated chat-picker
(``adm_pnl:`` -> ``adm_pnl_menu:``), mirroring the ``adm_kb:``/``adm_react:``
precedent rather than reusing ``adm_wl_chats:``. Per Decision 3, every
``FieldType.BOOL`` field with no existing dedicated UI gets a generic toggle
button (``adm_pnl_tgl:{lang}:{chat_id}:{code}``); per Decision 2, the three
KB/Reactions fields render as a status line + "open" button linking to their
existing sub-panels instead, so this module never writes those three columns.
Non-BOOL fields render read-only (F-1, deferred, owns FSM-driven editing).

See docs/decisions/ADR-0006-chat-settings-panel-architecture.md.
"""

from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.settings_fields import FieldGroup, FieldSpec, FieldType, fields_by_group, group_label
from src.models.chat_config import ChatConfig

_BACK = {"ru": "◀️ Назад", "en": "◀️ Back"}
_HISTORY_SHORT = {"ru": "История", "en": "History"}

# Button text is plain (Telegram doesn't apply parse_mode to captions), but
# very long joined/free-text values still make for an unreadable row.
_MAX_VALUE_LEN = 40


def chat_panel_picker_keyboard(
    chats: list[dict[str, object]],
    *,
    lang: str,
    page: int,
    total: int,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of whitelisted chats to pick one to open the panel for."""
    rows: list[list[InlineKeyboardButton]] = []

    for chat in chats:
        chat_id = chat.get("chat_id")
        title = str(chat.get("chat_title") or chat_id)[:35]
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"adm_pnl_menu:{lang}:{chat_id}",
                ),
            ]
        )

    total_pages = max(1, math.ceil(total / per_page))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm_pnl:{lang}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm_pnl:{lang}:{page + 1}"))
    rows.append(nav)

    rows.append(
        [InlineKeyboardButton(text=_BACK[lang], callback_data=f"adm_menu:{lang}")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _status(value: bool) -> str:
    return "✅" if value else "⚫"


def _format_value(field: FieldSpec, value: object) -> str:
    """Read-only display for non-BOOL fields (F-1, deferred, owns editing)."""
    if field.type is FieldType.STR_LIST:
        items = [str(v) for v in value] if isinstance(value, list | tuple) else []
        text = ", ".join(items) if items else "—"
    elif field.type is FieldType.FLOAT and isinstance(value, int | float):
        text = f"{float(value):g}"
    elif field.type is FieldType.INT and isinstance(value, int):
        text = str(value)
    else:
        text = str(value).strip() if value else "—"

    if len(text) > _MAX_VALUE_LEN:
        text = text[: _MAX_VALUE_LEN - 1] + "…"
    return text


def chat_panel_keyboard(
    lang: str,
    *,
    chat_id: int,
    config: ChatConfig,
    kb_status: bool,
    reactions_status: tuple[bool, bool],
) -> InlineKeyboardMarkup:
    """Render every registry field, grouped, per ADR-0006 Decisions 2 and 3.

    ``config`` is the effective ``ChatConfigService.get_config()`` value,
    used for every field except the KB/Reactions link rows. Those two use
    ``kb_status``/``reactions_status`` instead -- the caller resolves them
    with a fresh direct read, bypassing the service's 60s cache, because
    their existing toggle handlers (admin_kb.py/admin_reactions.py) don't
    self-invalidate it yet (E-1, not landed): a cached read here could show
    stale state right after a tap on the dedicated submenu.
    """
    rows: list[list[InlineKeyboardButton]] = []

    for group, fields in fields_by_group():
        if group is FieldGroup.KB:
            field = fields[0]  # kb_enabled -- the only field in this group
            text = f"{field.label_for(lang)}: {_status(kb_status)}"
            rows.append(
                [
                    InlineKeyboardButton(text=text, callback_data=f"adm_kb_menu:{lang}:{chat_id}"),
                ]
            )
            continue

        if group is FieldGroup.REACTIONS:
            enabled, history_enabled = reactions_status
            text = (
                f"{group_label(group, lang)}: {_status(enabled)} / "
                f"{_HISTORY_SHORT[lang]}: {_status(history_enabled)}"
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=text, callback_data=f"adm_react_menu:{lang}:{chat_id}"
                    ),
                ]
            )
            continue

        rows.append([InlineKeyboardButton(text=group_label(group, lang), callback_data="noop")])
        for field in fields:
            value = getattr(config, field.key)
            if field.type is FieldType.BOOL:
                text = f"{field.label_for(lang)}: {_status(bool(value))}"
                callback_data = f"adm_pnl_tgl:{lang}:{chat_id}:{field.code}"
            else:
                text = f"{field.label_for(lang)}: {_format_value(field, value)}"
                callback_data = "noop"
            rows.append([InlineKeyboardButton(text=text, callback_data=callback_data)])

    rows.append(
        [InlineKeyboardButton(text=_BACK[lang], callback_data=f"adm_pnl:{lang}:0")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
