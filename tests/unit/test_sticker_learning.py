"""Tests for sticker learning service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.ai.base import AIProviderError, EmbeddingResult, VisionResult
from src.services.modules.sticker.learning import StickerLearningService
from src.services.modules.sticker.models import StickerRenderError
from src.services.modules.sticker.renderer import RenderedSticker


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
@patch("src.services.modules.sticker.learning.render_tgs", new_callable=AsyncMock)
async def test_learn_animated_sticker_renders_and_analyzes(mock_render_tgs, sticker_service):
    mock_render_tgs.return_value = RenderedSticker(
        collage_png=b"fake-collage-png",
        duration=3.0,
        frame_times=[0.0, 0.6, 1.2, 1.8, 2.4, 3.0],
    )

    sticker = _make_sticker(is_animated=True)
    result = await sticker_service.learn(
        sticker=sticker,
        image_data=b"fake-tgs",
    )

    assert result.is_new is True
    assert result.analysis_failed is False
    assert result.visual_description == "A happy cat"
    mock_render_tgs.assert_awaited_once_with(b"fake-tgs")
    sticker_service._ai.analyze_image.assert_awaited_once()
    sticker_service._repo.save_sticker.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.services.modules.sticker.learning.render_tgs", new_callable=AsyncMock)
async def test_learn_animated_sticker_render_failure(mock_render_tgs, sticker_service):
    mock_render_tgs.side_effect = StickerRenderError("render failed")

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
@patch("src.services.modules.sticker.learning.render_webm", new_callable=AsyncMock)
async def test_learn_video_sticker_renders_and_analyzes(mock_render_webm, sticker_service):
    mock_render_webm.return_value = RenderedSticker(
        collage_png=b"fake-collage-png",
        duration=2.5,
        frame_times=[0.0, 0.5, 1.0, 1.5, 2.0, 2.5],
    )

    sticker = _make_sticker(is_video=True)
    result = await sticker_service.learn(
        sticker=sticker,
        image_data=b"fake-webm",
    )

    assert result.is_new is True
    assert result.analysis_failed is False
    assert result.visual_description == "A happy cat"
    mock_render_webm.assert_awaited_once_with(b"fake-webm")
    sticker_service._ai.analyze_image.assert_awaited_once()


@pytest.mark.asyncio
@patch("src.services.modules.sticker.learning.render_webm", new_callable=AsyncMock)
async def test_learn_video_sticker_render_failure(mock_render_webm, sticker_service):
    mock_render_webm.side_effect = StickerRenderError("render failed")

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


class TestMergeAdminDescription:
    @pytest.mark.asyncio
    async def test_merge_passes_model_and_json_params(self, sticker_service):
        """merge_admin_description passes model=o4-mini, temperature=0.4 and response_mime_type."""
        from src.services.ai.base import TextGenerationResult

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value={
                "original_vision_description": "A cat",
                "visual_description": "A cat",
                "emotion": "joy",
                "character_or_meme": None,
                "suggested_contexts": ["greeting"],
                "usage_contexts": [],
            }
        )
        sticker_service._ai.generate_text = AsyncMock(
            return_value=TextGenerationResult(
                text='{"visual": "A happy cat", "emotion": "joy", "contexts": ["greeting"]}',
                model="o4-mini",
                provider="openai",
            )
        )
        sticker_service._repo.update_description_and_fields = AsyncMock()

        await sticker_service.merge_admin_description("unique-123", "also happy")

        call_kwargs = sticker_service._ai.generate_text.call_args.kwargs
        assert call_kwargs["model"] == "o4-mini"
        assert call_kwargs["temperature"] == 0.4
        assert call_kwargs["response_mime_type"] == "application/json"

    @pytest.mark.asyncio
    async def test_merge_prompt_includes_original_vision(self, sticker_service):
        """merge prompt emphasizes original vision description."""
        from src.services.ai.base import TextGenerationResult

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value={
                "original_vision_description": "Anime girl with blue hair",
                "visual_description": "Anime girl waving",
                "emotion": "happy",
                "character_or_meme": None,
                "suggested_contexts": [],
                "usage_contexts": [],
            }
        )
        sticker_service._ai.generate_text = AsyncMock(
            return_value=TextGenerationResult(
                text='{"visual": "Anime girl waving hello", "emotion": "happy", "contexts": ["greeting"]}',
                model="o4-mini",
                provider="openai",
            )
        )
        sticker_service._repo.update_description_and_fields = AsyncMock()

        await sticker_service.merge_admin_description("unique-123", "she is waving")

        prompt = sticker_service._ai.generate_text.call_args.kwargs["prompt"]
        assert "Anime girl with blue hair" in prompt
        assert "Vision API" in prompt
        assert "основу" in prompt  # "используй его как основу"


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
