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

import structlog

logger = structlog.get_logger(__name__)

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
    # Token-priced, roughly half of whisper-1 per minute of speech. Verified
    # 2026-08-19 against the official pricing page: audio input $1.25/1M,
    # text input $5/1M, output $5/1M. `usage.input_tokens` mixes both input
    # kinds and we bill it all at the audio rate; audio is 97-99% of input on
    # real traffic, so the text share (priced 4x higher) under-reports total
    # cost by low single-digit percent. Split via
    # `usage.input_token_details.{audio,text}_tokens` if that ever matters.
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
) -> Decimal | None:
    """Cost in USD for a single AI operation, or ``None`` when it is unknowable.

    **"Free" and "we do not know" are different answers, and this used to give
    the same one to both.** Every path that could not price a call returned
    ``Decimal("0")``: a model missing from the table, a token-priced model whose
    response carried no usage object, a per-minute model with no duration. That
    zero is written to ``response_log.cost_usd``, which is what
    ``SpendLimitService`` sums and what /costs reports — so an unpriceable call
    was permanently indistinguishable from a genuinely free one, and the daily
    cap under-counted with nothing anywhere saying so. The provider layer
    already knew this was a hazard and warned about it for transcription
    (``providers/openai.py``); the zero was written anyway.

    ``None`` means "not priceable", and the column is nullable, so it lands as
    SQL NULL. Totals are unaffected — ``SUM`` ignores NULL exactly as it ignores
    zero — but the rows are now countable, which is the whole point: an
    under-report you can measure is a different thing from one you cannot see.

    This matters more the moment anything streams: a stream that ends without a
    usage object is precisely the "priced model, no numbers" case, and it would
    have logged $0 for every streamed reply.

    Returns:
        ``Decimal("0")`` only when the cost is genuinely zero — a model marked
        free, or a priced model that measurably consumed zero tokens.
        ``None`` when the cost cannot be determined.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        # Not in the table. It may be free, it may be the most expensive model
        # the provider sells; we have no way to tell, so we must not assert.
        logger.warning("Cannot price a call to an unknown model", model=model)
        return None
    if pricing.is_free:
        return _ZERO

    # Audio: per-minute pricing (Whisper). `per_minute` set is what makes a
    # model per-minute-billed, so a missing duration is unpriceable here rather
    # than falling through to the token branches below (which would silently
    # bill a per-minute model at its unset $0/1M token rates).
    if pricing.per_minute is not None:
        if duration_minutes is None:
            logger.warning(
                "Per-minute model returned no duration; cost cannot be computed",
                model=model,
            )
            return None
        return pricing.per_minute * Decimal(str(duration_minutes))

    # `is None`, not falsiness: a measured zero tokens genuinely costs zero and
    # must stay a zero, while an absent count must not.
    if tokens_input is None and tokens_output is None:
        logger.warning(
            "Priced model returned no usage tokens; cost cannot be computed",
            model=model,
        )
        return None

    cost = _ZERO
    if tokens_input and pricing.input_per_1m:
        cost += pricing.input_per_1m * Decimal(tokens_input) / _ONE_MILLION
    if tokens_output and pricing.output_per_1m:
        cost += pricing.output_per_1m * Decimal(tokens_output) / _ONE_MILLION
    return cost
