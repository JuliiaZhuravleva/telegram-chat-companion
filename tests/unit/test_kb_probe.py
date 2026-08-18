"""Unit tests for the KB coverage probe's pure seams.

The probe's value is that it reports the *blind* side, which nothing else can
see. So the tests that matter are about the verdict boundary and about a run
that measured nothing still being loud.
"""

from __future__ import annotations

import io

import pytest

from scripts.kb_probe import (
    ProbeResult,
    classify,
    format_results,
    load_questions,
    summarize,
)


class TestClassify:
    def test_above_floor_with_room_is_an_answer(self) -> None:
        assert classify(0.81, floor=0.7, borderline_margin=0.05) == "WOULD ANSWER"

    def test_below_floor_is_blind(self) -> None:
        assert classify(0.64, floor=0.7, borderline_margin=0.05) == "BLIND"

    def test_a_hit_that_barely_clears_is_borderline(self) -> None:
        """The production hit at 0.706 against a 0.70 floor is the real case.

        Reporting it as a comfortable answer would hide that a slightly worse
        phrasing of the same question gets nothing.
        """
        assert classify(0.706, floor=0.7, borderline_margin=0.05) == "BORDERLINE"

    def test_exactly_at_the_floor_is_a_hit_not_a_miss(self) -> None:
        """The pipeline filters with `>=`, so the probe must agree.

        A probe that disagreed with retrieval at the boundary would report a
        question as blind that production answers.
        """
        assert classify(0.7, floor=0.7, borderline_margin=0.05) == "BORDERLINE"
        assert classify(0.7, floor=0.7, borderline_margin=0.0) == "WOULD ANSWER"

    def test_nothing_retrieved_is_blind(self) -> None:
        assert classify(None, floor=0.7, borderline_margin=0.05) == "BLIND"

    def test_zero_floor_means_nothing_can_be_blind(self) -> None:
        """Floor 0.0 is the pipeline's "no filtering" value.

        With filtering off, whatever comes back reaches the model, so reporting
        BLIND would describe a configuration that is not running.
        """
        assert classify(0.02, floor=0.0, borderline_margin=0.05) == "WOULD ANSWER"
        assert classify(-0.4, floor=0.0, borderline_margin=0.05) == "WOULD ANSWER"


class TestLoadQuestions:
    def test_inline_and_file_questions_combine(self, tmp_path) -> None:
        path = tmp_path / "q.txt"
        path.write_text("во сколько созвон?\nкакие правила?\n", encoding="utf-8")

        questions = load_questions([str(path)], ["что за проектор?"])

        assert questions == ["что за проектор?", "во сколько созвон?", "какие правила?"]

    def test_comments_and_blanks_are_skipped(self, tmp_path) -> None:
        """A question file should be able to record where its questions came from.

        That provenance is not decoration: questions derived from the facts
        measure nothing, so the file needs somewhere to say otherwise.
        """
        path = tmp_path / "q.txt"
        path.write_text(
            "# taken from chat history 2026-08, not from the facts\n\nво сколько созвон?\n",
            encoding="utf-8",
        )

        assert load_questions([str(path)], []) == ["во сколько созвон?"]

    def test_stdin_is_readable_as_a_source(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("какие правила?\n"))

        assert load_questions(["-"], []) == ["какие правила?"]


class TestReport:
    def test_summary_counts_every_verdict_including_errors(self) -> None:
        results = [
            ProbeResult("a", "WOULD ANSWER", 0.9, []),
            ProbeResult("b", "BLIND", 0.4, []),
            ProbeResult("c", "ERROR", None, [], "embedding: boom"),
        ]

        counts = summarize(results)

        assert counts == {
            "WOULD ANSWER": 1,
            "BORDERLINE": 0,
            "BLIND": 1,
            "ERROR": 1,
        }

    def test_errors_are_not_counted_as_answered(self) -> None:
        """ "We could not ask" must never inflate coverage.

        An outage that read as 100% coverage would end the investigation it
        should have started.
        """
        results = [
            ProbeResult("a", "ERROR", None, [], "embedding: boom"),
            ProbeResult("b", "WOULD ANSWER", 0.9, []),
        ]

        report = format_results(results, floor=0.7, show_facts=False)

        assert "answered      1/2" in report
        assert "errors        1" in report

    def test_blind_questions_are_listed_first(self) -> None:
        """They are the finding; a report that buries them is a worse report."""
        results = [
            ProbeResult("хороший вопрос", "WOULD ANSWER", 0.9, []),
            ProbeResult("слепой вопрос", "BLIND", 0.4, []),
        ]

        report = format_results(results, floor=0.7, show_facts=False)

        assert report.index("слепой вопрос") < report.index("хороший вопрос")


@pytest.mark.parametrize("floor", [0.0, 0.7, 1.0])
def test_classify_never_raises_on_any_floor(floor: float) -> None:
    for sim in (None, -1.0, 0.0, 0.5, 1.0):
        assert classify(sim, floor=floor, borderline_margin=0.05) in {
            "WOULD ANSWER",
            "BORDERLINE",
            "BLIND",
        }
