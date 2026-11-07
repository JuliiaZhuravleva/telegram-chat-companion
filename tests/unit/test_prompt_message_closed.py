"""A prompt ended by a tap elsewhere reads closed, and leads back to its screen.

`PromptExitMiddleware` clears a prompt when the admin taps a button that is not
the prompt's own. Without more, the message that asked for the input still asks
— title, instruction, buttons — and the answer the admin then types reaches the
bot as an ordinary message. So the message is edited to its title, a plain line
saying no answer is awaited there (`CLOSED`), and one button back to the screen
the prompt belongs to. These tests drive the real router (``deliver``) after a
real `show()` put the prompt on screen, read what the bot was asked to send, and
route that button with no FSM state at all.
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
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

import src.bot.states.admin as admin_states
from src.bot.middleware.prompt_exit import CLOSED, CLOSED_ID
from src.bot.screens.model import Screen
from src.bot.screens.registry import Sample, load_all
from src.bot.screens.render import show, show_markup
from src.bot.screens.routes import CALLBACK_LIMIT, target_of
from src.bot.screens.samples import fake
from src.bot.states.admin import PROMPT_HOMES, PROMPT_SCREENS, AdminStates, title_line
from tests.unit.screen_routing import ADMIN_ID, FakeState, deliver

PROMPT_ID = 10
OTHER_ID = 11
_SENT_ID = 40


@pytest.fixture(autouse=True)
def _no_prompt_on_screen() -> Iterator[None]:
    admin_states._ON_SCREEN.clear()
    yield
    admin_states._ON_SCREEN.clear()


def _bot(*, echo: bool = True) -> AsyncMock:
    """A bot whose sends come back as real messages (ids from 40), edits as True.

    Telegram returns a sent message with its inline keyboard; ``echo=False`` is a
    message that came back without one."""
    sent = iter(range(_SENT_ID, _SENT_ID + 100))

    async def answer(method: TelegramMethod[Any], *_: object, **__: object) -> object:
        if isinstance(method, SendMessage):
            markup = method.reply_markup
            return Message(
                message_id=next(sent),
                date=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
                reply_markup=markup if echo and isinstance(markup, InlineKeyboardMarkup) else None,
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


def _home(edit: EditMessageText) -> InlineKeyboardButton:
    """The closed message's one button: one row, one button, nothing else."""
    markup = edit.reply_markup
    assert isinstance(markup, InlineKeyboardMarkup), markup
    (row,) = markup.inline_keyboard
    (button,) = row
    return button


async def _open(screen_id: str, state: AdminStates | Any, lang: str = "ru") -> FakeState:
    """Put ``screen_id`` on message `PROMPT_ID` as its area does, the prompt armed."""
    await show(_tap(f"adm_menu:{lang}", PROMPT_ID, _bot()), _sample(screen_id, lang))
    return FakeState(state)


@pytest.mark.parametrize(
    ("tap_lang", "closed", "home"),
    [
        ("ru", "Вы нажали кнопку на другом экране — ответа здесь больше не жду.", "⬅️ Стикеры"),
        (
            "en",
            "You tapped a button on another screen — no longer waiting for an answer here.",
            "⬅️ Stickers",
        ),
    ],
)
async def test_a_foreign_tap_closes_the_prompts_message(tap_lang: str, closed: str, home: str):
    """The language is the tapped payload's: a Russian prompt left by an English
    menu button is closed in English. The line is plain text (glare takes italics
    first), and the one button goes back to the stickers section the tolerance
    prompt belongs to."""
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value, lang="ru")
    title = title_line(_sample("tol.in", "ru").text)
    assert "<b>" in title, "the tolerance prompt has a bold title"

    bot = _bot()
    await deliver("callback_query", _tap(f"adm_menu:{tap_lang}", OTHER_ID, bot), fsm, bot=bot)

    assert await fsm.get_state() is None
    (edit,) = _edits(bot)
    assert (edit.chat_id, edit.message_id) == (ADMIN_ID, PROMPT_ID)
    assert edit.text == f"{title}\n{closed}"
    assert edit.parse_mode == "HTML"
    button = _home(edit)
    assert button.text == home
    assert button.callback_data == f"adm_pnl_grp:{tap_lang}:{fake.CHAT_ID}:stickers"


async def test_a_payload_without_a_language_closes_in_russian():
    """«Закрыть» opens no screen: the closed line still has to be true for it."""
    fsm = await _open("tol.in", AdminStates.awaiting_setting_value, lang="en")
    bot = _bot()
    await deliver("callback_query", _tap("adm_close", OTHER_ID, bot), fsm, bot=bot)
    (edit,) = _edits(bot)
    assert edit.text.endswith(f"\n{CLOSED('ru')}")
    assert _home(edit).text == "⬅️ Стикеры"


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
    assert edit.text == f"{title_line(screen.text)}\n{CLOSED('ru')}"
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
    assert edit.text == CLOSED("ru")
    assert _home(edit).text == "⬅️ Правила"
    assert _home(edit).callback_data == f"ar_list:ru:{fake.CHAT_ID}:0"


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


# ---------------------------------------------------------------------------
# The instruction editor sends its screens itself (a prompt may need several
# messages), not through show(): its prompt messages are closed all the same.
# ---------------------------------------------------------------------------


def _long_preview() -> Screen:
    from src.bot.screens import chat as chat_screens
    from src.bot.screens.samples.fake import CHAT_ID

    return chat_screens.build_sp_preview(CHAT_ID, "слово " * 1500, 7, "ru")


@pytest.mark.parametrize(
    "screen",
    [
        pytest.param(lambda: _sample("sp.prompt"), id="sp.prompt"),
        pytest.param(lambda: _sample("sp.preview"), id="sp.preview"),
        pytest.param(_long_preview, id="sp.preview-split"),
    ],
)
async def test_the_instruction_editors_message_is_closed(screen: Any):
    """The piece with the buttons — the last one — is the message closed."""
    from src.bot.handlers.admin_chat_panel import _send_split

    shown = screen()
    sent = await _send_split(_tap("adm_menu:ru", PROMPT_ID, _bot()).message, shown)
    if shown.id == "sp.preview" and len(shown.text) > 4096:
        assert len(sent) > 1, "the long preview goes out in several messages"

    bot = _bot()
    fsm = FakeState(AdminStates.awaiting_system_prompt)
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)

    assert await fsm.get_state() is None
    (edit,) = _edits(bot)
    assert (edit.chat_id, edit.message_id) == (ADMIN_ID, sent[-1].message_id)
    assert edit.text.endswith(CLOSED("ru"))
    assert _home(edit).text == "⬅️ Поведение"
    assert _home(edit).callback_data == f"adm_pnl_grp:ru:{fake.CHAT_ID}:behavior"


async def test_the_instruction_editors_refusal_is_closed():
    """A refused part is answered by a reply; that reply is the prompt's message now."""
    from src.bot.handlers.admin_chat_panel import _reply_refused

    send_bot = _bot()
    part = Message(
        message_id=PROMPT_ID,
        date=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
        chat=Chat(id=ADMIN_ID, type="private"),
        from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
        text="часть инструкции",
    ).as_(send_bot)
    await _reply_refused(part, _sample("sp.refused"))
    (reply,) = [c.args[0] for c in send_bot.await_args_list]
    assert isinstance(reply, SendMessage)

    bot = _bot()
    fsm = FakeState(AdminStates.awaiting_system_prompt)
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)

    (edit,) = _edits(bot)
    assert edit.message_id == _SENT_ID
    assert edit.text.endswith(CLOSED("ru"))
    assert _home(edit).text == "⬅️ Поведение"


async def test_a_message_that_came_back_without_its_buttons_offers_the_panel():
    """No id to go back with (Telegram returned the message bare): the one
    button is the panel's, never a dead end and never a guessed chat."""
    from src.bot.handlers.admin_chat_panel import _reply_refused

    send_bot = _bot(echo=False)
    part = Message(
        message_id=PROMPT_ID,
        date=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
        chat=Chat(id=ADMIN_ID, type="private"),
        from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
        text="часть инструкции",
    ).as_(send_bot)
    await _reply_refused(part, _sample("sp.refused"))

    bot = _bot()
    fsm = FakeState(AdminStates.awaiting_system_prompt)
    await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)

    (edit,) = _edits(bot)
    assert (_home(edit).text, _home(edit).callback_data) == ("⬅️ Панель", "adm_menu:ru")


# ---------------------------------------------------------------------------
# Every prompt screen, the ones added later included: its closed message leads
# back, and the way back works with no FSM state.
# ---------------------------------------------------------------------------


def _prompt_samples() -> list[Any]:
    samples = load_all()
    return [
        pytest.param(state, item, id=f"{state.rsplit(':', 1)[-1]}/{item.screen_id}/{item.title}")
        for state, screen_ids in PROMPT_SCREENS.items()
        for screen_id in screen_ids
        for item in samples[screen_id]
    ]


def _key_of(prefix: str) -> str:
    """The id a home of ``prefix`` carries in every sample: the sticker's, or the chat's."""
    return fake.FUID if prefix == "adm_stk_card:" else str(fake.CHAT_ID)


@pytest.mark.parametrize(("state", "item"), _prompt_samples())
async def test_every_prompt_screen_closes_with_one_way_back(state: str, item: Sample):
    screen = item.build("ru")
    opener = _bot()
    await show(_tap("adm_menu:ru", PROMPT_ID, opener), screen)
    fsm = FakeState(state)

    bot = _bot()
    with structlog.testing.capture_logs() as logs:
        await deliver("callback_query", _tap("adm_menu:ru", OTHER_ID, bot), fsm, bot=bot)
    assert await fsm.get_state() is None

    if screen.reply is not None:
        # Telegram refuses to edit a message with a reply keyboard: left, logged.
        assert _edits(bot) == []
        assert any(e.get("reason") == "reply keyboard" for e in logs), logs
        return

    (edit,) = _edits(bot)
    assert edit.text.endswith(CLOSED("ru"))
    button = _home(edit)
    home = PROMPT_HOMES[state]
    assert home is not None, f"{state} shows {item.screen_id} but has no home"
    data = button.callback_data or ""
    assert button.text.startswith("⬅️ ") and button.text != "⬅️ Панель", button.text
    assert len(data.encode()) <= CALLBACK_LIMIT, data
    assert target_of(data, CLOSED_ID) == home.screen_id, data
    assert data.split(":")[2] == _key_of(home.prefix), data

    handler = await deliver("callback_query", _tap(data, OTHER_ID, _bot()), FakeState())
    assert handler is not None and not handler.startswith("stale."), (data, handler)
