"""Inline keyboard for the "settings by default" screen (C-1, ADR-0006).

Callback data pattern: ``adm_defs{_action}:{lang}:{params...}``

Per ADR-0006's C-1 consequence to Decision 2, the KB/Reactions default
fields (``kb_enabled``, ``reactions_enabled``, ``reactions_history_enabled``)
are **not** link rows here (unlike the per-chat panel, B-1) -- there is no
dedicated "defaults" sub-panel for KB/Reactions to link to at the global
layer, so they toggle exactly like the other 8 ``new_fields()`` bool fields,
through ``BotConfigRepository.set(f"default_{key}", ...)``.

Scoped to ``settings_fields.new_fields()`` (11 fields) only -- the 13 legacy
migration-001 columns still carry a SQL ``DEFAULT`` and showing/editing a
"default" for them here would lie (see ``settings_fields.py`` module
docstring; C-2, deferred). Non-BOOL fields (``rules_mode``, sticker/image
comment chances) render read-only, same F-1 deferral as the chat panel.

See docs/decisions/ADR-0006-chat-settings-panel-architecture.md.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.settings_fields import FieldSpec, FieldType, fields_by_group, group_label

_BACK = {"ru": "◀️ Назад", "en": "◀️ Back"}

# Button text is plain (Telegram doesn't apply parse_mode to captions), but
# very long free-text values still make for an unreadable row.
_MAX_VALUE_LEN = 40


def _status(value: bool) -> str:
    return "✅" if value else "⚫"


def _format_value(field: FieldSpec, value: object) -> str:
    """Read-only display for non-BOOL fields (F-1, deferred, owns editing)."""
    if field.type is FieldType.FLOAT and isinstance(value, int | float):
        text = f"{float(value):g}"
    elif field.type is FieldType.INT and isinstance(value, int):
        text = str(value)
    else:
        text = str(value).strip() if value else "—"

    if len(text) > _MAX_VALUE_LEN:
        text = text[: _MAX_VALUE_LEN - 1] + "…"
    return text


def defaults_keyboard(lang: str, values: dict[str, object]) -> InlineKeyboardMarkup:
    """Render every ``new_fields()`` field, grouped (ADR-0006 C-1 notes).

    ``values`` maps ``FieldSpec.key`` -> current default value (the caller
    resolves "explicit ``default_<key>`` row" vs. "no override yet" fallback
    -- see ``render_defaults_panel`` in ``admin_defaults.py``).
    """
    rows: list[list[InlineKeyboardButton]] = []

    for group, fields in fields_by_group():
        new_group_fields = tuple(field for field in fields if not field.legacy)
        if not new_group_fields:
            continue

        rows.append([InlineKeyboardButton(text=group_label(group, lang), callback_data="noop")])
        for field in new_group_fields:
            value = values[field.key]
            if field.type is FieldType.BOOL:
                text = f"{field.label_for(lang)}: {_status(bool(value))}"
                callback_data = f"adm_defs_tgl:{lang}:{field.code}"
            else:
                text = f"{field.label_for(lang)}: {_format_value(field, value)}"
                callback_data = "noop"
            rows.append([InlineKeyboardButton(text=text, callback_data=callback_data)])

    rows.append(
        [InlineKeyboardButton(text=_BACK[lang], callback_data=f"adm_menu:{lang}")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
