"""
OpenAI AI provider.

Supports text generation, embeddings, vision, and transcription (Whisper).
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import structlog

from src.services.ai.base import (
    AIProvider,
    AIProviderError,
    EmbeddingResult,
    RateLimitError,
    TextGenerationResult,
    TranscriptionResult,
    VisionResult,
)

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.openai.com/v1"


# -- Upload filename ---------------------------------------------------
#
# The multipart filename is not cosmetic: /audio/transcriptions picks its
# demuxer from the extension, and a mismatch is a hard 400 ("Audio file might
# be corrupted or unsupported"). Every caller used to receive the same
# hardcoded "audio.ogg", which is a lie for Telegram video notes -- those are
# MP4. whisper-1 sniffed the container and transcribed them regardless, so the
# lie was free until 2026-08-19, when the switch to gpt-4o-mini-transcribe
# (which trusts the extension) silently killed video-note transcription in
# every chat while voice -- genuinely ogg -- kept working. Measured against the
# live API: identical MP4 bytes give 400 as "audio.ogg" and 200 as "audio.mp4".
#
# Derive the extension from the bytes rather than trusting a caller's label:
# the sender of the audio is the one thing that cannot be wrong about it, and
# a future caller transcribing anything but voice inherits the fix for free.
_MAGIC_EXTENSIONS: tuple[tuple[int, bytes, str], ...] = (
    (0, b"OggS", "ogg"),
    (4, b"ftyp", "mp4"),  # ISO-BMFF: mp4/m4a, what Telegram video notes are
    (0, b"RIFF", "wav"),
    (0, b"fLaC", "flac"),
    (0, b"\x1a\x45\xdf\xa3", "webm"),
    (0, b"ID3", "mp3"),
)

# Unrecognised bytes keep the historical name: it is no worse than what every
# caller got before, and the log line below is what makes the next such format
# visible instead of surfacing as an unexplained 400.
_FALLBACK_EXTENSION = "ogg"

# All extensions above are in the endpoint's supported set (flac, m4a, mp3,
# mp4, mpeg, mpga, oga, ogg, wav, webm).
_SUPPORTED_UPLOAD_EXTENSIONS = frozenset(
    {"flac", "m4a", "mp3", "mp4", "mpeg", "mpga", "oga", "ogg", "wav", "webm"}
)


def _upload_filename(audio_data: bytes) -> str:
    """Name the upload after the container the bytes actually are."""
    for offset, magic, extension in _MAGIC_EXTENSIONS:
        if audio_data[offset : offset + len(magic)] == magic:
            return f"audio.{extension}"

    logger.warning(
        "Unrecognised audio container; falling back to a default upload name",
        head=audio_data[:12].hex(),
        size=len(audio_data),
        fallback=_FALLBACK_EXTENSION,
    )
    return f"audio.{_FALLBACK_EXTENSION}"


class OpenAIProvider(AIProvider):
    """OpenAI API provider (GPT, Whisper, embeddings, vision)."""

    name = "openai"
    capabilities = {
        "text_generation": True,
        "embeddings": True,
        "vision": True,
        "transcription": True,
        "function_calling": True,
    }

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {self._api_key}",
            },
        )

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        max_tokens: int = 500,
        temperature: float = 0.9,
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Generate text using OpenAI Chat Completions API."""
        model = model or "gpt-5-nano"

        # Some models only support temperature=1.0:
        # - gpt-5-nano (cheapest tier)
        # - reasoning models (o1*, o3*, o4* — e.g. o4-mini)
        is_reasoning = model.startswith(("o1", "o3", "o4"))
        if (model == "gpt-5-nano" or is_reasoning) and temperature != 1.0:
            logger.warning(
                "Model only supports temperature=1.0, adjusting",
                model=model,
                requested_temperature=temperature,
            )
            temperature = 1.0

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
        }

        # Enable JSON mode when requested
        if kwargs.get("response_mime_type") == "application/json":
            payload["response_format"] = {"type": "json_object"}

        response = await self._request(f"{_BASE_URL}/chat/completions", payload)

        choices = response.get("choices", [])
        if not choices:
            raise AIProviderError(
                "OpenAI returned no choices",
                provider=self.name,
            )

        choice = choices[0]
        finish_reason = choice.get("finish_reason", "")
        text = choice.get("message", {}).get("content", "")
        if not text:
            raise AIProviderError(
                f"OpenAI returned empty content (finish_reason={finish_reason})",
                provider=self.name,
                retriable=finish_reason != "content_filter",
            )

        usage = response.get("usage", {})

        return TextGenerationResult(
            text=text,
            model=model,
            provider=self.name,
            tokens_input=usage.get("prompt_tokens"),
            tokens_output=usage.get("completion_tokens"),
            finish_reason=choice.get("finish_reason"),
        )

    async def generate_embedding(
        self,
        text: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> EmbeddingResult:
        """Generate embedding using OpenAI Embeddings API."""
        model = model or "text-embedding-3-small"

        payload: dict[str, Any] = {
            "model": model,
            "input": text,
        }

        response = await self._request(f"{_BASE_URL}/embeddings", payload)

        data = response.get("data", [])
        if not data:
            raise AIProviderError(
                "OpenAI returned empty embedding data",
                provider=self.name,
            )

        embedding = data[0].get("embedding", [])
        if not embedding:
            raise AIProviderError(
                "OpenAI returned empty embedding vector",
                provider=self.name,
            )

        usage = response.get("usage", {})

        return EmbeddingResult(
            embedding=embedding,
            model=model,
            provider=self.name,
            dimensions=len(embedding),
            tokens_input=usage.get("total_tokens"),
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> VisionResult:
        """Analyze image using OpenAI Responses API."""
        model = model or "gpt-5-nano"
        mime_type = kwargs.get("mime_type", "image/jpeg")

        b64_data = base64.b64encode(image_data).decode("utf-8")
        data_uri = f"data:{mime_type};base64,{b64_data}"

        payload: dict[str, Any] = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": data_uri},
                        {"type": "input_text", "text": prompt},
                    ],
                }
            ],
        }

        response = await self._request(f"{_BASE_URL}/responses", payload)

        text = self._extract_vision_text(response)
        logger.info(
            "OpenAI vision extraction result",
            extracted_text=text[:100] if text else None,
            has_output_text="output_text" in response,
            output_types=[item.get("type") for item in response.get("output", [])],
        )
        if not text:
            logger.error(
                "OpenAI vision empty extraction",
                response_keys=list(response.keys()),
                output=str(response.get("output", []))[:500],
            )
            raise AIProviderError(
                "OpenAI vision returned empty response",
                provider=self.name,
            )

        usage = response.get("usage", {})
        return VisionResult(
            text=text,
            model=model,
            provider=self.name,
            tokens_input=usage.get("input_tokens"),
            tokens_output=usage.get("output_tokens"),
        )

    async def transcribe_audio(
        self,
        audio_data: bytes,
        language: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Transcribe audio using the OpenAI transcription API."""
        model = model or "gpt-4o-mini-transcribe"
        filename = kwargs.get("filename") or _upload_filename(audio_data)

        # Multipart form upload — use separate headers without JSON content-type
        headers = {"Authorization": f"Bearer {self._api_key}"}

        # gpt-4o-*-transcribe models reject verbose_json (400) — they support
        # only json/text and report cost via a `usage` token object instead of
        # `duration`. whisper-1 is the reverse: per-minute pricing, and only
        # verbose_json carries the duration that pricing needs.
        response_format = "verbose_json" if model.startswith("whisper") else "json"

        files = {"file": (filename, audio_data)}
        data: dict[str, str] = {"model": model, "response_format": response_format}
        if language:
            data["language"] = language

        try:
            resp = await self._client.post(
                f"{_BASE_URL}/audio/transcriptions",
                files=files,
                data=data,
                headers=headers,
            )
        except httpx.TimeoutException as e:
            raise AIProviderError(
                f"OpenAI transcription request timed out: {e}",
                provider=self.name,
                retriable=True,
            ) from e
        except httpx.HTTPError as e:
            raise AIProviderError(
                f"OpenAI transcription HTTP error: {e}",
                provider=self.name,
                retriable=True,
            ) from e

        self._check_response(resp)

        result: dict[str, Any] = resp.json()
        text = result.get("text", "")
        usage = result.get("usage") or {}
        tokens_input = usage.get("input_tokens")
        tokens_output = usage.get("output_tokens")

        # A token-priced model answering without usage numbers would cost-log
        # as $0 — indistinguishable from a genuinely free model, forever, with
        # nothing else in the chain noticing (calculate_cost skips falsy
        # tokens, _log_usage happily writes the zero). Say it here, at the
        # only place that knows the numbers were absent rather than zero.
        if not model.startswith("whisper") and tokens_input is None:
            logger.warning(
                "Transcription response carried no usage tokens; cost will be logged as zero",
                model=model,
                response_keys=list(result.keys()),
            )

        return TranscriptionResult(
            text=text,
            model=model,
            provider=self.name,
            language=result.get("language") or language,
            duration=result.get("duration"),
            tokens_input=tokens_input,
            tokens_output=tokens_output,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    # -- Private helpers --

    async def _request(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Make a POST request to OpenAI API with error handling."""
        try:
            resp = await self._client.post(url, json=payload)
        except httpx.TimeoutException as e:
            raise AIProviderError(
                f"OpenAI request timed out: {e}",
                provider=self.name,
                retriable=True,
            ) from e
        except httpx.HTTPError as e:
            raise AIProviderError(
                f"OpenAI HTTP error: {e}",
                provider=self.name,
                retriable=True,
            ) from e

        self._check_response(resp)

        result: dict[str, Any] = resp.json()
        return result

    def _check_response(self, resp: httpx.Response) -> None:
        """Check HTTP response for errors."""
        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            retry_seconds = float(retry_after) if retry_after else 60.0
            raise RateLimitError(
                "OpenAI rate limit exceeded",
                provider=self.name,
                retry_after=retry_seconds,
            )

        if resp.status_code != 200:
            body = resp.text[:200]
            raise AIProviderError(
                f"OpenAI API error {resp.status_code}: {body}",
                provider=self.name,
                retriable=resp.status_code >= 500,
            )

    @staticmethod
    def _extract_vision_text(response: dict[str, Any]) -> str:
        """Extract text from OpenAI Responses API vision result.

        Note: response["text"] is a format *config* (e.g. {"format": ...}),
        NOT generated text. The actual text lives in output_text or
        output[type=message].content[].text.
        """
        # 1. Top-level "output_text" (SDK / convenience field)
        if response.get("output_text"):
            return str(response["output_text"])

        # 2. Find the "message" item in output array (skip "reasoning" items)
        output = response.get("output", [])
        for item in output:
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                text = block.get("text", "")
                if text:
                    return str(text)

        return ""
