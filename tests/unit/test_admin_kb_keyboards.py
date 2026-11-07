"""The organizer screens as data, the add prompt's reply keyboard, and the
group ``/remember`` undo button.

The organizer screens (``screens/orgs.py``) are asserted without Telegram:
labels, callbacks and texts per state. The prompt keyboard is the one piece
still built into aiogram markup here, because its ``request_users`` fields
are what Telegram's person search answers with.

The ``kb_undo:`` / ``kb_view:`` tests at the end belong to the group-chat
``/remember`` flow, which shares this file's history; the prefix boundary is
checked against the handlers' REAL filters rather than a re-typed prefix.
"""

from __future__ import annotations

import pytest

from src.bot.custom_emoji import FENCE
from src.bot.keyboards.admin_kb import (
    ORG_CANCEL_LABEL,
    ORG_PICK_LABEL,
    ORG_PICK_REQUEST_ID,
    kb_org_add_prompt_keyboard,
)
from src.bot.keyboards.kb import kb_undo_keyboard, kb_view_keyboard
from src.bot.screens import orgs
from src.bot.screens.lint import budget, label_width_px
from src.bot.screens.model import Screen
from src.bot.screens.routes import NOOP

CHAT_ID = -1001234567890

# Obviously-fake ids at the widest realistic shape (this repo is public).
# `chat_facts.id` is a bigint; 17 digits is the widest a Postgres sequence
# realistically reaches, and a Telegram user id is currently 10 digits.
WIDEST_FACT_ID = 99999999999999999
WIDEST_OWNER_ID = 9999999999
WIDEST_CHAT_ID = -1009999000001234
WIDEST_USER_ID = 99999999999

PEOPLE = [(111, "Аня"), (222, "Борис")]


def _get_callbacks(keyboard):
    return [
        btn.callback_data for row in keyboard.inline_keyboard for btn in row if btn.callback_data
    ]


def _get_labels(keyboard):
    return [btn.text for row in keyboard.inline_keyboard for btn in row]


def _rows(screen: Screen) -> list[list[tuple[str, str | None]]]:
    return [[(b.label, b.callback) for b in row] for row in screen.rows]


def _labels(screen: Screen) -> list[str]:
    return [b.label for row in screen.rows for b in row]


def _callbacks(screen: Screen) -> list[str]:
    return [b.callback for row in screen.rows for b in row if b.callback]


# ---------------------------------------------------------------------------
# Names on buttons
# ---------------------------------------------------------------------------


class TestCandidateName:
    def test_name_and_username(self) -> None:
        assert orgs.candidate_name("Иван", "ivan123", 1) == "Иван @ivan123"

    def test_username_only(self) -> None:
        assert orgs.candidate_name(None, "ivan123", 1) == "@ivan123"

    def test_first_name_only(self) -> None:
        assert orgs.candidate_name("Иван", None, 1) == "Иван"

    def test_neither_falls_back_to_the_id(self) -> None:
        assert orgs.candidate_name(None, None, 999) == "999"

    def test_a_blank_name_is_no_name(self) -> None:
        assert orgs.candidate_name("   ", None, 999) == "999"


class TestClip:
    def test_a_label_that_fits_is_unchanged(self) -> None:
        assert orgs.clip("✖️ Аня", budget(1)) == "✖️ Аня"

    def test_a_long_label_is_cut_to_the_row_with_an_ellipsis(self) -> None:
        long = "✖️ " + "Ж" * 60
        clipped = orgs.clip(long, budget(1))
        assert clipped.endswith("…")
        assert long.startswith(clipped[:-1])
        assert label_width_px(clipped) <= budget(1)

    def test_the_cut_is_by_width_not_by_characters(self) -> None:
        """Wide capitals lose more characters than narrow dots do."""
        wide = orgs.clip("Ж" * 80, budget(1))
        narrow = orgs.clip("." * 80, budget(1))
        assert len(narrow) > len(wide)


def test_page_of() -> None:
    assert [orgs.page_of(i) for i in range(7)] == [0, 0, 0, 1, 1, 1, 2]
    assert orgs.page_of(-1) == 0


# ---------------------------------------------------------------------------
# orgs
# ---------------------------------------------------------------------------


class TestOrgsScreen:
    def test_one_card_row_per_person_then_add_then_back(self) -> None:
        """A name opens the person's card; until 2026-11 the same tap removed them."""
        screen = orgs.orgs_screen("ru", CHAT_ID, "Чат", PEOPLE)

        assert screen.id == "orgs"
        assert _rows(screen) == [
            [("👤 Аня", f"adm_kb_org:ru:{CHAT_ID}:111")],
            [("👤 Борис", f"adm_kb_org:ru:{CHAT_ID}:222")],
            [("➕ Добавить", f"adm_kb_org_list:ru:{CHAT_ID}:0")],
            [("⬅️ Чат", f"adm_pnl_menu:ru:{CHAT_ID}")],
        ]
        assert screen.text.startswith("👥 <b>Организаторы</b> · Чат\n\n")

    def test_the_line_says_what_organizers_can_do_not_what_a_tap_does(self) -> None:
        """A text twin: the tap no longer removes, so the line that warned it
        did is gone, and the body does not list the buttons either."""
        for lang in ("ru", "en"):
            text = orgs.orgs_screen(lang, CHAT_ID, "Чат", PEOPLE).text
            assert text.endswith(orgs.ORGS_BODY(lang)), text
            assert f"«{orgs.UNDO(lang)}»" not in text
        ru = orgs.orgs_screen("ru", CHAT_ID, "Чат", PEOPLE).text
        assert "Нажатие" not in ru and "убира" not in ru and "Добавить" not in ru

    def test_empty_says_who_can_save_facts_anyway(self) -> None:
        screen = orgs.orgs_screen("ru", CHAT_ID, "Чат", [])

        assert screen.text.endswith("Пока никого. Админы бота могут сохранять факты всегда.")
        assert "Нажатие на имя" not in screen.text
        assert _labels(screen) == ["➕ Добавить", "⬅️ Чат"]

    def test_undo_shares_the_add_row_and_carries_the_position(self) -> None:
        screen = orgs.orgs_screen(
            "ru", CHAT_ID, "Чат", PEOPLE, notice="Вера больше не организатор", undo=(333, 2)
        )

        assert _rows(screen)[-2] == [
            ("↩️ Вернуть", f"adm_kb_org_undo:ru:{CHAT_ID}:333:2"),
            ("➕ Добавить", f"adm_kb_org_list:ru:{CHAT_ID}:0"),
        ]
        assert screen.notice == "Вера больше не организатор"

    def test_pager_wraps_around(self) -> None:
        screen = orgs.orgs_screen("ru", CHAT_ID, "Чат", PEOPLE, page=0, pages=3)

        assert _rows(screen)[2] == [
            ("◀", f"adm_kb_orgs:ru:{CHAT_ID}:2"),
            ("1/3", NOOP),
            ("▶", f"adm_kb_orgs:ru:{CHAT_ID}:1"),
        ]

    def test_a_full_page_with_pager_and_undo_stays_within_six_rows(self) -> None:
        people = [(i, f"Участник {i}") for i in range(orgs.ORGS_PER_PAGE)]
        screen = orgs.orgs_screen("ru", CHAT_ID, "Чат", people, pages=2, undo=(9, 0))

        assert len(screen.rows) <= 6
        assert all(len(row) <= 3 for row in screen.rows)

    def test_a_long_name_is_clipped_to_the_row(self) -> None:
        screen = orgs.orgs_screen("ru", CHAT_ID, "Чат", [(1, "Ж" * 60)])

        label = _labels(screen)[0]
        assert label.startswith("👤 Ж") and label.endswith("…")
        assert label_width_px(label) <= orgs.NAME_PX

    def test_someone_not_in_the_chat_is_marked(self) -> None:
        screen = orgs.orgs_screen("ru", CHAT_ID, "Чат", PEOPLE, absent={222})

        assert _rows(screen)[:2] == [
            [("👤 Аня", f"adm_kb_org:ru:{CHAT_ID}:111")],
            [("👤 Борис · не в чате", f"adm_kb_org:ru:{CHAT_ID}:222")],
        ]
        english = orgs.orgs_screen("en", CHAT_ID, "Chat", PEOPLE, absent={222})
        assert _labels(english)[1] == "👤 Борис · not in chat"

    def test_the_mark_is_explained_only_while_the_page_shows_it(self) -> None:
        """All four readers guessed what «не в чате» means; the task "remove
        the organizer who left" turns on it."""
        line = orgs.ORGS_ABSENT("ru")
        assert line in orgs.orgs_screen("ru", CHAT_ID, "Чат", PEOPLE, absent={222}).text
        assert line not in orgs.orgs_screen("ru", CHAT_ID, "Чат", PEOPLE).text
        assert line not in orgs.orgs_screen("ru", CHAT_ID, "Чат", PEOPLE, absent={999}).text

    def test_a_long_name_is_clipped_and_the_mark_survives(self) -> None:
        screen = orgs.orgs_screen("ru", CHAT_ID, "Чат", [(1, "Ж" * 60)], absent={1})

        label = _labels(screen)[0]
        assert label.startswith("👤 Ж") and label.endswith("… · не в чате")
        assert label_width_px(label) <= orgs.NAME_PX

    def test_the_title_is_escaped_and_a_missing_one_is_the_chat_id(self) -> None:
        assert (
            "&lt;i&gt;x&lt;/i&gt; &amp; y"
            in orgs.orgs_screen("ru", CHAT_ID, "<i>x</i> & y", []).text
        )
        assert orgs.orgs_screen("ru", CHAT_ID, None, []).text.startswith(
            f"👥 <b>Организаторы</b> · {CHAT_ID}"
        )

    def test_english(self) -> None:
        screen = orgs.orgs_screen("en", CHAT_ID, "Chat", PEOPLE, undo=(333, 0))

        assert screen.text.startswith("👥 <b>Organizers</b> · Chat")
        assert _labels(screen)[-3:] == ["↩️ Undo", "➕ Add", "⬅️ Chat"]
        assert all(":en:" in cb for cb in _callbacks(screen))


class TestDisplayName:
    """The name a live member or a stored row gives the organizer list."""

    @pytest.mark.parametrize(
        ("parts", "expected"),
        [
            (("anya", "Аня", "Ким"), "@anya"),
            ((None, "Аня", "Ким"), "Аня Ким"),
            ((None, "Аня", None), "Аня"),
            ((None, None, "Ким"), "Ким"),
            (("  ", " Аня ", ""), "Аня"),
            ((None, None, None), None),
            ((object(), object(), None), None),
        ],
        ids=["nick", "full", "first", "last", "blank_parts", "nothing", "not_text"],
    )
    def test_nick_else_the_name(self, parts: tuple[object, ...], expected: str | None) -> None:
        assert orgs.display_name(*parts) == expected


# ---------------------------------------------------------------------------
# org.list
# ---------------------------------------------------------------------------


class TestOrgListScreen:
    def test_current_organizers_are_marked_and_the_marks_are_explained(self) -> None:
        screen = orgs.org_list_screen("ru", CHAT_ID, "Чат", PEOPLE, current={111})

        assert screen.id == "org.list"
        assert _rows(screen) == [
            [("✓ Аня", f"adm_kb_org_pick:ru:{CHAT_ID}:111")],
            [("Борис", f"adm_kb_org_pick:ru:{CHAT_ID}:222")],
            [
                ("✏️ Нет в списке", f"adm_kb_org_add:ru:{CHAT_ID}"),
                ("⬅️ Организаторы", f"adm_kb_orgs:ru:{CHAT_ID}:0"),
            ],
        ]
        assert screen.text.endswith("✓ — уже организатор.")

    def test_without_current_organizers_on_the_page_the_mark_is_not_explained(self) -> None:
        screen = orgs.org_list_screen("ru", CHAT_ID, "Чат", PEOPLE, current={999})

        assert "✓" not in screen.text
        assert "✓ Аня" not in _labels(screen)

    def test_nobody_seen(self) -> None:
        screen = orgs.org_list_screen("ru", CHAT_ID, "Чат", [])

        assert "Бот пока не видел здесь ни одного сообщения." in screen.text
        assert _labels(screen) == ["✏️ Нет в списке", "⬅️ Организаторы"]

    def test_a_name_starting_with_an_emoji_is_fenced_off_as_data(self) -> None:
        screen = orgs.org_list_screen("ru", CHAT_ID, "Чат", [(5, "🎉 Праздник")])

        assert _labels(screen)[0] == FENCE + "🎉 Праздник"

    def test_pager(self) -> None:
        screen = orgs.org_list_screen("ru", CHAT_ID, "Чат", PEOPLE, page=1, pages=3)

        assert _rows(screen)[2] == [
            ("◀", f"adm_kb_org_list:ru:{CHAT_ID}:0"),
            ("2/3", NOOP),
            ("▶", f"adm_kb_org_list:ru:{CHAT_ID}:2"),
        ]

    def test_a_full_page_stays_within_six_rows(self) -> None:
        people = [(i, f"Участник {i}") for i in range(orgs.LIST_PER_PAGE)]
        screen = orgs.org_list_screen("ru", CHAT_ID, "Чат", people, pages=2)

        assert len(screen.rows) <= 6


# ---------------------------------------------------------------------------
# org.add: the reply keyboard
# ---------------------------------------------------------------------------


class TestOrgAddScreen:
    def test_a_new_message_with_telegrams_search_and_a_cancel(self) -> None:
        screen = orgs.org_add_screen("ru", "Чат", CHAT_ID)

        assert screen.id == "org.add"
        assert screen.rows == ()
        assert screen.reply is not None
        assert [[b.label for b in row] for row in screen.reply] == [
            ["🔍 Найти в Telegram"],
            ["✖️ Отмена"],
        ]
        assert screen.placeholder == "@username"
        assert screen.text.startswith("👥 <b>Новый организатор</b> · Чат\n\n")

    def test_the_title_falls_back_to_the_id_then_to_nothing(self) -> None:
        assert orgs.org_add_screen("ru", None, CHAT_ID).text.startswith(
            f"👥 <b>Новый организатор</b> · {CHAT_ID}\n"
        )
        assert orgs.org_add_screen("ru").text.startswith("👥 <b>Новый организатор</b>\n")

    def test_the_title_is_escaped(self) -> None:
        assert "&lt;b&gt;" in orgs.org_add_screen("ru", "<b>", CHAT_ID).text

    def test_the_labels_the_handlers_match_are_the_ones_on_the_keyboard(self) -> None:
        for lang in ("ru", "en"):
            keyboard = kb_org_add_prompt_keyboard(lang)
            assert keyboard.keyboard[0][0].text == ORG_PICK_LABEL[lang]
            assert keyboard.keyboard[1][0].text == ORG_CANCEL_LABEL[lang]


class TestKbOrgAddPromptKeyboard:
    """The prompt's keyboard is Telegram's own person picker (request_users)."""

    def test_top_button_asks_telegram_for_one_person_not_a_bot(self):
        kb = kb_org_add_prompt_keyboard("ru")
        button = kb.keyboard[0][0]
        request = button.request_users
        assert button.text == "🔍 Найти в Telegram"
        assert request is not None
        assert request.request_id == ORG_PICK_REQUEST_ID
        assert request.user_is_bot is False
        assert request.max_quantity == 1
        # Without these the shared user arrives as a bare id and the answer
        # could only say "участник 123".
        assert request.request_name is True
        assert request.request_username is True

    def test_second_row_is_a_plain_cancel_button(self):
        kb = kb_org_add_prompt_keyboard("ru")
        assert [[b.text for b in row] for row in kb.keyboard] == [
            ["🔍 Найти в Telegram"],
            ["✖️ Отмена"],
        ]
        assert kb.keyboard[1][0].request_users is None

    def test_one_time_and_resized(self):
        kb = kb_org_add_prompt_keyboard("ru")
        assert kb.one_time_keyboard is True
        assert kb.resize_keyboard is True

    def test_english_labels(self):
        kb = kb_org_add_prompt_keyboard("en")
        assert [[b.text for b in row] for row in kb.keyboard] == [
            ["🔍 Find in Telegram"],
            ["✖️ Cancel"],
        ]

    def test_request_id_fits_telegrams_signed_32_bit_field(self):
        assert 0 < ORG_PICK_REQUEST_ID < 2**31


# ---------------------------------------------------------------------------
# org.fail
# ---------------------------------------------------------------------------


class TestOrgFailScreen:
    @pytest.mark.parametrize("reason", ["not_found", "hidden", "elsewhere", "channel"])
    def test_one_row_the_members_only(self, reason: orgs.Reason) -> None:
        """No inline «✖️ Отмена»: the prompt's own is still on the reply keyboard
        under the input, and both at once read as two different cancels."""
        screen = orgs.org_fail_screen("ru", CHAT_ID, "Чат", reason)

        assert screen.id == "org.fail"
        assert _rows(screen) == [[("👥 Из участников", f"adm_kb_org_list:ru:{CHAT_ID}:0")]]
        assert screen.text == f"👥 <b>Новый организатор</b> · Чат\n\n{orgs.FAIL[reason]('ru')}"

    def test_every_reason_says_something_different(self) -> None:
        for lang in ("ru", "en"):
            texts = {orgs.FAIL[r](lang) for r in ("not_found", "hidden", "elsewhere", "channel")}
            assert len(texts) == 4


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def test_every_organizer_callback_fits_telegrams_64_byte_limit() -> None:
    """At the widest ids: a supergroup id, an 11-digit user id, a two-digit
    position and page. Telegram rejects an over-length ``callback_data`` when
    the message is sent, so the screen would not appear at all."""
    people = [(WIDEST_USER_ID, "Ж" * 40)] * orgs.ORGS_PER_PAGE
    screens = (
        [
            orgs.orgs_screen(
                lang, WIDEST_CHAT_ID, "Чат", people, page=98, pages=99, undo=(WIDEST_USER_ID, 99)
            )
            for lang in ("ru", "en")
        ]
        + [
            orgs.org_list_screen(lang, WIDEST_CHAT_ID, "Чат", people, page=98, pages=99)
            for lang in ("ru", "en")
        ]
        + [orgs.org_fail_screen(lang, WIDEST_CHAT_ID, "Чат", "hidden") for lang in ("ru", "en")]
    )

    callbacks = [cb for screen in screens for cb in _callbacks(screen)]
    assert len(callbacks) > 20, "no callback_data collected — the sweep would be vacuous"
    assert "adm_kb_org_undo:" in "".join(callbacks)
    for cb in callbacks:
        assert len(cb.encode()) <= 64, f"{cb} is {len(cb.encode())} bytes"


def test_the_group_undo_button_fits_telegrams_64_byte_limit() -> None:
    """``kb_undo:`` is the first write-capable button in a *group* chat:
    an over-length payload would fail the confirmation AFTER the fact was
    written, so the fact lands and the user is told nothing."""
    for lang in ("ru", "en"):
        kb = kb_undo_keyboard(lang, fact_id=WIDEST_FACT_ID, owner_id=WIDEST_OWNER_ID)
        callbacks = _get_callbacks(kb)
        assert callbacks
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
        assert not _matches("handle_kb_undo", "adm_kb_orgs:ru:-1001"), "matcher always says yes"
        assert not _matches("handle_kb_view_page", "adm_kb_orgs:ru:-1001"), (
            "matcher always says yes"
        )
