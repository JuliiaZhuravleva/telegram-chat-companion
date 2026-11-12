"""Which answers quote their message, decided on real aiogram ``Message`` objects.

A comment under a channel post lives in the channel's discussion group, and the
only thing that keeps a bot message under that post is a reply link: the Bot
API's ``message_thread_id`` is for forum topics, and aiogram's
``Message.answer`` passes it only when ``is_topic_message``. Unquoted, the
answer lands in the group's main feed and readers of the post never see it.

Built from real ``Message`` objects rather than MagicMocks on purpose: a bare
MagicMock attribute is truthy and not None, so ``reply_to_message`` and
``message_thread_id`` would read as "in a thread" for every mock.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message, User

from src.bot.reply_flow import answer_quote_target, in_reply_thread
from src.bot.screens.render import show_markup
from src.models.enums import TriggerType

_NOW = datetime(2026, 11, 12, tzinfo=UTC)
_GROUP = Chat(id=-1001, type="supergroup", title="g")
_FORUM = Chat(id=-1002, type="supergroup", title="f", is_forum=True)
_USER = User(id=7, is_bot=False, first_name="A")


def _msg(
    chat: Chat = _GROUP, *, reply_to: Message | None = None, thread: int | None = None
) -> Message:
    return Message(
        message_id=42,
        date=_NOW,
        chat=chat,
        from_user=_USER,
        text="hi",
        reply_to_message=reply_to,
        message_thread_id=thread,
    )


# The channel post as it appears in the discussion group: an automatic forward.
_POST = Message(message_id=10, date=_NOW, chat=_GROUP, text="post", is_automatic_forward=True)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param(_msg(), False, id="open-feed"),
        pytest.param(_msg(reply_to=_POST, thread=10), True, id="comment-under-post"),
        pytest.param(_msg(reply_to=_POST), True, id="reply-chain"),
        # The replied-to message can be missing from the update (too old, or
        # deleted) while Telegram still stamps the thread id.
        pytest.param(_msg(thread=10), True, id="thread-id-without-reply-object"),
        # In a forum `answer()` routes into the topic itself; quoting is not needed.
        pytest.param(_msg(_FORUM, reply_to=_POST, thread=10), False, id="forum-topic"),
    ],
)
def test_in_reply_thread(message: Message, expected: bool) -> None:
    assert in_reply_thread(message) is expected


@pytest.mark.parametrize(
    ("message", "trigger", "expected"),
    [
        # Unchanged: an unsolicited answer in the open feed stays unquoted.
        pytest.param(_msg(), TriggerType.RANDOM, None, id="random-open-feed"),
        # The fix: under a post it quotes, or it would leave the comments.
        pytest.param(_msg(reply_to=_POST, thread=10), TriggerType.RANDOM, 42, id="random-comment"),
        pytest.param(_msg(), TriggerType.TRIGGER, 42, id="trigger-always"),
        pytest.param(_msg(reply_to=_POST), TriggerType.REPLY, 42, id="reply-always"),
        pytest.param(_msg(_FORUM, thread=10), TriggerType.RANDOM, None, id="random-forum"),
    ],
)
def test_answer_quote_target(message: Message, trigger: TriggerType, expected: int | None) -> None:
    assert answer_quote_target(message, trigger) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_type", "expected"),
    [("supergroup", 42), ("group", 42), ("private", None)],
)
async def test_a_screen_answering_a_command_quotes_it_in_groups_only(
    chat_type: str, expected: int | None
) -> None:
    """``show()`` on a command: quoted in a group (it stays under the command and,
    in a discussion group, under the post); a DM has nothing to keep apart."""
    msg = MagicMock(spec=Message)
    msg.chat = MagicMock()
    msg.chat.id = -1001
    msg.chat.type = chat_type
    msg.message_id = 42
    msg.answer = AsyncMock(return_value=MagicMock(spec=Message))

    await show_markup(msg, "screen", None, screen_id="t")

    assert msg.answer.await_args.kwargs.get("reply_to_message_id") == expected
