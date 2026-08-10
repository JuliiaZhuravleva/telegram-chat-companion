"""Tests for scripts/eval_schema.py (S3-1: shared eval-case schema)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.eval_schema import EvalCase, EvalCaseFileError, load_cases, main

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "eval" / "cases.json"


def _valid_case(**overrides: object) -> dict:
    base = {
        "chat_id": -1009999990001,
        "question": "Where do we meet on Friday?",
        "asked_at": "2026-05-10T18:00:00+00:00",
        "expected_message_id_ranges": [{"start": 10, "end": 12}],
        "stratum": "found",
        "note": "The answer is in a single message.",
    }
    base.update(overrides)
    return base


class TestEvalCaseModel:
    def test_valid_case_parses(self) -> None:
        case = EvalCase.model_validate(_valid_case())
        assert case.chat_id == -1009999990001
        assert case.stratum == "found"
        assert case.expected_message_id_ranges[0].start == 10

    def test_missing_asked_at_is_rejected(self) -> None:
        payload = _valid_case()
        del payload["asked_at"]
        with pytest.raises(ValidationError, match="asked_at"):
            EvalCase.model_validate(payload)

    def test_naive_asked_at_is_rejected(self) -> None:
        payload = _valid_case(asked_at="2026-05-10T18:00:00")
        with pytest.raises(ValidationError, match="timezone-aware"):
            EvalCase.model_validate(payload)

    def test_answer_absent_requires_empty_ranges(self) -> None:
        payload = _valid_case(
            stratum="answer-absent",
            expected_message_id_ranges=[{"start": 1, "end": 1}],
        )
        with pytest.raises(ValidationError, match="answer-absent"):
            EvalCase.model_validate(payload)

    def test_answer_absent_with_empty_ranges_is_valid(self) -> None:
        case = EvalCase.model_validate(
            _valid_case(stratum="answer-absent", expected_message_id_ranges=[])
        )
        assert case.expected_message_id_ranges == []

    @pytest.mark.parametrize("stratum", ["found", "knowledge-update"])
    def test_non_absent_stratum_requires_a_range(self, stratum: str) -> None:
        payload = _valid_case(stratum=stratum, expected_message_id_ranges=[])
        with pytest.raises(ValidationError, match="requires at least one"):
            EvalCase.model_validate(payload)

    def test_range_end_before_start_is_rejected(self) -> None:
        payload = _valid_case(expected_message_id_ranges=[{"start": 20, "end": 5}])
        with pytest.raises(ValidationError, match="before start"):
            EvalCase.model_validate(payload)

    def test_empty_question_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalCase.model_validate(_valid_case(question=""))

    def test_unknown_stratum_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalCase.model_validate(_valid_case(stratum="made-up"))

    def test_case_is_frozen(self) -> None:
        case = EvalCase.model_validate(_valid_case())
        with pytest.raises(ValidationError):
            case.question = "changed"  # type: ignore[misc]


class TestLoadCases:
    def test_loads_the_tracked_template(self) -> None:
        cases = load_cases(TEMPLATE_PATH)
        assert len(cases) >= 3
        strata = {c.stratum for c in cases}
        assert strata == {"found", "knowledge-update", "answer-absent"}

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(EvalCaseFileError, match="cannot read"):
            load_cases(tmp_path / "does-not-exist.json")

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text("{not valid json")
        with pytest.raises(EvalCaseFileError, match="not valid JSON"):
            load_cases(path)

    def test_non_array_top_level_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text(json.dumps({"cases": []}))
        with pytest.raises(EvalCaseFileError, match="expected a JSON array"):
            load_cases(path)

    def test_invalid_case_reports_index_and_all_errors(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        bad = _valid_case()
        del bad["asked_at"]
        path.write_text(json.dumps([_valid_case(), bad, bad]))
        with pytest.raises(EvalCaseFileError) as exc_info:
            load_cases(path)
        message = str(exc_info.value)
        assert "2 invalid case(s)" in message
        assert "case[1]" in message
        assert "case[2]" in message


class TestCli:
    def test_main_exits_zero_on_valid_file(self) -> None:
        assert main([str(TEMPLATE_PATH)]) == 0

    def test_main_exits_nonzero_on_invalid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text("[]")
        bad = _valid_case()
        del bad["stratum"]
        path.write_text(json.dumps([bad]))
        assert main([str(path)]) == 1

    def test_main_with_no_args_exits_two(self) -> None:
        assert main([]) == 2
