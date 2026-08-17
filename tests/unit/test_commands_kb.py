"""Tests for /remember + /kb command handlers (A4, ADR-0003; S2/KB-07..KB-09).

The S2 contract these tests pin, and why each half matters:

* **Guard order is `kb_enabled` → authority → content.** A gate that sits
  downstream of the thing it gates is not a gate: before S2 a member of a chat
  with the KB switched off got a lecture about replies, i.e. the disabled-KB
  answer was unreachable for exactly the inputs that were also malformed.
* **Capture is append-only (KB-07).** The predicate carries the *capture's*
  identity, so two facts about one subject coexist and a redelivered update is
  answered "already saved" rather than written twice.
* **A degradation still saves the fact (KB-09).** Every unparseable directive
  produces a stored fact plus a note; nothing costs the user their text.
* **The confirmation is sent after the row is committed**, so a rejected
  confirmation reads to the user as a save that never happened. It is explicit
  escaped HTML with a plain-text retry.

All ids here are obviously fake — this repo is public (CLAUDE.md).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject
from aiogram.types import Message

from src.bot.handlers.commands import (
    _fit_message,
    handle_kb_undo,
    handle_kb_view_dm,
    handle_kb_view_group,
    handle_kb_view_page,
    handle_remember,
    handle_remember_dm,
)

ADMIN_ID = 111
ORGANIZER_ID = 222
RANDOM_USER_ID = 333
BOT_ID = 999
CHAT_ID = -1001
CMD_MESSAGE_ID = 1001
REPLY_MESSAGE_ID = 500

# The handler derives "today" from `datetime.now(CAPTURE_TZ)`; the tests resolve
# the same wall clock independently rather than importing the constant, so a
# future divergence between capture and rendering timezones shows up here.
TBILISI = ZoneInfo("Asia/Tbilisi")


# ---------------------------------------------------------------------------
# factories
# ---------------------------------------------------------------------------


def _make_chat_config(
    language: str = "ru", kb_enabled: bool = True, chat_id: int = CHAT_ID
) -> MagicMock:
    cfg = MagicMock()
    cfg.language = language
    cfg.kb_enabled = kb_enabled
    cfg.chat_id = chat_id
    return cfg


def _make_reply(
    *,
    message_id: int = REPLY_MESSAGE_ID,
    text: str | None = "исходное сообщение",
    caption: str | None = None,
    from_user_id: int | None = RANDOM_USER_ID,
) -> MagicMock:
    """A `reply_to_message` stub.

    `from_user_id` is explicit because `_resolve_captured_text` branches on
    `rpl.from_user.id == bot_id`: a bare MagicMock would make that comparison
    accidentally false and hide the bot-own-message path.
    """
    rpl = MagicMock()
    rpl.message_id = message_id
    rpl.text = text
    rpl.caption = caption
    if from_user_id is None:
        rpl.from_user = None
    else:
        rpl.from_user = MagicMock()
        rpl.from_user.id = from_user_id
    return rpl


def _make_quote(text: str, *, is_manual: bool = True) -> MagicMock:
    quote = MagicMock()
    quote.text = text
    quote.is_manual = is_manual
    return quote


def _make_message(
    *,
    text: str = "/remember тема: значение",
    message_id: int = CMD_MESSAGE_ID,
    user_id: int = ADMIN_ID,
    reply_to_message: MagicMock | None = None,
    quote: MagicMock | None = None,
    caption: str | None = None,
    chat_id: int = CHAT_ID,
    chat_type: str = "group",
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.caption = caption
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.reply_to_message = reply_to_message
    # Must default to a real None: the handler tests `message.quote is not None`,
    # and MagicMock's auto-attribute would make every message look quoted.
    msg.quote = quote
    msg.reply = AsyncMock()
    msg.answer = AsyncMock()
    msg.bot = None
    return msg


def _make_command(args: str = "") -> CommandObject:
    """A real `CommandObject` — the handler reads `command.args`, not message.text.

    Built from aiogram's own dataclass rather than a MagicMock so a future
    rename of the field breaks the test instead of silently yielding a
    MagicMock that is truthy and non-empty.
    """
    return CommandObject(prefix="/", command="remember", args=args or None)


def _make_bot_config_repo(admin_ids: list[int] | None = None) -> MagicMock:
    repo = MagicMock()
    # BotConfigRepository.get() already returns json.loads() output (a parsed
    # list), not a raw JSON string -- see src/utils/parse_admin_ids docstring.
    repo.get = AsyncMock(return_value=admin_ids if admin_ids is not None else [ADMIN_ID])
    return repo


def _make_chat_settings_repo(organizer_ids: list[int] | None = None) -> MagicMock:
    repo = MagicMock()
    ids = organizer_ids if organizer_ids is not None else [ORGANIZER_ID]
    repo.get = AsyncMock(return_value={"kb_organizer_ids": json.dumps(ids)})
    return repo


def _make_knowledge_repo(
    append_result: tuple[int, bool] = (42, True),
    existing_status: str = "active",
) -> MagicMock:
    repo = MagicMock()
    repo.append_fact = AsyncMock(return_value=append_result)
    # Present so "the handler must not supersede" is an assertable fact rather
    # than an attribute error.
    repo.upsert_fact = AsyncMock(return_value=42)
    repo.reject_fact = AsyncMock(return_value=True)
    repo.get_active_facts = AsyncMock(return_value=[])
    # Read only on the `created=False` path, to tell "this capture already saved
    # a live fact" from "…and it was then undone" — the second must not be
    # reported as "already saved", which would be a false claim about the data.
    repo.get_by_id = AsyncMock(return_value={"id": append_result[0], "status": existing_status})
    return repo


def _make_message_repo(transcription: dict[str, Any] | None = None) -> MagicMock:
    repo = MagicMock()
    repo.get_transcription_source = AsyncMock(return_value=transcription)
    return repo


def _make_ai_router() -> MagicMock:
    router = MagicMock()
    result = MagicMock()
    result.embedding = [0.1, 0.2]
    router.generate_embedding = AsyncMock(return_value=result)
    return router


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    return bot


class _Deps:
    """The eight dependencies `handle_remember` takes, plus its call convention.

    Keeping the invocation in one place is what lets every test drive the real
    handler (rather than a helper of it) without repeating a positional list
    whose order changed in S2 — `message_repo` is now the 8th positional.
    """

    def __init__(
        self,
        *,
        admin_ids: list[int] | None = None,
        organizer_ids: list[int] | None = None,
        append_result: tuple[int, bool] = (42, True),
        existing_status: str = "active",
        transcription: dict[str, Any] | None = None,
        ai_router: MagicMock | None = None,
    ) -> None:
        self.knowledge = _make_knowledge_repo(append_result, existing_status)
        self.bot_config = _make_bot_config_repo(admin_ids)
        self.chat_settings = _make_chat_settings_repo(organizer_ids)
        self.ai_router = ai_router if ai_router is not None else _make_ai_router()
        self.bot = _make_bot()
        self.messages = _make_message_repo(transcription)

    async def remember(
        self,
        msg: MagicMock,
        cfg: MagicMock,
        *,
        args: str = "",
        command: CommandObject | None = None,
        message_thread_id: int | None = None,
        bot_id: int | None = BOT_ID,
    ) -> None:
        await handle_remember(
            msg,
            cfg,
            self.knowledge,
            self.bot_config,
            self.chat_settings,
            self.ai_router,
            self.bot,
            self.messages,
            command=_make_command(args) if command is None else command,
            message_thread_id=message_thread_id,
            bot_id=bot_id,
        )

    @property
    def append_kwargs(self) -> dict[str, Any]:
        """kwargs of the single expected `append_fact` await.

        Raises if the call never happened, so an assertion over these can never
        pass vacuously.
        """
        self.knowledge.append_fact.assert_awaited_once()
        return dict(self.knowledge.append_fact.call_args.kwargs)


def _reply_text(msg: MagicMock) -> str:
    msg.reply.assert_awaited()
    return str(msg.reply.call_args[0][0])


# ---------------------------------------------------------------------------
# guard order
# ---------------------------------------------------------------------------


class TestRememberGuardOrder:
    """`kb_enabled` → authority → content, and the first gate reached wins.

    Threat: a gate that is present but downstream of what it must gate. The
    input below is refusable by all three guards at once, so only the ordering
    decides which answer the user gets and which repositories are touched.
    """

    @pytest.mark.asyncio
    async def test_kb_disabled_answers_first_and_touches_no_repository(self) -> None:
        msg = _make_message(user_id=RANDOM_USER_ID, reply_to_message=None)
        cfg = _make_chat_config(kb_enabled=False)
        deps = _Deps()

        await deps.remember(msg, cfg, args="")

        text = _reply_text(msg)
        msg.reply.assert_awaited_once()
        assert "отключена" in text
        # The other two refusals must be unreachable for this input.
        assert "организаторы" not in text
        assert "Нечего сохранять" not in text
        # Nothing downstream of the gate may run: no authority lookup, no write,
        # no paid embedding call, no transcription probe.
        deps.bot_config.get.assert_not_awaited()
        deps.chat_settings.get.assert_not_awaited()
        deps.knowledge.append_fact.assert_not_awaited()
        deps.knowledge.upsert_fact.assert_not_awaited()
        deps.ai_router.generate_embedding.assert_not_awaited()
        deps.messages.get_transcription_source.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_authority_answers_before_content(self) -> None:
        """An unauthorized member with nothing to save is refused for the right reason.

        Phase 1 asked "is this a reply?" first, so the no-rights answer was
        unreachable for anyone who also forgot to reply.
        """
        msg = _make_message(user_id=RANDOM_USER_ID, reply_to_message=None)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        text = _reply_text(msg)
        msg.reply.assert_awaited_once()
        assert "только организаторы" in text
        assert "Нечего сохранять" not in text
        deps.knowledge.append_fact.assert_not_awaited()


# ---------------------------------------------------------------------------
# authority
# ---------------------------------------------------------------------------


class TestRememberAuthority:
    @pytest.mark.asyncio
    async def test_unauthorized_member_writes_nothing(self) -> None:
        msg = _make_message(user_id=RANDOM_USER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        assert "только организаторы" in _reply_text(msg)
        deps.knowledge.append_fact.assert_not_awaited()
        deps.knowledge.upsert_fact.assert_not_awaited()
        deps.ai_router.generate_embedding.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_organizer_saves_with_authority_3(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        kwargs = deps.append_kwargs
        assert kwargs["authority_level"] == 3  # organizer, not bot admin
        assert kwargs["subject"] == "место"
        assert kwargs["value"] == "кафе Луна"
        assert kwargs["fact_text"] == "место: кафе Луна"
        assert kwargs["source"] == "manual"
        assert kwargs["chat_id"] == CHAT_ID
        assert kwargs["source_user_id"] == ORGANIZER_ID
        assert "Сохранено" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_bot_admin_saves_with_authority_4(self) -> None:
        msg = _make_message(user_id=ADMIN_ID)
        deps = _Deps(organizer_ids=[])

        await deps.remember(msg, _make_chat_config(), args="дата: 24.07")

        assert deps.append_kwargs["authority_level"] == 4


# ---------------------------------------------------------------------------
# append-only (KB-07)
# ---------------------------------------------------------------------------


class TestRememberAppendOnly:
    @pytest.mark.asyncio
    async def test_same_subject_twice_appends_two_facts_and_never_supersedes(self) -> None:
        """Threat: "add another detail" silently retiring the previous fact.

        Phase 1's constant predicate collapsed the designed key
        `(chat_id, subject, predicate)` to `(chat_id, subject)`, so the second
        `/remember` about one subject superseded the first.
        """
        deps = _Deps()
        cfg = _make_chat_config()

        for message_id in (1001, 1002):
            msg = _make_message(user_id=ORGANIZER_ID, message_id=message_id)
            await deps.remember(msg, cfg, args="место: кафе Луна")

        assert deps.knowledge.append_fact.await_count == 2
        first, second = (call.kwargs for call in deps.knowledge.append_fact.await_args_list)
        assert first["subject"] == second["subject"] == "место"
        assert first["predicate"] != second["predicate"], (
            "two captures of one subject share an identity -> the second supersedes the first"
        )
        deps.knowledge.upsert_fact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_redelivered_update_reuses_the_same_predicate(self) -> None:
        """The other half of KB-07: identity is stable *per capture*.

        A predicate built from a clock or a random token would make every
        Telegram redelivery a duplicate row instead of an "already saved".
        """
        deps = _Deps()
        cfg = _make_chat_config()

        for _ in range(2):
            msg = _make_message(user_id=ORGANIZER_ID, message_id=7007)
            await deps.remember(msg, cfg, args="место: кафе Луна")

        assert deps.knowledge.append_fact.await_count == 2
        first, second = (call.kwargs for call in deps.knowledge.append_fact.await_args_list)
        assert first["predicate"] == second["predicate"], (
            "the same command produced two identities -> redelivery duplicates the fact"
        )


class TestRememberIdempotency:
    @pytest.mark.asyncio
    async def test_already_saved_names_the_existing_row_and_replies_once(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps(append_result=(42, False))

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        text = _reply_text(msg)
        msg.reply.assert_awaited_once()
        assert "уже сохранено" in text
        assert "#42" in text
        assert "✅ Сохранено" not in text
        # The undo button belongs to the confirmation that actually wrote the row.
        assert msg.reply.call_args.kwargs.get("reply_markup") is None

    @pytest.mark.asyncio
    async def test_a_redelivery_after_undo_is_not_reported_as_saved(self) -> None:
        """The fact is gone; saying "already saved" would be a false claim.

        `reject_fact` sets `valid_to`, which moves the row out of the partial
        UNIQUE index — so this path is reachable only because `append_fact`
        pre-checks the key regardless of `valid_to`. Without both halves, a
        redelivered capture silently resurrects a fact the user just removed.
        """
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps(append_result=(42, False), existing_status="rejected")

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        text = _reply_text(msg)
        assert "убрали" in text
        assert "#42" in text
        assert "уже сохранено" not in text
        assert "✅ Сохранено" not in text
        deps.knowledge.get_by_id.assert_awaited_once_with(42, chat_id=CHAT_ID)
        assert msg.reply.call_args.kwargs.get("reply_markup") is None


# ---------------------------------------------------------------------------
# capture: reply / caption / quote (KB-08)
# ---------------------------------------------------------------------------


class TestRememberCapture:
    @pytest.mark.asyncio
    async def test_bare_command_on_a_reply_saves_the_replied_text(self) -> None:
        rpl = _make_reply(text="созвон по вторникам вечером")
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        kwargs = deps.append_kwargs
        assert kwargs["fact_text"] == "созвон по вторникам вечером"
        assert kwargs["source_message_id"] == REPLY_MESSAGE_ID
        assert "Сохранено" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_captured_url_is_stored_exactly_as_written(self) -> None:
        """A colon in captured text must not be reinterpreted as a label separator.

        `build_capture` used to rebuild `fact_text` as f"{subject}: {value}", which
        inserted a space after the first colon: a captured URL was stored and shown
        back as `https: //example.com/map`, dead in `/kb` and in the prompt. Pinned
        here as well as in the grammar tests because this is the path where the
        corruption reached `append_fact` and the user's confirmation.
        """
        url_fact = "схема проезда https://example.com/map"
        rpl = _make_reply(text=url_fact)
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        assert deps.append_kwargs["fact_text"] == url_fact

    @pytest.mark.asyncio
    async def test_caption_is_used_when_the_replied_message_has_no_text(self) -> None:
        rpl = _make_reply(text=None, caption="на фото — схема проезда")
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        assert deps.append_kwargs["fact_text"] == "на фото — схема проезда"

    @pytest.mark.asyncio
    async def test_manual_quote_wins_over_the_full_message(self) -> None:
        rpl = _make_reply(text="много слов, и где-то здесь важная деталь, и ещё много слов")
        msg = _make_message(
            user_id=ORGANIZER_ID,
            reply_to_message=rpl,
            quote=_make_quote("важная деталь"),
        )
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        kwargs = deps.append_kwargs
        assert kwargs["fact_text"] == "важная деталь"
        assert "много слов" not in kwargs["fact_text"]
        assert kwargs["source_message_id"] == REPLY_MESSAGE_ID
        # And the user is TOLD that only the fragment was kept. Without this line
        # the confirmation echoes three words and nothing says the rest of the
        # message was deliberately excluded -- the note was computed and logged
        # but never rendered (found by review).
        assert "выделенный фрагмент" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_automatic_quote_does_not_win_over_the_full_message(self) -> None:
        """Threat: a server-chosen excerpt stored as if the user had pointed at it.

        Telegram attaches a quote itself for e.g. a cross-chat reply
        (`is_manual` false). Preferring it would silently store less than the
        message said, under a confirmation claiming the fact was saved.
        """
        full = "много слов, и где-то здесь важная деталь, и ещё много слов"
        rpl = _make_reply(text=full)
        msg = _make_message(
            user_id=ORGANIZER_ID,
            reply_to_message=rpl,
            quote=_make_quote("важная деталь", is_manual=False),
        )
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        assert deps.append_kwargs["fact_text"] == full

    @pytest.mark.asyncio
    async def test_an_automatic_quote_never_wins_even_with_no_full_text(self) -> None:
        """Cross-chat capture is out of scope, and the excuse for it was unreachable.

        The handler used to prefer an automatic quote when the replied-to text was
        unreachable, justified as "the cross-chat case". Review showed that case
        cannot arrive here at all: a reply to a message in another chat carries
        `external_reply` + `quote` and **no** `reply_to_message`, so it returns at
        the guard before the quote is ever consulted. What remains — a same-chat
        reply with no text but an automatic quote — is a state Telegram does not
        produce (auto quotes exist only for text messages).

        So the branch was removed rather than left as an untestable excuse, and
        this test pins the contract that replaced it. Storing nothing is also the
        right answer for provenance: `source_message_id` points at a message in
        *this* chat, and a cross-chat id would be a dangling reference.
        """
        rpl = _make_reply(text=None, caption=None)
        msg = _make_message(
            user_id=ORGANIZER_ID,
            reply_to_message=rpl,
            quote=_make_quote("важная деталь", is_manual=False),
        )
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        deps.knowledge.append_fact.assert_not_awaited()
        assert "Нечего сохранять" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_long_replied_message_is_stored_in_full(self) -> None:
        """Threat: a silently shortened fact under a confirmation saying it was saved.

        `extract_reply_context()` truncates a reply at 500 characters and a
        quote at 300 with no signal to the caller — right for a prompt, wrong
        for a write path. Exact length, not a substring: a substring assertion
        passes against a truncated store.
        """
        long_text = " ".join(["слово"] * 120)  # 719 chars, no whitespace runs to fold
        assert len(long_text) == 719
        rpl = _make_reply(text=long_text)
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        kwargs = deps.append_kwargs
        assert len(kwargs["fact_text"]) == 719
        assert kwargs["fact_text"] == long_text
        # Over MAX_FACT_CHARS: the fact is stored whole, and the user is told the
        # prompt will truncate it.
        assert "длиннее 600" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_typed_text_wins_over_the_reply_but_keeps_its_provenance(self) -> None:
        rpl = _make_reply(text="исходное сообщение")
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        kwargs = deps.append_kwargs
        assert kwargs["fact_text"] == "место: кафе Луна"
        assert kwargs["source_message_id"] == REPLY_MESSAGE_ID

    @pytest.mark.asyncio
    async def test_empty_reply_text_is_refused_without_writing(self) -> None:
        rpl = _make_reply(text="   \n  ", caption=None)
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        assert "Нечего сохранять" in _reply_text(msg)
        deps.knowledge.append_fact.assert_not_awaited()


# ---------------------------------------------------------------------------
# capture: the bot's own messages (transcriptions vs. everything else)
# ---------------------------------------------------------------------------

_TRANSCRIPT = "встречаемся у входа в парк в семь"
_AUDIO_MESSAGE_ID = 480


def _transcription_row() -> dict[str, Any]:
    """Shape of `MessageRepository.get_transcription_source()`'s row (migration 028)."""
    return {
        "source_message_id": _AUDIO_MESSAGE_ID,
        "source_user_id": RANDOM_USER_ID,
        "source_first_name": "Аня",
        "source_username": None,
        "transcript": _TRANSCRIPT,
    }


class TestRememberTranscription:
    @pytest.mark.asyncio
    async def test_reply_to_a_transcription_stores_the_speech_not_the_header(self) -> None:
        rpl = _make_reply(
            message_id=490,
            text=f"🎙 Расшифровка от Аня:\n{_TRANSCRIPT}",
            from_user_id=BOT_ID,
        )
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps(transcription=_transcription_row())

        await deps.remember(msg, _make_chat_config(), args="")

        deps.messages.get_transcription_source.assert_awaited_once_with(CHAT_ID, 490)
        kwargs = deps.append_kwargs
        # Positive half first, so the absence assertion below cannot pass vacuously.
        assert kwargs["fact_text"] == _TRANSCRIPT
        assert "Расшифровка от" not in kwargs["fact_text"]
        # Attributed to the audio message, not to the bot's transcription post.
        assert kwargs["source_message_id"] == _AUDIO_MESSAGE_ID
        assert "Сохранено" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_reply_to_any_other_bot_message_is_refused(self) -> None:
        """Threat: laundering the model's own output into the authoritative base."""
        rpl = _make_reply(message_id=491, text="Я думаю, встреча в семь.", from_user_id=BOT_ID)
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps(transcription=None)

        await deps.remember(msg, _make_chat_config(), args="")

        text = _reply_text(msg)
        assert "моё собственное сообщение" in text
        assert "Я думаю" not in text
        deps.knowledge.append_fact.assert_not_awaited()
        deps.knowledge.upsert_fact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_transcription_lookup_failure_says_retry_not_refusal(self) -> None:
        """A database blip must not be reported as a permanent refusal.

        The two used to share one outcome, so a pool timeout told the user "that
        is my own message, save the original" — advice that is simply wrong for a
        transcription the bot *would* have captured a second later, and that gives
        them no reason to retry. "Could not check right now" is the honest answer:
        we do not know whether it was a transcription.
        """
        rpl = _make_reply(message_id=492, text="что-то от бота", from_user_id=BOT_ID)
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()
        deps.messages.get_transcription_source = AsyncMock(side_effect=RuntimeError("db down"))

        await deps.remember(msg, _make_chat_config(), args="")

        msg.reply.assert_awaited_once()
        text = _reply_text(msg)
        assert "не смог сейчас проверить" in text.lower()
        assert "ещё раз" in text, "a transient failure must invite a retry"
        assert "моё собственное сообщение" not in text
        deps.knowledge.append_fact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reply_to_a_human_message_never_probes_the_transcription_table(self) -> None:
        rpl = _make_reply(text="расписание на среду", from_user_id=RANDOM_USER_ID)
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        deps.messages.get_transcription_source.assert_not_awaited()
        assert deps.append_kwargs["fact_text"] == "расписание на среду"


# ---------------------------------------------------------------------------
# grammar, driven through the handler (KB-09)
# ---------------------------------------------------------------------------


class TestRememberGrammar:
    @pytest.mark.asyncio
    async def test_leading_topic_is_stored_and_stripped_from_the_fact(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="#тема сдаём отчёт в пятницу")

        kwargs = deps.append_kwargs
        assert kwargs["topic"] == "тема"
        assert kwargs["fact_text"] == "сдаём отчёт в пятницу"
        assert "Тема: тема" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_trailing_deadline_becomes_a_tz_aware_inclusive_expiry(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()
        today = datetime.now(TBILISI).date()

        await deps.remember(msg, _make_chat_config(), args="выставка в парке до 05.09")

        kwargs = deps.append_kwargs
        expires_at = kwargs["expires_at"]
        assert isinstance(expires_at, datetime)
        # Timezone-aware, and the bot's own display timezone: asyncpg encodes a
        # naive datetime in whatever zone the *process* runs in.
        assert expires_at.tzinfo is not None
        assert expires_at.utcoffset() == timedelta(hours=4)  # Asia/Tbilisi, no DST
        assert (expires_at.month, expires_at.day) == (9, 5)
        # Inclusive: live all through the 5th, gone on the 6th.
        assert (expires_at.hour, expires_at.minute, expires_at.second) == (23, 59, 59)
        # Never resolved into the past — that would hide the fact on write.
        assert expires_at.date() >= today
        assert kwargs["fact_text"] == "выставка в парке"
        assert f"05.09.{expires_at.year}" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_garbled_deadline_still_saves_and_says_so(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="сдать отчёт до пятницы")

        kwargs = deps.append_kwargs
        assert kwargs["expires_at"] is None
        # The clause the parser could not honour goes back into the text verbatim.
        assert kwargs["fact_text"] == "сдать отчёт до пятницы"
        text = _reply_text(msg)
        assert "Сохранено" in text
        assert "не распознал" in text
        assert "пятницы" in text

    @pytest.mark.asyncio
    async def test_past_date_saves_without_a_deadline_and_says_so(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="разовая акция до 2020-01-05")

        kwargs = deps.append_kwargs
        assert kwargs["expires_at"] is None, "a past deadline would hide the fact on write"
        assert kwargs["fact_text"] == "разовая акция до 2020-01-05"
        text = _reply_text(msg)
        assert "Сохранено" in text
        assert "уже прошла" in text

    @pytest.mark.asyncio
    async def test_rejected_topic_still_saves_without_a_topic_and_says_so(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="#a<b>c правила чата")

        kwargs = deps.append_kwargs
        assert kwargs["topic"] is None
        # The refused token is put BACK into the text, exactly like an unparsed
        # `до …` clause. It was peeled off as a directive and then refused, so
        # those characters are ordinary words the user asked to save — deleting
        # them made a rejected topic the one degradation that cost content.
        assert kwargs["fact_text"] == "#a<b>c правила чата"
        text = _reply_text(msg)
        assert "Сохранено" in text
        assert "не принял" in text
        # …and it is echoed back escaped, not as live markup, on both paths.
        assert "a&lt;b&gt;c" in text
        assert "a<b>c" not in text

    @pytest.mark.asyncio
    async def test_a_write_failure_is_reported_instead_of_going_silent(self) -> None:
        """A Message handler has no safety net — `src/bot/errors.py` answers only callbacks.

        `append_fact` genuinely raises: it re-raises a unique violation whose
        conflicting row it cannot find, rather than reporting a save that did not
        happen. Unhandled, that reaches the user as pure silence, they retype, and
        under append-only the retry writes a second fact.
        """
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()
        deps.knowledge.append_fact = AsyncMock(side_effect=RuntimeError("pool exhausted"))

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        msg.reply.assert_awaited_once()
        text = _reply_text(msg)
        assert "Не удалось сохранить" in text
        assert "Сохранено" not in text

    @pytest.mark.asyncio
    async def test_opening_hours_keep_their_words_and_draw_no_warning(self) -> None:
        """`до 22` is not a deadline: neither a date nor a relative marker.

        Threat: an unanchored / over-eager `до <token>` rule reading `22` as
        "the 22nd" and giving an opening-hours fact a silent two-week lifespan.
        """
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="часы: работаем с 10 до 22")

        kwargs = deps.append_kwargs
        assert kwargs["expires_at"] is None
        assert kwargs["fact_text"] == "часы: работаем с 10 до 22"
        text = _reply_text(msg)
        assert "не распознал" not in text
        assert "⏳" not in text

    @pytest.mark.asyncio
    async def test_multiline_capture_is_folded_to_one_line(self) -> None:
        """A stored newline renders as a second `- ` bullet in the model's prompt.

        i.e. arbitrary user text would read to the model as another curated fact
        of the chat. KB-08 makes multi-line captures ordinary, so the shape is
        closed where the text enters.
        """
        rpl = _make_reply(text="правила:\n- не спамить\n\n- не ругаться")
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        stored = deps.append_kwargs["fact_text"]
        assert "\n" not in stored
        assert stored == "правила: - не спамить - не ругаться"

    @pytest.mark.asyncio
    async def test_a_pasted_rules_list_becomes_exactly_one_complete_fact(self) -> None:
        """Owner's decision, 2026-08-17: a pasted list is ONE fact, not N.

        KB-10 originally specified splitting a numbered body into one fact per
        line under a shared topic. The numbers said otherwise: retrieval returns
        at most `_KB_SEARCH_LIMIT` facts and the prompt budget fits 2-4 of them,
        so twelve rules split into twelve facts answer "какие у нас правила?" with
        three of them — in the confident tone of a curated fact base. Storing the
        block whole means the answer is either complete or visibly truncated,
        never quietly partial.

        Pinned as a test because "split it per line" is exactly the change someone
        would make later thinking it an improvement. If it is ever wanted, it has
        to be an explicit opt-in, and this test is where that decision surfaces.
        """
        rules = "\n".join(f"{i}. правило номер {i}" for i in range(1, 13))
        rpl = _make_reply(text=f"Правила чата:\n{rules}")
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="#правила")

        # ONE fact, not twelve.
        deps.knowledge.append_fact.assert_awaited_once()
        stored = deps.append_kwargs["fact_text"]
        # …and it is COMPLETE: every rule survives, first and last included.
        for i in range(1, 13):
            assert f"правило номер {i}" in stored, f"rule {i} was lost"
        assert deps.append_kwargs["topic"] == "правила"
        assert "\n" not in stored


# ---------------------------------------------------------------------------
# confirmation safety
# ---------------------------------------------------------------------------


class TestRememberConfirmationSafety:
    @pytest.mark.asyncio
    async def test_crossing_markdown_is_shown_verbatim_not_interpreted(self) -> None:
        """Threat: `markdown_to_html` turning captured text into crossing tags.

        Telegram rejects those, and the reply happens *after* the row is
        committed — so the user reads a successful save as a failure and retypes
        it, which under append-only is a second fact.
        """
        raw = "• Тема — *a **b* c**"
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args=raw)

        deps.knowledge.append_fact.assert_awaited_once()
        msg.reply.assert_awaited_once()
        text = _reply_text(msg)
        assert "*a **b* c**" in text
        for tag in ("<b>", "</b>", "<i>", "</i>", "<em>", "<code>"):
            assert tag not in text, f"markdown was interpreted into {tag}"
        assert msg.reply.call_args.kwargs.get("parse_mode") == "HTML"

    @pytest.mark.asyncio
    async def test_html_in_captured_text_is_escaped(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="<b>& важно")

        text = _reply_text(msg)
        assert "&lt;b&gt;" in text
        assert "&amp;" in text
        assert "<b>" not in text

    @pytest.mark.asyncio
    async def test_rejected_html_confirmation_is_retried_as_plain_text(self) -> None:
        """A committed write must never be reported as silence."""
        msg = _make_message(user_id=ORGANIZER_ID)
        msg.reply = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message="can't parse entities"),
                None,
            ]
        )
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        deps.knowledge.append_fact.assert_awaited_once()
        assert msg.reply.await_count == 2, "the user was left with no confirmation at all"
        first, second = msg.reply.await_args_list
        assert first.kwargs.get("parse_mode") == "HTML"
        assert second.kwargs.get("parse_mode") is None
        assert second.args[0] == first.args[0]
        assert "Сохранено" in second.args[0]

    @pytest.mark.asyncio
    async def test_a_rejected_plain_retry_falls_back_to_a_terse_line(self) -> None:
        """The second attempt resends the SAME long body, so length kills it too.

        Reachable from ordinary use, not from an attack: KB-08 captures a
        replied-to message in full and deliberately does not truncate, so a
        confirmation long enough for Telegram to refuse is one long quoted
        message away. The row is already committed at this point, and the failure
        mode being closed here is "the chat sees nothing and the user retypes",
        which under append-only writes a second fact.
        """
        msg = _make_message(user_id=ORGANIZER_ID)
        msg.reply = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message="can't parse entities"),
                TelegramBadRequest(method=MagicMock(), message="message is too long"),
                None,
            ]
        )
        deps = _Deps(append_result=(77, True))

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        assert msg.reply.await_count == 3, "the user was left with no confirmation at all"
        terse = msg.reply.await_args_list[2].args[0]
        assert "#77" in terse
        assert len(terse) < 60, f"the last-resort line must be short, got {len(terse)} chars"
        assert msg.reply.await_args_list[2].kwargs.get("parse_mode") is None

    @pytest.mark.asyncio
    async def test_a_very_long_capture_is_echoed_as_a_capped_preview(self) -> None:
        """The stored fact keeps its length; the message reporting it does not."""
        long_text = "деталь " * 400  # ~2800 chars
        rpl = _make_reply(text=long_text)
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=rpl)
        deps = _Deps()

        await deps.remember(msg, _make_chat_config(), args="")

        # Stored in full — that is KB-08's whole point.
        assert len(deps.append_kwargs["fact_text"]) > 2000
        # Echoed capped, so the confirmation cannot be refused for length.
        assert len(_reply_text(msg)) < 1000
        assert "…" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_confirmation_carries_an_undo_button_for_the_saver(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps(append_result=(77, True))

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        markup = msg.reply.call_args.kwargs.get("reply_markup")
        assert markup is not None
        button = markup.inline_keyboard[0][0]
        assert button.callback_data == f"kb_undo:77:{ORGANIZER_ID}"


# ---------------------------------------------------------------------------
# typing indicator (I-5)
# ---------------------------------------------------------------------------


class TestRememberTypingIndicator:
    """The embedding call must run under the shared `typing_indicator` helper,
    and `message_thread_id` must reach it (a one-shot chat action goes stale
    after ~5s; omitting the thread id routes the indicator to General).
    """

    @pytest.mark.asyncio
    async def test_wraps_embedding_generation(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        with patch("src.bot.handlers.commands.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        mock_indicator.assert_called_once_with(deps.bot, CHAT_ID, None)
        deps.knowledge.append_fact.assert_awaited_once()
        deps.ai_router.generate_embedding.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forwards_message_thread_id(self) -> None:
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps()

        with patch("src.bot.handlers.commands.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await deps.remember(
                msg, _make_chat_config(), args="место: кафе Луна", message_thread_id=777
            )

        mock_indicator.assert_called_once_with(deps.bot, CHAT_ID, 777)

    @pytest.mark.asyncio
    async def test_no_indicator_on_early_return(self) -> None:
        """A guard-clause rejection never reaches the embedding call."""
        msg = _make_message(user_id=ORGANIZER_ID, reply_to_message=None)
        deps = _Deps()

        with patch("src.bot.handlers.commands.typing_indicator") as mock_indicator:
            await deps.remember(msg, _make_chat_config(), args="")

        mock_indicator.assert_not_called()
        deps.knowledge.append_fact.assert_not_awaited()
        assert "Нечего сохранять" in _reply_text(msg)

    @pytest.mark.asyncio
    async def test_embedding_failure_still_saves_the_fact(self) -> None:
        """A provider blip costs semantic search, never the fact."""
        ai_router = MagicMock()
        ai_router.generate_embedding = AsyncMock(side_effect=RuntimeError("boom"))
        msg = _make_message(user_id=ORGANIZER_ID)
        deps = _Deps(ai_router=ai_router)

        await deps.remember(msg, _make_chat_config(), args="место: кафе Луна")

        kwargs = deps.append_kwargs
        assert kwargs["embedding"] is None
        assert kwargs["fact_text"] == "место: кафе Луна"
        text = _reply_text(msg)
        msg.reply.assert_awaited_once()
        assert "Сохранено" in text
        assert "Поиск по смыслу" in text


# ---------------------------------------------------------------------------
# /remember in a DM
# ---------------------------------------------------------------------------


class TestRememberDm:
    @pytest.mark.asyncio
    async def test_dm_explains_and_writes_nothing(self) -> None:
        """A fact keyed to the private chat's id is unreachable from any group.

        `/kb` in the same DM would list it back, so the loop looks like it
        worked — which is why this is a notice and not a write.
        """
        msg = _make_message(chat_type="private", user_id=ADMIN_ID)

        await handle_remember_dm(msg, _make_chat_config())

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "групповом чате" in text
        assert "Сохранено" not in text
        msg.reply.assert_not_awaited()


# ---------------------------------------------------------------------------
# undo button
# ---------------------------------------------------------------------------


def _make_undo_callback(
    data: str,
    *,
    presser_id: int = ORGANIZER_ID,
    chat_id: int = CHAT_ID,
    chat_type: str = "group",
) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = presser_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.id = chat_id
    callback.message.chat.type = chat_type
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = None
    return callback


async def _run_undo(
    callback: MagicMock,
    cfg: MagicMock,
    knowledge: MagicMock,
    *,
    admin_ids: list[int] | None = None,
    organizer_ids: list[int] | None = None,
) -> None:
    await handle_kb_undo(
        callback,
        cfg,
        knowledge,
        _make_bot_config_repo(admin_ids),
        _make_chat_settings_repo(organizer_ids),
    )


class TestKbUndo:
    """The project's first write-capable button in a group chat.

    Telegram lets *any* member press *any* inline button, so ownership and
    live authority are checked independently.
    """

    @pytest.mark.asyncio
    async def test_non_owner_press_is_refused_and_removes_nothing(self) -> None:
        callback = _make_undo_callback(f"kb_undo:42:{ORGANIZER_ID}", presser_id=RANDOM_USER_ID)
        knowledge = _make_knowledge_repo()

        await _run_undo(callback, _make_chat_config(), knowledge)

        knowledge.reject_fact.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()
        assert "нажимает тот, кто сохранил" in callback.answer.call_args[0][0]
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_owner_press_rejects_the_fact_and_edits_the_message(self) -> None:
        callback = _make_undo_callback(f"kb_undo:42:{ORGANIZER_ID}", presser_id=ORGANIZER_ID)
        knowledge = _make_knowledge_repo()
        knowledge.reject_fact = AsyncMock(return_value=True)

        await _run_undo(callback, _make_chat_config(), knowledge)

        knowledge.reject_fact.assert_awaited_once_with(
            42, chat_id=CHAT_ID, rejected_by=ORGANIZER_ID
        )
        callback.message.edit_text.assert_awaited_once()
        assert "Убрал" in callback.message.edit_text.call_args[0][0]
        callback.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_already_removed_fact_reports_it_without_editing(self) -> None:
        callback = _make_undo_callback(f"kb_undo:42:{ORGANIZER_ID}", presser_id=ORGANIZER_ID)
        knowledge = _make_knowledge_repo()
        knowledge.reject_fact = AsyncMock(return_value=False)

        await _run_undo(callback, _make_chat_config(), knowledge)

        knowledge.reject_fact.assert_awaited_once()
        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()
        assert "уже убран" in callback.answer.call_args[0][0]
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_owner_who_lost_authority_is_refused(self) -> None:
        """Rights can be revoked between the save and the press."""
        callback = _make_undo_callback(f"kb_undo:42:{ORGANIZER_ID}", presser_id=ORGANIZER_ID)
        knowledge = _make_knowledge_repo()

        await _run_undo(
            callback, _make_chat_config(), knowledge, admin_ids=[ADMIN_ID], organizer_ids=[]
        )

        knowledge.reject_fact.assert_not_awaited()
        callback.answer.assert_awaited_once()
        assert "только организаторы" in callback.answer.call_args[0][0]
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_disabled_kb_refuses_the_press(self) -> None:
        """The button outlives the toggle: it sits on an already-sent message."""
        callback = _make_undo_callback(f"kb_undo:42:{ORGANIZER_ID}", presser_id=ORGANIZER_ID)
        knowledge = _make_knowledge_repo()

        await _run_undo(callback, _make_chat_config(kb_enabled=False), knowledge)

        knowledge.reject_fact.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()
        assert "отключена" in callback.answer.call_args[0][0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", ["kb_undo:", "kb_undo:x:y", "kb_undo:42", "kb_undo:42:"])
    async def test_malformed_payload_answers_without_raising(self, data: str) -> None:
        callback = _make_undo_callback(data)
        knowledge = _make_knowledge_repo()

        await _run_undo(callback, _make_chat_config(), knowledge)

        knowledge.reject_fact.assert_not_awaited()
        callback.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# /kb rendering
# ---------------------------------------------------------------------------


def _make_fact(**over: Any) -> dict[str, Any]:
    fact: dict[str, Any] = {
        "id": 1,
        "subject": "место",
        "predicate": "m1001",
        "value": "Кафе Луна",
        "fact_text": "место: Кафе Луна",
        "topic": None,
        "source_user_id": None,
        "updated_at": None,
        "expires_at": None,
    }
    fact.update(over)
    return fact


def _make_facts(count: int) -> list[dict[str, Any]]:
    return [
        _make_fact(
            id=i,
            subject=f"subj{i}",
            predicate=f"m{1000 + i}",
            value=f"val{i}",
            fact_text=f"факт номер {i}",
        )
        for i in range(count)
    ]


class TestHandleKbView:
    @pytest.mark.asyncio
    async def test_group_kb_disabled_early_response_no_facts_leaked(self) -> None:
        """S2-8: kb_enabled=False must get an explicit answer, not a silent
        return -- and must not query facts at all."""
        msg = _make_message(chat_type="group")
        cfg = _make_chat_config(kb_enabled=False)
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[_make_fact(fact_text="не должно попасть в ответ: секрет")]
        )

        await handle_kb_view_group(msg, cfg, repo)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "отключена" in text
        assert "секрет" not in text
        repo.get_active_facts.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dm_kb_disabled_early_response_no_facts_leaked(self) -> None:
        """S2-8: same guard for the DM variant."""
        msg = _make_message(chat_type="private")
        cfg = _make_chat_config(kb_enabled=False)
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[_make_fact(fact_text="не должно попасть в ответ: секрет")]
        )

        await handle_kb_view_dm(msg, cfg, repo)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "отключена" in text
        assert "секрет" not in text
        repo.get_active_facts.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_empty(self) -> None:
        msg = _make_message(chat_type="group")
        repo = _make_knowledge_repo()

        await handle_kb_view_group(msg, _make_chat_config(), repo)

        msg.answer.assert_awaited_once()
        assert "пуста" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_group_renders_fact_text_and_hides_the_predicate(self) -> None:
        """S2 renders `fact_text`, the same column the model is shown.

        `predicate` now carries a generated identity (`m1001`) that means
        nothing to a reader, and a captured quote has no natural subject/value
        split — printing the derived head next to the text it came from reads as
        a duplicated sentence.
        """
        msg = _make_message(chat_type="group")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[_make_fact(fact_text="встречаемся в кафе Луна", predicate="m1001")]
        )

        await handle_kb_view_group(msg, _make_chat_config(), repo)

        text = msg.answer.call_args[0][0]
        # Positive half first: the absence assertion below cannot pass vacuously.
        assert "встречаемся в кафе Луна" in text
        assert "m1001" not in text

    @pytest.mark.asyncio
    async def test_group_escapes_html_in_facts(self) -> None:
        """Facts are raw user input and the bot's default parse_mode is HTML."""
        msg = _make_message(chat_type="group")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[_make_fact(fact_text="<b>жирный</b> & хитрый")]
        )

        await handle_kb_view_group(msg, _make_chat_config(), repo)

        text = msg.answer.call_args[0][0]
        assert "&lt;b&gt;жирный&lt;/b&gt;" in text
        assert "&amp;" in text
        assert "<b>" not in text

    @pytest.mark.asyncio
    async def test_group_renders_the_deadline(self) -> None:
        """An expiring fact must be distinguishable from a permanent one."""
        msg = _make_message(chat_type="group")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[
                _make_fact(
                    fact_text="выставка в парке",
                    expires_at=datetime(2099, 9, 5, 23, 59, 59, tzinfo=TBILISI),
                )
            ]
        )

        await handle_kb_view_group(msg, _make_chat_config(), repo)

        text = msg.answer.call_args[0][0]
        assert "выставка в парке" in text
        assert "⏳" in text
        assert "05.09.2099" in text

    @pytest.mark.asyncio
    async def test_group_caps_a_long_fact(self) -> None:
        long_text = "начало " + ("х" * 890) + " КОНЕЦ"
        assert len(long_text) > 900
        msg = _make_message(chat_type="group")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=[_make_fact(fact_text=long_text)])

        await handle_kb_view_group(msg, _make_chat_config(), repo)

        text = msg.answer.call_args[0][0]
        assert "начало" in text
        assert "…" in text
        assert "КОНЕЦ" not in text
        assert len(text) < len(long_text)

    @pytest.mark.asyncio
    async def test_group_body_stays_within_the_telegram_limit(self) -> None:
        """Threat: a rejected sendMessage is not a degraded list, it is no list.

        KB-08 makes long facts ordinary — a captured quote is verbatim message
        text — so five of them must not make `/kb` permanently unusable.
        """
        facts = [_make_fact(id=i, fact_text=f"факт {i} " + "ю" * 1500) for i in range(5)]
        msg = _make_message(chat_type="group")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=facts)

        await handle_kb_view_group(msg, _make_chat_config(), repo)

        text = msg.answer.call_args[0][0]
        assert len(text) <= 4096
        assert "факт 0" in text

    @pytest.mark.asyncio
    async def test_dm_body_stays_within_the_telegram_limit(self) -> None:
        facts = [_make_fact(id=i, fact_text=f"факт {i} " + "ю" * 1500) for i in range(5)]
        msg = _make_message(chat_type="private")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=facts)

        await handle_kb_view_dm(msg, _make_chat_config(), repo)

        html = msg.answer.call_args[0][0]
        assert len(html) <= 4096

    @pytest.mark.asyncio
    async def test_a_full_page_of_maximal_facts_never_needs_truncating(self) -> None:
        """The per-line cap, not `_fit_message`, is what keeps `/kb` deliverable.

        Worth pinning as an explicit fact rather than leaving implied: with
        `_KB_LINE_MAX_CHARS = 200` and a DM page of 5 (group: 8), a maximal page is
        roughly 2 KB — half the limit — so the length guard cannot fire through
        either renderer today. It exists for the day someone raises the page size
        or the cap; `TestFitMessage` below is what tests the guard itself.
        """
        facts = [_make_fact(id=i, fact_text=f"факт {i} " + "ю" * 900) for i in range(5)]
        msg = _make_message(chat_type="private")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=facts)

        await handle_kb_view_dm(msg, _make_chat_config(), repo)

        html = msg.answer.call_args[0][0]
        assert len(html) <= 4096
        assert "… и ещё" not in html, "the guard fired — the per-line cap no longer suffices"
        # All five facts present, each with its own provenance line.
        assert html.count("• ") == 5
        assert html.count("обновлено") == 5

    @pytest.mark.asyncio
    async def test_dm_empty(self) -> None:
        msg = _make_message(chat_type="private")
        repo = _make_knowledge_repo()

        await handle_kb_view_dm(msg, _make_chat_config(), repo)

        msg.answer.assert_awaited_once()
        assert "пуста" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_dm_sectioned_by_topic_without_the_predicate(self) -> None:
        msg = _make_message(chat_type="private")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[
                _make_fact(
                    fact_text="встречаемся в кафе Луна",
                    topic="event:лето",
                    predicate="m1001",
                )
            ]
        )

        await handle_kb_view_dm(msg, _make_chat_config(), repo)

        msg.answer.assert_awaited_once()
        html = msg.answer.call_args[0][0]
        assert "event:лето" in html
        assert "встречаемся в кафе Луна" in html
        assert "m1001" not in html


def _make_kb_view_callback(
    data: str, chat_type: str = "group", chat_id: int = CHAT_ID
) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = chat_type
    callback.message.chat.id = chat_id
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = None
    return callback


class TestUndoOutcomesAreNamedHonestly:
    """`reject_fact` returns False for three different reasons, and they differ.

    Reporting all three as "already removed" is a false statement about the data
    for two of them, and "try again" after a *successful* removal is actively
    wrong advice — retrying cannot re-remove what is gone.
    """

    @pytest.mark.asyncio
    async def test_a_fact_that_is_gone_is_not_called_already_removed(self) -> None:
        callback = _make_undo_callback(f"kb_undo:42:{ORGANIZER_ID}", presser_id=ORGANIZER_ID)
        knowledge = _make_knowledge_repo()
        knowledge.reject_fact = AsyncMock(return_value=False)
        knowledge.get_by_id = AsyncMock(return_value=None)

        await _run_undo(callback, _make_chat_config(), knowledge)

        alert = callback.answer.await_args.args[0]
        assert "больше нет" in alert
        assert "уже убран" not in alert

    @pytest.mark.asyncio
    async def test_a_superseded_fact_says_so(self) -> None:
        callback = _make_undo_callback(f"kb_undo:42:{ORGANIZER_ID}", presser_id=ORGANIZER_ID)
        knowledge = _make_knowledge_repo()
        knowledge.reject_fact = AsyncMock(return_value=False)
        knowledge.get_by_id = AsyncMock(return_value={"id": 42, "status": "superseded"})

        await _run_undo(callback, _make_chat_config(), knowledge)

        assert "заменён" in callback.answer.await_args.args[0]

    @pytest.mark.asyncio
    async def test_a_failed_edit_after_a_successful_removal_says_what_happened(self) -> None:
        """The removal is committed; only the card could not be updated."""
        callback = _make_undo_callback(f"kb_undo:42:{ORGANIZER_ID}", presser_id=ORGANIZER_ID)
        callback.message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="message can't be edited")
        )
        knowledge = _make_knowledge_repo()
        knowledge.reject_fact = AsyncMock(return_value=True)

        await _run_undo(callback, _make_chat_config(), knowledge)

        knowledge.reject_fact.assert_awaited_once()
        alert = callback.answer.await_args.args[0]
        assert "убран" in alert
        assert "не смог" in alert, "the user must learn the card above is now stale"


class TestFitMessage:
    """The length guard itself: honest counts, whole facts, parsed-length budget.

    Tested directly because it is unreachable through the renderers today (see
    `test_a_full_page_of_maximal_facts_never_needs_truncating`). An untested
    unreachable guard is how a guard stops working before anyone needs it.
    """

    def test_the_dropped_count_counts_facts_not_lines(self) -> None:
        """ "… и ещё N" names facts, because that is what a reader counts.

        The DM view renders TWO lines per fact, so counting dropped *lines*
        reported roughly double. Found by review.
        """
        blocks = [[f"• факт {i} " + "ю" * 900, f"  <i>обновлено {i}</i>"] for i in range(8)]

        body = _fit_message(blocks, "ru", header=["заголовок"], footer=[])

        match = re.search(r"… и ещё (\d+)", body)
        assert match is not None, f"expected a truncation notice, got {len(body)} chars"
        shown = body.count("• ")
        assert int(match.group(1)) == len(blocks) - shown
        # No fact was separated from its own provenance line.
        assert body.count("обновлено") == shown
        assert len(body) <= 4096

    def test_the_footer_always_survives_truncation(self) -> None:
        """The pager is what makes the rest of the list reachable."""
        blocks = [[f"• факт {i} " + "ю" * 900] for i in range(8)]

        body = _fit_message(blocks, "ru", header=["з"], footer=["◀️ 1/3 ▶️"])

        assert body.endswith("◀️ 1/3 ▶️")
        assert "… и ещё" in body

    def test_escaped_entities_are_budgeted_at_their_parsed_length(self) -> None:
        """Telegram counts the parsed text, not the HTML we send.

        200 stored `&`s become 1000 characters of `&amp;`. Budgeting on the raw
        string dropped facts from pages that would have fitted comfortably.
        """
        blocks = [["• " + "&amp;" * 400] for _ in range(4)]  # 8000 raw, 1600 parsed

        body = _fit_message(blocks, "ru", header=[], footer=[])

        assert "… и ещё" not in body, "budgeted on the raw HTML, not the parsed length"
        assert body.count("• ") == 4


class TestHandleKbViewPage:
    """Regression coverage for the previously-dead ``kb_view:`` pagination callback."""

    @pytest.mark.asyncio
    async def test_group_second_page_shows_remaining_facts(self) -> None:
        callback = _make_kb_view_callback("kb_view:ru:1", chat_type="group")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=_make_facts(10))  # 8/page -> page 1 has 2

        await handle_kb_view_page(callback, _make_chat_config(), repo)

        callback.message.edit_text.assert_awaited_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "факт номер 8" in text
        assert "факт номер 0" not in text
        assert "2/2" in text
        # The generated identity must not surface on a paginated page either.
        assert "m1008" not in text

    @pytest.mark.asyncio
    async def test_dm_second_page_renders_html(self) -> None:
        callback = _make_kb_view_callback("kb_view:ru:1", chat_type="private")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=_make_facts(7))  # 5/page -> page 1 has 2

        await handle_kb_view_page(callback, _make_chat_config(), repo)

        callback.message.edit_text.assert_awaited_once()
        kwargs = callback.message.edit_text.call_args.kwargs
        assert kwargs.get("parse_mode") == "HTML"
        text = callback.message.edit_text.call_args[0][0]
        assert "факт номер 5" in text
        assert "факт номер 0" not in text
        assert "m1005" not in text

    @pytest.mark.asyncio
    async def test_answers_without_edit_when_no_facts(self) -> None:
        callback = _make_kb_view_callback("kb_view:ru:0")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=[])

        await handle_kb_view_page(callback, _make_chat_config(), repo)

        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disabled_kb_refuses_and_leaks_no_facts(self) -> None:
        """The command handlers gate on kb_enabled, so the paginator must too.

        Its buttons live on an already-sent message and outlive the toggle: a
        chat that disabled the KB still had a fully working reader in every
        previous /kb message, DM provenance included. The repo must not even
        be queried, and the press must produce a visible refusal rather than a
        silent no-op.
        """
        callback = _make_kb_view_callback("kb_view:ru:1")
        repo = _make_knowledge_repo()

        await handle_kb_view_page(callback, _make_chat_config(kb_enabled=False), repo)

        repo.get_active_facts.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_disabled_kb_refuses_in_dm_too(self) -> None:
        callback = _make_kb_view_callback("kb_view:ru:1", chat_type="private")
        repo = _make_knowledge_repo()

        await handle_kb_view_page(callback, _make_chat_config(kb_enabled=False), repo)

        repo.get_active_facts.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()
