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
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers import rules as rule_handlers
from src.bot.screens import rules
from src.bot.screens.model import Screen
from src.bot.screens.registry import Sample, load_all
from src.bot.screens.samples import fake
from src.bot.screens.samples import rules as rule_samples
from src.models.rules import RulesMode
from src.services.rules.engine import RuleEngine

LANGS = ("ru", "en")
WEIGHT_MODES = (RulesMode.HIGHEST_WEIGHT.value, RulesMode.WEIGHTED_RANDOM.value)
PAUSED = {"ru": "на паузе", "en": "paused"}
"""The row's word for a paused rule, as a reader sees it."""

_SAMPLES: list[Sample] = [
    item
    for variants in load_all().values()
    for item in variants
    if item.build.__module__ == rule_samples.__name__
]

_OLD_WORD = re.compile(r"(?<!\w)(?:вес|веса|весу|весом|весе|весам|весами)(?!\w)", re.IGNORECASE)
"""«вес» in any case form. English says "priority" too (`rules.PRIORITY`)."""


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
    """The outcome line answers the question with the rows' own words.

    The mode's name is not above it: «Режим: одно правило…» over «срабатывают все
    обязательные и одно…» read as a contradiction to all five readers of round 4."""
    lines = _list(lang, mode).text.splitlines()
    assert rules.mode_line(mode, lang) not in lines
    assert not any(ln.startswith(rules.mode_line(mode, lang).split(":")[0] + ":") for ln in lines)
    line = rules.several_line(mode, lang)
    assert line in lines, lines
    mandatory = rules.MANDATORY(lang).casefold()[:-2]  # «обязательн…», «require…»
    assert mandatory in line.casefold(), line
    assert rules.PRIORITY(lang).casefold() in line.casefold(), line
    # Which way a priority points: nobody in round 4 could tell whether 2 beats 1.
    assert rules._HIGHER(lang) in line, line
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


_ENGINE_SAYS = {
    RulesMode.ALL.value: (
        {1, 2, 3},
        3,
        "Если сообщение подходит под несколько правил, срабатывают все.",
    ),
    RulesMode.HIGHEST_WEIGHT.value: (
        {1, 2},
        2,
        "Если сообщение подходит под несколько правил, срабатывают все обязательные "
        "и одно из остальных — с наибольшим приоритетом (больше число — выше приоритет).",
    ),
    RulesMode.WEIGHTED_RANDOM.value: (
        {1},
        2,
        "Если сообщение подходит под несколько правил, срабатывают все обязательные "
        "и одно случайное из остальных: чем выше приоритет, тем вероятнее "
        "(больше число — выше приоритет).",
    ),
}
"""Mode → rules that always act, how many act, and the line that says so.

Rule 1 is mandatory with priority 1; rules 2 and 3 are optional, priorities 3
and 1. The line is pinned word for word: a sentence that keeps the keywords and
says something else («срабатывает одно обязательное…») must not pass."""


async def _acting(mode: str) -> list[int]:
    """Which of the three rules the real engine fires for one message matching all three."""

    def row(rule_id: int, *, weight: int, mandatory: bool = False) -> dict[str, object]:
        return {
            "id": rule_id,
            "chat_id": fake.CHAT_ID,
            "rule_type": "keyword_trigger",
            "config": {"keywords": ["x"], "action": "notify_admin"},
            "weight": weight,
            "mandatory": mandatory,
            "enabled": True,
            "status": "active",
            "trigger_count": 0,
        }

    repo = AsyncMock()
    repo.get_active_rules = AsyncMock(
        return_value=[row(1, weight=1, mandatory=True), row(2, weight=3), row(3, weight=1)]
    )
    message = MagicMock(text="x", caption=None, sticker=None)
    actions = await RuleEngine(repo).evaluate(
        chat_id=fake.CHAT_ID, user_id=7, message=message, rules_mode=mode
    )
    return [action.rule.id for action in actions]


@pytest.mark.parametrize("mode", list(_ENGINE_SAYS))
async def test_the_several_rules_line_says_what_the_engine_does(mode: str):
    always, count, said = _ENGINE_SAYS[mode]
    for _ in range(8):  # weighted_random draws: every draw keeps the mandatory one
        acting = await _acting(mode)
        assert (always <= set(acting), len(acting)) == (True, count), (mode, acting)
    assert rules.several_line(mode, "ru") == said
    assert said in _list("ru", mode).text.splitlines()


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


@pytest.mark.parametrize("lang", LANGS)
def test_turning_rules_on_does_not_promise_to_wake_a_paused_rule(lang: str):
    """The switch flips the chat's ``rules_enabled`` only (``handle_rules_all``): a
    paused rule stays paused, and the line that says what a tap leaves says so.
    Turning them off needs no such word: every rule stops."""
    assert PAUSED[lang] in rules.switch_hint(False, lang)
    assert PAUSED[lang] not in rules.switch_hint(True, lang)


# ---------------------------------------------------------------------------
# What each action sends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_every_action_has_a_line_saying_what_the_bot_sends(lang: str):
    screen = rules.action_screen(lang, fake.CHAT_ID, rule_samples.WORDS_NAME)
    lines = screen.text.splitlines()
    labels = [b.label for row in screen.rows for b in row if b.kind != "cancel"]
    # A warning and a reply are one choice: both reply to the author (rounds 3 and 4).
    assert len(labels) == 3, labels
    said = {label: [ln for ln in lines if ln.startswith(f"{label} — ")] for label in labels}
    assert {label: len(found) for label, found in said.items()} == dict.fromkeys(labels, 1), lines
    # The reply names a ready text, so both of its ways are on the line.
    reply = next(found[0] for label, found in said.items() if label.startswith("💬"))
    assert f"«{rules.WARN_PRESETS[0](lang)}»" in reply, reply


def test_messaging_the_admin_says_every_admin_gets_it():
    """The executor sends the card to every id in ``admin_ids``, not to the reader alone."""
    text = rules.action_screen("ru", fake.CHAT_ID, rule_samples.WORDS_NAME).text
    (line,) = [ln for ln in text.splitlines() if ln.startswith("🔔 Написать админу — ")]
    assert "админам бота" in line and "вам" not in line.split(), line


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


# ---------------------------------------------------------------------------
# A rule's row: the automatic names whole
# ---------------------------------------------------------------------------


def _auto_names(lang: str) -> list[str]:
    """Every name the builder gives a counting rule (``handle_builder_count``)."""
    return [
        name(lang).format(n=n)
        for name, choices in (
            (rule_handlers.SPAM_NAME, rules.SPAM_CHOICES),
            (rule_handlers.FLOOD_NAME, rules.FLOOD_CHOICES),
        )
        for n in choices
    ]


def _row(lang: str, rule: rules.RuleView, chat: rules.ChatRules) -> str:
    """The rule's button as the list draws it (the row under the switch)."""
    screen = rules.rules_screen(
        lang, chat_id=fake.CHAT_ID, title=fake.CHAT_TITLE, chat=chat, rules=[rule]
    )
    (button,) = screen.rows[1]
    assert button.callback.startswith("ar_view:"), button
    return button.label


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("mode", [RulesMode.ALL.value, *WEIGHT_MODES])
def test_an_automatic_name_is_never_cut_on_the_list(lang: str, mode: str):
    """«Сообщения: 20 за минуту» cut to «Сообщения: 20 за…» loses what the rule counts.

    Every state a row can show, in every mode: the chat's switch on and off, the
    rule working and paused, mandatory and two priorities."""
    cut = []
    for name in _auto_names(lang):
        for chat_on in (True, False):
            chat = rules.ChatRules(enabled=chat_on, common=False, mode=mode)
            for enabled in (True, False):
                for mandatory, weight in ((True, 1), (False, 1), (False, 10)):
                    rule = _rule(
                        1,
                        rule_type="spam_detect",
                        config={"name": name, "action": "notify_admin"},
                        enabled=enabled,
                        mandatory=mandatory,
                        weight=weight,
                    )
                    label = _row(lang, rule, chat)
                    if name not in label:
                        cut.append(label)
    assert cut == []


def test_the_list_samples_show_the_automatic_name_whole():
    labels = [
        b.label
        for item in _SAMPLES
        if item.screen_id == "rules"
        for row in item.build("ru").rows
        for b in row
        if b.callback and b.callback.startswith("ar_view:")
    ]
    spam = [label for label in labels if rule_samples.SPAM_NAME[:10] in label]
    assert spam, labels
    assert [label for label in spam if rule_samples.SPAM_NAME not in label] == []


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("mode", WEIGHT_MODES)
def test_a_paused_rule_is_not_tagged_as_always_acting(lang: str, mode: str):
    """The line under the mode says a mandatory rule always acts; a paused one does
    not act at all, so its row says it is paused and nothing about acting."""
    chat = rules.ChatRules(enabled=True, common=False, mode=mode)
    label = _row(lang, _rule(1, mandatory=True, enabled=False), chat)
    assert PAUSED[lang] in label and rules.MANDATORY(lang) not in label, label


@pytest.mark.parametrize("lang", LANGS)
def test_a_random_mode_sample_is_drawn(lang: str):
    """Its line is the longest the list says: the dump and the PNGs must show it."""
    line = rules.several_line(RulesMode.WEIGHTED_RANDOM.value, lang)
    drawn = [item.title for item in _SAMPLES if line in item.build(lang).text]
    assert drawn, "no sample draws the list under weighted_random"


# ---------------------------------------------------------------------------
# One reply to the author (round 4, R4): the ⚠️ and 💬 choices were one action
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_the_reply_screen_offers_the_ready_texts_and_the_placeholders(lang: str) -> None:
    """A tap on a ready text saves the rule; the placeholders are for a typed one."""
    screen = rules.text_screen(lang, fake.CHAT_ID, "spam", action="custom_response")

    *texts, last = screen.rows
    assert [row[0].label for row in texts] == [preset(lang) for preset in rules.WARN_PRESETS]
    assert [row[0].callback for row in texts] == [
        f"ar_b_txt:{lang}:{fake.CHAT_ID}:{i}" for i in range(len(rules.WARN_PRESETS))
    ]
    assert [b.kind for b in last] == ["cancel"]
    for placeholder in ("{first_name}", "{username}", "{rule_name}"):
        assert f"<code>{placeholder}</code>" in screen.text


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_both_reply_actions_read_as_one_verb(lang: str) -> None:
    """A rule saved as warn_user and one saved as custom_response say the same thing."""
    warn = _rule(
        1, config={"name": "a", "keywords": ["x"], "action": "warn_user", "warning_message": "Тише"}
    )
    reply = _rule(
        2,
        config={
            "name": "b",
            "keywords": ["x"],
            "action": "custom_response",
            "response_template": "Тише",
        },
    )

    assert rules.does(warn, lang) == rules.does(reply, lang)
    assert rules.does(warn, lang).startswith(rules._REPLIES(lang))


@pytest.mark.parametrize(("lang", "said"), [("ru", "ничего"), ("en", "nothing")])
def test_an_empty_reply_says_the_bot_sends_nothing(lang: str, said: str) -> None:
    """`_custom_response` sends nothing for an empty template; the rule must not claim a reply."""
    empty = _rule(
        1,
        config={
            "name": "a",
            "keywords": ["x"],
            "action": "custom_response",
            "response_template": "",
        },
    )

    assert rules.does(empty, lang).startswith(said)
    assert rules._REPLIES(lang) not in rules.does(empty, lang)
