"""Tests for the Knowledge Base admin sub-router's keyboards (A4).

Scope: unit coverage for the B-2 participant-picker keyboards
(`kb_organizer_picker_keyboard`, `_candidate_label`, `kb_org_add_prompt_keyboard`).
The rest of this module (`kb_chat_picker_keyboard`, `kb_menu_keyboard`,
`kb_organizers_keyboard`, `kb_view_keyboard`) has no dedicated keyboard-unit
coverage yet -- pre-existing gap, out of scope for B-2.
"""

from __future__ import annotations

from src.bot.keyboards.admin_kb import (
    _candidate_label,
    kb_org_add_prompt_keyboard,
    kb_organizer_picker_keyboard,
)

CHAT_ID = -1001234567890


def _get_callbacks(keyboard):
    return [
        btn.callback_data for row in keyboard.inline_keyboard for btn in row if btn.callback_data
    ]


def _get_labels(keyboard):
    return [btn.text for row in keyboard.inline_keyboard for btn in row]


class TestCandidateLabel:
    def test_name_and_username(self):
        label = _candidate_label({"user_id": 1, "first_name": "Иван", "username": "ivan123"})
        assert label == "Иван (@ivan123)"

    def test_username_only(self):
        label = _candidate_label({"user_id": 1, "first_name": None, "username": "ivan123"})
        assert label == "@ivan123"

    def test_first_name_only(self):
        label = _candidate_label({"user_id": 1, "first_name": "Иван", "username": None})
        assert label == "Иван"

    def test_neither_falls_back_to_user_id(self):
        label = _candidate_label({"user_id": 999, "first_name": None, "username": None})
        assert label == "999"

    def test_long_label_is_truncated(self):
        label = _candidate_label({"user_id": 1, "first_name": "A" * 40, "username": "somebody"})
        assert len(label) <= 35


class TestKbOrgAddPromptKeyboard:
    def test_has_show_participants_button(self):
        kb = kb_org_add_prompt_keyboard("ru", chat_id=CHAT_ID)
        callbacks = _get_callbacks(kb)
        assert callbacks == [f"adm_kb_org_list:ru:{CHAT_ID}:0"]

    def test_english_label(self):
        kb = kb_org_add_prompt_keyboard("en", chat_id=CHAT_ID)
        labels = _get_labels(kb)
        assert any("Show participants" in label for label in labels)


class TestKbOrganizerPickerKeyboard:
    def test_one_row_per_candidate_with_pick_callback(self):
        candidates = [
            {"user_id": 111, "first_name": "Alice", "username": "alice"},
            {"user_id": 222, "first_name": "Bob", "username": None},
        ]
        kb = kb_organizer_picker_keyboard(
            candidates, lang="ru", chat_id=CHAT_ID, page=0, total=2, per_page=5
        )
        callbacks = _get_callbacks(kb)
        assert f"adm_kb_org_pick:ru:{CHAT_ID}:111" in callbacks
        assert f"adm_kb_org_pick:ru:{CHAT_ID}:222" in callbacks

    def test_labels_match_candidate_label_format(self):
        candidates = [{"user_id": 111, "first_name": "Alice", "username": "alice"}]
        kb = kb_organizer_picker_keyboard(
            candidates, lang="ru", chat_id=CHAT_ID, page=0, total=1, per_page=5
        )
        labels = _get_labels(kb)
        assert "Alice (@alice)" in labels

    def test_no_pagination_row_when_total_fits_one_page(self):
        candidates = [{"user_id": 111, "first_name": "Alice", "username": "alice"}]
        kb = kb_organizer_picker_keyboard(
            candidates, lang="ru", chat_id=CHAT_ID, page=0, total=1, per_page=5
        )
        callbacks = _get_callbacks(kb)
        assert not any(c.startswith("adm_kb_org_list:") for c in callbacks)

    def test_pagination_row_when_total_exceeds_page(self):
        candidates = [{"user_id": i, "first_name": f"User{i}", "username": None} for i in range(5)]
        kb = kb_organizer_picker_keyboard(
            candidates, lang="ru", chat_id=CHAT_ID, page=0, total=12, per_page=5
        )
        callbacks = _get_callbacks(kb)
        assert f"adm_kb_org_list:ru:{CHAT_ID}:1" in callbacks
        # First page: no "previous" nav button.
        assert f"adm_kb_org_list:ru:{CHAT_ID}:-1" not in callbacks

    def test_middle_page_has_both_nav_directions(self):
        candidates = [{"user_id": i, "first_name": f"User{i}", "username": None} for i in range(5)]
        kb = kb_organizer_picker_keyboard(
            candidates, lang="ru", chat_id=CHAT_ID, page=1, total=12, per_page=5
        )
        callbacks = _get_callbacks(kb)
        assert f"adm_kb_org_list:ru:{CHAT_ID}:0" in callbacks
        assert f"adm_kb_org_list:ru:{CHAT_ID}:2" in callbacks

    def test_back_button_goes_to_organizers_list(self):
        kb = kb_organizer_picker_keyboard(
            [], lang="ru", chat_id=CHAT_ID, page=0, total=0, per_page=5
        )
        callbacks = _get_callbacks(kb)
        assert f"adm_kb_orgs:ru:{CHAT_ID}:0" in callbacks

    def test_empty_candidates_still_returns_back_row(self):
        kb = kb_organizer_picker_keyboard(
            [], lang="ru", chat_id=CHAT_ID, page=0, total=0, per_page=5
        )
        assert len(kb.inline_keyboard) == 1
