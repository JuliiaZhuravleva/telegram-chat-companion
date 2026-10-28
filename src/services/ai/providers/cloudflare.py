"""
Cloudflare Workers AI provider -- text generation only.

Workers AI serves open models (Gemma, Llama, ...) behind an OpenAI-compatible
chat completions endpoint scoped to an account, so a call needs two secrets:
the API token and the account id.

Why it exists: the blog advice classifier (slice 3b) reads every comment in a
discussion group, and Workers AI's free daily allowance (10,000 "neurons")
covers that volume at no cost, while Cloudflare does not train on Workers AI
inputs. It is not part of any default chain -- a caller reaches it only by
naming an `@cf/...` model.

Billing is in neurons, not tokens. The response carries `usage.neurons`; it is
logged at debug level because `response_log` has no column for it, and the
pricing table treats these models as free (they are, inside the allowance).
"""

from __future__ import annotations

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

_BASE_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"

DEFAULT_MODEL = "@cf/google/gemma-4-26b-a4b-it"


class CloudflareProvider(AIProvider):
    """Cloudflare Workers AI (OpenAI-compatible chat completions)."""

    name = "cloudflare"
    capabilities = {
        "text_generation": True,
        "embeddings": False,
        "vision": False,
        "transcription": False,
        "function_calling": False,
    }

    def __init__(self, api_key: str, *, account_id: str, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._base_url = _BASE_URL.format(account_id=account_id)
        # Thinking models spend most of a call reasoning; 60 s is too tight
        # for a 4096-token budget on a busy day.
        self._client = httpx.AsyncClient(
            timeout=180.0,
            headers={"Authorization": f"Bearer {self._api_key}"},
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
        """Generate text through Workers AI chat completions.

        Gemma 4 is a thinking model: with a small `max_tokens` the whole
        budget goes to `reasoning_content` and `content` comes back empty
        (measured: 20 tokens -> empty, 4096 -> an answer). That surfaces here
        as a retriable `AIProviderError`, same as OpenAI's empty content.
        """
        model = model or DEFAULT_MODEL

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if kwargs.get("response_mime_type") == "application/json":
            payload["response_format"] = {"type": "json_object"}

        response = await self._request(f"{self._base_url}/chat/completions", payload)

        choices = response.get("choices") or []
        if not choices:
            raise AIProviderError("Cloudflare returned no choices", provider=self.name)

        choice = choices[0]
        finish_reason = choice.get("finish_reason") or ""
        text = (choice.get("message") or {}).get("content") or ""
        if not text:
            raise AIProviderError(
                f"Cloudflare returned empty content (finish_reason={finish_reason})",
                provider=self.name,
                retriable=finish_reason != "content_filter",
            )

        usage = response.get("usage") or {}
        logger.debug("Cloudflare usage", model=model, neurons=usage.get("neurons"))

        return TextGenerationResult(
            text=text,
            model=model,
            provider=self.name,
            tokens_input=usage.get("prompt_tokens"),
            tokens_output=usage.get("completion_tokens"),
            finish_reason=finish_reason or None,
        )

    async def generate_embedding(
        self,
        text: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> EmbeddingResult:
        raise AIProviderError(
            "Cloudflare provider does not serve embeddings", provider=self.name, retriable=False
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> VisionResult:
        raise AIProviderError(
            "Cloudflare provider does not serve vision", provider=self.name, retriable=False
        )

    async def transcribe_audio(
        self,
        audio_data: bytes,
        language: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        raise AIProviderError(
            "Cloudflare provider does not serve transcription", provider=self.name, retriable=False
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _request(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(url, json=payload)
        except httpx.TimeoutException as e:
            raise AIProviderError(
                f"Cloudflare request timed out: {e}", provider=self.name, retriable=True
            ) from e
        except httpx.HTTPError as e:
            raise AIProviderError(
                f"Cloudflare HTTP error: {e}", provider=self.name, retriable=True
            ) from e

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            raise RateLimitError(
                "Cloudflare rate limit exceeded",
                provider=self.name,
                retry_after=float(retry_after) if retry_after else 60.0,
            )
        if resp.status_code != 200:
            raise AIProviderError(
                f"Cloudflare API error {resp.status_code}: {resp.text[:200]}",
                provider=self.name,
                retriable=resp.status_code >= 500,
            )

        result: dict[str, Any] = resp.json()
        return result
