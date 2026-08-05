"""Tests for admin panel keyboards."""

from unittest.mock import patch

import src.bot.keyboards.admin as admin_kb
from src.bot.keyboards.admin import (
    access_keyboard,
    approved_notification_keyboard,
    chats_list_keyboard,
    confirm_delete_attempt_keyboard,
    confirm_remove_chat_keyboard,
    costs_keyboard,
    language_keyboard,
    main_menu_keyboard,
    notifications_keyboard,
    pending_list_keyboard,
    rejected_list_keyboard,
    rejected_notification_keyboard,
    stats_keyboard,
    whitelist_menu_keyboard,
)


def _get_urls(keyboard):
    """Extract all button URLs from keyboard."""
    return [btn.url for row in keyboard.inline_keyboard for btn in row if btn.url]


def _get_callbacks(keyboard):
    """Extract all callback_data strings from keyboard."""
    return [
        btn.callback_data for row in keyboard.inline_keyboard for btn in row if btn.callback_data
    ]


def _get_labels(keyboard):
    """Extract all button labels from keyboard."""
    return [btn.text for row in keyboard.inline_keyboard for btn in row]


class TestMainMenuKeyboard:
    def test_russian_has_all_buttons(self):
        kb = main_menu_keyboard("ru")
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:" in c for c in callbacks)
        assert any("adm_stk_sets:" in c for c in callbacks)
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

    def test_has_rejected_button(self):
        kb = whitelist_menu_keyboard("ru")
        callbacks = _get_callbacks(kb)
        assert any(c.startswith("adm_wl_rejected:") for c in callbacks)

    def test_rejected_label_localized(self):
        labels_ru = _get_labels(whitelist_menu_keyboard("ru"))
        labels_en = _get_labels(whitelist_menu_keyboard("en"))
        assert any("Отклонённые" in lab for lab in labels_ru)
        assert any("Rejected" in lab for lab in labels_en)

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
        assert any("Approve" in lab for lab in labels)
        assert any("Reject" in lab for lab in labels)


class TestChatsListKeyboard:
    def test_has_remove_buttons(self):
        chats = [
            {"chat_id": -100, "chat_title": "Alpha", "chat_type": "group"},
            {"chat_id": -200, "chat_title": "Beta", "chat_type": "supergroup"},
        ]
        kb = chats_list_keyboard("ru", chats, page=0, total_pages=1, start_index=0)
        callbacks = _get_callbacks(kb)
        # ❌ now routes through the confirmation step
        assert any("adm_wl_rm_ask:ru:-100:0" in c for c in callbacks)
        assert any("adm_wl_rm_ask:ru:-200:0" in c for c in callbacks)
        # Must NOT directly delete from the list row anymore
        assert not any(c.startswith("adm_wl_rm:") for c in callbacks)

    def test_has_back_button(self):
        kb = chats_list_keyboard("en", [], page=0, total_pages=1, start_index=0)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:en" in c for c in callbacks)

    def test_pagination_shown_for_multiple_pages(self):
        chats = [{"chat_id": -100, "chat_title": "A", "chat_type": "group"}]
        kb = chats_list_keyboard("ru", chats, page=0, total_pages=3, start_index=0)
        callbacks = _get_callbacks(kb)
        # Should have next page button
        assert any("adm_wl_chats:ru:1" in c for c in callbacks)
        # Page indicator
        labels = _get_labels(kb)
        assert "1/3" in labels

    def test_no_pagination_for_single_page(self):
        chats = [{"chat_id": -100, "chat_title": "A", "chat_type": "group"}]
        kb = chats_list_keyboard("ru", chats, page=0, total_pages=1, start_index=0)
        labels = _get_labels(kb)
        # No page indicator
        assert not any("/" in label and label[0].isdigit() for label in labels)

    def test_supergroup_title_is_url_button(self):
        chats = [
            {
                "chat_id": -1001234567890,
                "chat_title": "Test Supergroup",
                "chat_type": "supergroup",
            },
        ]
        kb = chats_list_keyboard("ru", chats, page=0, total_pages=1, start_index=0)
        urls = _get_urls(kb)
        # Internal id: strip sign + "100" prefix
        assert "https://t.me/c/1234567890" in urls

    def test_private_chat_title_is_tg_user_link(self):
        chats = [
            {
                "chat_id": 1234567890,
                "chat_title": "Alice",
                "chat_type": "private",
            },
        ]
        kb = chats_list_keyboard("en", chats, page=0, total_pages=1, start_index=0)
        urls = _get_urls(kb)
        assert "tg://user?id=1234567890" in urls

    def test_old_group_title_stays_noop_callback(self):
        # Legacy groups (chat_type="group") have no shareable link —
        # title button becomes a pure noop to avoid triggering a
        # "message is not modified" re-render on tap.
        chats = [
            {"chat_id": -100, "chat_title": "Legacy Group", "chat_type": "group"},
        ]
        kb = chats_list_keyboard("ru", chats, page=2, total_pages=5, start_index=0)
        urls = _get_urls(kb)
        assert not any("-100" in (u or "") for u in urls)
        # Find the row for this chat (first data row) and check its title button
        title_btn = kb.inline_keyboard[0][0]
        assert title_btn.url is None
        assert title_btn.callback_data == "noop"

    def test_button_number_starts_at_one_on_first_page(self):
        """With start_index=0 (page 1), numbering starts at 1."""
        chats = [
            {"chat_id": -100, "chat_title": "Alpha", "chat_type": "group"},
            {"chat_id": -200, "chat_title": "Beta", "chat_type": "supergroup"},
        ]
        kb = chats_list_keyboard("ru", chats, page=0, total_pages=1, start_index=0)
        labels = _get_labels(kb)
        assert "1 ❌" in labels
        assert "2 ❌" in labels

    def test_button_number_matches_body_numbering_on_third_page(self):
        """Remove-button numbers must match the message-body numbering.

        The body numbers entries via ``enumerate(chats, start=page * _PER_PAGE + 1)``
        (see ``handlers/admin.py`` ``_render_wl_chats``). With ``_PER_PAGE=5`` and
        ``page=2`` (the 3rd page, 0-based), the first item on the page is #11.
        """
        per_page = 5
        page = 2
        start_index = page * per_page
        chats = [
            {"chat_id": -100, "chat_title": "Alpha", "chat_type": "group"},
            {"chat_id": -200, "chat_title": "Beta", "chat_type": "supergroup"},
        ]
        kb = chats_list_keyboard(
            "ru",
            chats,
            page=page,
            total_pages=5,
            start_index=start_index,
        )
        labels = _get_labels(kb)
        assert "11 ❌" in labels
        assert "12 ❌" in labels
        # Ensure the callback data still ties back to the right chat/page.
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_rm_ask:ru:-100:2" in c for c in callbacks)
        assert any("adm_wl_rm_ask:ru:-200:2" in c for c in callbacks)


class TestConfirmRemoveChatKeyboard:
    def test_has_yes_no_buttons(self):
        kb = confirm_remove_chat_keyboard("ru", chat_id=-100, page=0)
        callbacks = _get_callbacks(kb)
        # Yes → actual delete callback
        assert "adm_wl_rm:ru:-100:0" in callbacks
        # No → back to list at same page
        assert "adm_wl_chats:ru:0" in callbacks

    def test_preserves_page_on_cancel(self):
        kb = confirm_remove_chat_keyboard("en", chat_id=-200, page=3)
        callbacks = _get_callbacks(kb)
        assert "adm_wl_chats:en:3" in callbacks
        assert "adm_wl_rm:en:-200:3" in callbacks

    def test_labels_per_language(self):
        ru_labels = _get_labels(confirm_remove_chat_keyboard("ru", -1, 0))
        en_labels = _get_labels(confirm_remove_chat_keyboard("en", -1, 0))
        assert any("Да" in lab for lab in ru_labels)
        assert any("Yes" in lab for lab in en_labels)


class TestRejectedListKeyboard:
    def test_has_restore_and_delete_per_item(self):
        attempts = [
            {"id": 1, "chat_id": -100},
            {"id": 2, "chat_id": -200},
        ]
        kb = rejected_list_keyboard("ru", attempts, page=0, total_pages=1, start_index=0)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_restore:ru:1:0" in c for c in callbacks)
        assert any("adm_wl_del_ask:ru:1:0" in c for c in callbacks)
        assert any("adm_wl_restore:ru:2:0" in c for c in callbacks)
        assert any("adm_wl_del_ask:ru:2:0" in c for c in callbacks)
        # Must NOT use the direct-delete callback here
        assert not any(c.startswith("adm_wl_del:") for c in callbacks)

    def test_pagination_uses_rejected_callback(self):
        attempts = [{"id": 1, "chat_id": -100}]
        kb = rejected_list_keyboard("ru", attempts, page=0, total_pages=3, start_index=0)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_rejected:ru:1" in c for c in callbacks)

    def test_back_returns_to_whitelist_menu(self):
        kb = rejected_list_keyboard("en", [], page=0, total_pages=1, start_index=0)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:en" in c for c in callbacks)

    def test_restore_label_per_language(self):
        attempts = [{"id": 1, "chat_id": -100}]
        ru = _get_labels(rejected_list_keyboard("ru", attempts, 0, 1, start_index=0))
        en = _get_labels(rejected_list_keyboard("en", attempts, 0, 1, start_index=0))
        assert any("Вернуть" in lab for lab in ru)
        assert any("Restore" in lab for lab in en)

    def test_button_number_starts_at_one_on_first_page(self):
        """With start_index=0 (page 1), numbering starts at 1."""
        attempts = [{"id": 1, "chat_id": -100}, {"id": 2, "chat_id": -200}]
        kb = rejected_list_keyboard("ru", attempts, page=0, total_pages=1, start_index=0)
        labels = _get_labels(kb)
        assert any(lab.startswith("1 ") and "Вернуть" in lab for lab in labels)
        assert "1 🗑" in labels
        assert any(lab.startswith("2 ") and "Вернуть" in lab for lab in labels)
        assert "2 🗑" in labels

    def test_button_number_matches_body_numbering_on_third_page(self):
        """Button numbers must match the message-body numbering.

        The body numbers items via ``enumerate(attempts, start=page * _PER_PAGE + 1)``
        (see ``handlers/admin.py`` ``_render_wl_rejected``). With ``_PER_PAGE=5`` and
        ``page=2`` (the 3rd page, 0-based), the first item on the page is #11.
        """
        per_page = 5
        page = 2
        start_index = page * per_page
        attempts = [
            {"id": 101, "chat_id": -100},
            {"id": 102, "chat_id": -200},
        ]
        kb = rejected_list_keyboard(
            "ru",
            attempts,
            page=page,
            total_pages=5,
            start_index=start_index,
        )
        labels = _get_labels(kb)
        assert any(lab.startswith("11 ") and "Вернуть" in lab for lab in labels)
        assert "11 🗑" in labels
        assert any(lab.startswith("12 ") and "Вернуть" in lab for lab in labels)
        assert "12 🗑" in labels
        # Ensure the callback data still ties back to the right attempt/page.
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_restore:ru:101:2" in c for c in callbacks)
        assert any("adm_wl_restore:ru:102:2" in c for c in callbacks)


class TestConfirmDeleteAttemptKeyboard:
    def test_has_yes_and_cancel(self):
        kb = confirm_delete_attempt_keyboard("ru", attempt_id=42, page=1)
        callbacks = _get_callbacks(kb)
        assert "adm_wl_del:ru:42:1" in callbacks
        # Cancel returns to rejected list at same page
        assert "adm_wl_rejected:ru:1" in callbacks

    def test_yes_label_per_language(self):
        ru = _get_labels(confirm_delete_attempt_keyboard("ru", 1, 0))
        en = _get_labels(confirm_delete_attempt_keyboard("en", 1, 0))
        assert any("Да" in lab and "удалить" in lab for lab in ru)
        assert any("Yes" in lab and "delete" in lab for lab in en)


class TestPendingListKeyboard:
    def test_has_approve_reject_per_item(self):
        attempts = [
            {"id": 1, "chat_id": -100, "chat_title": "Test"},
            {"id": 2, "chat_id": -200, "chat_title": "Test2"},
        ]
        kb = pending_list_keyboard("ru", attempts, page=0, total_pages=1, start_index=0)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_apr:ru:1:0" in c for c in callbacks)
        assert any("adm_wl_rej:ru:1:0" in c for c in callbacks)
        assert any("adm_wl_apr:ru:2:0" in c for c in callbacks)
        assert any("adm_wl_rej:ru:2:0" in c for c in callbacks)

    def test_has_back_button(self):
        kb = pending_list_keyboard("en", [], page=0, total_pages=1, start_index=0)
        callbacks = _get_callbacks(kb)
        assert any("adm_wl:en" in c for c in callbacks)

    def test_button_number_starts_at_one_on_first_page(self):
        """With start_index=0 (page 1), numbering starts at 1."""
        attempts = [{"id": 1, "chat_id": -100}, {"id": 2, "chat_id": -200}]
        kb = pending_list_keyboard("ru", attempts, page=0, total_pages=1, start_index=0)
        labels = _get_labels(kb)
        assert "1 ✅" in labels
        assert "1 ❌" in labels
        assert "2 ✅" in labels
        assert "2 ❌" in labels

    def test_button_number_matches_body_numbering_on_third_page(self):
        """Button numbers must match the message-body numbering.

        The body numbers items via ``enumerate(attempts, start=page * _PER_PAGE + 1)``
        (see ``handlers/admin.py`` ``_render_wl_pending``). With ``_PER_PAGE=5`` and
        ``page=2`` (the 3rd page, 0-based), the first item on the page is #11.
        """
        per_page = 5
        page = 2
        start_index = page * per_page
        attempts = [
            {"id": 101, "chat_id": -100},
            {"id": 102, "chat_id": -200},
        ]
        kb = pending_list_keyboard(
            "ru",
            attempts,
            page=page,
            total_pages=5,
            start_index=start_index,
        )
        labels = _get_labels(kb)
        assert "11 ✅" in labels
        assert "11 ❌" in labels
        assert "12 ✅" in labels
        assert "12 ❌" in labels
        # Ensure the callback data still ties back to the right attempt/page.
        callbacks = _get_callbacks(kb)
        assert any("adm_wl_apr:ru:101:2" in c for c in callbacks)
        assert any("adm_wl_apr:ru:102:2" in c for c in callbacks)


class TestNumberedButtonSharedAcrossLists:
    """A-2: pending/rejected/chats must share ONE numbering helper.

    If a future edit re-inlines per-list numbering instead of going through
    ``_numbered_button``, this fails loudly instead of letting the three
    lists silently drift apart again.
    """

    def test_pending_routes_through_shared_helper(self):
        attempts = [{"id": 1, "chat_id": -100}]
        with patch.object(admin_kb, "_numbered_button", wraps=admin_kb._numbered_button) as helper:
            admin_kb.pending_list_keyboard("ru", attempts, page=0, total_pages=1, start_index=0)
        assert helper.called

    def test_rejected_routes_through_shared_helper(self):
        attempts = [{"id": 1, "chat_id": -100}]
        with patch.object(admin_kb, "_numbered_button", wraps=admin_kb._numbered_button) as helper:
            admin_kb.rejected_list_keyboard("ru", attempts, page=0, total_pages=1, start_index=0)
        assert helper.called

    def test_chats_routes_through_shared_helper(self):
        chats = [{"chat_id": -100, "chat_title": "Alpha", "chat_type": "group"}]
        with patch.object(admin_kb, "_numbered_button", wraps=admin_kb._numbered_button) as helper:
            admin_kb.chats_list_keyboard("ru", chats, page=0, total_pages=1, start_index=0)
        assert helper.called


class TestNotificationStatusKeyboards:
    def test_approved_keyboard_ru(self):
        kb = approved_notification_keyboard("ru")
        labels = _get_labels(kb)
        assert any("Одобрено" in label for label in labels)

    def test_approved_keyboard_en(self):
        kb = approved_notification_keyboard("en")
        labels = _get_labels(kb)
        assert any("Approved" in label for label in labels)

    def test_rejected_keyboard_ru(self):
        kb = rejected_notification_keyboard("ru")
        labels = _get_labels(kb)
        assert any("Отклонено" in label for label in labels)

    def test_rejected_keyboard_en(self):
        kb = rejected_notification_keyboard("en")
        labels = _get_labels(kb)
        assert any("Rejected" in label for label in labels)


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
