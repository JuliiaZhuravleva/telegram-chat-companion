"""Tests for SummaryService — focus on log_usage() call (B-1 regression)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.ai.base import AIProviderError, TextGenerationResult
from src.services.modules.summary import SummaryService


def _make_text_result(text: str = "Summary text") -> TextGenerationResult:
    return TextGenerationResult(
        text=text,
        model="gpt-5-nano",
        provider="openai",
        tokens_input=200,
        tokens_output=80,
    )


def _make_message_row(**overrides):
    row = {
        "username": "alice",
        "first_name": "Alice",
        "content": "Hello there",
        "created_at": MagicMock(strftime=lambda fmt: "12:00"),
        "is_bot_message": False,
    }
    row.update(overrides)
    return row


@pytest.fixture
def message_repo():
    repo = AsyncMock()
    repo.get_for_summary = AsyncMock(return_value=[_make_message_row()])
    return repo


@pytest.fixture
def ai_router():
    router = AsyncMock()
    router.generate_text = AsyncMock(return_value=_make_text_result())
    router.log_usage = AsyncMock()
    return router


@pytest.fixture
def summary_service(message_repo, ai_router):
    return SummaryService(message_repo, ai_router)


class TestSummaryLogUsage:
    """SummaryService must call router.log_usage() after successful generation."""

    @pytest.mark.asyncio
    async def test_calls_log_usage_on_success(self, summary_service, ai_router):
        """log_usage() must be scheduled when generate_text() succeeds."""
        await summary_service.generate(chat_id=-100123, language="ru")

        # Let fire-and-forget tasks complete
        await asyncio.sleep(0.05)

        ai_router.log_usage.assert_awaited_once()
        call_kwargs = ai_router.log_usage.call_args
        assert call_kwargs.kwargs["chat_id"] == -100123
        assert call_kwargs.kwargs["task_type"] == "summary"

    @pytest.mark.asyncio
    async def test_does_not_call_log_usage_on_ai_error(self, summary_service, ai_router):
        """log_usage() must NOT be called when generate_text() raises."""
        ai_router.generate_text.side_effect = AIProviderError("down", provider="openai")

        await summary_service.generate(chat_id=-100123, language="ru")
        await asyncio.sleep(0.05)

        ai_router.log_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_result_passed_to_log_usage(self, summary_service, ai_router):
        """The TextGenerationResult from generate_text() must be forwarded to log_usage()."""
        expected_result = _make_text_result("A short summary.")
        ai_router.generate_text.return_value = expected_result

        await summary_service.generate(chat_id=-999, language="en")
        await asyncio.sleep(0.05)

        positional_args = ai_router.log_usage.call_args.args
        assert positional_args[0] is expected_result

    @pytest.mark.asyncio
    async def test_no_messages_returns_early_without_log(self, summary_service, ai_router, message_repo):
        """When there are no messages, generate_text is never called."""
        message_repo.get_for_summary.return_value = []

        result = await summary_service.generate(chat_id=-100123, language="ru")
        await asyncio.sleep(0.05)

        ai_router.generate_text.assert_not_awaited()
        ai_router.log_usage.assert_not_awaited()
        assert result  # returns an explanatory message
