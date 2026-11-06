"""A switch is called the same on the defaults screen and on a chat's own panel.

The two screens used to keep their own label tables, and they drifted: the same
switch read «Ставить реакции» for all chats and «Реакции в чате» for one chat,
«Возвращение» on one and «Возврат» on the other. An admin who sets a default
and then looks at a chat cannot tell they are one setting. The walk below
reads the labels off the BUTTONS each screen draws, not off the tables, so a
screen that stopped reading `chat.LABELS` fails here.

The one allowed difference is the per-chat ``· общее`` suffix: on a chat's
panel it says the value comes from the defaults.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.bot.screens.chat import (
    COMMON,
    COMMUNITY_LINES,
    LABELS,
    SECTION_BUILDERS,
    ChatView,
    config_values,
)
from src.bot.screens.samples import fake
from src.bot.screens.settings import KEY_OF, SECTION_TOGGLES, defs_section_screen
from src.bot.settings_fields import FIELDS_BY_CODE
from src.models.chat_config import ChatConfig

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
    suffix = COMMON(lang)

    # Not vacuous: the walk saw every switch of the defaults screen, and every
    # one but the rules switch (the rules area draws its own) on the panel too.
    assert set(defaults) == {code for rows in SECTION_TOGGLES.values() for r in rows for code in r}
    assert set(defaults) & set(panel) == set(defaults) - {"re"}
    # ...and the inherited form was drawn, so the suffix branch below ran.
    assert any(name.endswith(suffix) for names in panel.values() for name in names)

    differ = {
        code: (defaults[code], sorted(panel[code]))
        for code in set(defaults) & set(panel)
        if not panel[code] <= {defaults[code], defaults[code] + suffix}
    }
    assert differ == {}


@pytest.mark.parametrize("lang", LANGS)
def test_the_defaults_screen_reads_the_panel_labels(lang: str):
    """The rules switch is on the defaults screen only: it still has to be the panel's word."""
    defaults = _defaults(lang)
    assert {c: n for c, n in defaults.items() if n != LABELS[c](lang)} == {}


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
