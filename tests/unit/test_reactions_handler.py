"""Tests for src.bot.handlers.reactions — handle_message_reaction (R-1, ADR-0004)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import ReactionTypeEmoji

from src.bot.handlers.reactions import handle_message_reaction
from src.models.chat_config import ChatConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emoji(e: str) -> ReactionTypeEmoji:
    return ReactionTypeEmoji(emoji=e)


def _make_event(
    *,
    chat_id: int = -1001,
    message_id: int = 42,
    old=(),
    new=(),
    user_id: int | None = 7,
    actor_chat_id: int | None = None,
) -> MagicMock:
    event = MagicMock()
    event.chat = MagicMock()
    event.chat.id = chat_id
    event.message_id = message_id
    event.old_reaction = list(old)
    event.new_reaction = list(new)

    if user_id is not None:
        event.user = MagicMock()
        event.user.id = user_id
    else:
        event.user = None

    if actor_chat_id is not None:
        event.actor_chat = MagicMock()
        event.actor_chat.id = actor_chat_id
    else:
        event.actor_chat = None

    return event


def _config(
    *,
    reactions_enabled: bool,
    reactions_history_enabled: bool = True,
    enabled: bool = True,
) -> ChatConfig:
    """Build a ChatConfig for a whitelisted chat by default.

    `enabled` defaults to False on ChatConfig itself (default-deny), but every
    reaction that reaches this handler in production comes from a chat the
    owner approved, so the tests model that and flip it explicitly to cover the
    de-whitelisted case.
    """
    return ChatConfig(
        chat_id=-1001,
        enabled=enabled,
        reactions_enabled=reactions_enabled,
        reactions_history_enabled=reactions_history_enabled,
    )


def _make_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.insert_events = AsyncMock()
    return repo


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


class TestGating:
    @pytest.mark.asyncio
    async def test_dewhitelisted_chat_records_nothing(self) -> None:
        """The whitelist master switch must stop the behavioural trail too.

        `AccessControlMiddleware` is not registered on `dp.message_reaction`,
        and registering it would silently gate nothing (its
        `_extract_event_info` only understands Message/CallbackQuery), so this
        handler owns the check. Regression guard: removing a chat from the
        whitelist sets only `enabled=False` and never clears
        `reactions_enabled`, so without this the bot keeps logging who reacted
        to what in a chat the owner switched off.
        """
        repo = _make_repo()
        event = _make_event(old=[], new=[_emoji("👍")])

        await handle_message_reaction(event, _config(enabled=False, reactions_enabled=True), repo)

        repo.insert_events.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_module_disabled_skips_everything(self) -> None:
        """reactions_enabled gates everything -- module off for this chat."""
        repo = _make_repo()
        event = _make_event(old=[], new=[_emoji("👍")])

        await handle_message_reaction(event, _config(reactions_enabled=False), repo)

        repo.insert_events.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_diff_skips_insert(self) -> None:
        """Same reaction set before/after -- nothing changed, no DB call."""
        repo = _make_repo()
        event = _make_event(old=[_emoji("👍")], new=[_emoji("👍")])

        await handle_message_reaction(event, _config(reactions_enabled=True), repo)

        repo.insert_events.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_history_disabled_skips_insert_but_does_not_raise(self) -> None:
        """reactions_history_enabled gates ONLY the INSERT -- the module stays
        on (e.g. for R-5's bot-initiated reactions), it just stops logging."""
        repo = _make_repo()
        event = _make_event(old=[], new=[_emoji("👍")])

        await handle_message_reaction(
            event,
            _config(reactions_enabled=True, reactions_history_enabled=False),
            repo,
        )

        repo.insert_events.assert_not_awaited()


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class TestRecording:
    @pytest.mark.asyncio
    async def test_records_added_reaction(self) -> None:
        repo = _make_repo()
        event = _make_event(chat_id=-999, message_id=5, old=[], new=[_emoji("🔥")], user_id=10)

        await handle_message_reaction(event, _config(reactions_enabled=True), repo)

        repo.insert_events.assert_awaited_once()
        kwargs = repo.insert_events.await_args.kwargs
        assert kwargs["chat_id"] == -999
        assert kwargs["message_id"] == 5
        assert kwargs["user_id"] == 10
        assert kwargs["actor_chat_id"] is None
        assert [e.emoji for e in kwargs["events"]] == ["🔥"]

    @pytest.mark.asyncio
    async def test_anonymous_reactor_passes_actor_chat_id_and_no_user_id(self) -> None:
        """user is None, actor_chat carries the anonymous-reactor chat."""
        repo = _make_repo()
        event = _make_event(
            old=[],
            new=[_emoji("👍")],
            user_id=None,
            actor_chat_id=-100555,
        )

        await handle_message_reaction(event, _config(reactions_enabled=True), repo)

        kwargs = repo.insert_events.await_args.kwargs
        assert kwargs["user_id"] is None
        assert kwargs["actor_chat_id"] == -100555

    @pytest.mark.asyncio
    async def test_repository_failure_does_not_raise(self) -> None:
        repo = _make_repo()
        repo.insert_events = AsyncMock(side_effect=RuntimeError("db down"))
        event = _make_event(old=[], new=[_emoji("👍")])

        # Must not propagate -- a DB hiccup on one reaction must not crash the
        # dispatcher / drop the update as unhandled.
        await handle_message_reaction(event, _config(reactions_enabled=True), repo)
