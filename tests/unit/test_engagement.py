"""Tests for src.services.relevancy.engagement — Tier 2 conversation signals."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.services.relevancy.engagement import compute_engagement


def _make_repo(stats: dict) -> AsyncMock:
    """Create a mock MessageRepository returning given stats."""
    defaults = {
        "total_count": 20,
        "bot_count": 3,
        "bot_ratio": 0.15,
        "seconds_since_last_bot": 120.0,
        "velocity_per_minute": 2.0,
        "consecutive_bot_at_end": 0,
    }
    defaults.update(stats)
    repo = AsyncMock()
    repo.get_bot_message_stats = AsyncMock(return_value=defaults)
    return repo


class TestEngagement:
    """Test compute_engagement() with various conversation states."""

    @pytest.mark.asyncio
    async def test_passes_normal_conversation(self) -> None:
        repo = _make_repo({})
        result = await compute_engagement(chat_id=-100, message_repo=repo)
        assert result.should_pass is True

    @pytest.mark.asyncio
    async def test_rejects_high_bot_ratio(self) -> None:
        repo = _make_repo({"bot_ratio": 0.35, "total_count": 20})
        result = await compute_engagement(chat_id=-100, message_repo=repo)
        assert result.should_pass is False
        assert result.reason == "bot_ratio_exceeded"

    @pytest.mark.asyncio
    async def test_allows_high_ratio_with_few_messages(self) -> None:
        """Don't suppress when there are too few messages for the ratio to be meaningful."""
        repo = _make_repo({"bot_ratio": 0.5, "total_count": 4})
        result = await compute_engagement(chat_id=-100, message_repo=repo)
        assert result.should_pass is True

    @pytest.mark.asyncio
    async def test_rejects_high_velocity(self) -> None:
        repo = _make_repo({"velocity_per_minute": 8.0})
        result = await compute_engagement(chat_id=-100, message_repo=repo)
        assert result.should_pass is False
        assert result.reason == "high_velocity"

    @pytest.mark.asyncio
    async def test_rejects_consecutive_bot_responses(self) -> None:
        repo = _make_repo({"consecutive_bot_at_end": 2})
        result = await compute_engagement(chat_id=-100, message_repo=repo)
        assert result.should_pass is False
        assert result.reason == "consecutive_bot_responses"

    @pytest.mark.asyncio
    async def test_allows_one_consecutive_bot_response(self) -> None:
        repo = _make_repo({"consecutive_bot_at_end": 1})
        result = await compute_engagement(chat_id=-100, message_repo=repo)
        assert result.should_pass is True

    @pytest.mark.asyncio
    async def test_empty_chat(self) -> None:
        repo = _make_repo(
            {
                "total_count": 0,
                "bot_count": 0,
                "bot_ratio": 0.0,
                "seconds_since_last_bot": None,
                "velocity_per_minute": 0.0,
                "consecutive_bot_at_end": 0,
            }
        )
        result = await compute_engagement(chat_id=-100, message_repo=repo)
        assert result.should_pass is True

    @pytest.mark.asyncio
    async def test_signals_populated(self) -> None:
        repo = _make_repo({"velocity_per_minute": 3.5})
        result = await compute_engagement(chat_id=-100, message_repo=repo)
        assert result.signals is not None
        assert result.signals.velocity_per_minute == 3.5
