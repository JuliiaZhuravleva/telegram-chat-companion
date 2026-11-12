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

from src.bot.handlers.callbacks import handle_close_callback, handle_summary_callback

OWNER_ID = 111


def _make_chat_config(*, save_messages: bool, language: str = "ru") -> MagicMock:
    cfg = MagicMock()
    cfg.language = language
    cfg.save_messages = save_messages
    return cfg


def _placeholder() -> MagicMock:
    """The "⏳" message ProgressNotice posts and later edits into the summary."""
    placeholder = MagicMock(spec=Message)
    placeholder.edit_text = AsyncMock()
    placeholder.delete = AsyncMock()
    placeholder.chat = MagicMock()
    placeholder.chat.id = -100123
    placeholder.message_id = 501
    return placeholder


def _make_callback(*, user_id: int = OWNER_ID, count: int = 100) -> MagicMock:
    callback = MagicMock()
    callback.data = f"help_summary:{OWNER_ID}:{count}"
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.id = -100123
    # `spec=Message` does not expose pydantic field names, so message_id has to
    # be set explicitly. The handler keys its record of appended continuation
    # messages on it, and a bare spec'd mock raises AttributeError instead.
    callback.message.message_id = 500
    callback.message.answer = AsyncMock(return_value=_placeholder())
    callback.message.reply_to_message = None
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


# ── A press posts a NEW summary ───────────────────────────────────────
#
# The button sits on /help (or under a /summary refusal). It used to edit that
# message into the summary and track the continuation parts it appended so a
# later refresh could delete them; a new message per press needs none of that,
# and the four tests of the tracking went with it.


def _summary_service(text: str | None) -> MagicMock:
    service = MagicMock()
    service.generate = AsyncMock(return_value=text)
    return service


@pytest.mark.asyncio
async def test_a_press_posts_a_new_message_and_leaves_the_help_alone() -> None:
    callback = _make_callback(count=500)
    callback.message.edit_text = AsyncMock()
    service = _summary_service("<b>Кратко</b>")

    await handle_summary_callback(callback, _make_chat_config(save_messages=True), service)

    callback.message.edit_text.assert_not_awaited()
    placeholder = callback.message.answer.return_value
    placeholder.edit_text.assert_awaited()
    assert placeholder.edit_text.await_args.args[0] == "<b>Кратко</b>"
    assert service.generate.await_args.kwargs["count"] == 500


@pytest.mark.asyncio
async def test_the_count_is_clamped_to_the_command_range() -> None:
    callback = _make_callback(count=5)
    service = _summary_service("<b>x</b>")

    await handle_summary_callback(callback, _make_chat_config(save_messages=True), service)

    assert service.generate.await_args.kwargs["count"] == 20


@pytest.mark.asyncio
async def test_a_failed_summary_says_so_in_the_placeholder() -> None:
    callback = _make_callback()

    await handle_summary_callback(
        callback, _make_chat_config(save_messages=True), _summary_service(None)
    )

    placeholder = callback.message.answer.return_value
    texts = [c.args[0] for c in placeholder.edit_text.await_args_list]
    assert any("Не удалось" in t for t in texts), texts


@pytest.mark.parametrize("data", ["help_summary:abc:100", "help_summary:111", "help_close:x"])
@pytest.mark.asyncio
async def test_an_unreadable_payload_is_an_old_button_in_the_chat_language(data: str) -> None:
    callback = _make_callback()
    callback.data = data
    service = _summary_service("<b>x</b>")
    cfg = _make_chat_config(save_messages=True, language="en")

    if data.startswith("help_close:"):
        await handle_close_callback(callback, cfg)
    else:
        await handle_summary_callback(callback, cfg, service)

    service.generate.assert_not_awaited()
    text = callback.answer.await_args.args[0]
    assert "out of date" in text
    assert "Invalid" not in text


@pytest.mark.asyncio
async def test_close_deletes_the_help_message() -> None:
    callback = _make_callback()
    callback.data = f"help_close:{OWNER_ID}"
    callback.message.delete = AsyncMock()

    await handle_close_callback(callback, _make_chat_config(save_messages=True))

    callback.message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_by_someone_else_is_refused() -> None:
    callback = _make_callback(user_id=999)
    callback.data = f"help_close:{OWNER_ID}"
    callback.message.delete = AsyncMock()

    await handle_close_callback(callback, _make_chat_config(save_messages=True))

    callback.message.delete.assert_not_awaited()
    assert "не для вас" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_a_second_tap_while_the_first_runs_starts_no_second_summary() -> None:
    """A summary is a paid call that takes seconds; a double tap paid twice."""
    import asyncio

    release = asyncio.Event()
    service = MagicMock()

    async def _slow(*_args: object, **_kwargs: object) -> str:
        await release.wait()
        return "<b>x</b>"

    service.generate = AsyncMock(side_effect=_slow)
    config = _make_chat_config(save_messages=True)
    first, second = _make_callback(count=100), _make_callback(count=500)

    running = asyncio.create_task(handle_summary_callback(first, config, service))
    await asyncio.sleep(0)  # let the first press reach generate()
    try:
        # Bounded: without the guard the second press waits on the same
        # unreleased model call, and the test must fail rather than hang.
        await asyncio.wait_for(handle_summary_callback(second, config, service), timeout=2)
    finally:
        release.set()
        await running

    assert service.generate.await_count == 1
    second.answer.assert_awaited_once_with("Уже готовлю саммари…")
    second.message.answer.assert_not_awaited()

    # Once it is done the button works again -- the claim is released.
    third = _make_callback()
    await handle_summary_callback(third, config, _summary_service("<b>y</b>"))
    third.message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_a_summary_that_raises_releases_the_button() -> None:
    service = MagicMock()
    service.generate = AsyncMock(side_effect=RuntimeError("model down"))
    config = _make_chat_config(save_messages=True)

    with pytest.raises(RuntimeError):
        await handle_summary_callback(_make_callback(), config, service)

    again = _summary_service("<b>x</b>")
    await handle_summary_callback(_make_callback(), config, again)
    again.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_summary_quotes_the_card_its_button_sits_on() -> None:
    """Quoted, the summary stays in the card's thread.

    Under a channel post the card sits in the comments; an unquoted summary
    landed in the discussion group's main feed instead.
    """
    callback = _make_callback()
    service = _summary_service("<b>Кратко</b>")

    await handle_summary_callback(callback, _make_chat_config(save_messages=True), service)

    assert callback.message.answer.await_args.kwargs.get("reply_to_message_id") == 500
