"""Tests for src.services.ai.base — data classes, exceptions, AIProvider ABC."""

import pytest

from src.services.ai.base import (
    AIProvider,
    AIProviderError,
    EmbeddingResult,
    RateLimitError,
    TextGenerationResult,
    TranscriptionResult,
    VisionResult,
)


class TestDataclasses:
    """Test that result dataclasses hold data correctly."""

    def test_text_generation_result(self):
        r = TextGenerationResult(text="hello", model="gpt-5-nano", provider="openai")
        assert r.text == "hello"
        assert r.tokens_input is None
        assert r.tokens_output is None
        assert r.finish_reason is None

    def test_text_generation_result_with_all_fields(self):
        r = TextGenerationResult(
            text="hello",
            model="gpt-5-nano",
            provider="openai",
            tokens_input=10,
            tokens_output=5,
            finish_reason="stop",
        )
        assert r.tokens_input == 10
        assert r.finish_reason == "stop"

    def test_embedding_result(self):
        r = EmbeddingResult(embedding=[0.1, 0.2], model="embed", provider="openai", dimensions=2)
        assert len(r.embedding) == 2
        assert r.dimensions == 2

    def test_vision_result(self):
        r = VisionResult(text="a cat", model="vision", provider="gemini")
        assert r.text == "a cat"

    def test_transcription_result_defaults(self):
        r = TranscriptionResult(text="hello", model="whisper", provider="openai")
        assert r.language is None
        assert r.duration is None

    def test_transcription_result_with_all_fields(self):
        r = TranscriptionResult(
            text="hello",
            model="whisper",
            provider="openai",
            language="en",
            duration=3.5,
        )
        assert r.language == "en"
        assert r.duration == 3.5


class TestExceptions:
    """Test exception hierarchy and attributes."""

    def test_ai_provider_error_attributes(self):
        err = AIProviderError("failed", provider="openai", retriable=False)
        assert str(err) == "failed"
        assert err.provider == "openai"
        assert err.retriable is False

    def test_ai_provider_error_default_not_retriable(self):
        err = AIProviderError("failed", provider="test")
        assert err.retriable is False

    def test_rate_limit_error_is_retriable(self):
        err = RateLimitError("rate limited", provider="openai", retry_after=30.0)
        assert err.retriable is True
        assert err.retry_after == 30.0

    def test_rate_limit_error_inherits_from_ai_provider_error(self):
        err = RateLimitError("rate limited", provider="openai")
        assert isinstance(err, AIProviderError)

    def test_rate_limit_error_retry_after_optional(self):
        err = RateLimitError("rate limited", provider="openai")
        assert err.retry_after is None


class TestAIProviderABC:
    """Test the abstract base class behavior."""

    def test_supports_method(self, mock_provider):
        provider = mock_provider(
            supported_capabilities={"text_generation": True, "vision": False},
        )
        assert provider.supports("text_generation") is True
        assert provider.supports("vision") is False
        assert provider.supports("unknown") is False

    def test_api_key_stored(self, mock_provider):
        provider = mock_provider(api_key="sk-secret")
        assert provider._api_key == "sk-secret"

    def test_cannot_instantiate_abstract_directly(self):
        with pytest.raises(TypeError):
            AIProvider(api_key="test")  # type: ignore[abstract]
