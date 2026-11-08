"""`show()`: the one delivery path of every redesigned screen.

Each test names the Telegram situation it stands for. The doubles are
``MagicMock(spec=Message)`` messages with ``AsyncMock`` send methods, so an
assertion on ``edit_text`` / ``answer`` is an assertion on the request that
would have gone to Telegram.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)

from src.bot.screens.model import Button, ReplyButton, Screen
from src.bot.screens.render import (
    back,
    cancel,
    page_slice,
    pager,
    render_text,
    show,
    to_markup,
    with_notice,
)
from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT, parsed_length
from tests.unit.test_custom_emoji import _buttons, _on, _send

TITLE = "🧪 <b>Проба</b>"


def _screen(**kwargs: Any) -> Screen:
    rows = kwargs.pop("rows", ((back("hub", "adm_menu:ru", "ru"),),))
    return Screen(id="probe", text=kwargs.pop("text", TITLE), rows=rows, **kwargs)


def _message() -> MagicMock:
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=MagicMock(spec=Message))
    message.chat = MagicMock()
    message.chat.id = -100
    return message


def _callback(anchor: Any = None) -> MagicMock:
    callback = MagicMock()
    callback.data = "adm_menu:ru"
    callback.answer = AsyncMock()
    callback.message = _message() if anchor is None else anchor
    return callback


def _not_modified() -> TelegramBadRequest:
    return TelegramBadRequest(
        method=MagicMock(), message="Bad Request: message is not modified: specified new message"
    )


def _cannot_edit() -> TelegramBadRequest:
    return TelegramBadRequest(
        method=MagicMock(), message="Bad Request: there is no text in the message to edit"
    )


# ---------------------------------------------------------------------------
# Edit in place, or a new message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_button_edits_its_own_message():
    callback = _callback()
    result = await show(callback, _screen())

    anchor = callback.message
    anchor.edit_text.assert_awaited_once()
    args, kwargs = anchor.edit_text.await_args
    assert args == (TITLE,)
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)
    assert kwargs["parse_mode"] == "HTML"
    anchor.answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with()
    assert result is anchor


@pytest.mark.asyncio
async def test_a_double_tap_is_not_an_error():
    """A refused edit saying "message is not modified" is the same screen: no new message."""
    callback = _callback()
    callback.message.edit_text.side_effect = _not_modified()
    await show(callback, _screen())
    callback.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_edit_telegram_refuses_falls_back_to_a_new_message():
    """The button sits under a sticker or a photo: there is no text to edit."""
    callback = _callback()
    callback.message.edit_text.side_effect = _cannot_edit()
    await show(callback, _screen())

    callback.message.answer.assert_awaited_once()
    args, kwargs = callback.message.answer.await_args
    assert args == (TITLE,)
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_inaccessible_message_gets_a_new_one():
    anchor = MagicMock(spec=InaccessibleMessage)
    anchor.answer = AsyncMock()
    callback = _callback(anchor)
    await show(callback, _screen())
    anchor.answer.assert_awaited_once()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_command_sends_a_new_message():
    message = _message()
    await show(message, _screen())
    message.answer.assert_awaited_once()
    message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_message_on_request():
    callback = _callback()
    await show(callback, _screen(), new_message=True)
    callback.message.edit_text.assert_not_awaited()
    callback.message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_reply_keyboard_is_always_a_new_message():
    """A reply keyboard cannot be edited into an existing message."""
    callback = _callback()
    screen = _screen(rows=(), reply=((ReplyButton("✖️ Отмена"),),))
    await show(callback, screen)
    callback.message.edit_text.assert_not_awaited()
    markup = callback.message.answer.await_args.kwargs["reply_markup"]
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert markup.one_time_keyboard and markup.resize_keyboard


@pytest.mark.asyncio
async def test_removing_a_reply_keyboard_is_a_new_message():
    callback = _callback()
    await show(callback, _screen(rows=(), remove_reply=True))
    callback.message.edit_text.assert_not_awaited()
    markup = callback.message.answer.await_args.kwargs["reply_markup"]
    assert isinstance(markup, ReplyKeyboardRemove)


# ---------------------------------------------------------------------------
# The callback query is answered exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toast():
    callback = _callback()
    await show(callback, _screen(), toast="Сохранено")
    callback.answer.assert_awaited_once_with("Сохранено", show_alert=False)


@pytest.mark.asyncio
async def test_alert():
    callback = _callback()
    await show(callback, _screen(), toast="Нельзя", alert=True)
    callback.answer.assert_awaited_once_with("Нельзя", show_alert=True)


@pytest.mark.asyncio
async def test_answered_means_not_answered_again():
    callback = _callback()
    await show(callback, _screen(), answered=True)
    callback.answer.assert_not_awaited()
    callback.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_answered_once_even_when_the_edit_falls_back():
    callback = _callback()
    callback.message.edit_text.side_effect = _cannot_edit()
    await show(callback, _screen(), toast="Готово")
    callback.answer.assert_awaited_once_with("Готово", show_alert=False)


@pytest.mark.asyncio
async def test_an_inline_query_button_has_no_message():
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = None
    with patch("src.bot.screens.render._is_callback", return_value=True):
        assert await show(callback, _screen()) is None
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True], ids=["edit", "new message"])
async def test_the_answer_comes_after_the_screen(fallback: bool):
    """The toast is the last thing the tap does: the screen is already there."""
    callback = _callback()
    order = MagicMock()
    order.attach_mock(callback.message.edit_text, "edit")
    order.attach_mock(callback.message.answer, "send")
    order.attach_mock(callback.answer, "answer")
    if fallback:
        callback.message.edit_text.side_effect = _cannot_edit()
    await show(callback, _screen(), toast="Сохранено")
    names = [name for name, *_ in order.mock_calls]
    assert names == (["edit", "send", "answer"] if fallback else ["edit", "answer"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refuse",
    ["network on the edit", "the edit and the new message both refused"],
)
async def test_a_screen_that_could_not_be_delivered_leaves_the_query_unanswered(refuse: str):
    """Telegram takes one answer per query: answering first would leave the
    error handler's alert nowhere to go, and the tap would end in silence."""
    callback = _callback()
    if refuse.startswith("network"):
        callback.message.edit_text.side_effect = TelegramNetworkError(
            method=MagicMock(), message="timeout"
        )
        expected: type[Exception] = TelegramNetworkError
    else:
        callback.message.edit_text.side_effect = _cannot_edit()
        callback.message.answer.side_effect = TelegramBadRequest(
            method=MagicMock(), message="Bad Request: can't parse entities: x"
        )
        expected = TelegramBadRequest
    with pytest.raises(expected):
        await show(callback, _screen(), toast="Сохранено")
    callback.answer.assert_not_awaited()


class _TelegramBot:
    """A bot double that keeps every request and answers a query only once, as Telegram does."""

    def __init__(self, *, edit_error: Exception | None = None) -> None:
        self.sent: list[Any] = []
        self._edit_error = edit_error
        self.mock = AsyncMock(side_effect=self._call)

    async def _call(self, method: Any, *_: object, **__: object) -> object:
        self.sent.append(method)
        if isinstance(method, AnswerCallbackQuery):
            if sum(isinstance(m, AnswerCallbackQuery) for m in self.sent) > 1:
                raise TelegramBadRequest(
                    method=method,
                    message="Bad Request: query is too old and response timeout expired "
                    "or query ID is invalid",
                )
            return True
        if isinstance(method, EditMessageText) and self._edit_error is not None:
            raise self._edit_error
        return True

    def answers(self) -> list[AnswerCallbackQuery]:
        return [m for m in self.sent if isinstance(m, AnswerCallbackQuery)]


def _real_tap(bot: AsyncMock, data: str) -> CallbackQuery:
    from aiogram.types import Chat, User

    from tests.unit.screen_routing import ADMIN_ID

    anchor = Message(
        message_id=3,
        date=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
        chat=Chat(id=ADMIN_ID, type="private"),
        from_user=User(id=1, is_bot=True, first_name="Bot"),
        text="Экран",
    ).as_(bot)
    return CallbackQuery(
        id="q1",
        from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
        chat_instance="ci",
        data=data,
        message=anchor,
    ).as_(bot)


@pytest.mark.asyncio
async def test_a_failed_tap_ends_in_the_error_handlers_alert():
    """The hub button through the real router, its edit lost to the network, then
    the global error handler as the dispatcher calls it: the admin sees its alert,
    the only answer the query gets."""
    from src.bot.errors import _ERROR_ALERT, handle_unexpected_error
    from tests.unit.screen_routing import FakeState, deliver

    bot = _TelegramBot(edit_error=TelegramNetworkError(method=MagicMock(), message="timeout"))
    tap = _real_tap(bot.mock, "adm_menu:ru")
    with pytest.raises(TelegramNetworkError) as raised:
        await deliver("callback_query", tap, FakeState(), call=True, bot=bot.mock)
    assert [type(m).__name__ for m in bot.sent] == ["EditMessageText"]

    update = Update(update_id=1, callback_query=tap)
    await handle_unexpected_error(ErrorEvent(update=update, exception=raised.value))
    (answer,) = bot.answers()
    assert (answer.text, answer.show_alert) == (_ERROR_ALERT, True)


@pytest.mark.asyncio
async def test_a_tap_that_lands_is_answered_once_after_its_screen():
    """The control: the same tap with Telegram up edits the hub, then answers."""
    from tests.unit.screen_routing import FakeState, deliver

    bot = _TelegramBot()
    await deliver(
        "callback_query", _real_tap(bot.mock, "adm_menu:ru"), FakeState(), call=True, bot=bot.mock
    )
    assert [type(m).__name__ for m in bot.sent] == ["EditMessageText", "AnswerCallbackQuery"]
    (answer,) = bot.answers()
    assert answer.text is None and not answer.show_alert


# ---------------------------------------------------------------------------
# Too long
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_too_long_is_split_with_the_keyboard_on_the_last_piece():
    lines = "\n".join(f"строка {n}: " + "я" * 60 for n in range(120))
    screen = _screen(text=f"{TITLE}\n{lines}")
    assert parsed_length(render_text(screen)) > TELEGRAM_MESSAGE_LIMIT

    callback = _callback()
    with patch("src.bot.screens.render.send_html_parts", new=AsyncMock()) as head:
        await show(callback, screen)

    callback.message.edit_text.assert_not_awaited()  # an edit would be refused for length
    parts = head.await_args.kwargs["parts"]
    last_args, last_kwargs = callback.message.answer.await_args
    assert isinstance(last_kwargs["reply_markup"], InlineKeyboardMarkup)
    pieces = [*parts, last_args[0]]
    assert len(pieces) >= 2
    assert all(parsed_length(p) <= TELEGRAM_MESSAGE_LIMIT for p in pieces)
    assert "строка 0:" in pieces[0] and "строка 119:" in pieces[-1]


# ---------------------------------------------------------------------------
# Markup and text
# ---------------------------------------------------------------------------


def test_inline_rows_and_a_reply_keyboard_are_exclusive():
    with pytest.raises(ValueError, match="exclusive"):
        to_markup(_screen(reply=((ReplyButton("x"),),)))
    with pytest.raises(ValueError, match="remove_reply"):
        to_markup(_screen(remove_reply=True))


def test_a_screen_without_a_keyboard():
    assert to_markup(_screen(rows=())) is None


def test_notice_is_escaped_and_italic():
    assert with_notice("T", "<b>x</b> & y") == "<i>✓ &lt;b&gt;x&lt;/b&gt; &amp; y</i>\n\nT"


def test_raw_notice_is_trusted_html():
    assert with_notice("T", notice_raw="<i>устарела</i>") == "<i>устарела</i>\n\nT"


def test_notice_and_raw_notice_are_exclusive():
    with pytest.raises(ValueError, match="exclusive"):
        with_notice("T", "a", "b")
    with pytest.raises(ValueError, match="exclusive"):
        render_text(_screen(notice="a", notice_raw="b"))


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def test_back_names_its_destination():
    assert back("chats", "adm_pnl:ru:0", "ru").label == "⬅️ Чаты"
    assert back("chats", "adm_pnl:en:0", "en").label == "⬅️ Chats"
    assert back("stk.set", "adm_stk_pk:ru:0123456789ab:0", "ru", name="Пак").label == "⬅️ Пак"
    with pytest.raises(KeyError):
        back("stk.set", "adm_stk_pk:ru:0123456789ab:0", "ru")  # no fixed name: builder passes one


def test_cancel():
    button = cancel("ar_cancel:en:-100", "en")
    assert (button.label, button.kind) == ("✖️ Cancel", "cancel")


def test_pager_wraps_around():
    row = pager(0, 3, lambda p: f"adm_pnl:ru:{p}")
    assert row is not None
    assert [(b.label, b.callback) for b in row] == [
        ("◀", "adm_pnl:ru:2"),
        ("1/3", "noop"),
        ("▶", "adm_pnl:ru:1"),
    ]
    last = pager(2, 3, lambda p: f"adm_pnl:ru:{p}")
    assert last is not None and last[2].callback == "adm_pnl:ru:0"


def test_one_page_has_no_pager():
    assert pager(0, 1, lambda p: f"adm_pnl:ru:{p}") is None
    assert pager(0, 0, lambda p: f"adm_pnl:ru:{p}") is None


@pytest.mark.parametrize(
    ("total", "page", "per_page", "expected"),
    [
        (0, 0, 5, (0, 1, 0)),  # an empty list is one empty page, not zero pages
        (5, 0, 5, (0, 1, 0)),
        (6, 1, 5, (1, 2, 5)),
        (6, 7, 5, (1, 2, 5)),  # an old button past the end: the last real page
        (6, -3, 5, (0, 2, 0)),
    ],
)
def test_page_slice_clamps(total, page, per_page, expected):
    assert page_slice(total, page, per_page) == expected


def test_page_slice_refuses_a_zero_page_size():
    with pytest.raises(ValueError):
        page_slice(10, 0, 0)


# ---------------------------------------------------------------------------
# Through the drawn-emoji middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pager_arrows_survive_the_drawn_emoji_swap():
    """A label that is ONLY a glyph is left as text by `CustomEmoji` (an icon
    with an empty label shows nothing on a client without icons), so the
    pager's ``◀``/``▶`` stay plain emoji while ``⬅️ Панель`` gets the drawn
    arrow. Recorded, not fixed: the pager is legible either way."""
    mw, bot = await _on()
    row = pager(0, 3, lambda p: f"adm_pnl:ru:{p}")
    assert row is not None
    screen = _screen(rows=(row, (back("hub", "adm_menu:ru", "ru"),)))
    out = await _send(mw, bot, SendMessage(chat_id=1, text=TITLE, reply_markup=to_markup(screen)))
    assert _buttons(out) == [("◀", None), ("1/3", None), ("▶", None), ("Панель", "b-back")]


@pytest.mark.asyncio
async def test_screen_markup_is_recognised_as_a_screen():
    """Control: the same swap runs on an edit, so an in-place screen gets icons too."""
    mw, bot = await _on()
    markup = to_markup(_screen())
    out = await _send(
        mw, bot, EditMessageText(chat_id=1, message_id=1, text=TITLE, reply_markup=markup)
    )
    assert _buttons(out) == [("Панель", "b-back")]


def test_button_model_refuses_two_actions():
    with pytest.raises(ValueError):
        Button("x", callback="adm_menu:ru", url="https://example.com")
