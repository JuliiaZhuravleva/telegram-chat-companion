"""Finding a rule's person with Telegram's own search (``request_users``).

The search is the rules area's one reply keyboard. Its picks carry their own
``request_id`` (``REQUEST_IDS.RULE_WHO``), and the knowledge base's organizer
picker (``REQUEST_IDS.ORG_PICK``) is matched in ``admin_kb``, whose router runs
first. So the routing tests below go through the REAL handler chain
(``src.bot.handlers.router``): a test calling ``handle_builder_found`` directly
would stay green with the two pickers taking each other's picks.

Every way out of the search takes its keyboard away: a one-time keyboard only
hides, and stays behind the input's icon. That includes the two ways out that
are not the search's own: a name tapped in the inline picker, and an app with no
person search sending the button's label back as text.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageReplyMarkup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    SharedUser,
    UsersShared,
)

from src.bot.handlers import admin_kb
from src.bot.handlers import rules as handlers
from src.bot.screens import rules as screens
from src.bot.screens.render import render_text, to_markup
from src.bot.screens.routes import REQUEST_IDS
from src.bot.states.admin import AdminStates
from src.models.chat_config import ChatConfig
from tests.unit.kb_organizer_fakes import no_organizer_write, written
from tests.unit.test_fsm_escape import _connected, _FakeState, _make_message, _propagate
from tests.unit.test_rules_handler import CHAT_ID, _callback, _deps, _State, _tap

GROUP_ID = CHAT_ID  # invented
PICKED_ID = 7001
TITLE = "Тестовый чат"


def _pick(msg: Message, request_id: int, users: list[SharedUser] | None = None) -> Message:
    """The service message Telegram sends when the admin picks a person."""
    if users is None:
        users = [SharedUser(user_id=PICKED_ID, first_name="Аня", username="anya")]
    return msg.model_copy(
        update={"text": None, "users_shared": UsersShared(request_id=request_id, users=users)}
    )


def _sends(bot: AsyncMock) -> list[Any]:
    """Every SendMessage the handlers made, read off the bot itself."""
    return [
        call.args[0]
        for call in bot.await_args_list
        if call.args and type(call.args[0]).__name__ == "SendMessage"
    ]


async def _searching(state: _FakeState | None = None) -> _FakeState:
    """A person rule's draft with the search open, as `handle_builder_find` leaves it."""
    state = state or _FakeState(AdminStates.awaiting_rule_who)
    await state.update_data(
        rule_chat_id=GROUP_ID, rule_type="user_specific", rule_finding=GROUP_ID, lang="ru"
    )
    return state


def _with_people(data: dict[str, object]) -> list[screens.Person]:
    """The chat's most active people, for the exits that show the inline picker."""
    data["message_repo"].get_top_active_users = AsyncMock(  # type: ignore[attr-defined]
        return_value=([{"user_id": PICKED_ID, "first_name": "Аня", "username": "anya"}], 1)
    )
    return [screens.Person(PICKED_ID, "Аня", "anya")]


def _with_rules_list(data: dict[str, object]) -> None:
    """What the rules list reads, for the exits that show it."""
    service = MagicMock()
    service.get_config = AsyncMock(
        return_value=replace(ChatConfig(chat_id=GROUP_ID), rules_enabled=True)
    )
    data["chat_config_service"] = service
    data["chat_settings_repo"].get = AsyncMock(  # type: ignore[attr-defined]
        return_value={"chat_id": GROUP_ID, "chat_title": TITLE}
    )
    rules_repo = AsyncMock()
    rules_repo.get_rules_page = AsyncMock(return_value=([], 0))
    data["rules_repo"] = rules_repo


@pytest.fixture
def kb_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    # TRUE on purpose: unpatched, the KB's own admin check comes back falsy
    # against a mock repository and the KB handler returns before doing
    # anything, so "the KB did not take it" would pass vacuously.
    monkeypatch.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))


# ---------------------------------------------------------------------------
# Opening the search
# ---------------------------------------------------------------------------


class TestOpeningTheSearch:
    async def _to_who(self, deps: dict[str, Any], state: _State) -> None:
        await _tap(handlers.handle_add_rule, f"ar_add:ru:{CHAT_ID}", state, deps)
        await _tap(handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:u", state, deps)

    async def test_sends_the_search_keyboard_with_its_own_request_id(self) -> None:
        deps, state = _deps(), _State()
        await self._to_who(deps, state)
        with patch.object(handlers, "safe_edit_reply_markup", new_callable=AsyncMock) as retire:
            callback = await _tap(
                handlers.handle_builder_find, f"ar_b_find:ru:{CHAT_ID}", state, deps
            )

        args, kwargs = callback.message.answer.await_args
        assert args[0] == render_text(screens.who_find_screen("ru"))
        markup = kwargs["reply_markup"]
        assert isinstance(markup, ReplyKeyboardMarkup)
        find, cancel = markup.keyboard[0][0], markup.keyboard[1][0]
        assert find.text == screens.FIND("ru")
        assert find.request_users is not None
        assert find.request_users.request_id == REQUEST_IDS.RULE_WHO
        assert find.request_users.request_id != REQUEST_IDS.ORG_PICK
        assert cancel.text == "✖️ Отмена"
        assert markup.input_field_placeholder == screens.FIND_PLACEHOLDER("ru")
        assert state.data["rule_finding"] == CHAT_ID
        assert await state.get_state() == AdminStates.awaiting_rule_who.state
        # The picker above loses its buttons: the search's Cancel is the one way out.
        assert retire.await_args.args[0] is callback.message
        assert retire.await_args.kwargs == {"reply_markup": None}

    async def test_a_picker_telegram_will_not_edit_is_logged_not_raised(self) -> None:
        deps, state = _deps(), _State()
        await self._to_who(deps, state)
        refused = TelegramBadRequest(
            method=EditMessageReplyMarkup(chat_id=1, message_id=1),
            message="message can't be edited",
        )
        with (
            patch.object(handlers, "safe_edit_reply_markup", AsyncMock(side_effect=refused)),
            patch.object(handlers, "logger") as logger,
        ):
            await _tap(handlers.handle_builder_find, f"ar_b_find:ru:{CHAT_ID}", state, deps)

        assert state.data["rule_finding"] == CHAT_ID
        assert logger.info.call_args.args[0] == "rule_who_keyboard_kept"

    @pytest.mark.parametrize(
        ("draft", "data"),
        [
            ({}, f"ar_b_find:ru:{CHAT_ID}"),
            ({"rule_chat_id": CHAT_ID, "rule_type": "spam_detect"}, f"ar_b_find:ru:{CHAT_ID}"),
            (
                {"rule_chat_id": -1008888888888, "rule_type": "user_specific"},
                f"ar_b_find:ru:{CHAT_ID}",
            ),
            ({"rule_chat_id": CHAT_ID, "rule_type": "user_specific"}, "ar_b_find:ru:chat"),
        ],
        ids=["no-draft", "another-kind", "another-chat", "garbled"],
    )
    async def test_without_this_chats_person_draft_is_stale(
        self, draft: dict[str, Any], data: str
    ) -> None:
        state = _State(**draft)
        with patch.object(handlers, "show_stale", new_callable=AsyncMock) as stale:
            callback = await _tap(handlers.handle_builder_find, data, state, _deps())

        stale.assert_awaited_once()
        callback.message.answer.assert_not_awaited()
        assert state.data.get("rule_finding") is None

    async def test_a_non_admin_gets_an_alert(self) -> None:
        callback = _callback(f"ar_b_find:ru:{CHAT_ID}")
        await handlers.handle_builder_find(callback, _State(), **{**_deps(), "is_admin": False})

        assert callback.answer.await_args.kwargs.get("show_alert") is True
        callback.message.answer.assert_not_awaited()


# ---------------------------------------------------------------------------
# A pick, through the real router
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("kb_admin")
class TestAPickReachesItsOwnPicker:
    async def test_a_rules_pick_becomes_the_rules_person(self) -> None:
        msg, data, bot = _make_message()
        state = await _searching()

        await _propagate(_pick(msg, REQUEST_IDS.RULE_WHO), data, state)

        assert state._data["rule_user"] == PICKED_ID
        assert state._data["rule_name"] == "Аня @anya"
        assert state._data["rule_finding"] is None
        assert await state.get_state() is None
        line, screen = _sends(bot)
        assert line.text == "Выбран человек: Аня @anya"
        assert isinstance(line.reply_markup, ReplyKeyboardRemove)
        assert screen.text == render_text(screens.action_screen("ru", GROUP_ID, "Аня @anya"))
        assert no_organizer_write(data["chat_settings_repo"])

    async def test_a_rules_pick_after_the_kb_prompt_took_over_is_stale_and_not_the_kbs(
        self,
    ) -> None:
        """Opening the KB prompt ended the search (a foreign tap), so a pick from
        its keyboard is stale: never a rule person, and never read by the KB's
        forward/@username reader, which would take a message with no text."""
        msg, data, bot = _make_message()
        state = await _searching(_FakeState(AdminStates.awaiting_kb_organizer))
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")

        await _propagate(_pick(msg, REQUEST_IDS.RULE_WHO), data, state)

        assert state._data.get("rule_user") is None
        assert [s.text for s in _sends(bot)] == [screens.WHO_STALE("ru")]
        assert no_organizer_write(data["chat_settings_repo"])
        assert await state.get_state() == AdminStates.awaiting_kb_organizer.state

    async def test_a_kb_pick_is_the_kbs_while_the_rules_search_is_open(self) -> None:
        msg, data, bot = _make_message()
        _connected(data)
        bot.get_chat_member = AsyncMock(return_value=MagicMock(status="member"))
        state = await _searching(_FakeState(AdminStates.awaiting_kb_organizer))
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")

        await _propagate(_pick(msg, REQUEST_IDS.ORG_PICK), data, state)

        assert state._data.get("rule_user") is None, "the rules picker took the KB's pick"
        assert written(data["chat_settings_repo"], -100123) == [PICKED_ID]

    @pytest.mark.parametrize("request_id", [999, 0], ids=["unknown-id", "zero"])
    async def test_a_pick_with_another_request_id_is_never_the_rules(self, request_id: int) -> None:
        """The picker's own ``request_id`` filter: no other picker's pick is ever
        the rule's person, even while the rules search is open and waiting."""
        assert request_id not in set(REQUEST_IDS)
        msg, data, bot = _make_message()
        state = await _searching()

        await _propagate(_pick(msg, request_id), data, state)

        assert state._data.get("rule_user") is None, "the rules picker took another pick"
        assert state._data["rule_finding"] == GROUP_ID, "the rules search was not touched"
        assert screens.WHO_PICKED("ru").format(name="Аня @anya") not in [
            s.text for s in _sends(bot)
        ]

    async def test_a_stale_kb_pick_is_never_the_rules(self) -> None:
        msg, data, bot = _make_message()
        state = await _searching()

        await _propagate(_pick(msg, REQUEST_IDS.ORG_PICK), data, state)

        assert state._data.get("rule_user") is None
        assert state._data["rule_finding"] == GROUP_ID, "the rules search was not touched"
        assert [s.text for s in _sends(bot)] == [admin_kb._PICK_STALE["ru"]]


# ---------------------------------------------------------------------------
# The other ways out, through the real router
# ---------------------------------------------------------------------------


class TestEveryWayOutRemovesTheKeyboard:
    @pytest.mark.parametrize("users", [None, []], ids=["no-search-open", "nobody-in-the-pick"])
    async def test_a_stale_pick_says_so(self, users: list[SharedUser] | None) -> None:
        msg, data, bot = _make_message()
        state = _FakeState() if users is None else await _searching()

        await _propagate(_pick(msg, REQUEST_IDS.RULE_WHO, users), data, state)

        (line,) = _sends(bot)
        assert line.text == screens.WHO_STALE("ru")
        assert isinstance(line.reply_markup, ReplyKeyboardRemove)
        assert state._data.get("rule_user") is None
        assert state._data.get("rule_finding") is None

    async def test_a_pick_after_the_draft_moved_on_is_stale(self) -> None:
        """A new rule started meanwhile: the search's chat no longer has a person draft."""
        msg, data, bot = _make_message()
        state = await _searching()
        await state.update_data(rule_type="spam_detect")

        await _propagate(_pick(msg, REQUEST_IDS.RULE_WHO), data, state)

        assert [s.text for s in _sends(bot)] == [screens.WHO_STALE("ru")]
        assert state._data.get("rule_user") is None

    @pytest.mark.parametrize("label", ["✖️ Отмена", "Отмена"], ids=["with-glyph", "bare"])
    async def test_cancel_makes_no_rule_and_shows_the_chats_rules(self, label: str) -> None:
        msg, data, bot = _make_message(text=label)
        _with_rules_list(data)
        state = await _searching()

        await _propagate(msg, data, state)

        line, rules = _sends(bot)
        assert line.text == screens.WHO_CANCELLED("ru")
        assert isinstance(line.reply_markup, ReplyKeyboardRemove)
        expected = screens.rules_screen(
            "ru",
            chat_id=GROUP_ID,
            title=TITLE,
            # The row has no ``rules_enabled`` of its own: the value is inherited.
            chat=screens.ChatRules(enabled=True, common=True),
            rules=[],
        )
        assert rules.text == render_text(expected)
        assert all(state._data[k] is None for k in handlers._DRAFT_KEYS)
        assert await state.get_state() is None

    async def test_cancel_in_english(self) -> None:
        msg, data, bot = _make_message(text="✖️ Cancel")
        _with_rules_list(data)
        state = await _searching()
        await state.update_data(lang="en")

        await _propagate(msg, data, state)

        assert _sends(bot)[0].text == screens.WHO_CANCELLED("en")

    async def test_the_cancel_word_with_no_search_open_is_not_the_rules(self) -> None:
        """Without the flag «Отмена» is ordinary text: the rules area must not answer it."""
        msg, data, bot = _make_message(text="✖️ Отмена")
        _with_rules_list(data)
        state = _FakeState()

        await _propagate(msg, data, state)

        assert screens.WHO_CANCELLED("ru") not in [s.text for s in _sends(bot)]

    async def test_slash_cancel_ends_the_search_and_removes_the_keyboard(self) -> None:
        msg, data, bot = _make_message(text="/cancel")
        state = await _searching()

        await _propagate(msg, data, state)

        assert state.clear_calls == 1
        assert "rule_finding" not in state._data
        (line,) = _sends(bot)
        assert isinstance(line.reply_markup, ReplyKeyboardRemove)


# ---------------------------------------------------------------------------
# An app without the person search, through the real router
# ---------------------------------------------------------------------------


class TestAnAppWithoutTheSearch:
    """An app that cannot open ``request_users`` sends the button's label as text."""

    @pytest.mark.parametrize("label", ["🔍 Найти в Telegram", "Найти в Telegram"])
    async def test_the_label_says_so_and_brings_back_the_chats_people(self, label: str) -> None:
        msg, data, bot = _make_message(text=label)
        people = _with_people(data)
        state = await _searching()

        await _propagate(msg, data, state)

        line, picker = _sends(bot)
        assert line.text == "Это приложение Telegram не открывает поиск."
        assert isinstance(line.reply_markup, ReplyKeyboardRemove)
        expected = screens.who_screen("ru", GROUP_ID, people, find=False)
        assert picker.text == render_text(expected)
        assert picker.reply_markup == to_markup(expected)
        labels = [b.text for row in picker.reply_markup.inline_keyboard for b in row]
        assert screens.FIND("ru") not in labels, "the button that opened nothing is back"
        assert labels[-1] == "✖️ Отмена"
        # The search is over; the draft is not.
        assert await state.get_state() is None
        assert state._data["rule_finding"] is None
        assert state._data["rule_type"] == "user_specific"
        assert state._data["rule_people"] == {str(PICKED_ID): "Аня @anya"}

    async def test_in_english(self) -> None:
        msg, data, bot = _make_message(text="🔍 Find in Telegram")
        _with_people(data)
        state = await _searching()
        await state.update_data(lang="en")

        await _propagate(msg, data, state)

        assert _sends(bot)[0].text == screens.WHO_UNSUPPORTED("en")

    async def test_a_name_from_that_picker_is_the_rules_person(self) -> None:
        msg, data, bot = _make_message(text="🔍 Найти в Telegram")
        _with_people(data)
        state = await _searching()
        await _propagate(msg, data, state)
        fsm = _State(**state._data)

        callback = await _tap(
            handlers.handle_builder_who, f"ar_b_who:ru:{GROUP_ID}:{PICKED_ID}", fsm, _deps()
        )

        assert fsm.data["rule_user"] == PICKED_ID
        # Nothing left to take away: the picker turns into the next step in place.
        callback.message.answer.assert_not_awaited()
        assert callback.message.edit_text.await_args.args[0] == render_text(
            screens.action_screen("ru", GROUP_ID, "Аня @anya")
        )

    async def test_its_pages_stay_without_the_button(self) -> None:
        """The pager re-draws the picker: the button must not come back on page two."""
        state = _State(rule_chat_id=CHAT_ID, rule_type="user_specific", rule_no_find=True)
        people = [{"user_id": PICKED_ID + i, "first_name": f"Имя{i}"} for i in range(4)]
        deps = _deps(people=people)
        deps["message_repo"].get_top_active_users = AsyncMock(return_value=(people, 9))

        callback = await _tap(
            handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:u:1", state, deps
        )

        labels = [
            b.text
            for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for b in row
        ]
        assert screens.FIND("ru") not in labels
        assert "✖️ Отмена" in labels

    async def test_a_new_rule_offers_the_search_again(self) -> None:
        state = _State(rule_chat_id=CHAT_ID, rule_type="user_specific", rule_no_find=True)
        deps = _deps()
        await _tap(handlers.handle_add_rule, f"ar_add:ru:{CHAT_ID}", state, deps)

        assert state.data["rule_no_find"] is None

    async def test_the_label_with_no_search_open_is_not_the_rules(self) -> None:
        msg, data, bot = _make_message(text="🔍 Найти в Telegram")
        _with_people(data)
        state = _FakeState()

        await _propagate(msg, data, state)

        assert screens.WHO_UNSUPPORTED("ru") not in [s.text for s in _sends(bot)]
        assert state._data.get("rule_no_find") is None


# ---------------------------------------------------------------------------
# A name tapped in the inline picker while the search is open
# ---------------------------------------------------------------------------


class TestANameTappedWhileTheSearchIsOpen:
    """Telegram may keep the picker's buttons (``rule_who_keyboard_kept``), so a
    name stays tappable above the open search."""

    def _open(self, current: Any = AdminStates.awaiting_rule_who) -> _State:
        return _State(
            current,
            rule_chat_id=CHAT_ID,
            rule_type="user_specific",
            rule_finding=CHAT_ID,
            lang="ru",
            rule_people={str(PICKED_ID): "Аня @anya"},
        )

    @pytest.mark.parametrize(
        "current", [AdminStates.awaiting_rule_who, None], ids=["state-up", "state-dropped"]
    )
    async def test_ends_the_search_and_takes_its_keyboard_away(self, current: Any) -> None:
        state = self._open(current)
        with patch.object(handlers, "safe_edit_reply_markup", new_callable=AsyncMock) as retire:
            callback = await _tap(
                handlers.handle_builder_who, f"ar_b_who:ru:{CHAT_ID}:{PICKED_ID}", state, _deps()
            )

        assert state.data["rule_user"] == PICKED_ID
        assert state.data["rule_finding"] is None
        assert await state.get_state() is None
        (line_args, line_kwargs), (step_args, step_kwargs) = [
            (c.args, c.kwargs) for c in callback.message.answer.await_args_list
        ]
        assert line_args[0] == "Выбран человек: Аня @anya"
        assert isinstance(line_kwargs["reply_markup"], ReplyKeyboardRemove)
        expected = screens.action_screen("ru", CHAT_ID, "Аня @anya")
        assert (step_args[0], step_kwargs["reply_markup"]) == (
            render_text(expected),
            to_markup(expected),
        )
        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()
        # The picker above loses its names: the next step is the live screen.
        assert retire.await_args.args[0] is callback.message

    async def test_with_no_search_open_it_says_nothing_extra(self) -> None:
        state = self._open(None)
        state.data["rule_finding"] = None

        callback = await _tap(
            handlers.handle_builder_who, f"ar_b_who:ru:{CHAT_ID}:{PICKED_ID}", state, _deps()
        )

        callback.message.answer.assert_not_awaited()
        assert callback.message.edit_text.await_args.args[0] == render_text(
            screens.action_screen("ru", CHAT_ID, "Аня @anya")
        )
