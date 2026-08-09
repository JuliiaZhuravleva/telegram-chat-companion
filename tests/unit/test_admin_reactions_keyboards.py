"""Tests for the Reactions admin sub-router's keyboards (R-D1)."""

from __future__ import annotations

from src.bot.keyboards.admin_reactions import (
    reactions_chat_picker_keyboard,
    reactions_menu_keyboard,
)
from src.bot.keyboards.nav import PANEL_ORIGIN

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
        # Short registry codes, not column names: the spelled-out payload was
        # 60 of Telegram's 64 callback bytes, leaving no room for the origin.
        assert f"adm_react_toggle:ru:{CHAT_ID}:rx" in callbacks
        assert f"adm_react_toggle:ru:{CHAT_ID}:rh" in callbacks
        assert all(len(cb.encode()) <= 64 for cb in callbacks)

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


class TestReactionsBackOrigin:
    """Same entry-point bug as the KB submenu (reported 2026-08-09)."""

    def _back_of(self, keyboard) -> str:
        return keyboard.inline_keyboard[-1][0].callback_data

    def test_from_panel_back_returns_to_that_chats_panel(self) -> None:
        kb = reactions_menu_keyboard(
            "ru",
            chat_id=CHAT_ID,
            reactions_enabled=True,
            reactions_history_enabled=True,
            origin=PANEL_ORIGIN,
        )
        assert self._back_of(kb) == f"adm_pnl_menu:ru:{CHAT_ID}"

    def test_from_own_picker_back_is_unchanged(self) -> None:
        kb = reactions_menu_keyboard(
            "ru", chat_id=CHAT_ID, reactions_enabled=True, reactions_history_enabled=True
        )
        assert self._back_of(kb) == "adm_react:ru:0"

    def test_origin_survives_both_toggles(self) -> None:
        kb = reactions_menu_keyboard(
            "ru",
            chat_id=CHAT_ID,
            reactions_enabled=True,
            reactions_history_enabled=True,
            origin=PANEL_ORIGIN,
        )
        toggles = [cb for cb in _get_callbacks(kb) if cb.startswith("adm_react_toggle:")]
        assert len(toggles) == 2
        assert all(cb.endswith(f":{PANEL_ORIGIN}") for cb in toggles), toggles

    def test_payload_fits_64_bytes_with_a_long_chat_id(self) -> None:
        kb = reactions_menu_keyboard(
            "ru",
            chat_id=-1009999000001234,
            reactions_enabled=True,
            reactions_history_enabled=True,
            origin=PANEL_ORIGIN,
        )
        for cb in _get_callbacks(kb):
            assert len(cb.encode()) <= 64, f"{cb} is {len(cb.encode())} bytes"
