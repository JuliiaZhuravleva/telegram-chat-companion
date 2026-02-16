"""Tests for voice transcription service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.ai.base import AIProviderError, TranscriptionResult
from src.services.modules.voice.transcription import VoiceTranscriptionService


@pytest.fixture
def voice_service():
    ai_router = MagicMock()
    message_repo = MagicMock()
    message_repo.save = AsyncMock()
    return VoiceTranscriptionService(ai_router, message_repo)


@pytest.mark.asyncio
async def test_transcribe_success(voice_service):
    voice_service._ai.transcribe_audio = AsyncMock(
        return_value=TranscriptionResult(
            text="Привет, мир!",
            model="whisper-1",
            provider="openai",
        )
    )

    result = await voice_service.transcribe(
        audio_data=b"fake-audio",
        chat_id=-100123,
        message_id=42,
        user_first_name="Alice",
        message_type="voice",
    )

    assert result is not None
    assert result.text == "Привет, мир!"
    voice_service._messages.save.assert_awaited_once()
    call_kwargs = voice_service._messages.save.call_args
    assert call_kwargs.kwargs.get("message_type") == "transcription" or \
           call_kwargs[1].get("message_type") == "transcription"


@pytest.mark.asyncio
async def test_transcribe_ai_error_returns_none(voice_service):
    voice_service._ai.transcribe_audio = AsyncMock(
        side_effect=AIProviderError("API error", provider="openai")
    )

    result = await voice_service.transcribe(
        audio_data=b"fake-audio",
        chat_id=-100123,
        message_id=42,
        user_first_name="Alice",
        message_type="voice",
    )

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_empty_text_returns_none(voice_service):
    voice_service._ai.transcribe_audio = AsyncMock(
        return_value=TranscriptionResult(
            text="   ",
            model="whisper-1",
            provider="openai",
        )
    )

    result = await voice_service.transcribe(
        audio_data=b"fake-audio",
        chat_id=-100123,
        message_id=42,
        user_first_name="Alice",
        message_type="voice",
    )

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_db_failure_still_returns_result(voice_service):
    voice_service._ai.transcribe_audio = AsyncMock(
        return_value=TranscriptionResult(
            text="Test transcription",
            model="whisper-1",
            provider="openai",
        )
    )
    voice_service._messages.save = AsyncMock(side_effect=RuntimeError("DB error"))

    result = await voice_service.transcribe(
        audio_data=b"fake-audio",
        chat_id=-100123,
        message_id=42,
        user_first_name="Alice",
        message_type="voice",
    )

    # Should still return the transcription even if DB save fails
    assert result is not None
    assert result.text == "Test transcription"


class TestFormatReply:
    def test_format(self):
        result = VoiceTranscriptionService.format_reply("Alice", "Hello world")
        assert "Alice" in result
        assert "Hello world" in result
        assert "\U0001f399" in result
        assert "Расшифровка" in result
