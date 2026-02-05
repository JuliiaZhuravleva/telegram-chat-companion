"""Voice and video note transcription service.

Uses OpenAI Whisper via AIRouter to transcribe audio,
saves the result to message history.
"""

from __future__ import annotations

import structlog

from src.database.repositories.messages import MessageRepository
from src.services.ai.base import AIProviderError, TranscriptionResult
from src.services.ai.router import AIRouter

logger = structlog.get_logger(__name__)


class VoiceTranscriptionService:
    """Transcribe voice messages and video notes using Whisper."""

    def __init__(
        self,
        ai_router: AIRouter,
        message_repo: MessageRepository,
    ) -> None:
        self._ai = ai_router
        self._messages = message_repo

    async def transcribe(
        self,
        *,
        audio_data: bytes,
        chat_id: int,
        message_id: int,
        user_first_name: str,
        message_type: str,
        language: str | None = None,
    ) -> TranscriptionResult | None:
        """Transcribe audio and save to message history.

        Args:
            audio_data: Raw audio bytes (ogg/opus).
            chat_id: Chat where the voice message was sent.
            message_id: Original message ID (for reply threading).
            user_first_name: Sender's first name (for formatting).
            message_type: "voice" or "video_note".
            language: Expected language (ISO code, e.g. "ru").

        Returns:
            TranscriptionResult on success, None on failure.
        """
        try:
            result = await self._ai.transcribe_audio(
                audio_data=audio_data,
                language=language,
            )
        except AIProviderError:
            logger.exception(
                "Transcription failed",
                chat_id=chat_id,
                message_type=message_type,
                user=user_first_name,
            )
            return None

        if not result.text.strip():
            logger.info(
                "Empty transcription result",
                chat_id=chat_id,
                message_type=message_type,
                user=user_first_name,
            )
            return None

        # Save transcription as a separate bot message in history
        try:
            await self._messages.save(
                chat_id=chat_id,
                message_id=message_id,
                message_type="transcription",
                content=result.text,
                is_bot_message=True,
                reply_to_message_id=message_id,
            )
        except Exception:
            logger.warning(
                "Failed to save transcription to DB",
                chat_id=chat_id,
            )

        return result

    @staticmethod
    def format_reply(user_first_name: str, transcription_text: str) -> str:
        """Format transcription for Telegram reply.

        Returns Markdown-formatted text.
        """
        return (
            f"\U0001f399 *Расшифровка от* {user_first_name}:\n\n"
            f"{transcription_text}"
        )
