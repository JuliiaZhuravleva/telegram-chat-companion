"""The summary button must honour the chat's `save_messages` toggle.

`handle_summary` (command path) refuses when a chat has message saving turned
off. `handle_summary_callback` reaches the same `SummaryService` but did not
check, and its button is rendered by `/help` and by the summary's own
navigation keyboard — both of which sit on already-sent messages and outlive
the toggle. Disabling `save_messages` (a privacy choice) therefore left every
pre-existing button able to summarize history saved before the flip, for any
group member who owns the button.

Found by the variant sweep during review of the rag-s2-hygiene branch; the
handler predates that branch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from src.bot.handlers.callbacks import handle_summary_callback

OWNER_ID = 111


def _make_chat_config(*, save_messages: bool, language: str = "ru") -> MagicMock:
    cfg = MagicMock()
    cfg.language = language
    cfg.save_messages = save_messages
    return cfg


def _make_callback(*, user_id: int = OWNER_ID, count: int = 100) -> MagicMock:
    callback = MagicMock()
    callback.data = f"help_summary:{OWNER_ID}:{count}"
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.id = -100123
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


@pytest.mark.asyncio
async def test_save_messages_disabled_refuses_without_generating() -> None:
    callback = _make_callback()
    summary_service = AsyncMock()

    await handle_summary_callback(callback, _make_chat_config(save_messages=False), summary_service)

    summary_service.generate.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert "отключено" in callback.answer.call_args[0][0]
    assert callback.answer.call_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_save_messages_disabled_refuses_in_english() -> None:
    callback = _make_callback()
    summary_service = AsyncMock()

    await handle_summary_callback(
        callback, _make_chat_config(save_messages=False, language="en"), summary_service
    )

    summary_service.generate.assert_not_awaited()
    assert "disabled" in callback.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_save_messages_enabled_still_generates() -> None:
    """False-positive control: the guard must not block the normal path."""
    callback = _make_callback()
    summary_service = AsyncMock()
    summary_service.generate.return_value = "<b>summary</b>"

    await handle_summary_callback(callback, _make_chat_config(save_messages=True), summary_service)

    summary_service.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_thread_id_comes_from_middleware_not_the_bot_message() -> None:
    """TD-102's one reachable variant: the bot's own message carries the
    thread id Telegram stamps on linked-channel discussion comments, so
    reading it raw narrowed the refresh to ~2 messages while /summary next to
    it covered the whole chat. The handler must use TopicMiddleware's kwarg
    (None unless the chat is a real forum), never msg.message_thread_id."""
    callback = _make_callback()
    callback.message.message_thread_id = 777  # what the raw read would grab
    summary_service = AsyncMock()
    summary_service.generate.return_value = "<b>summary</b>"

    await handle_summary_callback(
        callback,
        _make_chat_config(save_messages=True),
        summary_service,
        message_thread_id=None,
    )

    assert summary_service.generate.call_args.kwargs["message_thread_id"] is None


@pytest.mark.asyncio
async def test_forum_thread_id_from_middleware_is_honored() -> None:
    """The mirror control: a real forum topic (middleware passes the id
    through) must still get a topic-scoped summary."""
    callback = _make_callback()
    summary_service = AsyncMock()
    summary_service.generate.return_value = "<b>summary</b>"

    await handle_summary_callback(
        callback,
        _make_chat_config(save_messages=True),
        summary_service,
        message_thread_id=555,
    )

    assert summary_service.generate.call_args.kwargs["message_thread_id"] == 555


@pytest.mark.asyncio
async def test_non_owner_is_refused_before_the_toggle_is_consulted() -> None:
    """Ordering: a stranger keeps getting "not your button" rather than being
    told about the chat's configuration."""
    callback = _make_callback(user_id=999)
    summary_service = AsyncMock()

    await handle_summary_callback(callback, _make_chat_config(save_messages=False), summary_service)

    summary_service.generate.assert_not_awaited()
    assert "не для вас" in callback.answer.call_args[0][0]
