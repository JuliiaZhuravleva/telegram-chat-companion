"""Tests for the Knowledge Base admin sub-router's keyboards (A4).

Scope: the B-2 participant-picker keyboards (`kb_organizer_picker_keyboard`,
`_candidate_label`, `kb_org_add_prompt_keyboard`), the panel-origin routes
(`kb_menu_keyboard`, `kb_organizers_keyboard`), and — since S2/KB-08 —
`kb_undo_keyboard` plus the `kb_undo:` / `kb_view:` prefix boundary, which is
checked against the handlers' REAL filters rather than a re-typed prefix.

`kb_chat_picker_keyboard` still has no dedicated coverage here, and
`kb_view_keyboard` is exercised only as the other side of that prefix boundary
-- pre-existing gaps, not S2 regressions.
"""

from __future__ import annotations

from src.bot.keyboards.admin_kb import (
    _candidate_label,
    kb_menu_keyboard,
    kb_org_add_prompt_keyboard,
    kb_organizer_picker_keyboard,
    kb_organizers_keyboard,
    kb_undo_keyboard,
    kb_view_keyboard,
)
from src.bot.nav import PANEL_ORIGIN

CHAT_ID = -1001234567890

# Obviously-fake ids at the widest realistic shape (this repo is public).
# `chat_facts.id` is a bigint; 17 digits is the widest a Postgres sequence
# realistically reaches, and a Telegram user id is currently 10 digits.
WIDEST_FACT_ID = 99999999999999999
WIDEST_OWNER_ID = 9999999999


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


class TestSubPanelBackOrigin:
    """The KB submenu must return where the admin came from.

    Reported 2026-08-09: chat settings panel → chat → База знаний → Назад
    landed in the KB chat picker instead of back in that chat's panel.
    Older than the grouped panel (B-2), which only made it easy to hit.
    """

    def _back_of(self, keyboard) -> str:
        return keyboard.inline_keyboard[-1][0].callback_data

    def test_from_panel_back_returns_to_that_chats_panel(self) -> None:
        kb = kb_menu_keyboard("ru", chat_id=CHAT_ID, kb_enabled=True, origin=PANEL_ORIGIN)
        assert self._back_of(kb) == f"adm_pnl_menu:ru:{CHAT_ID}"

    def test_from_own_picker_back_is_unchanged(self) -> None:
        """The pre-existing route must keep working, not be traded away."""
        kb = kb_menu_keyboard("ru", chat_id=CHAT_ID, kb_enabled=True)
        assert self._back_of(kb) == "adm_kb:ru:0"

    def test_origin_survives_the_toggle_and_organizers_buttons(self) -> None:
        """Both lead to screens that come back here — if they dropped the
        origin, Back would silently revert after one tap."""
        kb = kb_menu_keyboard("ru", chat_id=CHAT_ID, kb_enabled=True, origin=PANEL_ORIGIN)
        callbacks = [cb for cb in _get_callbacks(kb) if not cb.startswith("adm_pnl_menu:")]
        assert callbacks, "expected the toggle and organizers rows"
        assert all(cb.endswith(f":{PANEL_ORIGIN}") for cb in callbacks), callbacks

    def test_organizers_screen_carries_the_origin_back_to_the_submenu(self) -> None:
        kb = kb_organizers_keyboard(
            [{"user_id": 7, "display_name": "Kim"}],
            lang="ru",
            chat_id=CHAT_ID,
            page=0,
            total=1,
            origin=PANEL_ORIGIN,
        )
        assert self._back_of(kb) == f"adm_kb_menu:ru:{CHAT_ID}:{PANEL_ORIGIN}"

    def test_unknown_origin_token_degrades_to_the_default_route(self) -> None:
        """A stale or forged payload must fall back, not raise."""
        kb = kb_menu_keyboard("ru", chat_id=CHAT_ID, kb_enabled=True, origin="zzz")
        assert self._back_of(kb) == "adm_kb:ru:0"

    def test_every_callback_fits_telegrams_64_byte_limit(self) -> None:
        """The origin token has to fit next to the longest realistic chat id.

        `kb_undo:` joins the sweep here (S2/KB-08): it is the first
        write-capable button in a *group* chat, and Telegram rejects an
        over-length `callback_data` at send time — the confirmation message
        would fail to post *after* the fact was already written, i.e. the fact
        lands and the user is told nothing.
        """
        long_id = -1009999000001234
        for kb in (
            kb_menu_keyboard("ru", chat_id=long_id, kb_enabled=True, origin=PANEL_ORIGIN),
            kb_organizers_keyboard(
                [{"user_id": 12345678901, "display_name": "Kim"}],
                lang="ru",
                chat_id=long_id,
                page=0,
                total=1,
                origin=PANEL_ORIGIN,
            ),
            kb_undo_keyboard("ru", fact_id=WIDEST_FACT_ID, owner_id=WIDEST_OWNER_ID),
            kb_undo_keyboard("en", fact_id=WIDEST_FACT_ID, owner_id=WIDEST_OWNER_ID),
        ):
            callbacks = _get_callbacks(kb)
            assert callbacks, "no callback_data collected — the sweep would be vacuous"
            for cb in callbacks:
                assert len(cb.encode()) <= 64, f"{cb} is {len(cb.encode())} bytes"


class TestKbUndoKeyboard:
    """The `/remember` confirmation's undo button (S2/KB-08).

    Threat model, and why it needs its own tests: this is the project's first
    write-capable inline button in a **group** chat, where Telegram lets any
    member press any button. The presser's right to use it is decided by the
    handler, but the payload is what tells the handler who was offered it — so
    the payload has to survive Telegram's transport intact (64 bytes) and parse
    back to exactly the two ids it was built from, unambiguously.
    """

    FACT_ID = 4242
    OWNER_ID = 1000000001

    def _only_callback(self) -> str:
        kb = kb_undo_keyboard("ru", fact_id=self.FACT_ID, owner_id=self.OWNER_ID)
        callbacks = _get_callbacks(kb)
        assert len(callbacks) == 1, callbacks
        return callbacks[0]

    def test_exactly_one_row_with_exactly_one_button(self) -> None:
        """The project caps a keyboard row at 2 buttons; undo is a single
        destructive action and must not acquire a neighbour it could be
        mis-tapped for."""
        kb = kb_undo_keyboard("ru", fact_id=self.FACT_ID, owner_id=self.OWNER_ID)
        assert len(kb.inline_keyboard) == 1, kb.inline_keyboard
        assert len(kb.inline_keyboard[0]) == 1, kb.inline_keyboard[0]

    def test_payload_round_trips_through_int_parsing(self) -> None:
        """The handler does `int(parts[1])`, `int(parts[2])` on a `:`-split of
        the payload. Three parts, and both ids come back byte-identical —
        a fourth field or a stray `:` would silently retire a different fact
        or hand the button to a different user."""
        payload = self._only_callback()
        parts = payload.split(":")
        assert parts[0] == "kb_undo", payload
        assert len(parts) == 3, parts
        assert int(parts[1]) == self.FACT_ID, payload
        assert int(parts[2]) == self.OWNER_ID, payload

    def test_label_is_localised_both_ways(self) -> None:
        assert any(
            "Убрать" in text for text in _get_labels(kb_undo_keyboard("ru", fact_id=1, owner_id=2))
        )
        assert any(
            "Remove" in text for text in _get_labels(kb_undo_keyboard("en", fact_id=1, owner_id=2))
        )

    def test_language_does_not_leak_into_the_payload(self) -> None:
        """Unlike the `adm_kb_*` family, `kb_undo:` carries no lang field — the
        handler reads it from `chat_config`. If one variant grew one, the
        handler's positional `int(parts[2])` would parse a language code."""
        ru = _get_callbacks(kb_undo_keyboard("ru", fact_id=7, owner_id=8))
        en = _get_callbacks(kb_undo_keyboard("en", fact_id=7, owner_id=8))
        assert ru == en == ["kb_undo:7:8"]


def _callback_filters(handler_name: str) -> list:
    """The REAL registered filters of a `commands` callback_query handler.

    Read off the router rather than re-spelled here: a test that restates
    `F.data.startswith("kb_undo:")` proves only that the test author can type
    the prefix twice.
    """
    from src.bot.handlers.commands import router as commands_router

    for handler in commands_router.callback_query.handlers:
        if handler.callback.__name__ == handler_name:
            filters = [f.callback for f in (handler.filters or ())]
            assert filters, f"{handler_name} has no filters — a match test would be vacuous"
            return filters
    raise AssertionError(f"no callback_query handler named {handler_name}")


def _matches(handler_name: str, data: str) -> bool:
    from types import SimpleNamespace

    query = SimpleNamespace(data=data)
    return all(bool(flt(query)) for flt in _callback_filters(handler_name))


class TestKbCallbackPrefixHygiene:
    """`kb_undo:` and `kb_view:` are siblings in the same router.

    Both are un-namespaced (no `adm_` prefix) because both live outside the
    admin DM panel, so they are the pair most able to collide. aiogram consumes
    an update at the first matching handler, so a prefix that matches the wrong
    handler does not fall through — `/kb` pagination would retire a fact, or
    undo would silently repaint a list.

    Driven through the handlers' REAL filter objects, and each payload comes
    from the real keyboard builder rather than a hand-typed string.
    """

    def test_kb_undo_payload_matches_only_the_undo_handler(self) -> None:
        payload = _get_callbacks(kb_undo_keyboard("ru", fact_id=4242, owner_id=1000000001))[0]
        assert _matches("handle_kb_undo", payload)
        assert not _matches("handle_kb_view_page", payload)

    def test_kb_view_payload_matches_only_the_view_handler(self) -> None:
        payloads = [
            cb
            for cb in _get_callbacks(kb_view_keyboard("ru", page=1, total_pages=3))
            if cb != "noop"
        ]
        assert payloads, "expected pagination callbacks"
        for payload in payloads:
            assert _matches("handle_kb_view_page", payload)
            assert not _matches("handle_kb_undo", payload)

    def test_the_trailing_colon_stops_a_future_sibling_prefix(self) -> None:
        """`str.startswith("kb_undo")` without the colon would also match
        `kb_undo_all:`; the colon is what makes the two disambiguate."""
        assert not _matches("handle_kb_undo", "kb_undo_all:1"), (
            "kb_undo_all: matched handle_kb_undo"
        )
        assert not _matches("handle_kb_view_page", "kb_viewer:ru:1"), (
            "kb_viewer: matched handle_kb_view_page"
        )

    def test_the_match_test_can_say_no(self) -> None:
        """Negative control for the matcher itself: an unrelated payload must
        match neither handler, so a `_matches` that always returned True (e.g.
        an empty filter list) cannot make the assertions above vacuous."""
        assert not _matches("handle_kb_undo", "adm_kb_menu:ru:-1001"), "matcher always says yes"
        assert not _matches("handle_kb_view_page", "adm_kb_menu:ru:-1001"), (
            "matcher always says yes"
        )
