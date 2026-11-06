"""The panel's design rules, checked on every registered sample in both languages.

Three kinds of test live here, and each needs the others to mean anything:

* every registered sample (``screens/samples/*.py``) breaks no rule, in ru and en;
* every rule has a known-bad control that MUST be flagged — "no violations"
  is only evidence when the check is shown to say no;
* ten screens copied from the design must pass — a rule that flags the design
  itself is wrong ("does not wrongly refuse").

The rules are in `src.bot.screens.lint`; ``scripts/dump_screens.py`` prints the
same list as warnings.
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from types import MappingProxyType

import pytest

from src.bot.screens import lint
from src.bot.screens.lint import budget, check_screen, label_width_px
from src.bot.screens.model import Button, ReplyButton, Screen
from src.bot.screens.registry import Sample, load_all, make_sample, sample
from src.bot.screens.render import back, cancel, pager
from src.bot.screens.samples.foundation import DESIGN_COPIES
from src.bot.screens.sections import _MEMBERS, SECTION_OF
from src.bot.settings_fields import FIELDS_BY_CODE
from tests.unit.screen_routing import first_match

LANGS = ("ru", "en")
REGISTERED: list[Sample] = [item for variants in load_all().values() for item in variants]


def _rule_ids(found: list[str]) -> set[str]:
    return {v.split(":", 1)[0] for v in found}


def _sample_id(item: Sample) -> str:
    return f"{item.screen_id}[{item.title}]"


# ---------------------------------------------------------------------------
# Every registered sample, both languages
# ---------------------------------------------------------------------------


def test_the_registry_is_not_empty():
    """Without this, an import failure in ``load_all`` would parametrise the
    sample test over nothing and report green."""
    assert any(item.screen_id == "stale" for item in REGISTERED)


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("item", REGISTERED, ids=_sample_id)
def test_sample_follows_the_rules(item: Sample, lang: str):
    screen = item.build(lang)
    assert screen.id == item.screen_id
    assert check_screen(screen, item, lang, route=first_match) == []


@pytest.mark.parametrize("item", [*REGISTERED, *DESIGN_COPIES], ids=_sample_id)
def test_every_exemption_is_needed(item: Sample):
    """An exemption the screen no longer needs hides the next real violation
    of that rule; each one must still fire in at least one language."""
    bare = replace(item, exempt=MappingProxyType({}))
    fired: set[str] = set()
    for lang in LANGS:
        fired |= _rule_ids(check_screen(item.build(lang), bare, lang, route=first_match))
    assert set(item.exempt) <= fired, f"unused exemptions: {set(item.exempt) - fired}"


# ---------------------------------------------------------------------------
# The design itself passes ("does not wrongly refuse")
# ---------------------------------------------------------------------------

DESIGN_IDS = (
    "hub",
    "chats",
    "requests",
    "chat",
    "memory",
    "orgs",
    "rules",
    "stk.card",
    "status",
    "settings",
)


def test_the_ten_design_screens_are_all_copied():
    assert tuple(item.screen_id for item in DESIGN_COPIES) == DESIGN_IDS


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("item", DESIGN_COPIES, ids=_sample_id)
def test_design_copy_passes(item: Sample, lang: str):
    assert check_screen(item.build(lang), item, lang, route=first_match) == []


# ---------------------------------------------------------------------------
# Every design screen id has a sample
# ---------------------------------------------------------------------------

AREA_IDS: dict[str, tuple[str, ...]] = {
    "foundation": ("stale",),
    "hub": (
        "hub",
        "requests",
        "rejected",
        "rejected.forget_ask",
        "chat.off_ask",
        "status",
        "status.bad",
        "stats",
        "costs",
        "costs.verify",
        "costs.missing",
        "costs.error",
        "settings",
        "notif",
        "lang",
        "defs",
        "defs.section",
    ),
    "chat": (
        "chats",
        "chats.empty",
        "panel.noquery",
        "panel.notfound",
        "panel.many",
        "chat",
        "behavior",
        "memory",
        "voice",
        "chat.stickers",
        "tol.prompt",
        "tol.in",
        "tol.invalid",
        "reactions",
        "community",
    ),
    "rules": (
        "rules",
        "rule",
        "rule.gone",
        "rule.del_ask",
        "rule.type",
        "rule.words",
        "rule.match",
        "rule.who",
        "rule.flood",
        "rule.spam",
        "rule.action",
        "rule.text",
        "rule.react",
        "rule.react_in",
        "rule.cfg",
        "rule.cfg_bad",
        "card.rule",
        "rules.warn",
    ),
    "kb": ("orgs", "org.list", "org.add", "org.fail"),
    "stickers": (
        "stk.sets",
        "stk.set",
        "stk.card",
        "stk.score",
        "stk.score_in",
        "stk.clear_ask",
        "stk.analyzing",
        "stk.error",
        "stk.unknown",
        "stk.reply",
        "card.sticker",
    ),
    "entry": (
        "start.member",
        "start.admin",
        "help.group",
        "help.dm",
        "menu.dm",
        "menu.group",
        "group.admin_hint",
        "group.added",
        "card.access",
        "card.access.ok",
        "card.access.no",
        "card.health",
        "card.recovered",
        "card.spend",
        "summary.result",
        "kb.group",
        "kb.dm",
        "callme.usage",
        "callme.set_account",
        "callme.other",
        "callme.cleared_other",
        "callme.nothing_other",
        "summary.invalid",
    ),
}
"""Design screen id → the ``samples/<module>.py`` that must hold its samples.

Keyed by samples module rather than area letter so the test can tell, with
no edit to this file, whether that area has landed: once ``samples/rules.py``
exists, every rules id without a sample is a failure; until then it is an
expected failure that names the owner.
"""


def _owned_ids() -> list[tuple[str, str]]:
    return [(module, screen_id) for module, ids in AREA_IDS.items() for screen_id in ids]


def test_design_ids_are_unique():
    ids = [screen_id for _, screen_id in _owned_ids()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(("module", "screen_id"), _owned_ids(), ids=lambda v: str(v))
def test_every_design_id_has_a_sample(module: str, screen_id: str):
    if screen_id in load_all():
        return
    landed = importlib.util.find_spec(f"src.bot.screens.samples.{module}") is not None
    if not landed:
        pytest.xfail(f"{screen_id}: samples/{module}.py has not landed yet")
    pytest.fail(f"{screen_id} has no sample in samples/{module}.py")


# ---------------------------------------------------------------------------
# Known-bad controls: one per rule, each MUST be flagged
# ---------------------------------------------------------------------------

TITLE = "🧪 <b>Проба</b>"
BACK = back("hub", "adm_menu:ru", "ru")


def _act(label: str = "🔁 Обновить", data: str = "adm_health:ru") -> Button:
    return Button(label, callback=data)


def _panel(*rows: tuple[Button, ...], text: str = TITLE) -> Screen:
    return Screen(id="probe", text=text, rows=(*rows, (BACK,)))


def _flags(
    screen: Screen,
    rule: str,
    *,
    lang: str = "ru",
    item: Sample | None = None,
    route: lint.RouteFn | None = None,
) -> list[str]:
    return [v for v in check_screen(screen, item, lang, route=route) if v.startswith(f"{rule}:")]


def _as(nav: str) -> Sample:
    return make_sample("probe", lambda _lang: _panel(), section="Панель", title="probe", nav=nav)


def test_the_good_control_is_clean():
    """The base every control below breaks in exactly one way."""
    assert check_screen(_panel((_act(),)), None, "ru", route=first_match) == []


def test_r1_four_buttons_in_a_row():
    screen = _panel(tuple(_act() for _ in range(4)))
    assert _flags(screen, "R1") == ["R1: row 1 has 4 buttons"]


def test_r2_rows():
    assert _flags(_panel(*[(_act(),)] * 5), "R2") == ["R2: 6 rows, limit 5"]
    paged = pager(0, 3, lambda page: f"adm_pnl:ru:{page}")
    assert paged is not None
    assert _flags(_panel(*[(_act(),)] * 4, paged), "R2") == [], "a pager row buys one more"
    assert _flags(_panel(*[(_act(),)] * 5, paged), "R2") == ["R2: 7 rows, limit 6"]


def test_r3_panel_back_placement():
    no_back = Screen(id="probe", text=TITLE, rows=((_act(),),))
    assert _flags(no_back, "R3") == ["R3: panel screen has 0 back buttons, needs 1"]
    first = Screen(id="probe", text=TITLE, rows=((BACK,), (_act(),)))
    assert _flags(first, "R3") == ["R3: back is not the last button of the last row"]
    crowded = Screen(id="probe", text=TITLE, rows=((_act(), _act(), BACK),))
    assert _flags(crowded, "R3") == ["R3: the back row holds more than one other button"]
    one_before = Screen(id="probe", text=TITLE, rows=((_act(), BACK),))
    assert _flags(one_before, "R3") == [], "rules: [➕ Добавить | ⬅️ Чат] is allowed"


def test_r3_root_prompt_card():
    close = Button("✖️ Закрыть", callback="adm_close:ru", kind="exit")
    root_with_back = Screen(id="probe", text=TITLE, rows=((close,), (BACK,)))
    assert _flags(root_with_back, "R3", item=_as("root")) == ["R3: root screen has a back button"]
    no_exit = Screen(id="probe", text=TITLE, rows=((_act(),),))
    assert _flags(no_exit, "R3", item=_as("root")) == [
        "R3: root screen needs exactly one ✖️ Закрыть exit"
    ]
    prompt = Screen(id="probe", text=TITLE, rows=((_act(),),))
    assert _flags(prompt, "R3", item=_as("prompt")) == ["R3: prompt has 0 cancel buttons, needs 1"]
    reply_cancel = Screen(id="probe", text=TITLE, reply=((ReplyButton("✖️ Отмена"),),))
    assert _flags(reply_cancel, "R3", item=_as("prompt")) == [], "a reply-keyboard cancel counts"
    inline_cancel = Screen(id="probe", text=TITLE, rows=((cancel("ar_cancel:ru:-100", "ru"),),))
    assert _flags(inline_cancel, "R3", item=_as("prompt")) == []
    card = Screen(id="probe", text=TITLE, rows=((BACK,), (BACK,)))
    assert _flags(card, "R3", item=_as("card")) == ["R3: card has 2 back buttons, at most 1"]


def test_r4_back_names_its_destination():
    vague = Button("⬅️ Назад", callback="adm_menu:ru", kind="back")
    assert _flags(Screen(id="probe", text=TITLE, rows=((vague,),)), "R4") == [
        "R4: '⬅️ Назад' — a back button names its destination"
    ]
    bare = Button("◀️ Назад", callback="adm_menu:ru")
    assert _flags(Screen(id="probe", text=TITLE, rows=((bare,), (BACK,))), "R4") == [
        "R4: '◀️ Назад' — a back button names its destination"
    ]
    wrong = Button("⬅️ Чаты", callback="adm_menu:ru", kind="back")
    assert _flags(Screen(id="probe", text=TITLE, rows=((wrong,),)), "R4") == [
        "R4: back to hub says 'Чаты', not 'Панель'"
    ]
    english = back("hub", "adm_menu:en", "ru")
    assert _flags(Screen(id="probe", text=TITLE, rows=((english,),)), "R4", lang="en") == [
        "R4: back to hub says 'Панель', not 'Panel'"
    ]


def test_r4_explicit_name_for_an_unnamed_target():
    """``stk.set`` has no fixed name (it is the pack's title): the builder names it."""
    pack = back("stk.set", "adm_stk_pk:ru:0123456789ab:0", "ru", name="Тестовый пак")
    assert _flags(Screen(id="probe", text=TITLE, rows=((pack,),)), "R4") == []


def test_r5_title_line():
    assert _flags(_panel(text="Проба без заголовка"), "R5") == [
        "R5: first line 'Проба без заголовка' is not 'emoji <b>title</b>'"
    ]
    assert _flags(_panel(text="<b>Проба</b>"), "R5"), "an emoji is required"
    assert _flags(_panel(text="🧪 <b>Проба</b> · Тестовый чат"), "R5") == [], "a tail is fine"


def test_r5_reads_the_title_after_the_notice():
    """The notice is above the text; R5 checks the screen's own first line."""
    screen = replace(_panel(), notice="Сохранено")
    assert _flags(screen, "R5") == []


def test_r6_callback_bytes():
    long = "adm_pnl_menu:ru:" + "9" * 49
    assert len(long.encode()) == 65
    flagged = _flags(_panel((_act(data=long),)), "R6")
    assert flagged == [f"R6: {long!r} is 65 bytes"]
    assert _flags(_panel((_act(data=long[:-1]),)), "R6") == [], "64 bytes is the limit, inclusive"


def test_r6_counts_bytes_not_characters():
    cyrillic = "adm_pnl_menu:ru:" + "я" * 25  # 41 characters, 66 bytes
    assert _flags(_panel((_act(data=cyrillic),)), "R6")


def test_r7_unrouted_and_stale_payloads():
    stale = _flags(_panel((_act(data="adm_settings:ru"),)), "R7", route=first_match)
    assert stale == ["R7: 'adm_settings:ru' reaches stale.handle_stale"]
    nothing = _flags(_panel((_act(data="zz_unknown:ru"),)), "R7", route=first_match)
    assert nothing == ["R7: 'zz_unknown:ru' reaches no handler"]
    counter = Button("1/3", callback="noop", kind="noop")
    assert _flags(_panel((counter,)), "R7", route=first_match) == [], "noop counts as routed"


def test_r7_needs_a_route_to_run():
    assert _flags(_panel((_act(data="zz_unknown:ru"),)), "R7") == []


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("🧪 <b>Проба</b>\n\nНажми кнопку ниже.", "singular imperative 'Нажми'"),
        ("🧪 <b>Проба</b>\n\nЭто твой чат.", "ты-form 'твой'"),
        ("🧪 <b>Проба</b>\n\nПожалуйста, подождите.", "nag word 'Пожалуйста'"),
        ("🧪 <b>Проба</b>\n\nНастройки успешно сохранены.", "nag word 'успешно'"),
    ],
)
def test_r8_russian_register(text: str, kind: str):
    assert _flags(_panel(text=text), "R8") == [f"R8: text has {kind}"]


def test_r8_reads_labels_and_respects_word_boundaries():
    assert _flags(_panel((_act("➕ Добавь"),)), "R8") == [
        "R8: label has singular imperative 'Добавь'"
    ]
    assert _flags(_panel(text="🧪 <b>Проба</b>\n\nДобавьте правило, тысяча раз."), "R8") == []


def test_r9_languages():
    english = Screen(
        id="probe",
        text="🧪 <b>Probe</b>",
        rows=((_act("🔁 Обновить"),), (back("hub", "adm_menu:en", "en"),)),
    )
    assert _flags(english, "R9", lang="en") == ["R9: en label has Cyrillic near '🔁 Обновить'"]
    latin = _panel((_act("📋 Whitelist"),))
    assert _flags(latin, "R9") == ["R9: ru label has Latin word 'Whitelist'"]
    assert _flags(_panel((_act("🔑 OpenAI"),)), "R9") == [], "allow-listed word"
    assert _flags(_panel((_act("👤 @test_user"),)), "R9") == [], "a @handle is not a word"


def test_r9_user_data_is_exempt():
    titled = Screen(
        id="probe",
        text="🧪 <b>Probe</b> · Тестовый чат",
        rows=((back("hub", "adm_menu:en", "en"),),),
    )
    item = make_sample(
        "probe",
        lambda _lang: titled,
        section="Панель",
        title="probe",
        nav="panel",
        user_data=("Тестовый чат",),
    )
    assert _flags(titled, "R9", lang="en") != []
    assert _flags(titled, "R9", lang="en", item=item) == []


def test_r10_calibration_controls():
    """The two labels the estimator was calibrated against (contract §3)."""
    wide = label_width_px("🌍 Глобальные настройки")
    narrow = label_width_px("🔔 Уведомления")
    assert wide > budget(2), f"≈{wide:.0f}px must be flagged: it truncates on a phone (E2)"
    assert narrow <= budget(2), f"≈{narrow:.0f}px must pass"
    pair = _panel((_act("🌍 Глобальные настройки"), _act("🔔 Уведомления")))
    assert _flags(pair, "R10") == [
        f"R10: '🌍 Глобальные настройки' ≈{wide:.0f}px > {budget(2):.0f}px"
    ]


def test_r10_budget_formula():
    assert budget(1) == 342
    assert budget(2) == 163
    assert budget(3) == pytest.approx(103.33, abs=0.01)


def test_r11_state_glyphs():
    bare_toggle = _act("Сохранять сообщения", "adm_pnl_tgl:ru:-100:sm")
    assert _flags(_panel((bare_toggle,)), "R11") == [
        "R11: toggle 'Сохранять сообщения' does not start with ✅/⚪/⏸"
    ]
    link = Button("✅ Открыть чат", url="https://t.me/example")
    assert _flags(_panel((link,)), "R11") == [
        "R11: link button '✅ Открыть чат' starts with a state glyph"
    ]
    assert _flags(_panel((_act("⚪ Сохранять сообщения", "adm_pnl_tgl:ru:-100:sm"),)), "R11") == []


def test_r12_html_and_length():
    assert _flags(_panel(text="🧪 <b>Проба</b>\n<i>незакрыто"), "R12") == ["R12: unclosed <i>"]
    assert _flags(_panel(text="🧪 <b>Проба</b>\n<script>x</script>"), "R12")[0] == (
        "R12: unknown tag <script>"
    )
    crossed = _flags(_panel(text="🧪 <b>Проба</b>\n<b><i>x</b></i>"), "R12")
    assert crossed == ["R12: </b> closes i"]
    long = _panel(text="🧪 <b>Проба</b>\n" + "я" * 4100)  # 🧪 is two UTF-16 units
    assert _flags(long, "R12") == ["R12: 4109 UTF-16 units, limit 4096"]


def test_r12_counts_parsed_text_not_markup():
    """``&amp;`` is one character to Telegram; the limit is on parsed text."""
    near = _panel(text="🧪 <b>Проба</b>\n" + "&amp;" * 4000)
    assert _flags(near, "R12") == []


def test_r13_screen_recognised_by_custom_emoji():
    link_only = Screen(
        id="probe", text=TITLE, rows=((Button("🔗 Ссылка", url="https://example.com"),),)
    )
    assert "R13: custom_emoji does not treat this markup as a screen" in check_screen(
        link_only, None, "ru"
    )
    card = _as("card")
    assert _flags(link_only, "R13", item=card) == [], "cards are not required to be screens"


def test_an_exemption_silences_only_its_rule():
    screen = _panel(tuple(_act(str(n)) for n in range(4)), text="Без заголовка")
    item = make_sample(
        "probe",
        lambda _lang: screen,
        section="Панель",
        title="probe",
        nav="panel",
        exempt={"R1": "control"},
    )
    assert _rule_ids(check_screen(screen, item, "ru")) == {"R5"}


# ---------------------------------------------------------------------------
# Registry validation
# ---------------------------------------------------------------------------


def _probe_build(_lang: str) -> Screen:
    return _panel()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"section": "Нет такой"}, "unknown section"),
        ({"exempt": {"R99": "why"}}, "unknown rule"),
        ({"exempt": {"R1": "  "}}, "has no reason"),
    ],
)
def test_make_sample_refuses(kwargs: dict, message: str):
    args = {"section": "Панель", "title": "probe", "nav": "panel", **kwargs}
    with pytest.raises(ValueError, match=message):
        make_sample("probe", _probe_build, **args)


def test_a_second_builder_under_the_same_title_is_refused():
    def first(_lang: str) -> Screen:
        return _panel()

    def second(_lang: str) -> Screen:
        return _panel()

    key = ("probe.dup", "один")
    try:
        sample("probe.dup", section="Панель", title="один", nav="panel")(first)
        sample("probe.dup", section="Панель", title="один", nav="panel")(
            first
        )  # same builder: fine
        with pytest.raises(ValueError, match="registered twice"):
            sample("probe.dup", section="Панель", title="один", nav="panel")(second)
    finally:
        from src.bot.screens import registry

        registry._REGISTRY.pop(key, None)


# ---------------------------------------------------------------------------
# sections.py
# ---------------------------------------------------------------------------


def test_every_field_code_is_in_exactly_one_section():
    members = [code for codes in _MEMBERS.values() for code in codes]
    assert len(members) == len(set(members)), "a code sits in two sections"
    assert set(SECTION_OF) == set(FIELDS_BY_CODE)
