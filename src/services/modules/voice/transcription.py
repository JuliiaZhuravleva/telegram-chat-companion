"""Voice and video note transcription service.

Uses OpenAI Whisper via AIRouter to transcribe audio,
saves the result to message history.
"""

from __future__ import annotations

import html

import structlog

from src.database.repositories.messages import MessageRepository
from src.services.ai.base import AIProviderError, TranscriptionResult
from src.services.ai.router import AIRouter
from src.utils.telegram_text import DEFAULT_SPLIT_LIMIT, parsed_length, split_html

logger = structlog.get_logger(__name__)

# ── Transcription message header ──────────────────────────────────────
#
# Rendered as HTML, not legacy Markdown, and both interpolated values are
# escaped. The previous version built `*Расшифровка от* {name}` and sent it
# with parse_mode="Markdown" without escaping: a display name carrying an
# unbalanced `*` or `_` (`Ivan_K*` — measured: three asterisks, no way to
# balance them) makes Telegram reject the whole sendMessage with "can't parse
# entities". That cost the transcription outright, and — once the answer path
# below was added after the send — silently killed that too, for every voice
# message from that user. HTML + html.escape() is what the rest of the project
# does (CLAUDE.md: "Default parse_mode=HTML ... must html.escape() dynamic
# content") and removes the whole question.
_HEADER_EMOJI = "\U0001f399"
_HEADER_LABEL = "Расшифровка от"


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
        user_id: int | None = None,
        username: str | None = None,
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

        # Write the transcript onto the VOICE message's own row -- that row is
        # what the prompt renders as "<speaker>: <what they said>".
        #
        # `message_type` is the audio's own type, not "transcription". It used
        # to be the latter, which was harmless only by accident: the row
        # normally already exists (MessageSaverMiddleware) and the UPSERT's DO
        # UPDATE touches content alone, so the label never stuck. When
        # `save_messages` is off for a chat, though, there is no prior row and
        # this INSERT created one genuinely labelled 'transcription' -- a label
        # that, since migration 028, means "bot bookkeeping row, keep it out of
        # the prompt". The speaker's words would have vanished from history in
        # exactly the chats that had opted out of storing messages.
        try:
            # Identity is passed even though MessageSaverMiddleware normally
            # inserted this row already (its UPSERT touches content only, so
            # these are no-ops then). It matters when `save_messages` is off
            # for the chat: the middleware returns early, THIS insert creates
            # the row, and without a name on it `get_transcription_source`
            # later reports the speaker as unknown for every voice message in
            # that chat.
            await self._messages.save(
                chat_id=chat_id,
                message_id=message_id,
                message_type=message_type,
                content=result.text,
                reply_to_message_id=message_id,
                user_id=user_id,
                username=username,
                first_name=user_first_name,
            )
        except Exception as exc:
            # Detail matters more than it looks: this row is where
            # `get_transcription_source` reads the transcript from. If the
            # write fails, a later reply to the transcription is handed the
            # rendered bot message instead -- header and all -- as if those
            # were the spoken words. A bare "failed" line leaves nothing to
            # connect that prompt degradation back to here.
            logger.warning(
                "Failed to save transcription to DB",
                chat_id=chat_id,
                message_id=message_id,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )

        return result

    async def record_transcription_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        source_message_id: int,
        message_thread_id: int | None = None,
    ) -> None:
        """Record that `message_id` is the bot's transcription of `source_message_id`.

        This single row is how the bot recognises its own transcriptions later
        (`MessageRepository.get_transcription_source`), so that a reply to one
        is routed to the person who spoke instead of counting as a reply to the
        bot. It replaces matching the rendered header text, which a user could
        forge by simply asking the bot to echo that text back.

        `content` is deliberately left NULL: the transcript already lives on the
        source row, and duplicating it would feed the same utterance to the
        model twice.

        Never raises. A failure here degrades to the pre-fix behaviour for
        replies to this one message (they count as addressed to the bot) --
        worth a warning, never worth losing the transcription the user can
        already see.
        """
        try:
            await self._messages.save(
                chat_id=chat_id,
                message_id=message_id,
                message_type="transcription",
                is_bot_message=True,
                reply_to_message_id=source_message_id,
                transcribed_message_id=source_message_id,
                message_thread_id=message_thread_id,
            )
        except Exception:
            logger.warning(
                "Failed to record transcription link",
                chat_id=chat_id,
                message_id=message_id,
                source_message_id=source_message_id,
                exc_info=True,
            )

    @staticmethod
    def format_reply(user_first_name: str, transcription_text: str) -> str:
        """Format transcription for Telegram reply.

        Returns HTML (the bot's default parse mode) with both interpolated
        values escaped -- see the header note above for why this is not
        Markdown any more.

        Kept for callers that want the whole thing as one string. Anything
        that actually sends it should use `format_reply_parts`, because a
        transcript long enough to exceed Telegram's 4096-character limit is
        not a rare edge: it is any voice message over roughly four minutes.
        """
        return (
            f"{_HEADER_EMOJI} <b>{_HEADER_LABEL}</b> {html.escape(user_first_name)}:"
            f"\n\n{html.escape(transcription_text)}"
        )

    @staticmethod
    def format_reply_parts(user_first_name: str, transcription_text: str) -> list[str]:
        """The same reply, split into messages Telegram will accept.

        A six-minute voice note transcribes to well over 4096 characters, and
        the un-split version of this was rejected outright -- costing the chat
        the transcription entirely, and the bot the bookkeeping row that tells
        it a later reply belongs to the speaker rather than to itself.

        Every part carries the header, because on Telegram the parts arrive as
        separate messages and a reader scrolling into the middle of a long
        transcript should still see whose words these are. The ``(2/3)``
        counter appears only when there is more than one part, so the ordinary
        short voice message is byte-identical to what `format_reply` produced
        before.
        """
        # A display name is user-controlled and can be 64 characters; the
        # header budget has to be derived from the real one, not assumed.
        name = html.escape(user_first_name)
        head = f"{_HEADER_EMOJI} <b>{_HEADER_LABEL}</b> {name}"

        # Reserve room for the longest counter this will realistically render
        # plus the blank line under the header. Reserving too much only costs
        # an extra message; reserving too little costs the whole thing.
        reserve = parsed_length(head) + len(" (99/99):") + 2
        budget = max(DEFAULT_SPLIT_LIMIT - reserve, 512)

        bodies = split_html(html.escape(transcription_text), limit=budget)
        if len(bodies) == 1:
            return [f"{head}:\n\n{bodies[0]}"]
        total = len(bodies)
        return [
            f"{head} ({index}/{total}):\n\n{body}" for index, body in enumerate(bodies, start=1)
        ]
