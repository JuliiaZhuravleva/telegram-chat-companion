"""
Google Gemini AI provider.

Supports text generation, embeddings (free tier), and vision.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx
import structlog

from src.services.ai.base import (
    AIProvider,
    AIProviderError,
    EmbeddingResult,
    RateLimitError,
    TextGenerationResult,
    VisionResult,
)

logger = structlog.get_logger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Russian verbal error prefixes — Gemini sometimes returns these instead of real content
_VERBAL_ERROR_PREFIXES = ("Не удалось", "Не получилось", "Невозможно", "Не могу")

# Rate limit detection patterns
_RATE_LIMIT_PATTERNS = [
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"exceeded.*quota", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"please retry in", re.IGNORECASE),
    re.compile(r"resource.?exhausted", re.IGNORECASE),
    re.compile(r"status code 429", re.IGNORECASE),
]

# Pattern to extract retry delay from Gemini error messages
_RETRY_DELAY_PATTERN = re.compile(r"retry in (\d+\.?\d*)s", re.IGNORECASE)


class GeminiProvider(AIProvider):
    """Google Gemini API provider."""

    name = "gemini"
    capabilities = {
        "text_generation": True,
        "embeddings": True,
        "vision": True,
        "transcription": False,
        "function_calling": True,
    }

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._client = httpx.AsyncClient(timeout=60.0)

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        max_tokens: int = 500,
        temperature: float = 0.9,
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Generate text using Gemini API."""
        model = model or "gemini-3-flash-preview"

        # Gemini combines system + user prompt into single user message
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        url = f"{_BASE_URL}/models/{model}:generateContent"
        response = await self._request(url, payload)

        text = self._extract_text(response)
        if self._is_verbal_error(text):
            raise AIProviderError(
                f"Gemini returned verbal error: {text[:50]}",
                provider=self.name,
                retriable=True,
            )

        return TextGenerationResult(
            text=text,
            model=model,
            provider=self.name,
            tokens_input=self._get_token_count(response, "promptTokenCount"),
            tokens_output=self._get_token_count(response, "candidatesTokenCount"),
            finish_reason=self._get_finish_reason(response),
        )

    async def generate_embedding(
        self,
        text: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> EmbeddingResult:
        """Generate embedding using Gemini API (free tier)."""
        model = model or "gemini-embedding-001"
        dimensions = kwargs.get("dimensions", 768)

        payload: dict[str, Any] = {
            "content": {"parts": [{"text": text}]},
            "outputDimensionality": dimensions,
        }

        url = f"{_BASE_URL}/models/{model}:embedContent"
        response = await self._request(url, payload)

        values = response.get("embedding", {}).get("values", [])
        if not values:
            raise AIProviderError(
                "Gemini returned empty embedding",
                provider=self.name,
            )

        return EmbeddingResult(
            embedding=values,
            model=model,
            provider=self.name,
            dimensions=len(values),
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> VisionResult:
        """Analyze image using Gemini vision."""
        model = model or "gemini-3-flash-preview"
        mime_type = kwargs.get("mime_type", "image/jpeg")

        b64_data = base64.b64encode(image_data).decode("utf-8")

        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                        {"text": prompt},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 500,
            },
        }

        url = f"{_BASE_URL}/models/{model}:generateContent"
        response = await self._request(url, payload)

        text = self._extract_text(response)

        return VisionResult(
            text=text,
            model=model,
            provider=self.name,
            tokens_input=self._get_token_count(response, "promptTokenCount"),
            tokens_output=self._get_token_count(response, "candidatesTokenCount"),
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    # -- Private helpers --

    async def _request(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Make a POST request to the Gemini API with error handling."""
        headers = {"x-goog-api-key": self._api_key}
        try:
            resp = await self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise AIProviderError(
                f"Gemini request timed out: {e}",
                provider=self.name,
                retriable=True,
            ) from e
        except httpx.HTTPError as e:
            raise AIProviderError(
                f"Gemini HTTP error: {e}",
                provider=self.name,
                retriable=True,
            ) from e

        body_text = resp.text

        # Check rate limit on HTTP 429 or body patterns
        if resp.status_code == 429 or self._is_rate_limited(body_text):
            retry_after = self._parse_retry_after(body_text)
            raise RateLimitError(
                f"Gemini rate limit exceeded (retry in {retry_after}s)",
                provider=self.name,
                retry_after=retry_after,
            )

        if resp.status_code != 200:
            logger.error("Gemini API error response", status=resp.status_code, body=body_text[:500])
            raise AIProviderError(
                f"Gemini API error (HTTP {resp.status_code})",
                provider=self.name,
                retriable=resp.status_code >= 500,
            )

        data: dict[str, Any] = resp.json()

        # Check for API-level errors in response body
        if "error" in data:
            error_msg = data["error"].get("message", "Unknown error")
            raise AIProviderError(
                f"Gemini API error: {error_msg}",
                provider=self.name,
            )

        return data

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        """Extract generated text from Gemini response."""
        try:
            candidates = response.get("candidates", [])
            if not candidates:
                raise AIProviderError(
                    "Gemini returned no candidates",
                    provider="gemini",
                )
            return str(candidates[0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError) as e:
            raise AIProviderError(
                f"Failed to parse Gemini response: {e}",
                provider="gemini",
            ) from e

    @staticmethod
    def _is_verbal_error(text: str) -> bool:
        """Detect Gemini verbal errors (Russian error prefixes with short text)."""
        if not text:
            return True
        for prefix in _VERBAL_ERROR_PREFIXES:
            if text.startswith(prefix) and (len(text) < 50 or len(text.split()) < 8):
                return True
        return False

    @staticmethod
    def _is_rate_limited(body_text: str) -> bool:
        """Check if response body contains rate limit indicators."""
        return any(pattern.search(body_text) for pattern in _RATE_LIMIT_PATTERNS)

    @staticmethod
    def _parse_retry_after(body_text: str) -> float:
        """Extract retry delay from response text, default to 65s."""
        match = _RETRY_DELAY_PATTERN.search(body_text)
        if match:
            return float(match.group(1)) + 5.0  # Add 5s buffer
        return 65.0

    @staticmethod
    def _get_token_count(response: dict[str, Any], key: str) -> int | None:
        """Extract token count from usage metadata."""
        metadata = response.get("usageMetadata", {})
        value = metadata.get(key)
        return int(value) if value is not None else None

    @staticmethod
    def _get_finish_reason(response: dict[str, Any]) -> str | None:
        """Extract finish reason from first candidate."""
        candidates = response.get("candidates", [])
        if candidates:
            return str(candidates[0].get("finishReason"))
        return None
