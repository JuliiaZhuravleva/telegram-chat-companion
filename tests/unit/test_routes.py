"""The callback grammar (`src.bot.screens.routes`) against the REAL handler tree.

What a failure here means in production:

* a payload over 64 bytes → Telegram rejects the WHOLE message (400), so the
  screen never appears;
* a prefix without its trailing ``:`` → ``startswith`` also matches a longer
  sibling and the wrong handler consumes the tap (CLAUDE.md gotcha);
* a payload no handler takes → the button spins until Telegram gives up — the
  failure the stale catch-all exists to make impossible.

`first_match` walks ``src.bot.handlers.router`` exactly as aiogram does
(own handlers, then sub-routers in include order) without a bot or database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.bot.handlers import router as main_router
from src.bot.screens.routes import (
    CALLBACK_LIMIT,
    LEGACY_SAMPLES,
    NOOP,
    ROUTES,
    WRITABLE,
    CallbackTooLong,
    UnknownRoute,
    cb,
    parse,
    prefix_of,
    target_of,
)
from src.bot.screens.sections import SECTIONS
from src.bot.settings_fields import FIELDS_BY_CODE
from src.models.rules import RuleType
from tests.unit.screen_routing import first_match

SRC_BOT = Path(__file__).resolve().parents[2] / "src" / "bot"

# Worst cases, not typical values: the longest shape each field can take.
LANG = "en"  # same length as "ru"; spelled out so the intent is visible
CID = -1009999999999  # longest supergroup id shape
UID = 9999999999  # Telegram user ids are up to 10 digits today; 52 bits in principle
INT4 = 2147483647  # SERIAL / INTEGER primary keys (rule, attempt, fact ids)
PAGE = 999
FUID = "AgAD" + "Z" * 28  # file_unique_id: undocumented length; observed ~16-20
PACK_REF = "0123456789ab"
CODE = max(FIELDS_BY_CODE, key=len)
SECTION = max(SECTIONS, key=len)
RULE_TYPE = max((t.value for t in RuleType), key=len)
ARG16 = "x" * 16
"""Envelope for a new prefix whose argument shape its area has not fixed yet:
the test proves the prefix leaves room for lang + chat id + a 16-byte token."""

WORST_CASE: dict[str, tuple[object, ...]] = {
    "adm_menu:": (LANG,),
    "adm_close:": (LANG,),
    "adm_pnl:": (LANG, PAGE),
    "adm_pnl_menu:": (LANG, CID),
    "adm_pnl_grp:": (LANG, CID, SECTION),
    "adm_pnl_tgl:": (LANG, CID, CODE),
    "adm_pnl_tol:": (LANG, CID),
    "adm_pnl_tolset:": (LANG, CID, "0.95"),
    "adm_pnl_tolcancel:": (LANG, CID),
    "adm_pnl_sp:": (LANG, CID),
    "adm_pnl_spok:": (LANG, CID),
    "adm_pnl_spre:": (LANG, CID),
    "adm_pnl_spx:": (LANG, CID),
    "adm_wl_rm_ask:": (LANG, CID, PAGE),
    "adm_wl_rm:": (LANG, CID, PAGE),
    "adm_wl_pending:": (LANG, PAGE),
    "adm_wl_apr:": (LANG, INT4, PAGE),
    "adm_wl_rej:": (LANG, INT4, PAGE),
    "adm_wl_rejected:": (LANG, PAGE),
    "adm_wl_restore:": (LANG, INT4, PAGE),
    "adm_wl_del_ask:": (LANG, INT4, PAGE),
    "adm_wl_del:": (LANG, INT4, PAGE),
    "adm_approve:": (LANG, INT4),
    "adm_reject:": (LANG, INT4),
    "ar_list:": (LANG, CID, PAGE),
    "ar_all:": (LANG, CID),
    "ar_view:": (LANG, INT4, CID),
    "ar_tog:": (LANG, INT4, CID, PAGE),
    "ar_del_ask:": (LANG, INT4, CID),
    "ar_del:": (LANG, INT4, CID),
    "ar_add:": (LANG, CID),
    "ar_type:": (LANG, CID, RULE_TYPE),
    "ar_cancel:": (LANG, CID),
    "ar_b_type:": (LANG, CID, RULE_TYPE),
    "ar_b_match:": (LANG, CID, ARG16),
    "ar_b_who:": (LANG, CID, ARG16),
    "ar_b_n:": (LANG, CID, ARG16),
    "ar_b_act:": (LANG, CID, ARG16),
    "ar_b_txt:": (LANG, CID, ARG16),
    "ar_b_rx:": (LANG, CID, ARG16),
    "ar_cfg:": (LANG, INT4, CID),
    "adm_kb_orgs:": (LANG, CID, PAGE),
    "adm_kb_org_rm:": (LANG, CID, UID),
    "adm_kb_org_undo:": (LANG, CID, UID),
    "adm_kb_org_list:": (LANG, CID, PAGE),
    "adm_kb_org_pick:": (LANG, CID, UID),
    "adm_kb_org_add:": (LANG, CID),
    "adm_stk_sets:": (LANG, PAGE),
    "adm_stk_pk:": (LANG, PACK_REF, PAGE),
    "adm_stk_view:": (LANG, FUID),
    "adm_stk_card:": (LANG, FUID),
    "adm_stk_score:": (LANG, FUID),
    "adm_stk_reanalyze:": (LANG, FUID),
    "adm_stk_clr_ask:": (LANG, FUID),
    "adm_stk_clr:": (LANG, FUID),
    "adm_stk_dmchk:": (LANG, FUID),
    "adm_stk_expset:": (LANG, FUID, 9),
    "adm_stk_expedit:": (LANG, FUID),
    "adm_stk_expcancel:": (LANG, FUID),
    "adm_stk_expreset:": (LANG, FUID),
    "adm_health:": (LANG,),
    "adm_stats:": (LANG, "30d"),
    "adm_costs:": (LANG, "30d"),
    "adm_costs_verify:": (LANG, "30d"),
    "adm_settings:": (LANG,),
    "adm_defs:": (LANG, PAGE),
    "adm_defs_sec:": (LANG, SECTION),
    "adm_defs_tgl:": (LANG, CODE),
    "adm_notif:": (LANG,),
    "adm_nstk:": (LANG,),
    "adm_ntog:": (LANG, "unauthorized"),
    "adm_lang:": (LANG,),
    "adm_lang_set:": (LANG, LANG),
    "help_summary:": (UID, 500),
    "help_close:": (UID,),
    "help_dm:": (LANG, "start"),
    "help_callme:": (LANG, "acc"),
    "kb_view:": (LANG, PAGE),
    "kb_undo:": (INT4, UID),
}


def _by_status(*statuses: str) -> list[str]:
    return [prefix for prefix, route in ROUTES.items() if route.status in statuses]


# ---------------------------------------------------------------------------
# Shape of the table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", list(ROUTES))
def test_every_prefix_ends_with_a_colon(prefix: str):
    assert prefix.endswith(":")
    assert prefix.count(":") == 1, "a prefix is one segment; arguments come after it"


def test_no_prefix_is_a_prefix_of_another():
    clashes = [(a, b) for a in ROUTES for b in ROUTES if a != b and b.startswith(a)]
    assert clashes == []


def test_the_colon_is_what_keeps_siblings_apart():
    """The control for the test above: drop the colons and the clashes appear."""
    bare = [p.rstrip(":") for p in ROUTES]
    assert any(b.startswith(a) for a in bare for b in bare if a != b)


def test_every_writable_prefix_has_a_worst_case():
    assert set(WORST_CASE) == set(_by_status(*WRITABLE))


@pytest.mark.parametrize("prefix", list(WORST_CASE))
def test_worst_case_payload_fits(prefix: str):
    data = cb(prefix, *WORST_CASE[prefix])  # cb() raises past 64 bytes
    assert len(data.encode()) <= CALLBACK_LIMIT


def test_longest_payloads_measured():
    """The three longest payloads, measured. A hand count of 45 / 40 / 36 ran one
    byte high on two of them (safe side); the measured sizes are pinned here so
    a change to an id shape shows up as a diff."""
    assert len(cb("adm_kb_org_undo:", "en", CID, UID)) == 44
    assert len(cb("adm_pnl_grp:", "en", CID, "community")) == 39
    assert len(cb("ar_view:", "en", INT4, CID)) == 36


# ---------------------------------------------------------------------------
# The source agrees with the table
# ---------------------------------------------------------------------------

_FILTER_RE = re.compile(r"""F\.data\.startswith\(\s*["']([a-z_]+:?)["']""")
_WRITE_RE = re.compile(r"""(?:\bcallback(?:_data)?\s*=\s*f?["']|\bcb\(\s*["'])([a-z][a-z_]*:)""")
"""A payload written into a button: aiogram's ``callback_data=``, a screen
`Button`'s ``callback=``, or `routes.cb`."""
OWN_PREFIXES = ("adm_", "ar_", "help_", "kb_")


def _scan(pattern: re.Pattern[str]) -> dict[str, list[str]]:
    """prefix → the files under ``src/bot`` that match it, relative to it."""
    found: dict[str, list[str]] = {}
    for path in sorted(SRC_BOT.rglob("*.py")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(match.group(1), []).append(path.relative_to(SRC_BOT).as_posix())
    return found


def test_the_scanners_see_the_source():
    """Known positives, so an empty scan is not mistaken for a clean one: one
    from the tree (a kept prefix, so it outlives every area's change) and one
    per write form the regex claims to read."""
    assert "adm_pnl_menu:" in _scan(_FILTER_RE)
    assert "adm_pnl_menu:" in _scan(_WRITE_RE)
    for line in (
        'InlineKeyboardButton(text=t, callback_data=f"adm_wl:{lang}")',
        'Button(_WHITELIST(lg), callback=f"adm_wl:{lg}")',
        'Button(label, callback=cb("adm_wl:", lg))',
    ):
        assert [m.group(1) for m in _WRITE_RE.finditer(line)] == ["adm_wl:"], line


def test_every_filter_prefix_is_routed():
    filters = {p: files for p, files in _scan(_FILTER_RE).items() if p.startswith(OWN_PREFIXES)}
    unknown = {p: files for p, files in filters.items() if p not in ROUTES}
    assert unknown == {}, "a handler filters on a prefix routes.ROUTES does not know"


def test_every_filter_prefix_has_its_colon():
    """``startswith("adm_foo")`` would also take ``adm_foo_bar:``."""
    bare = {p: files for p, files in _scan(_FILTER_RE).items() if not p.endswith(":")}
    assert bare == {}


def test_every_written_prefix_is_routed():
    written = {p: files for p, files in _scan(_WRITE_RE).items() if p.startswith(OWN_PREFIXES)}
    unknown = {p: files for p, files in written.items() if p not in ROUTES}
    assert unknown == {}, "a keyboard writes a prefix routes.ROUTES does not know"


RETIRED_WRITES_TODAY: frozenset[tuple[str, str]] = frozenset()
"""Where a ``retired`` or ``compat`` prefix is still written on the base the
areas start from. A ratchet: each area deletes its own pairs (and may then
delete them here); nothing may add one. A retired write is a button that
reaches the catch-all the day its area merges."""


def _unwritable_writes(scan: dict[str, list[str]]) -> set[tuple[str, str]]:
    return {
        (path, prefix)
        for prefix, paths in scan.items()
        if prefix in ROUTES and ROUTES[prefix].status not in WRITABLE
        for path in paths
    }


def test_no_new_retired_or_compat_writes():
    new = _unwritable_writes(_scan(_WRITE_RE)) - RETIRED_WRITES_TODAY
    assert new == set(), "a file writes a retired or compat prefix; its button would go stale"


def test_the_retired_write_ratchet_says_no():
    """Its control: one more file writing ``adm_wl:``, or one more retired
    prefix in a file already listed, is refused."""
    scan = {
        "adm_wl:": ["keyboards/new.py"],
        "adm_stk_back:": ["screens/hub.py"],
        "adm_pnl:": ["x.py"],
    }
    assert _unwritable_writes(scan) - RETIRED_WRITES_TODAY == {
        ("keyboards/new.py", "adm_wl:"),
        ("screens/hub.py", "adm_stk_back:"),
    }


_INVALID_RE = re.compile(r"""answer\(\s*["']Invalid (?:data|type|preset|chat ID)["']""")
INVALID_ALERTS_TODAY = {}
"""Untranslated ``"Invalid data"`` popups a live handler shows for a payload it
cannot parse, on the base the areas start from. A payload a handler cannot
parse is a stale button and owes the user `screens.stale.show_stale` — the
panel as it is now, in their language — not an English alert. A ratchet:
counts only go down."""


def _invalid_alerts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(SRC_BOT.rglob("*.py")):
        n = len(_INVALID_RE.findall(path.read_text(encoding="utf-8")))
        if n:
            counts[path.relative_to(SRC_BOT).as_posix()] = n
    return counts


def test_no_new_invalid_data_alerts():
    grown = {
        path: (n, INVALID_ALERTS_TODAY.get(path, 0))
        for path, n in _invalid_alerts().items()
        if n > INVALID_ALERTS_TODAY.get(path, 0)
    }
    assert grown == {}, "file: (now, allowed) — call show_stale on a payload you cannot parse"


def test_the_invalid_alert_scan_sees_both_quotes():
    for line in (
        'await callback.answer("Invalid data", show_alert=True)',
        "await callback.answer('Invalid preset', show_alert=True)",
        'await callback.answer(\n            "Invalid type",\n            show_alert=True)',
    ):
        assert _INVALID_RE.search(line), line
    assert not _INVALID_RE.search('await callback.answer("Invalid callback data.")')


# ---------------------------------------------------------------------------
# Every payload lands
# ---------------------------------------------------------------------------


def test_legacy_samples_cover_every_prefix_that_can_be_sitting_in_a_dm():
    assert set(LEGACY_SAMPLES) == set(ROUTES) - set(_by_status("new"))


@pytest.mark.parametrize(("prefix", "data"), list(LEGACY_SAMPLES.items()))
def test_legacy_sample_is_its_prefix(prefix: str, data: str):
    assert prefix_of(data) == prefix
    assert len(data.encode()) <= CALLBACK_LIMIT


@pytest.mark.parametrize(("prefix", "data"), list(LEGACY_SAMPLES.items()))
def test_legacy_payloads_land(prefix: str, data: str):
    """No spinner, ever: every payload an old button can carry reaches a handler,
    live or the catch-all.

    A kept prefix must reach its live handler: the catch-all would make a
    working button look out of date. So must a legacy prefix while some file
    still writes it; once its area stops writing it (``ar_cancel:`` may be
    replaced) the handler may go and old buttons fall to the catch-all.
    Retired and compat prefixes may reach either while their area is mid-change.
    """
    handler = first_match(data)
    assert handler is not None, f"{data!r} reaches no handler"
    status = ROUTES[prefix].status
    if status == "kept" or (status == "legacy" and prefix in _scan(_WRITE_RE)):
        assert not handler.startswith("stale."), f"{data!r} fell to {handler}"


@pytest.mark.parametrize("prefix", _by_status("new"))
def test_new_prefixes_land(prefix: str):
    """Until its area lands the handler, a new prefix reaches the catch-all."""
    data = cb(prefix, *WORST_CASE[prefix])
    assert first_match(data) is not None


@pytest.mark.parametrize(
    "data",
    [
        "adm_",
        "adm_nonexistent:ru",
        "ar_nonexistent:ru:1",
        "adm_pnl_menu",  # a prefix without its colon
    ],
)
def test_garbled_admin_payloads_reach_the_catch_all(data: str):
    """A payload no live prefix claims. One a live prefix DOES claim but cannot
    parse (``ar_view:`` with empty fields) reaches that live handler, which owes
    the user `show_stale` itself — an area's job, not the router's."""
    assert first_match(data) == "stale.handle_stale"


def test_a_foreign_payload_reaches_nothing():
    """The walk's own control: it can say "no handler"."""
    assert first_match("zz_unknown:ru") is None


def test_access_card_payloads_reach_the_access_card():
    assert first_match(LEGACY_SAMPLES["adm_approve:"]) == (
        "access_card.handle_approve_notification"
    )
    assert first_match(LEGACY_SAMPLES["adm_reject:"]) == "access_card.handle_reject_notification"


def test_noop_reaches_a_handler():
    assert first_match(NOOP) is not None


# ---------------------------------------------------------------------------
# Router order (docs/admin-dm-internals.md, F.2)
# ---------------------------------------------------------------------------

ROUTER_ORDER = (
    "fsm_cancel",
    "admin_sticker",
    "admin_kb",
    "admin_chat_panel",
    "admin_defaults",
    "access_card",
    "admin",
    "rules",
    "stale",
    "commands",
    "callbacks",
    "chat_events",
    "community",
    "reactions",
    "media",
    "message",
)


def _router_module(sub) -> str:
    observers = sub.observers.values()
    for observer in observers:
        for handler in observer.handlers:
            return handler.callback.__module__.rsplit(".", 1)[-1]
    return sub.name


def test_router_include_order():
    assert tuple(_router_module(sub) for sub in main_router.sub_routers) == ROUTER_ORDER


# ---------------------------------------------------------------------------
# cb / parse / prefix_of / target_of
# ---------------------------------------------------------------------------


def test_cb_builds():
    assert cb("adm_pnl_menu:", "ru", -100) == "adm_pnl_menu:ru:-100"
    assert cb("adm_menu:", "en") == "adm_menu:en"


@pytest.mark.parametrize("prefix", ["adm_stk_set:", "adm_wl:", "adm_stk_back:", "zz_unknown:"])
def test_cb_refuses_what_must_not_be_written(prefix: str):
    with pytest.raises(UnknownRoute):
        cb(prefix, "ru")


def test_cb_refuses_past_64_bytes():
    with pytest.raises(CallbackTooLong, match="65 bytes"):
        cb("adm_menu:", "ru:" + "x" * 53)
    assert len(cb("adm_menu:", "ru:" + "x" * 52)) == 64


def test_cb_counts_bytes():
    with pytest.raises(CallbackTooLong):
        cb("adm_menu:", "я" * 28)  # 28 characters, 56 + 9 = 65 bytes


@pytest.mark.parametrize(
    ("data", "n", "expected"),
    [
        ("adm_pnl_menu:ru:-100", 2, ["ru", "-100"]),
        ("adm_kb_orgs:ru:-100:0:p", 3, ["ru", "-100", "0"]),  # trailing origin token ignored
        ("adm_pnl_menu:ru", 2, None),  # too short
        ("adm_pnl_menu:ru::", 2, None),  # empty field
        ("adm_pnl_menu::-100", 2, None),
        ("", 1, None),
        (None, 1, None),
    ],
)
def test_parse(data, n, expected):
    assert parse(data, n) == expected


def test_prefix_of_takes_the_longest_match():
    assert prefix_of("adm_pnl:ru:0") == "adm_pnl:"
    assert prefix_of("adm_pnl_menu:ru:-100") == "adm_pnl_menu:"
    assert prefix_of("adm_wl_rm_ask:ru:-100:0") == "adm_wl_rm_ask:"
    assert prefix_of("zz:ru") is None


def test_target_of():
    assert target_of("adm_menu:ru", "chat") == "hub"
    assert target_of(NOOP, "chat") == "нет действия"
    assert target_of("zz:ru", "chat") == "?"
    assert target_of("adm_pnl_tgl:ru:-100:sm", "memory") == "memory"
    assert target_of("adm_pnl_tgl:ru:-100:tol", "chat") == "chat.stickers"
    assert target_of("adm_pnl_grp:ru:-100:modules", "chat") == "memory"  # legacy group token
    assert target_of("adm_pnl_grp:ru:-100", "chat") == "chat"
