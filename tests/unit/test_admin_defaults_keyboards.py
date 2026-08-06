"""Tests for the "settings by default" screen's keyboard (C-1, ADR-0006)."""

from __future__ import annotations

from src.bot.keyboards.admin_defaults import _format_value, defaults_keyboard
from src.bot.settings_fields import FIELDS_BY_KEY, FieldGroup, group_label, new_fields


def _get_callbacks(keyboard):
    return [
        btn.callback_data for row in keyboard.inline_keyboard for btn in row if btn.callback_data
    ]


def _get_labels(keyboard):
    return [btn.text for row in keyboard.inline_keyboard for btn in row]


def _row_for(keyboard, callback_data: str):
    for row in keyboard.inline_keyboard:
        for btn in row:
            if btn.callback_data == callback_data:
                return btn
    return None


def _all_values(**overrides: object) -> dict[str, object]:
    """Build a full values dict (every new_fields() key) with overrides."""
    base: dict[str, object] = {
        "link_comments_enabled": False,
        "relevancy_gate_enabled": True,
        "sticker_reply_to_sticker_enabled": True,
        "sticker_reply_to_sticker_chance": 0.5,
        "image_comment_sticker_enabled": True,
        "image_comment_sticker_chance": 0.3,
        "rules_enabled": False,
        "rules_mode": "all",
        "kb_enabled": False,
        "reactions_enabled": False,
        "reactions_history_enabled": True,
    }
    base.update(overrides)
    return base


class TestFormatValue:
    def test_float_uses_general_format(self):
        field = FIELDS_BY_KEY["sticker_reply_to_sticker_chance"]
        assert _format_value(field, 0.5) == "0.5"

    def test_str_placeholder_when_empty(self):
        field = FIELDS_BY_KEY["rules_mode"]
        assert _format_value(field, "") == "—"

    def test_str_plain(self):
        field = FIELDS_BY_KEY["rules_mode"]
        assert _format_value(field, "all") == "all"


class TestDefaultsKeyboard:
    def test_only_new_fields_present(self):
        kb = defaults_keyboard("ru", _all_values())
        callbacks = _get_callbacks(kb)
        # Every new_fields() bool field gets its own toggle callback.
        for field in new_fields():
            if field.type.value == "bool":
                assert f"adm_defs_tgl:ru:{field.code}" in callbacks
        # Legacy fields (e.g. rag_enabled) must never appear on this screen.
        assert "adm_defs_tgl:ru:rag" not in callbacks

    def test_bool_field_gets_toggle_row(self):
        kb = defaults_keyboard("ru", _all_values(rules_enabled=True))
        btn = _row_for(kb, "adm_defs_tgl:ru:re")
        assert btn is not None
        assert "✅" in btn.text

    def test_kb_and_reactions_are_ordinary_toggles_not_links(self):
        """ADR-0006's C-1 consequence to Decision 2: unlike the chat panel
        (B-1), the defaults screen has no dedicated KB/Reactions sub-panel to
        link to, so these three fields toggle like any other bool field."""
        kb = defaults_keyboard("ru", _all_values(kb_enabled=True))
        btn = _row_for(kb, "adm_defs_tgl:ru:kb")
        assert btn is not None
        assert "✅" in btn.text
        assert not any(cb.startswith("adm_kb_menu:") for cb in _get_callbacks(kb))
        assert not any(cb.startswith("adm_react_menu:") for cb in _get_callbacks(kb))

    def test_non_bool_field_is_read_only_noop(self):
        kb = defaults_keyboard("ru", _all_values(rules_mode="strict"))
        labels = _get_labels(kb)
        assert any("strict" in label for label in labels)
        rows = [btn for row in kb.inline_keyboard for btn in row if "strict" in btn.text]
        assert all(btn.callback_data == "noop" for btn in rows)

    def test_group_headers_present(self):
        kb = defaults_keyboard("ru", _all_values())
        labels = _get_labels(kb)
        for group in (
            FieldGroup.MODULES,
            FieldGroup.STICKERS,
            FieldGroup.RULES,
            FieldGroup.KB,
            FieldGroup.REACTIONS,
        ):
            assert group_label(group, "ru") in labels
        # BEHAVIOR is all-legacy -- no new_fields() member, so no header here.
        assert group_label(FieldGroup.BEHAVIOR, "ru") not in labels

    def test_back_button_goes_to_main_menu(self):
        kb = defaults_keyboard("ru", _all_values())
        assert kb.inline_keyboard[-1][0].callback_data == "adm_menu:ru"

    def test_english_labels(self):
        kb = defaults_keyboard("en", _all_values())
        labels = _get_labels(kb)
        assert group_label(FieldGroup.MODULES, "en") in labels
