"""Inline keyboards for the chat settings panel (B-1, ADR-0006).

Callback data pattern: ``adm_pnl_{action}:{lang}:{params...}``

Per ADR-0006 Decision 4, the panel gets its own dedicated chat-picker
(``adm_pnl:`` -> ``adm_pnl_menu:``), mirroring the ``adm_kb:``/``adm_react:``
precedent rather than reusing ``adm_wl_chats:``. Per Decision 3, every
``FieldType.BOOL`` field with no existing dedicated UI gets a generic toggle
button (``adm_pnl_tgl:{lang}:{chat_id}:{code}``); per Decision 2, the three
KB/Reactions fields render as a status line + "open" button linking to their
existing sub-panels instead, so this module never writes those three columns.
Non-BOOL fields render read-only (F-1, deferred, owns generic FSM-driven
editing) -- except ``tolerance_level``, which gets its own small dedicated
edit flow (``adm_pnl_tol:``, ADR-0008 Decision 10) independent of F-1.

Per B-2 (ADR-0006 "Implementation notes", item 2), every ``new_fields()`` row
(the 11 nullable/no-DEFAULT columns) gets an "inherited from default" marker
suffix when its *raw* per-chat column is ``NULL`` -- the effective
(``ChatConfig``) value alone can't distinguish "explicitly set, happens to
match the default" from "inherited." The 13 legacy columns never get the
marker (they always materialize a value, see ``settings_fields.py``'s module
docstring) -- ``_is_inherited`` bakes that gate in so no call site can forget
it.

See docs/decisions/ADR-0006-chat-settings-panel-architecture.md.
"""

from __future__ import annotations

import math
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.settings_fields import FieldGroup, FieldSpec, FieldType, fields_by_group, group_label
from src.models.chat_config import ChatConfig

_BACK = {"ru": "◀️ Назад", "en": "◀️ Back"}
_HISTORY_SHORT = {"ru": "История", "en": "History"}
_INHERITED_MARK = {"ru": " · унаследовано", "en": " · inherited"}

# Button text is plain (Telegram doesn't apply parse_mode to captions), but
# very long joined/free-text values still make for an unreadable row.
_MAX_VALUE_LEN = 40


def tolerance_cancel_keyboard(lang: str, chat_id: int) -> InlineKeyboardMarkup:
    """Single-button escape hatch attached to the tolerance FSM prompt.

    Without it the ``awaiting_setting_value`` state had no exit: invalid
    input deliberately re-prompts (reject-not-clamp, ADR-0008 Decision 10),
    so an admin who changed their mind was stuck until they typed a valid
    float (2026-08-07 review). ``chat_id``/``lang`` ride along so the cancel
    handler can re-render the panel the prompt came from.
    """
    label = "✖️ Отмена" if lang == "ru" else "✖️ Cancel"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"adm_pnl_tolcancel:{lang}:{chat_id}",
                )
            ]
        ]
    )


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


def _is_inherited(field: FieldSpec, row: dict[str, Any] | None) -> bool:
    """True when ``field``'s row is honestly "inherited from default" (B-2).

    Only ``not field.legacy`` fields qualify: the 13 legacy migration-001
    columns materialize a SQL DEFAULT on ``ensure_exists()`` and never read
    back NULL for a chat the bot has already seen, so the marker would be a
    lie for them until C-2 (deferred tech debt) drops those defaults. The
    check is baked in here (not left to callers) precisely because it's easy
    to forget by analogy with the other 11 fields.
    """
    if field.legacy:
        return False
    raw = row.get(field.key) if row is not None else None
    return raw is None


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
    row: dict[str, Any] | None,
    kb_status: bool,
    reactions_status: tuple[bool, bool],
) -> InlineKeyboardMarkup:
    """Render every registry field, grouped, per ADR-0006 Decisions 2 and 3.

    ``config`` is the effective ``ChatConfigService.get_config()`` value,
    used for every field except the KB/Reactions link rows. Those two use
    ``kb_status``/``reactions_status`` instead -- the caller resolves them
    with a fresh direct read, bypassing the service's 60s cache. Their
    existing toggle handlers (admin_kb.py/admin_reactions.py) now
    self-invalidate on write (E-1), so this is defense in depth rather than
    a required workaround.

    ``row`` is the *raw* ``chat_settings_repo.get(chat_id)`` row (B-2) --
    needed alongside ``config`` because the effective value alone can't tell
    "explicitly set, happens to match the default" from "inherited." Used
    only to compute the inherited-from-default marker (``_is_inherited``);
    it never changes what value is displayed.
    """
    rows: list[list[InlineKeyboardButton]] = []

    for group, fields in fields_by_group():
        if group is FieldGroup.KB:
            field = fields[0]  # kb_enabled -- the only field in this group
            marker = _INHERITED_MARK[lang] if _is_inherited(field, row) else ""
            text = f"{field.label_for(lang)}: {_status(kb_status)}{marker}"
            rows.append(
                [
                    InlineKeyboardButton(text=text, callback_data=f"adm_kb_menu:{lang}:{chat_id}"),
                ]
            )
            continue

        if group is FieldGroup.REACTIONS:
            fields_by_key = {f.key: f for f in fields}
            enabled_marker = (
                _INHERITED_MARK[lang]
                if _is_inherited(fields_by_key["reactions_enabled"], row)
                else ""
            )
            history_marker = (
                _INHERITED_MARK[lang]
                if _is_inherited(fields_by_key["reactions_history_enabled"], row)
                else ""
            )
            enabled, history_enabled = reactions_status
            text = (
                f"{group_label(group, lang)}: {_status(enabled)}{enabled_marker} / "
                f"{_HISTORY_SHORT[lang]}: {_status(history_enabled)}{history_marker}"
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
            marker = _INHERITED_MARK[lang] if _is_inherited(field, row) else ""
            if field.type is FieldType.BOOL:
                text = f"{field.label_for(lang)}: {_status(bool(value))}{marker}"
                callback_data = f"adm_pnl_tgl:{lang}:{chat_id}:{field.code}"
            else:
                text = f"{field.label_for(lang)}: {_format_value(field, value)}{marker}"
                # ADR-0008 Decision 10: tolerance_level gets its own dedicated
                # FSM edit flow (admin_chat_panel.py handler), independent of
                # F-1's still-deferred generic non-BOOL editing -- every other
                # non-BOOL field stays read-only ("noop") until F-1 lands.
                callback_data = (
                    f"adm_pnl_tol:{lang}:{chat_id}" if field.key == "tolerance_level" else "noop"
                )
            rows.append([InlineKeyboardButton(text=text, callback_data=callback_data)])

    rows.append(
        [InlineKeyboardButton(text=_BACK[lang], callback_data=f"adm_pnl:{lang}:0")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
