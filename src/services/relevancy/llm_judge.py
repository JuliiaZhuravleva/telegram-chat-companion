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

logger = structlog.get_logger(__name__)

_JUDGE_PROMPT = """\
You are evaluating whether a chat bot should jump into a group conversation uninvited.

Recent messages:
{history}

Current message: "{message}"

Should the bot respond? Consider:
- PRO: Is there something interesting to add? An information gap to fill?
- CONTRA: Would responding feel forced, repetitive, or annoying?

Think briefly, then answer YES or NO on the last line."""

_YES_NO_RE = re.compile(r"\b(YES|NO)\b", re.IGNORECASE)


@dataclass(frozen=True)
class JudgeResult:
    """Outcome of Tier 3 LLM judge."""

    should_respond: bool
    reasoning: str = ""
    tokens_input: int | None = None
    tokens_output: int | None = None
    model: str = ""
    cost_usd: Decimal = Decimal("0")


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
    prompt = _JUDGE_PROMPT.format(history=history, message=message_text[:200])

    try:
        result = await ai_router.generate_text(
            prompt=prompt,
            system_prompt=None,
            model="gpt-5-nano",
            max_tokens=1024,  # gpt-5-nano uses internal reasoning tokens
            temperature=1.0,
        )
    except AIProviderError:
        logger.warning("llm_judge_failed", reason="all_providers_failed")
        return JudgeResult(should_respond=False, reasoning="llm_error")

    # Parse YES / NO from the response
    match = _YES_NO_RE.search(result.text)
    should_respond = match.group(1).upper() == "YES" if match else False

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
        cost_usd=cost,
    )
