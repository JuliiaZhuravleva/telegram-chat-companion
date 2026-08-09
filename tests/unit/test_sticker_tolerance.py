"""Tests for is_within_tolerance (ADR-0008 Decision 2) and
format_explicitness_line (A-1)."""

import pytest

from src.services.modules.sticker.tolerance import format_explicitness_line, is_within_tolerance


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


# ── format_explicitness_line (A-1) ───────────────────────────────────────


class TestFormatExplicitnessLine:
    def test_passing_score_shows_score_ceiling_and_pass_ru(self) -> None:
        line = format_explicitness_line(0.37, 0.5, "ru")
        assert "0.37" in line
        assert "0.50" in line
        assert "✅" in line
        assert "пройдёт" in line
        assert "не пройдёт" not in line
        assert "Оценка откровенности" in line

    def test_failing_score_shows_fail_verdict_ru(self) -> None:
        line = format_explicitness_line(0.9, 0.5, "ru")
        assert "❌" in line
        assert "не пройдёт" in line

    def test_boundary_score_equals_tolerance_passes(self) -> None:
        """Ceiling semantics (ADR-0008 Decision 2): score == tolerance passes."""
        line = format_explicitness_line(0.5, 0.5, "ru")
        assert "✅" in line

    def test_unscored_none_never_fabricates_a_verdict(self) -> None:
        line = format_explicitness_line(None, 1.0, "ru")
        assert "✅" not in line
        assert "❌" not in line
        assert "не оценён" in line

    def test_english_variant(self) -> None:
        line = format_explicitness_line(0.9, 0.5, "en")
        assert "❌" in line
        assert "blocked" in line
        assert "Explicitness score" in line

    def test_english_unscored(self) -> None:
        line = format_explicitness_line(None, 1.0, "en")
        assert "not scored" in line
        assert "✅" not in line
        assert "❌" not in line

    def test_default_lang_is_russian(self) -> None:
        line = format_explicitness_line(0.1, 0.5)
        assert "Оценка откровенности" in line
