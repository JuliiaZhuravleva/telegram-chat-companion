"""Tests for src.services.relevancy.gate — full RelevancyGate orchestrator."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.models.chat_config import ChatConfig
from src.services.ai.base import AIProviderError, TextGenerationResult
from src.services.relevancy.gate import RelevancyGate


def _make_config(**overrides) -> ChatConfig:
    defaults = {"chat_id": -100, "enabled": True, "relevancy_gate_enabled": True}
    defaults.update(overrides)
    return ChatConfig(**defaults)


def _make_message_repo(stats: dict | None = None, recent: list | None = None) -> AsyncMock:
    repo = AsyncMock()
    default_stats = {
        "total_count": 20,
        "bot_count": 3,
        "bot_ratio": 0.15,
        "seconds_since_last_bot": 120.0,
        "velocity_per_minute": 2.0,
        "consecutive_bot_at_end": 0,
    }
    if stats:
        default_stats.update(stats)
    repo.get_bot_message_stats = AsyncMock(return_value=default_stats)
    repo.get_recent = AsyncMock(return_value=recent or [])
    return repo


def _make_ai_router(response_text: str = "YES") -> AsyncMock:
    router = AsyncMock()
    router.generate_text = AsyncMock(
        return_value=TextGenerationResult(
            text=response_text,
            model="gpt-5-nano",
            provider="openai",
            tokens_input=100,
            tokens_output=10,
        )
    )
    return router


def _make_response_log() -> AsyncMock:
    repo = AsyncMock()
    repo.log = AsyncMock()
    return repo


class TestRelevancyGateDisabled:
    """When relevancy_gate_enabled=False, always pass."""

    @pytest.mark.asyncio
    async def test_gate_disabled_always_passes(self) -> None:
        config = _make_config(relevancy_gate_enabled=False)
        gate = RelevancyGate(
            ai_router=_make_ai_router(),
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="anything",
            config=config,
        )
        assert decision.should_respond is True
        assert decision.tier == "disabled"


class TestRelevancyGateTier1:
    """Tier 1 fast rules should short-circuit before any I/O."""

    @pytest.mark.asyncio
    async def test_rejects_acknowledgment(self) -> None:
        config = _make_config()
        repo = _make_message_repo()
        gate = RelevancyGate(
            ai_router=_make_ai_router(),
            message_repo=repo,
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="ладно",
            config=config,
        )
        assert decision.should_respond is False
        assert decision.tier == "fast_rules"
        # No DB calls should have been made
        repo.get_bot_message_stats.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_short_text(self) -> None:
        config = _make_config()
        gate = RelevancyGate(
            ai_router=_make_ai_router(),
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="ok",
            config=config,
        )
        assert decision.should_respond is False
        assert decision.tier == "fast_rules"


class TestRelevancyGateTier2:
    """Tier 2 engagement checks should short-circuit before LLM call."""

    @pytest.mark.asyncio
    async def test_rejects_high_bot_ratio(self) -> None:
        config = _make_config()
        ai = _make_ai_router()
        gate = RelevancyGate(
            ai_router=ai,
            message_repo=_make_message_repo(stats={"bot_ratio": 0.4, "total_count": 20}),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="А что вы думаете об этом?",
            config=config,
        )
        assert decision.should_respond is False
        assert decision.tier == "engagement"
        # LLM should NOT have been called
        ai.generate_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_high_velocity(self) -> None:
        config = _make_config()
        ai = _make_ai_router()
        gate = RelevancyGate(
            ai_router=ai,
            message_repo=_make_message_repo(stats={"velocity_per_minute": 10.0}),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Интересный вопрос",
            config=config,
        )
        assert decision.should_respond is False
        assert decision.tier == "engagement"
        ai.generate_text.assert_not_called()


class TestRelevancyGateTier3:
    """Tier 3 LLM judge — full pipeline reaching the LLM."""

    @pytest.mark.asyncio
    async def test_llm_says_yes(self) -> None:
        config = _make_config()
        gate = RelevancyGate(
            ai_router=_make_ai_router("Let me think... YES"),
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Кто знает хороший рецепт пасты?",
            config=config,
        )
        assert decision.should_respond is True
        assert decision.tier == "llm_judge"

    @pytest.mark.asyncio
    async def test_llm_says_no(self) -> None:
        config = _make_config()
        gate = RelevancyGate(
            ai_router=_make_ai_router("Not relevant. NO"),
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Кто знает хороший рецепт пасты?",
            config=config,
        )
        assert decision.should_respond is False
        assert decision.tier == "llm_judge"

    @pytest.mark.asyncio
    async def test_llm_error_defaults_to_no(self) -> None:
        """Fail-closed: if LLM fails, bot stays silent."""
        config = _make_config()
        ai = AsyncMock()
        ai.generate_text = AsyncMock(side_effect=AIProviderError("timeout", provider="openai"))
        gate = RelevancyGate(
            ai_router=ai,
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Кто знает хороший рецепт пасты?",
            config=config,
        )
        assert decision.should_respond is False
        assert decision.tier == "llm_judge"

    @pytest.mark.asyncio
    async def test_llm_ambiguous_response_defaults_to_no(self) -> None:
        """If LLM doesn't say YES or NO clearly, default to NO."""
        config = _make_config()
        gate = RelevancyGate(
            ai_router=_make_ai_router("I'm not sure about this"),
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Кто знает хороший рецепт?",
            config=config,
        )
        assert decision.should_respond is False

    @pytest.mark.asyncio
    async def test_cost_logged_for_llm_check(self) -> None:
        config = _make_config()
        log_repo = _make_response_log()
        gate = RelevancyGate(
            ai_router=_make_ai_router("YES"),
            message_repo=_make_message_repo(),
            response_log_repo=log_repo,
        )
        await gate.evaluate(
            chat_id=-100,
            message_text="Расскажите что-нибудь интересное",
            config=config,
        )
        log_repo.log.assert_called_once()
        call_kwargs = log_repo.log.call_args
        assert call_kwargs[1]["task_type"] == "relevancy_check"
        assert call_kwargs[1]["model"] == "gpt-5-nano"

    @pytest.mark.asyncio
    async def test_cost_usd_in_decision(self) -> None:
        config = _make_config()
        gate = RelevancyGate(
            ai_router=_make_ai_router("YES"),
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Интересный вопрос по Python",
            config=config,
        )
        assert decision.cost_usd >= Decimal("0")

    @pytest.mark.asyncio
    async def test_provider_from_llm_used_in_log(self) -> None:
        """Provider from AIRouter result is forwarded to the log, not hardcoded 'openai'."""
        from unittest.mock import AsyncMock as _AsyncMock

        config = _make_config()
        log_repo = _make_response_log()

        # Simulate a non-OpenAI provider winning the fallback chain
        router = _AsyncMock()
        router.generate_text = _AsyncMock(
            return_value=TextGenerationResult(
                text="YES",
                model="gemini-3-flash-preview",
                provider="gemini",
                tokens_input=80,
                tokens_output=8,
            )
        )
        gate = RelevancyGate(
            ai_router=router,
            message_repo=_make_message_repo(),
            response_log_repo=log_repo,
        )
        await gate.evaluate(
            chat_id=-100,
            message_text="Расскажи что-нибудь интересное",
            config=config,
        )
        log_repo.log.assert_called_once()
        call_kwargs = log_repo.log.call_args[1]
        assert call_kwargs["provider"] == "gemini"
        assert call_kwargs["model"] == "gemini-3-flash-preview"

    @pytest.mark.asyncio
    async def test_history_passed_in_chronological_order(self) -> None:
        """get_recent() returns newest-first; gate must reverse before LLM call."""
        config = _make_config()
        # Simulate DB returning [newest=Charlie, ..., oldest=Alice]
        recent_from_db = [
            {"first_name": "Charlie", "content": "third msg", "is_bot_message": False},
            {"first_name": "Bob", "content": "second msg", "is_bot_message": False},
            {"first_name": "Alice", "content": "first msg", "is_bot_message": False},
        ]
        repo = _make_message_repo(recent=recent_from_db)
        ai = _make_ai_router("YES")
        gate = RelevancyGate(
            ai_router=ai,
            message_repo=repo,
            response_log_repo=_make_response_log(),
        )
        await gate.evaluate(
            chat_id=-100,
            message_text="Как всё прошло?",
            config=config,
        )
        prompt: str = ai.generate_text.call_args.kwargs["prompt"]
        # With chronological ordering Alice (oldest) must appear before Charlie (newest)
        alice_pos = prompt.index("Alice")
        charlie_pos = prompt.index("Charlie")
        assert alice_pos < charlie_pos, (
            "History should be oldest→newest; Charlie appeared before Alice in the prompt"
        )


class TestRelevancyGateSuggestedEmoji:
    """R-5 (ADR-0004 Decision 4): the tier-3 piggyback passes through GateDecision."""

    @pytest.mark.asyncio
    async def test_suggested_emoji_passed_through_on_no(self) -> None:
        config = _make_config(reactions_enabled=True)
        gate = RelevancyGate(
            ai_router=_make_ai_router("Not a fit.\nNO\n🔥"),
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Кто знает хороший рецепт пасты?",
            config=config,
        )
        assert decision.should_respond is False
        assert decision.suggested_emoji == "🔥"

    @pytest.mark.asyncio
    async def test_no_emoji_requested_when_reactions_disabled(self) -> None:
        """`reactions_enabled` is threaded into the judge call, so a chat with
        the module off never pays for the emoji instruction or the 73-emoji
        list -- and cannot receive a suggestion it could not act on."""
        ai = _make_ai_router("Not a fit.\nNO\n🔥")
        gate = RelevancyGate(
            ai_router=ai,
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )

        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Кто знает хороший рецепт пасты?",
            config=_make_config(reactions_enabled=False),
        )

        assert decision.suggested_emoji is None
        prompt: str = ai.generate_text.call_args.kwargs["prompt"]
        assert "🔥" not in prompt

    @pytest.mark.asyncio
    async def test_suggested_emoji_none_on_yes(self) -> None:
        config = _make_config()
        gate = RelevancyGate(
            ai_router=_make_ai_router("Good fit.\nYES"),
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="Кто знает хороший рецепт пасты?",
            config=config,
        )
        assert decision.should_respond is True
        assert decision.suggested_emoji is None

    @pytest.mark.asyncio
    async def test_suggested_emoji_none_on_earlier_tier_rejection(self) -> None:
        """Only tier == 'llm_judge' ever sets suggested_emoji."""
        config = _make_config()
        ai = _make_ai_router("Not a fit.\nNO\n🔥")
        gate = RelevancyGate(
            ai_router=ai,
            message_repo=_make_message_repo(),
            response_log_repo=_make_response_log(),
        )
        decision = await gate.evaluate(
            chat_id=-100,
            message_text="ладно",
            config=config,
        )
        assert decision.tier == "fast_rules"
        assert decision.suggested_emoji is None
        ai.generate_text.assert_not_called()
