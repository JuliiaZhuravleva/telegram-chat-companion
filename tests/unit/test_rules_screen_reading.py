"""What the rules screens have to say so a reader can act without guessing.

Three questions a newcomer brings here: if one message matches two rules, which
act; what the bot sends for each action, the warning and the reply told apart;
and how to turn every rule off. The list used to name the mode in a fragment and
never said that a mandatory rule acts whatever the mode; «вес» means nothing to a
reader who has not seen the JSON; «✅ Правила включены» reads as a state as easily
as a button. And the list samples held the very «реклама, купи» rule the builder
samples make, so a reader asked to create it found it there already.

The engine's own order is the reference (``RuleEngine.evaluate``): every
matching mandatory rule acts; the mode picks only among the optional ones; an
unknown mode counts as ``all``.
"""

from __future__ import annotations

import re

import pytest

from src.bot.screens import rules
from src.bot.screens.model import Screen
from src.bot.screens.registry import Sample, load_all
from src.bot.screens.samples import fake
from src.bot.screens.samples import rules as rule_samples
from src.models.rules import RulesMode

LANGS = ("ru", "en")
WEIGHT_MODES = (RulesMode.HIGHEST_WEIGHT.value, RulesMode.WEIGHTED_RANDOM.value)

_SAMPLES: list[Sample] = [
    item
    for variants in load_all().values()
    for item in variants
    if item.build.__module__ == rule_samples.__name__
]

_OLD_WORD = re.compile(r"(?<!\w)(?:вес|веса|весу|весом|весе|весам|весами)(?!\w)", re.IGNORECASE)
"""«вес» in any case form. English keeps "weight" (`rules.PRIORITY`)."""


def _words(screen: Screen) -> list[str]:
    """Everything a reader sees on ``screen``: its text and every label."""
    return [screen.text, *(b.label for row in screen.rows for b in row)]


def _rule(rule_id: int, **over: object) -> rules.RuleView:
    base: dict[str, object] = {
        "id": rule_id,
        "chat_id": fake.CHAT_ID,
        "rule_type": "keyword_trigger",
        "config": {"name": f"rule {rule_id}", "keywords": ["x"], "action": "notify_admin"},
    }
    base.update(over)
    return rules.RuleView(**base)  # type: ignore[arg-type]


def _list(lang: str, mode: str, *, enabled: bool = True) -> Screen:
    return rules.rules_screen(
        lang,
        chat_id=fake.CHAT_ID,
        title=fake.CHAT_TITLE,
        chat=rules.ChatRules(enabled=enabled, common=False, mode=mode),
        rules=[_rule(1, mandatory=True, weight=3), _rule(2, weight=2), _rule(3)],
    )


# ---------------------------------------------------------------------------
# Two rules match: which act
# ---------------------------------------------------------------------------


def test_the_sample_set_is_not_empty():
    assert len(_SAMPLES) > 20, [s.title for s in _SAMPLES]


@pytest.mark.parametrize("item", _SAMPLES, ids=lambda s: f"{s.screen_id}[{s.title}]")
def test_no_rules_screen_says_ves(item: Sample):
    """The rules area calls it «приоритет» everywhere: one name, and one readers know."""
    screen = item.build("ru")
    hits = [m.group(0) for text in _words(screen) for m in _OLD_WORD.finditer(text)]
    assert hits == [], (item.title, hits)


@pytest.mark.parametrize("lang", LANGS)
def test_the_priority_word_is_the_same_on_every_surface_that_shows_it(lang: str):
    word = rules.PRIORITY(lang).casefold()
    chat = rules.ChatRules(enabled=True, common=False, mode=RulesMode.HIGHEST_WEIGHT.value)
    plain = _rule(2, weight=2)
    rows = [b.label for row in _list(lang, chat.mode).rows for b in row]
    surfaces = {
        "list row": next(label for label in rows if label.endswith(" 2")),
        "card": rules.rule_screen(lang, plain, chat=chat).text,
        "json prompt": rules.cfg_screen(lang, fake.CHAT_ID).text,
        **{f"mode {m}": rules.mode_line(m, lang) for m in WEIGHT_MODES},
    }
    assert {where: word in text.casefold() for where, text in surfaces.items()} == dict.fromkeys(
        surfaces, True
    )


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("mode", WEIGHT_MODES)
def test_a_weight_mode_says_mandatory_rules_always_act(lang: str, mode: str):
    """The line under the mode answers the question with the rows' own words."""
    lines = _list(lang, mode).text.splitlines()
    assert rules.mode_line(mode, lang) in lines
    line = rules.several_line(mode, lang)
    assert line in lines, lines
    mandatory = rules.MANDATORY(lang).casefold()[:-2]  # «обязательн…», «require…»
    assert mandatory in line.casefold(), line
    assert rules.PRIORITY(lang).casefold() in line.casefold(), line
    # The row tag it explains is the same word.
    rows = [b.label for row in _list(lang, mode).rows for b in row]
    assert any(label.endswith(f" · {rules.MANDATORY(lang)}") for label in rows), rows


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("mode", [RulesMode.ALL.value, "made_up"])
def test_every_matching_rule_acts_says_so_without_mandatory(lang: str, mode: str):
    """``all`` (and a mode the engine does not know, which it runs as ``all``)."""
    lines = _list(lang, mode).text.splitlines()
    line = rules.several_line(mode, lang)
    assert line == rules.several_line(RulesMode.ALL.value, lang)
    assert line in lines, lines
    assert rules.MANDATORY(lang).casefold()[:-2] not in line.casefold()


@pytest.mark.parametrize("lang", LANGS)
def test_an_empty_list_does_not_explain_matches(lang: str):
    screen = rules.rules_screen(
        lang,
        chat_id=fake.CHAT_ID,
        title=fake.CHAT_TITLE,
        chat=rules.ChatRules(enabled=True, common=False),
        rules=[],
    )
    assert rules.several_line(RulesMode.ALL.value, lang) not in screen.text


# ---------------------------------------------------------------------------
# The master switch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("enabled", [True, False], ids=["on", "off"])
def test_the_list_calls_the_top_button_a_switch_and_says_what_a_tap_leaves(
    lang: str, enabled: bool
):
    screen = _list(lang, RulesMode.ALL.value, enabled=enabled)
    switch = rules.SWITCH(lang)
    (line,) = [line for line in screen.text.splitlines() if switch in line]
    after = rules.switch_name(not enabled, lang).split()[-1]  # «выключены» / «off»
    assert re.search(rf"(?<!\w){re.escape(after)}(?!\w)", line), line
    assert screen.rows[0][0].callback.startswith("ar_all:")


# ---------------------------------------------------------------------------
# What each action sends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_every_action_has_a_line_saying_what_the_bot_sends(lang: str):
    screen = rules.action_screen(lang, fake.CHAT_ID, rule_samples.WORDS_NAME)
    lines = screen.text.splitlines()
    labels = [b.label for row in screen.rows for b in row if b.kind != "cancel"]
    assert len(labels) == len(rules.ACTIONS)
    said = {label: [ln for ln in lines if ln.startswith(f"{label} — ")] for label in labels}
    assert {label: len(found) for label, found in said.items()} == dict.fromkeys(labels, 1), lines
    # The warning names a ready text, so it is not "your own text" too.
    warn = next(found[0] for label, found in said.items() if label.startswith("⚠️"))
    assert f"«{rules.WARN_PRESETS[0](lang)}»" in warn, warn


def test_the_admin_action_still_says_it_saves_at_once():
    """The sentence that already answered "does it save now?" stays word for word."""
    assert "«Написать админу» сразу сохранит правило." in (
        rules.action_screen("ru", fake.CHAT_ID, rule_samples.WORDS_NAME).text
    )


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_the_list_samples_do_not_already_hold_the_rule_the_builder_samples_make(lang: str):
    """A reader asked to make the builder's rule must not find it in the list already."""
    stems = [word[:5].casefold() for word in rule_samples.WORDS]
    rows = [
        b.label
        for item in _SAMPLES
        if item.screen_id in ("rules", "rule.gone")
        for row in item.build(lang).rows
        for b in row
        if b.callback and b.callback.startswith("ar_view:")
    ]
    assert rows, "no rule rows drawn"
    assert [label for label in rows if any(s in label.casefold() for s in stems)] == []
