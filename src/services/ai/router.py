"""
AI Router - Smart provider selection with fallback.

Routes AI requests to the appropriate provider based on:
1. Task type (text, embeddings, vision, transcription)
2. User configuration
3. Provider availability
4. Automatic fallback on errors
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from src.config import Settings
from src.database.repositories.response_log import ResponseLogRepository
from src.services.ai.base import (
    AIProvider,
    AIProviderError,
    EmbeddingResult,
    RateLimitError,
    TextGenerationResult,
    TranscriptionResult,
    VisionResult,
)
from src.services.ai.capabilities import get_providers_for_capability, provider_supports
from src.services.ai.pricing import calculate_cost

logger = structlog.get_logger()


class AIRouter:
    """
    Routes AI requests to appropriate providers with automatic fallback.

    Usage:
        router = AIRouter(settings)
        result = await router.generate_text("Hello, world!")
    """

    def __init__(
        self,
        settings: Settings,
        response_log_repo: ResponseLogRepository | None = None,
    ) -> None:
        self._settings = settings
        self._providers: dict[str, AIProvider] = {}
        self._response_log = response_log_repo
        self._initialize_providers()

    def _initialize_providers(self) -> None:
        """Initialize available providers based on configured API keys."""
        if self._settings.openai_api_key:
            logger.info("OpenAI provider available")

        if self._settings.gemini_api_key:
            logger.info("Gemini provider available")

        if self._settings.grok_api_key:
            logger.info("Grok provider available")

        if self._settings.deepseek_api_key:
            logger.info("DeepSeek provider available")

    async def _get_provider(self, provider_name: str) -> AIProvider:
        """Get or create a provider instance."""
        if provider_name in self._providers:
            return self._providers[provider_name]

        # Lazy initialization of providers
        if provider_name == "openai" and self._settings.openai_api_key:
            from src.services.ai.providers.openai import OpenAIProvider

            self._providers[provider_name] = OpenAIProvider(self._settings.openai_api_key)
        elif provider_name == "gemini" and self._settings.gemini_api_key:
            from src.services.ai.providers.gemini import GeminiProvider

            self._providers[provider_name] = GeminiProvider(self._settings.gemini_api_key)
        else:
            raise AIProviderError(
                f"Provider {provider_name} not available or not configured",
                provider=provider_name,
            )

        return self._providers[provider_name]

    def _get_provider_chain(self, task: str) -> list[str]:
        """Get the provider chain for a task (primary + fallbacks)."""
        task_config = self._settings.ai.tasks.get(task)

        if task_config:
            chain = [task_config.provider] + task_config.fallback
        else:
            # Default chain based on capability
            chain = get_providers_for_capability(task)
            if not chain:
                chain = [self._settings.ai.default_provider]

        # Filter to only available providers
        available = []
        for provider in chain:
            if self._is_provider_available(provider):
                available.append(provider)

        return available

    def _is_provider_available(self, provider_name: str) -> bool:
        """Check if a provider is configured and available."""
        api_keys = {
            "openai": self._settings.openai_api_key,
            "gemini": self._settings.gemini_api_key,
            "grok": self._settings.grok_api_key,
            "deepseek": self._settings.deepseek_api_key,
        }
        return bool(api_keys.get(provider_name))

    async def log_usage(
        self,
        *,
        task_type: str,
        provider: str,
        model: str,
        chat_id: int = 0,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Log AI usage with cost calculation. Non-critical.

        Public so that callers of generate_text() outside the text pipeline
        (e.g. summary, sticker merge) can record their own costs.
        """
        if self._response_log is None:
            return
        try:
            cost = calculate_cost(
                model,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                duration_minutes=duration_seconds / 60.0 if duration_seconds else None,
            )
            await self._response_log.log(
                chat_id,
                provider=provider,
                model=model,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                task_type=task_type,
                cost_usd=cost,
                duration_seconds=duration_seconds,
            )
        except Exception:
            logger.warning("Failed to log AI usage", task_type=task_type, model=model)

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: str | int | float,
    ) -> TextGenerationResult:
        """Generate text using the configured provider with fallback."""
        task_config = self._settings.ai.tasks.get("text_generation")
        chain = self._get_provider_chain("text_generation")

        if not chain:
            raise AIProviderError(
                "No providers available for text generation",
                provider="none",
            )

        # Extract caller overrides once, before the retry loop
        caller_model: str | None = str(kwargs.pop("model")) if "model" in kwargs else None
        caller_max_tokens: int | None = int(kwargs.pop("max_tokens")) if "max_tokens" in kwargs else None
        caller_temperature: float | None = float(kwargs.pop("temperature")) if "temperature" in kwargs else None

        last_error: Exception | None = None

        for i, provider_name in enumerate(chain):
            try:
                provider = await self._get_provider(provider_name)
                # Caller override > config > default (None-aware to preserve 0/0.0)
                # Only use config model for the primary provider; fallbacks use their defaults
                # (a Gemini model name won't work on OpenAI and vice versa)
                if caller_model is not None:
                    model: str | None = caller_model
                elif i == 0 and task_config:
                    model = task_config.model
                else:
                    model = None
                max_tokens: int = caller_max_tokens if caller_max_tokens is not None else (
                    task_config.max_tokens if task_config else 500
                )
                temperature: float = caller_temperature if caller_temperature is not None else (
                    task_config.temperature if task_config else 0.9
                )

                logger.debug(
                    "Attempting text generation",
                    provider=provider_name,
                    model=model,
                )

                return await provider.generate_text(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )

            except RateLimitError as e:
                logger.warning(
                    "Rate limit hit, trying fallback",
                    provider=provider_name,
                    retry_after=e.retry_after,
                )
                last_error = e
                continue

            except AIProviderError as e:
                logger.error(
                    "Provider error, trying fallback",
                    provider=provider_name,
                    error=str(e),
                )
                last_error = e
                if not e.retriable:
                    continue

        raise AIProviderError(
            f"All providers failed for text generation. Last error: {last_error}",
            provider="router",
        )

    async def generate_embedding(
        self,
        text: str,
        **kwargs: str | int,
    ) -> EmbeddingResult:
        """Generate embedding using the configured provider with fallback."""
        task_config = self._settings.ai.tasks.get("embeddings")
        chain = self._get_provider_chain("embeddings")

        if not chain:
            raise AIProviderError(
                "No providers available for embeddings",
                provider="none",
            )

        last_error: Exception | None = None

        for provider_name in chain:
            if not provider_supports(provider_name, "embeddings"):
                continue

            try:
                provider = await self._get_provider(provider_name)
                model = task_config.model if task_config else None

                result = await provider.generate_embedding(
                    text=text,
                    model=model,
                    **kwargs,
                )
                asyncio.ensure_future(self.log_usage(
                    task_type="embedding",
                    provider=result.provider,
                    model=result.model,
                    tokens_input=result.tokens_input,
                ))
                return result

            except AIProviderError as e:
                logger.error(
                    "Embedding generation failed, trying fallback",
                    provider=provider_name,
                    error=str(e),
                )
                last_error = e
                continue

        raise AIProviderError(
            f"All providers failed for embeddings. Last error: {last_error}",
            provider="router",
        )

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        **kwargs: Any,
    ) -> VisionResult:
        """Analyze image using the configured provider with fallback."""
        chain = self._get_provider_chain("vision")

        if not chain:
            raise AIProviderError(
                "No providers available for vision",
                provider="none",
            )

        last_error: Exception | None = None

        for provider_name in chain:
            if not provider_supports(provider_name, "vision"):
                continue

            try:
                provider = await self._get_provider(provider_name)
                result = await provider.analyze_image(
                    image_data=image_data,
                    prompt=prompt,
                    **kwargs,
                )
                asyncio.ensure_future(self.log_usage(
                    task_type="vision",
                    provider=result.provider,
                    model=result.model,
                    tokens_input=result.tokens_input,
                    tokens_output=result.tokens_output,
                ))
                return result

            except AIProviderError as e:
                logger.error(
                    "Vision analysis failed, trying fallback",
                    provider=provider_name,
                    error=str(e),
                )
                last_error = e
                continue

        raise AIProviderError(
            f"All providers failed for vision. Last error: {last_error}",
            provider="router",
        )

    async def transcribe_audio(
        self,
        audio_data: bytes,
        language: str | None = None,
        **kwargs: str,
    ) -> TranscriptionResult:
        """Transcribe audio using the configured provider."""
        chain = self._get_provider_chain("transcription")

        if not chain:
            raise AIProviderError(
                "No providers available for transcription",
                provider="none",
            )

        last_error: Exception | None = None

        for provider_name in chain:
            if not provider_supports(provider_name, "transcription"):
                continue

            try:
                provider = await self._get_provider(provider_name)
                result = await provider.transcribe_audio(
                    audio_data=audio_data,
                    language=language,
                    **kwargs,
                )
                asyncio.ensure_future(self.log_usage(
                    task_type="transcription",
                    provider=result.provider,
                    model=result.model,
                    duration_seconds=result.duration,
                ))
                return result

            except AIProviderError as e:
                logger.error(
                    "Transcription failed, trying fallback",
                    provider=provider_name,
                    error=str(e),
                )
                last_error = e
                continue

        raise AIProviderError(
            f"All providers failed for transcription. Last error: {last_error}",
            provider="router",
        )

    async def close(self) -> None:
        """Close all provider connections."""
        for provider in self._providers.values():
            await provider.close()
        self._providers.clear()
