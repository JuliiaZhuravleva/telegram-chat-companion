"""
Model pricing table and cost calculation.

All prices are per 1M tokens (USD) unless noted otherwise.
Whisper uses per-minute pricing.

IMPORTANT: Prices are approximate and should be verified periodically
against provider pricing pages. No provider offers a pricing API.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_ZERO = Decimal("0")
_ONE_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class ModelPricing:
    """Pricing for a single model."""

    input_per_1m: Decimal = _ZERO
    output_per_1m: Decimal = _ZERO
    per_minute: Decimal | None = None  # Audio models (Whisper)
    is_free: bool = False


# Last verified: 2026-02-10
MODEL_PRICING: dict[str, ModelPricing] = {
    # --- OpenAI ---
    "gpt-5-nano": ModelPricing(
        input_per_1m=Decimal("0.05"),
        output_per_1m=Decimal("0.40"),
    ),
    "gpt-5-mini": ModelPricing(
        input_per_1m=Decimal("0.25"),
        output_per_1m=Decimal("2.00"),
    ),
    "gpt-5.2": ModelPricing(
        input_per_1m=Decimal("1.75"),
        output_per_1m=Decimal("14.00"),
    ),
    "o4-mini": ModelPricing(
        input_per_1m=Decimal("1.10"),
        output_per_1m=Decimal("4.40"),
    ),
    # OpenAI embeddings
    "text-embedding-3-small": ModelPricing(
        input_per_1m=Decimal("0.02"),
    ),
    "text-embedding-3-large": ModelPricing(
        input_per_1m=Decimal("0.13"),
    ),
    # OpenAI transcription
    "whisper-1": ModelPricing(per_minute=Decimal("0.006")),
    # Token-priced (audio input / text output), roughly half of whisper-1
    # per minute of speech. Verified 2026-08-19 against the official model
    # page (developers.openai.com/api/docs/models/gpt-4o-mini-transcribe).
    "gpt-4o-mini-transcribe": ModelPricing(
        input_per_1m=Decimal("1.25"),
        output_per_1m=Decimal("5.00"),
    ),
    # --- Gemini ---
    "gemini-3-flash-preview": ModelPricing(
        input_per_1m=Decimal("0.50"),
        output_per_1m=Decimal("3.00"),
    ),
    "gemini-3-pro-preview": ModelPricing(
        input_per_1m=Decimal("2.00"),
        output_per_1m=Decimal("12.00"),
    ),
    "gemini-embedding-001": ModelPricing(is_free=True),
    # --- Grok (xAI) ---
    "grok-4-1-fast": ModelPricing(
        input_per_1m=Decimal("0.20"),
        output_per_1m=Decimal("0.50"),
    ),
    "grok-4": ModelPricing(
        input_per_1m=Decimal("3.00"),
        output_per_1m=Decimal("15.00"),
    ),
    "grok-2-vision-1212": ModelPricing(
        input_per_1m=Decimal("2.00"),
        output_per_1m=Decimal("10.00"),
    ),
    # --- DeepSeek ---
    "deepseek-v3.2": ModelPricing(
        input_per_1m=Decimal("0.27"),
        output_per_1m=Decimal("1.10"),
    ),
    "deepseek-r1-0528": ModelPricing(
        input_per_1m=Decimal("0.55"),
        output_per_1m=Decimal("2.19"),
    ),
    # --- Anthropic ---
    "claude-sonnet-4": ModelPricing(
        input_per_1m=Decimal("3.00"),
        output_per_1m=Decimal("15.00"),
    ),
    "claude-opus-4-5": ModelPricing(
        input_per_1m=Decimal("15.00"),
        output_per_1m=Decimal("75.00"),
    ),
}


def calculate_cost(
    model: str,
    *,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    duration_minutes: float | None = None,
) -> Decimal:
    """Calculate cost in USD for a single AI operation.

    Returns Decimal("0") for unknown or free models.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None or pricing.is_free:
        return _ZERO

    # Audio: per-minute pricing (Whisper)
    if pricing.per_minute is not None and duration_minutes is not None:
        return pricing.per_minute * Decimal(str(duration_minutes))

    cost = _ZERO
    if tokens_input and pricing.input_per_1m:
        cost += pricing.input_per_1m * Decimal(tokens_input) / _ONE_MILLION
    if tokens_output and pricing.output_per_1m:
        cost += pricing.output_per_1m * Decimal(tokens_output) / _ONE_MILLION
    return cost
