"""Organizer writes through the REAL router: a chat with no row, and the list that ends the prompt.

Every tap and message here is propagated through ``src.bot.handlers.router``
with its middlewares and filters, so the handler under test is the one aiogram
would pick, with the add prompt's state as the test sets it.

A writer that answers None matched no ``chat_settings`` row (the group became
a supergroup and its row moved to the new id, or the chat is gone). Nothing
was stored, so the answer is the chat list under ``CHAT_GONE``: no «добавлен»
or «снова организатор» line drawn from ids held in memory, and no name kept.
On the prompt's paths the prompt is closed and its reply keyboard taken away
first.

The writers' SQL is tested against PostgreSQL in
``tests/integration/test_kb_organizer_writers.py``; here they are the fake in
``kb_organizer_fakes``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import (
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    SharedUser,
    UsersShared,
)

from src.bot.handlers import admin_kb
from src.bot.handlers.admin_chat_panel import CHAT_GONE
from src.bot.keyboards.admin_kb import ORG_PICK_REQUEST_ID
from src.bot.screens import orgs
from src.bot.screens.routes import cb
from src.bot.screens.samples import fake
from src.bot.states.admin import AdminStates
from src.database.repositories.admin import AdminRepository
from tests.unit.kb_organizer_fakes import wire_organizer_writers, written
from tests.unit.screen_routing import ADMIN_ID, FakeState, admin_tap, deliver
from tests.unit.test_fsm_escape import _make_message, _propagate

GROUP = fake.CHAT_ID_SHORT
OTHER_GROUP = fake.CHAT_ID_OTHER
ORG = fake.USER_ID
NEW = fake.USER_ID_OTHER
LANGS = ["ru", "en"]


class _Container:
    def __init__(self, deps: dict[type, Any]) -> None:
        self._deps = deps

    async def get(self, dependency: type) -> Any:
        return self._deps[dependency]


def _chat_list() -> tuple[AsyncMock, _Container]:
    """What the chat list reads: no connected chats, no requests."""
    admin = AsyncMock()
    admin.count_attempts = AsyncMock(return_value=0)
    admin.get_enabled_chats_page_by_activity = AsyncMock(return_value=([], 0))
    return admin, _Container({AdminRepository: admin})


def _settings(ids: list[int] | None) -> MagicMock:
    """The chat's settings row with these organizers; None is a chat with no row."""
    repo = MagicMock()
    repo.get = AsyncMock(
        return_value=None
        if ids is None
        else {"chat_title": fake.CHAT_TITLE, "kb_organizer_ids": json.dumps(ids)}
    )
    return wire_organizer_writers(repo)


def _bot_config() -> MagicMock:
    repo = MagicMock()

    async def _get(key: str) -> object:
        return [ADMIN_ID] if key == "admin_ids" else None

    repo.get = AsyncMock(side_effect=_get)
    return repo


def _store() -> AsyncMock:
    return AsyncMock(
        names=AsyncMock(return_value={}),
        remember=AsyncMock(return_value=True),
        remember_seen=AsyncMock(return_value=True),
    )


class _Tapped:
    """One tap through the router, and what it left behind."""

    def __init__(self, callback: MagicMock, store: AsyncMock, admin: AsyncMock) -> None:
        self.callback = callback
        self.store = store
        self.admin = admin

    @property
    def sent(self) -> list[tuple[str, Any]]:
        return [
            (c.args[0], c.kwargs.get("reply_markup"))
            for c in self.callback.message.answer.await_args_list
        ]


async def _tap(
    data: str,
    settings: MagicMock,
    state: FakeState | None = None,
    *,
    candidates: list[dict[str, object]] | None = None,
) -> _Tapped:
    callback = admin_tap(data)
    callback.bot = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    message_repo = AsyncMock()
    message_repo.get_top_active_users = AsyncMock(
        return_value=(candidates or [], len(candidates or []))
    )
    admin, container = _chat_list()
    store = _store()
    await deliver(
        "callback_query",
        callback,
        state or FakeState(),
        call=True,
        chat_settings_repo=settings,
        bot_config_repo=_bot_config(),
        organizer_names=store,
        message_repo=message_repo,
        dishka_container=container,
    )
    return _Tapped(callback, store, admin)


async def _prompt(chat_id: int = GROUP, lang: str = "ru") -> FakeState:
    state = FakeState(AdminStates.awaiting_kb_organizer)
    await state.update_data(kb_chat_id=chat_id, kb_lang=lang)
    return state


def _no_notice(text: str, lang: str) -> None:
    for notice in (orgs.ADDED, orgs.RESTORED, orgs.REMOVED):
        tail = notice(lang).replace("{n}", "")
        assert tail not in text, f"a {tail!r} line drawn after a write that stored nothing"


# ---------------------------------------------------------------------------
# A writer that matched no row: the chat list under CHAT_GONE
# ---------------------------------------------------------------------------


def _pick(lang: str) -> str:
    return cb("adm_kb_org_pick:", lang, GROUP, NEW)


def _undo(lang: str) -> str:
    return cb("adm_kb_org_undo:", lang, GROUP, ORG, 0)


def _remove(lang: str) -> str:
    return cb("adm_kb_org_rm:", lang, GROUP, ORG)


@pytest.mark.parametrize("lang", LANGS)
class TestATapOnAChatWithNoRow:
    async def test_a_pick_from_the_participant_list(self, lang: str) -> None:
        settings = _settings(None)

        tap = await _tap(_pick(lang), settings)

        settings.add_kb_organizer.assert_awaited_once_with(GROUP, NEW)
        text = tap.callback.message.edit_text.await_args.args[0]
        assert CHAT_GONE(lang) in text
        tap.admin.get_enabled_chats_page_by_activity.assert_awaited()
        _no_notice(text, lang)
        tap.store.remember_seen.assert_not_awaited()
        tap.store.remember.assert_not_awaited()

    async def test_an_undo(self, lang: str) -> None:
        settings = _settings(None)

        tap = await _tap(_undo(lang), settings)

        settings.restore_kb_organizer.assert_awaited_once_with(GROUP, ORG, 0)
        text = tap.callback.message.edit_text.await_args.args[0]
        assert CHAT_GONE(lang) in text
        tap.admin.get_enabled_chats_page_by_activity.assert_awaited()
        _no_notice(text, lang)
        assert "↩️" not in str(tap.callback.message.edit_text.await_args.kwargs["reply_markup"])

    async def test_a_remove_whose_row_moved_after_the_read(self, lang: str) -> None:
        """The row was read with the organizer on it and was gone by the write."""
        settings = _settings([ORG])
        settings.remove_kb_organizer = AsyncMock(return_value=None)

        tap = await _tap(_remove(lang), settings)

        settings.remove_kb_organizer.assert_awaited_once_with(GROUP, ORG)
        text = tap.callback.message.edit_text.await_args.args[0]
        assert CHAT_GONE(lang) in text
        _no_notice(text, lang)
        markup = tap.callback.message.edit_text.await_args.kwargs["reply_markup"]
        assert "adm_kb_org_undo:" not in str(markup), "an undo for a remove that stored nothing"

    async def test_a_pick_with_the_prompt_open_closes_it_first(self, lang: str) -> None:
        settings = _settings(None)
        state = await _prompt(GROUP, lang)

        tap = await _tap(_pick(lang), settings, state)

        assert state.clear_calls == 1
        assert await state.get_state() is None
        tap.callback.message.edit_text.assert_not_awaited()
        (line, keyboard), (text, markup) = tap.sent
        assert line == admin_kb._ADD_CANCELLED[lang]
        assert isinstance(keyboard, ReplyKeyboardRemove)
        assert CHAT_GONE(lang) in text
        assert isinstance(markup, InlineKeyboardMarkup)
        _no_notice(text, lang)
        tap.store.remember_seen.assert_not_awaited()


# ---------------------------------------------------------------------------
# The prompt's own answers on a chat with no row
# ---------------------------------------------------------------------------


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> None:
    # TRUE on purpose: unpatched, the check resolves against an AsyncMock repo,
    # comes back falsy, and every handler returns before writing anything.
    monkeypatch.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))


def _sends(bot: AsyncMock) -> list[Any]:
    return [
        call.args[0]
        for call in bot.await_args_list
        if call.args and type(call.args[0]).__name__ == "SendMessage"
    ]


def _shared(msg: Message) -> Message:
    person = SharedUser(user_id=NEW, first_name="Аня", last_name="Ким", username="anya")
    return msg.model_copy(
        update={
            "text": None,
            "users_shared": UsersShared(request_id=ORG_PICK_REQUEST_ID, users=[person]),
        }
    )


def _forwarded() -> tuple[Message, dict[str, object], AsyncMock]:
    return _make_message(text="привет", forwarded=True)


def _typed() -> tuple[Message, dict[str, object], AsyncMock]:
    msg, data, bot = _make_message(text="@someone")
    data["message_repo"].find_by_username = AsyncMock(  # type: ignore[attr-defined]
        return_value={"user_id": NEW, "first_name": "Аня"}
    )
    return msg, data, bot


def _picked() -> tuple[Message, dict[str, object], AsyncMock]:
    msg, data, bot = _make_message()
    return _shared(msg), data, bot


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("arrives", [_forwarded, _typed, _picked], ids=["forward", "typed", "pick"])
async def test_an_add_from_the_prompt_on_a_chat_with_no_row(arrives: Any, lang: str) -> None:
    msg, data, bot = arrives()
    admin_repo, container = _chat_list()
    data["dishka_container"] = container
    state = await _prompt(GROUP, lang)

    await _propagate(msg, data, state)

    settings = data["chat_settings_repo"]
    settings.add_kb_organizer.assert_awaited_once()  # type: ignore[attr-defined]
    assert written(settings, GROUP) is None
    # The forward/@username reader clears the prompt before its admin check
    # too, so the count is not one: the state at the end is what matters.
    assert await state.get_state() is None
    line, listing = _sends(bot)
    assert line.text == admin_kb._ADD_CANCELLED[lang]
    assert isinstance(line.reply_markup, ReplyKeyboardRemove)
    assert CHAT_GONE(lang) in listing.text
    admin_repo.get_enabled_chats_page_by_activity.assert_awaited()
    _no_notice(listing.text, lang)
    names = data["organizer_names"]
    names.remember.assert_not_awaited()  # type: ignore[attr-defined]
    names.remember_seen.assert_not_awaited()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The participant list and the add prompt (kb-organizers-2)
# ---------------------------------------------------------------------------


def _list(chat_id: int, lang: str = "ru") -> str:
    return cb("adm_kb_org_list:", lang, chat_id, 0)


CANDIDATES: list[dict[str, object]] = [{"user_id": NEW, "first_name": "Аня", "username": None}]


@pytest.mark.parametrize("lang", LANGS)
async def test_another_chats_participant_list_ends_the_prompt(lang: str) -> None:
    """The prompt names no chat on its screen: left open, the admin's next
    @username or forward would add the person to the chat they browsed away from."""
    state = await _prompt(GROUP, lang)

    tap = await _tap(_list(OTHER_GROUP, lang), _settings([]), state, candidates=CANDIDATES)

    assert state.clear_calls == 1
    assert await state.get_state() is None
    tap.callback.message.edit_text.assert_not_awaited()
    (line, keyboard), (text, markup) = tap.sent
    assert line == admin_kb._ADD_CANCELLED[lang]
    assert isinstance(keyboard, ReplyKeyboardRemove)
    assert isinstance(markup, InlineKeyboardMarkup)
    picks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert cb("adm_kb_org_pick:", lang, OTHER_GROUP, NEW) in picks, picks


async def test_the_prompts_own_chat_list_keeps_it_open() -> None:
    """The list is offered from the prompt's own failure answer: browsing it is
    part of the add, so the prompt and its keyboard stay."""
    state = await _prompt(GROUP)

    tap = await _tap(_list(GROUP), _settings([]), state, candidates=CANDIDATES)

    assert state.clear_calls == 0
    assert await state.get_state() == AdminStates.awaiting_kb_organizer.state
    assert tap.sent == []
    tap.callback.message.edit_text.assert_awaited_once()


async def test_the_list_without_a_prompt_is_drawn_in_place() -> None:
    state = FakeState()

    tap = await _tap(_list(OTHER_GROUP), _settings([]), state, candidates=CANDIDATES)

    assert state.clear_calls == 0
    assert tap.sent == []
    tap.callback.message.edit_text.assert_awaited_once()
