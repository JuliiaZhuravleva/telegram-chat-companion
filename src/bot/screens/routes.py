"""Callback-data grammar for the admin panel: one table, one builder, one parser.

Grammar: ``{prefix}{lang}:{arg}:...`` where every prefix ends with ``:`` and
``lang`` stays in position 1 — some sixty existing parsers read ``parts[1]``.

**A prefix is kept wherever the screen's meaning survives.** Buttons already
sitting in admins' DMs then open the new screen instead of a stale one, so the
callback grammar — not the old keyboard functions — is the compatibility
surface. New prefixes exist only for screens that did not exist before.

Statuses:

* ``kept``    — live today and stays; `cb()` builds it.
* ``new``     — added by the redesign; until its area lands the handler, a tap
                falls to the stale catch-all (``stale.py``), never a spinner.
* ``legacy``  — live today, not named by the redesign; its owning area decides.
* ``compat``  — still parsed so old buttons work, never written again.
* ``retired`` — handler deleted (or about to be); falls to the catch-all,
                never written again.

`cb()` refuses ``compat`` and ``retired``: writing one would mint a button that
is stale on the day it is sent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Final, Literal

from src.bot.screens.i18n import Tr
from src.bot.screens.sections import SECTION_OF, SECTION_SCREEN, section_of_token

CALLBACK_LIMIT: Final = 64
"""Telegram's limit on ``callback_data``, in BYTES of UTF-8."""

NOOP: Final = "noop"
"""The one exact (non-prefix) payload: a button that does nothing (page counter)."""

Status = Literal["kept", "new", "legacy", "compat", "retired"]
Target = str | Callable[[list[str]], str]


class CallbackTooLong(ValueError):
    """A built payload exceeds Telegram's 64-byte ``callback_data`` limit."""


class UnknownRoute(ValueError):
    """A builder asked for a prefix that is not in `ROUTES`, or one that must not be written."""


@dataclass(frozen=True, slots=True)
class Route:
    target: Target
    """Screen id the button opens, ``"self"`` for "re-renders the current screen",
    a short Russian description for non-screens, or a function of the parsed parts."""

    status: Status
    toggle: bool = False
    """A toggle: its label must start with a state glyph (lint R11)."""


def _section_arg(parts: list[str]) -> str:
    section = section_of_token(parts[3]) if len(parts) > 3 else None
    return SECTION_SCREEN[section] if section else "chat"


def _toggle_arg(parts: list[str]) -> str:
    section = SECTION_OF.get(parts[3]) if len(parts) > 3 else None
    return SECTION_SCREEN[section] if section else "chat"


ROUTES: Final[dict[str, Route]] = {
    # ── hub ──
    "adm_menu:": Route("hub", "kept"),
    "adm_close:": Route("удаляет сообщение", "kept"),
    # ── chats and the chat root ──
    "adm_pnl:": Route("chats", "kept"),
    "adm_pnl_menu:": Route("chat", "kept"),
    "adm_pnl_grp:": Route(_section_arg, "kept"),
    "adm_pnl_tgl:": Route(_toggle_arg, "kept", toggle=True),
    "adm_pnl_tol:": Route("tol.prompt", "kept"),
    "adm_pnl_tolset:": Route("chat.stickers", "new"),
    "adm_pnl_tolcancel:": Route("chat.stickers", "kept"),
    "adm_pnl_sp:": Route("редактор инструкции", "kept"),
    "adm_pnl_spok:": Route("behavior", "kept"),
    "adm_pnl_spre:": Route("редактор инструкции", "kept"),
    "adm_pnl_spx:": Route("behavior", "kept"),
    "adm_wl_rm_ask:": Route("chat.off_ask", "kept"),
    "adm_wl_rm:": Route("chats", "kept"),
    # ── requests, rejected, the access card ──
    "adm_wl_pending:": Route("requests", "kept"),
    "adm_wl_apr:": Route("chat", "kept"),
    "adm_wl_rej:": Route("requests", "kept"),
    "adm_wl_rejected:": Route("rejected", "kept"),
    "adm_wl_restore:": Route("rejected", "kept"),
    "adm_wl_del_ask:": Route("rejected.forget_ask", "kept"),
    "adm_wl_del:": Route("rejected", "kept"),
    "adm_approve:": Route("card.access.ok", "kept"),
    "adm_reject:": Route("card.access.no", "kept"),
    # ── rules ──
    "ar_list:": Route("rules", "kept"),
    "ar_all:": Route("rules", "new", toggle=True),
    "ar_view:": Route("rule", "kept"),
    # The rule card's pause/resume. The list's per-row 🔄 wrote the same
    # payload shape (``ar_tog:{l}:{rid}:{cid}:{page}``) and is what the
    # redesign retires: the rules area stops writing it from the list, and an
    # old list button, indistinguishable by shape, toggles and opens the card.
    "ar_tog:": Route("rule", "kept", toggle=True),
    "ar_del_ask:": Route("rule.del_ask", "kept"),
    "ar_del:": Route("rules", "kept"),
    "ar_add:": Route("rule.type", "kept"),
    "ar_type:": Route("rule.cfg", "legacy"),
    "ar_cancel:": Route("rules", "legacy"),
    "ar_b_type:": Route("rule.words | rule.who | rule.flood | rule.spam", "new"),
    "ar_b_match:": Route("rule.action", "new"),
    "ar_b_who:": Route("rule.action", "new"),
    "ar_b_n:": Route("rule.action", "new"),
    "ar_b_act:": Route("rule.text | rule.react", "new"),
    "ar_b_txt:": Route("rule", "new"),
    "ar_b_rx:": Route("rule", "new"),
    "ar_cfg:": Route("rule.cfg", "new"),
    # ── knowledge-base organizers ──
    "adm_kb_orgs:": Route("orgs", "kept"),
    "adm_kb_org_rm:": Route("orgs", "kept"),
    "adm_kb_org_undo:": Route("orgs", "new"),
    "adm_kb_org_list:": Route("org.list", "kept"),
    "adm_kb_org_pick:": Route("orgs", "kept"),
    "adm_kb_org_add:": Route("org.add", "kept"),
    "adm_kb_org_x:": Route("orgs", "new"),
    # ── stickers ──
    "adm_stk_sets:": Route("stk.sets", "kept"),
    "adm_stk_pk:": Route("stk.set", "new"),
    "adm_stk_set:": Route("stk.set", "compat"),
    "adm_stk_view:": Route("stk.card", "kept"),
    "adm_stk_card:": Route("stk.card", "new"),
    "adm_stk_score:": Route("stk.score", "new"),
    "adm_stk_reanalyze:": Route("stk.analyzing", "kept"),
    "adm_stk_clr_ask:": Route("stk.clear_ask", "kept"),
    "adm_stk_clr:": Route("stk.card", "kept"),
    "adm_stk_dmchk:": Route("stk.card", "kept"),
    "adm_stk_expset:": Route("stk.card", "legacy"),
    "adm_stk_expedit:": Route("stk.score_in", "legacy"),
    "adm_stk_expcancel:": Route("stk.card", "legacy"),
    "adm_stk_expreset:": Route("stk.card", "legacy"),
    # ── status, stats, costs ──
    "adm_health:": Route("status", "kept"),
    "adm_stats:": Route("stats", "kept"),
    "adm_costs:": Route("costs", "kept"),
    "adm_costs_verify:": Route("costs.verify", "kept"),
    # ── general settings ──
    "adm_settings:": Route("settings", "new"),
    "adm_defs:": Route("defs", "kept"),
    "adm_defs_sec:": Route("defs.section", "new"),
    "adm_defs_tgl:": Route("defs.section", "kept", toggle=True),
    "adm_notif:": Route("notif", "kept"),
    "adm_nstk:": Route("notif", "kept"),
    "adm_ntog:": Route("notif", "kept", toggle=True),
    "adm_lang:": Route("lang", "kept"),
    "adm_lang_set:": Route("lang", "kept"),
    # ── group-side and /kb (outside the admin DM; owned by entry & cards) ──
    "help_summary:": Route("summary.result", "kept"),
    "help_close:": Route("удаляет сообщение", "kept"),
    "help_dm:": Route(lambda p: "start.member" if p[2:3] == ["start"] else "help.dm", "new"),
    "help_callme:": Route(
        lambda p: "callme.set_account" if p[2:3] == ["acc"] else "callme.usage", "new"
    ),
    "kb_view:": Route("self", "kept"),
    "kb_undo:": Route("убирает факт", "kept"),
    # ── retired: handlers go, old buttons fall to the stale catch-all ──
    "adm_wl:": Route("hub", "retired"),
    "adm_wl_chats:": Route("chats", "retired"),
    "adm_kb:": Route("chats", "retired"),
    "adm_kb_menu:": Route("memory", "retired"),
    "adm_kb_toggle:": Route("memory", "retired", toggle=True),
    "adm_react:": Route("chats", "retired"),
    "adm_react_menu:": Route("reactions", "retired"),
    "adm_react_toggle:": Route("reactions", "retired", toggle=True),
    "adm_rules:": Route("chats", "retired"),
    "adm_stk_back:": Route("stk.set", "retired"),
}

WRITABLE: Final[frozenset[Status]] = frozenset({"kept", "new", "legacy"})


def cb(prefix: str, *parts: object) -> str:
    """Build a payload: ``cb("adm_pnl_menu:", "ru", -100123)`` → ``"adm_pnl_menu:ru:-100123"``.

    Raises `UnknownRoute` for a prefix outside `ROUTES` or one that must never
    be written again, and `CallbackTooLong` past 64 bytes — at build time, where
    the stack trace names the builder, instead of as a Telegram 400 that loses
    the whole message.
    """
    route = ROUTES.get(prefix)
    if route is None:
        raise UnknownRoute(f"{prefix!r} is not in routes.ROUTES")
    if route.status not in WRITABLE:
        raise UnknownRoute(f"{prefix!r} is {route.status}: old buttons parse, new ones never")
    data = prefix + ":".join(str(part) for part in parts)
    size = len(data.encode("utf-8"))
    if size > CALLBACK_LIMIT:
        raise CallbackTooLong(f"{data!r} is {size} bytes, limit {CALLBACK_LIMIT}")
    return data


def parse(data: str | None, n: int) -> list[str] | None:
    """The first ``n`` fields after the prefix (``[lang, arg1, …]``), or None.

    None when the payload is shorter than ``n`` fields or one of them is empty
    — a garbled payload is a stale one, not a crash. Extra trailing fields are
    ignored, which is what keeps old ``…:p`` origin tokens parsing.
    """
    if not data:
        return None
    fields = data.split(":")[1:]
    if len(fields) < n or any(not field for field in fields[:n]):
        return None
    return fields[:n]


def prefix_of(data: str) -> str | None:
    """The `ROUTES` prefix a payload starts with (longest match), or None."""
    best: str | None = None
    for prefix in ROUTES:
        if data.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


def target_of(callback: str, current_id: str) -> str:
    """Where a button goes, for the dump and the rules test: a screen id or a description."""
    if callback == NOOP:
        return "нет действия"
    prefix = prefix_of(callback)
    if prefix is None:
        return "?"
    target = ROUTES[prefix].target
    resolved = target(callback.split(":")) if callable(target) else target
    return current_id if resolved == "self" else resolved


BACK_NAMES: Final[dict[str, Tr]] = {
    "hub": Tr("Панель", "Panel"),
    "chats": Tr("Чаты", "Chats"),
    "chat": Tr("Чат", "Chat"),
    "memory": Tr("Память", "Memory"),
    "chat.stickers": Tr("Стикеры", "Stickers"),
    "rules": Tr("Правила", "Rules"),
    "rule": Tr("Правило", "Rule"),
    "orgs": Tr("Организаторы", "Organizers"),
    "requests": Tr("Заявки", "Requests"),
    "settings": Tr("Общие настройки", "General settings"),
    "defs": Tr("Для всех чатов", "All chats"),
    "status": Tr("Состояние", "Status"),
    "costs": Tr("Расходы", "Costs"),
    "stk.sets": Tr("Паки", "Packs"),
    "stk.card": Tr("Стикер", "Sticker"),
}
"""The name on a back button is the name of where it goes. A target missing
here (``stk.set``: the pack's own title) gets its name from the builder."""


class REQUEST_IDS(IntEnum):  # a constant table: REQUEST_IDS.ORG_PICK
    """``request_id`` of each ``request_users`` reply button, so a shared user
    is routed by which question it answers rather than by FSM state alone."""

    ORG_PICK = 1
    RULE_WHO = 2


# One realistic payload per prefix that buttons already sitting in DMs can
# carry. Each must land on SOME handler (live or the stale catch-all) — an
# unrouted callback leaves the user's button spinning until Telegram gives up.
# Values are invented (samples/fake.py shapes), trailing ``:p`` is the retired
# origin token old payloads still carry.
LEGACY_SAMPLES: Final[dict[str, str]] = {
    "adm_menu:": "adm_menu:ru",
    "adm_close:": "adm_close:ru",
    "adm_pnl:": "adm_pnl:ru:0",
    "adm_pnl_menu:": "adm_pnl_menu:ru:-1009999999999",
    "adm_pnl_grp:": "adm_pnl_grp:ru:-1009999999999:modules",
    "adm_pnl_tgl:": "adm_pnl_tgl:ru:-1009999999999:rag",
    "adm_pnl_tol:": "adm_pnl_tol:ru:-1009999999999",
    "adm_pnl_tolcancel:": "adm_pnl_tolcancel:ru:-1009999999999",
    "adm_pnl_sp:": "adm_pnl_sp:ru:-1009999999999",
    "adm_pnl_spok:": "adm_pnl_spok:ru:-1009999999999",
    "adm_pnl_spre:": "adm_pnl_spre:ru:-1009999999999",
    "adm_pnl_spx:": "adm_pnl_spx:ru:-1009999999999",
    "adm_wl_rm_ask:": "adm_wl_rm_ask:ru:-1009999999999:0",
    "adm_wl_rm:": "adm_wl_rm:ru:-1009999999999:0",
    "adm_wl_pending:": "adm_wl_pending:ru:0",
    "adm_wl_apr:": "adm_wl_apr:ru:99999:0",
    "adm_wl_rej:": "adm_wl_rej:ru:99999:0",
    "adm_wl_rejected:": "adm_wl_rejected:ru:0",
    "adm_wl_restore:": "adm_wl_restore:ru:99999:0",
    "adm_wl_del_ask:": "adm_wl_del_ask:ru:99999:0",
    "adm_wl_del:": "adm_wl_del:ru:99999:0",
    "adm_approve:": "adm_approve:ru:99999",
    "adm_reject:": "adm_reject:ru:99999",
    "ar_list:": "ar_list:ru:-1009999999999:0",
    "ar_view:": "ar_view:ru:99999",
    "ar_tog:": "ar_tog:ru:99999:-1009999999999:0",
    "ar_del_ask:": "ar_del_ask:ru:99999",
    "ar_del:": "ar_del:ru:99999",
    "ar_add:": "ar_add:ru:-1009999999999",
    "ar_type:": "ar_type:ru:-1009999999999:keyword_trigger",
    "ar_cancel:": "ar_cancel:ru:-1009999999999",
    "adm_kb_orgs:": "adm_kb_orgs:ru:-1009999999999:0:p",
    "adm_kb_org_rm:": "adm_kb_org_rm:ru:-1009999999999:555000111:p",
    "adm_kb_org_list:": "adm_kb_org_list:ru:-1009999999999:0:p",
    "adm_kb_org_pick:": "adm_kb_org_pick:ru:-1009999999999:555000111:p",
    "adm_kb_org_add:": "adm_kb_org_add:ru:-1009999999999:p",
    "adm_stk_sets:": "adm_stk_sets:ru:0",
    "adm_stk_set:": "adm_stk_set:ru:test_pack:0",
    "adm_stk_view:": "adm_stk_view:ru:AgADZZZZZZZZZZZZZZZZ",
    "adm_stk_reanalyze:": "adm_stk_reanalyze:ru:AgADZZZZZZZZZZZZZZZZ",
    "adm_stk_clr_ask:": "adm_stk_clr_ask:ru:AgADZZZZZZZZZZZZZZZZ",
    "adm_stk_clr:": "adm_stk_clr:ru:AgADZZZZZZZZZZZZZZZZ",
    "adm_stk_dmchk:": "adm_stk_dmchk:ru:AgADZZZZZZZZZZZZZZZZ",
    "adm_stk_expset:": "adm_stk_expset:ru:AgADZZZZZZZZZZZZZZZZ:2",
    "adm_stk_expedit:": "adm_stk_expedit:ru:AgADZZZZZZZZZZZZZZZZ",
    "adm_stk_expcancel:": "adm_stk_expcancel:ru:AgADZZZZZZZZZZZZZZZZ",
    "adm_stk_expreset:": "adm_stk_expreset:ru:AgADZZZZZZZZZZZZZZZZ",
    "adm_health:": "adm_health:ru",
    "adm_stats:": "adm_stats:ru:24h",
    "adm_costs:": "adm_costs:ru:24h",
    "adm_costs_verify:": "adm_costs_verify:ru:24h",
    "adm_defs:": "adm_defs:ru:0",
    "adm_defs_tgl:": "adm_defs_tgl:ru:cm",
    "adm_notif:": "adm_notif:ru",
    "adm_nstk:": "adm_nstk:ru",
    "adm_ntog:": "adm_ntog:ru:unauthorized",
    "adm_lang:": "adm_lang:ru",
    "adm_lang_set:": "adm_lang_set:ru:en",
    "help_summary:": "help_summary:555000111:100",
    "help_close:": "help_close:555000111",
    "kb_view:": "kb_view:ru:1",
    "kb_undo:": "kb_undo:99999:555000111",
    "adm_wl:": "adm_wl:ru",
    "adm_wl_chats:": "adm_wl_chats:ru:0",
    "adm_kb:": "adm_kb:ru:0",
    "adm_kb_menu:": "adm_kb_menu:ru:-1009999999999:p",
    "adm_kb_toggle:": "adm_kb_toggle:ru:-1009999999999:p",
    "adm_react:": "adm_react:ru:0",
    "adm_react_menu:": "adm_react_menu:ru:-1009999999999:p",
    "adm_react_toggle:": "adm_react_toggle:ru:-1009999999999:rx:p",
    "adm_rules:": "adm_rules:ru:0",
    "adm_stk_back:": "adm_stk_back:ru:test_pack:0",
}
