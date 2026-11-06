"""Adding an organizer through Telegram's own person picker (``request_users``).

Every routing test here goes through the REAL handler chain
(``src.bot.handlers.router``), not the handler function: the property that
matters is which handler a ``users_shared`` service message or a keyboard
label reaches. The forward/@username reader further down the same router
would also accept both — a pick has no text and no forward, a label is
ordinary text — and would answer «Не нашёл такого участника». A test that
called ``handle_kb_organizer_shared`` directly would stay green with the
handlers registered in the wrong order.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember
from aiogram.types import (
    ChatMemberLeft,
    ChatMemberMember,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    SharedUser,
    User,
    UsersShared,
)

from src.bot.handlers import admin_kb
from src.bot.handlers.admin_kb import _shared_user_label
from src.bot.keyboards.admin_kb import ORG_PICK_REQUEST_ID
from src.bot.states.admin import AdminStates
from tests.unit.test_fsm_escape import ADMIN_ID, _FakeState, _make_message, _propagate

GROUP_ID = -100123  # obviously fake
PICKED_ID = 7001
NOT_FOUND = "Не нашёл такого участника"


def _with_pick(
    msg: Message, *, request_id: int = ORG_PICK_REQUEST_ID, users: list[SharedUser] | None = None
) -> Message:
    """The service message Telegram sends when the admin picks a person."""
    if users is None:
        users = [SharedUser(user_id=PICKED_ID, first_name="Аня", last_name="Ким", username="anya")]
    return msg.model_copy(
        update={"text": None, "users_shared": UsersShared(request_id=request_id, users=users)}
    )


def _sends(bot: AsyncMock) -> list[object]:
    """Every SendMessage the handlers made, read off the bot itself."""
    return [
        call.args[0]
        for call in bot.await_args_list
        if call.args and type(call.args[0]).__name__ == "SendMessage"
    ]


def _member(status: str) -> object:
    user = User(id=PICKED_ID, is_bot=False, first_name="Аня")
    return ChatMemberLeft(user=user) if status == "left" else ChatMemberMember(user=user)


def _bot_answering(bot: AsyncMock, member: object | Exception) -> AsyncMock:
    """Make ``bot.get_chat_member`` return ``member`` (or raise it).

    The handler calls the shortcut method, not ``bot(GetChatMember(...))`` —
    routing through ``bot.side_effect`` was tried first and never fired, which
    left the error-path test passing vacuously. Callers assert on the returned
    mock that the lookup really happened.
    """
    lookup = (
        AsyncMock(side_effect=member)
        if isinstance(member, Exception)
        else AsyncMock(return_value=member)
    )
    bot.get_chat_member = lookup
    return lookup


async def _parked_in_prompt() -> _FakeState:
    state = _FakeState(AdminStates.awaiting_kb_organizer)
    await state.update_data(kb_chat_id=GROUP_ID, kb_lang="ru")
    return state


def _written_ids(data: dict[str, object]) -> list[int] | None:
    repo = data["chat_settings_repo"]
    if not repo.set_field.await_count:  # type: ignore[attr-defined]
        return None
    chat_id, field, value = repo.set_field.await_args.args  # type: ignore[attr-defined]
    assert (chat_id, field) == (GROUP_ID, "kb_organizer_ids")
    return json.loads(value)


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patched TRUE on purpose: left unpatched the check resolves against an
    # AsyncMock repo, comes back falsy, and every handler returns before
    # doing anything — every negative assertion below would pass vacuously.
    monkeypatch.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))


@pytest.mark.usefixtures("admin")
class TestAPickAddsTheOrganizer:
    async def test_the_pick_reaches_its_handler_not_the_username_reader(self) -> None:
        msg, data, bot = _make_message()
        _bot_answering(bot, _member("member"))
        state = await _parked_in_prompt()

        await _propagate(_with_pick(msg), data, state)

        assert _written_ids(data) == [PICKED_ID]
        sends = _sends(bot)
        assert len(sends) == 1
        assert NOT_FOUND not in sends[0].text  # type: ignore[attr-defined]
        assert sends[0].text == "✅ Аня Ким (@anya) добавлен(а) в организаторы."  # type: ignore[attr-defined]
        assert isinstance(sends[0].reply_markup, ReplyKeyboardRemove)  # type: ignore[attr-defined]
        assert await state.get_state() is None

    async def test_someone_outside_the_chat_is_added_with_a_warning(self) -> None:
        msg, data, bot = _make_message()
        lookup = _bot_answering(bot, _member("left"))
        state = await _parked_in_prompt()

        await _propagate(_with_pick(msg), data, state)

        lookup.assert_awaited_once_with(GROUP_ID, PICKED_ID)
        assert _written_ids(data) == [PICKED_ID]
        assert _sends(bot)[0].text.endswith("⚠️ Сейчас этого человека в чате нет.")  # type: ignore[attr-defined]

    async def test_an_unverifiable_membership_is_not_reported_as_absence(self) -> None:
        msg, data, bot = _make_message()
        lookup = _bot_answering(
            bot, TelegramBadRequest(method=GetChatMember(chat_id=GROUP_ID, user_id=1), message="x")
        )
        state = await _parked_in_prompt()

        await _propagate(_with_pick(msg), data, state)

        lookup.assert_awaited_once()

        assert _written_ids(data) == [PICKED_ID]
        assert "⚠️" not in _sends(bot)[0].text  # type: ignore[attr-defined]

    async def test_a_repeat_pick_writes_nothing_new(self) -> None:
        msg, data, bot = _make_message()
        _bot_answering(bot, _member("member"))
        data["chat_settings_repo"].get = AsyncMock(  # type: ignore[attr-defined]
            return_value={"kb_organizer_ids": json.dumps([PICKED_ID])}
        )
        state = await _parked_in_prompt()

        await _propagate(_with_pick(msg), data, state)

        assert _written_ids(data) is None
        assert "добавлен(а)" in _sends(bot)[0].text  # type: ignore[attr-defined]


@pytest.mark.usefixtures("admin")
class TestAStalePickWritesNothing:
    async def test_without_the_prompt_state(self) -> None:
        """The keyboard outlives its prompt behind the input's keyboard icon."""
        msg, data, bot = _make_message()
        state = _FakeState()

        await _propagate(_with_pick(msg), data, state)

        assert _written_ids(data) is None
        sends = _sends(bot)
        assert len(sends) == 1
        assert sends[0].text.startswith("Этот выбор уже закрыт")  # type: ignore[attr-defined]
        assert isinstance(sends[0].reply_markup, ReplyKeyboardRemove)  # type: ignore[attr-defined]

    async def test_with_a_foreign_request_id(self) -> None:
        msg, data, bot = _make_message()
        state = await _parked_in_prompt()

        await _propagate(_with_pick(msg, request_id=ORG_PICK_REQUEST_ID + 1), data, state)

        assert _written_ids(data) is None
        assert _sends(bot)[0].text.startswith("Этот выбор уже закрыт")  # type: ignore[attr-defined]


async def test_a_non_admin_pick_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=False))
    msg, data, bot = _make_message()
    state = await _parked_in_prompt()

    await _propagate(_with_pick(msg), data, state)

    assert _written_ids(data) is None
    assert _sends(bot) == []


@pytest.mark.usefixtures("admin")
class TestTheLabels:
    async def test_cancel_ends_the_prompt_and_takes_the_keyboard_away(self) -> None:
        msg, data, bot = _make_message(text="✖️ Отмена")
        state = await _parked_in_prompt()

        await _propagate(msg, data, state)

        assert _written_ids(data) is None
        assert await state.get_state() is None
        sends = _sends(bot)
        assert [s.text for s in sends] == ["Добавление организатора отменено."]  # type: ignore[attr-defined]
        assert isinstance(sends[0].reply_markup, ReplyKeyboardRemove)  # type: ignore[attr-defined]

    async def test_the_picker_label_as_text_keeps_the_prompt_open(self) -> None:
        """Only a client that cannot show the picker sends its label as text."""
        msg, data, bot = _make_message(text="🔍 Найти в Telegram")
        state = await _parked_in_prompt()

        await _propagate(msg, data, state)

        assert _written_ids(data) is None
        assert await state.get_state() == AdminStates.awaiting_kb_organizer.state
        texts = [s.text for s in _sends(bot)]  # type: ignore[attr-defined]
        assert texts == [admin_kb._PICK_UNSUPPORTED["ru"]]

    async def test_a_typed_username_still_reaches_the_reader(self) -> None:
        """Positive control for the label routes: they must not shadow the reader."""
        msg, data, bot = _make_message(text="@someone")
        data["message_repo"].find_by_username = AsyncMock(return_value={"user_id": 77})  # type: ignore[attr-defined]
        state = await _parked_in_prompt()

        await _propagate(msg, data, state)

        assert _written_ids(data) == [77]
        assert isinstance(_sends(bot)[0].reply_markup, ReplyKeyboardRemove)  # type: ignore[attr-defined]

    async def test_a_forwarded_cancel_label_is_content(self) -> None:
        """A forward whose text happens to be the label is an organizer add."""
        msg, data, bot = _make_message(text="✖️ Отмена", forwarded=True)
        state = await _parked_in_prompt()

        await _propagate(msg, data, state)

        assert _written_ids(data) == [99]


async def test_slash_cancel_takes_the_keyboard_away_too() -> None:
    msg, data, bot = _make_message(text="/cancel")
    state = await _parked_in_prompt()

    await _propagate(msg, data, state)

    sends = _sends(bot)
    assert len(sends) == 1
    assert isinstance(sends[0].reply_markup, ReplyKeyboardRemove)  # type: ignore[attr-defined]


async def test_the_prompt_is_sent_with_the_picker_through_the_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ➕ tap, end to end through the callback chain."""
    from datetime import UTC, datetime

    from aiogram.types import CallbackQuery, Chat

    monkeypatch.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))
    bot = AsyncMock()
    user = User(id=ADMIN_ID, is_bot=False, first_name="Admin")
    screen = Message(
        message_id=5,
        date=datetime(2026, 11, 6, tzinfo=UTC),
        chat=Chat(id=ADMIN_ID, type="private"),
        from_user=user,
        text="👥 Организаторы чата",
    ).as_(bot)
    callback = CallbackQuery(
        id="q",
        from_user=user,
        chat_instance="c",
        message=screen,
        data=f"adm_kb_org_add:ru:{GROUP_ID}",
    ).as_(bot)
    _, data, _ = _make_message()
    data["bot"] = bot
    state = _FakeState()
    data["state"] = state
    data["raw_state"] = None
    data["event_from_user"] = user

    from src.bot.handlers import router as main_router

    await main_router.propagate_event("callback_query", callback, **data)

    sends = _sends(bot)
    assert len(sends) == 1
    assert isinstance(sends[0].reply_markup, ReplyKeyboardMarkup)  # type: ignore[attr-defined]
    assert sends[0].reply_markup.keyboard[0][0].request_users is not None  # type: ignore[attr-defined]
    assert await state.get_state() == AdminStates.awaiting_kb_organizer.state
    assert (await state.get_data()) == {"kb_chat_id": GROUP_ID, "kb_lang": "ru"}


class TestSharedUserLabel:
    def test_full_name_and_username(self) -> None:
        user = SharedUser(user_id=1, first_name="Аня", last_name="Ким", username="anya")
        assert _shared_user_label(user) == "Аня Ким (@anya)"

    def test_username_only(self) -> None:
        assert _shared_user_label(SharedUser(user_id=1, username="anya")) == "@anya"

    def test_name_only(self) -> None:
        assert _shared_user_label(SharedUser(user_id=1, first_name="Аня")) == "Аня"

    def test_nothing_falls_back_to_the_id(self) -> None:
        assert _shared_user_label(SharedUser(user_id=42)) == "42"


class TestDrawnIconsDoNotBreakTheLabels:
    """With drawn emoji on, ``CustomEmoji`` moves a reply button's glyph into its icon.

    The tap then sends the label WITHOUT the glyph. These tests render the real prompt
    keyboard through the real middleware and route the exact text a client would send.
    """

    @staticmethod
    async def _tap_texts() -> list[str]:
        from aiogram.client.default import DefaultBotProperties
        from aiogram.methods import SendMessage

        from src.bot.custom_emoji import CustomEmoji
        from src.bot.keyboards.admin_kb import kb_org_add_prompt_keyboard

        def sticker(glyph: str, cid: str) -> object:
            return type("S", (), {"emoji": glyph, "custom_emoji_id": cid})()

        found = type("Set", (), {"stickers": [sticker("✖️", "x"), sticker("🔍", "s")]})()
        bot = AsyncMock()
        bot.default = DefaultBotProperties(parse_mode="HTML")
        bot.get_sticker_set = AsyncMock(return_value=found)
        mw = CustomEmoji()
        await mw.load(bot, "buttons", None)
        sent: list[SendMessage] = []

        async def make_request(_bot: object, m: SendMessage) -> str:
            sent.append(m)
            return "ok"

        await mw(
            make_request,  # type: ignore[arg-type]
            bot,
            SendMessage(chat_id=1, text="x", reply_markup=kb_org_add_prompt_keyboard("ru")),
        )
        rows = sent[0].reply_markup.keyboard  # type: ignore[union-attr]
        assert all(b.icon_custom_emoji_id for r in rows for b in r), "icons were not applied"
        return [b.text for r in rows for b in r]

    @pytest.mark.usefixtures("admin")
    async def test_the_iconed_cancel_tap_still_cancels(self) -> None:
        _, cancel = await self._tap_texts()
        assert cancel == "Отмена"
        msg, data, bot = _make_message(text=cancel)
        state = await _parked_in_prompt()

        await _propagate(msg, data, state)

        assert _written_ids(data) is None
        assert await state.get_state() is None
        assert [s.text for s in _sends(bot)] == ["Добавление организатора отменено."]  # type: ignore[attr-defined]

    @pytest.mark.usefixtures("admin")
    async def test_the_iconed_picker_label_still_gets_the_fallback(self) -> None:
        pick, _ = await self._tap_texts()
        msg, data, bot = _make_message(text=pick)
        state = await _parked_in_prompt()

        await _propagate(msg, data, state)

        assert _written_ids(data) is None
        assert [s.text for s in _sends(bot)] == [admin_kb._PICK_UNSUPPORTED["ru"]]  # type: ignore[attr-defined]
