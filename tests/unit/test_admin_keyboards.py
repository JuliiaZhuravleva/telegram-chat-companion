"""Tests for admin panel keyboards."""

from src.bot.keyboards.admin import (
    access_keyboard,
    approved_notification_keyboard,
    chats_list_keyboard,
    costs_keyboard,
    language_keyboard,
    main_menu_keyboard,
    notifications_keyboard,
    pending_list_keyboard,
    rejected_notification_keyboard,
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
        assert any("adm_costs:" in c for c in callbacks)
        assert any("adm_notif:" in c for c in callbacks)
        assert any("adm_lang:" in c for c in callbacks)
        assert any("adm_close:" in c for c in callbacks)

    def test_english_has_all_buttons(self):
        kb = main_menu_keyboard("en")
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:" in c for c in callbacks)
        assert any("adm_notif:" in c for c in callbacks)
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


class TestChatsListKeyboard:
    def test_has_remove_buttons(self):
        chats = [
            {"chat_id": -100, "chat_title": "Alpha", "chat_type": "group"},
            {"chat_id": -200, "chat_title": "Beta", "chat_type": "supergroup"},
        ]
        kb = chats_list_keyboard("ru", chats, page=0, total_pages=1)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_rm:ru:-100" in c for c in callbacks)
        assert any("adm_wl_rm:ru:-200" in c for c in callbacks)

    def test_has_back_button(self):
        kb = chats_list_keyboard("en", [], page=0, total_pages=1)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:en" in c for c in callbacks)

    def test_pagination_shown_for_multiple_pages(self):
        chats = [{"chat_id": -100, "chat_title": "A", "chat_type": "group"}]
        kb = chats_list_keyboard("ru", chats, page=0, total_pages=3)
        callbacks = _get_callbacks(kb)
        # Should have next page button
        assert any("adm_wl_chats:ru:1" in c for c in callbacks)
        # Page indicator
        labels = _get_labels(kb)
        assert "1/3" in labels

    def test_no_pagination_for_single_page(self):
        chats = [{"chat_id": -100, "chat_title": "A", "chat_type": "group"}]
        kb = chats_list_keyboard("ru", chats, page=0, total_pages=1)
        labels = _get_labels(kb)
        # No page indicator
        assert not any("/" in l and l[0].isdigit() for l in labels)


class TestPendingListKeyboard:
    def test_has_approve_reject_per_item(self):
        attempts = [
            {"id": 1, "chat_id": -100, "chat_title": "Test"},
            {"id": 2, "chat_id": -200, "chat_title": "Test2"},
        ]
        kb = pending_list_keyboard("ru", attempts, page=0, total_pages=1)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_apr:ru:1:0" in c for c in callbacks)
        assert any("adm_wl_rej:ru:1:0" in c for c in callbacks)
        assert any("adm_wl_apr:ru:2:0" in c for c in callbacks)
        assert any("adm_wl_rej:ru:2:0" in c for c in callbacks)

    def test_has_back_button(self):
        kb = pending_list_keyboard("en", [], page=0, total_pages=1)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:en" in c for c in callbacks)


class TestNotificationStatusKeyboards:
    def test_approved_keyboard_ru(self):
        kb = approved_notification_keyboard("ru")
        labels = _get_labels(kb)
        assert any("Одобрено" in l for l in labels)

    def test_approved_keyboard_en(self):
        kb = approved_notification_keyboard("en")
        labels = _get_labels(kb)
        assert any("Approved" in l for l in labels)

    def test_rejected_keyboard_ru(self):
        kb = rejected_notification_keyboard("ru")
        labels = _get_labels(kb)
        assert any("Отклонено" in l for l in labels)

    def test_rejected_keyboard_en(self):
        kb = rejected_notification_keyboard("en")
        labels = _get_labels(kb)
        assert any("Rejected" in l for l in labels)


class TestCostsKeyboard:
    def test_has_three_periods(self):
        kb = costs_keyboard("ru", "24h")
        callbacks = _get_callbacks(kb)
        assert any("adm_costs:ru:1h" in c for c in callbacks)
        assert any("adm_costs:ru:24h" in c for c in callbacks)
        assert any("adm_costs:ru:7d" in c for c in callbacks)

    def test_current_period_marked(self):
        kb = costs_keyboard("en", "7d")
        labels = _get_labels(kb)
        assert "[7d]" in labels

    def test_has_verify_button(self):
        kb = costs_keyboard("ru", "24h")
        callbacks = _get_callbacks(kb)
        assert any("adm_costs_verify:" in c for c in callbacks)

    def test_verify_button_english(self):
        kb = costs_keyboard("en", "24h")
        labels = _get_labels(kb)
        assert any("Verify" in label for label in labels)

    def test_has_back_button(self):
        kb = costs_keyboard("en", "1h")
        callbacks = _get_callbacks(kb)
        assert any("adm_menu:" in c for c in callbacks)


class TestNotificationsKeyboard:
    _ALL_ON = {
        "sticker": "on",
        "unauthorized": True,
        "jailbreak": True,
        "blacklist": True,
        "ai_fallback": True,
    }

    def test_has_all_notification_types(self):
        kb = notifications_keyboard("ru", self._ALL_ON)
        callbacks = _get_callbacks(kb)
        assert any("adm_nstk:" in c for c in callbacks)
        assert any("adm_ntog:" in c and "unauthorized" in c for c in callbacks)
        assert any("adm_ntog:" in c and "jailbreak" in c for c in callbacks)
        assert any("adm_ntog:" in c and "blacklist" in c for c in callbacks)
        assert any("adm_ntog:" in c and "ai_fallback" in c for c in callbacks)

    def test_has_back_button(self):
        kb = notifications_keyboard("en", self._ALL_ON)
        callbacks = _get_callbacks(kb)
        assert any("adm_menu:" in c for c in callbacks)

    def test_sticker_off_label_ru(self):
        settings = {**self._ALL_ON, "sticker": "off"}
        kb = notifications_keyboard("ru", settings)
        labels = _get_labels(kb)
        assert any("\u0432\u044b\u043a\u043b" in lab for lab in labels)

    def test_sticker_detailed_label_ru(self):
        settings = {**self._ALL_ON, "sticker": "detailed"}
        kb = notifications_keyboard("ru", settings)
        labels = _get_labels(kb)
        assert any("\u043f\u043e\u0434\u0440\u043e\u0431\u043d\u043e" in lab for lab in labels)

    def test_sticker_on_label_en(self):
        kb = notifications_keyboard("en", self._ALL_ON)
        labels = _get_labels(kb)
        assert any("on" in lab.lower() and "Sticker" in lab for lab in labels)

    def test_disabled_abuse_type_shows_black_circle(self):
        settings = {**self._ALL_ON, "unauthorized": False}
        kb = notifications_keyboard("en", settings)
        labels = _get_labels(kb)
        assert any("\u26ab" in lab and "Unauthorized" in lab for lab in labels)

    def test_enabled_abuse_type_shows_checkmark(self):
        kb = notifications_keyboard("en", self._ALL_ON)
        labels = _get_labels(kb)
        assert any("\u2705" in lab and "Unauthorized" in lab for lab in labels)

    def test_language_embedded_in_callbacks(self):
        kb = notifications_keyboard("en", self._ALL_ON)
        callbacks = _get_callbacks(kb)
        for cb in callbacks:
            if cb == "noop":
                continue
            parts = cb.split(":")
            assert parts[1] == "en", f"Expected :en: in {cb}"
