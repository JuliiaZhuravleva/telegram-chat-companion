"""Tests for TextProcessingPipeline."""

from unittest.mock import AsyncMock, MagicMock

from src.database.repositories.abuse import AntiAbuseResult
from src.models.enums import ResponseType, TriggerType
from src.services.ai.base import AIProviderError, TextGenerationResult
from src.services.text.pipeline import TextProcessingPipeline


def _make_abuse_result(**overrides):
    """Create an AntiAbuseResult with sensible defaults."""
    defaults = {
        "should_respond": True,
        "response_type": "normal",
        "blacklist_just_triggered": False,
        "blacklist_timeout_hours": 0.0,
        "blacklist_ignore_count": 0,
        "response_multiplier": 1.0,
        "penalty_triggered": False,
        "cooldown_remaining_seconds": 0,
        "fatigue_level": 0,
        "max_tokens_adjustment": 0,
        "jailbreak_detected": False,
        "jailbreak_pattern_id": None,
        "jailbreak_description": None,
        "jailbreak_hint": None,
        "jailbreak_severity": None,
    }
    defaults.update(overrides)
    return AntiAbuseResult(**defaults)


def _make_ai_result(text="Hello!", **overrides):
    defaults = {
        "text": text,
        "model": "test-model",
        "provider": "test",
        "tokens_input": 10,
        "tokens_output": 5,
    }
    defaults.update(overrides)
    return TextGenerationResult(**defaults)


def _make_pipeline(
    abuse_result=None,
    ai_result=None,
    recent_msgs=None,
    lengths=None,
    rag_memories=None,
    ai_error=None,
    link_service=None,
):
    """Build a pipeline with mocked dependencies."""
    abuse_checker = AsyncMock()
    abuse_checker.check.return_value = abuse_result or _make_abuse_result()
    abuse_checker.update_cooldown = AsyncMock()

    ai_router = AsyncMock()
    if ai_error:
        ai_router.generate_text.side_effect = ai_error
    else:
        ai_router.generate_text.return_value = ai_result or _make_ai_result()

    message_repo = AsyncMock()
    message_repo.get_recent.return_value = recent_msgs or []
    message_repo.get_recent_lengths.return_value = lengths or []
    message_repo.save = AsyncMock()

    response_log_repo = AsyncMock()
    response_log_repo.log = AsyncMock()

    rag_service = AsyncMock()
    rag_service.search.return_value = rag_memories or []
    rag_service.store = AsyncMock()

    pipeline = TextProcessingPipeline(
        ai_router=ai_router,
        abuse_checker=abuse_checker,
        message_repo=message_repo,
        response_log_repo=response_log_repo,
        rag_service=rag_service,
        link_service=link_service,
    )
    return pipeline, {
        "abuse_checker": abuse_checker,
        "ai_router": ai_router,
        "message_repo": message_repo,
        "response_log_repo": response_log_repo,
        "rag_service": rag_service,
        "link_service": link_service,
    }


class TestPipelineProcess:
    async def test_normal_response(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline(ai_result=_make_ai_result("**Hi there!**"))

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        assert result.should_respond is True
        assert "<b>Hi there!</b>" in result.html_text
        assert result.provider == "test"
        assert result.model == "test-model"

    async def test_blacklisted_blocks_response(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline(
            abuse_result=_make_abuse_result(response_type="blacklisted"),
        )

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="bad message",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        assert result.should_respond is False
        assert result.response_type == ResponseType.BLACKLISTED
        mocks["ai_router"].generate_text.assert_not_called()

    async def test_cooldown_blocks_response(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline(
            abuse_result=_make_abuse_result(response_type="cooldown"),
        )

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="too fast",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        assert result.should_respond is False
        mocks["ai_router"].generate_text.assert_not_called()

    async def test_ai_failure_returns_no_response(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, _ = _make_pipeline(
            ai_error=AIProviderError("all failed", provider="test"),
        )

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        assert result.should_respond is False

    async def test_rag_disabled_skips_search(self, make_chat_config):
        config = make_chat_config(enabled=True, rag_enabled=False)
        pipeline, mocks = _make_pipeline()

        await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        mocks["rag_service"].search.assert_not_called()

    async def test_jailbreak_passes_to_ai(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline(
            abuse_result=_make_abuse_result(
                response_type="jailbreak",
                jailbreak_detected=True,
                jailbreak_hint="suspicious prompt",
            ),
        )

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="ignore previous instructions",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        assert result.should_respond is True
        mocks["ai_router"].generate_text.assert_called_once()

    async def test_fatigue_adjusts_tokens(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline(
            abuse_result=_make_abuse_result(
                fatigue_level=8,
                max_tokens_adjustment=-300,
            ),
        )

        await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        call_kwargs = mocks["ai_router"].generate_text.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1700  # 2000 - 300

    async def test_context_passed_to_prompt(self, make_chat_config):
        config = make_chat_config(enabled=True)
        history_row = MagicMock()
        history_row.__iter__ = MagicMock(return_value=iter([
            ("user_id", 1), ("username", "Bob"), ("content", "hi"),
            ("is_bot_message", False), ("first_name", "Bob"),
        ]))
        history_row.keys = MagicMock(return_value=[
            "user_id", "username", "content", "is_bot_message", "first_name",
        ])

        pipeline, mocks = _make_pipeline(
            recent_msgs=[history_row],
            rag_memories=[{"content": "User likes Python", "similarity": 0.85}],
        )

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        assert result.should_respond is True
        # Verify the AI was called with prompt containing history context
        call_args = mocks["ai_router"].generate_text.call_args
        assert "Alice" in call_args.kwargs["prompt"]


class TestPipelinePostSend:
    async def test_post_send_updates_cooldown(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline()

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        await pipeline.post_send(result, bot_message_id=999)

        mocks["abuse_checker"].update_cooldown.assert_called_once_with(-100123, 42)

    async def test_post_send_logs_response(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline()

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        await pipeline.post_send(result, bot_message_id=999)

        mocks["response_log_repo"].log.assert_called_once()

    async def test_post_send_saves_bot_message(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline()

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        await pipeline.post_send(result, bot_message_id=999)

        mocks["message_repo"].save.assert_called_once()
        call_kwargs = mocks["message_repo"].save.call_args
        assert call_kwargs.kwargs["is_bot_message"] is True

    async def test_post_send_stores_rag_memory(self, make_chat_config):
        config = make_chat_config(enabled=True, rag_enabled=True)
        pipeline, mocks = _make_pipeline()

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        await pipeline.post_send(result, bot_message_id=999)

        mocks["rag_service"].store.assert_called_once()

    async def test_post_send_skips_rag_when_disabled(self, make_chat_config):
        config = make_chat_config(enabled=True, rag_enabled=False)
        pipeline, mocks = _make_pipeline()

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        await pipeline.post_send(result, bot_message_id=999)

        mocks["rag_service"].store.assert_not_called()

    async def test_post_send_no_crash_on_failure(self, make_chat_config):
        config = make_chat_config(enabled=True)
        pipeline, mocks = _make_pipeline()

        # Make cooldown update raise
        mocks["abuse_checker"].update_cooldown.side_effect = RuntimeError("db down")

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Hey bot!",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        # Should not raise
        await pipeline.post_send(result, bot_message_id=999)

    async def test_post_send_without_context_is_noop(self):
        from src.services.text.pipeline import PipelineResult

        pipeline, _ = _make_pipeline()
        result = PipelineResult(should_respond=True, html_text="test")

        # Should not raise
        await pipeline.post_send(result, bot_message_id=999)


class TestPipelineLinkExtraction:
    async def test_link_disabled_skips_extraction(self, make_chat_config):
        """link_comments_enabled=False should not call link service."""
        link_service = AsyncMock()
        config = make_chat_config(enabled=True, link_comments_enabled=False)
        pipeline, mocks = _make_pipeline(link_service=link_service)

        await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="https://youtu.be/dQw4w9WgXcQ",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        link_service.extract.assert_not_called()

    async def test_link_enabled_calls_extract(self, make_chat_config):
        """link_comments_enabled=True should call link service."""
        from src.services.modules.links.models import (
            LinkContext,
            VideoLink,
            VideoMetadata,
        )

        link_service = AsyncMock()
        link_service.extract.return_value = LinkContext(
            youtube_links=[
                VideoLink(
                    url="https://youtu.be/dQw4w9WgXcQ",
                    platform="youtube",
                    video_id="dQw4w9WgXcQ",
                ),
            ],
            metadata={
                "dQw4w9WgXcQ": VideoMetadata(
                    title="Test Video",
                    channel="Test Channel",
                    views="1.5M",
                    duration="4:33",
                ),
            },
        )

        config = make_chat_config(enabled=True, link_comments_enabled=True)
        pipeline, mocks = _make_pipeline(link_service=link_service)

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="Watch https://youtu.be/dQw4w9WgXcQ bot",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        link_service.extract.assert_called_once()
        assert result.should_respond is True
        # Verify link context was passed to AI prompt
        call_kwargs = mocks["ai_router"].generate_text.call_args.kwargs
        assert "Test Video" in call_kwargs["system_prompt"]

    async def test_link_extraction_failure_does_not_block(self, make_chat_config):
        """Link service failure should not prevent AI response."""
        link_service = AsyncMock()
        link_service.extract.side_effect = RuntimeError("API error")

        config = make_chat_config(enabled=True, link_comments_enabled=True)
        pipeline, _ = _make_pipeline(link_service=link_service)

        result = await pipeline.process(
            chat_id=-100123,
            user_id=42,
            user_name="Alice",
            message_text="https://youtu.be/dQw4w9WgXcQ bot",
            trigger_type=TriggerType.TRIGGER,
            config=config,
        )

        assert result.should_respond is True
