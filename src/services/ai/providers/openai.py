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

        response = await self._request(f"{_BASE_URL}/chat/completions", payload)

        choices = response.get("choices", [])
        if not choices:
            raise AIProviderError(
                "OpenAI returned no choices",
                provider=self.name,
            )

        choice = choices[0]
        text = choice.get("message", {}).get("content", "")
        if not text:
            raise AIProviderError(
                "OpenAI returned empty content",
                provider=self.name,
                retriable=True,
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

        return EmbeddingResult(
            embedding=embedding,
            model=model,
            provider=self.name,
            dimensions=len(embedding),
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

        return VisionResult(text=text, model=model, provider=self.name)

    async def transcribe_audio(
        self,
        audio_data: bytes,
        language: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """Transcribe audio using OpenAI Whisper API."""
        model = model or "whisper-1"
        filename = kwargs.get("filename", "audio.ogg")

        # Multipart form upload — use separate headers without JSON content-type
        headers = {"Authorization": f"Bearer {self._api_key}"}

        files = {"file": (filename, audio_data)}
        data: dict[str, str] = {"model": model}
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
                f"OpenAI Whisper request timed out: {e}",
                provider=self.name,
                retriable=True,
            ) from e
        except httpx.HTTPError as e:
            raise AIProviderError(
                f"OpenAI Whisper HTTP error: {e}",
                provider=self.name,
                retriable=True,
            ) from e

        self._check_response(resp)

        result: dict[str, Any] = resp.json()
        text = result.get("text", "")

        return TranscriptionResult(
            text=text,
            model=model,
            provider=self.name,
            language=result.get("language") or language,
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
