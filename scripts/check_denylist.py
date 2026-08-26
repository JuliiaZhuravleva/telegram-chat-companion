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

#: Files whose whole point is to contain the shapes below.
SELF_EXEMPT = {"scripts/check_denylist.py", ".secrets-denylist.toml"}


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
    """
    if len(set(digits)) <= 3:  # 9999999999, 1500000000
        return True
    if digits.endswith("0" * 6):  # -1004200000000; a real id ending in six zeros is 1-in-a-million
        return True
    run = 1
    for a, b in zip(digits, digits[1:], strict=False):
        # Both directions: 987654321 is as obviously invented as 123456789.
        run = run + 1 if abs(int(b) - int(a)) == 1 else 1
        if run >= 6:
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
) -> Iterator[Violation]:
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        waived = set(WAIVER_RE.findall(line))
        # Note the asymmetry, and it is deliberate: a SHAPE rule may be exempted
        # for a whole file (it is a heuristic, and a fixture file for a guard is
        # supposed to contain the shapes), but a KNOWN REAL value never is. So
        # pasting a live id into an exempt fixture file still fails the commit.
        for value, label in literals:
            if value in line and "literal" not in waived:
                yield Violation(
                    path, lineno, "literal", f"known real identifier ({label})", redact(value)
                )
        for rule in patterns:
            if rule.id in waived or path in rule.exempt_paths:
                continue
            for match in rule.regex.finditer(line):
                hit = match.group(0)
                if rule.skip_synthetic_ids and is_synthetic_id(re.sub(r"\D", "", hit)):
                    continue
                if any(a.fullmatch(hit) for a in rule.allow):
                    continue
                yield Violation(path, lineno, rule.id, rule.why, redact(hit))


def tracked_files() -> list[str]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [p for p in raw.split("\0") if p]


def add_literal() -> int:
    """Append a value read from stdin, so it never lands in shell history."""
    value = sys.stdin.readline().strip()
    if not value:
        sys.exit("nothing on stdin")
    label = sys.stdin.readline().strip() or "unlabelled"
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    with LITERALS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f'\n[[literal]]\nvalue = "{value}"\nlabel = "{label}"\n')
    print(
        f"added literal ({len(value)} chars, sha256:{digest}…) as {label!r} to {LITERALS_FILE.name}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--add-literal", action="store_true", help="read value+label from stdin")
    args = ap.parse_args()

    if args.add_literal:
        return add_literal()

    patterns = load_patterns()
    literals = load_literals()
    targets = args.paths or tracked_files()

    violations: list[Violation] = []
    scanned = 0
    for rel in targets:
        if rel in SELF_EXEMPT or rel == LITERALS_FILE.name:
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError, OSError):
            continue
        scanned += 1
        violations.extend(scan_text(text, rel, patterns, literals))

    if violations:
        print(f"denylist: {len(violations)} violation(s) in {scanned} file(s)\n", file=sys.stderr)
        for v in violations:
            print(v.render(), file=sys.stderr)
        print(
            "\nFix the value, or waive one line with an inline '# denylist-ok: <rule>' marker.",
            file=sys.stderr,
        )
        return 1

    literal_note = f", {len(literals)} literal(s)" if literals else ", no local literals file"
    print(f"denylist clean ({scanned} file(s), {len(patterns)} pattern(s){literal_note})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
