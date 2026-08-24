"""No handler may edit a message without tolerating "message is not modified" (TD-048).

Telegram raises `TelegramBadRequest("message is not modified")` when an edit
would produce byte-identical text *and* markup — a double-tap on a refresh or
pagination button, or re-opening the page you are already on. An unguarded
`edit_text` therefore kills the handler on an input the user produces by
accident. 29 sites in the legacy admin/rules handlers were unprotected; this
test is what stops the 30th from appearing.

**The rule is about the CALL, not about its surroundings.** An earlier design
asked "is this call lexically inside a try whose handlers name
TelegramBadRequest?", and that rule has two escape routes that reintroduce the
exact bug while staying green:

* `try: await msg.edit_text(...)` / `except TelegramBadRequest: raise` — the
  shape satisfies the rule and is byte-for-byte the unprotected behaviour.
* `await callback.message.bot.edit_message_text(chat_id=..., message_id=...)`
  — a Bot-object edit, which is not a `Message` method at all. That is one
  line away from existing practice: `callback.message.bot.delete_message(...)`
  is already used in `admin_sticker`.

So the rule here is simply: **route edits through `safe_edit_text` /
`safe_edit_reply_markup`**, with an explicit, reasoned allowlist for the sites
that deliberately do something else. Adding to that allowlist is a code review,
which is the point.
"""

from __future__ import annotations

import ast
from pathlib import Path

import aiogram
from aiogram.types import Message

HANDLERS_DIR = Path(__file__).resolve().parents[2] / "src" / "bot" / "handlers"

# Derived from the installed aiogram rather than typed out, so a method added
# by an upgrade is covered on the day it appears. Both directions matter: the
# Message methods AND the Bot-object equivalents, which no `Message`-derived
# list contains.
EDIT_METHODS = frozenset(
    {name for name in dir(Message) if name.startswith("edit_")}
    | {name for name in dir(aiogram.Bot) if name.startswith("edit_")}
)

# (module, enclosing function) pairs that edit raw on purpose. Every entry is a
# decision someone made and can defend; nothing here is "we did not get to it".
DELIBERATE_RAW_EDITS: dict[tuple[str, str], str] = {
    ("admin.py", "_edit_verify_screen"): "hand-rolled not-modified guard, predates the helper",
    ("admin.py", "handle_health"): "hand-rolled not-modified guard, predates the helper",
    ("admin.py", "_render_wl_chats"): "hand-rolled not-modified guard, predates the helper",
    ("admin.py", "_render_wl_rejected"): "hand-rolled not-modified guard, predates the helper",
    ("admin_sticker.py", "handle_run_analysis"): (
        "swallows ANY TelegramBadRequest and continues — the ⏳ progress edit is "
        "cosmetic and must never abort the analysis (ADR-0003 lifecycle)"
    ),
    ("admin_sticker.py", "handle_admin_sticker_dm_analyze"): (
        "same edit-in-place lifecycle as handle_run_analysis; the progress edit "
        "failing must not abort the analysis"
    ),
    ("admin_sticker.py", "handle_clear_ask"): "contextlib.suppress already in place",
    ("admin_sticker.py", "handle_clear"): "contextlib.suppress already in place",
    (
        "callbacks.py",
        "handle_summary_callback",
    ): "broad except that logs; summary refresh is best-effort",
    ("commands.py", "_deliver_summary"): (
        "the except body DELETES the placeholder and resends — a genuine fallback, "
        "not a suppression, so the helper's semantics are wrong here"
    ),
}


def _enclosing_function(tree: ast.Module, node: ast.AST) -> str:
    innermost = None
    for candidate in ast.walk(tree):
        if isinstance(candidate, ast.FunctionDef | ast.AsyncFunctionDef):
            end = candidate.end_lineno or candidate.lineno
            inside = candidate.lineno <= node.lineno <= end  # type: ignore[attr-defined]
            if inside and (innermost is None or candidate.lineno > innermost.lineno):
                innermost = candidate
    return innermost.name if innermost else "<module>"


def _raw_edit_sites(source: str, module: str) -> list[tuple[str, int, str, str]]:
    """(module, line, function, call) for every raw edit-method call."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in EDIT_METHODS
        ):
            receiver = ast.unparse(node.func.value)
            found.append(
                (
                    module,
                    node.lineno,
                    _enclosing_function(tree, node),
                    f"{receiver}.{node.func.attr}",
                )
            )
    return found


def _handler_files() -> list[Path]:
    # rglob, not glob: `src/bot/handlers/` has no subpackages today, and the day
    # someone adds one a non-recursive glob would stop covering it in silence.
    return sorted(HANDLERS_DIR.rglob("*.py"))


class TestTheScanItselfIsNotVacuous:
    """A broken scanner returns zero findings exactly like a clean codebase."""

    def test_edit_method_names_were_actually_derived(self) -> None:
        assert "edit_text" in EDIT_METHODS
        assert "edit_reply_markup" in EDIT_METHODS
        assert "edit_message_text" in EDIT_METHODS, (
            "the Bot-object edits are missing — a `bot.edit_message_text(...)` call "
            "would be invisible to this whole test"
        )
        assert len(EDIT_METHODS) >= 10

    def test_the_handler_files_were_actually_found(self) -> None:
        names = {p.name for p in _handler_files()}
        assert names, f"no handler files found under {HANDLERS_DIR} — the path is wrong"
        assert {"admin.py", "rules.py", "admin_sticker.py", "commands.py", "callbacks.py"} <= names

    def test_the_detector_catches_the_shapes_that_actually_slip_through(self) -> None:
        """Positive controls derived from the THREAT, not from the implementation.

        Each of these passed the earlier "is it inside a try?" design while
        being the bug. They are fed as source strings rather than by mutating
        the real tree, so a killed test run cannot leave a probe behind.
        """
        threats = {
            "plain": "async def h(msg):\n    await msg.edit_text('x')\n",
            "attribute chain": "async def h(cb):\n    await cb.message.edit_text('x')\n",
            "guarded but re-raising": (
                "async def h(msg):\n"
                "    try:\n"
                "        await msg.edit_text('x')\n"
                "    except TelegramBadRequest:\n"
                "        raise\n"
            ),
            "bot-object edit": (
                "async def h(cb):\n"
                "    await cb.message.bot.edit_message_text(chat_id=1, message_id=2, text='x')\n"
            ),
            "inside an except body": (
                "async def h(msg):\n"
                "    try:\n"
                "        pass\n"
                "    except Exception:\n"
                "        await msg.edit_text('x')\n"
            ),
            "edit_reply_markup": "async def h(msg):\n    await msg.edit_reply_markup(reply_markup=None)\n",
        }
        for label, src in threats.items():
            assert _raw_edit_sites(src, "probe.py"), f"the detector is blind to: {label}"

    def test_the_detector_does_not_fire_on_the_helpers(self) -> None:
        """Negative control: the compliant form must NOT be reported, or the
        rule below would be unsatisfiable and every entry would be noise."""
        compliant = (
            "async def h(msg):\n"
            "    await safe_edit_text(msg, 'x', reply_markup=None)\n"
            "    await safe_edit_reply_markup(msg, reply_markup=None)\n"
        )
        assert _raw_edit_sites(compliant, "probe.py") == []


class TestNoNewRawEdits:
    def test_every_raw_edit_in_handlers_is_a_declared_exception(self) -> None:
        offenders = [
            f"{module}:{line} in {func}() — {call}"
            for path in _handler_files()
            for module, line, func, call in _raw_edit_sites(path.read_text(), path.name)
            if (module, func) not in DELIBERATE_RAW_EDITS
        ]
        assert offenders == [], (
            "raw message edits found outside the declared exceptions. Route them "
            "through safe_edit_text / safe_edit_reply_markup (src/bot/utils.py), or "
            "add an entry to DELIBERATE_RAW_EDITS with the reason:\n  " + "\n  ".join(offenders)
        )

    def test_the_allowlist_has_not_gone_stale(self) -> None:
        """An entry whose site disappeared silently widens the allowlist.

        Without this, deleting a handler leaves a (module, function) pair that
        will re-permit a raw edit the day someone reuses that function name.
        """
        live = {
            (module, func)
            for path in _handler_files()
            for module, _line, func, _call in _raw_edit_sites(path.read_text(), path.name)
        }
        stale = sorted(set(DELIBERATE_RAW_EDITS) - live)
        assert stale == [], f"DELIBERATE_RAW_EDITS names sites that no longer exist: {stale}"
