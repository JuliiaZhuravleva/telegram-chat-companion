"""Tests for AI pricing and cost calculation."""

from decimal import Decimal

from src.services.ai.pricing import MODEL_PRICING, calculate_cost


class TestCalculateCost:
    """Tests for calculate_cost()."""

    def test_text_model_with_tokens(self) -> None:
        # gpt-5-nano: $0.05/1M input, $0.40/1M output
        cost = calculate_cost(
            "gpt-5-nano",
            tokens_input=1000,
            tokens_output=500,
        )
        expected = Decimal("0.05") * Decimal("1000") / Decimal("1000000") + Decimal(
            "0.40"
        ) * Decimal("500") / Decimal("1000000")
        assert cost == expected

    def test_free_model_returns_a_real_zero(self) -> None:
        """`is_free` is a fact about the model, so zero here is an assertion we
        are entitled to make — unlike the unknown cases below."""
        cost = calculate_cost(
            "gemini-embedding-001",
            tokens_input=10000,
        )
        assert cost == Decimal("0")

    def test_whisper_per_minute_pricing(self) -> None:
        # whisper-1: $0.006/minute
        cost = calculate_cost("whisper-1", duration_minutes=2.5)
        expected = Decimal("0.006") * Decimal("2.5")
        assert cost == expected

    def test_unknown_model_is_unpriceable_not_free(self) -> None:
        """It used to return Decimal("0"), and that was a claim, not a fact.

        A model absent from the table may be free or may be the most expensive
        thing the provider sells. The zero was written to response_log.cost_usd,
        which SpendLimitService sums — so the daily cap under-counted and
        nothing could tell those rows from genuinely free ones afterwards.
        """
        cost = calculate_cost(
            "nonexistent-model",
            tokens_input=1000,
            tokens_output=500,
        )
        assert cost is None

    def test_a_priced_model_without_usage_numbers_is_unpriceable(self) -> None:
        """The case streaming makes routine: a response with no usage object."""
        cost = calculate_cost("gpt-5-nano")
        assert cost is None

    def test_measured_zero_tokens_really_is_zero(self) -> None:
        """The control for the test above: 0 and None must not be conflated.

        A response that measurably consumed nothing costs nothing, and must stay
        a hard zero. Written with `is None` checks rather than falsiness for
        exactly this reason.
        """
        cost = calculate_cost("gpt-5-nano", tokens_input=0, tokens_output=0)
        assert cost == Decimal("0")

    def test_only_input_tokens(self) -> None:
        # text-embedding-3-small: $0.02/1M input, $0 output
        cost = calculate_cost("text-embedding-3-small", tokens_input=5000)
        expected = Decimal("0.02") * Decimal("5000") / Decimal("1000000")
        assert cost == expected

    def test_a_per_minute_model_without_a_duration_is_unpriceable(self) -> None:
        """whisper-1 is billed per minute; token counts cannot stand in.

        Falling through to the token branches would bill it at its unset
        $0/1M rates and call the result zero — a per-minute model priced as if
        it were free.
        """
        cost = calculate_cost("whisper-1", tokens_input=100)
        assert cost is None

    def test_transcribe_model_is_token_priced(self) -> None:
        # gpt-4o-mini-transcribe: $1.25/1M input (audio), $5/1M output
        cost = calculate_cost(
            "gpt-4o-mini-transcribe",
            tokens_input=1_000_000,
            tokens_output=100_000,
        )
        expected = Decimal("1.25") + Decimal("5.00") * Decimal(100_000) / Decimal(1_000_000)
        assert cost == expected

    def test_transcribe_model_ignores_duration(self) -> None:
        # Token-priced model: a stray duration (whisper's field) must not be
        # billed — only tokens count. Guards the calculate_cost branch order.
        with_duration = calculate_cost(
            "gpt-4o-mini-transcribe",
            tokens_input=1000,
            tokens_output=100,
            duration_minutes=99.0,
        )
        without = calculate_cost(
            "gpt-4o-mini-transcribe",
            tokens_input=1000,
            tokens_output=100,
        )
        assert with_duration == without

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
