#!/usr/bin/env python3
"""Project-specific commit guard: values and shapes that must not go public.

    python3 scripts/check_denylist.py                 # every tracked file
    python3 scripts/check_denylist.py <path> ...      # named files (pre-commit)
    python3 scripts/check_denylist.py --add-literal   # append a value, read from stdin

Why this exists alongside gitleaks. gitleaks matches credential *shapes* and is
good at it -- measured 2026-08-26, its default ruleset catches the real bot
token in .env and correctly ignores the synthetic ones in tests/. What it
cannot see is an identifier with no shape at all: a bare Telegram chat, user or
bot id is an integer, and an integer looks like every other integer. That class
has already leaked into this public repository twice (TD-006, and the
production bot id in tests/unit/test_error_handler.py).

Two files, and the split is the whole point:

  .secrets-denylist.toml         COMMITTED   patterns (regex). Public-safe.
  .secrets-denylist.local.toml   GITIGNORED  literal real values. Never public.

The literals are deliberately NOT hashed-and-committed. A Telegram id is ten
digits; sha256 over a 10^10 space is brute-forced in seconds, so a committed
hash file would publish exactly what it claims to protect. Keeping the literals
local is honest about that, and costs nothing: the gate that matters is the
pre-commit hook, which runs on the machine that holds the values.

Output is redacted -- a guard that prints the secret it caught into a CI log
has moved the leak rather than stopped it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PATTERNS_FILE = REPO_ROOT / ".secrets-denylist.toml"
LITERALS_FILE = REPO_ROOT / ".secrets-denylist.local.toml"

#: An inline marker waives one rule on one line, e.g. ``# denylist-ok: home-path``.
#: Naming an unknown rule does NOT waive anything -- the safe direction.
WAIVER_RE = re.compile(r"denylist-ok:\s*([a-z0-9-]+)")

#: Files whose whole point is to contain the shapes below. Like exempt_paths,
#: this mutes SHAPE rules only -- never the literal check. The earlier version
#: skipped these files wholesale in main(), one level above scan_text, so the
#: "a literal is never file-exempt" invariant held in the function the tests
#: drove and was false in the program. Two committed, public files were
#: unprotected: the rule file, where measured examples get pasted into
#: comments, and this script's own docstring.
SELF_EXEMPT = frozenset({"scripts/check_denylist.py", ".secrets-denylist.toml"})


#: Digit-group separators this repo actually writes (-1_003_..., 1,003,...).
_SEPARATED_DIGITS = re.compile(r"(?<=\d)[\s_,](?=\d)")
#: A supergroup id also travels as the t.me/c/ tail, with "-100" stripped --
#: build_chat_url() in src/utils/telegram.py emits exactly that form.
_SUPERGROUP = re.compile(r"^-100(\d{10})$")


def literal_forms(value: str) -> tuple[str, ...]:
    """Every spelling of one denylisted value that this codebase actually uses.

    Exact substring matching catches one rendering. Missing the others is not
    hypothetical: `t.me/c/9999000042` is what our own link builder produces for
    the chat whose canonical id is `-1009999000042`, and that form appears
    throughout docs/. A literal registered canonically would sail past it.

    (The first draft of this docstring used the REAL chat id as the example and
    was caught by this very guard, but only after SELF_EXEMPT stopped skipping
    this file wholesale. Before that fix it would have been committed.)
    """
    forms = {value}
    inner = _SUPERGROUP.match(value)
    if inner:
        forms.add(inner.group(1))
    return tuple(forms)


class Rule(NamedTuple):
    id: str
    regex: re.Pattern[str]
    why: str
    skip_synthetic_ids: bool
    allow: list[re.Pattern[str]]
    exempt_paths: frozenset[str]


class Violation(NamedTuple):
    path: str
    line: int
    rule: str
    why: str
    redacted: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.why}\n    matched: {self.redacted}"


def redact(value: str) -> str:
    """Show enough to locate the hit, never enough to reconstruct it."""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def is_synthetic_id(digits: str) -> bool:
    """True for a value a human obviously invented as a placeholder.

    Without this the id rule fires on every ADR that writes -1001234567890 and
    every fixture that writes 9999999999. Measured over the tracked tree, the
    bare-id shape alone produced 186 hits, effectively all of them synthetic --
    a rule that wrongly refuses is as real a defect as one that misses.

    Deliberately NARROW, because every false positive here is a LEAK, not a
    nuisance. Two earlier versions were too generous and swallowed ids in the
    live Telegram range: an "any six steps of +/-1" run matched oscillation as
    well as sequences (so -1003456789012 was skipped), and a trailing-zeros
    branch skipped anything ending in six zeros (so -1003400000000 was too).
    Now a run must be MONOTONIC and at least 8 long, and roundness is not a
    signal at all -- the repository's -1009999xxxxxx convention is an exact
    allow-list entry, which is better than a probabilistic guess.
    """
    if len(set(digits)) <= 3:  # 9999999999, 1500000000
        return True
    run, direction = 1, 0
    for a, b in zip(digits, digits[1:], strict=False):
        step = int(b) - int(a)
        if step in (1, -1) and (direction == 0 or step == direction):
            run, direction = run + 1, step
        else:
            run, direction = (2, step) if step in (1, -1) else (1, 0)
        if run >= 8:  # 1234567890 / 9876543210, monotonic only
            return True
    return False


def load_patterns() -> list[Rule]:
    if not PATTERNS_FILE.exists():
        sys.exit(f"missing {PATTERNS_FILE.name}")
    doc = tomllib.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
    out = []
    for entry in doc.get("pattern", []):
        out.append(
            Rule(
                id=entry["id"],
                regex=re.compile(entry["regex"]),
                why=entry["why"],
                skip_synthetic_ids=bool(entry.get("skip_synthetic_ids", False)),
                allow=[re.compile(a) for a in entry.get("allow", [])],
                exempt_paths=frozenset(entry.get("exempt_paths", [])),
            )
        )
    return out


def load_literals() -> list[tuple[str, str]]:
    """(value, label) pairs from the gitignored local file. Absent file = no literals."""
    if not LITERALS_FILE.exists():
        return []
    doc = tomllib.loads(LITERALS_FILE.read_text(encoding="utf-8"))
    return [(e["value"], e.get("label", "denylisted value")) for e in doc.get("literal", [])]


def scan_text(
    text: str,
    path: str,
    patterns: list[Rule],
    literals: list[tuple[str, str]],
    where: str = "",
) -> Iterator[Violation]:
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        waived = set(WAIVER_RE.findall(line))
        # Three deliberate asymmetries, all in the same direction -- a SHAPE rule
        # is a heuristic and may be relaxed, a KNOWN REAL value never is:
        #   * a shape rule may be exempted for a whole file, a literal may not;
        #   * a shape rule may be waived on a line, a literal may NOT. The
        #     sibling guard learned this first (its WAIVABLE_RULES exists
        #     because the marker is written by whoever is being inspected):
        #     a one-line comment must not switch off the one check that already
        #     knows the value is real. Fix the value instead;
        #   * a literal is matched through digit separators and through every
        #     alternate spelling this codebase emits (see literal_forms).
        shadow = _SEPARATED_DIGITS.sub("", line)
        for value, label in literals:
            for form in literal_forms(value):
                if form in line or form in shadow:
                    yield Violation(
                        path,
                        lineno,
                        "literal",
                        f"known real identifier ({label}){where}",
                        redact(value),
                    )
                    break
        for rule in patterns:
            if rule.id in waived or path in rule.exempt_paths or path in SELF_EXEMPT:
                continue
            for match in rule.regex.finditer(line):
                hit = match.group(0)
                if rule.skip_synthetic_ids and is_synthetic_id(re.sub(r"\D", "", hit)):
                    continue
                if any(a.fullmatch(hit) for a in rule.allow):
                    continue
                yield Violation(path, lineno, rule.id, f"{rule.why}{where}", redact(hit))


def tracked_files() -> list[str]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [p for p in raw.split("\0") if p]


def add_literal() -> int:
    """Append a value read from stdin, so it never lands in shell history.

    The value is JSON-serialised rather than interpolated. A TOML basic string
    processes backslash escapes, so a raw f-string write turned a value
    containing `\\t` into a real tab on the next read -- the guard then
    reported "N literal(s)" while searching for something that can never
    appear, which is a fail-open in the one place the operator believes they
    have just protected a value. A bare quote wedged the file outright.
    """
    value = sys.stdin.readline().strip()
    if not value:
        sys.exit("nothing on stdin")
    label = sys.stdin.readline().strip() or "unlabelled"

    previous = LITERALS_FILE.read_bytes() if LITERALS_FILE.exists() else None
    with LITERALS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"\n[[literal]]\nvalue = {json.dumps(value)}\nlabel = {json.dumps(label)}\n")

    # Read it back: a write that stores something else, or breaks the file for
    # every later run, must fail loudly now rather than silently later.
    try:
        stored = [e["value"] for e in tomllib.loads(LITERALS_FILE.read_text("utf-8"))["literal"]]
    except Exception as exc:  # noqa: BLE001 -- restoring matters more than the type
        if previous is not None:
            LITERALS_FILE.write_bytes(previous)
        sys.exit(f"refused: the value broke {LITERALS_FILE.name} ({exc}); file restored")
    if value not in stored:
        if previous is not None:
            LITERALS_FILE.write_bytes(previous)
        sys.exit("refused: the value did not survive the round trip; file restored")

    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    print(f"added literal ({len(value)} chars, sha256:{digest}…) as {label!r}, round trip verified")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--add-literal", action="store_true", help="read value+label from stdin")
    ap.add_argument(
        "--require-literals",
        action="store_true",
        help="fail if the local literals file is absent (pre-commit passes this; CI does not, "
        "because the file is gitignored and cannot exist there)",
    )
    args = ap.parse_args()

    if args.add_literal:
        return add_literal()

    patterns = load_patterns()
    literals = load_literals()
    if not literals:
        # Loud, on stderr, and never folded into a line that says "clean".
        # The literal half is the ONLY cover for values with no shape, and its
        # absence is the default state in CI, a fresh clone and a worktree.
        print(
            f"denylist: WARNING -- {LITERALS_FILE.name} is absent or empty, so only "
            f"{len(patterns)} shape rule(s) ran. Values with no shape are NOT checked.",
            file=sys.stderr,
        )
        if args.require_literals:
            print(
                "denylist: refusing to pass -- seed it with:\n"
                "    printf '%s\\n%s\\n' '<value>' '<label>' | "
                "python3 scripts/check_denylist.py --add-literal",
                file=sys.stderr,
            )
            return 2
    targets = args.paths or tracked_files()

    violations: list[Violation] = []
    unscannable: list[str] = []
    scanned = 0
    for rel in targets:
        if rel == LITERALS_FILE.name:
            continue
        # The PATH is checked as well as the contents. A chat id in a filename
        # is more visible than one in a line of body text -- it shows in the
        # GitHub tree, the commit list and search -- and scanning only contents
        # published it while reporting clean.
        violations.extend(scan_text(rel, rel, patterns, literals, where="in the file name"))
        path = REPO_ROOT / rel
        try:
            # errors="replace" so an odd encoding is still SCANNED rather than
            # skipped; only a genuine I/O failure reaches the handler below.
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            unscannable.append(f"{rel}: {exc.__class__.__name__}: {exc}")
            continue
        scanned += 1
        violations.extend(scan_text(text, rel, patterns, literals))

    # "Could not check" must never render as "clean" -- the silent zero this
    # repository keeps relearning. The sibling guard reserves exit 2 for it.
    if unscannable:
        print(f"denylist: could NOT scan {len(unscannable)} file(s):", file=sys.stderr)
        for line in unscannable:
            print(f"    {line}", file=sys.stderr)
        return 2

    if violations:
        print(f"denylist: {len(violations)} violation(s) in {scanned} file(s)\n", file=sys.stderr)
        for v in violations:
            print(v.render(), file=sys.stderr)
        print(
            "\nFix the value, or waive one line with an inline '# denylist-ok: <rule>' marker.",
            file=sys.stderr,
        )
        return 1

    literal_note = f", {len(literals)} literal(s)" if literals else ", SHAPES ONLY"
    print(f"denylist clean ({scanned} file(s), {len(patterns)} pattern(s){literal_note})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
