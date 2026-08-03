"""RelevancyGate — three-tier pre-check for random bot responses.

Architecture:
  Tier 1  Fast rules      — zero cost, pure Python regex/length checks
  Tier 2  Engagement       — 1 SQL query, conversation-dynamics signals
  Tier 3  LLM judge        — cheapest model (gpt-5-nano), ~$0.00002/check

Only called for ``TriggerType.RANDOM``. Explicit triggers (TRIGGER, REPLY)
bypass the gate entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import structlog

from src.database.repositories.messages import MessageRepository
from src.database.repositories.response_log import ResponseLogRepository
from src.models.chat_config import ChatConfig
from src.services.ai.router import AIRouter
from src.services.relevancy.engagement import compute_engagement
from src.services.relevancy.fast_rules import check_fast_rules
from src.services.relevancy.llm_judge import llm_judge

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GateDecision:
    """Outcome of the relevancy gate evaluation."""

    should_respond: bool
    tier: str  # "fast_rules" | "engagement" | "llm_judge" | "disabled"
    reason: str
    cost_usd: Decimal = Decimal("0")
    # Tier-3 reaction piggyback (R-5, ADR-0004 Decision 4): the emoji
    # `llm_judge` suggests when it says NO, at zero extra token cost. Only
    # ever set when tier == "llm_judge"; None on every other tier.
    suggested_emoji: str | None = None


class RelevancyGate:
    """Three-tier relevancy gate for RANDOM-triggered messages."""

    def __init__(
        self,
        ai_router: AIRouter,
        message_repo: MessageRepository,
        response_log_repo: ResponseLogRepository,
    ) -> None:
        self._ai = ai_router
        self._messages = message_repo
        self._response_log = response_log_repo

    async def evaluate(
        self,
        *,
        chat_id: int,
        message_text: str,
        config: ChatConfig,
    ) -> GateDecision:
        """Run three-tier relevancy check.

        Short-circuits as soon as any tier rejects the message.
        """
        if not config.relevancy_gate_enabled:
            return GateDecision(True, "disabled", "gate_disabled")

        # --- Tier 1: Fast rules (zero cost) ---
        fast = check_fast_rules(message_text)
        if not fast.should_pass:
            decision = GateDecision(False, "fast_rules", fast.reason)
            self._log_decision(chat_id, decision)
            return decision

        # --- Tier 2: Engagement signals (1 SQL query) ---
        engagement = await compute_engagement(chat_id, self._messages)
        if not engagement.should_pass:
            decision = GateDecision(False, "engagement", engagement.reason)
            self._log_decision(chat_id, decision)
            return decision

        # --- Tier 3: LLM judge (cheapest model) ---
        # get_recent() returns newest-first; reverse to chronological order
        # so the LLM sees the conversation as it unfolded (oldest → newest).
        recent = await self._messages.get_recent(chat_id, limit=5)
        history = [dict(r) for r in reversed(recent)]
        judge = await llm_judge(message_text, history, self._ai)

        decision = GateDecision(
            should_respond=judge.should_respond,
            tier="llm_judge",
            reason=judge.reasoning,
            cost_usd=judge.cost_usd,
            suggested_emoji=judge.suggested_emoji,
        )

        # Log LLM cost to response_log for cost tracking
        if judge.model:
            await self._safe_log_usage(
                chat_id=chat_id,
                model=judge.model,
                provider=judge.provider or "openai",
                tokens_input=judge.tokens_input,
                tokens_output=judge.tokens_output,
                cost_usd=judge.cost_usd,
            )

        self._log_decision(chat_id, decision)
        return decision

    def _log_decision(self, chat_id: int, decision: GateDecision) -> None:
        logger.info(
            "relevancy_gate_decision",
            chat_id=chat_id,
            should_respond=decision.should_respond,
            tier=decision.tier,
            reason=decision.reason,
        )

    async def _safe_log_usage(
        self,
        *,
        chat_id: int,
        model: str,
        provider: str,
        tokens_input: int | None,
        tokens_output: int | None,
        cost_usd: Decimal,
    ) -> None:
        try:
            await self._response_log.log(
                chat_id,
                task_type="relevancy_check",
                provider=provider,
                model=model,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                cost_usd=cost_usd,
            )
        except Exception:
            logger.warning("Failed to log relevancy check usage", chat_id=chat_id)
