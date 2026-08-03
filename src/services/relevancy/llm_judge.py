"""Tier 3: LLM-based relevancy classification.

Uses the cheapest available model (gpt-5-nano) to decide whether the bot
should jump into a group conversation. ~$0.00002 per check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

from src.services.ai.base import AIProviderError
from src.services.ai.pricing import calculate_cost
from src.services.ai.router import AIRouter
from src.services.modules.reactions.models import ALLOWED_REACTION_EMOJI

logger = structlog.get_logger(__name__)

_JUDGE_PROMPT = """\
You are evaluating whether a chat bot should jump into a group conversation uninvited.

Recent messages:
{history}

Current message: "{message}"

Should the bot respond? Consider:
- PRO: Is there something interesting to add? An information gap to fill?
- CONTRA: Would responding feel forced, repetitive, or annoying?

Think briefly. On the second-to-last line answer YES or NO.
If NO, on the last line suggest ONE emoji reaction from this list that fits \
the message, or NONE if nothing fits: {allowed_emoji}"""

_YES_NO_RE = re.compile(r"\b(YES|NO)\b", re.IGNORECASE)
# A candidate reaction line must be a bare emoji token, not prose -- any
# Latin letter means the model didn't follow the "last line = emoji" format
# (e.g. an ambiguous/error response), so treat it as no suggestion at all
# rather than pass prose through to ReactionSelector (ADR-0004 Decision 4).
_PROSE_RE = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class JudgeResult:
    """Outcome of Tier 3 LLM judge."""

    should_respond: bool
    reasoning: str = ""
    tokens_input: int | None = None
    tokens_output: int | None = None
    model: str = ""
    provider: str = ""
    cost_usd: Decimal = Decimal("0")
    suggested_emoji: str | None = None  # only meaningful when should_respond is False (R-5)


def _extract_suggested_emoji(text: str) -> str | None:
    """Pull the tier-3 reaction suggestion off the last non-empty line.

    Fail-closed: parse failure, "NONE", or prose (contains a Latin letter)
    all resolve to None -- final validation against `ALLOWED_REACTION_EMOJI`
    happens downstream in `ReactionSelector`, this is just cheap pre-filtering.
    """
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None
    last_line = lines[-1]
    if not last_line or last_line.upper() == "NONE":
        return None
    if _PROSE_RE.search(last_line):
        return None
    return last_line


async def llm_judge(
    message_text: str,
    recent_messages: list[dict[str, Any]],
    ai_router: AIRouter,
) -> JudgeResult:
    """Ask a cheap LLM whether the bot should respond.

    Defaults to ``should_respond=False`` on any error (fail-closed).
    """
    # Format last 5 messages, truncated for token efficiency
    lines: list[str] = []
    for msg in recent_messages[-5:]:
        name = msg.get("first_name") or msg.get("username") or "?"
        content = (msg.get("content") or "")[:60]
        prefix = "Bot" if msg.get("is_bot_message") else name
        lines.append(f"  {prefix}: {content}")

    history = "\n".join(lines) if lines else "  (no recent messages)"
    prompt = _JUDGE_PROMPT.format(
        history=history,
        message=message_text[:200],
        allowed_emoji=" ".join(ALLOWED_REACTION_EMOJI),
    )

    try:
        result = await ai_router.generate_text(
            prompt=prompt,
            system_prompt=None,
            model="gpt-5-nano",
            # gpt-5-nano spends internal reasoning tokens out of this same
            # budget. At 1024 it can burn the whole allowance thinking and
            # return empty content (finish_reason=length) -- observed live on
            # 2026-08-03, surfacing as llm_judge_failed/all_providers_failed,
            # which fails the gate closed: the bot stays silent AND sets no
            # R-5 reaction. R-5 made this likelier by adding the full
            # ALLOWED_REACTION_EMOJI list to the prompt. CLAUDE.md's rule for
            # reasoning models is 4096+.
            max_tokens=4096,
            temperature=1.0,
        )
    except AIProviderError as exc:
        # Log the provider's own message: this path fails the gate closed (no
        # reply AND no R-5 reaction), so when it starts firing the operator
        # needs to tell rate-limiting from empty-content from a timeout. A bare
        # "all_providers_failed" gives no way to choose a remedy.
        logger.warning(
            "llm_judge_failed",
            reason="all_providers_failed",
            error=str(exc),
            provider=getattr(exc, "provider", None),
            exc_info=True,
        )
        return JudgeResult(should_respond=False, reasoning="llm_error")

    # Parse YES / NO from the response
    match = _YES_NO_RE.search(result.text)
    should_respond = match.group(1).upper() == "YES" if match else False

    # Piggyback: only meaningful when the bot is staying silent (R-5).
    suggested_emoji = None if should_respond else _extract_suggested_emoji(result.text)

    cost = calculate_cost(
        result.model,
        tokens_input=result.tokens_input,
        tokens_output=result.tokens_output,
    )

    return JudgeResult(
        should_respond=should_respond,
        reasoning=result.text.strip()[:100],
        tokens_input=result.tokens_input,
        tokens_output=result.tokens_output,
        model=result.model,
        provider=result.provider,
        cost_usd=cost,
        suggested_emoji=suggested_emoji,
    )
