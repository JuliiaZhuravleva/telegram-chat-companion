"""Tests for the Reactions admin sub-router's keyboards (R-D1)."""

from __future__ import annotations

from src.bot.keyboards.admin_reactions import (
    reactions_chat_picker_keyboard,
    reactions_menu_keyboard,
)

CHAT_ID = -1001234567890


def _get_callbacks(keyboard):
    return [
        btn.callback_data for row in keyboard.inline_keyboard for btn in row if btn.callback_data
    ]


def _get_labels(keyboard):
    return [btn.text for row in keyboard.inline_keyboard for btn in row]


class TestReactionsChatPickerKeyboard:
    def test_lists_chats_with_menu_callback(self) -> None:
        chats = [{"chat_id": CHAT_ID, "chat_title": "Test Chat"}]
        kb = reactions_chat_picker_keyboard(chats, lang="ru", page=0, total=1)
        callbacks = _get_callbacks(kb)
        assert f"adm_react_menu:ru:{CHAT_ID}" in callbacks

    def test_falls_back_to_chat_id_when_no_title(self) -> None:
        chats = [{"chat_id": CHAT_ID, "chat_title": None}]
        kb = reactions_chat_picker_keyboard(chats, lang="ru", page=0, total=1)
        labels = _get_labels(kb)
        assert str(CHAT_ID) in labels

    def test_has_back_to_main_menu_button(self) -> None:
        kb = reactions_chat_picker_keyboard([], lang="en", page=0, total=0)
        callbacks = _get_callbacks(kb)
        assert "adm_menu:en" in callbacks

    def test_pagination_prev_next(self) -> None:
        chats = [{"chat_id": CHAT_ID, "chat_title": "Chat"}]
        kb = reactions_chat_picker_keyboard(chats, lang="ru", page=1, total=25, per_page=10)
        callbacks = _get_callbacks(kb)
        assert "adm_react:ru:0" in callbacks  # prev
        assert "adm_react:ru:2" in callbacks  # next

    def test_no_prev_on_first_page(self) -> None:
        kb = reactions_chat_picker_keyboard([], lang="ru", page=0, total=5, per_page=10)
        callbacks = _get_callbacks(kb)
        assert not any(c == "adm_react:ru:-1" for c in callbacks)


class TestReactionsMenuKeyboard:
    def test_shows_both_toggles_on(self) -> None:
        kb = reactions_menu_keyboard(
            "ru", chat_id=CHAT_ID, reactions_enabled=True, reactions_history_enabled=True
        )
        labels = _get_labels(kb)
        assert any("✅" in label for label in labels if "Модуль" in label)
        assert any("✅" in label for label in labels if "истории" in label)

    def test_shows_both_toggles_off(self) -> None:
        kb = reactions_menu_keyboard(
            "ru", chat_id=CHAT_ID, reactions_enabled=False, reactions_history_enabled=False
        )
        labels = _get_labels(kb)
        assert any("⚫" in label for label in labels if "Модуль" in label)
        assert any("⚫" in label for label in labels if "истории" in label)

    def test_toggle_callbacks_address_distinct_fields(self) -> None:
        kb = reactions_menu_keyboard(
            "ru", chat_id=CHAT_ID, reactions_enabled=True, reactions_history_enabled=True
        )
        callbacks = _get_callbacks(kb)
        assert f"adm_react_toggle:ru:{CHAT_ID}:reactions_enabled" in callbacks
        assert f"adm_react_toggle:ru:{CHAT_ID}:reactions_history_enabled" in callbacks

    def test_back_button_returns_to_picker(self) -> None:
        kb = reactions_menu_keyboard(
            "en", chat_id=CHAT_ID, reactions_enabled=True, reactions_history_enabled=True
        )
        callbacks = _get_callbacks(kb)
        assert "adm_react:en:0" in callbacks

    def test_adm_react_prefix_does_not_collide_with_menu_or_toggle(self) -> None:
        """CLAUDE.md gotcha: F.data.startswith("adm_react:") must not also
        match "adm_react_menu:..." / "adm_react_toggle:..." -- the trailing
        colon in the picker's own prefix disambiguates them."""
        kb = reactions_menu_keyboard(
            "ru", chat_id=CHAT_ID, reactions_enabled=True, reactions_history_enabled=True
        )
        callbacks = _get_callbacks(kb)
        assert not any(
            c.startswith("adm_react:") for c in callbacks if "toggle" in c or "menu" in c
        )
