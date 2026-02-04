"""Tests for adaptive response length calculation."""

from src.services.text.adaptive_length import compute_length_instruction


class TestAdaptiveLength:
    def test_empty_list_returns_none(self):
        assert compute_length_instruction([]) is None

    def test_very_short_messages(self):
        result = compute_length_instruction([10, 15, 20, 25, 30])
        assert result is not None
        assert "1-2 sentences" in result

    def test_short_messages(self):
        result = compute_length_instruction([40, 50, 60, 70, 80])
        assert result is not None
        assert "1-3 sentences" in result

    def test_medium_messages(self):
        result = compute_length_instruction([100, 120, 130, 140, 150])
        assert result is not None
        assert "2-4 sentences" in result

    def test_long_messages_no_constraint(self):
        result = compute_length_instruction([200, 300, 400])
        assert result is None

    def test_single_short_message(self):
        result = compute_length_instruction([10])
        assert result is not None
        assert "1-2 sentences" in result

    def test_single_long_message(self):
        result = compute_length_instruction([500])
        assert result is None

    def test_boundary_30(self):
        result = compute_length_instruction([30])
        assert "1-2 sentences" in result

    def test_boundary_31(self):
        result = compute_length_instruction([31])
        assert "1-3 sentences" in result

    def test_boundary_80(self):
        result = compute_length_instruction([80])
        assert "1-3 sentences" in result

    def test_boundary_81(self):
        result = compute_length_instruction([81])
        assert "2-4 sentences" in result

    def test_boundary_150(self):
        result = compute_length_instruction([150])
        assert "2-4 sentences" in result

    def test_boundary_151(self):
        result = compute_length_instruction([151])
        assert result is None

    def test_median_with_mixed_lengths(self):
        # Median of [10, 20, 50, 100, 200] = 50 → "1-3 sentences"
        result = compute_length_instruction([10, 20, 50, 100, 200])
        assert result is not None
        assert "1-3 sentences" in result
