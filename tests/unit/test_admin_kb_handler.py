"""The knowledge-base organizer handlers, called directly with test doubles.

Routing (which handler a pick, a label or a forward reaches) is tested through
the real router in ``test_admin_kb_user_picker.py``; here each handler's own
decisions: what it writes, what it shows, and what it does to the FSM state.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    MessageOriginChannel,
    MessageOriginHiddenUser,
    MessageOriginUser,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    User,
)

from src.bot.handlers import admin_kb
from src.bot.handlers.admin_kb import (
    _extract_username,
    handle_kb_organizer_add_cancel,
    handle_kb_organizer_add_prompt,
    handle_kb_organizer_add_reply,
    handle_kb_organizer_list,
    handle_kb_organizer_pick,
    handle_kb_organizer_remove,
    handle_kb_organizer_undo,
    handle_kb_organizers,
)
from src.bot.screens import orgs
from src.bot.states.admin import AdminStates
from tests.unit.test_fsm_escape import _FakeState

ADMIN_ID = 111
CHAT_ID = -1001234567890  # obviously fake
ORG_USER_ID = 555
OTHER_ID = 777
NEW_ID = 888

NAMES = {ORG_USER_ID: "Аня", OTHER_ID: "Борис", NEW_ID: "@newbie", 901: "Вера", 902: "Глеб"}
ORGS_HEAD = "👥 <b>Организаторы</b>"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def names(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Telegram's name lookup, recorded: which ids the handler asked about."""
    asked: list[int] = []

    async def lookup(_bot: object, _chat_id: int, user_id: int, fallback: str) -> str:
        asked.append(user_id)
        return NAMES.get(user_id, fallback)

    monkeypatch.setattr(admin_kb, "resolve_display_name", lookup)
    return asked


@pytest.fixture
def stale(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(admin_kb, "show_stale", mock)
    return mock


def _make_callback(data: str, user_id: int = ADMIN_ID, chat_type: str = "private") -> MagicMock:
    callback = MagicMock(spec=CallbackQuery)
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = chat_type
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = None
    return callback


def _make_bot_config_repo() -> MagicMock:
    repo = MagicMock()

    async def _get(key: str) -> object:
        return [ADMIN_ID] if key == "admin_ids" else None

    repo.get = AsyncMock(side_effect=_get)
    return repo


def _make_chat_settings_repo(
    organizer_ids: list[int] | None = None, title: str | None = "Тестовый чат"
) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(
        return_value={
            "chat_title": title,
            "kb_organizer_ids": json.dumps(
                organizer_ids if organizer_ids is not None else [ORG_USER_ID]
            ),
        }
    )
    repo.set_field = AsyncMock()
    return repo


def _make_message_repo(
    found: dict[str, object] | None = None,
    known_elsewhere: bool = False,
    top_active_users: tuple[list[dict[str, object]], int] = ([], 0),
) -> MagicMock:
    repo = MagicMock()
    repo.find_by_username = AsyncMock(return_value=found)
    repo.username_seen_elsewhere = AsyncMock(return_value=known_elsewhere)
    repo.get_top_active_users = AsyncMock(return_value=top_active_users)
    return repo


async def _prompt_state() -> _FakeState:
    state = _FakeState(AdminStates.awaiting_kb_organizer)
    await state.update_data(kb_chat_id=CHAT_ID, kb_lang="ru")
    return state


def _written(repo: MagicMock) -> list[int] | None:
    if not repo.set_field.await_count:
        return None
    repo.set_field.assert_awaited_once()
    chat_id, field, value = repo.set_field.await_args.args
    assert (chat_id, field) == (CHAT_ID, "kb_organizer_ids")
    return json.loads(value)


def _edited(callback: MagicMock) -> tuple[str, InlineKeyboardMarkup]:
    callback.message.edit_text.assert_awaited_once()
    call = callback.message.edit_text.await_args
    return call.args[0], call.kwargs["reply_markup"]


def _sent(target: MagicMock) -> list[tuple[str, Any]]:
    return [(c.args[0], c.kwargs.get("reply_markup")) for c in target.answer.await_args_list]


def _buttons(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data or "") for row in markup.inline_keyboard for b in row]


def _labels(markup: InlineKeyboardMarkup) -> list[str]:
    return [label for label, _ in _buttons(markup)]


# ---------------------------------------------------------------------------
# @username parsing
# ---------------------------------------------------------------------------


class TestExtractUsername:
    def test_strips_leading_at(self) -> None:
        assert _extract_username("@newbie") == "newbie"

    def test_accepts_bare_username(self) -> None:
        assert _extract_username("newbie") == "newbie"

    def test_strips_surrounding_whitespace(self) -> None:
        assert _extract_username("  @newbie  ") == "newbie"

    def test_none_when_no_text(self) -> None:
        assert _extract_username(None) is None

    def test_none_when_too_short(self) -> None:
        assert _extract_username("@abc") is None

    def test_none_when_too_long(self) -> None:
        assert _extract_username("@" + "a" * 33) is None

    def test_none_when_not_a_single_token(self) -> None:
        assert _extract_username("please add @newbie") is None

    def test_none_when_invalid_characters(self) -> None:
        assert _extract_username("@new-bie!") is None


# ---------------------------------------------------------------------------
# A payload the handler cannot parse is a stale button
# ---------------------------------------------------------------------------


async def _call(handler: Any, callback: MagicMock, **deps: Any) -> None:
    await handler(
        callback,
        chat_settings_repo=deps.pop("chat_settings_repo", None) or _make_chat_settings_repo(),
        bot_config_repo=_make_bot_config_repo(),
        **deps,
    )


GARBLED = [
    (handle_kb_organizers, "adm_kb_orgs:ru:abc:0", {"state": _FakeState()}),
    (handle_kb_organizers, "adm_kb_orgs:ru", {"state": _FakeState()}),
    (handle_kb_organizer_remove, "adm_kb_org_rm:ru:-100:x", {}),
    (handle_kb_organizer_remove, "adm_kb_org_rm:ru:-100", {}),
    (handle_kb_organizer_undo, "adm_kb_org_undo:ru::5", {}),
    (handle_kb_organizer_list, "adm_kb_org_list:ru:", {"message_repo": _make_message_repo()}),
    (handle_kb_organizer_pick, "adm_kb_org_pick:ru:-100:", {"state": _FakeState()}),
    (handle_kb_organizer_add_prompt, "adm_kb_org_add:en", {"state": _FakeState()}),
    (handle_kb_organizer_add_cancel, "adm_kb_org_x:ru:zz", {"state": _FakeState()}),
]


@pytest.mark.parametrize(("handler", "data", "deps"), GARBLED)
async def test_a_payload_that_does_not_parse_shows_the_stale_screen(
    handler: Any, data: str, deps: dict[str, Any], stale: AsyncMock
) -> None:
    callback = _make_callback(data)
    repo = _make_chat_settings_repo()

    await _call(handler, callback, chat_settings_repo=repo, **deps)

    stale.assert_awaited_once()
    assert stale.await_args.args[0] is callback
    assert stale.await_args.args[1] == ("en" if ":en" in data else "ru")
    repo.set_field.assert_not_awaited()
    callback.message.edit_text.assert_not_awaited()


@pytest.mark.parametrize(("handler", "data", "deps"), GARBLED)
async def test_a_non_admin_gets_an_alert_and_nothing_else(
    handler: Any, data: str, deps: dict[str, Any], stale: AsyncMock
) -> None:
    callback = _make_callback(data, user_id=999)
    repo = _make_chat_settings_repo()

    await _call(handler, callback, chat_settings_repo=repo, **deps)

    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    repo.get.assert_not_awaited()
    stale.assert_not_awaited()


# ---------------------------------------------------------------------------
# orgs
# ---------------------------------------------------------------------------


class TestOrganizerList:
    async def test_shows_the_chats_organizers_with_its_title(self) -> None:
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:0")
        repo = _make_chat_settings_repo([ORG_USER_ID, OTHER_ID])

        await _call(handle_kb_organizers, callback, chat_settings_repo=repo, state=_FakeState())

        text, markup = _edited(callback)
        assert text.startswith(f"{ORGS_HEAD} · Тестовый чат")
        assert _labels(markup) == ["✖️ Аня", "✖️ Борис", "➕ Добавить", "⬅️ Память"]
        assert _buttons(markup)[-1][1] == f"adm_pnl_grp:ru:{CHAT_ID}:memory"

    async def test_the_empty_list_says_who_can_save_facts_anyway(self) -> None:
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:0")
        repo = _make_chat_settings_repo([])

        await _call(handle_kb_organizers, callback, chat_settings_repo=repo, state=_FakeState())

        text, markup = _edited(callback)
        assert "Пока никого. Админы бота могут сохранять факты всегда." in text
        assert _labels(markup) == ["➕ Добавить", "⬅️ Память"]

    async def test_an_old_button_with_an_origin_token_still_opens_the_list(self) -> None:
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:0:p")

        await _call(handle_kb_organizers, callback, state=_FakeState())

        text, _ = _edited(callback)
        assert text.startswith(ORGS_HEAD)

    async def test_names_are_looked_up_for_the_shown_page_only(self, names: list[int]) -> None:
        ids = [ORG_USER_ID, OTHER_ID, NEW_ID, 901, 902]
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:1")

        await _call(
            handle_kb_organizers,
            callback,
            chat_settings_repo=_make_chat_settings_repo(ids),
            state=_FakeState(),
        )

        assert sorted(names) == [901, 902]
        _, markup = _edited(callback)
        assert _labels(markup)[:2] == ["✖️ Вера", "✖️ Глеб"]
        assert "2/2" in _labels(markup)

    async def test_a_page_past_the_end_shows_the_last_page(self) -> None:
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:9")
        repo = _make_chat_settings_repo([ORG_USER_ID, OTHER_ID, NEW_ID, 901])

        await _call(handle_kb_organizers, callback, chat_settings_repo=repo, state=_FakeState())

        _, markup = _edited(callback)
        assert _labels(markup)[0] == "✖️ Вера"
        assert "2/2" in _labels(markup)

    async def test_an_unknown_title_shows_the_chat_id(self) -> None:
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:0")
        repo = _make_chat_settings_repo(title=None)

        await _call(handle_kb_organizers, callback, chat_settings_repo=repo, state=_FakeState())

        text, _ = _edited(callback)
        assert text.startswith(f"{ORGS_HEAD} · {CHAT_ID}")

    async def test_a_title_is_escaped(self) -> None:
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:0")
        repo = _make_chat_settings_repo(title="<b>Чат</b> & co")

        await _call(handle_kb_organizers, callback, chat_settings_repo=repo, state=_FakeState())

        text, _ = _edited(callback)
        assert "&lt;b&gt;Чат&lt;/b&gt; &amp; co" in text

    async def test_from_inside_the_add_prompt_it_ends_the_prompt(self) -> None:
        """An old ``⬅️ Организаторы`` tapped while the prompt is open is a way
        out: the state goes, the reply keyboard goes, the list comes as a new
        message (a message carrying ReplyKeyboardRemove cannot carry buttons)."""
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:0")
        state = await _prompt_state()

        await _call(handle_kb_organizers, callback, state=state)

        assert await state.get_state() is None
        callback.message.edit_text.assert_not_awaited()
        (line, remove), (text, markup) = _sent(callback.message)
        assert line == "Добавление организатора отменено."
        assert isinstance(remove, ReplyKeyboardRemove)
        assert text.startswith(ORGS_HEAD)
        assert isinstance(markup, InlineKeyboardMarkup)

    async def test_another_dialog_is_left_alone(self) -> None:
        callback = _make_callback(f"adm_kb_orgs:ru:{CHAT_ID}:0")
        state = _FakeState(AdminStates.awaiting_rule_config)

        await _call(handle_kb_organizers, callback, state=state)

        assert state.clear_calls == 0
        _edited(callback)


# ---------------------------------------------------------------------------
# Remove and undo
# ---------------------------------------------------------------------------


class TestRemove:
    async def test_removes_and_offers_undo_with_the_position(self) -> None:
        callback = _make_callback(f"adm_kb_org_rm:ru:{CHAT_ID}:{ORG_USER_ID}")
        repo = _make_chat_settings_repo([ORG_USER_ID, OTHER_ID])

        await _call(handle_kb_organizer_remove, callback, chat_settings_repo=repo)

        assert _written(repo) == [OTHER_ID]
        text, markup = _edited(callback)
        assert text.startswith("<i>✓ Аня больше не организатор</i>\n\n")
        assert ("↩️ Вернуть", f"adm_kb_org_undo:ru:{CHAT_ID}:{ORG_USER_ID}:0") in _buttons(markup)
        assert "✖️ Аня" not in _labels(markup)

    async def test_the_page_the_person_was_on_is_kept(self) -> None:
        ids = [ORG_USER_ID, OTHER_ID, NEW_ID, 901, 902]
        callback = _make_callback(f"adm_kb_org_rm:ru:{CHAT_ID}:901")
        repo = _make_chat_settings_repo(ids)

        await _call(handle_kb_organizer_remove, callback, chat_settings_repo=repo)

        assert _written(repo) == [ORG_USER_ID, OTHER_ID, NEW_ID, 902]
        _, markup = _edited(callback)
        assert _labels(markup)[0] == "✖️ Глеб"
        assert "2/2" in _labels(markup)

    async def test_someone_already_gone_writes_nothing(self) -> None:
        callback = _make_callback(f"adm_kb_org_rm:ru:{CHAT_ID}:999999")
        repo = _make_chat_settings_repo([ORG_USER_ID])

        await _call(handle_kb_organizer_remove, callback, chat_settings_repo=repo)

        assert _written(repo) is None
        text, markup = _edited(callback)
        assert not text.startswith("<i>")
        assert "↩️ Вернуть" not in _labels(markup)

    async def test_a_name_with_markup_is_escaped_in_the_notice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(NAMES, ORG_USER_ID, "<b>Аня</b>")
        callback = _make_callback(f"adm_kb_org_rm:ru:{CHAT_ID}:{ORG_USER_ID}")

        await _call(handle_kb_organizer_remove, callback)

        text, _ = _edited(callback)
        assert text.startswith("<i>✓ &lt;b&gt;Аня&lt;/b&gt; больше не организатор</i>")


class TestUndo:
    async def test_puts_the_person_back_where_they_were(self) -> None:
        callback = _make_callback(f"adm_kb_org_undo:ru:{CHAT_ID}:{ORG_USER_ID}:0")
        repo = _make_chat_settings_repo([OTHER_ID])

        await _call(handle_kb_organizer_undo, callback, chat_settings_repo=repo)

        assert _written(repo) == [ORG_USER_ID, OTHER_ID]
        text, markup = _edited(callback)
        assert text.startswith("<i>✓ Аня снова организатор</i>")
        assert "↩️ Вернуть" not in _labels(markup)
        assert _labels(markup)[0] == "✖️ Аня"

    async def test_a_second_tap_writes_nothing(self) -> None:
        callback = _make_callback(f"adm_kb_org_undo:ru:{CHAT_ID}:{ORG_USER_ID}:0")
        repo = _make_chat_settings_repo([ORG_USER_ID, OTHER_ID])

        await _call(handle_kb_organizer_undo, callback, chat_settings_repo=repo)

        assert _written(repo) is None
        _edited(callback)

    async def test_a_position_past_the_end_appends(self) -> None:
        callback = _make_callback(f"adm_kb_org_undo:ru:{CHAT_ID}:{ORG_USER_ID}:40")
        repo = _make_chat_settings_repo([OTHER_ID])

        await _call(handle_kb_organizer_undo, callback, chat_settings_repo=repo)

        assert _written(repo) == [OTHER_ID, ORG_USER_ID]

    async def test_without_a_position_it_appends(self) -> None:
        callback = _make_callback(f"adm_kb_org_undo:ru:{CHAT_ID}:{ORG_USER_ID}")
        repo = _make_chat_settings_repo([OTHER_ID])

        await _call(handle_kb_organizer_undo, callback, chat_settings_repo=repo)

        assert _written(repo) == [OTHER_ID, ORG_USER_ID]

    async def test_remove_then_undo_round_trips(self) -> None:
        """The undo button the remove drew, fed back: the list is as it was."""
        ids = [ORG_USER_ID, OTHER_ID, NEW_ID, 901]
        repo = _make_chat_settings_repo(ids)
        remove = _make_callback(f"adm_kb_org_rm:ru:{CHAT_ID}:{NEW_ID}")
        await _call(handle_kb_organizer_remove, remove, chat_settings_repo=repo)
        _, markup = _edited(remove)
        undo = dict(_buttons(markup))["↩️ Вернуть"]

        after = _make_chat_settings_repo(_written(repo))
        callback = _make_callback(undo)
        await _call(handle_kb_organizer_undo, callback, chat_settings_repo=after)

        assert _written(after) == ids


# ---------------------------------------------------------------------------
# org.list and pick
# ---------------------------------------------------------------------------


class TestParticipantList:
    async def test_shows_a_page_with_current_organizers_marked(self) -> None:
        callback = _make_callback(f"adm_kb_org_list:ru:{CHAT_ID}:1")
        candidates = [
            {"user_id": ORG_USER_ID, "first_name": "Аня", "username": "anya"},
            {"user_id": NEW_ID, "first_name": "Новый", "username": None},
        ]
        message_repo = _make_message_repo(top_active_users=(candidates, 12))

        await _call(handle_kb_organizer_list, callback, message_repo=message_repo)

        message_repo.get_top_active_users.assert_awaited_once_with(CHAT_ID, 1, orgs.LIST_PER_PAGE)
        text, markup = _edited(callback)
        assert "✓ — уже организатор." in text
        assert _buttons(markup)[:2] == [
            ("✓ Аня @anya", f"adm_kb_org_pick:ru:{CHAT_ID}:{ORG_USER_ID}"),
            ("Новый", f"adm_kb_org_pick:ru:{CHAT_ID}:{NEW_ID}"),
        ]
        assert _labels(markup)[-2:] == ["✏️ Нет в списке", "⬅️ Организаторы"]
        assert "2/3" in _labels(markup)

    async def test_nobody_seen_says_so_and_offers_the_other_way(self) -> None:
        callback = _make_callback(f"adm_kb_org_list:ru:{CHAT_ID}:0")

        await _call(handle_kb_organizer_list, callback, message_repo=_make_message_repo())

        text, markup = _edited(callback)
        assert "Бот пока не видел здесь ни одного сообщения." in text
        assert _labels(markup) == ["✏️ Нет в списке", "⬅️ Организаторы"]

    async def test_a_page_past_the_end_is_fetched_again_clamped(self) -> None:
        callback = _make_callback(f"adm_kb_org_list:ru:{CHAT_ID}:7")
        message_repo = _make_message_repo(top_active_users=([], 5))

        await _call(handle_kb_organizer_list, callback, message_repo=message_repo)

        assert [c.args for c in message_repo.get_top_active_users.await_args_list] == [
            (CHAT_ID, 7, orgs.LIST_PER_PAGE),
            (CHAT_ID, 1, orgs.LIST_PER_PAGE),
        ]


class TestPick:
    async def test_adds_and_shows_the_list_with_a_notice(self) -> None:
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:{NEW_ID}")
        repo = _make_chat_settings_repo([ORG_USER_ID])
        state = _FakeState()

        await _call(handle_kb_organizer_pick, callback, chat_settings_repo=repo, state=state)

        assert _written(repo) == [ORG_USER_ID, NEW_ID]
        text, markup = _edited(callback)
        assert text.startswith("<i>✓ @newbie теперь организатор</i>")
        assert _labels(markup)[:2] == ["✖️ Аня", "✖️ @newbie"]
        assert state.clear_calls == 0

    async def test_the_list_opens_on_the_new_persons_page(self) -> None:
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:{NEW_ID}")
        repo = _make_chat_settings_repo([ORG_USER_ID, OTHER_ID, 901])

        await _call(handle_kb_organizer_pick, callback, chat_settings_repo=repo, state=_FakeState())

        _, markup = _edited(callback)
        assert _labels(markup)[0] == "✖️ @newbie"
        assert "2/2" in _labels(markup)

    async def test_an_organizer_already_gets_a_toast_only(self) -> None:
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:{ORG_USER_ID}")
        repo = _make_chat_settings_repo([ORG_USER_ID])

        await _call(handle_kb_organizer_pick, callback, chat_settings_repo=repo, state=_FakeState())

        assert _written(repo) is None
        callback.answer.assert_awaited_once_with("Уже организатор")
        callback.message.edit_text.assert_not_awaited()

    async def test_from_inside_the_prompt_it_ends_the_prompt(self) -> None:
        """Picking from the list must not leave the prompt armed: a later text
        would be read as a username. The reply keyboard goes too."""
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:{NEW_ID}")
        repo = _make_chat_settings_repo([ORG_USER_ID])
        state = await _prompt_state()

        await _call(handle_kb_organizer_pick, callback, chat_settings_repo=repo, state=state)

        assert _written(repo) == [ORG_USER_ID, NEW_ID]
        assert await state.get_state() is None
        (line, remove), (text, markup) = _sent(callback.message)
        assert line == "✓ @newbie теперь организатор."
        assert isinstance(remove, ReplyKeyboardRemove)
        assert text.startswith(ORGS_HEAD)
        assert isinstance(markup, InlineKeyboardMarkup)


# ---------------------------------------------------------------------------
# org.add and its exits
# ---------------------------------------------------------------------------


class TestAddPrompt:
    async def test_sends_the_prompt_with_telegrams_person_search(self) -> None:
        callback = _make_callback(f"adm_kb_org_add:ru:{CHAT_ID}")
        state = _FakeState()

        await _call(handle_kb_organizer_add_prompt, callback, state=state)

        callback.message.edit_text.assert_not_awaited()
        ((text, keyboard),) = _sent(callback.message)
        assert text.startswith("👥 <b>Новый организатор</b> · Тестовый чат")
        assert isinstance(keyboard, ReplyKeyboardMarkup)
        assert keyboard.keyboard[0][0].request_users is not None
        assert keyboard.keyboard[1][0].text == "✖️ Отмена"
        assert keyboard.input_field_placeholder == "@username"
        assert await state.get_state() == AdminStates.awaiting_kb_organizer.state
        assert await state.get_data() == {"kb_chat_id": CHAT_ID, "kb_lang": "ru"}

    async def test_an_old_button_with_an_origin_token_still_works(self) -> None:
        callback = _make_callback(f"adm_kb_org_add:ru:{CHAT_ID}:p")
        state = _FakeState()

        await _call(handle_kb_organizer_add_prompt, callback, state=state)

        assert (await state.get_data())["kb_chat_id"] == CHAT_ID


class TestInlineCancel:
    async def test_ends_the_prompt_and_takes_the_keyboard_away(self) -> None:
        callback = _make_callback(f"adm_kb_org_x:ru:{CHAT_ID}")
        state = await _prompt_state()

        await _call(handle_kb_organizer_add_cancel, callback, state=state)

        assert await state.get_state() is None
        (line, remove), (text, markup) = _sent(callback.message)
        assert line == "Добавление организатора отменено."
        assert isinstance(remove, ReplyKeyboardRemove)
        assert text.startswith(ORGS_HEAD)
        assert isinstance(markup, InlineKeyboardMarkup)

    async def test_it_does_not_end_an_unrelated_dialog(self) -> None:
        callback = _make_callback(f"adm_kb_org_x:ru:{CHAT_ID}")
        state = _FakeState(AdminStates.awaiting_rule_config)
        await state.update_data(rule_chat_id=CHAT_ID)

        await _call(handle_kb_organizer_add_cancel, callback, state=state)

        assert state.clear_calls == 0
        assert (await state.get_data()) == {"rule_chat_id": CHAT_ID}
        text, _ = _edited(callback)
        assert text.startswith(ORGS_HEAD)


# ---------------------------------------------------------------------------
# The reader: a forward or an @username
# ---------------------------------------------------------------------------


def _reply(*, forward_origin: object = None, text: str | None = None) -> MagicMock:
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(id=ADMIN_ID)
    message.forward_origin = forward_origin
    message.text = text
    message.answer = AsyncMock()
    message.bot = None
    return message


async def _read(
    message: MagicMock,
    *,
    message_repo: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock, _FakeState]:
    repo = _make_chat_settings_repo()
    message_repo = message_repo or _make_message_repo()
    state = await _prompt_state()
    await handle_kb_organizer_add_reply(
        message,
        repo,
        _make_bot_config_repo(),
        message_repo,
        state,  # type: ignore[arg-type]
    )
    return repo, message_repo, state


def _user_origin(username: str | None = "newbie") -> MessageOriginUser:
    return MessageOriginUser(
        date=datetime.now(),
        sender_user=User(id=NEW_ID, is_bot=False, first_name="Новый", username=username),
    )


class TestReaderAdds:
    async def test_a_forward_adds_its_author(self) -> None:
        message = _reply(forward_origin=_user_origin())

        repo, _, state = await _read(message)

        assert _written(repo) == [ORG_USER_ID, NEW_ID]
        assert await state.get_state() is None
        (line, remove), (text, markup) = _sent(message)
        assert line == "✓ @newbie теперь организатор."
        assert isinstance(remove, ReplyKeyboardRemove)
        assert text.startswith(ORGS_HEAD)
        assert "✖️ @newbie" in _labels(markup)

    async def test_a_forward_from_someone_without_a_username_uses_the_name(self) -> None:
        message = _reply(forward_origin=_user_origin(username=None))

        await _read(message)

        assert _sent(message)[0][0] == "✓ Новый теперь организатор."

    async def test_a_username_seen_in_the_chat_adds(self) -> None:
        message = _reply(text="@newbie")
        message_repo = _make_message_repo(found={"user_id": NEW_ID})

        repo, message_repo, _ = await _read(message, message_repo=message_repo)

        message_repo.find_by_username.assert_awaited_once_with(CHAT_ID, "newbie")
        assert _written(repo) == [ORG_USER_ID, NEW_ID]
        assert _sent(message)[0][0] == "✓ @newbie теперь организатор."

    async def test_someone_already_on_the_list_is_said_so(self) -> None:
        message = _reply(text="@anya_org")
        message_repo = _make_message_repo(found={"user_id": ORG_USER_ID})

        repo, _, state = await _read(message, message_repo=message_repo)

        assert _written(repo) is None
        assert _sent(message)[0][0] == "✓ @anya_org уже организатор."
        assert await state.get_state() is None


_HIDDEN = MessageOriginHiddenUser(date=datetime.now(), sender_user_name="Скрытый")
_CHANNEL = MessageOriginChannel(
    date=datetime.now(), chat=Chat(id=-1009999, type="channel"), message_id=1
)
_REASONS = {
    "hidden": "приватность пересылки",
    "channel": "переслано из канала или группы",
    "elsewhere": "не видел его в этом чате",
    "not_found": "Не нашёл такого участника",
}


class TestReaderFails:
    """Each case its own words; the prompt stays open for the next try."""

    @pytest.mark.parametrize(
        ("message_kwargs", "message_repo_kwargs", "reason"),
        [
            ({"forward_origin": _HIDDEN}, {}, "hidden"),
            ({"forward_origin": _CHANNEL}, {}, "channel"),
            ({"text": "@elsewhere_user"}, {"known_elsewhere": True}, "elsewhere"),
            ({"text": "@totally_unknown"}, {}, "not_found"),
            ({"text": None}, {}, "not_found"),
        ],
        ids=["hidden", "channel", "elsewhere", "not_found", "no_text"],
    )
    async def test_says_why_and_keeps_the_prompt_open(
        self,
        message_kwargs: dict[str, Any],
        message_repo_kwargs: dict[str, Any],
        reason: str,
    ) -> None:
        message = _reply(**message_kwargs)

        repo, _, state = await _read(
            message, message_repo=_make_message_repo(**message_repo_kwargs)
        )

        assert _written(repo) is None
        ((text, markup),) = _sent(message)
        assert [key for key, words in _REASONS.items() if words in text] == [reason]
        assert _buttons(markup) == [
            ("👥 Из участников", f"adm_kb_org_list:ru:{CHAT_ID}:0"),
            ("✖️ Отмена", f"adm_kb_org_x:ru:{CHAT_ID}"),
        ]
        assert await state.get_state() == AdminStates.awaiting_kb_organizer.state
        assert await state.get_data() == {"kb_chat_id": CHAT_ID, "kb_lang": "ru"}

    async def test_no_text_does_not_query_the_history(self) -> None:
        _, message_repo, _ = await _read(_reply(text=None))

        message_repo.find_by_username.assert_not_awaited()

    async def test_a_non_admin_is_cleared_and_answered_nothing(self) -> None:
        message = _reply(text="@newbie")
        message.from_user = MagicMock(id=999)

        repo, _, state = await _read(message)

        assert _written(repo) is None
        assert _sent(message) == []
        assert await state.get_state() is None
