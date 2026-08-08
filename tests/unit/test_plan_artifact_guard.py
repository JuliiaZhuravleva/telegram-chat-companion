"""Adversarial fixtures for the docs/plans artifact guard.

The guard's job is to report "clean", and a broken detector reports clean too.
So every rule here is driven from a **hostile input**, not from the guard's own
regexes: what could an orchestrator plausibly write into a tracked artifact that
must never reach a public repository? The two real incidents this repo has had
are both in the list — a raw ``claude -p`` payload in ``.log.jsonl`` (TD-021) and
an absolute developer path in ``approve_action`` (TD-042, which regenerated
itself after being scrubbed once).

The false-positive cases matter as much: the first draft of this guard flagged 92
files because verdict sidecars hold long prose on purpose, and a gate that fires
on correct work gets switched off rather than fixed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_plan_artifacts import RULES, main, scan_file


def write(tmp_path: Path, name: str, text: str, *, marker: str) -> Path:
    """Write a fixture and prove it carries the thing under test.

    A fixture whose hostile content silently failed to land is byte-identical to
    a clean one, and the assertion below is the only thing separating "the guard
    caught it" from "there was nothing to catch".
    """
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert marker in path.read_text(encoding="utf-8"), "fixture did not apply"
    return path


def rules_hit(path: Path, root: Path) -> set[str]:
    return {v.rule for v in scan_file(path, root)}


# --------------------------------------------------------------------------
# True positives — derived from what could leak, not from what the code matches
# --------------------------------------------------------------------------


def test_raw_wrapper_payload_in_event_log(tmp_path: Path) -> None:
    """The 2026-07-24 leak: 4000 chars of `claude -p` output in a tracked log.

    Built the way the dispatcher builds it — a *valid* record whose one field is
    enormous. Hand-concatenating the JSON instead produces an unparseable line,
    which the guard rejects for a different reason and proves nothing about this
    rule.
    """
    wrapper = json.dumps(
        {"is_error": True, "session_id": "a7a2fa94", "result": "assistant said: " + "x" * 3000}
    )
    record = {
        "ts": "2026-08-07T00:00:00Z",
        "event": "dispatch_error",
        "kind": "specialist_fresh",
        "item": "A-2",
        "raw": wrapper,
    }
    path = write(
        tmp_path,
        "p.execution.md.log.jsonl",
        json.dumps(record, separators=(",", ":")) + "\n",
        marker='"raw":',
    )
    hits = rules_hit(path, tmp_path)
    assert "malformed" not in hits, "fixture is not a valid record"
    assert "captured-output" in hits


def test_credential_inside_a_captured_stderr_trace(tmp_path: Path) -> None:
    """stderr is where tokens surface — the whole reason 2>&1 capture is dangerous."""
    key = "sk-" + "proj-AbCdEf0123456789xyz"  # split: see CREDENTIAL_SHAPES below
    path = write(
        tmp_path,
        "p.execution.md.log.jsonl",
        f'{{"ts":"t","event":"dispatch_error","raw_excerpt":"Traceback: auth failed for {key}"}}\n',
        marker=key,
    )
    assert "credential" in rules_hit(path, tmp_path)


# Assembled from fragments rather than written out. Testing a secret detector
# needs secret-shaped fixtures, and the repo's *other* secret detector — gitleaks,
# in pre-commit and in the `gitleaks` CI job — correctly refuses to let a literal
# token shape into a tracked file (it flagged two of these on the first run).
# Splitting them keeps that gate at full strength instead of adding allowlist
# entries, which would have to be maintained and could be over-broad. The strings
# are reassembled below, so the guard still sees the complete shape.
CREDENTIAL_SHAPES = [
    "Authorization: Bearer " + "ya29.a0AfB_" + "abcdef123456",
    "op:" + "//Claude-Access/openai/credential",
    "gh" + "p_" + "AbCdEf0123456789AbCdEf0123456789",
    "GITHUB_TOKEN" + "=" + "ghs_0123456789abcdefghij",
    "-----BEGIN OPENSSH PRIVATE KEY" + "-----",
    "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dBjftJeZ4CVP",
]


@pytest.mark.parametrize("secret", CREDENTIAL_SHAPES)
def test_credential_shapes_in_prose(tmp_path: Path, secret: str) -> None:
    """A verdict sidecar is prose, and prose is exactly where a pasted key hides."""
    path = write(
        tmp_path,
        "p.execution.md.verdicts/A-1-1.json",
        '{"verdict":"PASS","notes":"could not reach the API, tried ' + secret + '"}',
        marker=secret,
    )
    assert "credential" in rules_hit(path, tmp_path)


def test_absolute_home_path_in_an_envelope(tmp_path: Path) -> None:
    """TD-042: the generator lives outside this repo, so this leak comes back."""
    path = write(
        tmp_path,
        "p.execution.md",
        "review_gate:\n  approve_action: /execute-plan "
        "/Users/someone/my-projects/thing/docs/plans/p.execution.md --resume\n",
        marker="/Users/someone/",
    )
    assert "home-path" in rules_hit(path, tmp_path)


def test_absolute_home_path_on_a_linux_runner(tmp_path: Path) -> None:
    """Same leak, different platform — an agent running in a container writes /home."""
    path = write(
        tmp_path,
        "p.execution.md",
        "  approve_action: /execute-plan /home/runner/work/thing/docs/plans/p.md\n",
        marker="/home/runner/",
    )
    assert "home-path" in rules_hit(path, tmp_path)


def test_telegram_chat_and_user_ids(tmp_path: Path) -> None:
    """CLAUDE.md states this rule in prose; gitleaks cannot see it (no shape)."""
    supergroup = write(
        tmp_path,
        "a.execution.md",
        "QA ran against the test chat -1003908877878 with the admin account.\n",
        marker="-1003908877878",
    )
    assert "telegram-id" in rules_hit(supergroup, tmp_path)

    bare = write(
        tmp_path,
        "b.execution.md.verdicts/A-1-1.json",
        '{"verdict":"PASS","notes":"reproduced as user 5870677432 in the DM"}',
        marker="5870677432",
    )
    assert "telegram-id" in rules_hit(bare, tmp_path)


def test_malformed_record_is_reported_not_skipped(tmp_path: Path) -> None:
    """A line that does not parse must never become a silent pass."""
    path = write(
        tmp_path,
        "p.execution.md.log.jsonl",
        '{"ts":"t","event":"ok","item":"A-1"}\n{"ts":"t","raw":"op://vault/item/field"\n',
        marker="op://",
    )
    hits = rules_hit(path, tmp_path)
    assert "malformed" in hits, "unparseable record was skipped"
    assert "credential" in hits, "secret in an unparseable record went unscanned"


# --------------------------------------------------------------------------
# False positives — the cases that decide whether anyone keeps the gate on
# --------------------------------------------------------------------------


def test_long_verdict_prose_is_allowed(tmp_path: Path) -> None:
    """Verdict notes are the audit trail. Longest real one measured: 3332 chars."""
    path = write(
        tmp_path,
        "p.execution.md.verdicts/A-1-1.json",
        '{"verdict":"PASS","notes":"' + "Implemented per ADR-0007. " * 200 + '"}',
        marker="ADR-0007",
    )
    assert rules_hit(path, tmp_path) == set()


def test_numeric_telemetry_is_not_an_id(tmp_path: Path) -> None:
    """Token counts and durations are 9-11 digits often; they are numbers, not ids."""
    path = write(
        tmp_path,
        "p.execution.md.log.jsonl",
        '{"ts":"t","event":"item_done","cache_read_input_tokens":12005996643,'
        '"duration_ms":1234567890,"cost_usd":6.0809388}\n',
        marker="1234567890",
    )
    assert rules_hit(path, tmp_path) == set()


def test_commit_sha_is_not_an_id(tmp_path: Path) -> None:
    """A hex SHA contains long digit runs; the lookarounds must exclude them."""
    path = write(
        tmp_path,
        "p.execution.md",
        "Verified commit 916de6e8253b67a32e307216352d64eea6b66ccb against the ADR.\n",
        marker="916de6e8",
    )
    assert rules_hit(path, tmp_path) == set()


def test_relative_paths_and_placeholders_are_allowed(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "p.execution.md",
        "  approve_action: /execute-plan <projects>/thing/docs/plans/p.execution.md\n",
        marker="<projects>/",
    )
    assert rules_hit(path, tmp_path) == set()


# --------------------------------------------------------------------------
# Harness integrity
# --------------------------------------------------------------------------


def test_every_rule_has_an_adversarial_fixture() -> None:
    """A rule nobody tests is a rule nobody knows still fires."""
    covered = {"credential", "home-path", "telegram-id"}
    assert {name for name, _, _ in RULES} == covered


def test_exit_codes_distinguish_clean_from_dirty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """1 means "found something". It must never be reachable by crashing."""
    clean = write(tmp_path, "clean.execution.md", "status: done\n", marker="status")
    assert main([str(clean), "--root", str(tmp_path)]) == 0

    dirty = write(
        tmp_path,
        "dirty.execution.md",
        "  approve_action: /execute-plan /Users/someone/my-projects/x\n",
        marker="/Users/someone/",
    )
    assert main([str(dirty), "--root", str(tmp_path)]) == 1
    assert "home-path" in capsys.readouterr().out


def test_unreadable_target_is_error_not_clean(tmp_path: Path) -> None:
    """Exit 2, never 0 — "could not check" must not read as "nothing to find"."""
    assert main([str(tmp_path / "missing.execution.md"), "--root", str(tmp_path)]) == 2


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------


def test_tracked_plan_artifacts_are_clean() -> None:
    """Scan what this repository actually publishes.

    This lives in the ``test`` CI job on purpose. Running it as a step in ``lint``
    would mean editing ``.github/``, and per docs/deployment.md a commit that
    touches a workflow file is **held** from the automatic release — a permanent
    manual step for every future change here. ``test`` is already a required check
    and already watched by the deployer by name, so the enforcement is identical
    and costs nothing.

    Exit 2 (git missing, unreadable file) fails this too, which is deliberate: a
    check that could not run must not read as a clean tree.
    """
    root = Path(__file__).resolve().parents[2]
    assert main(["--root", str(root)]) == 0, (
        "docs/plans artifacts carry something that must not be public — "
        "run `python3 scripts/check_plan_artifacts.py` for the detail"
    )
