"""Tests for scripts/eval_metrics.py (S3-4: recall@k / MRR / blind-rate / best-sim)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from scripts.eval_metrics import Metrics, _percentile, compute_metrics, format_metrics
from scripts.eval_rag import CaseResult
from scripts.eval_schema import EvalCase


def _make_case(**overrides: object) -> EvalCase:
    base: dict[str, object] = {
        "chat_id": -1009999990001,
        "question": "Where do we meet on Friday?",
        "asked_at": datetime(2026, 5, 10, 18, 0, 0, tzinfo=UTC),
        "expected_message_id_ranges": [{"start": 140, "end": 142}],
        "stratum": "found",
        "note": "note",
    }
    base.update(overrides)
    return EvalCase.model_validate(base)


def _hit(message_id: int | None, similarity: float) -> dict[str, Any]:
    return {
        "id": 1,
        "content": "x",
        "similarity": similarity,
        "metadata": None,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "source_message_id": message_id,
    }


class TestPercentile:
    def test_single_value_returns_itself(self) -> None:
        assert _percentile([0.5], 50) == 0.5

    def test_even_split_interpolates(self) -> None:
        # numpy's default (linear) method: [1,2,3,4] p50 == 2.5
        assert _percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5

    def test_p0_and_p100_are_endpoints(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]
        assert _percentile(values, 0) == 1.0
        assert _percentile(values, 100) == 4.0


class TestRecallAndMrr:
    def test_found_case_hit_in_range_counts(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[_hit(141, 0.9)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 1.0
        assert metrics.mrr == 1.0
        assert metrics.n_recall_cases == 1

    def test_found_case_no_hit_in_range_is_a_miss(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[_hit(999, 0.9)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 0.0
        assert metrics.mrr == 0.0

    def test_found_case_accepts_any_of_multiple_ranges(self) -> None:
        case = _make_case(
            stratum="found",
            expected_message_id_ranges=[{"start": 10, "end": 10}, {"start": 300, "end": 305}],
        )
        result = CaseResult(case=case, hits=[_hit(302, 0.9)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 1.0

    def test_knowledge_update_hit_only_in_stale_range_is_a_miss(self) -> None:
        # Fixture semantics (tests/fixtures/eval/cases.json): stale range
        # listed first, fresh range listed last -- landing only on the
        # stale one must NOT count as correct (S3-5).
        case = _make_case(
            stratum="knowledge-update",
            expected_message_id_ranges=[{"start": 88, "end": 88}, {"start": 201, "end": 203}],
        )
        result = CaseResult(case=case, hits=[_hit(88, 0.95)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 0.0
        assert metrics.mrr == 0.0

    def test_knowledge_update_hit_in_fresh_range_counts(self) -> None:
        case = _make_case(
            stratum="knowledge-update",
            expected_message_id_ranges=[{"start": 88, "end": 88}, {"start": 201, "end": 203}],
        )
        result = CaseResult(case=case, hits=[_hit(88, 0.95), _hit(202, 0.85)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 1.0
        # rank 2 -> reciprocal 0.5
        assert metrics.mrr == pytest.approx(0.5)

    def test_answer_absent_excluded_from_recall_denominator(self) -> None:
        found = _make_case(stratum="found", expected_message_id_ranges=[{"start": 1, "end": 1}])
        absent = _make_case(stratum="answer-absent", expected_message_id_ranges=[])
        results = [
            CaseResult(case=found, hits=[_hit(1, 0.9)]),
            CaseResult(case=absent, hits=[]),
        ]

        metrics = compute_metrics(results, k=5)

        assert metrics.n_recall_cases == 1
        assert metrics.recall_at_k == 1.0

    def test_hit_beyond_k_is_ignored(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 1, "end": 1}])
        hits = [_hit(99, 0.9), _hit(98, 0.8), _hit(1, 0.7)]
        result = CaseResult(case=case, hits=hits)

        metrics = compute_metrics([result], k=2)

        assert metrics.recall_at_k == 0.0

    def test_null_source_message_id_is_skipped_not_a_crash(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 1, "end": 1}])
        result = CaseResult(case=case, hits=[_hit(None, 0.9), _hit(1, 0.7)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 1.0
        assert metrics.mrr == pytest.approx(0.5)


class TestBlindAndNegativeControl:
    def test_blind_rate_counts_empty_hits_on_eligible_strata(self) -> None:
        found_empty = _make_case(
            stratum="found", expected_message_id_ranges=[{"start": 1, "end": 1}]
        )
        found_hit = _make_case(stratum="found", expected_message_id_ranges=[{"start": 2, "end": 2}])
        results = [
            CaseResult(case=found_empty, hits=[]),
            CaseResult(case=found_hit, hits=[_hit(2, 0.9)]),
        ]

        metrics = compute_metrics(results, k=5)

        assert metrics.blind_rate == pytest.approx(0.5)
        assert metrics.n_blind_eligible == 2

    def test_answer_absent_never_counted_toward_blind_rate(self) -> None:
        absent = _make_case(stratum="answer-absent", expected_message_id_ranges=[])
        result = CaseResult(case=absent, hits=[])

        metrics = compute_metrics([result], k=5)

        assert metrics.n_blind_eligible == 0
        assert metrics.blind_rate == 0.0

    def test_negative_control_rate_rewards_correct_empty_result(self) -> None:
        absent_correct = _make_case(stratum="answer-absent", expected_message_id_ranges=[])
        absent_wrong = _make_case(stratum="answer-absent", expected_message_id_ranges=[])
        results = [
            CaseResult(case=absent_correct, hits=[]),
            CaseResult(case=absent_wrong, hits=[_hit(5, 0.71)]),
        ]

        metrics = compute_metrics(results, k=5)

        assert metrics.negative_control_rate == pytest.approx(0.5)
        assert metrics.n_negative_control == 2


class TestBestSimPercentiles:
    def test_percentiles_use_each_cases_best_hit_only(self) -> None:
        case_a = _make_case()
        case_b = _make_case()
        results = [
            CaseResult(case=case_a, hits=[_hit(1, 0.6), _hit(2, 0.8)]),
            CaseResult(case=case_b, hits=[_hit(3, 1.0)]),
        ]

        metrics = compute_metrics([results[0], results[1]], k=5, percentiles=(50,))

        # best sims collected: [0.8, 1.0] -> sorted -> p50 interpolated == 0.9
        assert metrics.best_sim_percentiles[50] == pytest.approx(0.9)
        assert metrics.n_best_sim == 2

    def test_no_hits_anywhere_yields_empty_percentiles(self) -> None:
        case = _make_case()
        result = CaseResult(case=case, hits=[])

        metrics = compute_metrics([result], k=5)

        assert metrics.best_sim_percentiles == {}
        assert metrics.n_best_sim == 0

    def test_answer_absent_hits_still_contribute_to_percentiles(self) -> None:
        # best-sim distribution is stratum-agnostic (S3-4): it exists to
        # calibrate the floor across all traffic, not just positive cases.
        absent = _make_case(stratum="answer-absent", expected_message_id_ranges=[])
        result = CaseResult(case=absent, hits=[_hit(9, 0.71)])

        metrics = compute_metrics([result], k=5, percentiles=(50,))

        assert metrics.n_best_sim == 1
        assert metrics.best_sim_percentiles[50] == pytest.approx(0.71)


class TestEmbeddingErrorsExcluded:
    def test_embedding_error_case_excluded_from_every_rate(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 1, "end": 1}])
        result = CaseResult(case=case, hits=[], embedding_error="all providers failed")

        metrics = compute_metrics([result], k=5)

        assert metrics.n_recall_cases == 0
        assert metrics.n_blind_eligible == 0
        assert metrics.n_negative_control == 0
        assert metrics.n_best_sim == 0
        assert metrics.n_embedding_errors == 1

    def test_answer_absent_embedding_error_excluded_from_negative_control(self) -> None:
        case = _make_case(stratum="answer-absent", expected_message_id_ranges=[])
        result = CaseResult(case=case, hits=[], embedding_error="boom")

        metrics = compute_metrics([result], k=5)

        assert metrics.n_negative_control == 0
        assert metrics.n_embedding_errors == 1


class TestFormatMetrics:
    def test_format_includes_all_sections(self) -> None:
        metrics = Metrics(
            k=5,
            recall_at_k=0.75,
            mrr=0.5,
            n_recall_cases=4,
            blind_rate=0.25,
            n_blind_eligible=4,
            negative_control_rate=1.0,
            n_negative_control=2,
            best_sim_percentiles={50: 0.85},
            n_best_sim=3,
            n_embedding_errors=1,
        )

        text = format_metrics(metrics)

        assert "recall@5: 0.750" in text
        assert "MRR: 0.500" in text
        assert "blind rate" in text
        assert "negative-control rate" in text
        assert "p50=0.850" in text
        assert "1 case(s) with a query embedding_error" in text

    def test_format_with_no_hits_reports_percentiles_as_na(self) -> None:
        metrics = compute_metrics(
            [
                CaseResult(
                    case=_make_case(stratum="answer-absent", expected_message_id_ranges=[]), hits=[]
                )
            ],
            k=5,
        )

        text = format_metrics(metrics)

        assert "best-sim percentiles: n/a" in text


def _chunk_hit(msg_from: int, msg_to: int, similarity: float | None = 0.8) -> dict[str, Any]:
    """A `chat_chunks` hit: a span of messages, not a pointer to one."""
    return {
        "id": 1,
        "content": "x",
        "similarity": similarity,
        "rrf_score": 0.03,
        "vec_rank": 1,
        "fts_rank": None,
        "msg_from": msg_from,
        "msg_to": msg_to,
    }


class TestChunkHitsAreScoredByRange:
    """S5: two stores answer through this harness and their hits differ in shape.

    The failure this guards is not a wrong score but a *plausible* one: before
    range support, every chunk hit lacked `source_message_id` and scored as a
    miss, so a store returning perfect answers measured recall 0.000 -- a number
    indistinguishable from genuinely terrible retrieval.
    """

    def test_a_chunk_overlapping_the_expected_range_counts(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[_chunk_hit(130, 145)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 1.0
        assert metrics.n_range_matched == 1

    def test_a_chunk_that_ends_before_the_range_is_a_miss(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[_chunk_hit(100, 139)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 0.0
        assert metrics.n_range_matched == 0

    def test_a_chunk_that_starts_after_the_range_is_a_miss(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[_chunk_hit(143, 200)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 0.0

    def test_touching_the_range_by_one_message_counts(self) -> None:
        """Overlap is inclusive at both edges: a chunk ending exactly on the
        first expected message does contain it."""
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[_chunk_hit(100, 140)])

        assert compute_metrics([result], k=5).recall_at_k == 1.0

    def test_a_hit_with_neither_shape_is_a_miss_not_a_crash(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[{"id": 1, "content": "x", "similarity": 0.9}])

        assert compute_metrics([result], k=5).recall_at_k == 0.0

    def test_a_pointed_hit_is_not_counted_as_range_matched(self) -> None:
        """`n_range_matched` exists to warn that a number came from the loose
        chunk path; a `chat_memory` run must leave it at zero or the warning
        fires on every run and stops meaning anything."""
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[_hit(141, 0.9)])

        assert compute_metrics([result], k=5).n_range_matched == 0

    def test_the_report_says_the_number_is_not_comparable(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 140, "end": 142}])
        result = CaseResult(case=case, hits=[_chunk_hit(130, 145)])

        text = format_metrics(compute_metrics([result], k=5))

        assert "NOT COMPARABLE" in text
        assert "upper bound" in text


class TestRowsWithoutSimilarity:
    """A lexical hit on a not-yet-embedded chunk has no similarity.

    `max()` over a list holding one None raises TypeError, and it does so at
    the very last step of a run -- after every provider call is already paid
    for. That is a whole eval run lost to a row that should simply have been
    skipped.
    """

    def test_a_none_similarity_does_not_crash_the_percentiles(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 1, "end": 999}])
        result = CaseResult(case=case, hits=[_chunk_hit(10, 20, None), _chunk_hit(30, 40, 0.6)])

        metrics = compute_metrics([result], k=5)

        assert metrics.best_sim_percentiles[50] == pytest.approx(0.6)
        assert metrics.n_no_similarity == 1

    def test_a_case_whose_hits_all_lack_similarity_is_left_out_of_percentiles(self) -> None:
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 1, "end": 999}])
        result = CaseResult(case=case, hits=[_chunk_hit(10, 20, None)])

        metrics = compute_metrics([result], k=5)

        assert metrics.best_sim_percentiles == {}
        assert metrics.n_best_sim == 0
        assert metrics.n_no_similarity == 1

    def test_it_is_still_credited_as_a_hit_and_not_blind(self) -> None:
        """No similarity is a missing *score*, not a missing answer -- the row
        was returned and would be injected."""
        case = _make_case(stratum="found", expected_message_id_ranges=[{"start": 1, "end": 999}])
        result = CaseResult(case=case, hits=[_chunk_hit(10, 20, None)])

        metrics = compute_metrics([result], k=5)

        assert metrics.recall_at_k == 1.0
        assert metrics.blind_rate == 0.0
