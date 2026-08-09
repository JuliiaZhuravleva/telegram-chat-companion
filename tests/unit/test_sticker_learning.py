"""Tests for sticker learning service."""

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from src.services.ai.base import (
    AIProviderError,
    EmbeddingResult,
    TextGenerationResult,
    VisionResult,
)
from src.services.modules.sticker.dedup import compute_image_hash
from src.services.modules.sticker.learning import StickerLearningService
from src.services.modules.sticker.models import (
    ReanalyzeResult,
    StickerLearningResult,
    StickerRenderError,
)
from src.services.modules.sticker.motion import AnimationMotion
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
    ai_router.generate_text = AsyncMock(
        return_value=TextGenerationResult(
            text='{"visual": "A happy cat", "emotion": "joy", "contexts": ["greeting"]}',
            model="o4-mini",
            provider="openai",
            tokens_input=150,
            tokens_output=60,
        )
    )
    ai_router.log_usage = AsyncMock()

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
async def test_learn_new_sticker_wires_explicitness_score(sticker_service):
    """ADR-0008: a valid 'explicit' field in the Vision response reaches
    both save_sticker()'s kwargs and the returned StickerLearningResult."""
    sticker_service._ai.analyze_image = AsyncMock(
        return_value=VisionResult(
            text='{"visual": "A happy cat", "emotion": "joy", "explicit": 0.6}',
            model="gemini-3-flash",
            provider="gemini",
        )
    )

    sticker = _make_sticker()
    result = await sticker_service.learn(sticker=sticker, image_data=b"fake-png")

    assert result.explicitness_score == 0.6
    # ADR-0009 Decision 6: a fresh Vision analysis is never manual.
    assert result.explicitness_is_manual is False
    save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
    assert save_kwargs["explicitness_score"] == 0.6


@pytest.mark.asyncio
async def test_learn_new_sticker_explicitness_score_none_when_absent(sticker_service):
    """Vision response with no 'explicit' key at all (e.g. an older prompt
    version or a partial response) must not crash — resolves to None."""
    sticker = _make_sticker()
    result = await sticker_service.learn(sticker=sticker, image_data=b"fake-png")

    assert result.explicitness_score is None
    save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
    assert save_kwargs["explicitness_score"] is None


@pytest.mark.asyncio
async def test_learn_new_sticker_rejects_invalid_explicitness_score(sticker_service):
    """Reject-not-clamp (ADR-0008 Decision 4) end to end: a wildly-wrong
    Vision value never reaches the DB as a coerced-in-range number."""
    sticker_service._ai.analyze_image = AsyncMock(
        return_value=VisionResult(
            text='{"visual": "A happy cat", "emotion": "joy", "explicit": "70%"}',
            model="gemini-3-flash",
            provider="gemini",
        )
    )

    sticker = _make_sticker()
    result = await sticker_service.learn(sticker=sticker, image_data=b"fake-png")

    assert result.explicitness_score is None
    save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
    assert save_kwargs["explicitness_score"] is None


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

    results = await sticker_service.search("happy greeting", tolerance_level=0.5)

    assert len(results) == 1
    assert results[0].file_id == "file-1"
    assert results[0].similarity == 0.85


@pytest.mark.asyncio
async def test_search_threads_tolerance_level_to_repo(sticker_service):
    """ADR-0008 Decision 6: tolerance_level must reach search_by_embedding
    unchanged, not silently dropped at this layer."""
    sticker_service._repo.search_by_embedding = AsyncMock(return_value=[])

    await sticker_service.search("happy greeting", tolerance_level=0.73)

    sticker_service._repo.search_by_embedding.assert_awaited_once()
    assert sticker_service._repo.search_by_embedding.call_args.kwargs["tolerance_level"] == 0.73


@pytest.mark.asyncio
async def test_search_embedding_failure(sticker_service):
    sticker_service._ai.generate_embedding = AsyncMock(
        side_effect=AIProviderError("Embedding failed", provider="gemini")
    )

    results = await sticker_service.search("test", tolerance_level=0.5)

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

    def test_explicit_score_valid(self):
        text = '{"visual": "test", "explicit": 0.4}'
        result = StickerLearningService._parse_vision_response(text)
        assert result["explicit"] == 0.4

    def test_explicit_score_boundary_values_accepted(self):
        """ADR-0008 Decision 2 needs the boundary itself included, not just
        the open interval."""
        assert (
            StickerLearningService._parse_vision_response('{"visual": "t", "explicit": 0.0}')[
                "explicit"
            ]
            == 0.0
        )
        assert (
            StickerLearningService._parse_vision_response('{"visual": "t", "explicit": 1.0}')[
                "explicit"
            ]
            == 1.0
        )

    def test_explicit_score_out_of_range_rejected_not_clamped(self):
        """ADR-0008 Decision 4: reject, don't clamp — a wildly-wrong value
        (e.g. the model returns a percentage) must resolve to None, never be
        coerced into [0, 1]."""
        text = '{"visual": "test", "explicit": 7.0}'
        result = StickerLearningService._parse_vision_response(text)
        assert result["explicit"] is None

    def test_explicit_score_negative_rejected(self):
        text = '{"visual": "test", "explicit": -0.5}'
        result = StickerLearningService._parse_vision_response(text)
        assert result["explicit"] is None

    def test_explicit_score_non_numeric_rejected(self):
        text = '{"visual": "test", "explicit": "very"}'
        result = StickerLearningService._parse_vision_response(text)
        assert result["explicit"] is None

    def test_explicit_score_absent_key_omitted_no_spurious_entry(self):
        """A response with no 'explicit' key (e.g. merge/pack-context
        prompts, which never ask for this field) must not manufacture the
        key — downstream `.get('explicit')` already returns None either way,
        but this also proves no warning-worthy validation path fired."""
        text = '{"visual": "test", "emotion": "joy"}'
        result = StickerLearningService._parse_vision_response(text)
        assert "explicit" not in result

    def test_explicit_score_regex_fallback_on_truncated_json(self):
        """Attempt 3 (regex fallback for truncated responses) also extracts
        and validates 'explicit', not just the string fields."""
        text = '{"visual": "A cat", "explicit": 0.8, "emotion": "joy'  # truncated
        result = StickerLearningService._parse_vision_response(text)
        assert result["visual"] == "A cat"
        assert result["explicit"] == 0.8

    def test_explicit_score_regex_fallback_rejects_out_of_range(self):
        text = '{"visual": "A cat", "explicit": 12, "emotion": "joy'  # truncated
        result = StickerLearningService._parse_vision_response(text)
        assert result["explicit"] is None


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
                "admin_notes": None,
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
        """merge prompt emphasizes original vision description and priority rules."""
        from src.services.ai.base import TextGenerationResult

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value={
                "original_vision_description": "Anime girl with blue hair",
                "visual_description": "Anime girl waving",
                "emotion": "happy",
                "character_or_meme": None,
                "suggested_contexts": [],
                "usage_contexts": [],
                "admin_notes": None,
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
        assert "ПРИОРИТЕТ" in prompt

    @pytest.mark.asyncio
    async def test_merge_prompt_includes_accumulated_notes(self, sticker_service):
        """Accumulated admin_notes from previous merges are included in prompt."""
        from src.services.ai.base import TextGenerationResult

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value={
                "original_vision_description": "A cat",
                "visual_description": "A cat",
                "emotion": "joy",
                "character_or_meme": None,
                "suggested_contexts": [],
                "usage_contexts": [],
                "admin_notes": "previous correction note",
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

        await sticker_service.merge_admin_description("unique-123", "new note")

        prompt = sticker_service._ai.generate_text.call_args.kwargs["prompt"]
        assert "previous correction note" in prompt
        assert "Предыдущие заметки" in prompt

    @pytest.mark.asyncio
    async def test_merge_prompt_omits_empty_accumulated_notes(self, sticker_service):
        """When admin_notes is None, the accumulated notes section is omitted."""
        from src.services.ai.base import TextGenerationResult

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value={
                "original_vision_description": "A cat",
                "visual_description": "A cat",
                "emotion": "joy",
                "character_or_meme": None,
                "suggested_contexts": [],
                "usage_contexts": [],
                "admin_notes": None,
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

        await sticker_service.merge_admin_description("unique-123", "new note")

        prompt = sticker_service._ai.generate_text.call_args.kwargs["prompt"]
        assert "Предыдущие заметки" not in prompt


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
        text = StickerLearningService._build_embedding_text("A sticker", None, None, None, None)
        assert text == "A sticker"
        assert "Emotion" not in text


class TestBuildVisionPrompt:
    def test_basic_prompt(self):
        sticker = _make_sticker(set_name="funny_cats")
        prompt = StickerLearningService._build_vision_prompt(sticker)
        assert "funny_cats" in prompt
        assert "JSON" in prompt
        assert "visual" in prompt

    def test_explicit_field_included_in_json_schema(self):
        """ADR-0008 Decision 4: the same Vision call now also asks for an
        explicitness score, on every sticker type (not just when timing/
        motion data is present)."""
        sticker = _make_sticker()
        prompt = StickerLearningService._build_vision_prompt(sticker, sticker_type="static")
        assert '"explicit"' in prompt

    def test_with_pack_context(self):
        sticker = _make_sticker()
        prompt = StickerLearningService._build_vision_prompt(
            sticker, pack_context=["Another happy cat", "Sad cat"]
        )
        assert "Another happy cat" in prompt
        assert "Sad cat" in prompt

    @staticmethod
    def _timing_with_motion(*, is_oscillating: bool) -> RenderedSticker:
        motion = AnimationMotion(
            duration=1.0,
            keyframe_indices=[0, 5, 10, 15, 20, 29],
            keyframe_times=[0.0, 0.17, 0.33, 0.5, 0.67, 1.0],
            avg_motion=0.5,
            peak_motion_time=0.33,
            motion_scores=[0.1, 0.9, 0.1, 0.9, 0.1, 0.9],
            is_oscillating=is_oscillating,
        )
        return RenderedSticker(
            collage_png=b"fake-png",
            duration=1.0,
            frame_times=motion.keyframe_times,
            motion=motion,
        )

    def test_oscillation_hint_included_when_motion_is_oscillating_animated(self):
        sticker = _make_sticker(is_animated=True)
        prompt = StickerLearningService._build_vision_prompt(
            sticker,
            sticker_type="animated",
            timing=self._timing_with_motion(is_oscillating=True),
        )
        assert "ОСЦИЛЛЯЦИЯ" in prompt
        assert "ШЛЕЙФ" in prompt

    def test_oscillation_hint_omitted_when_motion_not_oscillating_animated(self):
        sticker = _make_sticker(is_animated=True)
        prompt = StickerLearningService._build_vision_prompt(
            sticker,
            sticker_type="animated",
            timing=self._timing_with_motion(is_oscillating=False),
        )
        assert "ОСЦИЛЛЯЦИЯ" not in prompt
        assert "ШЛЕЙФ" not in prompt

    def test_oscillation_hint_included_when_motion_is_oscillating_video(self):
        sticker = _make_sticker(is_video=True)
        prompt = StickerLearningService._build_vision_prompt(
            sticker,
            sticker_type="video",
            timing=self._timing_with_motion(is_oscillating=True),
        )
        assert "ОСЦИЛЛЯЦИЯ" in prompt
        assert "ШЛЕЙФ" in prompt

    def test_oscillation_hint_omitted_when_motion_not_oscillating_video(self):
        sticker = _make_sticker(is_video=True)
        prompt = StickerLearningService._build_vision_prompt(
            sticker,
            sticker_type="video",
            timing=self._timing_with_motion(is_oscillating=False),
        )
        assert "ОСЦИЛЛЯЦИЯ" not in prompt
        assert "ШЛЕЙФ" not in prompt

    def test_no_oscillation_hint_without_timing(self):
        """Static stickers (no timing/motion at all) never get the hint —
        current route for static stickers stays untouched."""
        sticker = _make_sticker()
        prompt = StickerLearningService._build_vision_prompt(sticker, sticker_type="static")
        assert "ОСЦИЛЛЯЦИЯ" not in prompt


class TestLogUsageOnMerge:
    """merge_admin_description() must call log_usage() after a successful AI call."""

    @pytest.mark.asyncio
    async def test_merge_calls_log_usage_on_success(self, sticker_service):
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value={
                "original_vision_description": "A cat",
                "visual_description": "A cat",
                "emotion": "joy",
                "character_or_meme": None,
                "suggested_contexts": [],
                "usage_contexts": [],
                "admin_notes": None,
            }
        )
        sticker_service._repo.update_description_and_fields = AsyncMock()

        await sticker_service.merge_admin_description("unique-123", "admin note")
        # fire-and-forget tasks
        await asyncio.sleep(0.05)

        sticker_service._ai.log_usage.assert_awaited_once()
        call_kwargs = sticker_service._ai.log_usage.call_args.kwargs
        assert call_kwargs["task_type"] == "sticker_merge"

    @pytest.mark.asyncio
    async def test_merge_does_not_call_log_usage_on_ai_error(self, sticker_service):
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value={
                "original_vision_description": "A cat",
                "visual_description": "A cat",
                "emotion": "joy",
                "character_or_meme": None,
                "suggested_contexts": [],
                "usage_contexts": [],
                "admin_notes": None,
            }
        )
        sticker_service._ai.generate_text = AsyncMock(
            side_effect=AIProviderError("AI down", provider="openai")
        )
        sticker_service._repo.append_admin_note = AsyncMock()

        await sticker_service.merge_admin_description("unique-123", "admin note")
        await asyncio.sleep(0.05)

        sticker_service._ai.log_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_merge_does_not_call_log_usage_when_sticker_not_found(self, sticker_service):
        sticker_service._repo.get_by_file_unique_id = AsyncMock(return_value=None)

        result = await sticker_service.merge_admin_description("missing-uid", "note")
        await asyncio.sleep(0.05)

        assert result is None
        sticker_service._ai.generate_text.assert_not_awaited()
        sticker_service._ai.log_usage.assert_not_awaited()


# ---------------------------------------------------------------------------
# reanalyze() → ReanalyzeResult (A-2)
# ---------------------------------------------------------------------------


def _make_existing_sticker_record(
    file_id: str = "file-123",
    file_unique_id: str = "unique-123",
    set_name: str = "test_set",
    emoji: str = "😀",
    is_animated: bool = False,
    is_video: bool = False,
) -> dict:
    return {
        "file_id": file_id,
        "file_unique_id": file_unique_id,
        "set_name": set_name,
        "emoji": emoji,
        "is_animated": is_animated,
        "is_video": is_video,
    }


def _make_bot_mock(
    file_path: str | None = "stickers/file.webp",
    buf_data: bytes = b"fake-bytes",
) -> MagicMock:
    bot = MagicMock()
    file_obj = MagicMock()
    file_obj.file_path = file_path

    buf = MagicMock()
    buf.read = MagicMock(return_value=buf_data)

    bot.get_file = AsyncMock(return_value=file_obj)
    bot.download_file = AsyncMock(return_value=buf)
    return bot


class TestReanalyze:
    """Tests for StickerLearningService.reanalyze() — new ReanalyzeResult return type."""

    @pytest.mark.asyncio
    async def test_reanalyze_success_returns_ok_result(self, sticker_service):
        """reanalyze() returns ok=True when analysis succeeds."""
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value=_make_existing_sticker_record()
        )
        sticker_service._repo.clear_analysis = AsyncMock()
        bot = _make_bot_mock()

        result = await sticker_service.reanalyze(bot, "unique-123")

        assert isinstance(result, ReanalyzeResult)
        assert result.ok is True
        assert result.reason is None
        assert result.visual_description == "A happy cat"

    @pytest.mark.asyncio
    async def test_reanalyze_sticker_not_found_returns_download_reason(self, sticker_service):
        """If the sticker record is missing, returns ok=False reason='download'."""
        sticker_service._repo.get_by_file_unique_id = AsyncMock(return_value=None)
        bot = _make_bot_mock()

        result = await sticker_service.reanalyze(bot, "missing-uid")

        assert result.ok is False
        assert result.reason == "download"

    @pytest.mark.asyncio
    async def test_reanalyze_download_error_returns_download_reason(self, sticker_service):
        """Telegram download failure returns ok=False reason='download'."""
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value=_make_existing_sticker_record()
        )
        bot = _make_bot_mock()
        bot.get_file = AsyncMock(side_effect=Exception("network error"))

        result = await sticker_service.reanalyze(bot, "unique-123")

        assert result.ok is False
        assert result.reason == "download"

    @pytest.mark.asyncio
    async def test_reanalyze_missing_file_path_returns_download_reason(self, sticker_service):
        """file_path=None on the file object → ok=False reason='download'."""
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value=_make_existing_sticker_record()
        )
        bot = _make_bot_mock(file_path=None)

        result = await sticker_service.reanalyze(bot, "unique-123")

        assert result.ok is False
        assert result.reason == "download"

    @pytest.mark.asyncio
    async def test_reanalyze_vision_api_failure_returns_vision_reason(self, sticker_service):
        """AIProviderError (non-content-filter) → ok=False reason='vision'."""
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value=_make_existing_sticker_record()
        )
        sticker_service._repo.clear_analysis = AsyncMock()
        sticker_service._ai.analyze_image = AsyncMock(
            side_effect=AIProviderError("Vision API down", provider="gemini")
        )
        bot = _make_bot_mock()

        result = await sticker_service.reanalyze(bot, "unique-123")

        assert result.ok is False
        assert result.reason == "vision"

    @pytest.mark.asyncio
    async def test_reanalyze_content_filter_returns_content_filter_reason(self, sticker_service):
        """AIProviderError with PROHIBITED_CONTENT → ok=False reason='content_filter'."""
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value=_make_existing_sticker_record()
        )
        sticker_service._repo.clear_analysis = AsyncMock()
        sticker_service._ai.analyze_image = AsyncMock(
            side_effect=AIProviderError("PROHIBITED_CONTENT blocked", provider="gemini")
        )
        bot = _make_bot_mock()

        result = await sticker_service.reanalyze(bot, "unique-123")

        assert result.ok is False
        assert result.reason == "content_filter"

    @pytest.mark.asyncio
    async def test_reanalyze_empty_vision_response_returns_empty_reason(self, sticker_service):
        """Empty/unparseable vision response → ok=False reason='empty'."""
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value=_make_existing_sticker_record()
        )
        sticker_service._repo.clear_analysis = AsyncMock()
        sticker_service._ai.analyze_image = AsyncMock(
            return_value=VisionResult(text="{}", model="gemini-3-flash", provider="gemini")
        )
        bot = _make_bot_mock()

        result = await sticker_service.reanalyze(bot, "unique-123")

        assert result.ok is False
        assert result.reason == "empty"

    @pytest.mark.asyncio
    async def test_reanalyze_clears_analysis_before_learn(self, sticker_service):
        """clear_analysis() is called before the new learn() run."""
        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            return_value=_make_existing_sticker_record()
        )
        sticker_service._repo.clear_analysis = AsyncMock()
        bot = _make_bot_mock()

        await sticker_service.reanalyze(bot, "unique-123")

        sticker_service._repo.clear_analysis.assert_awaited_once_with("unique-123")


class TestLearnFailureReason:
    """Unit tests for failure_reason propagation in StickerLearningResult (A-2)."""

    @pytest.mark.asyncio
    async def test_learn_vision_error_sets_vision_reason(self, sticker_service):
        """AIProviderError from vision → StickerLearningResult.failure_reason == 'vision'."""
        sticker_service._ai.analyze_image = AsyncMock(
            side_effect=AIProviderError("Vision API down", provider="gemini")
        )
        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=b"fake")

        assert result.analysis_failed is True
        assert result.failure_reason == "vision"

    @pytest.mark.asyncio
    async def test_learn_content_filter_sets_content_filter_reason(self, sticker_service):
        """PROHIBITED_CONTENT AIProviderError → failure_reason == 'content_filter'."""
        sticker_service._ai.analyze_image = AsyncMock(
            side_effect=AIProviderError("PROHIBITED_CONTENT blocked", provider="gemini")
        )
        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=b"fake")

        assert result.analysis_failed is True
        assert result.failure_reason == "content_filter"

    @pytest.mark.asyncio
    async def test_learn_empty_vision_response_sets_empty_reason(self, sticker_service):
        """Empty/no-visual JSON response → failure_reason == 'empty'."""
        sticker_service._ai.analyze_image = AsyncMock(
            return_value=VisionResult(text="{}", model="gemini-3-flash", provider="gemini")
        )
        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=b"fake")

        assert result.analysis_failed is True
        assert result.failure_reason == "empty"

    @pytest.mark.asyncio
    async def test_learn_success_sets_no_failure_reason(self, sticker_service):
        """Successful analysis → failure_reason is None."""
        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=b"fake")

        assert result.analysis_failed is False
        assert result.failure_reason is None

    @pytest.mark.asyncio
    @patch("src.services.modules.sticker.learning.render_tgs", new_callable=AsyncMock)
    async def test_learn_render_failure_sets_vision_reason(self, mock_render_tgs, sticker_service):
        """StickerRenderError on animated sticker → failure_reason == 'vision'."""
        mock_render_tgs.side_effect = StickerRenderError("render failed")
        sticker = _make_sticker(is_animated=True)
        result = await sticker_service.learn(sticker=sticker, image_data=b"fake-tgs")

        assert result.analysis_failed is True
        assert result.failure_reason == "vision"


# ---------------------------------------------------------------------------
# Duplicate detection via image hash (ADR-0007, A-2)
# ---------------------------------------------------------------------------


def _real_png_bytes(seed: int = 0) -> bytes:
    """A real, Pillow-parseable image — needed because compute_image_hash()
    fails open (image_hash=None) on the fake `b"fake-png"` bytes the other
    tests use, which would silently skip the whole dedup code path."""
    img = Image.new("RGBA", (64, 64), (200, 30, 30, 255))
    for x in range(10, 30):
        for y in range(10, 30):
            img.putpixel((x, y), (30 + seed, 255, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _canonical_record(**overrides) -> dict:
    base = {
        "file_unique_id": "canonical-uid",
        "visual_description": "A happy cat waving",
        "original_vision_description": "A happy cat waving",
        "emotion": "joy",
        "suggested_contexts": ["greeting"],
        "style_tags": ["cute"],
        "character_or_meme": "Pepe",
        "description_embedding": [0.2] * 768,
        "image_hash": "0000000000000000",
        "explicitness_score": 0.3,
        "explicitness_is_manual": False,
    }
    base.update(overrides)
    return base


class TestDuplicateDetection:
    """learn()'s pre-Vision image-hash dedup check (ADR-0007)."""

    @pytest.mark.asyncio
    async def test_duplicate_match_skips_vision_and_copies_canonical_fields(self, sticker_service):
        image_data = _real_png_bytes()
        target_hash = compute_image_hash(image_data)

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            side_effect=[None, _canonical_record()]
        )
        sticker_service._repo.get_dedup_candidates = AsyncMock(
            return_value=[
                {
                    "file_unique_id": "canonical-uid",
                    "image_hash": target_hash,
                    "created_at": "2026-01-01T00:00:00Z",
                    "duplicate_of_file_unique_id": None,
                }
            ]
        )

        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=image_data)

        assert result.is_new is True
        assert result.analysis_failed is False
        assert result.duplicate_of == "canonical-uid"
        assert result.visual_description == "A happy cat waving"
        assert result.emotion == "joy"
        assert result.character_or_meme == "Pepe"
        # ADR-0008 Decision 7: explicitness_score is a Vision-derived column
        # too — copied verbatim from the canonical row, no new Vision call.
        assert result.explicitness_score == 0.3
        # ADR-0009 Decision 6: canonical wasn't manually-scored -> duplicate isn't either.
        assert result.explicitness_is_manual is False

        sticker_service._ai.analyze_image.assert_not_awaited()
        sticker_service._ai.generate_embedding.assert_not_awaited()

        sticker_service._repo.save_sticker.assert_awaited_once()
        save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
        assert save_kwargs["visual_description"] == "A happy cat waving"
        assert save_kwargs["duplicate_of_file_unique_id"] == "canonical-uid"
        assert save_kwargs["image_hash"] == target_hash
        assert save_kwargs["explicitness_score"] == 0.3
        assert save_kwargs["explicitness_is_manual"] is False

        # Embedding copied via update_embedding(), not regenerated.
        sticker_service._repo.update_embedding.assert_awaited_once_with(
            sticker.file_unique_id, [0.2] * 768
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "canonical_row",
        [
            None,
            _canonical_record(visual_description=None, original_vision_description=None),
            _canonical_record(analysis_failed=True),
        ],
        ids=["vanished", "analysis-cleared", "analysis-failed"],
    )
    async def test_dead_canonical_falls_back_to_vision(self, sticker_service, canonical_row):
        """A hash match whose canonical row vanished, was cleared via
        «Очистить анализ», or has analysis_failed=true must NOT be copied
        from — copying would mint a permanently dead row (no description,
        analysis_failed=false, never re-analyzed because learn()
        short-circuits on existing rows). The chain stays reachable through
        find_duplicate()'s flattening even though the canonical itself
        dropped out of get_dedup_candidates() (2026-08-07 review). All three
        cases fall open into the normal Vision pipeline."""
        image_data = _real_png_bytes()
        target_hash = compute_image_hash(image_data)

        sticker_service._repo.get_by_file_unique_id = AsyncMock(side_effect=[None, canonical_row])
        sticker_service._repo.get_dedup_candidates = AsyncMock(
            return_value=[
                {
                    "file_unique_id": "canonical-uid",
                    "image_hash": target_hash,
                    "created_at": "2026-01-01T00:00:00Z",
                    "duplicate_of_file_unique_id": None,
                }
            ]
        )

        result = await sticker_service.learn(sticker=_make_sticker(), image_data=image_data)

        sticker_service._ai.analyze_image.assert_awaited()
        assert result.duplicate_of is None
        assert result.visual_description == "A happy cat"

    @pytest.mark.asyncio
    async def test_duplicate_copies_null_explicitness_score_from_unscored_canonical(
        self, sticker_service
    ):
        """ADR-0008 Decision 7 accepted edge case: a canonical row that
        predates the explicitness feature (NULL) makes the new duplicate
        NULL too, not a fresh Vision call and not a fabricated 0.0."""
        image_data = _real_png_bytes()
        target_hash = compute_image_hash(image_data)

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            side_effect=[None, _canonical_record(explicitness_score=None)]
        )
        sticker_service._repo.get_dedup_candidates = AsyncMock(
            return_value=[
                {
                    "file_unique_id": "canonical-uid",
                    "image_hash": target_hash,
                    "created_at": "2026-01-01T00:00:00Z",
                    "duplicate_of_file_unique_id": None,
                }
            ]
        )

        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=image_data)

        assert result.explicitness_score is None
        save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
        assert save_kwargs["explicitness_score"] is None

    @pytest.mark.asyncio
    async def test_duplicate_copies_manual_flag_together_with_score(self, sticker_service):
        """ADR-0009 Decision 6: a canonical row with a hand-vetted score
        makes the new duplicate inherit BOTH the score and the manual flag
        together — copying the score alone would silently reintroduce the
        ADR's own bug one hop away (the duplicate's own first re-analysis
        would then clobber it, since a bare INSERT defaults the flag back
        to false)."""
        image_data = _real_png_bytes()
        target_hash = compute_image_hash(image_data)

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            side_effect=[
                None,
                _canonical_record(explicitness_score=0.9, explicitness_is_manual=True),
            ]
        )
        sticker_service._repo.get_dedup_candidates = AsyncMock(
            return_value=[
                {
                    "file_unique_id": "canonical-uid",
                    "image_hash": target_hash,
                    "created_at": "2026-01-01T00:00:00Z",
                    "duplicate_of_file_unique_id": None,
                }
            ]
        )

        result = await sticker_service.learn(sticker=_make_sticker(), image_data=image_data)

        assert result.explicitness_score == 0.9
        assert result.explicitness_is_manual is True
        save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
        assert save_kwargs["explicitness_score"] == 0.9
        assert save_kwargs["explicitness_is_manual"] is True

    @pytest.mark.asyncio
    async def test_no_matching_candidate_falls_through_to_vision(self, sticker_service):
        image_data = _real_png_bytes()
        target_hash = compute_image_hash(image_data)
        # Flip every bit -> maximum possible distance, guaranteed to exceed
        # DEDUP_HAMMING_THRESHOLD regardless of what target_hash happens to be.
        far_hash = f"{(~int(target_hash, 16)) & ((1 << 64) - 1):016x}"

        sticker_service._repo.get_dedup_candidates = AsyncMock(
            return_value=[
                {
                    "file_unique_id": "other-uid",
                    "image_hash": far_hash,
                    "created_at": "2026-01-01T00:00:00Z",
                    "duplicate_of_file_unique_id": None,
                }
            ]
        )

        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=image_data)

        assert result.duplicate_of is None
        sticker_service._ai.analyze_image.assert_awaited_once()
        sticker_service._repo.save_sticker.assert_awaited_once()
        save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
        assert save_kwargs["image_hash"] == target_hash
        assert save_kwargs["duplicate_of_file_unique_id"] is None

    @pytest.mark.asyncio
    async def test_force_reanalyze_skips_dedup_check_entirely(self, sticker_service):
        """Admin re-analyze must always run Vision, never silently resolve to
        a copy — even if a matching candidate exists (contract of
        force_reanalyze, unchanged by ADR-0007)."""
        image_data = _real_png_bytes()
        sticker_service._repo.get_dedup_candidates = AsyncMock(return_value=[])

        sticker = _make_sticker()
        await sticker_service.learn(sticker=sticker, image_data=image_data, force_reanalyze=True)

        sticker_service._repo.get_dedup_candidates.assert_not_awaited()
        sticker_service._ai.analyze_image.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unparseable_image_fails_open_to_vision(self, sticker_service):
        """Hash computation failure must not break ingestion — proceeds to
        the normal Vision pipeline exactly as if there were no candidates."""
        sticker_service._repo.get_dedup_candidates = AsyncMock(return_value=[])

        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=b"not-an-image")

        assert result.analysis_failed is False
        sticker_service._repo.get_dedup_candidates.assert_not_awaited()
        sticker_service._ai.analyze_image.assert_awaited_once()
        save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
        assert save_kwargs["image_hash"] is None

    @pytest.mark.asyncio
    @patch("src.services.modules.sticker.learning.render_tgs", new_callable=AsyncMock)
    async def test_duplicate_reapplies_format_tag_for_own_type(
        self, mock_render_tgs, sticker_service
    ):
        """Pitfall 1 (ADR-0007 Decision 7): a cross-type hash match must not
        carry over the canonical's format tag verbatim — the new (animated)
        sticker gets its OWN format tag, not the canonical's (a static
        sticker with no format tag, and a stale 'video' tag to prove it's
        stripped, not merely appended-to)."""
        hash_frame = _real_png_bytes()
        target_hash = compute_image_hash(hash_frame)
        mock_render_tgs.return_value = RenderedSticker(
            collage_png=b"fake-collage-png",
            duration=3.0,
            frame_times=[0.0, 0.6, 1.2, 1.8, 2.4, 3.0],
            hash_frame=hash_frame,
        )

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            side_effect=[
                None,
                _canonical_record(style_tags=["meme", "video"]),
            ]
        )
        sticker_service._repo.get_dedup_candidates = AsyncMock(
            return_value=[
                {
                    "file_unique_id": "canonical-uid",
                    "image_hash": target_hash,
                    "created_at": "2026-01-01T00:00:00Z",
                    "duplicate_of_file_unique_id": None,
                }
            ]
        )

        sticker = _make_sticker(is_animated=True)
        result = await sticker_service.learn(sticker=sticker, image_data=b"fake-tgs")

        assert result.duplicate_of == "canonical-uid"
        save_kwargs = sticker_service._repo.save_sticker.call_args.kwargs
        assert save_kwargs["style_tags"] == ["meme", "animated"]

    @pytest.mark.asyncio
    async def test_duplicate_chain_flattens_to_root(self, sticker_service):
        """Matching a row that is itself already a detected duplicate points
        the new sticker at the ROOT, not the intermediate row (Decision 6)."""
        image_data = _real_png_bytes()
        target_hash = compute_image_hash(image_data)

        sticker_service._repo.get_by_file_unique_id = AsyncMock(
            side_effect=[None, _canonical_record(file_unique_id="root-uid")]
        )
        sticker_service._repo.get_dedup_candidates = AsyncMock(
            return_value=[
                {
                    "file_unique_id": "mid-duplicate-uid",
                    "image_hash": target_hash,
                    "created_at": "2026-01-01T00:00:00Z",
                    "duplicate_of_file_unique_id": "root-uid",
                }
            ]
        )

        sticker = _make_sticker()
        result = await sticker_service.learn(sticker=sticker, image_data=image_data)

        assert result.duplicate_of == "root-uid"
        # The copy reads the ROOT's own record, not the intermediate row's.
        sticker_service._repo.get_by_file_unique_id.assert_awaited_with("root-uid")


# ---------------------------------------------------------------------------
# notify_admins — explicitness line + tolerance_level threading (A-1)
# ---------------------------------------------------------------------------


def _make_notify_bot() -> MagicMock:
    bot = MagicMock()
    sticker_msg = MagicMock()
    sticker_msg.message_id = 1
    bot.send_sticker = AsyncMock(return_value=sticker_msg)
    desc_msg = MagicMock()
    desc_msg.message_id = 2
    bot.send_message = AsyncMock(return_value=desc_msg)
    return bot


class TestNotifyAdminsExplicitnessLine:
    @pytest.mark.asyncio
    async def test_scored_result_shows_pass_verdict_against_chat_tolerance(self, sticker_service):
        sticker_service._repo.save_notification = AsyncMock()
        bot = _make_notify_bot()
        sticker = _make_sticker()
        result = StickerLearningResult(
            is_new=True,
            file_unique_id="unique-123",
            visual_description="a cat",
            explicitness_score=0.3,
        )

        await sticker_service.notify_admins(bot, sticker, result, [111], tolerance_level=0.5)

        text = bot.send_message.call_args.args[1]
        assert "0.30" in text
        assert "0.50" in text
        assert "✅ пройдёт" in text

    @pytest.mark.asyncio
    async def test_unscored_result_shows_not_scored_never_a_verdict(self, sticker_service):
        sticker_service._repo.save_notification = AsyncMock()
        bot = _make_notify_bot()
        sticker = _make_sticker()
        result = StickerLearningResult(
            is_new=True,
            file_unique_id="unique-123",
            visual_description="a cat",
            explicitness_score=None,
        )

        await sticker_service.notify_admins(bot, sticker, result, [111], tolerance_level=1.0)

        text = bot.send_message.call_args.args[1]
        assert "не оценён" in text
        assert "✅" not in text
        assert "❌" not in text

    @pytest.mark.asyncio
    async def test_uses_the_default_tolerance_level_kwarg_when_caller_omits_it(
        self, sticker_service
    ):
        """media.py always threads the real chat's tolerance_level, but the
        function-level default (ChatConfig's own 0.5 fallback) must still
        produce a sane, non-crashing verdict for any other caller."""
        sticker_service._repo.save_notification = AsyncMock()
        bot = _make_notify_bot()
        sticker = _make_sticker()
        result = StickerLearningResult(
            is_new=True,
            file_unique_id="unique-123",
            visual_description="a cat",
            explicitness_score=0.4,
        )

        await sticker_service.notify_admins(bot, sticker, result, [111])

        text = bot.send_message.call_args.args[1]
        assert "0.50" in text
        assert "✅ пройдёт" in text

    @pytest.mark.asyncio
    async def test_no_description_omits_explicitness_line(self, sticker_service):
        """Defensive gate mirroring admin_sticker.py's own: no visual
        description at all -> no explicitness line, even if a score
        somehow came back (shouldn't happen per ADR-0008 Decision 4, but
        must not render a misleading line if it does)."""
        sticker_service._repo.save_notification = AsyncMock()
        bot = _make_notify_bot()
        sticker = _make_sticker()
        result = StickerLearningResult(
            is_new=True,
            file_unique_id="unique-123",
            visual_description=None,
            explicitness_score=0.4,
        )

        await sticker_service.notify_admins(bot, sticker, result, [111])

        text = bot.send_message.call_args.args[1]
        assert "Оценка откровенности" not in text
