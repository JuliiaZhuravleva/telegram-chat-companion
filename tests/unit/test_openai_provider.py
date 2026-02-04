"""Tests for OpenAIProvider."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.ai.base import AIProviderError, RateLimitError
from src.services.ai.providers.openai import OpenAIProvider


@pytest.fixture
def provider():
    return OpenAIProvider(api_key="test-openai-key")


def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
    headers: dict | None = None,
):
    """Create a mock httpx.Response."""
    kwargs: dict = {
        "status_code": status_code,
        "headers": headers or {},
        "request": httpx.Request("POST", "https://example.com"),
    }
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
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 15, "completion_tokens": 3},
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.generate_text("Say hello")

        assert result.text == "Hello!"
        assert result.model == "gpt-5-nano"
        assert result.provider == "openai"
        assert result.tokens_input == 15
        assert result.tokens_output == 3
        assert result.finish_reason == "stop"

    async def test_with_system_prompt(self, provider):
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": "Response"}, "finish_reason": "stop"}],
                "usage": {},
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await provider.generate_text("User msg", system_prompt="Be helpful")

        payload = mock_post.call_args[1]["json"]
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "Be helpful"
        assert payload["messages"][1]["role"] == "user"

    async def test_without_system_prompt(self, provider):
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": "Response"}, "finish_reason": "stop"}],
                "usage": {},
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await provider.generate_text("User msg")

        payload = mock_post.call_args[1]["json"]
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"

    async def test_no_choices(self, provider):
        mock_resp = _mock_response(json_data={"choices": []})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="no choices"):
                await provider.generate_text("Test")

    async def test_empty_content(self, provider):
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="empty content"):
                await provider.generate_text("Test")


# -- Embeddings --


class TestGenerateEmbedding:
    async def test_successful_embedding(self, provider):
        embedding = [0.1, 0.2, 0.3] * 512
        mock_resp = _mock_response(
            json_data={"data": [{"embedding": embedding, "index": 0}]}
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.generate_embedding("Test text")

        assert result.embedding == embedding
        assert result.model == "text-embedding-3-small"
        assert result.provider == "openai"
        assert result.dimensions == len(embedding)

    async def test_empty_data(self, provider):
        mock_resp = _mock_response(json_data={"data": []})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="empty embedding data"):
                await provider.generate_embedding("Test")


# -- Vision --


class TestAnalyzeImage:
    async def test_output_text_format(self, provider):
        mock_resp = _mock_response(
            json_data={"output_text": "A dog playing fetch"}
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.analyze_image(b"fake-image", "Describe this")

        assert result.text == "A dog playing fetch"
        assert result.provider == "openai"

    async def test_output_array_format(self, provider):
        mock_resp = _mock_response(
            json_data={
                "output": [{"content": [{"text": "A sunset over mountains"}]}]
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.analyze_image(b"fake-image", "Describe")

        assert result.text == "A sunset over mountains"

    async def test_empty_vision_response(self, provider):
        mock_resp = _mock_response(json_data={})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="empty response"):
                await provider.analyze_image(b"fake", "Describe")

    async def test_payload_format(self, provider):
        mock_resp = _mock_response(json_data={"output_text": "Result"})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await provider.analyze_image(b"data", "Prompt", mime_type="image/png")

        call_url = mock_post.call_args[0][0]
        assert "/responses" in call_url

        payload = mock_post.call_args[1]["json"]
        content = payload["input"][0]["content"]
        assert content[0]["type"] == "input_image"
        assert content[0]["image_url"].startswith("data:image/png;base64,")
        assert content[1]["type"] == "input_text"


# -- Transcription --


class TestTranscribeAudio:
    async def test_successful_transcription(self, provider):
        mock_resp = _mock_response(
            json_data={"text": "Hello, how are you?", "language": "en"}
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.transcribe_audio(b"fake-audio-data")

        assert result.text == "Hello, how are you?"
        assert result.model == "whisper-1"
        assert result.provider == "openai"
        assert result.language == "en"

    async def test_with_language_hint(self, provider):
        mock_resp = _mock_response(
            json_data={"text": "Привет", "language": "ru"}
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await provider.transcribe_audio(b"audio", language="ru")

        # Check that language is in the form data
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["data"]["language"] == "ru"

    async def test_multipart_upload(self, provider):
        mock_resp = _mock_response(json_data={"text": "test"})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await provider.transcribe_audio(b"audio-bytes", filename="voice.ogg")

        call_kwargs = mock_post.call_args[1]
        assert "files" in call_kwargs
        assert call_kwargs["files"]["file"][0] == "voice.ogg"


# -- Rate Limiting --


class TestRateLimiting:
    async def test_http_429_with_retry_header(self, provider):
        mock_resp = _mock_response(
            status_code=429,
            text="Rate limit exceeded",
            headers={"retry-after": "30"},
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(RateLimitError) as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retry_after == 30.0

    async def test_http_429_no_retry_header(self, provider):
        mock_resp = _mock_response(status_code=429, text="Rate limited")

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(RateLimitError) as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retry_after == 60.0  # default


# -- Error Handling --


class TestErrorHandling:
    async def test_http_500_is_retriable(self, provider):
        mock_resp = _mock_response(status_code=500, text="Server error")

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
            side_effect=httpx.TimeoutException("connection timed out"),
        ):
            with pytest.raises(AIProviderError, match="timed out") as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retriable is True

    async def test_http_error(self, provider):
        with patch.object(
            provider._client, "post", new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(AIProviderError, match="HTTP error"):
                await provider.generate_text("Test")
