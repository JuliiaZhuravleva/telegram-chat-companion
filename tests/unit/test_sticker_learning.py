"""Tests for sticker learning service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.ai.base import AIProviderError, EmbeddingResult, VisionResult
from src.services.modules.sticker.learning import StickerLearningService


def _make_sticker(
    file_id: str = "file-123",
    file_unique_id: str = "unique-123",
    set_name: str = "test_set",
    emoji: str = "😀",
    is_animated: bool = False,
    is_video: bool = False,
):
    sticker = MagicMock()
    sticker.file_id = file_id
    sticker.file_unique_id = file_unique_id
    sticker.set_name = set_name
    sticker.emoji = emoji
    sticker.is_animated = is_animated
    sticker.is_video = is_video
    return sticker


@pytest.fixture
def sticker_service():
    ai_router = MagicMock()
    ai_router.analyze_image = AsyncMock(
        return_value=VisionResult(
            text='{"visual": "A happy cat", "emotion": "joy", '
            '"contexts": ["to express happiness"], "tags": ["cute"], '
            '"character": null}',
            model="gemini-3-flash",
            provider="gemini",
        )
    )
    ai_router.generate_embedding = AsyncMock(
        return_value=EmbeddingResult(
            embedding=[0.1] * 768,
            model="gemini-embedding-001",
            provider="gemini",
            dimensions=768,
        )
    )

    repo = MagicMock()
    repo.get_by_file_unique_id = AsyncMock(return_value=None)
    repo.save_sticker = AsyncMock(return_value=1)
    repo.update_embedding = AsyncMock()
    repo.increment_usage = AsyncMock()
    repo.get_pack_context = AsyncMock(return_value=[])
    repo.accumulate_context = AsyncMock()

    return StickerLearningService(ai_router, repo)


@pytest.mark.asyncio
async def test_learn_new_sticker(sticker_service):
    sticker = _make_sticker()
    result = await sticker_service.learn(
        sticker=sticker,
        image_data=b"fake-png",
    )

    assert result.is_new is True
    assert result.visual_description == "A happy cat"
    assert result.emotion == "joy"
    assert result.analysis_failed is False

    sticker_service._repo.save_sticker.assert_awaited_once()
    sticker_service._repo.update_embedding.assert_awaited_once()


@pytest.mark.asyncio
async def test_learn_existing_sticker(sticker_service):
    sticker_service._repo.get_by_file_unique_id = AsyncMock(
        return_value={
            "visual_description": "Existing description",
            "emotion": "happy",
            "character_or_meme": None,
        }
    )

    sticker = _make_sticker()
    result = await sticker_service.learn(
        sticker=sticker,
        image_data=b"fake-png",
    )

    assert result.is_new is False
    assert result.visual_description == "Existing description"
    sticker_service._repo.increment_usage.assert_awaited_once()
    sticker_service._repo.save_sticker.assert_not_awaited()


@pytest.mark.asyncio
async def test_learn_animated_sticker_skips_analysis(sticker_service):
    sticker = _make_sticker(is_animated=True)
    result = await sticker_service.learn(
        sticker=sticker,
        image_data=b"fake-tgs",
    )

    assert result.is_new is True
    assert result.analysis_failed is True
    sticker_service._ai.analyze_image.assert_not_awaited()
    sticker_service._repo.save_sticker.assert_awaited_once()


@pytest.mark.asyncio
async def test_learn_video_sticker_skips_analysis(sticker_service):
    sticker = _make_sticker(is_video=True)
    result = await sticker_service.learn(
        sticker=sticker,
        image_data=b"fake-webm",
    )

    assert result.is_new is True
    assert result.analysis_failed is True
    sticker_service._ai.analyze_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_learn_vision_failure(sticker_service):
    sticker_service._ai.analyze_image = AsyncMock(
        side_effect=AIProviderError("Vision failed", provider="gemini")
    )

    sticker = _make_sticker()
    result = await sticker_service.learn(
        sticker=sticker,
        image_data=b"fake-png",
    )

    assert result.is_new is True
    assert result.analysis_failed is True
    # Should still save the sticker (without description)
    sticker_service._repo.save_sticker.assert_awaited_once()


@pytest.mark.asyncio
async def test_learn_with_preceding_messages_existing(sticker_service):
    sticker_service._repo.get_by_file_unique_id = AsyncMock(
        return_value={
            "visual_description": "test",
            "emotion": "joy",
            "character_or_meme": None,
        }
    )

    sticker = _make_sticker()
    await sticker_service.learn(
        sticker=sticker,
        image_data=b"fake-png",
        preceding_messages=["msg1", "msg2"],
    )

    sticker_service._repo.accumulate_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_success(sticker_service):
    sticker_service._repo.search_by_embedding = AsyncMock(
        return_value=[
            {
                "file_id": "file-1",
                "file_unique_id": "unique-1",
                "visual_description": "Happy cat",
                "emotion": "joy",
                "character_or_meme": None,
                "suggested_contexts": ["greeting"],
                "usage_contexts": [],
                "similarity": 0.85,
                "total_uses": 10,
                "bot_uses": 2,
            }
        ]
    )

    results = await sticker_service.search("happy greeting")

    assert len(results) == 1
    assert results[0].file_id == "file-1"
    assert results[0].similarity == 0.85


@pytest.mark.asyncio
async def test_search_embedding_failure(sticker_service):
    sticker_service._ai.generate_embedding = AsyncMock(
        side_effect=AIProviderError("Embedding failed", provider="gemini")
    )

    results = await sticker_service.search("test")

    assert results == []


class TestParseVisionResponse:
    def test_valid_json(self):
        text = '{"visual": "A cat", "emotion": "joy", "contexts": ["greeting"], "tags": ["cute"], "character": null}'
        result = StickerLearningService._parse_vision_response(text)
        assert result["visual"] == "A cat"
        assert result["emotion"] == "joy"
        assert result["contexts"] == ["greeting"]
        assert "character" not in result  # null should be filtered

    def test_json_in_code_block(self):
        text = '```json\n{"visual": "A dog", "emotion": "happy"}\n```'
        result = StickerLearningService._parse_vision_response(text)
        assert result["visual"] == "A dog"

    def test_invalid_json(self):
        result = StickerLearningService._parse_vision_response("not json")
        assert result == {}

    def test_null_string_character_filtered(self):
        text = '{"visual": "test", "character": "null"}'
        result = StickerLearningService._parse_vision_response(text)
        assert "character" not in result

    def test_none_string_character_filtered(self):
        text = '{"visual": "test", "character": "None"}'
        result = StickerLearningService._parse_vision_response(text)
        assert "character" not in result


class TestBuildEmbeddingText:
    def test_full_context(self):
        text = StickerLearningService._build_embedding_text(
            "A happy cat",
            "joy",
            "Pepe",
            ["greeting", "celebration"],
            ["hey guys"],
        )
        assert "A happy cat" in text
        assert "joy" in text
        assert "Pepe" in text
        assert "greeting" in text
        assert "hey guys" in text

    def test_minimal_context(self):
        text = StickerLearningService._build_embedding_text(
            "A sticker", None, None, None, None
        )
        assert text == "A sticker"
        assert "Emotion" not in text


class TestBuildVisionPrompt:
    def test_basic_prompt(self):
        sticker = _make_sticker(set_name="funny_cats")
        prompt = StickerLearningService._build_vision_prompt(sticker)
        assert "funny_cats" in prompt
        assert "JSON" in prompt
        assert "visual" in prompt

    def test_with_pack_context(self):
        sticker = _make_sticker()
        prompt = StickerLearningService._build_vision_prompt(
            sticker, pack_context=["Another happy cat", "Sad cat"]
        )
        assert "Another happy cat" in prompt
        assert "Sad cat" in prompt
