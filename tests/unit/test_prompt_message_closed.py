"""A prompt ended by a tap elsewhere reads closed: its message says «Ввод закрыт.».

`PromptExitMiddleware` clears a prompt when the admin taps a button that is not
the prompt's own. Without more, the message that asked for the input still asks
— title, instruction, buttons — and the answer the admin then types reaches the
bot as an ordinary message. These tests drive the real router (``deliver``)
after a real `show()` put the prompt on screen, and read what the bot was asked
to send.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, User

import src.bot.states.admin as admin_states
from src.bot.screens.model import Screen
from src.bot.screens.registry import load_all
from src.bot.screens.render import show, show_markup
from src.bot.states.admin import AdminStates, title_line
from tests.unit.screen_routing import ADMIN_ID, FakeState, deliver

PROMPT_ID = 10
OTHER_ID = 11
_SENT_ID = 40


@pytest.fixture(autouse=True)
def _no_prompt_on_screen() -> Iterator[None]:
    admin_states._ON_SCREEN.clear()
    yield
    admin_states._ON_SCREEN.clear()


def _bot() -> AsyncMock:
    """A bot whose sends come back as real messages (ids from 40), edits as True."""
    sent = iter(range(_SENT_ID, _SENT_ID + 100))

    async def answer(method: TelegramMethod[Any], *_: object, **__: object) -> object:
        if isinstance(method, SendMessage):
            return Message(
                message_id=next(sent),
                date=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        return True

    return AsyncMock(side_effect=answer)


def _tap(data: str, message_id: int, bot: AsyncMock) -> CallbackQuery:
    """A real callback from the admin's DM, on the bot's message ``message_id``."""
    anchor = Message(
        message_id=message_id,
        date=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
        chat=Chat(id=ADMIN_ID, type="private"),
        from_user=User(id=1, is_bot=True, first_name="Bot"),
        text="Экран",
    ).as_(bot)
    return CallbackQuery(
        id=f"q{message_id}",
        from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
        chat_instance="ci",
        data=data,
        message=anchor,
    ).as_(bot)


def _sample(screen_id: str, lang: str = "ru") -> Screen:
    return load_all()[screen_id][0].build(lang)


def _edits(bot: AsyncMock) -> list[EditMessageText]:
    return [c.args[0] for c in bot.await_args_list if isinstance(c.args[0], EditMessageText)]


async def _open(screen_id: str, state: AdminStates | Any, lang: str = "ru") -> FakeState:
    """Put ``screen_id`` on message `PROMPT_ID` as its area does, the prompt armed."""
    await show(_tap(f"adm_menu:{lang}", PROMPT_ID, _bot()), _sample(screen_id, lang))
    return FakeState(state)


@pytest.mark.parametrize(
    ("tap_lang", "closed"),
    [("ru", "<i>Ввод закрыт.</i>"), ("en", "<i>Input closed.</i>")],
)
async def test_a_foreign_tap_closes_the_prompts_message(tap_lang: str, closed: str):
    """The language is the tapped payload's: a Russian prompt left by an English
    menu button is closed in English."""
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value, lang="ru")
    title = title_line(_sample("tol.in", "ru").text)
    assert "<b>" in title, "the tolerance prompt has a bold title"

    bot = _bot()
    await deliver("callback_query", _tap(f"adm_menu:{tap_lang}", OTHER_ID, bot), fsm, bot=bot)

    assert await fsm.get_state() is None
    (edit,) = _edits(bot)
    assert (edit.chat_id, edit.message_id) == (ADMIN_ID, PROMPT_ID)
    assert edit.text == f"{title}\n{closed}"
    assert edit.reply_markup is None
    assert edit.parse_mode == "HTML"


async def test_a_payload_without_a_language_closes_in_russian():
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value, lang="en")
    bot = _bot()
    await deliver("callback_query", _tap("adm_close", OTHER_ID, bot), fsm, bot=bot)
    (edit,) = _edits(bot)
    assert edit.text.endswith("\n<i>Ввод закрыт.</i>")


async def test_an_own_tap_leaves_the_prompts_message():
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value)
    bot = _bot()
    await deliver("callback_query", _tap("adm_pnl_tolcancel:ru:-100", OTHER_ID, bot), fsm, bot=bot)
    assert await fsm.get_state() == AdminStates.awaiting_setting_value.state
    assert _edits(bot) == []


async def test_a_tap_on_the_prompts_own_message_is_not_edited_over():
    """That message is the tapped one: the handler draws its new screen there."""
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value)
    bot = _bot()
    await deliver("callback_query", _tap("adm_menu:ru", PROMPT_ID, bot), fsm, bot=bot)
    assert await fsm.get_state() is None
    assert _edits(bot) == []


async def test_the_message_is_closed_once():
    """The record goes with the first close: a second foreign tap edits nothing."""
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value)
    bot = _bot()
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    await fsm.set_state(AdminStates.awaiting_setting_value)
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    assert len(_edits(bot)) == 1


async def test_a_screen_that_replaced_the_prompt_is_not_closed():
    """Another screen drawn into the prompt's message took the prompt off it."""
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value)
    await show_markup(_tap("adm_menu:ru", PROMPT_ID, _bot()), "Меню", None, screen_id="menu")
    bot = _bot()
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    assert _edits(bot) == []


async def test_the_title_is_the_screens_not_its_notice():
    """«Нужно число…» sits above the title of a refused tolerance value; the
    closed message names the screen, not the refusal."""
    screen = _sample("tol.invalid")
    assert screen.notice_raw is not None, "the sample carries its refusal as a notice"
    fsm = await _open("tol.invalid", AdminStates.awaiting_setting_value)
    bot = _bot()
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    (edit,) = _edits(bot)
    assert edit.text == f"{title_line(screen.text)}\n<i>Ввод закрыт.</i>"
    assert "<b>" in edit.text


async def test_another_prompts_message_is_not_closed_for_this_one():
    fsm = await _open("tol.in", AdminStates.awaiting_rule_words)
    bot = _bot()
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    assert await fsm.get_state() is None
    assert _edits(bot) == []


async def test_an_error_reply_closes_without_repeating_send_again():
    """A rules error reply is the prompt's message now; its line is a reason
    («… ещё раз»), not a title, so the closed message carries only the note."""
    screen = _sample("rule.cfg_bad")
    assert "<b>" not in screen.text.split("\n", 1)[0], "the error screen starts with its reason"
    fsm = await _open("rule.cfg_bad", AdminStates.awaiting_rule_config)
    bot = _bot()
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    (edit,) = _edits(bot)
    assert edit.text == "<i>Ввод закрыт.</i>"


async def test_a_prompt_behind_a_reply_keyboard_is_left_and_logged():
    """Telegram refuses to edit a message sent with a reply keyboard (the organizer
    prompt): no edit is tried, and the log says why."""
    opener = _bot()
    await show(_tap("adm_kb_orgs:ru:-100", PROMPT_ID, opener), _sample("org.add"))
    (sent,) = [c.args[0] for c in opener.await_args_list if isinstance(c.args[0], SendMessage)]
    assert sent.reply_markup is not None and not hasattr(sent.reply_markup, "inline_keyboard")
    assert admin_states._ON_SCREEN[ADMIN_ID].message_id == _SENT_ID, "the sent message is noted"

    fsm = FakeState(AdminStates.awaiting_kb_organizer)
    bot = _bot()
    with structlog.testing.capture_logs() as logs:
        await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    assert await fsm.get_state() is None
    assert _edits(bot) == []
    (left,) = [e for e in logs if e["event"] == "prompt_message_left_open"]
    assert left["screen_id"] == "org.add" and left["reason"] == "reply keyboard"


@pytest.mark.parametrize(
    "error",
    [
        "Bad Request: message to edit not found",
        "Bad Request: message can't be edited",
        "Bad Request: message is not modified: specified new message content and reply "
        "markup are exactly the same",
    ],
)
async def test_a_message_that_cannot_be_closed_is_logged_not_raised(error: str):
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value)
    bot = _bot()

    async def refuse(method: TelegramMethod[Any], *_: object, **__: object) -> object:
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method=method, message=error)
        return True

    bot.side_effect = refuse
    with structlog.testing.capture_logs() as logs:
        await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    assert len(_edits(bot)) == 1
    events = [e["event"] for e in logs]
    if "not modified" in error:
        assert "prompt_message_closed" in events
    else:
        (left,) = [e for e in logs if e["event"] == "prompt_message_left_open"]
        assert error in left["reason"]


async def test_the_close_runs_after_the_taps_own_handler():
    """The handler that takes the tap runs first and in full (here ✖️ Закрыть
    deletes its message); the close comes after."""
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value)
    bot = _bot()
    await deliver("callback_query", _tap("adm_close:ru", OTHER_ID, bot), fsm, bot=bot, call=True)
    names = [type(c.args[0]).__name__ for c in bot.await_args_list]
    assert names[-1] == "EditMessageText", names
    assert "DeleteMessage" in names[:-1], names


def test_title_line():
    assert title_line("🎚 <b>Порог</b> · Чат\n\nВведите число") == "🎚 <b>Порог</b> · Чат"
    assert title_line("\n  🎚 <b>Порог</b>\nещё") == "🎚 <b>Порог</b>"
    assert title_line("Не похоже на число. Отправьте ещё раз.") == ""
    # A tag left open on the line would be cut in half: sent as plain text.
    assert title_line("<blockquote><b>Длинная</b> цитата\nдальше</blockquote>") == (
        "Длинная цитата"
    )
    assert title_line("<b>a &amp; b</b> <i>x") == "a &amp; b x"
    long = title_line("<b>" + "я" * 300 + "</b>")
    assert long.endswith("…") and len(long) == 200
