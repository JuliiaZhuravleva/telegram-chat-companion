"""Tests for GeminiProvider."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.ai.base import AIProviderError, RateLimitError
from src.services.ai.providers.gemini import GeminiProvider


@pytest.fixture
def provider():
    return GeminiProvider(api_key="test-gemini-key")


def _mock_response(status_code: int = 200, json_data: dict | None = None, text: str = ""):
    """Create a mock httpx.Response."""
    kwargs: dict = {"status_code": status_code, "request": httpx.Request("POST", "https://example.com")}
    if json_data is not None:
        kwargs["json"] = json_data
    else:
        kwargs["text"] = text
    return httpx.Response(**kwargs)


# -- Text Generation --


class TestGenerateText:
    async def test_successful_generation(self, provider):
        mock_resp = _mock_response(
            json_data={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Hello, world!"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 5,
                },
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.generate_text("Say hello")

        assert result.text == "Hello, world!"
        assert result.model == "gemini-3-flash-preview"
        assert result.provider == "gemini"
        assert result.tokens_input == 10
        assert result.tokens_output == 5
        assert result.finish_reason == "STOP"

    async def test_custom_model_and_params(self, provider):
        mock_resp = _mock_response(
            json_data={
                "candidates": [{"content": {"parts": [{"text": "Custom response"}]}}],
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await provider.generate_text(
                "Test",
                system_prompt="Be helpful",
                model="gemini-3-pro-preview",
                max_tokens=1000,
                temperature=0.5,
            )

        # Verify the URL contains the custom model
        call_args = mock_post.call_args
        assert "gemini-3-pro-preview" in call_args[0][0]

        # Verify system prompt is concatenated
        payload = call_args[1]["json"]
        text = payload["contents"][0]["parts"][0]["text"]
        assert "Be helpful" in text
        assert "Test" in text

    async def test_verbal_error_detection(self, provider):
        mock_resp = _mock_response(
            json_data={
                "candidates": [{"content": {"parts": [{"text": "Не удалось обработать"}]}}],
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="verbal error"):
                await provider.generate_text("Test")

    async def test_empty_response_is_verbal_error(self, provider):
        mock_resp = _mock_response(
            json_data={
                "candidates": [{"content": {"parts": [{"text": ""}]}}],
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="verbal error"):
                await provider.generate_text("Test")

    async def test_no_candidates(self, provider):
        mock_resp = _mock_response(json_data={"candidates": []})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="no candidates"):
                await provider.generate_text("Test")


# -- Embeddings --


class TestGenerateEmbedding:
    async def test_successful_embedding(self, provider):
        embedding_values = [0.1] * 768
        mock_resp = _mock_response(
            json_data={"embedding": {"values": embedding_values}}
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.generate_embedding("Test text")

        assert result.embedding == embedding_values
        assert result.model == "gemini-embedding-001"
        assert result.provider == "gemini"
        assert result.dimensions == 768

    async def test_empty_embedding(self, provider):
        mock_resp = _mock_response(json_data={"embedding": {"values": []}})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="empty embedding"):
                await provider.generate_embedding("Test")

    async def test_custom_dimensions(self, provider):
        mock_resp = _mock_response(
            json_data={"embedding": {"values": [0.1] * 256}}
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            result = await provider.generate_embedding("Test", dimensions=256)

        payload = mock_post.call_args[1]["json"]
        assert payload["outputDimensionality"] == 256
        assert result.dimensions == 256


# -- Vision --


class TestAnalyzeImage:
    async def test_successful_analysis(self, provider):
        mock_resp = _mock_response(
            json_data={
                "candidates": [{"content": {"parts": [{"text": "A cat sitting on a table"}]}}],
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.analyze_image(b"fake-image-data", "Describe this image")

        assert result.text == "A cat sitting on a table"
        assert result.provider == "gemini"

    async def test_image_payload_format(self, provider):
        mock_resp = _mock_response(
            json_data={
                "candidates": [{"content": {"parts": [{"text": "Description"}]}}],
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await provider.analyze_image(b"fake", "Describe", mime_type="image/png")

        payload = mock_post.call_args[1]["json"]
        parts = payload["contents"][0]["parts"]
        assert parts[0]["inline_data"]["mime_type"] == "image/png"
        assert parts[1]["text"] == "Describe"

        # Vision uses lower temperature
        assert payload["generationConfig"]["temperature"] == 0.4


# -- Rate Limiting --


class TestRateLimiting:
    async def test_http_429(self, provider):
        mock_resp = _mock_response(status_code=429, text="Too many requests")

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(RateLimitError) as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retry_after == 65.0  # default + buffer

    async def test_rate_limit_pattern_in_body(self, provider):
        mock_resp = _mock_response(
            status_code=200,
            text='{"error": {"message": "Resource exhausted, please retry in 30.5s"}}',
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(RateLimitError) as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retry_after == 35.5  # 30.5 + 5.0 buffer

    async def test_quota_exceeded_pattern(self, provider):
        mock_resp = _mock_response(
            status_code=200,
            text='{"error": {"message": "You have exceeded your quota"}}',
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(RateLimitError):
                await provider.generate_text("Test")


# -- Error Handling --


class TestErrorHandling:
    async def test_api_error_in_body(self, provider):
        mock_resp = _mock_response(
            json_data={"error": {"message": "Invalid API key"}}
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="Invalid API key"):
                await provider.generate_text("Test")

    async def test_http_500_is_retriable(self, provider):
        mock_resp = _mock_response(status_code=500, text="Internal server error")

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError) as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retriable is True

    async def test_http_400_is_not_retriable(self, provider):
        mock_resp = _mock_response(status_code=400, text="Bad request")

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError) as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retriable is False

    async def test_timeout(self, provider):
        with patch.object(
            provider._client, "post", new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("timed out"),
        ):
            with pytest.raises(AIProviderError, match="timed out") as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retriable is True


# -- Static helpers --


class TestVerbalErrorDetection:
    def test_russian_error_prefix_short(self):
        assert GeminiProvider._is_verbal_error("Не удалось обработать") is True

    def test_russian_error_prefix_long_text_many_words(self):
        # Long text with many words should NOT be a verbal error
        long_text = "Не удалось " + "word " * 20
        assert GeminiProvider._is_verbal_error(long_text) is False

    def test_normal_text(self):
        assert GeminiProvider._is_verbal_error("Привет! Как дела?") is False

    def test_empty_text(self):
        assert GeminiProvider._is_verbal_error("") is True

    def test_невозможно_prefix(self):
        assert GeminiProvider._is_verbal_error("Невозможно выполнить") is True
