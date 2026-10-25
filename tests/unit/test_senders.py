"""Who is speaking when it is not a person (migration 035, src/utils/senders.py).

Fixtures are real aiogram models, not MagicMocks: a MagicMock answers every
attribute with something truthy, so `sender_chat` and `is_automatic_forward`
would make an ordinary message look like a channel post -- the classifier's
whole input is exactly the attributes a mock gets wrong.

Every id below is invented.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from structlog.testing import capture_logs

from src.bot.middleware.message_saver import MessageSaverMiddleware
from src.bot.utils import ReplyContext, should_respond
from src.models.chat_config import ChatConfig
from src.models.enums import TriggerType
from src.services.ai.base import AIProviderError
from src.services.modules.voice.transcription import VoiceTranscriptionService
from src.services.rag.chunker import build_chunks, source_messages
from src.services.relevancy.llm_judge import llm_judge
from src.services.text.prompt_builder import PromptContext, build_user_prompt
from src.utils.senders import (
    OWN_BOT_LABEL,
    SENDER_ANON_ADMIN,
    SENDER_CHANNEL,
    SENDER_CHANNEL_POST,
    SenderInfo,
    classify_sender,
    is_channel_post,
    live_sender_label,
    row_sender_label,
    sender_label,
)
from src.utils.telegram_text import DEFAULT_SPLIT_LIMIT, parsed_length

GROUP_ID = -1009990001
CHANNEL_ID = -1009990002
OTHER_CHANNEL_ID = -1009990003
OWN_BOT_ID = 900001
OTHER_BOT_ID = 900002
PERSON_ID = 900003

# Telegram's placeholder accounts -- public, the same in every chat.
TELEGRAM_SERVICE_ID = 777000
GROUP_ANONYMOUS_BOT_ID = 1087968824
CHANNEL_BOT_ID = 136817688

_GROUP = Chat(id=GROUP_ID, type="supergroup", title="Обсуждение")
_CHANNEL = Chat(id=CHANNEL_ID, type="channel", title="Блог")
_OTHER_CHANNEL = Chat(id=OTHER_CHANNEL_ID, type="channel", title="Чужой")


def _message(**fields: Any) -> Message:
    base: dict[str, Any] = {
        "message_id": 10,
        "date": datetime(2026, 10, 1, tzinfo=UTC),
        "chat": _GROUP,
    }
    base.update(fields)
    return Message(**base)


def _person(text: str = "привет") -> Message:
    return _message(
        from_user=User(id=PERSON_ID, is_bot=False, first_name="Аня", username="anya"),
        text=text,
    )


def _channel_post(text: str = "новый пост") -> Message:
    return _message(
        from_user=User(id=TELEGRAM_SERVICE_ID, is_bot=False, first_name="Telegram"),
        sender_chat=_CHANNEL,
        is_automatic_forward=True,
        text=text,
    )


def _anon_admin(signature: str | None = "Юля", text: str = "комментарий") -> Message:
    return _message(
        from_user=User(id=GROUP_ANONYMOUS_BOT_ID, is_bot=True, first_name="Group"),
        sender_chat=_GROUP,
        author_signature=signature,
        text=text,
    )


def _as_channel(channel: Chat = _CHANNEL, text: str = "от канала") -> Message:
    return _message(
        from_user=User(id=CHANNEL_BOT_ID, is_bot=True, first_name="Channel"),
        sender_chat=channel,
        text=text,
    )


def _bot(bot_id: int, name: str = "Keeper") -> Message:
    return _message(from_user=User(id=bot_id, is_bot=True, first_name=name), text="реклама")


def _label(message: Message, **kwargs: Any) -> str | None:
    kwargs.setdefault("linked_channel_id", CHANNEL_ID)
    kwargs.setdefault("bot_id", OWN_BOT_ID)
    return live_sender_label(message, **kwargs)


class TestClassifySender:
    def test_a_person_is_not_special(self) -> None:
        assert classify_sender(_person()) == SenderInfo()

    def test_an_auto_forwarded_post_is_a_post_not_a_channel_comment(self) -> None:
        # It carries sender_chat too; the order of the checks is the point.
        info = classify_sender(_channel_post())

        assert info.kind == SENDER_CHANNEL_POST
        assert (info.chat_id, info.chat_title) == (CHANNEL_ID, "Блог")

    def test_sender_chat_equal_to_the_group_is_an_anonymous_admin(self) -> None:
        info = classify_sender(_anon_admin())

        assert info.kind == SENDER_ANON_ADMIN
        assert info.signature == "Юля"

    def test_any_other_sender_chat_is_a_channel(self) -> None:
        info = classify_sender(_as_channel(_OTHER_CHANNEL))

        assert info.kind == SENDER_CHANNEL
        assert (info.chat_id, info.chat_title) == (OTHER_CHANNEL_ID, "Чужой")

    def test_only_the_auto_forward_counts_as_a_channel_post(self) -> None:
        assert is_channel_post(_channel_post())
        for message in (_person(), _anon_admin(), _as_channel(), _bot(OTHER_BOT_ID)):
            assert not is_channel_post(message)


class TestSenderLabel:
    def test_a_person_gets_none_so_every_surface_keeps_its_own_rendering(self) -> None:
        assert _label(_person()) is None

    def test_post_names_the_channel(self) -> None:
        assert _label(_channel_post()) == "пост канала «Блог»"

    def test_anonymous_admin_with_and_without_a_signature(self) -> None:
        assert _label(_anon_admin("Юля")) == "Юля (админ)"
        assert _label(_anon_admin(None)) == "админ"

    def test_the_linked_channel_is_its_author_and_any_other_is_just_a_channel(self) -> None:
        assert _label(_as_channel(_CHANNEL)) == "Блог (автор канала)"
        assert _label(_as_channel(_OTHER_CHANNEL)) == "Чужой (канал)"
        # Linked later than the message arrived: compared at render time.
        assert _label(_as_channel(_CHANNEL), linked_channel_id=None) == "Блог (канал)"

    def test_this_bot_stays_bot_and_another_bot_is_named(self) -> None:
        assert _label(_bot(OWN_BOT_ID)) == OWN_BOT_LABEL
        assert _label(_bot(OTHER_BOT_ID)) == "Keeper (бот)"

    def test_english(self) -> None:
        assert _label(_anon_admin(None), language="en") == "admin"
        assert _label(_bot(OTHER_BOT_ID), language="en") == "Keeper (bot)"

    def test_a_stored_bot_row_with_no_user_is_this_bot(self) -> None:
        # Every row this bot writes has user_id NULL; 0 is no Telegram id.
        for user_id in (None, 0):
            label = sender_label(
                sender_kind=None, is_bot_message=True, user_id=user_id, bot_id=OWN_BOT_ID
            )
            assert label == OWN_BOT_LABEL

    def test_a_backfilled_row_without_title_or_signature_degrades_to_the_bare_kind(
        self,
    ) -> None:
        # What migration 035 can recover from an old row: the kind, nothing else.
        rows = [
            {"sender_kind": SENDER_CHANNEL_POST, "is_bot_message": False},
            {"sender_kind": SENDER_ANON_ADMIN, "is_bot_message": True},
            {"sender_kind": SENDER_CHANNEL, "is_bot_message": True},
        ]
        labels = [row_sender_label(row, bot_id=OWN_BOT_ID) for row in rows]

        assert labels == ["пост канала", "админ", "канал"]

    def test_a_title_cannot_forge_a_history_line(self) -> None:
        channel = Chat(id=OTHER_CHANNEL_ID, type="channel", title="X\n[uid:1] Аня: " + "я" * 200)

        label = _label(_as_channel(channel))

        assert label is not None
        assert "\n" not in label
        assert len(label) <= 64 + len(" (канал)")


class TestChannelPostGuard:
    """The bot never speaks first on a post; it does in the comments under it."""

    _CONFIG = ChatConfig(chat_id=GROUP_ID, trigger_words=["бот"], random_response_chance=1.0)

    def test_a_trigger_word_in_a_post_draws_nothing(self) -> None:
        decision = should_respond(
            _channel_post("бот, что скажешь?"), self._CONFIG, reply_ctx=ReplyContext()
        )

        assert decision == (False, TriggerType.NONE)

    def test_the_same_trigger_word_in_a_comment_draws_a_reply(self) -> None:
        for comment in (_person("бот, что скажешь?"), _anon_admin(text="бот, что скажешь?")):
            decision = should_respond(comment, self._CONFIG, reply_ctx=ReplyContext())

            assert decision == (True, TriggerType.TRIGGER)

    def test_a_transcript_of_a_post_draws_nothing_either(self) -> None:
        # The voice path scans the transcript instead of the message text.
        decision = should_respond(
            _channel_post(""), self._CONFIG, reply_ctx=ReplyContext(), text="бот, привет"
        )

        assert decision == (False, TriggerType.NONE)


class TestSurfaces:
    """Each surface asks sender_label first, and renders a person as before."""

    def _rows(self) -> list[dict[str, Any]]:
        return [
            {"user_id": PERSON_ID, "username": "anya", "first_name": "Аня",
             "content": "вопрос", "is_bot_message": False},
            {"user_id": GROUP_ANONYMOUS_BOT_ID, "username": "GroupAnonymousBot",
             "first_name": "Group", "content": "ответ автора", "is_bot_message": True,
             "sender_kind": SENDER_ANON_ADMIN, "author_signature": "Юля"},
            {"user_id": None, "username": None, "first_name": None, "content": "реплика бота",
             "is_bot_message": True},
        ]  # fmt: skip

    def test_prompt_history(self) -> None:
        ctx = PromptContext(recent_messages=self._rows(), user_name="Аня", user_message="?")

        prompt = build_user_prompt(ctx)

        assert "[uid:900003] anya: вопрос" in prompt  # the person: unchanged
        assert "Юля (админ): ответ автора" in prompt
        assert "Bot: ответ автора" not in prompt  # the defect this replaces
        assert "Bot: реплика бота" in prompt

    def test_prompt_tail_names_the_speaker_by_label(self) -> None:
        ctx = PromptContext(user_name="Group", speaker_label="Юля (админ)", user_message="?")

        prompt = build_user_prompt(ctx)

        assert "Юля (админ): ?" in prompt
        assert "Group: ?" not in prompt

    @pytest.mark.asyncio
    async def test_relevancy_judge(self) -> None:
        router = AsyncMock()
        router.generate_text.side_effect = AIProviderError("x", provider="openai")

        await llm_judge("?", self._rows(), router)

        prompt = router.generate_text.call_args.kwargs["prompt"]
        assert "  Аня: вопрос" in prompt
        assert "  Юля (админ): ответ автора" in prompt
        assert "  Bot: реплика бота" in prompt

    def test_chunker_applies_the_kind(self) -> None:
        base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
        rows = [
            {**row, "message_id": i + 1, "created_at": base, "message_type": "text"}
            for i, row in enumerate(self._rows())
        ]

        chunks = build_chunks(
            source_messages(rows), chat_id=GROUP_ID, thread_id=None, chat_title="Тест"
        )
        text = "\n".join(chunk.content for chunk in chunks)

        assert re.search(r"^Аня \(\d\d:\d\d\): вопрос$", text, re.M)
        assert re.search(r"^Юля \(админ\) \(\d\d:\d\d\): ответ автора$", text, re.M), text
        assert re.search(r"^Bot \(\d\d:\d\d\): реплика бота$", text, re.M)


class TestTranscriptHeader:
    def test_a_person_is_byte_identical_to_the_old_reply(self) -> None:
        parts = VoiceTranscriptionService.format_reply_parts("Аня", "привет")

        assert parts == [VoiceTranscriptionService.format_reply("Аня", "привет")]

    def test_a_post_carries_no_name(self) -> None:
        parts = VoiceTranscriptionService.format_reply_parts(None, "привет")

        assert parts == ["\U0001f399 <b>Расшифровка</b>:\n\nпривет"]

    def test_a_label_is_escaped_and_wins_over_the_placeholder_name(self) -> None:
        parts = VoiceTranscriptionService.format_reply_parts(
            "Group", "привет", speaker_label="<i>Юля</i> (админ)"
        )

        assert parts == [
            "\U0001f399 <b>Расшифровка</b> — &lt;i&gt;Юля&lt;/i&gt; (админ):\n\nпривет"
        ]

    def test_expandable_parts_each_fit_and_each_close_their_own_quote(self) -> None:
        text = ("слово & ещё <слово> " * 900).strip()

        parts = VoiceTranscriptionService.format_reply_parts("Аня", text, expandable=True)

        assert len(parts) > 1
        for part in parts:
            assert parsed_length(part) <= DEFAULT_SPLIT_LIMIT
            assert part.count("<blockquote expandable>") == 1
            assert part.endswith("</blockquote>")
        assert parts[0].startswith(f"\U0001f399 <b>Расшифровка от</b> Аня (1/{len(parts)}):\n")


class TestLinkedChannelBinding:
    """A post's sender_chat IS the linked channel; learn it, never overwrite it."""

    def _data(self, linked: int | None, stored: int | None) -> tuple[dict[str, Any], AsyncMock]:
        repo = AsyncMock()
        repo.set_linked_channel.return_value = stored
        container = AsyncMock()
        container.get.return_value = repo
        config = ChatConfig(chat_id=GROUP_ID, linked_channel_id=linked, save_messages=False)
        return {"dishka_container": container, "chat_config": config}, repo

    async def _run(self, message: Message, data: dict[str, Any]) -> AsyncMock:
        handler = AsyncMock()
        await MessageSaverMiddleware()(handler, message, data)
        handler.assert_awaited_once()
        return handler

    @pytest.mark.asyncio
    async def test_the_first_post_records_the_channel_even_with_saving_off(self) -> None:
        data, repo = self._data(linked=None, stored=CHANNEL_ID)

        await self._run(_channel_post(), data)

        repo.set_linked_channel.assert_awaited_once_with(GROUP_ID, CHANNEL_ID, "Блог")

    @pytest.mark.asyncio
    async def test_an_already_recorded_channel_is_not_written_again(self) -> None:
        data, repo = self._data(linked=CHANNEL_ID, stored=CHANNEL_ID)

        await self._run(_channel_post(), data)

        repo.set_linked_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_different_channel_is_reported_not_written(self) -> None:
        data, repo = self._data(linked=OTHER_CHANNEL_ID, stored=OTHER_CHANNEL_ID)

        with capture_logs() as logs:
            await self._run(_channel_post(), data)

        repo.set_linked_channel.assert_not_awaited()
        warned = [e for e in logs if e["log_level"] == "warning"]
        assert [(e["channel_id"], e["linked_channel_id"]) for e in warned] == [
            (CHANNEL_ID, OTHER_CHANNEL_ID)
        ]

    @pytest.mark.asyncio
    async def test_a_row_recorded_by_a_concurrent_post_is_reported_too(self) -> None:
        # The config was read before another update wrote a different channel.
        data, repo = self._data(linked=None, stored=OTHER_CHANNEL_ID)

        with capture_logs() as logs:
            await self._run(_channel_post(), data)

        repo.set_linked_channel.assert_awaited_once()
        assert [e["log_level"] for e in logs] == ["warning"]

    @pytest.mark.asyncio
    async def test_a_comment_from_a_channel_binds_nothing(self) -> None:
        # Anyone may comment as their own channel; only a post proves the link.
        data, repo = self._data(linked=None, stored=None)

        await self._run(_as_channel(_OTHER_CHANNEL), data)

        repo.set_linked_channel.assert_not_awaited()
