"""Tests for OpenAIProvider."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.ai.base import AIProviderError, RateLimitError
from src.services.ai.providers.openai import (
    _MAGIC_EXTENSIONS,
    _SUPPORTED_UPLOAD_EXTENSIONS,
    OpenAIProvider,
    _sniff_extension,
    _upload_filename,
)

# Real container heads, not invented ones: an MP4 declares `ftyp` at offset 4
# (the first four bytes are the box size), which is exactly the offset a naive
# `startswith` check would miss. Transcription tests upload these rather than
# b"audio" so that the provider's own container sniffing sees a real format --
# with placeholder bytes it legitimately warns about an unknown container, and
# the warning assertions below would be measuring the fixture, not the code.
MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
OGG_HEAD = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"


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

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
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

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
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
            with pytest.raises(AIProviderError, match="empty content.*finish_reason=stop"):
                await provider.generate_text("Test")

    async def test_empty_content_content_filter_not_retriable(self, provider):
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}],
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError) as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retriable is False
        assert "content_filter" in str(exc_info.value)

    async def test_response_format_json_mode(self, provider):
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": '{"key": "val"}'}, "finish_reason": "stop"}],
                "usage": {},
            }
        )

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await provider.generate_text("Test", response_mime_type="application/json")

        payload = mock_post.call_args[1]["json"]
        assert payload["response_format"] == {"type": "json_object"}

    async def test_no_response_format_by_default(self, provider):
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
                "usage": {},
            }
        )

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await provider.generate_text("Test")

        payload = mock_post.call_args[1]["json"]
        assert "response_format" not in payload


# -- Embeddings --


class TestGenerateEmbedding:
    async def test_successful_embedding(self, provider):
        embedding = [0.1, 0.2, 0.3] * 512
        mock_resp = _mock_response(json_data={"data": [{"embedding": embedding, "index": 0}]})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.generate_embedding("Test text")

        assert result.embedding == embedding
        assert result.model == "text-embedding-3-small"
        assert result.provider == "openai"
        assert result.dimensions == len(embedding)

    async def test_returns_tokens_input(self, provider):
        embedding = [0.1] * 768
        mock_resp = _mock_response(
            json_data={
                "data": [{"embedding": embedding, "index": 0}],
                "usage": {"total_tokens": 42},
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.generate_embedding("Test text")

        assert result.tokens_input == 42

    async def test_empty_data(self, provider):
        mock_resp = _mock_response(json_data={"data": []})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="empty embedding data"):
                await provider.generate_embedding("Test")


# -- Vision --


class TestAnalyzeImage:
    async def test_output_text_format(self, provider):
        mock_resp = _mock_response(json_data={"output_text": "A dog playing fetch"})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.analyze_image(b"fake-image", "Describe this")

        assert result.text == "A dog playing fetch"
        assert result.provider == "openai"

    async def test_output_array_format(self, provider):
        mock_resp = _mock_response(
            json_data={
                "output": [
                    {"type": "reasoning", "summary": []},
                    {
                        "type": "message",
                        "content": [{"text": "A sunset over mountains"}],
                    },
                ]
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.analyze_image(b"fake-image", "Describe")

        assert result.text == "A sunset over mountains"

    async def test_returns_token_counts(self, provider):
        mock_resp = _mock_response(
            json_data={
                "output_text": "A dog",
                "usage": {"input_tokens": 150, "output_tokens": 20},
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.analyze_image(b"fake-image", "Describe")

        assert result.tokens_input == 150
        assert result.tokens_output == 20

    async def test_empty_vision_response(self, provider):
        mock_resp = _mock_response(json_data={})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(AIProviderError, match="empty response"):
                await provider.analyze_image(b"fake", "Describe")

    async def test_payload_format(self, provider):
        mock_resp = _mock_response(json_data={"output_text": "Result"})

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
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
            json_data={
                "text": "Hello, how are you?",
                "usage": {"type": "tokens", "input_tokens": 60, "output_tokens": 8},
            }
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.transcribe_audio(b"fake-audio-data")

        assert result.text == "Hello, how are you?"
        assert result.model == "gpt-4o-mini-transcribe"
        assert result.provider == "openai"
        assert result.tokens_input == 60
        assert result.tokens_output == 8

    async def test_default_model_requests_plain_json(self, provider):
        """gpt-4o-*-transcribe rejects verbose_json with a 400 — the request
        must ask for plain json, or every voice message fails outright."""
        mock_resp = _mock_response(json_data={"text": "test"})

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await provider.transcribe_audio(b"audio")

        data = mock_post.call_args[1]["data"]
        assert data["model"] == "gpt-4o-mini-transcribe"
        assert data["response_format"] == "json"

    async def test_whisper_keeps_verbose_json_and_duration(self, provider):
        """whisper-1 is per-minute priced: only verbose_json carries the
        duration that cost logging needs."""
        mock_resp = _mock_response(json_data={"text": "Hello", "language": "en", "duration": 3.5})

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            result = await provider.transcribe_audio(b"audio", model="whisper-1")

        data = mock_post.call_args[1]["data"]
        assert data["response_format"] == "verbose_json"
        assert result.model == "whisper-1"
        assert result.language == "en"
        assert result.duration == 3.5
        assert result.tokens_input is None

    async def test_missing_usage_leaves_tokens_none_and_warns(self, provider):
        """A token-priced model answering without usage would cost-log as $0
        forever, indistinguishable from a free model — the one place that can
        tell 'absent' from 'zero' must say so (deep-review 2026-08-19)."""
        mock_resp = _mock_response(json_data={"text": "test"})

        with (
            patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp),
            patch("src.services.ai.providers.openai.logger") as mock_logger,
        ):
            result = await provider.transcribe_audio(OGG_HEAD)

        assert result.tokens_input is None
        assert result.tokens_output is None
        assert result.duration is None
        mock_logger.warning.assert_called_once()

    async def test_whisper_without_usage_does_not_warn(self, provider):
        """False-positive control: whisper-1 never returns usage tokens — its
        cost comes from duration, so the absence is normal, not a silent zero."""
        mock_resp = _mock_response(json_data={"text": "Hello", "duration": 2.0})

        with (
            patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_resp),
            patch("src.services.ai.providers.openai.logger") as mock_logger,
        ):
            await provider.transcribe_audio(OGG_HEAD, model="whisper-1")

        mock_logger.warning.assert_not_called()

    async def test_with_language_hint(self, provider):
        mock_resp = _mock_response(json_data={"text": "Привет", "language": "ru"})

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await provider.transcribe_audio(b"audio", language="ru")

        # Check that language is in the form data
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["data"]["language"] == "ru"

    async def test_multipart_upload(self, provider):
        mock_resp = _mock_response(json_data={"text": "test"})

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await provider.transcribe_audio(b"audio-bytes", filename="voice.ogg")

        call_kwargs = mock_post.call_args[1]
        assert "files" in call_kwargs
        assert call_kwargs["files"]["file"][0] == "voice.ogg"


class TestUploadFilename:
    """The upload name decides which demuxer the endpoint uses.

    Regression guard for 2026-08-24: every caller was handed a hardcoded
    "audio.ogg", so Telegram video notes (MP4) were uploaded under an ogg
    name. whisper-1 tolerated it; gpt-4o-mini-transcribe answers 400
    ("Audio file might be corrupted or unsupported"), which is a silent loss
    -- the handler drops the transcription and the chat sees nothing at all.
    Verified against the live API: the same MP4 bytes give 400 as "audio.ogg"
    and 200 as "audio.mp4".
    """

    def test_video_note_bytes_are_named_mp4(self):
        assert _upload_filename(MP4_HEAD) == "audio.mp4"

    def test_voice_bytes_are_named_ogg(self):
        assert _upload_filename(OGG_HEAD) == "audio.ogg"

    @pytest.mark.parametrize(
        ("head", "expected"),
        [
            (b"RIFF\x24\x08\x00\x00WAVEfmt ", "audio.wav"),
            (b"fLaC\x00\x00\x00\x22", "audio.flac"),
            (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81", "audio.webm"),
            (b"ID3\x04\x00\x00\x00", "audio.mp3"),
        ],
    )
    def test_other_containers(self, head, expected):
        assert _upload_filename(head) == expected

    def test_unrecognised_bytes_fall_back_without_raising(self):
        assert _upload_filename(b"not-any-known-container") == "audio.ogg"

    def test_short_input_does_not_raise(self):
        """A truncated download must not take the slicing out of bounds."""
        assert _upload_filename(b"Og") == "audio.ogg"
        assert _upload_filename(b"") == "audio.ogg"

    def test_every_emitted_extension_is_accepted_by_the_endpoint(self):
        """A new magic entry is only useful if /audio/transcriptions takes it.

        Guessing an extension the API rejects would swap one 400 for another.
        """
        emitted = {signature.extension for signature in _MAGIC_EXTENSIONS}
        assert emitted <= _SUPPORTED_UPLOAD_EXTENSIONS

    def test_ogg_is_detected_and_not_merely_defaulted(self):
        """The ogg row must be falsifiable, and `_upload_filename` cannot be.

        Its answer for ogg ("audio.ogg") is the same string as the fallback,
        so every assertion phrased against it passes with the OggS row
        deleted -- verified by mutation on 2026-08-24: removing that row left
        all six ogg assertions green, and the only tests that went red were
        the usage-token ones, failing on an extra warning and pointing at cost
        logging rather than at detection. Ogg is >90% of production traffic
        and was the one path with no test that could fail. `_sniff_extension`
        answers None when nothing matched, which is what separates the two.
        """
        assert _sniff_extension(OGG_HEAD) == "ogg"

    def test_unknown_bytes_sniff_to_none_rather_than_a_default(self):
        assert _sniff_extension(b"not-any-known-container") is None

    def test_riff_alone_is_not_wav(self):
        """RIFF names a container family, not a format.

        RIFF/AVI and RIFF/WEBP share the leading magic. Matching on it alone
        would return a confidently wrong "wav" AND skip the fallback warning,
        which is the design's only signal that a format went unrecognised.
        """
        riff_avi = b"RIFF\x24\x08\x00\x00AVI LIST"
        assert _sniff_extension(riff_avi) is None

    def test_wav_needs_both_riff_and_wave(self):
        assert _sniff_extension(b"RIFF\x24\x08\x00\x00WAVEfmt ") == "wav"


class TestDeclaredFilename:
    """A caller's name wins, but no longer passes unexamined.

    The incident this module now guards against was exactly a filename nobody
    checked against the bytes.
    """

    def test_declared_name_disagreeing_with_the_bytes_warns(self):
        with patch("src.services.ai.providers.openai.logger") as mock_logger:
            result = _upload_filename(MP4_HEAD, declared="voice.ogg")

        assert result == "voice.ogg"
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.kwargs["sniffed"] == "mp4"
        assert mock_logger.warning.call_args.kwargs["declared"] == "voice.ogg"

    def test_declared_name_agreeing_with_the_bytes_is_silent(self):
        with patch("src.services.ai.providers.openai.logger") as mock_logger:
            result = _upload_filename(MP4_HEAD, declared="video_note.MP4")

        assert result == "video_note.MP4"
        mock_logger.warning.assert_not_called()

    def test_unrecognised_bytes_do_not_accuse_the_caller(self):
        """With nothing sniffed there is no disagreement to report -- warning
        here would blame a caller that may well be right."""
        with patch("src.services.ai.providers.openai.logger") as mock_logger:
            result = _upload_filename(b"unknown-container", declared="from-caller.m4a")

        assert result == "from-caller.m4a"
        mock_logger.warning.assert_not_called()


class TestTranscribeUploadName:
    """The name must survive all the way into the multipart request."""

    async def test_mp4_reaches_the_api_as_mp4(self, provider):
        mock_resp = _mock_response(json_data={"text": "test"})

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await provider.transcribe_audio(MP4_HEAD)

        assert mock_post.call_args[1]["files"]["file"][0] == "audio.mp4"

    async def test_ogg_reaches_the_api_as_ogg(self, provider):
        mock_resp = _mock_response(json_data={"text": "test"})

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await provider.transcribe_audio(OGG_HEAD)

        assert mock_post.call_args[1]["files"]["file"][0] == "audio.ogg"

    async def test_explicit_filename_still_wins_over_sniffing(self, provider):
        """Callers that know better keep the override -- the sniff is a
        default, not a policy."""
        mock_resp = _mock_response(json_data={"text": "test"})

        with patch.object(
            provider._client, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await provider.transcribe_audio(MP4_HEAD, filename="from-caller.m4a")

        assert mock_post.call_args[1]["files"]["file"][0] == "from-caller.m4a"


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
            provider._client,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("connection timed out"),
        ):
            with pytest.raises(AIProviderError, match="timed out") as exc_info:
                await provider.generate_text("Test")

        assert exc_info.value.retriable is True

    async def test_http_error(self, provider):
        with patch.object(
            provider._client,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(AIProviderError, match="HTTP error"):
                await provider.generate_text("Test")
