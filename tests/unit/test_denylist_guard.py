"""Adversarial fixtures for scripts/check_denylist.py.

Deliberately fake throughout. The bug this guard exists to catch is a REAL
identifier copied into a fixture because it looked convincing, so a suite that
needed real values to be convincing would reproduce the defect it tests for.
Every assertion here is about the shape, never about a specific live id.

The controls run in both directions. A guard that misses is the obvious
failure; a guard that wrongly refuses is the one no positive control can find,
so each rule also gets an input it must stay silent about.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_denylist as guard  # noqa: E402


def hits(text: str, literals: list[tuple[str, str]] | None = None) -> set[str]:
    """Rule ids fired by one blob, using the real committed pattern file."""
    return {
        v.rule for v in guard.scan_text(text, "fixture.md", guard.load_patterns(), literals or [])
    }


# --------------------------------------------------------------------------
# Positive controls -- every rule in the committed file must have one, and the
# test that asserts that is below, so a new rule without a fixture fails CI.
# --------------------------------------------------------------------------

POSITIVE: dict[str, str] = {
    "telegram-chat-id": "QA ran in the chat -1003418762095 yesterday.",
    "home-path": "traceback from /Users/vasilisa/projects/app/main.py line 3",
}

NEGATIVE: dict[str, str] = {
    # Placeholder ids the repository actually writes, in every shape the
    # synthetic filter is meant to absorb.
    "telegram-chat-id": (
        # A round number is NOT on this list any more: the trailing-zeros
        # branch that absorbed it also absorbed ids in the live range, so
        # roundness stopped being a synthetic signal. Placeholders use the
        # repository's -1009999xxxxxx convention instead.
        "ADR example -1001234567890, fixture -1009999000042, repeated -1009999999999"
    ),
    "home-path": "CI wrote /home/runner/work/x.log and the docs say /Users/someone/projects/",
}


@pytest.mark.parametrize("rule", sorted(POSITIVE))
def test_rule_fires_on_its_positive_control(rule: str) -> None:
    assert rule in hits(POSITIVE[rule]), f"{rule} missed its own positive control"


@pytest.mark.parametrize("rule", sorted(NEGATIVE))
def test_rule_stays_silent_on_its_negative_control(rule: str) -> None:
    """A rule that wrongly refuses is as real a defect as one that misses."""
    assert rule not in hits(NEGATIVE[rule]), f"{rule} fired on a legitimate placeholder"


def test_every_committed_rule_has_both_controls() -> None:
    """A rule with no fixture is a rule nobody has proven. Fail rather than trust."""
    declared = {rule.id for rule in guard.load_patterns()}
    assert declared == set(POSITIVE), f"missing positive control: {declared ^ set(POSITIVE)}"
    assert declared == set(NEGATIVE), f"missing negative control: {declared ^ set(NEGATIVE)}"


# --------------------------------------------------------------------------
# Literals -- the half that catches what has no shape.
# --------------------------------------------------------------------------


def test_literal_fires_even_though_the_value_has_no_shape() -> None:
    """A bare bot id is an integer; no shape rule can see it. This is the point."""
    literals = [("8123456789", "some bot")]
    assert "literal" in hits("bot = Bot(token='8123456789:AAsomething')", literals)


def test_literal_does_not_fire_on_an_unrelated_number() -> None:
    assert "literal" not in hits("retry after 8123456780 ms", [("8123456789", "some bot")])


def test_synthetic_filter_never_mutes_a_literal() -> None:
    """The filter exists for shape rules. A known-real value is known-real."""
    # 1111111111 would be classed synthetic by shape, but if it is on the
    # denylist it is on the denylist.
    assert "literal" in hits("id 1111111111", [("1111111111", "deliberately odd")])


def test_a_file_exemption_mutes_the_shape_rule_it_names() -> None:
    """Fixture files for the guards are exempt from the shape rules by path."""
    exempt = "tests/unit/test_denylist_guard.py"
    fired = {
        v.rule for v in guard.scan_text("chat -1003418762095", exempt, guard.load_patterns(), [])
    }
    assert "telegram-chat-id" not in fired


def test_a_file_exemption_never_covers_a_literal() -> None:
    """The invariant that keeps the exemption honest.

    A fixture file is allowed to contain id-SHAPED text -- that is its job. It
    is NOT allowed to contain a known real id. If this asymmetry ever collapses,
    the exemption becomes a hole exactly where the fixtures live, which is where
    real values have historically been pasted because they looked convincing.
    """
    exempt = "tests/unit/test_denylist_guard.py"
    patterns = guard.load_patterns()
    assert any(exempt in r.exempt_paths for r in patterns), "precondition: file is exempt"
    fired = [
        v.rule
        for v in guard.scan_text("id 8123456789", exempt, patterns, [("8123456789", "a bot")])
    ]
    assert fired == ["literal"], "a real value must still fire inside an exempt file"


# --------------------------------------------------------------------------
# Waivers
# --------------------------------------------------------------------------


def test_waiver_mutes_only_the_rule_it_names() -> None:
    text = "chat -1003418762095 at /Users/vasilisa/x/  # denylist-ok: home-path"
    fired = hits(text)
    assert "home-path" not in fired, "the named rule must be waived"
    assert "telegram-chat-id" in fired, "a waiver for one rule must not mute another"


def test_waiver_naming_an_unknown_rule_waives_nothing() -> None:
    """The safe direction: a typo in a marker must not silently disable a rule."""
    assert "home-path" in hits("/Users/vasilisa/x/  # denylist-ok: hoem-path")


# --------------------------------------------------------------------------
# Properties of the guard itself
# --------------------------------------------------------------------------


def test_output_never_reprints_the_matched_value() -> None:
    """A guard that prints the secret into a CI log has moved the leak, not stopped it."""
    value = "8123456789"
    (violation,) = list(guard.scan_text(f"id {value}", "f.md", [], [(value, "some bot")]))
    rendered = violation.render()
    assert value not in rendered, "the full value must never reach the output"
    assert value[:2] in rendered and value[-2:] in rendered, "must stay locatable"


def test_the_literals_file_is_gitignored() -> None:
    """It holds real values. If it ever becomes committable the design is broken."""
    result = subprocess.run(
        ["git", "check-ignore", guard.LITERALS_FILE.name],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, ".secrets-denylist.local.toml must stay gitignored"


def test_guard_is_wired_into_pre_commit() -> None:
    """A correct helper is not a used helper -- pin the call site, not the function.

    Deleting the hook is the cheapest way for this whole file to become
    decorative, and nothing else in the suite would notice.
    """
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "scripts/check_denylist.py" in config, "the guard must run as a pre-commit hook"


def test_hook_scans_every_file_type_not_a_narrow_glob() -> None:
    """A filter narrower than the script silently disables the hook for the rest.

    The same lesson the ruff pin and the plan-artifact hook already record.
    """
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    block = config[config.index("check-denylist") :]
    block = block[: block.index("- repo:")] if "- repo:" in block else block
    assert not re.search(r"^\s+files:", block, re.M), (
        "check-denylist must not carry a files: filter -- it is a whole-tree guard"
    )


def test_tracked_tree_is_clean() -> None:
    """The gate itself, run the way CI runs it."""
    result = subprocess.run(
        [sys.executable, "scripts/check_denylist.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"denylist violations in the tracked tree:\n{result.stderr}"


# --------------------------------------------------------------------------
# main() -- the call site, not the helper.
#
# Everything above drives scan_text(). That is exactly how the first version of
# this suite passed while the program violated its own invariant: main() used
# to skip SELF_EXEMPT files WHOLESALE, one level above the function under test,
# so "a literal is never file-exempt" held in the helper and was false in the
# guard. These tests run the real main() against a temporary tree.
# --------------------------------------------------------------------------


def run_main(tmp_path, files: dict[str, str], *, literals: str | None, argv: list[str]):
    """Drive the real main() over a throwaway repo. Returns (code, out, err)."""
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    # NOT named .secrets-denylist.toml: that name is a fixture in its own right
    # (it is in SELF_EXEMPT), and writing the rule file over it clobbered the
    # very content under test -- the first version of this helper did exactly
    # that and the assertion failed for a reason that had nothing to do with
    # the guard.
    patterns = tmp_path / ".patterns-under-test.toml"
    patterns.write_text((REPO_ROOT / ".secrets-denylist.toml").read_text("utf-8"), encoding="utf-8")
    lit = tmp_path / ".secrets-denylist.local.toml"
    if literals is not None:
        lit.write_text(literals, encoding="utf-8")

    saved = (guard.REPO_ROOT, guard.PATTERNS_FILE, guard.LITERALS_FILE, sys.argv)
    guard.REPO_ROOT, guard.PATTERNS_FILE, guard.LITERALS_FILE = tmp_path, patterns, lit
    sys.argv = ["check_denylist.py", *argv]
    buf_out, buf_err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            code = guard.main()
    finally:
        guard.REPO_ROOT, guard.PATTERNS_FILE, guard.LITERALS_FILE, sys.argv = saved
    return code, buf_out.getvalue(), buf_err.getvalue()


FAKE_LITERALS = '[[literal]]\nvalue = "8123456789"\nlabel = "a bot"\n'


def test_main_still_checks_literals_inside_a_self_exempt_file(tmp_path) -> None:
    """The invariant, asserted where it was actually broken.

    .secrets-denylist.toml is committed and public, and it is precisely where
    someone pastes a measured example into a comment. Muting shape rules there
    is right; muting the literal check is not. Deleting the SELF_EXEMPT guard
    from the pattern loop and restoring the wholesale skip in main() must make
    this test fail.
    """
    code, _out, err = run_main(
        tmp_path,
        {".secrets-denylist.toml": "# calibrated on 8123456789\n"},
        literals=FAKE_LITERALS,
        argv=[".secrets-denylist.toml"],
    )
    assert code == 1, "a known real value must be caught even in a self-exempt file"
    assert "literal" in err


def test_main_mutes_shape_rules_inside_a_self_exempt_file(tmp_path) -> None:
    """The other half of the asymmetry -- otherwise the rule file cannot hold rules."""
    code, out, _err = run_main(
        tmp_path,
        {".secrets-denylist.toml": "# example -1003418762095\n"},
        literals=FAKE_LITERALS,
        argv=[".secrets-denylist.toml"],
    )
    assert code == 0, out


def test_main_exits_2_when_a_file_cannot_be_scanned(tmp_path) -> None:
    """'Could not check' must never render as 'clean'. Exit 1 would be wrong too."""
    code, out, err = run_main(tmp_path, {}, literals=FAKE_LITERALS, argv=["nope.md"])
    assert code == 2, f"expected the could-not-scan code, got {code}: {out}{err}"
    assert "could NOT scan" in err
    assert "clean" not in out


def test_main_refuses_when_literals_are_required_but_missing(tmp_path) -> None:
    """Fail closed on a machine that never seeded the local file."""
    code, _out, err = run_main(
        tmp_path, {"a.md": "hello\n"}, literals=None, argv=["--require-literals", "a.md"]
    )
    assert code == 2
    assert "--add-literal" in err, "the refusal must say how to fix it"


def test_main_warns_loudly_when_literals_are_absent(tmp_path) -> None:
    """Without the flag it still runs -- but 'clean' must not imply full coverage."""
    code, out, err = run_main(tmp_path, {"a.md": "hello\n"}, literals=None, argv=["a.md"])
    assert code == 0
    assert "WARNING" in err and "NOT checked" in err
    assert "SHAPES ONLY" in out, "the success line must not read as full coverage"


def test_main_checks_the_file_name_not_only_the_contents(tmp_path) -> None:
    """A chat id in a path is more visible than one in body text, not less."""
    code, _out, err = run_main(
        tmp_path,
        {"docs/chat--1003418762095-notes.md": "nothing here\n"},
        literals=FAKE_LITERALS,
        argv=["docs/chat--1003418762095-notes.md"],
    )
    assert code == 1
    assert "in the file name" in err


def test_a_literal_is_not_waivable(tmp_path) -> None:
    """A shape rule is a heuristic and may be waived; a known real value may not.

    The sibling guard learned this first: the marker is written by whoever is
    being inspected, so a waivable literal lets the leak switch off its own
    detection with one line of text.
    """
    code, _out, err = run_main(
        tmp_path,
        {"a.md": "id 8123456789  # denylist-ok: literal\n"},
        literals=FAKE_LITERALS,
        argv=["a.md"],
    )
    assert code == 1, "a one-line comment must not mute a known real identifier"
    assert "literal" in err


@pytest.mark.parametrize(
    "spelling",
    [
        "chat -1009999000042 here",  # canonical
        "chat -1_009_999_000_042 here",  # digit separators, as this repo writes ints
        "open t.me/c/9999000042/7 here",  # what build_chat_url() actually emits
    ],
)
def test_a_literal_is_found_in_every_spelling_this_codebase_emits(tmp_path, spelling) -> None:
    code, _out, err = run_main(
        tmp_path,
        {"a.md": spelling + "\n"},
        literals='[[literal]]\nvalue = "-1009999000042"\nlabel = "a chat"\n',
        argv=["a.md"],
    )
    assert code == 1, f"missed the {spelling!r} spelling"
    assert "literal" in err


@pytest.mark.parametrize("real", ["1003456789012", "1003210987654", "1003400000000"])
def test_synthetic_filter_does_not_swallow_ids_in_the_live_range(real: str) -> None:
    """False positives here are LEAKS, so the heuristic must stay narrow.

    Two earlier versions failed exactly these: a non-monotonic run of six, and
    a trailing-zeros branch.
    """
    assert not guard.is_synthetic_id(real)


@pytest.mark.parametrize("fake", ["1234567890", "9876543210", "9999999999", "1500000000"])
def test_synthetic_filter_still_absorbs_obvious_placeholders(fake: str) -> None:
    assert guard.is_synthetic_id(fake)
