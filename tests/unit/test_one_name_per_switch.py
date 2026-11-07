"""A switch is called the same on the defaults screen and on a chat's own panel.

The two screens used to keep their own label tables, and they drifted: the same
switch read «Ставить реакции» for all chats and «Реакции в чате» for one chat,
«Возвращение» on one and «Возврат» on the other. An admin who sets a default
and then looks at a chat cannot tell they are one setting. The walk below
reads the labels off the BUTTONS each screen draws, not off the tables, so a
screen that stopped reading `chat.LABELS` fails here.

No difference is allowed, not even for a value the chat takes from the
defaults: its button reads exactly as the defaults screen's, and the text says
where the value comes from («Как в общих настройках: …»). A suffix on the
button («⚪ … · общее») read as "off, or not set?" and hid the value.

The rules switch lives on the rules area's own screens, not on the panel, and
says its state in one word after the panel's noun («Правила включены»). That
word is the same on the defaults screen, the chat's rules, a rule's card and
the notice after a tap. The rules mode and the sticker tolerance are not
switches, but the same holds: one line on every screen that shows them.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.bot.screens import rules, settings
from src.bot.screens.chat import (
    COMMON_NOTE,
    COMMUNITY_LINES,
    LABELS,
    SECTION_BUILDERS,
    ChatView,
    config_values,
)
from src.bot.screens.i18n import Tr
from src.bot.screens.samples import fake
from src.bot.screens.settings import KEY_OF, SECTION_TOGGLES, defs_section_screen
from src.bot.settings_fields import FIELDS_BY_CODE
from src.models.chat_config import ChatConfig

_OLD_SUFFIX = Tr(" · общее", " · all")
"""What a button once added for an inherited value; it read as part of the state."""

LANGS = ("ru", "en")
GLYPHS = ("✅ ", "⚪ ")


def _name(label: str) -> str:
    for glyph in GLYPHS:
        if label.startswith(glyph):
            return label.removeprefix(glyph)
    raise AssertionError(f"a switch without its state glyph: {label!r}")


def _defaults(lang: str) -> dict[str, str]:
    """code -> name, read off the defaults screen of every section."""
    names: dict[str, str] = {}
    values = dict.fromkeys(KEY_OF.values(), True)
    for section in SECTION_TOGGLES:
        for row in defs_section_screen(lang, section, values).rows:
            for button in row:
                if button.callback and button.callback.startswith("adm_defs_tgl:"):
                    names[button.callback.split(":")[2]] = _name(button.label)
    return names


def _all_on(row: dict[str, Any]) -> ChatView:
    """A chat with every switch on, so every follower switch is drawn too."""
    on = {FIELDS_BY_CODE[code].key: True for code in LABELS}
    return ChatView(
        chat_id=fake.CHAT_ID,
        title=fake.CHAT_TITLE,
        chat_type="supergroup",
        values=config_values(ChatConfig(chat_id=fake.CHAT_ID, enabled=True), **on),
        row=row,
    )


def _panel(lang: str) -> dict[str, set[str]]:
    """code -> every name a chat's panel gives it, inherited and own values both."""
    names: dict[str, set[str]] = {}
    own = {FIELDS_BY_CODE[code].key: True for code in LABELS}
    for row in ({}, own):
        view = _all_on(row)
        for build in SECTION_BUILDERS.values():
            for buttons in build(view, lang).rows:
                for button in buttons:
                    if button.callback and button.callback.startswith("adm_pnl_tgl:"):
                        code = button.callback.split(":")[3]
                        names.setdefault(code, set()).add(_name(button.label))
    return names


@pytest.mark.parametrize("lang", LANGS)
def test_every_switch_on_both_screens_has_one_name(lang: str):
    defaults, panel = _defaults(lang), _panel(lang)
    suffix = _OLD_SUFFIX(lang)

    # Not vacuous: the walk saw every switch of the defaults screen, and every
    # one but the rules switch (the rules area draws its own) on the panel too.
    assert set(defaults) == {code for rows in SECTION_TOGGLES.values() for r in rows for code in r}
    assert set(defaults) & set(panel) == set(defaults) - {"re"}
    # ...and the inherited form was drawn: its text says so, its buttons do not.
    head = COMMON_NOTE(lang).split("{", 1)[0]
    inherited = _all_on({})
    assert any(head in build(inherited, lang).text for build in SECTION_BUILDERS.values())
    assert not any(suffix in name for names in panel.values() for name in names)

    differ = {
        code: (defaults[code], sorted(panel[code]))
        for code in set(defaults) & set(panel)
        if panel[code] != {defaults[code]}
    }
    assert differ == {}


@pytest.mark.parametrize("lang", LANGS)
def test_the_defaults_screen_reads_the_panel_labels(lang: str):
    """The rules switch is on the defaults screen only: it still has to be the panel's word."""
    defaults = _defaults(lang)
    assert {c: n for c, n in defaults.items() if c != "re" and n != LABELS[c](lang)} == {}
    assert defaults["re"].startswith(f"{LABELS['re'](lang)} "), defaults["re"]


_RULE = rules.RuleView(id=1, chat_id=fake.CHAT_ID, rule_type="spam_detect")


def _defaults_rules(lang: str, enabled: bool, mode: str = "all") -> Any:
    return defs_section_screen(lang, "rules", {KEY_OF["re"]: enabled, "rules_mode": mode})


def _rules_area(lang: str, chat: rules.ChatRules) -> list[Any]:
    """The chat's rules and a rule's card, as the rules area draws them."""
    return [
        rules.rules_screen(
            lang, chat_id=fake.CHAT_ID, title=fake.CHAT_TITLE, chat=chat, rules=[_RULE]
        ),
        rules.rule_screen(lang, _RULE, chat=chat),
    ]


def _switch(screen: Any) -> list[str]:
    return [
        b.label
        for row in screen.rows
        for b in row
        if b.callback and b.callback.startswith(("ar_all:", "adm_defs_tgl:"))
    ]


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("enabled", [True, False], ids=["on", "off"])
def test_the_rules_switch_has_one_name_on_every_surface(lang: str, enabled: bool):
    (default,) = _switch(_defaults_rules(lang, enabled))
    name = _name(default)
    head = COMMON_NOTE(lang).split("{", 1)[0]

    seen: set[str] = set()
    says_common: dict[bool, bool] = {}
    for common in (True, False):
        area = _rules_area(lang, rules.ChatRules(enabled=enabled, common=common))
        for screen in area:
            seen.update(_switch(screen))
        says_common[common] = any(head in screen.text for screen in area)
    # Not vacuous: both forms were drawn, and only the inherited one names its origin.
    assert says_common == {True: True, False: False}
    assert {_name(label) for label in seen} == {name}, seen
    assert {label[:1] for label in seen} == {default[:1]}, "the state glyph differs"

    notice = (rules.RULES_ON if enabled else rules.RULES_OFF)(lang)
    assert notice == name, "the notice after a tap is not the switch's own words"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("mode", ["highest_weight", "weighted_random"])
def test_a_rules_mode_reads_the_same_on_the_chats_rules_and_on_the_defaults(lang: str, mode: str):
    """The outcome sentence is one text on both; the mode's name is on the defaults only.

    On a chat's list the name above the sentence read as a contradiction (round 4),
    so that list says the outcome alone."""
    shown = settings._info_lines(lang, "rules", {"rules_mode": mode})
    assert shown == [rules.mode_line(mode, lang), rules.several_line(mode, lang)]
    chat = rules.ChatRules(enabled=True, common=False, mode=mode)
    chat_lines = _rules_area(lang, chat)[0].text.splitlines()
    for line in shown:
        assert line in _defaults_rules(lang, True, mode).text.splitlines()
    assert rules.several_line(mode, lang) in chat_lines
    assert rules.mode_line(mode, lang) not in chat_lines


@pytest.mark.parametrize("lang", LANGS)
def test_every_mode_has_its_name_on_the_defaults(lang: str):
    """``all`` is shown on the defaults only (the chat's rules show weights only for the others)."""
    names = {
        mode: _defaults_rules(lang, True, mode).text.splitlines()[-2]
        for mode in ("all", *rules.MODES)
    }
    # ...and under it, what acts when one message matches several rules.
    assert all(
        _defaults_rules(lang, True, mode).text.splitlines()[-1] == rules.several_line(mode, lang)
        for mode in ("all", *rules.MODES)
    )
    assert len(set(names.values())) == len(names), names
    assert all(line.startswith(names["all"].split(":")[0] + ": ") for line in names.values())
    assert "all" not in names["all"] and "highest_weight" not in names["highest_weight"]


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("tol", [0.5, 0.0, 0.6], ids=["preset", "zero-preset", "custom"])
def test_the_sticker_tolerance_reads_the_panels_value(lang: str, tol: float):
    """The defaults show the panel's words for it (``0.5, обычно``), never a bare preset number."""
    key = FIELDS_BY_CODE["tol"].key
    view = ChatView(
        chat_id=fake.CHAT_ID,
        title=fake.CHAT_TITLE,
        chat_type="supergroup",
        values=config_values(ChatConfig(chat_id=fake.CHAT_ID, enabled=True), **{key: tol}),
        row={key: tol},
    )
    (button,) = [
        b.label
        for row in SECTION_BUILDERS["stickers"](view, lang).rows
        for b in row
        if b.label.startswith("🔞")
    ]
    panel_value = button.split(": ", 1)[1]
    prefix = settings._TOLERANCE(lang).format(v="")
    lines = defs_section_screen(lang, "stickers", {key: tol}).text.splitlines()
    assert prefix + panel_value in lines, (panel_value, lines)


@pytest.mark.parametrize("lang", LANGS)
def test_the_lines_that_explain_a_switch_open_with_its_name(lang: str):
    """The community legend and the sticker chances name the switch they explain."""
    community = defs_section_screen(lang, "community", {}).text
    for code, line in COMMUNITY_LINES:
        assert line(lang).startswith(f"{LABELS[code](lang)}: "), (code, line(lang))
        assert line(lang) in community, code
    stickers = defs_section_screen(lang, "stickers", {}).text.splitlines()
    assert any(s.startswith(f"{LABELS['sr'](lang)}: ") for s in stickers), stickers
    assert any(s.startswith(f"{LABELS['ic'](lang)}: ") for s in stickers), stickers
