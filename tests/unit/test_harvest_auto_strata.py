"""Tests for scripts/harvest_auto_strata.py (S3-6: auto-strata harvest)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.eval_schema import EvalCase, load_cases
from scripts.harvest_auto_strata import (
    MEMORY_SEEKING_REGEX,
    _case_from_row,
    _parse_args,
    harvest_cases,
    write_cases,
)


def _row(**overrides: object) -> dict[str, Any]:
    base: dict[str, object] = {
        "chat_id": -1009999990001,
        "trigger_message_id": 305,
        "created_at": datetime(2026, 7, 15, 14, 45, 0, tzinfo=UTC),
        "question": "А о чем мы говорили до этого?",
    }
    base.update(overrides)
    return base


class FakeConnection:
    """Minimal stand-in for asyncpg.Connection -- records the call, returns
    scripted rows. asyncpg.Record supports ``row["col"]`` access, which a
    plain dict already provides, so no extra fake type is needed."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fetch_calls: list[tuple[Any, ...]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((query, *args))
        return self.rows


class TestCaseFromRow:
    def test_builds_found_stratum_case_with_wide_placeholder_range(self) -> None:
        case = _case_from_row(_row())

        assert case.stratum == "found"
        assert case.chat_id == -1009999990001
        assert case.question == "А о чем мы говорили до этого?"
        assert case.asked_at == datetime(2026, 7, 15, 14, 45, 0, tzinfo=UTC)
        # S3-6: the range is an honest wide placeholder ("anything up to the
        # question"), not a fabricated pinpoint -- start=1, end=trigger id.
        assert len(case.expected_message_id_ranges) == 1
        assert case.expected_message_id_ranges[0].start == 1
        assert case.expected_message_id_ranges[0].end == 305

    def test_note_flags_case_as_unverified_auto_harvest(self) -> None:
        case = _case_from_row(_row())

        assert "S3-6" in case.note
        assert "unverified" in case.note.lower()

    def test_result_is_a_valid_eval_case(self) -> None:
        # EvalCase's own validators (asked_at tz-aware, non-empty ranges for
        # stratum="found") must accept every harvested row -- this is the
        # exact schema S3-1's real golden set is validated against too.
        case = _case_from_row(_row())
        assert isinstance(case, EvalCase)


class TestHarvestCases:
    @pytest.mark.asyncio
    async def test_returns_one_case_per_row(self) -> None:
        conn = FakeConnection([_row(chat_id=-1), _row(chat_id=-2)])

        cases = await harvest_cases(conn, limit=60)  # type: ignore[arg-type]

        assert [c.chat_id for c in cases] == [-1, -2]

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self) -> None:
        conn = FakeConnection([])

        cases = await harvest_cases(conn, limit=60)  # type: ignore[arg-type]

        assert cases == []

    @pytest.mark.asyncio
    async def test_query_passes_the_ported_regex_and_limit(self) -> None:
        """The harvest query must use the exact regex ported from
        internal/analysis/q5_replay.py:31-35 -- this is a like-for-like
        move into the tracked harness, not a redesign, and a silently
        drifted regex would harvest a different (unverified) corpus."""
        conn = FakeConnection([])

        await harvest_cases(conn, limit=42)  # type: ignore[arg-type]

        assert len(conn.fetch_calls) == 1
        _query, trigger_types, regex, min_len, max_len, limit = conn.fetch_calls[0]
        assert regex == MEMORY_SEEKING_REGEX
        assert set(trigger_types) == {"trigger", "reply_to_bot", "reply"}
        assert (min_len, max_len) == (15, 400)
        assert limit == 42


class TestWriteCases:
    def test_round_trips_through_load_cases(self, tmp_path: Path) -> None:
        """S3-1's mandate: the auto-harvest output must validate against the
        SAME schema as the tracked template and the real golden set -- write
        it out and read it back through the shared ``load_cases()`` entry
        point, not just assert the in-memory objects look right."""
        cases = [_case_from_row(_row(chat_id=-1)), _case_from_row(_row(chat_id=-2))]
        out = tmp_path / "cases_auto_harvest.json"

        write_cases(cases, out)
        loaded = load_cases(out)

        assert [c.chat_id for c in loaded] == [-1, -2]
        assert all(c.stratum == "found" for c in loaded)

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "internal" / "eval" / "cases_auto_harvest.json"
        cases = [_case_from_row(_row())]

        write_cases(cases, out)

        assert out.exists()
        assert len(load_cases(out)) == 1


class TestParseArgs:
    def test_dsn_is_required(self) -> None:
        with pytest.raises(SystemExit):
            _parse_args([])

    def test_defaults(self) -> None:
        args = _parse_args(["postgresql://r:r@127.0.0.1:55435/n8n"])

        assert args.dsn == "postgresql://r:r@127.0.0.1:55435/n8n"
        assert args.out == Path("internal/eval/cases_auto_harvest.json")
        assert args.limit == 60

    def test_out_and_limit_overrides(self) -> None:
        args = _parse_args(
            [
                "postgresql://r:r@127.0.0.1:55435/n8n",
                "--out",
                "tmp/cases.json",
                "--limit",
                "5",
            ]
        )

        assert args.out == Path("tmp/cases.json")
        assert args.limit == 5
