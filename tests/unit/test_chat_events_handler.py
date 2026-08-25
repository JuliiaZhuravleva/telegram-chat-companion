"""Tests for src.bot.handlers.chat_events — message edits + bot membership changes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.access_requests import SubmitResult
from src.bot.handlers.chat_events import (
    _GONE_STATUSES,
    _PRESENT_STATUSES,
    handle_edited_message,
    handle_my_chat_member,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_member_update(
    status: str,
    *,
    chat_id: int = -1001,
    title: str | None = "Test chat",
    chat_type: str = "supergroup",
    old_status: str = "left",
) -> MagicMock:
    event = MagicMock()
    event.chat = MagicMock()
    event.chat.id = chat_id
    event.chat.title = title
    event.chat.full_name = title
    event.chat.type = chat_type
    event.chat.username = None
    event.new_chat_member = MagicMock()
    event.new_chat_member.status = status
    # `old_status` decides whether this is a JOIN or a status change within the
    # chat. It defaults to "left" so the common case reads as a fresh add.
    event.old_chat_member = MagicMock()
    event.old_chat_member.status = old_status
    event.from_user = MagicMock()
    event.from_user.id = 500001
    event.from_user.first_name = "Adder"
    event.from_user.last_name = None
    event.from_user.username = "adder"
    return event


def _make_repos(*, enabled: bool = False) -> tuple[AsyncMock, MagicMock]:
    repo = AsyncMock()
    repo.set_field = AsyncMock()
    # `ensure_exists` really does return the row (SELECT * -> dict), and the
    # handler now reads `enabled` off it. An AsyncMock's default return is a
    # Mock whose .get() is truthy, which would make every chat look enabled.
    repo.ensure_exists = AsyncMock(return_value={"chat_id": -1001, "enabled": enabled})
    config_service = MagicMock()
    config_service.invalidate = MagicMock()
    return repo, config_service


def _make_access_requests() -> AsyncMock:
    service = AsyncMock()
    service.submit = AsyncMock(return_value=SubmitResult(attempt_id=7, notified=True))
    return service


# ---------------------------------------------------------------------------
# edited_message
# ---------------------------------------------------------------------------


class TestEditedMessage:
    @pytest.mark.asyncio
    async def test_handler_accepts_edit_without_error(self, make_message) -> None:
        """The persistence itself is MessageSaverMiddleware's job; this handler
        only has to exist and match so the inner middleware chain runs."""
        await handle_edited_message(make_message(text="edited text"))

    @pytest.mark.asyncio
    async def test_handler_tolerates_missing_from_user(self, make_message) -> None:
        message = make_message(text="edited")
        message.from_user = None
        await handle_edited_message(message)


# ---------------------------------------------------------------------------
# my_chat_member
# ---------------------------------------------------------------------------


class TestBotRemoved:
    @pytest.mark.parametrize("status", sorted(_GONE_STATUSES))
    @pytest.mark.asyncio
    async def test_removal_disables_chat(self, status: str) -> None:
        repo, config_service = _make_repos()
        await handle_my_chat_member(
            _make_member_update(status), repo, config_service, _make_access_requests(), MagicMock()
        )

        repo.set_field.assert_awaited_once_with(-1001, "enabled", False)
        repo.ensure_exists.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_removal_invalidates_cached_config(self) -> None:
        """ChatConfig is cached for 60s — without invalidation the bot would keep
        treating the chat as enabled for up to a minute after being kicked."""
        repo, config_service = _make_repos()
        await handle_my_chat_member(
            _make_member_update("kicked"),
            repo,
            config_service,
            _make_access_requests(),
            MagicMock(),
        )

        config_service.invalidate.assert_called_once_with(-1001)

    @pytest.mark.asyncio
    async def test_db_failure_does_not_raise_and_skips_invalidation(self) -> None:
        repo, config_service = _make_repos()
        repo.set_field = AsyncMock(side_effect=RuntimeError("db down"))

        await handle_my_chat_member(
            _make_member_update("left"), repo, config_service, _make_access_requests(), MagicMock()
        )

        config_service.invalidate.assert_not_called()


class TestBotAdded:
    @pytest.mark.parametrize("status", sorted(_PRESENT_STATUSES))
    @pytest.mark.asyncio
    async def test_addition_records_metadata(self, status: str) -> None:
        repo, config_service = _make_repos()
        await handle_my_chat_member(
            _make_member_update(status), repo, config_service, _make_access_requests(), MagicMock()
        )

        repo.ensure_exists.assert_awaited_once_with(-1001, "Test chat", "supergroup")

    @pytest.mark.parametrize("status", sorted(_PRESENT_STATUSES))
    @pytest.mark.asyncio
    async def test_addition_never_enables_the_chat(self, status: str) -> None:
        """Access stays opt-in: being added must not whitelist the chat, or
        anyone could enable the bot by adding it to their group."""
        repo, config_service = _make_repos()
        await handle_my_chat_member(
            _make_member_update(status), repo, config_service, _make_access_requests(), MagicMock()
        )

        repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_full_name_when_title_missing(self) -> None:
        repo, config_service = _make_repos()
        event = _make_member_update("member", title=None, chat_type="private")
        event.chat.full_name = "Jane Doe"

        await handle_my_chat_member(
            event, repo, config_service, _make_access_requests(), MagicMock()
        )

        repo.ensure_exists.assert_awaited_once_with(-1001, "Jane Doe", "private")

    @pytest.mark.asyncio
    async def test_db_failure_does_not_raise(self) -> None:
        repo, config_service = _make_repos()
        repo.ensure_exists = AsyncMock(side_effect=RuntimeError("db down"))

        await handle_my_chat_member(
            _make_member_update("member"),
            repo,
            config_service,
            _make_access_requests(),
            MagicMock(),
        )


class TestUnknownStatus:
    @pytest.mark.asyncio
    async def test_unhandled_status_touches_nothing(self) -> None:
        repo, config_service = _make_repos()
        await handle_my_chat_member(
            _make_member_update("banana"),
            repo,
            config_service,
            _make_access_requests(),
            MagicMock(),
        )

        repo.set_field.assert_not_awaited()
        repo.ensure_exists.assert_not_awaited()
        config_service.invalidate.assert_not_called()
