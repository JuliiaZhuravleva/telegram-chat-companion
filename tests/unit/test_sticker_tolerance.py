"""Tests for is_within_tolerance (ADR-0008 Decision 2)."""

import pytest

from src.services.modules.sticker.tolerance import is_within_tolerance


@pytest.mark.parametrize(
    ("score", "tolerance", "expected"),
    [
        (0.5, 0.5, True),  # boundary: equal is included (ceiling, not strict)
        (0.4, 0.5, True),
        (0.6, 0.5, False),
        (0.0, 0.0, True),
        (1.0, 1.0, True),
        (0.99, 1.0, True),
        (None, 1.0, False),  # fail-closed: unscored excluded even at anarchy
        (None, 0.0, False),
    ],
)
def test_is_within_tolerance_table(score, tolerance, expected):
    assert is_within_tolerance(score, tolerance) is expected


def test_naive_gte_would_fail_this_direction_control():
    """Negative control for the inequality direction: a naive `>=` (floor,
    not ceiling) would incorrectly admit 0.9 at tolerance=0.5 and incorrectly
    reject 0.1 at tolerance=0.5 -- opposite of the real (ceiling) semantics."""
    assert is_within_tolerance(0.9, 0.5) is False
    assert is_within_tolerance(0.1, 0.5) is True
