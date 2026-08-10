"""Shared schema for RAG eval cases (S3-1).

One validator for two files that must never drift apart:

* ``tests/fixtures/eval/cases.json`` — tracked synthetic template, fake
  chats/messages, committed so CI and any contributor can see the shape.
* ``internal/eval/cases.json`` — the real golden set (S3b/Q10), gitignored
  because it carries real chat content; filled in by Julia, not by this slice.

``internal/`` is gitignored wholesale (``.gitignore:114``), so the committed
template cannot physically live there — it lives here instead, in the tracked
tree, and both files are validated by the same ``EvalCase`` model so a schema
change to one cannot silently leave the other behind.

Case fields (docs/plans/rag-s3-eval-harness.md, S3-1):

* ``chat_id`` — privacy invariant, retrieval is always scoped by chat.
* ``question`` — the question text.
* ``asked_at`` — REQUIRED (S3-3): the moment the question was asked. The real
  search path (``MemoryRepository.search``) has no time bound; the eval
  harness needs one to filter out the memory of the question itself, and a
  case with no ``asked_at`` cannot be replayed correctly, so it must be
  rejected rather than silently accepted (S3-8).
* ``expected_message_id_ranges`` — inclusive ``[start, end]`` message_id
  range(s) the answer should come from (Q10's format). Empty for
  ``stratum="answer-absent"`` (nothing should be found); non-empty otherwise.
* ``stratum`` — ``found`` | ``knowledge-update`` | ``answer-absent``
  (roadmap §3 / analysis doc §5). ``answer-absent`` is the eval's negative
  control (S3-5) — it is what stops a lowered similarity threshold from
  looking like a pure win.
* ``note`` — free-form "what the bot should have done", from the behavior
  taxonomy in the analysis doc (§2/§3).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

Stratum = Literal["found", "knowledge-update", "answer-absent"]

STRATA: tuple[Stratum, ...] = ("found", "knowledge-update", "answer-absent")


class MessageIdRange(BaseModel):
    """Inclusive ``[start, end]`` range of ``message_id``."""

    model_config = {"frozen": True}

    start: int = Field(gt=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def _check_order(self) -> MessageIdRange:
        if self.end < self.start:
            raise ValueError(f"range end ({self.end}) is before start ({self.start})")
        return self


class EvalCase(BaseModel):
    """One eval case: a question, when it was asked, and where the answer lives."""

    model_config = {"frozen": True}

    chat_id: int
    question: str = Field(min_length=1)
    asked_at: datetime
    expected_message_id_ranges: list[MessageIdRange]
    stratum: Stratum
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _require_tz_aware_asked_at(self) -> EvalCase:
        # The real path stores TIMESTAMPTZ (alembic 001/002/003); a naive
        # asked_at would compare against it unpredictably (S3-3's `before:`
        # filter), so reject it here rather than let it misbehave at replay
        # time.
        if self.asked_at.tzinfo is None:
            raise ValueError("asked_at must be timezone-aware")
        return self

    @model_validator(mode="after")
    def _check_ranges_match_stratum(self) -> EvalCase:
        if self.stratum == "answer-absent":
            if self.expected_message_id_ranges:
                raise ValueError(
                    "stratum='answer-absent' means no answer exists in history; "
                    "expected_message_id_ranges must be empty"
                )
        elif not self.expected_message_id_ranges:
            raise ValueError(
                f"stratum={self.stratum!r} requires at least one expected_message_id_range"
            )
        return self


class EvalCaseFileError(ValueError):
    """Raised when an eval case file fails to parse or validate."""


def load_cases(path: Path) -> list[EvalCase]:
    """Load and validate every case in ``path``.

    Used on both the tracked template and the real (gitignored) golden set —
    see module docstring. Raises ``EvalCaseFileError`` with every case's
    validation errors aggregated, so a bad file reports all its problems at
    once rather than stopping at the first.
    """
    try:
        raw = json.loads(path.read_text())
    except OSError as e:
        raise EvalCaseFileError(f"{path}: cannot read: {e}") from e
    except json.JSONDecodeError as e:
        raise EvalCaseFileError(f"{path}: not valid JSON: {e}") from e

    if not isinstance(raw, list):
        raise EvalCaseFileError(f"{path}: expected a JSON array of cases, got {type(raw).__name__}")

    cases: list[EvalCase] = []
    errors: list[str] = []
    for i, item in enumerate(raw):
        try:
            cases.append(EvalCase.model_validate(item))
        except ValidationError as e:
            errors.append(f"case[{i}]: {e}")

    if errors:
        raise EvalCaseFileError(f"{path}: {len(errors)} invalid case(s):\n" + "\n\n".join(errors))

    return cases


def main(argv: list[str] | None = None) -> int:
    """CLI: validate one or more eval case files. Exit 0 iff all are valid."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python3 scripts/eval_schema.py <cases.json> [more.json ...]")
        return 2

    ok = True
    for arg in argv:
        path = Path(arg)
        try:
            cases = load_cases(path)
        except EvalCaseFileError as e:
            print(f"INVALID {e}")
            ok = False
            continue
        by_stratum = {s: sum(1 for c in cases if c.stratum == s) for s in STRATA}
        print(f"OK {path}: {len(cases)} case(s) {by_stratum}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
