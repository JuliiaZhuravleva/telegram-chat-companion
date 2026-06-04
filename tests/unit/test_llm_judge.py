"""Tests for src.services.relevancy.llm_judge — Tier 3 LLM classifier."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.services.ai.base import AIProviderError, TextGenerationResult
from src.services.relevancy.llm_judge import JudgeResult, llm_judge


def _make_router(text: str = "YES", provider: str = "openai") -> AsyncMock:
    router = AsyncMock()
    router.generate_text = AsyncMock(
        return_value=TextGenerationResult(
            text=text,
            model="gpt-5-nano",
            provider=provider,
            tokens_input=100,
            tokens_output=10,
        )
    )
    return router


class TestLlmJudgeDecision:
    """YES / NO / ambiguous / error outcomes."""

    @pytest.mark.asyncio
    async def test_yes_response(self) -> None:
        router = _make_router("The bot should join. YES")
        result = await llm_judge("test", [], router)
        assert result.should_respond is True

    @pytest.mark.asyncio
    async def test_no_response(self) -> None:
        router = _make_router("Not relevant at all. NO")
        result = await llm_judge("test", [], router)
        assert result.should_respond is False

    @pytest.mark.asyncio
    async def test_ambiguous_response_defaults_to_no(self) -> None:
        router = _make_router("I am not entirely sure about this.")
        result = await llm_judge("test", [], router)
        assert result.should_respond is False

    @pytest.mark.asyncio
    async def test_error_defaults_to_no_fail_closed(self) -> None:
        router = AsyncMock()
        router.generate_text = AsyncMock(
            side_effect=AIProviderError("timeout", provider="openai")
        )
        result = await llm_judge("test", [], router)
        assert result.should_respond is False
        assert result.reasoning == "llm_error"

    @pytest.mark.asyncio
    async def test_case_insensitive_yes(self) -> None:
        router = _make_router("Yes, the bot should respond here.")
        result = await llm_judge("test", [], router)
        assert result.should_respond is True


class TestLlmJudgeProvider:
    """Provider field is carried from the AI result (bug-fix coverage)."""

    @pytest.mark.asyncio
    async def test_provider_from_openai(self) -> None:
        router = _make_router("YES", provider="openai")
        result = await llm_judge("test", [], router)
        assert result.provider == "openai"

    @pytest.mark.asyncio
    async def test_provider_from_gemini(self) -> None:
        """If router falls back to Gemini, provider is correctly captured."""
        router = AsyncMock()
        router.generate_text = AsyncMock(
            return_value=TextGenerationResult(
                text="YES",
                model="gemini-3-flash-preview",
                provider="gemini",
                tokens_input=80,
                tokens_output=8,
            )
        )
        result = await llm_judge("test", [], router)
        assert result.provider == "gemini"
        assert result.model == "gemini-3-flash-preview"

    @pytest.mark.asyncio
    async def test_provider_empty_on_error(self) -> None:
        router = AsyncMock()
        router.generate_text = AsyncMock(
            side_effect=AIProviderError("fail", provider="openai")
        )
        result = await llm_judge("test", [], router)
        assert result.provider == ""


class TestLlmJudgeHistoryFormatting:
    """Recent message history is formatted in the prompt correctly."""

    @pytest.mark.asyncio
    async def test_history_included_in_prompt(self) -> None:
        router = _make_router("YES")
        messages = [
            {"first_name": "Alice", "content": "first msg", "is_bot_message": False},
            {"first_name": None, "username": "bob42", "content": "second msg", "is_bot_message": False},
            {"first_name": "Bot", "content": "bot reply", "is_bot_message": True},
        ]
        await llm_judge("current message", messages, router)
        prompt: str = router.generate_text.call_args.kwargs["prompt"]
        assert "Alice" in prompt
        assert "bob42" in prompt
        assert "Bot" in prompt

    @pytest.mark.asyncio
    async def test_message_content_truncated_to_60_chars(self) -> None:
        router = _make_router("YES")
        long_content = "A" * 120
        messages = [{"first_name": "User", "content": long_content, "is_bot_message": False}]
        await llm_judge("test", messages, router)
        prompt: str = router.generate_text.call_args.kwargs["prompt"]
        # The formatted line should contain at most 60 A's (content truncation)
        a_run = "A" * 61
        assert a_run not in prompt

    @pytest.mark.asyncio
    async def test_empty_history_shows_placeholder(self) -> None:
        router = _make_router("NO")
        await llm_judge("test", [], router)
        prompt: str = router.generate_text.call_args.kwargs["prompt"]
        assert "no recent messages" in prompt

    @pytest.mark.asyncio
    async def test_history_order_preserved(self) -> None:
        """llm_judge renders messages in the order supplied by the caller.

        gate.py is responsible for reversing the DB result to chronological order
        before calling llm_judge.  This test verifies that llm_judge does NOT
        re-sort: messages arrive as [oldest, ..., newest] and appear that way
        in the prompt.
        """
        router = _make_router("YES")
        # Already in chronological order (gate.py reversed the DB rows)
        messages = [
            {"first_name": "Alpha", "content": "older msg", "is_bot_message": False},
            {"first_name": "Beta", "content": "newer msg", "is_bot_message": False},
        ]
        await llm_judge("test", messages, router)
        prompt: str = router.generate_text.call_args.kwargs["prompt"]
        alpha_pos = prompt.index("Alpha")
        beta_pos = prompt.index("Beta")
        assert alpha_pos < beta_pos


class TestLlmJudgeCost:
    """Cost fields are populated from the generation result."""

    @pytest.mark.asyncio
    async def test_cost_non_negative(self) -> None:
        router = _make_router("YES")
        result = await llm_judge("test", [], router)
        assert result.cost_usd >= Decimal("0")

    @pytest.mark.asyncio
    async def test_tokens_carried(self) -> None:
        router = _make_router("YES")
        result = await llm_judge("test", [], router)
        assert result.tokens_input == 100
        assert result.tokens_output == 10

    @pytest.mark.asyncio
    async def test_reasoning_truncated_to_100_chars(self) -> None:
        long_response = "Y" * 200 + " YES"
        router = _make_router(long_response)
        result = await llm_judge("test", [], router)
        assert len(result.reasoning) <= 100
