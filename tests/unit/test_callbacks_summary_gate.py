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


# ── Multi-part refreshes ──────────────────────────────────────────────


def _refreshable_callback(sent_ids, deleted: list[int]) -> MagicMock:
    """A summary button whose anchor records its edits, sends and deletions."""
    callback = _make_callback()
    msg = callback.message
    msg.edit_text = AsyncMock()
    msg.answer = AsyncMock(side_effect=lambda *_a, **_kw: MagicMock(message_id=next(sent_ids)))
    msg.bot = MagicMock()
    msg.bot.delete_message = AsyncMock(side_effect=lambda _chat, mid: deleted.append(mid))
    return callback


def _summary_service(text: str) -> MagicMock:
    service = MagicMock()
    service.generate = AsyncMock(return_value=text)
    return service


_LONG_SUMMARY = "пункт обсуждения " * 900  # comfortably over 4096 units


@pytest.mark.asyncio
async def test_the_keyboard_stays_on_the_anchor_of_a_multi_part_summary() -> None:
    """A continuation that fails to send must not leave the summary buttonless.

    The keyboard used to ride on the LAST part, and the anchor's own keyboard
    was cleared to make room for it — so a single failed `answer` (flood
    control, a network blip; FloodControlMiddleware absorbs only <=30s waits, and only three of them) left
    the chat with a summary carrying no refresh, no count switch and no close.
    """
    from src.bot.handlers.callbacks import _CONTINUATIONS

    _CONTINUATIONS.clear()
    deleted: list[int] = []
    callback = _refreshable_callback(iter([601, 602, 603]), deleted)
    callback.message.answer = AsyncMock(side_effect=RuntimeError("flood"))

    await handle_summary_callback(
        callback, _make_chat_config(save_messages=True), _summary_service(_LONG_SUMMARY)
    )

    assert callback.message.edit_text.await_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_a_second_refresh_removes_what_the_first_appended() -> None:
    """Otherwise every press strands another orphaned tail above the new summary.

    The anchor is edited in place, so a refresh cannot overwrite the parts the
    previous one posted below it. Driving the handler twice used to leave
    [old p1][old p2][new p1][new p2] with the reader's eye landing on stale
    text; only the ids we remember can be cleaned up.
    """
    from src.bot.handlers.callbacks import _CONTINUATIONS

    _CONTINUATIONS.clear()
    sent_ids = iter(range(601, 620))
    cfg = _make_chat_config(save_messages=True)

    deleted_first: list[int] = []
    first = _refreshable_callback(sent_ids, deleted_first)
    await handle_summary_callback(first, cfg, _summary_service(_LONG_SUMMARY))
    appended = [c.args[0] for c in first.message.answer.await_args_list]
    assert appended, "this fixture must produce a multi-part summary"
    assert deleted_first == [], "nothing existed to clean up on the first refresh"

    deleted_second: list[int] = []
    second = _refreshable_callback(sent_ids, deleted_second)
    await handle_summary_callback(second, cfg, _summary_service(_LONG_SUMMARY))

    assert deleted_second == [601 + i for i in range(len(appended))], (
        "the second refresh must delete exactly what the first appended"
    )


@pytest.mark.asyncio
async def test_a_summary_that_shrinks_to_one_message_leaves_no_tail() -> None:
    """The regression the tracking exists for, in its most visible form."""
    from src.bot.handlers.callbacks import _CONTINUATIONS

    _CONTINUATIONS.clear()
    sent_ids = iter(range(601, 620))
    cfg = _make_chat_config(save_messages=True)

    long_call = _refreshable_callback(sent_ids, [])
    await handle_summary_callback(long_call, cfg, _summary_service(_LONG_SUMMARY))

    deleted: list[int] = []
    short_call = _refreshable_callback(sent_ids, deleted)
    await handle_summary_callback(short_call, cfg, _summary_service("<b>Кратко</b>"))

    assert deleted, "the old continuations must go when the new summary fits in one message"
    short_call.message.answer.assert_not_awaited()
    assert not _CONTINUATIONS, "nothing left to track once the summary is one message"


@pytest.mark.asyncio
async def test_closing_a_multi_part_summary_removes_its_tail_too() -> None:
    """Only the anchor carries the Close button.

    Deleting it alone left the continuation messages in the chat with nothing
    that could ever remove them — and stranded the tracking entry, pointing at
    a message that no longer exists, so even a later refresh could not clean it
    up. In a group that is permanent litter nobody can clear.
    """
    from src.bot.handlers.callbacks import _CONTINUATIONS

    _CONTINUATIONS.clear()
    sent_ids = iter(range(601, 620))
    cfg = _make_chat_config(save_messages=True)

    posted = _refreshable_callback(sent_ids, [])
    await handle_summary_callback(posted, cfg, _summary_service(_LONG_SUMMARY))
    appended = len(posted.message.answer.await_args_list)
    assert appended > 0, "this fixture must produce a multi-part summary"

    deleted: list[int] = []
    closing = _refreshable_callback(iter(range(700, 710)), deleted)
    closing.data = f"help_close:{OWNER_ID}"
    closing.message.delete = AsyncMock()

    await handle_close_callback(closing, cfg)

    assert deleted == [601 + i for i in range(appended)], (
        "the continuations must be deleted along with the anchor"
    )
    closing.message.delete.assert_awaited_once()
    assert not _CONTINUATIONS
