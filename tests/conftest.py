"""
Root test configuration.

IMPORTANT: os.environ lines MUST remain at the top of this file,
before any imports from src/. The Settings() singleton in
src/config.py is created at import time and requires these variables.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test_db")

from typing import Any  # noqa: I001 — imports MUST be after os.environ above
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.models.chat_config import ChatConfig
from src.services.ai.base import (
    AIProvider,
    EmbeddingResult,
    TextGenerationResult,
    TranscriptionResult,
    VisionResult,
)


# ---------------------------------------------------------------------------
# Settings fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def make_settings(monkeypatch):
    """
    Factory fixture: build a Settings instance with custom overrides.

    Env-backed fields (telegram_bot_token, database_url, *_api_key) are set
    via environment variables because Settings uses aliases (TELEGRAM_BOT_TOKEN, etc.)
    and pydantic-settings reads them from env, not from init kwargs by python name.

    Usage:
        def test_something(make_settings):
            s = make_settings(openai_api_key="sk-test")
    """

    _ENV_FIELDS = {
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "database_url": "DATABASE_URL",
        "openai_api_key": "OPENAI_API_KEY",
        "gemini_api_key": "GEMINI_API_KEY",
        "grok_api_key": "GROK_API_KEY",
        "deepseek_api_key": "DEEPSEEK_API_KEY",
    }

    def _factory(**overrides: Any) -> Settings:
        defaults: dict[str, Any] = {
            "telegram_bot_token": "test-token",
            "database_url": "postgresql://test:test@localhost/test",
        }
        defaults.update(overrides)

        # Set or clear ALL env-backed fields to prevent .env leakage
        init_kwargs: dict[str, Any] = {}
        for key, env_name in _ENV_FIELDS.items():
            value = defaults.pop(key, None)
            if value is not None:
                monkeypatch.setenv(env_name, str(value))
            else:
                monkeypatch.delenv(env_name, raising=False)

        init_kwargs.update(defaults)
        return Settings(_env_file=None, **init_kwargs)

    return _factory


# ---------------------------------------------------------------------------
# ChatConfig fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def make_chat_config():
    """
    Factory fixture: build a ChatConfig with custom overrides.

    Usage:
        cfg = make_chat_config(chat_id=123, enabled=True)
    """

    def _factory(chat_id: int = -1001234567890, **overrides: Any) -> ChatConfig:
        defaults: dict[str, Any] = {"chat_id": chat_id, "enabled": True}
        defaults.update(overrides)
        if "trigger_words" in defaults and isinstance(defaults["trigger_words"], list):
            defaults["trigger_words"] = tuple(defaults["trigger_words"])
        return ChatConfig(**defaults)

    return _factory


# ---------------------------------------------------------------------------
# Mock AI Provider
# ---------------------------------------------------------------------------


class MockAIProvider(AIProvider):
    """
    Concrete AIProvider for testing.

    Configure which capabilities it supports and what results it returns.
    Can be set to raise exceptions to test fallback behavior.
    """

    name: str = "mock"

    def __init__(
        self,
        name: str = "mock",
        api_key: str = "test-key",
        supported_capabilities: dict[str, bool] | None = None,
        text_result: TextGenerationResult | None = None,
        embedding_result: EmbeddingResult | None = None,
        vision_result: VisionResult | None = None,
        transcription_result: TranscriptionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__(api_key=api_key)
        self.name = name
        self.capabilities = supported_capabilities or {
            "text_generation": True,
            "embeddings": True,
            "vision": False,
            "transcription": False,
            "function_calling": False,
        }
        self._text_result = text_result or TextGenerationResult(
            text="mock response",
            model="mock-model",
            provider=name,
            tokens_input=10,
            tokens_output=5,
        )
        self._embedding_result = embedding_result or EmbeddingResult(
            embedding=[0.1] * 768,
            model="mock-embed",
            provider=name,
            dimensions=768,
        )
        self._vision_result = vision_result
        self._transcription_result = transcription_result
        self._error = error
        self.call_log: list[str] = []
        self.last_text_call: dict[str, Any] = {}
        self.last_transcription_call: dict[str, Any] = {}

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        max_tokens: int = 500,
        temperature: float = 0.9,
        **kwargs: Any,
    ) -> TextGenerationResult:
        self.call_log.append("generate_text")
        self.last_text_call = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **kwargs,
        }
        if self._error:
            raise self._error
        return self._text_result

    async def generate_embedding(
        self,
        text: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> EmbeddingResult:
        self.call_log.append("generate_embedding")
        if self._error:
            raise self._error
        return self._embedding_result

    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> VisionResult:
        self.call_log.append("analyze_image")
        if self._error:
            raise self._error
        if self._vision_result:
            return self._vision_result
        raise NotImplementedError("mock does not support vision")

    async def transcribe_audio(
        self,
        audio_data: bytes,
        language: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        self.call_log.append("transcribe_audio")
        self.last_transcription_call = {
            "language": language,
            "model": model,
            **kwargs,
        }
        if self._error:
            raise self._error
        if self._transcription_result:
            return self._transcription_result
        raise NotImplementedError("mock does not support transcription")

    async def close(self) -> None:
        self.call_log.append("close")


@pytest.fixture
def mock_provider():
    """Factory fixture for creating MockAIProvider instances."""

    def _factory(**kwargs: Any) -> MockAIProvider:
        return MockAIProvider(**kwargs)

    return _factory


# ---------------------------------------------------------------------------
# Aiogram Message mock
# ---------------------------------------------------------------------------


@pytest.fixture
def make_message():
    """
    Factory fixture: build a mock aiogram Message.

    Usage:
        msg = make_message(text="hello bot", chat_id=-100123)
    """

    def _factory(
        text: str | None = "hello",
        caption: str | None = None,
        chat_id: int = -1001234567890,
        user_id: int = 12345,
        message_id: int = 1,
        reply_to_message: Any = None,
    ) -> MagicMock:
        message = MagicMock()
        message.text = text
        message.caption = caption
        message.message_id = message_id

        message.chat = MagicMock()
        message.chat.id = chat_id

        message.from_user = MagicMock()
        message.from_user.id = user_id

        message.reply_to_message = reply_to_message

        message.reply = AsyncMock()

        return message

    return _factory
