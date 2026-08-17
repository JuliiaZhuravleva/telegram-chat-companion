"""Guard the plan-orchestration artifacts that this public repo tracks on purpose.

    python3 scripts/check_plan_artifacts.py            # every tracked docs/plans file
    python3 scripts/check_plan_artifacts.py <paths...> # named files (pre-commit)

``docs/plans/*.execution.md``, ``*.log.jsonl``, ``*.verdicts/*.json`` and
``*.progress`` are **deliberately** committed as the audit trail of a plan run —
only ``*.execution.md.raw/`` is gitignored. So everything an orchestrator agent
writes into the other four sinks reaches a public repository, and the only thing
standing between "an agent wrote it" and "the world can read it" used to be the
writer's own care.

That is not a hypothetical. On 2026-07-24 the risk was recognised (TD-021) and
half-fixed: the ``.raw/`` sidecar was gitignored while ``.log.jsonl`` was
classified as a structured audit trail — but its ``dispatch_error`` branch
carried the same payload, up to 4000 characters of ``claude -p`` output captured
with ``2>&1``, i.e. stderr, which is where auth tokens surface inside error
traces. The first such record was committed three hours later. Separately, the
absolute developer path in ``approve_action``/``reject_action`` was scrubbed in
PR #25 on 2026-08-05 and had **regenerated itself** by 2026-08-06, because the
generator that writes it lives outside this repository.

Both leaks share a shape: the fix lived in the tooling, so it could not protect
the repository against a tool that changed, regressed, or was simply not the one
that ran. This check is the repository's own layer. It is intentionally blind to
who produced the file.

What it looks for, derived from the threat rather than from any writer's code:

* ``oversize-value`` — in ``*.log.jsonl`` only, a string value far longer than
  any structured field. Measured on the nine tracked logs every legitimate value
  is <= 36 characters and the one leaked payload was 4012, so this separates "a
  field" from "a raw dump smuggled into a field" without needing to recognise
  what leaked. It deliberately does **not** apply to ``*.verdicts/*.json``, whose
  ``notes``/``evidence`` fields are prose by design (longest legitimate: 3332
  chars) — a gate that fires on 92 files for doing their job gets switched off.
* ``captured-output`` — a field *named* like process output (``raw``, ``stdout``,
  ``stderr``, ``traceback``…) holding more than a short excerpt, in any structured
  artifact. This is the rule that would have caught the original leak on day one.
* ``credential`` — token, key, JWT, ``op://`` and private-key shapes. Overlaps
  gitleaks by design: gitleaks scans the diff, this scans the tree, and a file
  can enter the tree by a route that never showed the credential in a diff.
* ``home-path`` — an absolute ``/Users/<name>/`` or ``/home/<name>/``. Has no
  credential shape, so gitleaks structurally cannot see it.
* ``telegram-id`` — ``-100…`` chat ids and bare 9-11 digit ids, the rule CLAUDE.md
  states in prose (TD-015 asked for it in code).

In JSON and JSONL only **string** values are scanned for ids, so the dispatcher's
own numeric telemetry (token counts, durations) cannot produce a false hit. A
line that does not parse is scanned as text rather than skipped: a malformed
record must not become a silent pass.

An oversized JSON document pasted into a ``.md`` write-up is caught too, since
the size rules above only see values inside a parsed artifact.

``telegram-id`` — and **only** that rule, see :data:`WAIVABLE_RULES` — can be
waived with an explicit marker, so a legitimate 10-digit number in prose does not
force the pattern to be loosened for everyone::

    Ran with a budget of 1000000000 tokens.  <!-- check-plan-artifacts: allow telegram-id -->

Exit codes: 0 = clean; 1 = violations (details on stdout); 2 = the check could
not be completed (no git, unreadable file, anything unexpected) — never confused
with "clean", and never with "violations found" either.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

# Every legitimate value in the tracked logs measured <= 36 chars; the leaked
# payload was 4012. 600 sits far from both, and above the 400-char excerpt the
# current orchestrator writes plus its truncation marker.
MAX_VALUE_CHARS = 600

# Keys that hold captured process output wherever they appear. A short redacted
# excerpt for triage is fine; anything more is the raw dump this check exists for.
CAPTURED_OUTPUT_KEYS = frozenset(
    {"raw", "raw_excerpt", "output", "stdout", "stderr", "traceback", "payload", "response"}
)

STRUCTURED_SUFFIXES = {".json", ".jsonl"}

# Per-rule waiver marker, e.g. `<!-- check-plan-artifacts: allow telegram-id -->`.
# Rule-specific on purpose: a blanket "ignore this line" would also wave through
# a credential that happens to sit next to the number being excused.
_ALLOW_RE = re.compile(r"check-plan-artifacts:\s*allow\s+([a-z][a-z-]*)")

# …and only this rule may be waived at all. The marker lives INSIDE the file
# being scanned, and these files are written by orchestrator agents — the exact
# authors this check exists to be independent of. A waivable `credential` rule
# would let the thing under inspection switch off its own inspection by emitting
# one line of text. `telegram-id` is the only rule with a real false-positive
# surface (any bare 9-11 digit number in prose), so it is the only one that
# needs an escape hatch. Markers naming any other rule are ignored, and the
# violation still fires — the safe direction, and visible immediately.
WAIVABLE_RULES = frozenset({"telegram-id"})

# (rule, pattern, why) — kept as a table so the unit tests can assert that every
# rule has at least one adversarial fixture behind it.
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "credential",
        re.compile(
            r"sk-[A-Za-z0-9_-]{6,}"
            r"|gh[pousr]_[A-Za-z0-9]{6,}"
            r"|github_pat_[A-Za-z0-9_]{6,}"
            r"|AKIA[0-9A-Z]{8,}"
            r"|xox[baprs]-[A-Za-z0-9-]{8,}"
            r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}"
            r"|op://[^\s\"']+"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
            r"|(?i:\b(?:bearer|authorization:)\s+[A-Za-z0-9._~+/=-]{8,})"
            r"|(?i:\b[A-Z_]*(?:TOKEN|SECRET|PASSWORD|API_?KEY)\s*[=:]\s*[\"']?[A-Za-z0-9_\-./+]{8,})"
        ),
        "credential-shaped string",
    ),
    (
        "home-path",
        re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"),
        "absolute developer path (use a placeholder such as <projects>/)",
    ),
    (
        "telegram-id",
        re.compile(r"-100\d{10}|(?<![0-9A-Za-z_.-])\d{9,11}(?![0-9A-Za-z_.-])"),
        "looks like a Telegram chat or user id",
    ),
]


class Violation(NamedTuple):
    path: str
    line: int
    rule: str
    detail: str
    excerpt: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}\n    {self.excerpt}"


def _excerpt(text: str, start: int, end: int, width: int = 30) -> str:
    lo = max(0, start - width)
    hi = min(len(text), end + width)
    body = text[lo:hi].replace("\n", "\\n")
    return f"…{body}…" if (lo, hi) != (0, len(text)) else body


def scan_text(text: str, path: str, line: int, where: str) -> Iterator[Violation]:
    """Apply every regex rule to one blob of text."""
    for rule, pattern, why in RULES:
        for match in pattern.finditer(text):
            yield Violation(
                path=path,
                line=line,
                rule=rule,
                detail=f"{why}{where}",
                excerpt=_excerpt(text, match.start(), match.end()),
            )


def _walk(node: Any, trail: str = "", key: str = "") -> Iterator[tuple[str, str, str]]:
    """Yield (trail, leaf key, string value) for every string in a document."""
    if isinstance(node, str):
        yield trail or ".", key, node
    elif isinstance(node, dict):
        for child_key, value in node.items():
            yield from _walk(value, f"{trail}.{child_key}", child_key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{trail}[{index}]", key)


def scan_structured(text: str, path: str) -> Iterator[Violation]:
    """Scan a .json / .jsonl file through its parsed shape.

    Only string values are examined, which is what keeps numeric telemetry
    (``cache_read_input_tokens``, ``duration_api_ms``) from tripping the id rule.
    """
    is_lines = path.endswith(".jsonl")
    records: list[tuple[int, str]]
    records = (
        [(i, line) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
        if is_lines
        else [(1, text)]
    )

    for line_no, chunk in records:
        try:
            document = json.loads(chunk)
        except json.JSONDecodeError:
            # Fail loud, not silent: an unparseable record still gets scanned,
            # and is reported so nobody reads the pass as "this file is fine".
            yield Violation(
                path=path,
                line=line_no,
                rule="malformed",
                detail="record is not valid JSON; scanned as raw text instead",
                excerpt=chunk[:80],
            )
            yield from scan_text(chunk, path, line_no, "")
            continue

        for trail, key, value in _walk(document):
            if len(value) > MAX_VALUE_CHARS:
                # Two different questions: "is this an event log, where every
                # legitimate field is tiny?" and "is this field named like
                # captured process output, wherever it lives?"
                rule = (
                    "captured-output"
                    if key in CAPTURED_OUTPUT_KEYS
                    else ("oversize-value" if is_lines else "")
                )
                if rule:
                    yield Violation(
                        path=path,
                        line=line_no,
                        rule=rule,
                        detail=(
                            f"{len(value)} chars in {trail} (limit {MAX_VALUE_CHARS}) — "
                            "raw agent output does not belong in a tracked artifact"
                        ),
                        excerpt=value[:80].replace("\n", "\\n") + "…",
                    )
            yield from scan_text(value, path, line_no, f" in {trail}")


def _display_path(path: Path, root: Path) -> str:
    """Repo-relative where possible, the path as given otherwise.

    ``Path.relative_to`` raises for a path outside ``root`` — which used to
    escape as an uncaught ``ValueError`` and exit 1, the code this script's own
    contract reserves for "violations found". "Could not check" reporting as
    "found something" is the same confusion as a silent zero, just inverted.
    """
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _allowed_rules(source_line: str) -> set[str]:
    """Rules waived on one line by an explicit marker.

    The id rule matches any bare 9-11 digit run, so a legitimate number in prose
    ("budget 1000000000 tokens") blocks a commit. The alternative — loosening the
    pattern until prose stops matching — trades a noisy gate for a quiet one,
    and this whole file exists because a quiet gate is worse. An explicit,
    per-rule waiver keeps detection at full strength and puts the decision in
    the diff where a reviewer can see it::

        Ran with a budget of 1000000000 tokens.  <!-- check-plan-artifacts: allow telegram-id -->

    Only :data:`WAIVABLE_RULES` can be excused; a marker naming anything else is
    ignored, because the marker is written by the same agents whose output this
    check is meant to police.
    """
    return set(_ALLOW_RE.findall(source_line)) & WAIVABLE_RULES


def scan_file(path: Path, root: Path) -> Iterator[Violation]:
    rel = _display_path(path, root)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    structured = path.suffix in STRUCTURED_SUFFIXES
    found = scan_structured(text, rel) if structured else _scan_plain(text, lines, rel)

    for violation in found:
        # Where a waiver may be written. A `.jsonl` record and a plain-text line
        # are both one line, so the marker sits on the offending line. A
        # pretty-printed `.json` is ONE record spread over many lines and every
        # violation in it is reported against line 1, so the marker is honoured
        # anywhere in that file — looking only at line 1 would mean `{`, i.e. a
        # waiver that can never be written. Only `telegram-id` is waivable, so
        # the widened scope cannot reach a credential.
        whole_doc = structured and not rel.endswith(".jsonl")
        source = (
            text
            if whole_doc
            else (lines[violation.line - 1] if 0 < violation.line <= len(lines) else "")
        )
        if violation.rule in _allowed_rules(source):
            continue
        yield violation


def _embedded_payloads(text: str, path: str) -> Iterator[Violation]:
    """Oversized JSON documents pasted into an unstructured file.

    ``raw_decode`` parses one value and reports where it ended, so this covers a
    pretty-printed payload spanning many lines as well as a single-line dump —
    the first version only matched the latter, and a paste out of a log viewer
    is just as likely to be indented.

    Parsing (never length alone) is the test, so prose cannot trip it.
    """
    decoder = json.JSONDecoder()
    for match in re.finditer(r"^[ \t]*[{\[]", text, re.MULTILINE):
        start = match.end() - 1
        try:
            _, end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        size = end - start
        if size <= MAX_VALUE_CHARS:
            continue
        yield Violation(
            path=path,
            line=text.count("\n", 0, start) + 1,
            rule="captured-output",
            detail=(
                f"{size} chars of embedded JSON (limit {MAX_VALUE_CHARS}) — "
                "raw agent output does not belong in a tracked artifact"
            ),
            excerpt=text[start : start + 80].replace("\n", "\\n") + "…",
        )


def _scan_plain(text: str, lines: list[str], path: str) -> Iterator[Violation]:
    """Markdown, .progress and anything else without a parseable structure."""
    yield from _embedded_payloads(text, path)
    for line_no, line in enumerate(lines, 1):
        yield from scan_text(line, path, line_no, "")


#: Tracked docs outside docs/plans/ that carry the same public-repo risk profile
#: (aggregate numbers written by a run, sourced from real chat content) and are
#: therefore required to pass this guard too. docs/rag-eval-baseline.md (S3-7):
#: a long-lived reference under docs/ rather than docs/plans/ (it outlives any
#: one plan), but it is written from the same eval-harness runs that produce
#: per-case detail in internal/, so it must be scanned the same way.
EXTRA_TRACKED_PATHS: tuple[str, ...] = ("docs/rag-eval-baseline.md", "docs/kb-eval-baseline.md")


def tracked_plan_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "docs/plans/", *EXTRA_TRACKED_PATHS],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {result.stderr.strip()}")
    return [root / name for name in result.stdout.split("\0") if name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, help="files to scan")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (default: the repo this script lives in)",
    )
    args = parser.parse_args(argv)

    try:
        targets = args.paths or tracked_plan_files(args.root)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    violations: list[Violation] = []
    for target in targets:
        try:
            violations.extend(scan_file(target, args.root))
        except Exception as exc:
            # Deliberately broad, and it must stay that way: ANY failure to scan
            # has to leave through exit 2. An escaping exception exits 1, which
            # this contract reserves for "violations found" — so a crash would
            # be read as a finding, and a caller that only distinguishes 0 from
            # non-zero would call an unscanned tree "checked".
            print(f"error: cannot scan {target}: {exc!r}", file=sys.stderr)
            return 2

    if not violations:
        print(f"docs/plans artifacts clean ({len(targets)} file(s) scanned)")
        return 0

    for violation in violations:
        print(violation.render())
    print(f"\n{len(violations)} violation(s) in {len({v.path for v in violations})} file(s).")
    print(
        "These files are public. Scrub the value (a placeholder keeps the audit trail "
        "readable), then re-run this check."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
