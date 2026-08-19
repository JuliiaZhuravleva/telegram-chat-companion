"""
Abstract base class for AI providers.

All AI providers must implement this interface to be used by the bot.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class TextGenerationResult:
    """Result from text generation."""

    text: str
    model: str
    provider: str
    tokens_input: int | None = None
    tokens_output: int | None = None
    finish_reason: str | None = None


@dataclass
class EmbeddingResult:
    """Result from embedding generation."""

    embedding: list[float]
    model: str
    provider: str
    dimensions: int
    tokens_input: int | None = None


@dataclass
class VisionResult:
    """Result from image analysis."""

    text: str
    model: str
    provider: str
    tokens_input: int | None = None
    tokens_output: int | None = None


@dataclass
class TranscriptionResult:
    """Result from audio transcription.

    Cost accounting differs by model family: whisper-1 is priced per minute
    (`duration`, from verbose_json), gpt-4o-*-transcribe per token
    (`tokens_input`/`tokens_output`, from the response `usage` object).
    Whichever fields the model does not report stay None.
    """

    text: str
    model: str
    provider: str
    language: str | None = None
    duration: float | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None


class AIProviderError(Exception):
    """Base exception for AI provider errors."""

    def __init__(self, message: str, provider: str, retriable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retriable = retriable


class RateLimitError(AIProviderError):
    """Rate limit exceeded."""

    def __init__(self, message: str, provider: str, retry_after: float | None = None) -> None:
        super().__init__(message, provider, retriable=True)
        self.retry_after = retry_after


class AIProvider(ABC):
    """
    Abstract base class for all AI providers.

    Implement this interface to add support for a new AI provider.
    Not all methods need to be implemented - providers can declare
    which capabilities they support.
    """

    # Provider identifier (e.g., "openai", "gemini")
    name: str = "base"

    # Capabilities this provider supports
    capabilities: dict[str, bool] = {
        "text_generation": False,
        "embeddings": False,
        "vision": False,
        "transcription": False,
        "function_calling": False,
    }

    def __init__(self, api_key: str, **kwargs: Any) -> None:  # noqa: ARG002
        """
        Initialize the provider.

        Args:
            api_key: API key for the provider
            **_kwargs: Additional provider-specific configuration
        """
        self._api_key = api_key

    def supports(self, capability: str) -> bool:
        """Check if this provider supports a capability."""
        return self.capabilities.get(capability, False)

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        max_tokens: int = 500,
        temperature: float = 0.9,
        **kwargs: Any,
    ) -> TextGenerationResult:
        """
        Generate text from a prompt.

        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt
            model: Model to use (provider-specific)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional provider-specific parameters

        Returns:
            TextGenerationResult with the generated text

        Raises:
            AIProviderError: If generation fails
            RateLimitError: If rate limit is exceeded
        """
        ...

    @abstractmethod
    async def generate_embedding(
        self,
        text: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> EmbeddingResult:
        """
        Generate embedding vector for text.

        Args:
            text: Text to embed
            model: Embedding model to use
            **kwargs: Additional provider-specific parameters

        Returns:
            EmbeddingResult with the embedding vector

        Raises:
            AIProviderError: If embedding generation fails
        """
        ...

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> VisionResult:
        """
        Analyze an image with a prompt.

        Args:
            image_data: Raw image bytes
            prompt: Question/prompt about the image
            model: Vision model to use
            **kwargs: Additional provider-specific parameters

        Returns:
            VisionResult with the analysis

        Raises:
            NotImplementedError: If provider doesn't support vision
            AIProviderError: If analysis fails
        """
        raise NotImplementedError(f"{self.name} does not support vision")

    async def transcribe_audio(
        self,
        audio_data: bytes,
        language: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        """
        Transcribe audio to text.

        Args:
            audio_data: Raw audio bytes
            language: Expected language (ISO code)
            model: Transcription model to use
            **kwargs: Additional provider-specific parameters

        Returns:
            TranscriptionResult with the transcription

        Raises:
            NotImplementedError: If provider doesn't support transcription
            AIProviderError: If transcription fails
        """
        raise NotImplementedError(f"{self.name} does not support transcription")

    async def close(self) -> None:  # noqa: B027
        """Clean up provider resources. Override in subclasses if needed."""
