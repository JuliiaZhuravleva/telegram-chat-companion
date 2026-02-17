"""Tests for sticker responder — emoji sentiment reranking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.modules.sticker.models import StickerSearchResult
from src.services.modules.sticker.responder import (
    StickerResponderService,
    _has_sentiment_mismatch,
)


def _make_result(
    *,
    file_id: str = "f1",
    similarity: float = 0.8,
    emoji: str | None = None,
) -> StickerSearchResult:
    return StickerSearchResult(
        file_id=file_id,
        file_unique_id=f"u_{file_id}",
        visual_description="test sticker",
        emotion="happy",
        character_or_meme=None,
        suggested_contexts=[],
        similarity=similarity,
        total_uses=0,
        bot_uses=0,
        emoji=emoji,
    )


class TestHasSentimentMismatch:
    """Test _has_sentiment_mismatch helper."""

    def test_no_emoji_no_mismatch(self):
        result = _make_result(emoji=None)
        assert _has_sentiment_mismatch(result, "привет!") is False

    def test_negative_emoji_positive_context(self):
        result = _make_result(emoji="\U0001f621")  # angry
        assert _has_sentiment_mismatch(result, "Привет! Как дела?") is True

    def test_negative_emoji_neutral_context(self):
        result = _make_result(emoji="\U0001f621")  # angry
        assert _has_sentiment_mismatch(result, "Пойду поем") is False

    def test_positive_emoji_positive_context(self):
        result = _make_result(emoji="\U0001f600")  # grinning face
        assert _has_sentiment_mismatch(result, "Привет!") is False

    def test_negative_emoji_thank_context(self):
        result = _make_result(emoji="\U0001f44e")  # thumbs down
        assert _has_sentiment_mismatch(result, "Спасибо за помощь!") is True

    def test_negative_emoji_english_greeting(self):
        result = _make_result(emoji="\U0001f620")  # angry
        assert _has_sentiment_mismatch(result, "Hello everyone!") is True

    def test_crying_emoji_congrats_context(self):
        result = _make_result(emoji="\U0001f62d")  # loudly crying
        assert _has_sentiment_mismatch(result, "Поздравляю!") is True


class TestGetStickerCandidates:
    """Test that get_sticker_candidates applies reranking."""

    @pytest.mark.asyncio
    async def test_demotes_mismatched_sticker(self):
        """Angry sticker should be demoted when context is a greeting."""
        angry = _make_result(file_id="angry", similarity=0.85, emoji="\U0001f621")
        happy = _make_result(file_id="happy", similarity=0.83, emoji="\U0001f600")
        neutral = _make_result(file_id="neutral", similarity=0.80, emoji=None)

        mock_learning = MagicMock()
        mock_learning.search = AsyncMock(return_value=[angry, happy, neutral])
        mock_repo = MagicMock()

        service = StickerResponderService(mock_learning, mock_repo)
        result = await service.get_sticker_candidates("Привет!", limit=3)

        # Happy sticker (0.83) should come before angry (0.85 - 0.05 = 0.80)
        assert result[0].file_id == "happy"
        assert result[1].file_id == "angry" or result[1].file_id == "neutral"

    @pytest.mark.asyncio
    async def test_no_reranking_neutral_context(self):
        """No penalty in neutral context — order preserved."""
        angry = _make_result(file_id="angry", similarity=0.85, emoji="\U0001f621")
        happy = _make_result(file_id="happy", similarity=0.83, emoji="\U0001f600")

        mock_learning = MagicMock()
        mock_learning.search = AsyncMock(return_value=[angry, happy])
        mock_repo = MagicMock()

        service = StickerResponderService(mock_learning, mock_repo)
        result = await service.get_sticker_candidates("Пойду поем", limit=2)

        assert result[0].file_id == "angry"  # highest similarity preserved

    @pytest.mark.asyncio
    async def test_empty_search_results(self):
        mock_learning = MagicMock()
        mock_learning.search = AsyncMock(return_value=[])
        mock_repo = MagicMock()

        service = StickerResponderService(mock_learning, mock_repo)
        result = await service.get_sticker_candidates("Привет!", limit=3)

        assert result == []

    @pytest.mark.asyncio
    async def test_requests_extra_candidates(self):
        """Should request limit + 3 from search for reranking pool."""
        mock_learning = MagicMock()
        mock_learning.search = AsyncMock(return_value=[])
        mock_repo = MagicMock()

        service = StickerResponderService(mock_learning, mock_repo)
        await service.get_sticker_candidates("test", limit=3)

        mock_learning.search.assert_awaited_once_with(
            "test", limit=6, min_similarity=0.6,
        )
