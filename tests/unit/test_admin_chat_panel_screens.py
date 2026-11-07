"""The chat sections' pure builders, called directly.

The handler tests reach these builders through a mocked repository and check
a label or two; the sample screens cover one view each. What only a builder
call can pin is the shape across states: which switches a section hides,
which values the text says come from the general settings, and what the
community text says is on.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from src.bot.screens import chat as screens
from src.bot.screens.chat import ChatView, config_values
from src.bot.screens.lint import clip_room, label_width_px
from src.bot.screens.model import Screen
from src.bot.settings_fields import FIELDS_BY_CODE
from src.models.chat_config import ChatConfig

CHAT_ID = -1000000000001
LANGS = ("ru", "en")
LEGACY_CODES = ("sm", "rag", "tv", "tn", "af", "ia", "sl")
"""Switches whose columns carry a DEFAULT: NULL there is not "inherited"."""
INHERITING_CODES = ("ch", "tx", "lc", "rg", "sr", "ic")
"""Non-legacy switches on these four sections: NULL means the global value."""
COMMUNITY_SUBTOGGLES = ("gf", "wb", "fp", "fd", "rj")


def _view(row: dict[str, Any], *, organizers: int = 0, **values: Any) -> ChatView:
    return ChatView(
        chat_id=CHAT_ID,
        title="Тестовый чат",
        chat_type="supergroup",
        values=config_values(ChatConfig(chat_id=CHAT_ID, enabled=True), **values),
        row=row,
        organizers=organizers,
    )


def _switches(screen: Screen) -> dict[str, str]:
    """Each switch's code -> its label, read off the ``adm_pnl_tgl:`` callbacks."""
    return {
        b.callback.rsplit(":", 1)[1]: b.label
        for row in screen.rows
        for b in row
        if b.callback is not None and b.callback.startswith("adm_pnl_tgl:")
    }


def _null(*codes: str) -> dict[str, Any]:
    return {FIELDS_BY_CODE[code].key: None for code in codes}


# ── what a section hides ──────────────────────────────────────────────────


class TestCommunityHidesItsSwitchesWhileTheModeIsOff:
    @pytest.mark.parametrize("lang", LANGS)
    def test_off_shows_only_the_mode_and_advice(self, lang: str) -> None:
        screen = screens.build_community(_view({}, community_enabled=False), lang)

        assert set(_switches(screen)) == {"cm", "aw"}
        callbacks = [b.callback or "" for row in screen.rows for b in row]
        for code in COMMUNITY_SUBTOGGLES:
            assert not any(c.endswith(f":{code}") for c in callbacks), code

    @pytest.mark.parametrize("lang", LANGS)
    def test_on_shows_all_seven(self, lang: str) -> None:
        screen = screens.build_community(_view({}, community_enabled=True), lang)

        assert set(_switches(screen)) == {"cm", *COMMUNITY_SUBTOGGLES, "aw"}


class TestReactionsHideTheHistorySwitchWhileOff:
    @pytest.mark.parametrize("lang", LANGS)
    def test_off_has_no_history_switch(self, lang: str) -> None:
        screen = screens.build_reactions(_view({}, reactions_enabled=False), lang)

        assert set(_switches(screen)) == {"rx"}
        assert not any(
            b.callback == f"adm_pnl_tgl:{lang}:{CHAT_ID}:rh" for row in screen.rows for b in row
        )

    @pytest.mark.parametrize("lang", LANGS)
    def test_on_has_it(self, lang: str) -> None:
        screen = screens.build_reactions(_view({}, reactions_enabled=True), lang)

        assert set(_switches(screen)) == {"rx", "rh"}


# ── a value from the general settings ────────────────────────────────────


_SECTIONS = ("behavior", "memory", "voice", "stickers")


def _all_switches(row: dict[str, Any], lang: str, **values: Any) -> dict[str, str]:
    view = _view(row, **values)
    found: dict[str, str] = {}
    for section in _SECTIONS:
        found.update(_switches(screens.SECTION_BUILDERS[section](view, lang)))
    return found


def _all_texts(row: dict[str, Any], lang: str, **values: Any) -> str:
    view = _view(row, **values)
    return "\n".join(screens.SECTION_BUILDERS[s](view, lang).text for s in _SECTIONS)


def _common_line(text: str) -> str:
    """The one line of ``text`` that names values taken from the general settings."""
    head = screens.COMMON_NOTE("ru").split("{", 1)[0]
    lines = [line for line in text.split("\n") if line.startswith(head)]
    assert len(lines) <= 1, lines
    return lines[0] if lines else ""


class TestAButtonShowsTheValueAndTheTextItsOrigin:
    """``✅``/``⚪`` and the name are the whole button: «⚪ Искать по архиву чата ·
    общее» read as "off, or not set?". Which values come from the general
    settings is one plain line of the text. A legacy column's NULL is not
    "inherited" (its DEFAULT fills new rows), so that line never names it; the
    inheriting switches, NULL in the same row, are named — the control that
    the line is reachable at all."""

    @pytest.mark.parametrize("lang", LANGS)
    def test_no_button_says_where_its_value_comes_from(self, lang: str) -> None:
        labels = _all_switches(_null(*LEGACY_CODES, *INHERITING_CODES, "kb"), lang)

        assert set(LEGACY_CODES) | set(INHERITING_CODES) <= set(labels)
        for code, label in labels.items():
            assert (
                label == f"{'✅' if label.startswith('✅') else '⚪'} {screens.LABELS[code](lang)}"
            )

    @pytest.mark.parametrize("lang", LANGS)
    def test_the_text_names_the_inheriting_switches_and_not_the_legacy_ones(
        self, lang: str
    ) -> None:
        view = _view(_null(*LEGACY_CODES, *INHERITING_CODES))
        for section in _SECTIONS:
            screen = screens.SECTION_BUILDERS[section](view, lang)
            on_screen = set(_switches(screen))
            line = next(
                (x for x in screen.text.split("\n") if x.startswith(_common_head(lang))), ""
            )
            for code in on_screen & set(INHERITING_CODES):
                assert screens.LABELS[code](lang) in line, (section, code, line)
            for code in on_screen & set(LEGACY_CODES):
                assert screens.LABELS[code](lang) not in line, (section, code, line)

    @pytest.mark.parametrize("section", _SECTIONS)
    def test_the_line_is_plain_text_not_an_aside(self, section: str) -> None:
        """Italics are what glare takes first; this caveat must survive it."""
        view = _view(_null(*INHERITING_CODES))
        line = _common_line(screens.SECTION_BUILDERS[section](view, "ru").text)
        assert line.startswith("Как в общих настройках: "), section
        assert "<i>" not in line and "</i>" not in line, line

    @pytest.mark.parametrize("lang", LANGS)
    def test_a_chat_that_set_its_own_values_has_no_line(self, lang: str) -> None:
        own = {FIELDS_BY_CODE[code].key: True for code in (*INHERITING_CODES, "kb")}
        own["tolerance_level"] = 0.5

        text = _all_texts({**_null(*LEGACY_CODES), **own}, lang)

        assert _common_head(lang) not in text
        assert "общих настройках" not in text and "general settings" not in text

    def test_the_explicitness_button_is_named_in_the_line_not_on_the_button(self) -> None:
        screen = screens.build_stickers(_view({}), "ru")
        (button,) = [
            b for row in screen.rows for b in row if (b.callback or "").startswith("adm_pnl_tol:")
        ]
        assert "общее" not in button.label, button.label
        assert "Откровенность" in _common_line(screen.text)

    def test_every_value_inherited_says_so_once(self) -> None:
        screen = screens.build_community(_view({}, community_enabled=True), "ru")
        assert "Все значения на кнопках — как в общих настройках." in screen.text
        assert "Как в общих настройках: " not in screen.text


def _common_head(lang: str) -> str:
    return screens.COMMON_NOTE(lang).split("{", 1)[0]


class TestTheKnowledgeBaseSaysCommonInTheText:
    """The knowledge base has a row of its own since the organizers moved to the
    chat's root; it says where its value comes from in the same line as every
    other switch."""

    def test_the_switch_is_named_as_everywhere_else(self) -> None:
        assert screens.LABELS["kb"]("ru") == "База знаний"

    @pytest.mark.parametrize("on", [True, False])
    def test_the_button_shows_the_value_alone(self, on: bool) -> None:
        screen = screens.build_memory(_view(_null("kb"), kb_enabled=on), "ru")

        assert _switches(screen)["kb"] == f"{'✅' if on else '⚪'} База знаний"

    @pytest.mark.parametrize("on", [True, False])
    def test_inherited_is_named_in_the_line(self, on: bool) -> None:
        screen = screens.build_memory(_view(_null("kb"), kb_enabled=on), "ru")

        assert "База знаний" in _common_line(screen.text)

    def test_set_for_the_chat_is_not_named(self) -> None:
        screen = screens.build_memory(_view({"kb_enabled": True}, kb_enabled=True), "ru")

        assert _switches(screen)["kb"] == "✅ База знаний"
        assert "База знаний" not in _common_line(screen.text)

    @pytest.mark.parametrize("lang", LANGS)
    @pytest.mark.parametrize("on", [True, False])
    @pytest.mark.parametrize("row", [{}, {"kb_enabled": False}])
    def test_every_memory_label_fits_its_row(
        self, lang: str, on: bool, row: dict[str, Any]
    ) -> None:
        """With the margin `fit_label` keeps, not the lint's full share: the
        estimate put «⚪ База знаний · общее» at about 152 of 163 px, the lint
        passed it, and the rendered phone row still cut it."""
        screen = screens.build_memory(_view(row, organizers=12, kb_enabled=on), lang)

        for buttons in screen.rows:
            limit = clip_room(len(buttons))
            for b in buttons:
                assert label_width_px(b.label) <= limit, (b.label, label_width_px(b.label))


# ── the chat's root ───────────────────────────────────────────────────────


def _callbacks(screen: Screen) -> list[str]:
    return [b.callback for row in screen.rows for b in row if b.callback]


class TestTheChatRoot:
    @pytest.mark.parametrize("lang", LANGS)
    def test_the_organizers_are_on_the_root_and_not_under_memory(self, lang: str) -> None:
        view = _view({}, organizers=3)
        organizers = [
            b
            for row in screens.build_chat(view, lang).rows
            for b in row
            if b.callback == f"adm_kb_orgs:{lang}:{CHAT_ID}:0"
        ]

        assert [b.label for b in organizers] == [screens._ORGANIZERS(lang).format(n=3)]
        assert not [c for c in _callbacks(screens.build_memory(view, lang)) if "adm_kb_orgs:" in c]

    @pytest.mark.parametrize("lang", LANGS)
    def test_four_action_rows_of_up_to_three_that_fit_the_phone(self, lang: str) -> None:
        """Two-digit counts on both counted buttons: the widest the root gets."""
        view = ChatView(
            chat_id=CHAT_ID,
            title="Тестовый чат",
            chat_type="supergroup",
            values=config_values(ChatConfig(chat_id=CHAT_ID, enabled=True)),
            row={},
            rules=12,
            organizers=12,
        )
        screen = screens.build_chat(view, lang)

        *actions, last = screen.rows
        assert len(actions) <= 4
        # «Отключить бота» sits by the way out: in the section grid it read as a state.
        assert [b.kind for b in last] == ["action", "back"]
        assert last[0].callback == f"adm_wl_rm_ask:{lang}:{CHAT_ID}:0"
        for buttons in (*actions, last):
            assert len(buttons) <= 3
            for b in buttons:
                assert label_width_px(b.label) <= clip_room(len(buttons)), b.label

    def test_newcomers_are_named_where_a_reader_looks_for_them(self) -> None:
        """«Сообщество» was guessed by every reader looking for "greet newcomers"."""
        title = screens.SECTION_TITLES["community"]
        view = _view({})

        assert title("ru") == "👋 Новички и уходы"
        assert title("ru") in [b.label for row in screens.build_chat(view, "ru").rows for b in row]
        assert screens.build_community(view, "ru").text.startswith("👋 <b>Новички и уходы</b> · ")
        assert screens.build_community(view, "en").text.startswith(
            "👋 <b>Joining and leaving</b> · "
        )


# ── the chat list ─────────────────────────────────────────────────────────


def _chats(n: int) -> list[dict[str, Any]]:
    return [{"chat_id": -1000 - i, "chat_title": f"Чат {i}"} for i in range(n)]


class TestTheChatList:
    @pytest.mark.parametrize(
        ("lang", "page", "listed", "requests", "line"),
        [
            ("ru", 0, 3, 1, "Подключено: 5, показаны 1–3."),
            ("ru", 1, 2, 1, "Подключено: 5, показаны 4–5."),
            ("ru", 1, 1, 0, "Подключено: 5, показан 5-й."),
            ("en", 0, 4, 0, "Connected: 5, showing 1–4."),
        ],
    )
    def test_more_than_one_page_says_which_chats_this_one_lists(
        self, lang: str, page: int, listed: int, requests: int, line: str
    ) -> None:
        screen = screens.build_chats(
            lang, _chats(listed), total=5, page=page, pages=2, requests=requests
        )

        assert line in screen.text

    def test_one_page_says_only_how_many(self) -> None:
        screen = screens.build_chats("ru", _chats(3), total=3, page=0, pages=1, rejected=2)

        assert "Подключено: 3." in screen.text
        assert "показан" not in screen.text

    @pytest.mark.parametrize("lang", LANGS)
    def test_the_full_names_fit_with_two_digit_counts(self, lang: str) -> None:
        """«🙏 Запросы доступа · 12» needs the row to itself; «🚫 Отклонённые»
        shares the last row with the way back."""
        for total in (0, 5):
            screen = screens.build_chats(
                lang,
                _chats(3 if total else 0),
                total=total,
                page=0,
                pages=2,
                requests=12,
                rejected=12,
            )
            assert [b.callback for b in screen.rows[0]] == [f"adm_wl_pending:{lang}:0"]
            assert [b.callback for b in screen.rows[-1]] == [
                f"adm_wl_rejected:{lang}:0",
                f"adm_menu:{lang}",
            ]
            for buttons in screen.rows:
                for b in buttons:
                    assert label_width_px(b.label) <= clip_room(len(buttons)), b.label

    def test_rejected_chats_cost_no_chat_row(self) -> None:
        assert screens.chats_per_page(0, 0) == screens.chats_per_page(0, 3) == 4
        assert screens.chats_per_page(2, 0) == screens.chats_per_page(2, 3) == 3


# ── what the community text says ──────────────────────────────────────────

_COMMUNITY_CODES = ("cm", "gf", "wb", "fp", "fd", "rj", "aw")
_COMBOS = [
    dict(zip(_COMMUNITY_CODES, bits, strict=True))
    for bits in itertools.product((False, True), repeat=len(_COMMUNITY_CODES))
]


def _own(flags: dict[str, bool]) -> dict[str, bool]:
    return {FIELDS_BY_CODE[code].key: on for code, on in flags.items()}


def _community(flags: dict[str, bool], lang: str) -> str:
    own = _own(flags)
    return screens.build_community(_view(own, **own), lang).text


_NAME_PROMISES: dict[str, str] = {
    # name on the button -> the words that would say the bot does what it names
    "Режим сообщества": "— здоровается",
    "Ждать сообщения": "первом сообщении",
    "Возврат": "«с возвращением»",
    "Прощание": "прощается в чате",
    "Уходы": "в личку, кто ушёл",
    "Шутка": "шутит",
    "Советы": "советы",
    "Community mode": "— greets",
    "Wait for a message": "first message",
    "Welcome back": "“welcome back”",
    "Farewell": "says goodbye",
    "Departures": "message about who left",
    "Joke": "jokes",
    "Advice": "advice",
    # The name the greeting switch had: under ⚪ it promised no greeting.
    "Новичок": "с новичком",
    "Newcomer": "a newcomer",
}


def _facts(flags: dict[str, bool]) -> dict[Any, bool]:
    """Each line the community text may say -> whether these switches make it true
    (``services/community.py``: a part acts only while the mode is on; the advice
    watcher reads only its own flag)."""
    cm = flags["cm"]
    return {
        screens._COMMUNITY_OFF: not cm,
        screens._GREETS_FIRST: cm and flags["gf"],
        screens._GREETS_JOIN: cm and not flags["gf"],
        screens._WELCOMES_BACK: cm and flags["wb"],
        screens._SAYS_GOODBYE: cm and flags["fp"],
        screens._JOKES: cm and flags["rj"],
        screens._TELLS_YOU: cm and flags["fd"],
        screens._WATCHES_ADVICE: flags["aw"],
    }


class TestTheCommunityTextSaysOnlyWhatIsOn:
    """The toggles name the parts; the text says what is in force now. A line
    describing a part whose toggle is off («Прощание: прощается в чате» under
    ⚪ Прощание) reads as a fact the toggle denies."""

    @pytest.mark.parametrize("lang", LANGS)
    def test_every_line_is_there_exactly_when_it_is_true(self, lang: str) -> None:
        for flags in _COMBOS:
            text = _community(flags, lang)
            for line, true in _facts(flags).items():
                assert (line(lang) in text) is true, (flags, line(lang), text)

    @pytest.mark.parametrize("lang", LANGS)
    def test_no_line_repeats_a_toggles_name_as_a_heading(self, lang: str) -> None:
        names = [screens.LABELS[code](lang) for code in _COMMUNITY_CODES]
        for flags in _COMBOS:
            for line in _community(flags, lang).split("\n"):
                assert not any(line.startswith(f"{name}:") for name in names), (flags, line)

    def test_farewell_in_the_chat_is_said_only_while_it_is_on(self) -> None:
        """The round-3 reading, as a reader asks it: «попрощается ли бот в чате?»"""
        for flags in _COMBOS:
            said = "прощается в чате" in _community(flags, "ru")
            assert said is (flags["cm"] and flags["fp"]), flags

    @pytest.mark.parametrize("lang", LANGS)
    def test_no_line_says_the_bot_does_what_an_off_switch_names(self, lang: str) -> None:
        """A reader takes a switch's name for what it does: «⚪ Новичок» read
        "no newcomer greeting" while the text rightly said the bot greets on
        joining — and the only switch that stops greetings is the mode itself.
        Read off the buttons drawn, for every combination, inherited or the
        chat's own; a name missing from `_NAME_PROMISES` fails, so a renamed
        switch has to say what its new name promises."""
        for flags in _COMBOS:
            own = _own(flags)
            for row in (own, {}):
                screen = screens.build_community(_view(row, **own), lang)
                for label in _switches(screen).values():
                    if not label.startswith("⚪ "):
                        continue
                    name = label.removeprefix("⚪ ")
                    assert name in _NAME_PROMISES, f"say what «{name}» promises"
                    assert _NAME_PROMISES[name] not in screen.text, (flags, name, screen.text)

    def test_the_greeting_line_readers_rely_on_is_kept(self) -> None:
        on = dict.fromkeys(_COMMUNITY_CODES, False) | {"cm": True, "gf": True}
        assert "на его первом сообщении, а не при входе" in _community(on, "ru")

    @pytest.mark.parametrize("lang", LANGS)
    def test_every_label_fits_its_row(self, lang: str) -> None:
        for flags in _COMBOS:
            own = {FIELDS_BY_CODE[code].key: on for code, on in flags.items()}
            for inherited in (own, {}):
                screen = screens.build_community(_view(inherited, **own), lang)
                for buttons in screen.rows:
                    for b in buttons:
                        assert label_width_px(b.label) <= clip_room(len(buttons)), b.label


# ── round 4: what ✅/⚪ mean and whose value a tap changes ─────────────────


_SWITCH_SCREENS = ("behavior", "memory", "voice", "stickers", "reactions")


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("name", _SWITCH_SCREENS)
def test_every_switch_screen_says_what_the_two_marks_mean(name: str, lang: str) -> None:
    """All five readers of round 4 guessed ⚪; none could tell it from "not set"."""
    screen = getattr(screens, f"build_{name}")(_view({}), lang)

    assert _switches(screen), "the control: this screen has switches"
    assert screens.TAP_TOGGLES(lang) in screen.text
    assert screen.text.count(screens.TAP_TOGGLES(lang)) == 1


@pytest.mark.parametrize(
    ("lang", "only_here"),
    [
        ("ru", "Изменение здесь касается только этого чата."),
        ("en", "A change here applies to this chat only."),
    ],
)
def test_the_origin_line_says_a_change_stays_in_this_chat(lang: str, only_here: str) -> None:
    """Both forms: some values inherited, and every one of them."""
    some = screens.build_behavior(_view(_null("rg")), lang)
    every = screens.build_stickers(_view(_null("sl", "sr", "ic", "tol")), lang)
    own = screens.build_behavior(
        _view({FIELDS_BY_CODE[code].key: True for code in ("rg", "af", "lc")}), lang
    )

    assert screens.COMMON_NOTE(lang).split(":", 1)[0] in some.text
    assert only_here in some.text
    assert only_here in every.text
    # No inherited value, no origin line: the sentence is about the common values.
    assert only_here not in own.text


@pytest.mark.parametrize(("lang", "hint"), [("ru", "Нажмите кнопку"), ("en", "Tap a button")])
def test_the_sticker_chances_say_a_tap_picks_how_often(lang: str, hint: str) -> None:
    screen = screens.build_sticker_chances(_view({}), lang)

    assert hint in screen.text
    assert any(
        b.callback and b.callback.startswith("adm_pnl_pct:") for row in screen.rows for b in row
    )
