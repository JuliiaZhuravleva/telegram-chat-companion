"""Tests for admin panel keyboards."""

from src.bot.keyboards.admin import (
    access_keyboard,
    language_keyboard,
    main_menu_keyboard,
    stats_keyboard,
    whitelist_menu_keyboard,
)


def _get_callbacks(keyboard):
    """Extract all callback_data strings from keyboard."""
    return [
        btn.callback_data
        for row in keyboard.inline_keyboard
        for btn in row
        if btn.callback_data
    ]


def _get_labels(keyboard):
    """Extract all button labels from keyboard."""
    return [btn.text for row in keyboard.inline_keyboard for btn in row]


class TestMainMenuKeyboard:
    def test_russian_has_all_buttons(self):
        kb = main_menu_keyboard("ru")
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:" in c for c in callbacks)
        assert any("adm_stk:" in c for c in callbacks)
        assert any("adm_defs:" in c for c in callbacks)
        assert any("adm_stats:" in c for c in callbacks)
        assert any("adm_lang:" in c for c in callbacks)
        assert any("adm_close:" in c for c in callbacks)

    def test_english_has_all_buttons(self):
        kb = main_menu_keyboard("en")
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:" in c for c in callbacks)
        assert any("adm_close:" in c for c in callbacks)

    def test_language_embedded_in_callbacks(self):
        kb = main_menu_keyboard("en")
        callbacks = _get_callbacks(kb)
        for cb in callbacks:
            # All callbacks should contain :en:
            parts = cb.split(":")
            assert parts[1] == "en", f"Expected :en: in {cb}"

    def test_language_ru_embedded(self):
        kb = main_menu_keyboard("ru")
        callbacks = _get_callbacks(kb)
        for cb in callbacks:
            parts = cb.split(":")
            assert parts[1] == "ru"


class TestLanguageKeyboard:
    def test_has_russian_and_english_buttons(self):
        kb = language_keyboard("ru")
        callbacks = _get_callbacks(kb)
        assert any("adm_lang_set:" in c and ":ru" in c for c in callbacks)
        assert any("adm_lang_set:" in c and ":en" in c for c in callbacks)

    def test_has_back_button(self):
        kb = language_keyboard("en")
        callbacks = _get_callbacks(kb)
        assert any("adm_menu:" in c for c in callbacks)


class TestStatsKeyboard:
    def test_has_three_periods(self):
        kb = stats_keyboard("ru", "24h")
        callbacks = _get_callbacks(kb)
        assert any(":1h" in c for c in callbacks)
        assert any(":24h" in c for c in callbacks)
        assert any(":7d" in c for c in callbacks)

    def test_current_period_marked(self):
        kb = stats_keyboard("ru", "24h")
        labels = _get_labels(kb)
        assert "[24h]" in labels

    def test_has_back_button(self):
        kb = stats_keyboard("en", "1h")
        callbacks = _get_callbacks(kb)
        assert any("adm_menu:" in c for c in callbacks)


class TestWhitelistMenuKeyboard:
    def test_has_chats_and_pending(self):
        kb = whitelist_menu_keyboard("ru")
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_chats:" in c for c in callbacks)
        assert any("adm_wl_pending:" in c for c in callbacks)

    def test_has_back_button(self):
        kb = whitelist_menu_keyboard("en")
        callbacks = _get_callbacks(kb)
        assert any("adm_menu:" in c for c in callbacks)


class TestAccessKeyboard:
    def test_has_approve_and_reject(self):
        kb = access_keyboard("ru", attempt_id=42)
        callbacks = _get_callbacks(kb)
        assert "adm_approve:ru:42" in callbacks
        assert "adm_reject:ru:42" in callbacks

    def test_english_labels(self):
        kb = access_keyboard("en", attempt_id=1)
        labels = _get_labels(kb)
        assert "Approve" in labels
        assert "Reject" in labels
