"""The chat sections' pure builders, called directly.

The handler tests reach these builders through a mocked repository and check
a label or two; the sample screens cover one view each. What only a builder
call can pin is the shape across states: which switches a section hides,
and which labels may say ``· общее``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.bot.screens import chat as screens
from src.bot.screens.chat import ChatView, config_values
from src.bot.screens.lint import budget, label_width_px
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


# ── · общее ──────────────────────────────────────────────────────────────


_SECTIONS = ("behavior", "memory", "voice", "stickers")


def _all_switches(row: dict[str, Any], lang: str, **values: Any) -> dict[str, str]:
    view = _view(row, **values)
    found: dict[str, str] = {}
    for section in _SECTIONS:
        found.update(_switches(screens.SECTION_BUILDERS[section](view, lang)))
    return found


class TestOnlyInheritingSwitchesSayCommon:
    """A legacy column's NULL is not "inherited" (its DEFAULT fills new rows),
    so its button never says ``· общее``; the inheriting switches on the same
    screens, NULL in the same row, do — that is the control that the marker
    is reachable at all."""

    @pytest.mark.parametrize("lang", LANGS)
    def test_legacy_nulls_are_unmarked_and_inheriting_nulls_are_marked(self, lang: str) -> None:
        common = screens.COMMON(lang)
        labels = _all_switches(_null(*LEGACY_CODES, *INHERITING_CODES, "kb"), lang)

        assert set(LEGACY_CODES) | set(INHERITING_CODES) <= set(labels)
        for code in LEGACY_CODES:
            assert common not in labels[code], (code, labels[code])
        for code in INHERITING_CODES:
            assert labels[code].endswith(common), (code, labels[code])

    @pytest.mark.parametrize("lang", LANGS)
    def test_a_chat_that_set_its_own_values_has_no_marker(self, lang: str) -> None:
        own = {FIELDS_BY_CODE[code].key: True for code in (*INHERITING_CODES, "kb")}
        own["tolerance_level"] = 0.5

        labels = _all_switches({**_null(*LEGACY_CODES), **own}, lang)

        assert not [label for label in labels.values() if screens.COMMON(lang) in label]


class TestTheKnowledgeBaseSaysCommonInTheText:
    """«База знаний · общее» does not fit half a phone row next to the
    organizers button, so the memory section says it in its footnote."""

    def test_the_switch_is_named_as_everywhere_else(self) -> None:
        assert screens.LABELS["kb"]("ru") == "База знаний"

    @pytest.mark.parametrize(
        ("on", "line"),
        [
            (True, "База знаний включена в общих настройках."),
            (False, "База знаний выключена в общих настройках."),
        ],
    )
    def test_inherited_goes_to_the_footnote_with_its_value(self, on: bool, line: str) -> None:
        screen = screens.build_memory(_view(_null("kb"), kb_enabled=on), "ru")

        label = _switches(screen)["kb"]
        assert label == f"{'✅' if on else '⚪'} База знаний"
        assert line in screen.text

    def test_set_for_the_chat_says_nothing_about_common(self) -> None:
        screen = screens.build_memory(_view({"kb_enabled": True}, kb_enabled=True), "ru")

        assert "общих настройках" not in screen.text

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
            limit = budget(len(buttons)) * screens._FIT_MARGIN
            for b in buttons:
                assert label_width_px(b.label) <= limit, (b.label, label_width_px(b.label))
