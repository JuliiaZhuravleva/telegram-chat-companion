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
    saved = voice_service._messages.save.call_args.kwargs
    # The transcript lands on the AUDIO message's own row, labelled with the
    # audio's type. It used to be saved as message_type="transcription", which
    # since migration 028 means "bot bookkeeping row, keep out of the prompt" —
    # in a chat with save_messages off (no prior row for the UPSERT to hit)
    # that label would have stuck and the speaker's words would have silently
    # vanished from the model's history.
    assert saved["message_type"] == "voice"
    assert saved["content"] == "Привет, мир!"
    assert saved.get("transcribed_message_id") is None
    assert not saved.get("is_bot_message")


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

    def test_a_display_name_with_markup_characters_does_not_break_the_send(self):
        """The header used to be legacy Markdown with nothing escaped.

        A display name like `Ivan_K*` produced an odd number of asterisks and a
        dangling underscore, Telegram rejected the whole sendMessage with
        "can't parse entities", and the transcription was lost — silently, and
        for every voice note that user ever sent. It became worse once an
        answer step was added after the send, which then never ran either.
        """
        result = VoiceTranscriptionService.format_reply("Ivan_K*", "hi")

        # Nothing that HTML would treat as markup survives unescaped, and the
        # only tags present are the ones this function puts there itself.
        assert "Ivan_K*" in result
        assert result.count("<b>") == 1
        assert result.count("</b>") == 1

    def test_html_special_characters_are_escaped_on_both_sides(self):
        """parse_mode=HTML means a `<` in a name or in Whisper output is markup
        unless escaped (CLAUDE.md: every send must escape dynamic content)."""
        result = VoiceTranscriptionService.format_reply("<b>Eve</b>", "a < b & c")

        assert "&lt;b&gt;Eve&lt;/b&gt;" in result
        assert "a &lt; b &amp; c" in result
        # The injected tags must not have survived as real markup.
        assert result.count("<b>") == 1


class TestRecordTranscriptionMessage:
    """The row that replaces the old header-matching.

    `transcribed_message_id` being set is the entire definition of "this bot
    message is a relayed transcription" — nothing parses text any more.
    """

    @pytest.mark.asyncio
    async def test_writes_the_link_row(self, voice_service):
        await voice_service.record_transcription_message(
            chat_id=-100123,
            message_id=778,
            source_message_id=777,
            message_thread_id=5,
        )

        saved = voice_service._messages.save.call_args.kwargs
        assert saved["chat_id"] == -100123
        assert saved["message_id"] == 778
        assert saved["transcribed_message_id"] == 777
        assert saved["message_type"] == "transcription"
        assert saved["is_bot_message"] is True
        assert saved["message_thread_id"] == 5
        # No content: the transcript already lives on the source row, and
        # storing it twice would feed the same utterance to the model twice.
        assert saved.get("content") is None

    @pytest.mark.asyncio
    async def test_a_db_failure_never_propagates(self, voice_service):
        """The user can already see the transcription. Losing the link degrades
        replies to it back to the old behaviour; crashing the handler would be
        strictly worse."""
        voice_service._messages.save = AsyncMock(side_effect=RuntimeError("DB down"))

        await voice_service.record_transcription_message(
            chat_id=-100123, message_id=778, source_message_id=777
        )
