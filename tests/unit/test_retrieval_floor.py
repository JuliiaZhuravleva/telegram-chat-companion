"""Tests for the one shared retrieval-floor rule.

Cases are derived from the shapes a row can actually arrive in — a repository
row, a mocked fixture, a hand-built dict in a script — rather than from reading
the comparison back. The awkward ones (bool, NaN, a missing key) are exactly
the values that make a naive ``>=`` do something confident and wrong.

That both the knowledge-base and the RAG paths genuinely route through this
function is not asserted here — it cannot be, from here. It is pinned by their
own behaviour tests, and verified by control: shifting the comparison in this
file turns 2 knowledge-base tests and 3 RAG tests red in
tests/unit/test_text_pipeline.py.
"""

from __future__ import annotations

import math

import pytest

from src.services.retrieval_floor import rows_above_floor


def _row(similarity: object, row_id: int = 1) -> dict[str, object]:
    return {"id": row_id, "similarity": similarity}


class TestTheFloorItself:
    def test_a_row_exactly_at_the_floor_clears_it(self) -> None:
        """Inclusive. The KB floor was chosen as a value a real hit lands on."""
        assert rows_above_floor([_row(0.7)], 0.7) == [_row(0.7)]

    def test_a_row_just_below_is_dropped(self) -> None:
        assert rows_above_floor([_row(0.6999)], 0.7) == []

    def test_order_is_preserved(self) -> None:
        rows = [_row(0.9, 1), _row(0.5, 2), _row(0.8, 3)]

        assert [r["id"] for r in rows_above_floor(rows, 0.7)] == [1, 3]

    def test_the_input_is_not_mutated(self) -> None:
        rows = [_row(0.9), _row(0.1)]

        rows_above_floor(rows, 0.7)

        assert len(rows) == 2


class TestNoFloor:
    """``0.0`` is the documented rollback and the floor-sweep's baseline."""

    def test_zero_keeps_everything_including_negative_similarity(self) -> None:
        # Cosine similarity is defined on [-1, 1]. A `>= 0.0` test would cut
        # these, so "set it to 0.0 to retrieve everything" would be a lie.
        rows = [_row(0.9, 1), _row(-0.4, 2)]

        assert rows_above_floor(rows, 0.0) == rows

    def test_zero_keeps_rows_a_positive_floor_would_reject_as_malformed(self) -> None:
        rows = [_row(None, 1), _row("high", 2)]

        assert rows_above_floor(rows, 0.0) == rows

    def test_a_negative_floor_also_means_no_floor(self) -> None:
        assert rows_above_floor([_row(-0.9)], -0.5) == [_row(-0.9)]


class TestUnusableSimilarities:
    """Below any floor: a row cannot be *shown* to clear one."""

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "0.95",  # a string that would compare fine in some languages
            float("nan"),  # every comparison with NaN is False
            [0.95],
            {"value": 0.95},
        ],
    )
    def test_dropped_by_a_positive_floor(self, value: object) -> None:
        assert rows_above_floor([_row(value)], 0.7) == []

    def test_a_row_with_no_similarity_key_at_all_is_dropped(self) -> None:
        assert rows_above_floor([{"id": 1, "content": "hi"}], 0.7) == []

    @pytest.mark.parametrize("value", [True, False])
    def test_booleans_are_not_similarities(self, value: bool) -> None:
        """`bool` subclasses `int`, so `True` would compare as 1.0 and clear
        every floor there is — the most confident possible wrong answer."""
        assert rows_above_floor([_row(value)], 0.7) == []

    def test_nan_is_dropped_rather_than_kept_by_a_failed_comparison(self) -> None:
        # Guarding the reasoning, not just the outcome: if the implementation
        # ever inverts its condition, NaN is the value that silently flips.
        assert math.isnan(float("nan"))
        assert rows_above_floor([_row(float("nan"))], 0.7) == []
        assert rows_above_floor([_row(float("nan"))], 0.0) != []


class TestIntegerAndEdgeInputs:
    def test_an_integer_similarity_is_accepted(self) -> None:
        """pgvector returns floats, but scripts and fixtures hand-build rows."""
        assert rows_above_floor([_row(1)], 0.7) == [_row(1)]

    def test_empty_input(self) -> None:
        assert rows_above_floor([], 0.7) == []
        assert rows_above_floor([], 0.0) == []
