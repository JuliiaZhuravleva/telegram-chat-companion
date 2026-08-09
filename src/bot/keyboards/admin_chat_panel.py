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

Per B-2 (ADR-0010, grouped navigation), the panel is two screens instead of
one flat list: ``chat_panel_root_keyboard()`` renders the section list (4
group buttons + the unchanged KB/Reactions link rows), and
``chat_panel_group_keyboard()`` renders one field-owning group's fields --
the same per-field row logic the old single ``chat_panel_keyboard()`` used
(toggle / read-only / ``tolerance_level``'s dedicated edit flow), just scoped
to one group instead of all four.

See docs/decisions/ADR-0006-chat-settings-panel-architecture.md and
docs/decisions/ADR-0010-chat-panel-grouped-navigation.md.
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

# B-3: the per-group status shown on the root screen's section buttons.
# Two shapes, because the groups aren't alike: a group made of toggles says
# how many are on, a group with no toggles at all (behavior) can only honestly
# say how much is in it. Counting overrides instead was rejected — the
# inherited marker is only truthful for non-legacy fields (see _is_inherited),
# so an "N overridden" number would quietly lie for legacy-heavy groups.
_GROUP_TOGGLES_ON = {"ru": "вкл {on}/{total}", "en": "on {on}/{total}"}
_GROUP_SETTINGS_COUNT = {"ru": "{n} {word}", "en": "{n} {word}"}
_RU_SETTINGS_FORMS = ("настройка", "настройки", "настроек")
_EN_SETTINGS_FORMS = ("setting", "settings")

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
        # C-1: message count is not an id (no Telegram bare-number autolink
        # concern) -- shown so the activity-sorted order doesn't look
        # arbitrary. Key is absent for callers that don't opt into
        # activity sorting, so this stays a no-op suffix for them.
        count = chat.get("message_count_24h")
        text = f"{title} · {count}" if count is not None else title
        rows.append(
            [
                InlineKeyboardButton(
                    text=text,
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


def chat_panel_candidates_keyboard(
    chats: list[dict[str, object]], *, lang: str
) -> InlineKeyboardMarkup:
    """Disambiguation list for the D-1 shortcut's title search.

    Several whitelisted chats matched the admin's query text, so list them
    to tap instead of guessing which one was meant. No pagination (the
    caller caps the search at a small limit) and no back row -- this
    keyboard rides a fresh DM message the shortcut command sent, not a
    screen with a "previous" state to return to. Row rendering (title,
    falling back to the bare chat id when untitled) mirrors
    ``chat_panel_picker_keyboard``'s.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for chat in chats:
        chat_id = chat.get("chat_id")
        title = str(chat.get("chat_title") or chat_id)[:35]
        rows.append(
            [InlineKeyboardButton(text=title, callback_data=f"adm_pnl_menu:{lang}:{chat_id}")]
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


def _settings_word(n: int, lang: str) -> str:
    """Pluralize "settings" for the group-size summary (B-3)."""
    if lang != "ru":
        one, many = _EN_SETTINGS_FORMS
        return one if n == 1 else many
    one, few, many = _RU_SETTINGS_FORMS
    if n % 100 // 10 == 1:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _group_summary(fields: tuple[FieldSpec, ...], config: ChatConfig, lang: str) -> str:
    """Short status for one section button on the root screen (B-3).

    ADR-0010 Decision 3 left the formula open; this is it. Groups holding
    toggles report how many are on, which is the thing an admin scans for.
    Groups with no toggles report their size instead of an invented status.
    """
    toggles = [f for f in fields if f.type is FieldType.BOOL]
    if toggles:
        on = sum(1 for f in toggles if bool(getattr(config, f.key)))
        return _GROUP_TOGGLES_ON[lang].format(on=on, total=len(toggles))
    return _GROUP_SETTINGS_COUNT[lang].format(n=len(fields), word=_settings_word(len(fields), lang))


def chat_panel_root_keyboard(
    lang: str,
    *,
    chat_id: int,
    row: dict[str, Any] | None,
    config: ChatConfig,
    kb_status: bool,
    reactions_status: tuple[bool, bool],
) -> InlineKeyboardMarkup:
    """Render the root section-list screen (ADR-0010 Decisions 1 and 3).

    Four tappable group buttons (behavior/modules/stickers/rules, each
    opening ``chat_panel_group_keyboard()``'s screen) replace the old flat
    field list. KB/Reactions keep ADR-0006 Decision 2's link-out rows
    unchanged -- they stay one tap away, not a 5th/6th group screen
    (ADR-0010 Decision 3).

    ``kb_status``/``reactions_status`` are fresh direct reads (see
    ``render_chat_panel``'s ``_fresh_effective``), same as the pre-B-2
    behavior. ``row`` is the *raw* ``chat_settings_repo.get(chat_id)`` row,
    used only for the inherited-from-default marker on the KB/Reactions link
    rows (``_is_inherited``) -- the 4 group buttons carry no per-field state
    and need no marker.

    ``config`` is the effective ``ChatConfigService.get_config()`` value,
    used only for the per-group status suffix (B-3, ``_group_summary``).
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

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{group_label(group, lang)} · {_group_summary(fields, config, lang)}",
                    callback_data=f"adm_pnl_grp:{lang}:{chat_id}:{group.value}",
                )
            ]
        )

    rows.append(
        [InlineKeyboardButton(text=_BACK[lang], callback_data=f"adm_pnl:{lang}:0")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_panel_group_keyboard(
    lang: str,
    *,
    chat_id: int,
    group: FieldGroup,
    config: ChatConfig,
    row: dict[str, Any] | None,
) -> InlineKeyboardMarkup:
    """Render one field-owning group's screen (ADR-0010 Decision 4).

    Lifted, not redesigned, from the pre-B-2 flat ``chat_panel_keyboard()``
    loop body: only the scope (one group's fields instead of all four) and
    the container changed -- toggle / read-only / ``tolerance_level``'s
    dedicated edit-flow callback shapes and the inherited marker are
    unchanged. Only called for the 4 field-owning groups (behavior/modules/
    stickers/rules); KB/Reactions never reach here (ADR-0010 Decision 3).

    ``config`` is the effective ``ChatConfigService.get_config()`` value.
    ``row`` is the *raw* per-chat row, used only for ``_is_inherited``.
    """
    rows: list[list[InlineKeyboardButton]] = []

    fields = next((group_fields for g, group_fields in fields_by_group() if g is group), ())
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
        [InlineKeyboardButton(text=_BACK[lang], callback_data=f"adm_pnl_menu:{lang}:{chat_id}")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
