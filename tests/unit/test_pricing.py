"""Tests for AI pricing and cost calculation."""

from decimal import Decimal

from src.services.ai.pricing import MODEL_PRICING, calculate_cost


class TestCalculateCost:
    """Tests for calculate_cost()."""

    def test_text_model_with_tokens(self) -> None:
        # gpt-5-nano: $0.05/1M input, $0.40/1M output
        cost = calculate_cost(
            "gpt-5-nano", tokens_input=1000, tokens_output=500,
        )
        expected = (
            Decimal("0.05") * Decimal("1000") / Decimal("1000000")
            + Decimal("0.40") * Decimal("500") / Decimal("1000000")
        )
        assert cost == expected

    def test_free_model_returns_zero(self) -> None:
        cost = calculate_cost(
            "gemini-embedding-001", tokens_input=10000,
        )
        assert cost == Decimal("0")

    def test_whisper_per_minute_pricing(self) -> None:
        # whisper-1: $0.006/minute
        cost = calculate_cost("whisper-1", duration_minutes=2.5)
        expected = Decimal("0.006") * Decimal("2.5")
        assert cost == expected

    def test_unknown_model_returns_zero(self) -> None:
        cost = calculate_cost(
            "nonexistent-model", tokens_input=1000, tokens_output=500,
        )
        assert cost == Decimal("0")

    def test_none_tokens_returns_zero(self) -> None:
        cost = calculate_cost("gpt-5-nano")
        assert cost == Decimal("0")

    def test_zero_tokens_returns_zero(self) -> None:
        cost = calculate_cost("gpt-5-nano", tokens_input=0, tokens_output=0)
        assert cost == Decimal("0")

    def test_only_input_tokens(self) -> None:
        # text-embedding-3-small: $0.02/1M input, $0 output
        cost = calculate_cost("text-embedding-3-small", tokens_input=5000)
        expected = Decimal("0.02") * Decimal("5000") / Decimal("1000000")
        assert cost == expected

    def test_whisper_without_duration_returns_zero(self) -> None:
        # whisper-1 with no duration — can't compute cost
        cost = calculate_cost("whisper-1", tokens_input=100)
        assert cost == Decimal("0")

    def test_expensive_model_higher_cost(self) -> None:
        cheap = calculate_cost("gpt-5-nano", tokens_input=1000, tokens_output=1000)
        expensive = calculate_cost("gpt-5.2", tokens_input=1000, tokens_output=1000)
        assert expensive > cheap


class TestModelPricingTable:
    """Tests for MODEL_PRICING completeness."""

    def test_all_default_models_have_pricing(self) -> None:
        from src.services.ai.capabilities import DEFAULT_MODELS

        for provider, models in DEFAULT_MODELS.items():
            for task, model in models.items():
                assert model in MODEL_PRICING, (
                    f"Model {model} ({provider}/{task}) missing from MODEL_PRICING"
                )

    def test_free_model_is_free(self) -> None:
        assert MODEL_PRICING["gemini-embedding-001"].is_free is True

    def test_whisper_has_per_minute(self) -> None:
        pricing = MODEL_PRICING["whisper-1"]
        assert pricing.per_minute is not None
        assert pricing.per_minute > Decimal("0")
