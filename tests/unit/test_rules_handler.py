"""Rules handlers: a chat's list, a rule's card, the builder, the JSON prompt.

Every handler runs for real against fake repositories, and what it shows is
read back through `show`'s real delivery: ``edit_text`` on a tapped message,
``answer`` under a typed one. Expected screens are built with the screen
functions themselves, so a test fails on a wrong screen, not on wording.
"""

from __future__ import annotations

import asyncio
import html
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State
from aiogram.methods import EditMessageReplyMarkup
from aiogram.types import Chat, Document, Message, PhotoSize, User

from src.bot.handlers import rules as handlers
from src.bot.screens import rules as screens
from src.bot.screens.render import render_text, to_markup
from src.bot.states.admin import AdminStates
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.messages import MessageRepository
from src.database.repositories.rules import RulesRepository
from src.models.chat_config import ChatConfig
from src.services.chat_config import ChatConfigService
from src.services.modules.reactions.selector import ReactionSelector
from src.services.rules.engine import RuleEngine
from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT, parsed_length
from tests.unit.screen_routing import ADMIN_ID, deliver

CHAT_ID = -1009999999999  # invented
OTHER_CHAT = -1008888888888  # invented
RULE_ID = 42
TITLE = "Тестовый чат"
PROMPT_ID = 555

_ON = screens.ChatRules(enabled=True, common=False)
_OFF = screens.ChatRules(enabled=False, common=False)


def _rule(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": RULE_ID,
        "chat_id": CHAT_ID,
        "rule_type": "keyword_trigger",
        "config": {"name": "spam-words", "keywords": ["spam"], "action": "notify_admin"},
        "enabled": True,
        "weight": 1,
        "mandatory": False,
        "trigger_count": 0,
    }
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _State:
    """A dict-backed FSMContext: what the handlers read is what they wrote."""

    def __init__(self, current: State | None = None, **data: Any) -> None:
        self.current = current.state if current is not None else None
        self.data: dict[str, Any] = dict(data)

    async def get_state(self) -> str | None:
        return self.current

    async def set_state(self, state: State | str | None = None) -> None:
        self.current = state.state if isinstance(state, State) else state

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def update_data(
        self, data: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        self.data.update(kwargs)
        self.data.update(data or {})
        return dict(self.data)

    async def set_data(self, data: dict[str, Any]) -> None:
        self.data = dict(data)

    async def clear(self) -> None:
        self.current = None
        self.data = {}


def _callback(data: str) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock(id=1)
    callback.answer = AsyncMock()
    message = MagicMock(spec=Message)
    message.message_id = PROMPT_ID
    message.chat = MagicMock()
    message.chat.type = "private"
    message.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=MagicMock(message_id=PROMPT_ID + 1))
    callback.message = message
    return callback


def _typed(
    text: str | None = None, *, caption: str | None = None, sticker: Any = None
) -> MagicMock:
    message = MagicMock(spec=Message)
    message.text = text
    message.caption = caption
    message.sticker = sticker
    message.chat = Chat(id=1001, type="private")
    message.date = datetime(2026, 11, 1, tzinfo=UTC)
    message.bot = MagicMock()
    message.answer = AsyncMock(return_value=MagicMock(message_id=900))
    return message


def _deps(
    rule: dict[str, Any] | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
    enabled: bool = True,
    stored: bool | None = True,
    people: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rules_repo = MagicMock(spec=RulesRepository)
    rules_repo.get = AsyncMock(return_value=rule)
    listed = list(rows or [])
    rules_repo.get_rules_page = AsyncMock(return_value=(listed, len(listed)))
    rules_repo.create = AsyncMock(return_value=RULE_ID)
    rules_repo.delete = AsyncMock(return_value=True)
    rules_repo.toggle = AsyncMock(
        side_effect=lambda _rid, *, enabled: {**rule, "enabled": enabled} if rule else None
    )
    service = MagicMock(spec=ChatConfigService)
    service.get_config = AsyncMock(
        return_value=replace(ChatConfig(chat_id=CHAT_ID), rules_enabled=enabled)
    )
    settings = MagicMock(spec=ChatSettingsRepository)
    settings.get = AsyncMock(
        return_value={"chat_id": CHAT_ID, "chat_title": TITLE, "rules_enabled": enabled}
    )
    settings.toggle_bool_field = AsyncMock(return_value=stored)
    message_repo = MagicMock(spec=MessageRepository)
    found = list(people or [])
    message_repo.get_top_active_users = AsyncMock(return_value=(found, len(found)))
    return {
        "rules_repo": rules_repo,
        "chat_config_service": service,
        "chat_settings_repo": settings,
        "message_repo": message_repo,
        "is_admin": True,
    }


def _services(deps: dict[str, Any]) -> dict[str, Any]:
    """A message handler takes its services only: the admin check is its filter."""
    return {k: deps[k] for k in ("rules_repo", "chat_config_service", "chat_settings_repo")}


async def _tap(handler: Any, data: str, state: _State, deps: dict[str, Any]) -> MagicMock:
    callback = _callback(data)
    await handler(callback, state, **deps)
    return callback


def _shown(callback: MagicMock) -> tuple[str, Any]:
    """The text and keyboard the tap edited its message into."""
    args, kwargs = callback.message.edit_text.await_args
    return args[0], kwargs["reply_markup"]


def _sent(message: MagicMock) -> tuple[str, Any]:
    """The text and keyboard answered under a message."""
    args, kwargs = message.answer.await_args
    return args[0], kwargs["reply_markup"]


def _datas(markup: Any) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def _labels(markup: Any) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _same(shown: tuple[str, Any], screen: Any) -> bool:
    return shown == (render_text(screen), to_markup(screen))


@pytest.fixture(autouse=True)
def stale() -> Iterator[AsyncMock]:
    with patch.object(handlers, "show_stale", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture(autouse=True)
def retire() -> Iterator[AsyncMock]:
    """The prompt above a typed answer loses its keyboard through this call."""
    with patch.object(handlers, "safe_edit_reply_markup", new_callable=AsyncMock) as mock:
        yield mock


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


class TestTheList:
    async def test_shows_the_chats_rules(self) -> None:
        deps = _deps(rows=[_rule()])
        callback = await _tap(handlers.handle_rules_list, f"ar_list:ru:{CHAT_ID}:0", _State(), deps)

        expected = screens.rules_screen(
            "ru", chat_id=CHAT_ID, title=TITLE, chat=_ON, rules=[screens.rule_view(_rule())]
        )
        assert _same(_shown(callback), expected)

    @pytest.mark.parametrize("enabled", [True, False])
    async def test_rows_open_a_card_and_neither_pause_nor_delete(self, enabled: bool) -> None:
        """Pause and delete live on the rule's card; a row only opens it."""
        rows = [_rule(), _rule(id=43, enabled=False), _rule(id=44)]
        callback = await _tap(
            handlers.handle_rules_list,
            f"ar_list:ru:{CHAT_ID}:0",
            _State(),
            _deps(rows=rows, enabled=enabled),
        )

        datas = _datas(_shown(callback)[1])
        assert [d for d in datas if d.startswith(("ar_tog:", "ar_del"))] == []
        assert [d for d in datas if d.startswith("ar_view:")] == [
            f"ar_view:ru:{rid}:{CHAT_ID}" for rid in (42, 43, 44)
        ]
        assert f"ar_all:ru:{CHAT_ID}" in datas

    async def test_a_page_past_the_end_shows_the_last_one(self) -> None:
        """A list that shrank under an old pager button: the last page, not an empty one."""
        deps = _deps()
        last = [_rule(id=50)]
        deps["rules_repo"].get_rules_page = AsyncMock(side_effect=[([], 4), (last, 4)])

        callback = await _tap(handlers.handle_rules_list, f"ar_list:ru:{CHAT_ID}:5", _State(), deps)

        assert deps["rules_repo"].get_rules_page.await_args.args == (CHAT_ID, 1, screens.PER_PAGE)
        expected = screens.rules_screen(
            "ru",
            chat_id=CHAT_ID,
            title=TITLE,
            chat=_ON,
            rules=[screens.rule_view(last[0])],
            page=1,
            pages=2,
        )
        assert _same(_shown(callback), expected)

    async def test_an_untitled_chat_is_named_by_its_id(self) -> None:
        deps = _deps()
        deps["chat_settings_repo"].get = AsyncMock(return_value=None)

        callback = await _tap(handlers.handle_rules_list, f"ar_list:en:{CHAT_ID}:0", _State(), deps)

        assert screens.chat_id_text(CHAT_ID) in _shown(callback)[0]

    @pytest.mark.parametrize("data", ["ar_list:ru:abc:0", "ar_list:ru", "ar_list:ru::0"])
    async def test_a_garbled_payload_is_stale(self, data: str, stale: AsyncMock) -> None:
        deps = _deps()
        callback = await _tap(handlers.handle_rules_list, data, _State(), deps)

        stale.assert_awaited_once()
        callback.message.edit_text.assert_not_awaited()
        deps["rules_repo"].get_rules_page.assert_not_awaited()

    async def test_a_non_admin_gets_an_alert(self) -> None:
        deps = {**_deps(), "is_admin": False}
        callback = await _tap(handlers.handle_rules_list, f"ar_list:ru:{CHAT_ID}:0", _State(), deps)

        assert callback.answer.await_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# The switch for all of a chat's rules
# ---------------------------------------------------------------------------


class TestTheMasterSwitch:
    @pytest.mark.parametrize("effective", [True, False])
    async def test_flips_from_what_the_chat_does(self, effective: bool) -> None:
        """NULL-aware: the repository flips the EFFECTIVE value inside its UPDATE."""
        deps = _deps(enabled=effective, stored=not effective)
        await _tap(handlers.handle_rules_all, f"ar_all:ru:{CHAT_ID}", _State(), deps)

        deps["chat_settings_repo"].toggle_bool_field.assert_awaited_once_with(
            CHAT_ID, "rules_enabled", effective
        )
        deps["chat_config_service"].invalidate.assert_called_once_with(CHAT_ID)

    async def test_an_inherited_value_is_said_in_the_text_not_on_the_button(self) -> None:
        deps = _deps(enabled=False)
        deps["chat_settings_repo"].get = AsyncMock(
            return_value={"chat_id": CHAT_ID, "chat_title": TITLE, "rules_enabled": None}
        )
        callback = await _tap(handlers.handle_rules_list, f"ar_list:ru:{CHAT_ID}:0", _State(), deps)

        text, markup = _shown(callback)
        assert "⚪ Правила выключены" in _labels(markup)
        assert "Берутся из общих настроек: Правила." in text

    async def test_a_value_set_for_the_chat_names_no_origin(self) -> None:
        deps = _deps(enabled=False)
        deps["chat_settings_repo"].get = AsyncMock(
            return_value={"chat_id": CHAT_ID, "chat_title": TITLE, "rules_enabled": False}
        )
        callback = await _tap(handlers.handle_rules_list, f"ar_list:ru:{CHAT_ID}:0", _State(), deps)

        assert "Берутся из общих настроек" not in _shown(callback)[0]

    async def test_from_a_card_redraws_the_card_saying_what_changed(self) -> None:
        deps = _deps(_rule(), enabled=False, stored=False)
        callback = await _tap(
            handlers.handle_rules_all, f"ar_all:ru:{CHAT_ID}:{RULE_ID}", _State(), deps
        )

        expected = screens.rule_screen(
            "ru", screens.rule_view(_rule()), chat=_OFF, notice=screens.RULES_OFF("ru")
        )
        assert _same(_shown(callback), expected)
        assert _shown(callback)[0].startswith("<i>✓ Правила выключены</i>")

    @pytest.mark.parametrize("lang", ["ru", "en"])
    @pytest.mark.parametrize("stored", [True, False], ids=["on", "off"])
    async def test_the_notice_repeats_the_switch_the_admin_now_sees(
        self, stored: bool, lang: str
    ) -> None:
        """One name: the line over the list is the button under it, without its glyph."""
        deps = _deps(enabled=stored, stored=stored)
        callback = await _tap(handlers.handle_rules_all, f"ar_all:{lang}:{CHAT_ID}", _State(), deps)

        text, markup = _shown(callback)
        switch = _labels(markup)[0]
        assert switch.startswith(("✅ ", "⚪ "))
        assert text.startswith(f"<i>✓ {switch[2:]}</i>"), (text.splitlines()[0], switch)

    @pytest.mark.parametrize(
        ("stored", "says"),
        [(True, "<i>✓ Правила включены</i>"), (False, "<i>✓ Правила выключены</i>")],
        ids=["on", "off"],
    )
    async def test_from_the_list_redraws_it_saying_what_changed(
        self, stored: bool, says: str
    ) -> None:
        deps = _deps(enabled=stored, stored=stored)
        callback = await _tap(handlers.handle_rules_all, f"ar_all:ru:{CHAT_ID}", _State(), deps)

        notice = (screens.RULES_ON if stored else screens.RULES_OFF)("ru")
        chat = _ON if stored else _OFF
        expected = screens.rules_screen(
            "ru", chat_id=CHAT_ID, title=TITLE, chat=chat, rules=[], notice=notice
        )
        assert _same(_shown(callback), expected)
        assert _shown(callback)[0].startswith(says)

    async def test_from_a_card_whose_rule_is_gone_shows_the_list_saying_both(self) -> None:
        deps = _deps(None)
        callback = await _tap(
            handlers.handle_rules_all, f"ar_all:ru:{CHAT_ID}:{RULE_ID}", _State(), deps
        )

        expected = screens.gone_screen(
            "ru", chat_id=CHAT_ID, title=TITLE, chat=_ON, rules=[], notice=screens.RULES_ON("ru")
        )
        assert _same(_shown(callback), expected)
        text = _shown(callback)[0]
        assert text.startswith("<i>✓ Правила включены</i>\n" + screens.GONE_NOTICE("ru"))

    async def test_no_settings_row_is_stale_and_logged(self, stale: AsyncMock) -> None:
        deps = _deps(stored=None)
        with patch.object(handlers, "logger") as logger:
            await _tap(handlers.handle_rules_all, f"ar_all:ru:{CHAT_ID}", _State(), deps)

        stale.assert_awaited_once()
        deps["chat_config_service"].invalidate.assert_not_called()
        logger.warning.assert_called_once_with("rules_toggle_no_row", chat_id=CHAT_ID)

    async def test_a_garbled_rule_id_is_stale_before_anything_is_written(
        self, stale: AsyncMock
    ) -> None:
        deps = _deps()
        await _tap(handlers.handle_rules_all, f"ar_all:ru:{CHAT_ID}:x", _State(), deps)

        stale.assert_awaited_once()
        deps["chat_settings_repo"].toggle_bool_field.assert_not_awaited()


# ---------------------------------------------------------------------------
# One rule: its card, pause, delete
# ---------------------------------------------------------------------------


class TestTheCard:
    async def test_shows_the_rule(self) -> None:
        callback = await _tap(
            handlers.handle_rule_view, f"ar_view:ru:{RULE_ID}:{CHAT_ID}", _State(), _deps(_rule())
        )

        expected = screens.rule_screen("ru", screens.rule_view(_rule()), chat=_ON)
        assert _same(_shown(callback), expected)

    async def test_j_opens_its_json(self) -> None:
        callback = await _tap(
            handlers.handle_rule_view,
            f"ar_view:ru:{RULE_ID}:{CHAT_ID}:j",
            _State(),
            _deps(_rule()),
        )

        expected = screens.rule_screen("ru", screens.rule_view(_rule()), chat=_ON, show_json=True)
        assert _same(_shown(callback), expected)
        assert "<pre>" in _shown(callback)[0]

    @pytest.mark.parametrize(
        ("payload", "toggle", "goes_to"),
        [
            (f"ar_view:ru:{RULE_ID}:{CHAT_ID}", "🧩 Как записано (JSON)", ":j"),
            (f"ar_view:ru:{RULE_ID}:{CHAT_ID}:j", "🧩 Скрыть запись (JSON)", f":{CHAT_ID}"),
        ],
    )
    async def test_the_stored_form_says_what_it_is(
        self, payload: str, toggle: str, goes_to: str
    ) -> None:
        """Plain words first, JSON last; shown, it is captioned with what it is
        and with the one thing that can be done with such text."""
        callback = await _tap(handlers.handle_rule_view, payload, _State(), _deps(_rule()))
        text, markup = _shown(callback)

        assert [[b.text for b in row] for row in markup.inline_keyboard] == [
            ["⏸ На паузу", "🗑 Удалить"],
            [toggle],
            ["⬅️ Правила"],
        ]
        assert markup.inline_keyboard[1][0].callback_data.endswith(goes_to)
        caption = (
            "Это запись правила для бота; выше — главное из неё словами. Изменить правило "
            "нельзя, можно создать новое: «➕ Добавить» → «🧩 Настроить в JSON»."
        )
        if payload.endswith(":j"):
            lines = text.splitlines()
            assert lines[lines.index(caption) + 1].startswith("<pre>")
        else:
            assert caption not in text
            assert "<pre>" not in text

    def test_the_caption_names_buttons_that_are_there(self) -> None:
        """«➕ Добавить» → «🧩 Настроить в JSON»: the rule list's button, then the type step's."""
        listed = screens.rules_screen(
            "ru", chat_id=CHAT_ID, title="Чат", chat=_ON, rules=[screens.rule_view(_rule())]
        )
        add = [b.callback for row in listed.rows for b in row if b.label == "➕ Добавить"]
        assert add == [f"ar_add:ru:{CHAT_ID}"]
        kinds = screens.type_screen("ru", CHAT_ID)
        cfg = [b.callback for row in kinds.rows for b in row if b.label == "🧩 Настроить в JSON"]
        assert cfg == [f"ar_cfg:ru:{CHAT_ID}"]

    async def test_n_sends_it_as_a_new_message(self) -> None:
        """From the card a fired rule sends: that card stays as the record."""
        callback = await _tap(
            handlers.handle_rule_view,
            f"ar_view:ru:{RULE_ID}:{CHAT_ID}:n",
            _State(),
            _deps(_rule()),
        )

        callback.message.edit_text.assert_not_awaited()
        expected = screens.rule_screen("ru", screens.rule_view(_rule()), chat=_ON)
        assert _same(_sent(callback.message), expected)

    @pytest.mark.parametrize(
        ("handler", "payload"),
        [
            (handlers.handle_rule_view, f"ar_view:ru:{RULE_ID}:{CHAT_ID}"),
            (handlers.handle_rule_view, f"ar_view:ru:{RULE_ID}:{CHAT_ID}:j"),
            (handlers.handle_rule_delete_ask, f"ar_del_ask:ru:{RULE_ID}:{CHAT_ID}"),
        ],
        ids=["card", "json", "delete-ask"],
    )
    async def test_an_unnamed_rule_of_three_hundred_words_opens_in_place(
        self, handler: Any, payload: str
    ) -> None:
        """A JSON rule bounds no word, and without a name it is named by all of
        them: the head of its card and of the delete question ran past 4096, so
        the tap no longer edited the screen but sent it again as several new
        messages, the first of them nothing but the rule's words."""
        words = [f"нецензурное{i:03d}" for i in range(300)]
        assert parsed_length(", ".join(words)) > TELEGRAM_MESSAGE_LIMIT
        rule = _rule(config={"keywords": words, "action": "notify_admin"})
        callback = await _tap(handler, payload, _State(), _deps(rule))

        callback.message.answer.assert_not_awaited()
        text = _shown(callback)[0]
        assert parsed_length(text) <= TELEGRAM_MESSAGE_LIMIT, parsed_length(text)
        shown = screens.cap_name(", ".join(words))
        assert shown == ", ".join(words)[: screens.NAME_CHARS - 1] + "…"
        assert html.escape(shown, quote=False) in text

    async def test_the_alert_cards_button_on_a_deleted_rule_leaves_the_card(self) -> None:
        """The card a fired rule sends stays the record of what fired: once the
        rule is gone its button put the chat's list over that card, who wrote
        what and all, instead of under it."""
        card = screens.rule_card(
            "ru",
            rule_id=RULE_ID,
            chat_id=CHAT_ID,
            name="spam-words",
            chat_title=None,
            first_name="Alice",
            username=None,
            match_detail="contains:spam",
            excerpt="buy spam",
        )
        (button,) = [b for row in card.rows for b in row]
        callback = await _tap(handlers.handle_rule_view, button.callback, _State(), _deps(None))

        callback.message.edit_text.assert_not_awaited()
        expected = screens.gone_screen("ru", chat_id=CHAT_ID, title=TITLE, chat=_ON, rules=[])
        assert _same(_sent(callback.message), expected)

    async def test_a_config_stored_as_a_string_is_read(self) -> None:
        rule = _rule(config=json.dumps({"name": "json-rule", "keywords": ["x"]}))
        callback = await _tap(
            handlers.handle_rule_view, f"ar_view:ru:{RULE_ID}:{CHAT_ID}", _State(), _deps(rule)
        )

        assert "json-rule" in _shown(callback)[0]

    async def test_a_gone_rule_shows_its_chats_list_saying_so(self) -> None:
        callback = await _tap(
            handlers.handle_rule_view, f"ar_view:ru:{RULE_ID}:{CHAT_ID}", _State(), _deps(None)
        )

        expected = screens.gone_screen("ru", chat_id=CHAT_ID, title=TITLE, chat=_ON, rules=[])
        assert _same(_shown(callback), expected)
        assert _shown(callback)[0].startswith(screens.GONE_NOTICE("ru"))

    async def test_a_gone_rule_on_an_old_button_without_its_chat_is_stale(
        self, stale: AsyncMock
    ) -> None:
        await _tap(handlers.handle_rule_view, f"ar_view:ru:{RULE_ID}", _State(), _deps(None))

        stale.assert_awaited_once()

    @pytest.mark.parametrize("data", ["ar_view:ru:x", f"ar_view:ru:{RULE_ID}:chat", "ar_view:ru"])
    async def test_a_garbled_payload_is_stale(self, data: str, stale: AsyncMock) -> None:
        deps = _deps(_rule())
        await _tap(handlers.handle_rule_view, data, _State(), deps)

        stale.assert_awaited_once()
        deps["rules_repo"].get.assert_not_awaited()


class TestPause:
    async def test_pauses_and_redraws_the_card_with_resume(self) -> None:
        deps = _deps(_rule())
        callback = await _tap(
            handlers.handle_rule_toggle, f"ar_tog:ru:{RULE_ID}:{CHAT_ID}:0", _State(), deps
        )

        deps["rules_repo"].toggle.assert_awaited_once_with(RULE_ID, enabled=False)
        expected = screens.rule_screen("ru", screens.rule_view(_rule(enabled=False)), chat=_ON)
        assert _same(_shown(callback), expected)
        assert "▶ Возобновить" in _labels(_shown(callback)[1])

    async def test_resumes(self) -> None:
        deps = _deps(_rule(enabled=False))
        callback = await _tap(
            handlers.handle_rule_toggle, f"ar_tog:en:{RULE_ID}:{CHAT_ID}:0", _State(), deps
        )

        deps["rules_repo"].toggle.assert_awaited_once_with(RULE_ID, enabled=True)
        assert "⏸ Pause" in _labels(_shown(callback)[1])

    async def test_a_gone_rule_shows_the_list_saying_so(self) -> None:
        deps = _deps(None)
        callback = await _tap(
            handlers.handle_rule_toggle, f"ar_tog:ru:{RULE_ID}:{CHAT_ID}:0", _State(), deps
        )

        deps["rules_repo"].toggle.assert_not_awaited()
        assert _shown(callback)[0].startswith(screens.GONE_NOTICE("ru"))

    async def test_an_old_button_without_a_chat_is_stale(self, stale: AsyncMock) -> None:
        deps = _deps(_rule())
        await _tap(handlers.handle_rule_toggle, f"ar_tog:ru:{RULE_ID}", _State(), deps)

        stale.assert_awaited_once()
        deps["rules_repo"].toggle.assert_not_awaited()


class TestDeleteAsk:
    async def test_asks_and_deletes_nothing(self) -> None:
        deps = _deps(_rule())
        callback = await _tap(
            handlers.handle_rule_delete_ask, f"ar_del_ask:ru:{RULE_ID}:{CHAT_ID}", _State(), deps
        )

        expected = screens.delete_ask_screen("ru", screens.rule_view(_rule()))
        assert _same(_shown(callback), expected)
        assert "spam-words" in _shown(callback)[0]
        deps["rules_repo"].delete.assert_not_awaited()

    async def test_yes_deletes_and_back_returns_to_the_rule(self) -> None:
        callback = await _tap(
            handlers.handle_rule_delete_ask,
            f"ar_del_ask:ru:{RULE_ID}:{CHAT_ID}",
            _State(),
            _deps(_rule()),
        )

        assert _datas(_shown(callback)[1]) == [
            f"ar_del:ru:{RULE_ID}:{CHAT_ID}",
            f"ar_view:ru:{RULE_ID}:{CHAT_ID}",
        ]
        assert _labels(_shown(callback)[1]) == ["🗑 Удалить", "⬅️ Не удалять"]

    async def test_english(self) -> None:
        callback = await _tap(
            handlers.handle_rule_delete_ask,
            f"ar_del_ask:en:{RULE_ID}:{CHAT_ID}",
            _State(),
            _deps(_rule()),
        )

        assert "Delete the rule?" in _shown(callback)[0]

    async def test_a_non_admin_gets_an_alert(self) -> None:
        deps = {**_deps(_rule()), "is_admin": False}
        callback = await _tap(
            handlers.handle_rule_delete_ask, f"ar_del_ask:ru:{RULE_ID}:{CHAT_ID}", _State(), deps
        )

        assert callback.answer.await_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()
        deps["rules_repo"].delete.assert_not_awaited()

    async def test_a_gone_rule_shows_the_list_saying_so(self) -> None:
        callback = await _tap(
            handlers.handle_rule_delete_ask,
            f"ar_del_ask:ru:{RULE_ID}:{CHAT_ID}",
            _State(),
            _deps(None),
        )

        assert _shown(callback)[0].startswith(screens.GONE_NOTICE("ru"))


class TestDelete:
    async def test_deletes_and_shows_the_list_with_a_notice(self) -> None:
        deps = _deps(_rule())
        callback = await _tap(
            handlers.handle_rule_delete, f"ar_del:ru:{RULE_ID}:{CHAT_ID}", _State(), deps
        )

        deps["rules_repo"].delete.assert_awaited_once_with(RULE_ID)
        expected = screens.rules_screen(
            "ru", chat_id=CHAT_ID, title=TITLE, chat=_ON, rules=[], notice="Правило удалено"
        )
        assert _same(_shown(callback), expected)

    async def test_an_old_button_without_its_chat_still_lands_on_the_rules_chat(self) -> None:
        deps = _deps(_rule())
        await _tap(handlers.handle_rule_delete, f"ar_del:ru:{RULE_ID}", _State(), deps)

        assert deps["rules_repo"].get_rules_page.await_args.args[0] == CHAT_ID

    async def test_lost_a_race_shows_the_list_saying_so(self) -> None:
        deps = _deps(_rule())
        deps["rules_repo"].delete = AsyncMock(return_value=False)
        callback = await _tap(
            handlers.handle_rule_delete, f"ar_del:ru:{RULE_ID}:{CHAT_ID}", _State(), deps
        )

        assert _shown(callback)[0].startswith(screens.GONE_NOTICE("ru"))

    async def test_a_non_admin_cannot_delete(self) -> None:
        deps = {**_deps(_rule()), "is_admin": False}
        callback = await _tap(
            handlers.handle_rule_delete, f"ar_del:ru:{RULE_ID}:{CHAT_ID}", _State(), deps
        )

        deps["rules_repo"].delete.assert_not_awaited()
        assert callback.answer.await_args.kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


async def _start(deps: dict[str, Any], state: _State, lang: str = "ru") -> MagicMock:
    return await _tap(handlers.handle_add_rule, f"ar_add:{lang}:{CHAT_ID}", state, deps)


def _created(deps: dict[str, Any]) -> dict[str, Any]:
    """What the builder handed `RulesRepository.create`."""
    deps["rules_repo"].create.assert_awaited_once()
    return dict(deps["rules_repo"].create.await_args.kwargs)


class TestBuilderStart:
    async def test_a_new_rule_forgets_the_last_draft(self) -> None:
        state = _State(rule_chat_id=OTHER_CHAT, rule_type="spam_detect", rule_n=40, keep="x")
        callback = await _start(_deps(), state)

        assert _same(_shown(callback), screens.type_screen("ru", CHAT_ID))
        assert state.data["rule_chat_id"] == CHAT_ID
        assert all(state.data[k] is None for k in handlers._DRAFT_KEYS if k != "rule_chat_id")
        assert state.data["keep"] == "x", "only the builder's own keys are reset"

    async def test_it_leaves_a_rule_prompt(self) -> None:
        state = _State(AdminStates.awaiting_rule_text, rule_chat_id=OTHER_CHAT)
        await _start(_deps(), state)

        assert state.current is None


class TestBuilderKeywords:
    async def test_words_to_a_rule(self, retire: AsyncMock) -> None:
        deps, state = _deps(), _State()
        await _start(deps, state)

        callback = await _tap(
            handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:k", state, deps
        )
        assert state.current == AdminStates.awaiting_rule_words.state
        assert _same(_shown(callback), screens.words_screen("ru", CHAT_ID))
        assert state.data["rule_prompt_id"] == PROMPT_ID

        typed = _typed("реклама, Купи\nреклама,  купи ")
        await handlers.handle_rule_words_input(typed, state)
        assert state.current is None
        assert state.data["rule_words"] == ["реклама", "Купи"]
        assert _same(_sent(typed), screens.match_screen("ru", CHAT_ID, ["реклама", "Купи"]))
        assert retire.await_args.args[0].message_id == PROMPT_ID

        callback = await _tap(
            handlers.handle_builder_match, f"ar_b_match:ru:{CHAT_ID}:e", state, deps
        )
        assert _same(_shown(callback), screens.action_screen("ru", CHAT_ID, "реклама, Купи"))

        callback = await _tap(
            handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:n", state, deps
        )
        assert _created(deps) == {
            "chat_id": CHAT_ID,
            "rule_type": "keyword_trigger",
            "config": {
                "name": "реклама, Купи",
                "keywords": ["реклама", "Купи"],
                "match_type": "exact",
                "action": "notify_admin",
            },
            "weight": 1,
            "mandatory": False,
        }
        text, markup = _shown(callback)
        assert text.startswith("<i>✓ Правило создано</i>\n\n")
        assert f"ar_tog:ru:{RULE_ID}:{CHAT_ID}:0" in _datas(markup)
        assert all(state.data[k] is None for k in handlers._DRAFT_KEYS), "the draft is spent"

    @pytest.mark.parametrize(
        ("lang", "inside", "longer"),
        [("ru", "внутри слова", "«закотка»"), ("en", "inside a word", "“superкотs”")],
    )
    async def test_the_anywhere_button_says_it_reaches_inside_a_word(
        self, lang: str, inside: str, longer: str
    ) -> None:
        """The engine's ``contains`` is a substring test: the rule the first
        button makes for «кот» fires on «который», so the button, its example
        and the card say the word is caught even inside another word."""
        deps, state = _deps(), _State()
        await _start(deps, state)
        await _tap(handlers.handle_builder_type, f"ar_b_type:{lang}:{CHAT_ID}:k", state, deps)
        typed = _typed("кот")
        await handlers.handle_rule_words_input(typed, state)
        text, markup = _sent(typed)
        assert f"{inside}: {longer}" in text, "the example shows the word inside another"
        anywhere = [
            b.callback_data for row in markup.inline_keyboard for b in row if inside in b.text
        ]
        assert len(anywhere) == 1, _labels(markup)

        await _tap(handlers.handle_builder_match, anywhere[0], state, deps)
        callback = await _tap(
            handlers.handle_builder_action, f"ar_b_act:{lang}:{CHAT_ID}:n", state, deps
        )

        created = _created(deps)
        repo = MagicMock(spec=RulesRepository)
        repo.get_active_rules = AsyncMock(
            return_value=[{"id": RULE_ID, "chat_id": CHAT_ID, **created, "enabled": True}]
        )
        repo.record_trigger = AsyncMock()
        fired = await RuleEngine(repo).evaluate(
            chat_id=CHAT_ID,
            user_id=7,
            message=MagicMock(text="который час?", caption=None, sticker=None),
            rules_mode="all",
        )
        assert [a.rule.id for a in fired] == [RULE_ID], "«кот» inside «который» fires"
        assert f"«кот» — {screens.CONTAINS_HOW(lang)}" in _shown(callback)[0]
        assert inside in screens.CONTAINS_HOW(lang)

    async def test_a_long_list_of_words_is_named_by_its_start(self) -> None:
        words = [f"слово{i:02d}" for i in range(15)]
        state = _State(AdminStates.awaiting_rule_words, rule_chat_id=CHAT_ID, lang="ru")

        await handlers.handle_rule_words_input(_typed(", ".join(words)), state)

        name = state.data["rule_name"]
        assert len(name) <= 60
        assert name.endswith("…")

    @pytest.mark.parametrize(
        ("typed", "error"),
        [
            (" , ,\n", screens.WORDS_NONE("ru")),
            (", ".join(f"w{i}" for i in range(21)), screens.WORDS_TOO_MANY("ru").format(n=20)),
        ],
        ids=["none", "too-many"],
    )
    async def test_unusable_words_keep_the_prompt(
        self, typed: str, error: str, retire: AsyncMock
    ) -> None:
        state = _State(
            AdminStates.awaiting_rule_words,
            rule_chat_id=CHAT_ID,
            lang="ru",
            rule_prompt_id=PROMPT_ID,
        )
        message = _typed(typed)

        await handlers.handle_rule_words_input(message, state)

        text, markup = _sent(message)
        assert text == error
        assert _datas(markup) == [f"ar_list:ru:{CHAT_ID}:0"]
        assert state.current == AdminStates.awaiting_rule_words.state
        assert state.data["rule_prompt_id"] == 900, "the error is now the one live Cancel"
        assert retire.await_args.args[0].message_id == PROMPT_ID

    async def test_exactly_the_cap_is_accepted(self) -> None:
        state = _State(AdminStates.awaiting_rule_words, rule_chat_id=CHAT_ID, lang="ru")
        await handlers.handle_rule_words_input(_typed(", ".join(f"w{i}" for i in range(20))), state)
        assert state.current is None
        assert len(state.data["rule_words"]) == 20

    async def test_an_over_long_word_is_quoted_back_cut_and_escaped(self) -> None:
        state = _State(AdminStates.awaiting_rule_words, rule_chat_id=CHAT_ID, lang="ru")
        message = _typed("ok, <b>" + "я" * 70)

        await handlers.handle_rule_words_input(message, state)

        text = _sent(message)[0]
        assert "&lt;b&gt;" in text
        assert "<b>" not in text
        assert "…»" in text
        assert state.current == AdminStates.awaiting_rule_words.state

    async def test_a_word_of_exactly_the_cap_is_accepted(self) -> None:
        state = _State(AdminStates.awaiting_rule_words, rule_chat_id=CHAT_ID, lang="ru")
        await handlers.handle_rule_words_input(_typed("я" * 64), state)
        assert state.current is None

    async def test_a_prompt_without_a_chat_ends_quietly(self) -> None:
        state = _State(AdminStates.awaiting_rule_words, lang="ru")
        message = _typed("реклама")
        with patch.object(handlers, "logger") as logger:
            await handlers.handle_rule_words_input(message, state)

        message.answer.assert_not_awaited()
        assert state.current is None
        logger.warning.assert_called_once()


class TestBuilderPerson:
    async def test_a_person_to_a_rule_that_fires_on_every_message(self) -> None:
        people = [
            {"user_id": 7001, "first_name": "Аня", "username": "anya", "message_count": 9},
            {"user_id": 7002, "first_name": None, "username": None, "message_count": 3},
        ]
        deps, state = _deps(people=people), _State()
        await _start(deps, state)

        callback = await _tap(
            handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:u", state, deps
        )
        persons = [screens.Person(7001, "Аня", "anya"), screens.Person(7002)]
        assert _same(_shown(callback), screens.who_screen("ru", CHAT_ID, persons))

        await _tap(handlers.handle_builder_who, f"ar_b_who:ru:{CHAT_ID}:7001", state, deps)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:n", state, deps)

        assert _created(deps)["config"] == {
            "name": "Аня @anya",
            "user_ids": [7001],
            "conditions": {"any_message": True},
            "action": "notify_admin",
        }

    async def test_a_person_paged_past_the_end_shows_the_last_page(self) -> None:
        deps, state = _deps(), _State()
        await _start(deps, state)
        last = [{"user_id": 7009, "first_name": "Ира", "username": None}]
        deps["message_repo"].get_top_active_users = AsyncMock(side_effect=[([], 5), (last, 5)])

        callback = await _tap(
            handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:u:9", state, deps
        )

        assert deps["message_repo"].get_top_active_users.await_args.args == (
            CHAT_ID,
            1,
            screens.WHO_PER_PAGE,
        )
        expected = screens.who_screen("ru", CHAT_ID, [screens.Person(7009, "Ира")], page=1, pages=2)
        assert _same(_shown(callback), expected)

    async def test_a_long_name_is_cut_like_any_rule_name(self) -> None:
        people = [{"user_id": 7001, "first_name": "Я" * 64, "username": "u" * 32}]
        deps, state = _deps(people=people), _State()
        await _start(deps, state)
        await _tap(handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:u", state, deps)

        await _tap(handlers.handle_builder_who, f"ar_b_who:ru:{CHAT_ID}:7001", state, deps)

        name = state.data["rule_name"]
        assert len(name) == handlers._NAME_CHARS
        assert name == "Я" * (handlers._NAME_CHARS - 1) + "…"

    async def test_a_person_for_a_rule_of_another_kind_is_stale(self, stale: AsyncMock) -> None:
        state = _State(rule_chat_id=CHAT_ID, rule_type="spam_detect")
        await _tap(handlers.handle_builder_who, f"ar_b_who:ru:{CHAT_ID}:7001", state, _deps())
        stale.assert_awaited_once()


class TestBuilderCounts:
    async def test_stickers_to_a_rule_with_a_ready_reaction(self) -> None:
        deps, state = _deps(), _State()
        await _start(deps, state)

        callback = await _tap(
            handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:f", state, deps
        )
        assert _same(_shown(callback), screens.count_screen("ru", CHAT_ID, "f"))
        await _tap(handlers.handle_builder_count, f"ar_b_n:ru:{CHAT_ID}:10", state, deps)
        callback = await _tap(
            handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:r", state, deps
        )
        assert _same(_shown(callback), screens.react_screen("ru", CHAT_ID, "Стикеры: 10 за минуту"))
        await _tap(handlers.handle_builder_preset_reaction, f"ar_b_rx:ru:{CHAT_ID}:1", state, deps)

        created = _created(deps)
        assert created["rule_type"] == "sticker_flood"
        assert created["config"] == {
            "name": "Стикеры: 10 за минуту",
            "max_stickers_per_interval": 10,
            "interval_seconds": 60,
            "action": "set_reaction",
            "emoji": screens.REACTIONS[1],
        }

    async def test_messages_to_a_rule_with_a_ready_warning(self) -> None:
        """Spam counts messages only: the engine's length cap is written out as off."""
        deps, state = _deps(), _State()
        await _start(deps, state, lang="en")

        await _tap(handlers.handle_builder_type, f"ar_b_type:en:{CHAT_ID}:s", state, deps)
        await _tap(handlers.handle_builder_count, f"ar_b_n:en:{CHAT_ID}:20", state, deps)
        callback = await _tap(
            handlers.handle_builder_action, f"ar_b_act:en:{CHAT_ID}:c", state, deps
        )
        assert state.current == AdminStates.awaiting_rule_text.state
        assert _same(
            _shown(callback),
            screens.text_screen("en", CHAT_ID, "Messages: 20 a minute", action="custom_response"),
        )
        await _tap(handlers.handle_builder_preset_text, f"ar_b_txt:en:{CHAT_ID}:1", state, deps)

        created = _created(deps)
        assert created["rule_type"] == "spam_detect"
        assert created["config"] == {
            "name": "Messages: 20 a minute",
            "max_messages": 20,
            "time_window_seconds": 60,
            "max_chars_per_message": 0,
            "action": "warn_user",
            "warning_message": screens.WARN_PRESETS[1]("en"),
        }
        assert state.current is None

    async def test_the_retired_warning_choice_still_answers_from_an_old_keyboard(self) -> None:
        """``w`` is no longer offered, but a keyboard sent before the merge still works."""
        deps, state = _deps(), _State()
        await _start(deps, state)
        await _tap(handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:s", state, deps)
        await _tap(handlers.handle_builder_count, f"ar_b_n:ru:{CHAT_ID}:20", state, deps)
        offered = [
            b.callback for row in screens.action_screen("ru", CHAT_ID, "x").rows for b in row
        ]
        assert f"ar_b_act:ru:{CHAT_ID}:w" not in offered, offered

        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:w", state, deps)
        await _tap(handlers.handle_builder_preset_text, f"ar_b_txt:ru:{CHAT_ID}:0", state, deps)

        config = _created(deps)["config"]
        assert config["action"] == "warn_user"
        assert config["warning_message"] == screens.WARN_PRESETS[0]("ru")

    @pytest.mark.parametrize(("kind", "n"), [("f", 20), ("s", 10)])
    async def test_a_count_from_the_other_kinds_choices_is_stale(
        self, kind: str, n: int, stale: AsyncMock
    ) -> None:
        """5/10/20 stickers and 10/20/40 messages: a count is checked against its own kind."""
        deps, state = _deps(), _State()
        await _start(deps, state)
        await _tap(handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:{kind}", state, deps)
        other = {"f": 40, "s": 5}[kind]

        await _tap(handlers.handle_builder_count, f"ar_b_n:ru:{CHAT_ID}:{other}", state, deps)
        stale.assert_awaited_once()

        stale.reset_mock()
        await _tap(handlers.handle_builder_count, f"ar_b_n:ru:{CHAT_ID}:{n}", state, deps)
        stale.assert_not_awaited()


class TestBuilderTypedAnswers:
    async def _to_action(self, deps: dict[str, Any], state: _State) -> None:
        await _start(deps, state)
        await _tap(handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:s", state, deps)
        await _tap(handlers.handle_builder_count, f"ar_b_n:ru:{CHAT_ID}:40", state, deps)

    async def test_a_typed_reply_makes_the_rule(self, retire: AsyncMock) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:c", state, deps)
        message = _typed("Тише, {first_name}! <b>не</b> & так")

        await handlers.handle_rule_text_input(message, state, **_services(deps))

        config = _created(deps)["config"]
        assert config["action"] == "custom_response"
        assert config["response_template"] == "Тише, {first_name}! <b>не</b> & так"
        assert _sent(message)[0].startswith("<i>✓ Правило создано</i>")
        assert state.current is None
        assert retire.await_args.args[0].message_id == PROMPT_ID

    async def test_a_typed_reaction_is_stored_in_telegrams_spelling(self) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:ri", state, deps)
        assert state.current == AdminStates.awaiting_rule_react.state

        await handlers.handle_rule_react_input(_typed("❤️"), state, **_services(deps))

        emoji = _created(deps)["config"]["emoji"]
        assert emoji == ReactionSelector.select("❤️")
        assert emoji == "❤", "Telegram spells this reaction without U+FE0F"

    async def test_a_reaction_telegram_refuses_keeps_the_prompt(self) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:ri", state, deps)
        message = _typed("🚀")

        await handlers.handle_rule_react_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        text, markup = _sent(message)
        assert text == screens.EMOJI_BAD("ru")
        assert _datas(markup) == [f"ar_b_act:ru:{CHAT_ID}:r"]
        assert state.current == AdminStates.awaiting_rule_react.state

    @pytest.mark.parametrize(
        ("text", "error"),
        [
            ("   ", screens.TEXT_EMPTY("ru")),
            (None, screens.TEXT_EMPTY("ru")),
            ("я" * 4097, screens.text_too_long("ru", 1)),
            # 2049 code points, 4098 UTF-16 units: Telegram counts the latter.
            ("😀" * 2049, screens.text_too_long("ru", 2)),
        ],
        ids=["blank", "a-photo-without-caption", "too-long", "astral-too-long"],
    )
    async def test_an_unusable_text_keeps_the_prompt(self, text: str | None, error: str) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:w", state, deps)
        message = _typed(text)

        await handlers.handle_rule_text_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert _sent(message)[0] == error
        assert state.current == AdminStates.awaiting_rule_text.state

    async def test_a_text_of_exactly_the_cap_is_accepted(self) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:w", state, deps)

        await handlers.handle_rule_text_input(_typed("я" * 4096), state, **_services(deps))

        assert len(_created(deps)["config"]["warning_message"]) == 4096

    async def test_astral_text_of_exactly_the_cap_is_accepted(self) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:w", state, deps)

        await handlers.handle_rule_text_input(_typed("😀" * 2048), state, **_services(deps))

        assert _created(deps)["config"]["warning_message"] == "😀" * 2048

    @pytest.mark.parametrize(
        ("text", "over"),
        [
            # 4012 as typed; 4128 once a 64-character astral first name is put in.
            ("{first_name}" + "a" * 4000, 32),
            # The rule's own name goes in too («Сообщения: 40 за минуту», 23).
            ("{rule_name}{first_name}" + "a" * 3960, 15),
        ],
        ids=["first-name", "rule-name"],
    )
    async def test_a_reply_that_overflows_once_a_name_is_put_in_keeps_the_prompt(
        self, text: str, over: int
    ) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:c", state, deps)
        message = _typed(text)

        await handlers.handle_rule_text_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert _sent(message)[0] == screens.text_too_long("ru", over, filled=True)
        assert "С подставленным именем" in _sent(message)[0]
        assert state.current == AdminStates.awaiting_rule_text.state

    async def test_a_reply_that_fits_with_the_longest_name_is_accepted(self) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:c", state, deps)
        text = "{first_name}" + "a" * 3968  # 128 + 3968 = 4096 at worst

        await handlers.handle_rule_text_input(_typed(text), state, **_services(deps))

        assert _created(deps)["config"]["response_template"] == text

    async def test_a_sticker_answers_the_reaction_prompt_with_its_emoji(self) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:ri", state, deps)

        await handlers.handle_rule_react_input(
            _typed(sticker=MagicMock(emoji="👍")), state, **_services(deps)
        )

        config = _created(deps)["config"]
        assert config["action"] == "set_reaction"
        assert config["emoji"] == "👍"
        assert state.current is None

    @pytest.mark.parametrize("emoji", ["🚀", None, ""], ids=["not-a-reaction", "none", "empty"])
    async def test_a_sticker_without_a_usable_emoji_keeps_the_prompt(
        self, emoji: str | None
    ) -> None:
        deps, state = _deps(), _State()
        await self._to_action(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:ri", state, deps)
        message = _typed(sticker=MagicMock(emoji=emoji))

        await handlers.handle_rule_react_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert _sent(message)[0] == screens.EMOJI_BAD("ru")
        assert state.current == AdminStates.awaiting_rule_react.state

    async def test_an_incomplete_draft_says_no_rule_was_made(self, retire: AsyncMock) -> None:
        """The last answer typed, but a step before it is missing: never silence."""
        state = _State(
            AdminStates.awaiting_rule_text,
            rule_chat_id=CHAT_ID,
            rule_type="keyword_trigger",
            rule_action="warn_user",
            rule_name="w",
            rule_prompt_id=PROMPT_ID,
            lang="ru",
        )
        deps = _deps()
        message = _typed("Не надо так")

        await handlers.handle_rule_text_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        expected = screens.rules_screen(
            "ru",
            chat_id=CHAT_ID,
            title=TITLE,
            chat=_ON,
            rules=[],
            notice_raw=handlers.NOT_CREATED("ru"),
        )
        assert _same(_sent(message), expected)
        assert _sent(message)[0].startswith("<i>Правило не создано: начните заново.</i>")
        assert state.current is None
        assert retire.await_args.args[0].message_id == PROMPT_ID

    async def test_created_while_the_chats_rules_are_off_says_so_and_offers_the_switch(
        self,
    ) -> None:
        deps, state = _deps(enabled=False), _State()
        await self._to_action(deps, state)

        callback = await _tap(
            handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:n", state, deps
        )

        text, markup = _shown(callback)
        assert text.startswith("<i>✓ Правило создано</i>")
        assert "Правила этого чата выключены" in text
        assert _datas(markup)[0] == f"ar_all:ru:{CHAT_ID}:{RULE_ID}"


_FULL_DRAFT: dict[str, Any] = {
    "rule_type": "keyword_trigger",
    "rule_words": ["w"],
    "rule_match": "contains",
    "rule_name": "w",
    "rule_action": "warn_user",
}


class TestBuilderStaleSteps:
    """A builder button outlives its draft; such a tap is stale, never a rule elsewhere."""

    @pytest.mark.parametrize(
        ("handler", "data"),
        [
            (handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:k"),
            (handlers.handle_builder_match, f"ar_b_match:ru:{CHAT_ID}:c"),
            (handlers.handle_builder_who, f"ar_b_who:ru:{CHAT_ID}:7001"),
            (handlers.handle_builder_count, f"ar_b_n:ru:{CHAT_ID}:10"),
            (handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:n"),
            (handlers.handle_builder_preset_text, f"ar_b_txt:ru:{CHAT_ID}:0"),
            (handlers.handle_builder_preset_reaction, f"ar_b_rx:ru:{CHAT_ID}:0"),
        ],
        ids=lambda v: v if isinstance(v, str) else v.__name__,
    )
    @pytest.mark.parametrize("draft_chat", [None, OTHER_CHAT], ids=["no-draft", "another-chat"])
    async def test_a_step_without_this_chats_draft_is_stale(
        self, handler: Any, data: str, draft_chat: int | None, stale: AsyncMock
    ) -> None:
        state = _State(rule_chat_id=draft_chat, **_FULL_DRAFT)
        deps = _deps()

        await _tap(handler, data, state, deps)

        stale.assert_awaited_once()
        deps["rules_repo"].create.assert_not_awaited()

    @pytest.mark.parametrize(
        ("handler", "data"),
        [
            (handlers.handle_add_rule, "ar_add:ru:chat"),
            (handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:z"),
            (handlers.handle_builder_match, f"ar_b_match:ru:{CHAT_ID}:x"),
            (handlers.handle_builder_who, f"ar_b_who:ru:{CHAT_ID}:anna"),
            (handlers.handle_builder_count, f"ar_b_n:ru:{CHAT_ID}:many"),
            (handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:q"),
            (handlers.handle_builder_preset_text, f"ar_b_txt:ru:{CHAT_ID}:9"),
            (handlers.handle_builder_preset_reaction, f"ar_b_rx:ru:{CHAT_ID}:-1"),
            (handlers.handle_rule_cfg, "ar_cfg:ru"),
            (handlers.handle_type_selected, f"ar_type:ru:{CHAT_ID}:no_such_type"),
            (handlers.handle_rule_config_cancel, "ar_cancel:ru:x"),
        ],
        ids=lambda v: v if isinstance(v, str) else v.__name__,
    )
    async def test_a_garbled_payload_is_stale(
        self, handler: Any, data: str, stale: AsyncMock
    ) -> None:
        state = _State(rule_chat_id=CHAT_ID, **_FULL_DRAFT)
        deps = _deps()

        callback = await _tap(handler, data, state, deps)

        stale.assert_awaited_once()
        deps["rules_repo"].create.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()

    async def test_an_action_before_the_count_is_stale(self, stale: AsyncMock) -> None:
        state = _State(rule_chat_id=CHAT_ID, rule_type="spam_detect")
        deps = _deps()

        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:n", state, deps)

        stale.assert_awaited_once()
        deps["rules_repo"].create.assert_not_awaited()

    @pytest.mark.parametrize("action", ["set_reaction", "notify_admin", None])
    async def test_a_ready_text_for_a_rule_that_does_not_reply_is_stale(
        self, action: str | None, stale: AsyncMock
    ) -> None:
        """Ready texts sit on the reply prompt only; a draft that reacts or messages
        the admin (or has no action yet) has no use for one."""
        state = _State(
            rule_chat_id=CHAT_ID,
            rule_type="spam_detect",
            rule_n=20,
            rule_name="x",
            rule_action=action,
        )
        deps = _deps()

        await _tap(handlers.handle_builder_preset_text, f"ar_b_txt:ru:{CHAT_ID}:0", state, deps)

        stale.assert_awaited_once()
        deps["rules_repo"].create.assert_not_awaited()


# ---------------------------------------------------------------------------
# The JSON prompt
# ---------------------------------------------------------------------------


async def _cfg(deps: dict[str, Any], rule_type: str | None = None) -> _State:
    state = _State()
    data = f"ar_type:ru:{CHAT_ID}:{rule_type}" if rule_type else f"ar_cfg:ru:{CHAT_ID}"
    handler = handlers.handle_type_selected if rule_type else handlers.handle_rule_cfg
    callback = await _tap(handler, data, state, deps)
    assert state.current == AdminStates.awaiting_rule_config.state
    assert _same(_shown(callback), screens.cfg_screen("ru", CHAT_ID, rule_type))
    return state


class TestTheJsonPrompt:
    """An invalid action or set_reaction emoji is refused at creation: at
    evaluation time both are dropped with only a server-side log, so «Правило
    создано» would mean a permanent, invisible no-op. So is a keyword rule with
    no words: the engine reads ``keywords`` as ``[]`` and it never fires (and a
    blank word, ``""``, matches every message)."""

    async def test_a_keyword_rule_without_words_says_so_in_english(self) -> None:
        deps = _deps()
        state = await _cfg(deps)
        await state.update_data(lang="en")
        message = _typed('{"name": "x"}')

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert _sent(message)[0] == (
            "A keyword rule needs a <code>keywords</code> field with a list of words, e.g. "
            '<code>"keywords": ["hello"]</code>: without it the rule never fires. '
            "Please try again."
        )

    async def test_another_type_needs_no_keywords(self) -> None:
        """Control: the refusal is about the keyword type, not about the field."""
        deps = _deps()
        state = await _cfg(deps)

        await handlers.handle_rule_config_input(
            _typed('{"type": "spam_detect", "max_messages": 5}'), state, **_services(deps)
        )

        assert _created(deps)["rule_type"] == "spam_detect"

    @pytest.mark.parametrize(
        "row",
        [
            _rule(),
            _rule(rule_type="spam_detect", config={"max_messages": 5}, weight=3, mandatory=True),
            _rule(rule_type="user_specific", config={"user_ids": [11, 12], "action": "warn_user"}),
        ],
        ids=["keyword", "weighted-spam", "user-specific"],
    )
    async def test_the_shown_record_pasted_back_makes_the_same_rule(
        self, row: dict[str, Any]
    ) -> None:
        """The caption says a rule is made anew from such text: pasted from the
        default prompt (whose type is keywords), the shown record must make the
        rule it shows, not a keyword rule with no words."""
        card = await _tap(
            handlers.handle_rule_view,
            f"ar_view:ru:{RULE_ID}:{CHAT_ID}:j",
            _State(),
            _deps(row),
        )
        text = _shown(card)[0]
        shown = html.unescape(text[text.index("<pre>") + 5 : text.index("</pre>")])
        deps = _deps()
        state = await _cfg(deps)

        await handlers.handle_rule_config_input(_typed(shown), state, **_services(deps))

        assert _created(deps) == {
            "chat_id": CHAT_ID,
            "rule_type": row["rule_type"],
            "config": row["config"],
            "weight": row["weight"],
            "mandatory": row["mandatory"],
        }

    async def test_a_valid_set_reaction_rule_is_created(self, retire: AsyncMock) -> None:
        deps = _deps()
        state = await _cfg(deps)
        message = _typed(
            '{"name": "pill", "keywords": ["kw"], "action": "set_reaction", "emoji": "💊"}'
        )

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        created = _created(deps)
        assert created["rule_type"] == "keyword_trigger"
        assert created["config"]["emoji"] == "💊"
        assert state.current is None
        assert _sent(message)[0].startswith("<i>✓ Правило создано</i>")
        assert retire.await_args.args[0].message_id == PROMPT_ID

    async def test_the_type_field_picks_the_type_and_leaves_the_config(self) -> None:
        deps = _deps()
        state = await _cfg(deps)

        await handlers.handle_rule_config_input(
            _typed('{"type": "spam_detect", "max_messages": 5, "weight": 3, "mandatory": true}'),
            state,
            **_services(deps),
        )

        assert _created(deps) == {
            "chat_id": CHAT_ID,
            "rule_type": "spam_detect",
            "config": {"max_messages": 5},
            "weight": 3,
            "mandatory": True,
        }

    async def test_an_old_type_button_supplies_the_type(self) -> None:
        deps = _deps()
        state = await _cfg(deps, "sticker_flood")

        await handlers.handle_rule_config_input(
            _typed('{"max_stickers_per_interval": 3}'), state, **_services(deps)
        )

        assert _created(deps)["rule_type"] == "sticker_flood"

    async def test_a_rule_without_an_action_is_still_created(self) -> None:
        """The action key is optional: the engine defaults to notify_admin."""
        deps = _deps()
        state = await _cfg(deps)

        await handlers.handle_rule_config_input(
            _typed('{"name": "plain", "keywords": ["kw"]}'), state, **_services(deps)
        )

        deps["rules_repo"].create.assert_awaited_once()

    @pytest.mark.parametrize(
        "attached",
        [
            {"document": Document(file_id="d", file_unique_id="du", file_name="rule.json")},
            {"photo": [PhotoSize(file_id="p", file_unique_id="pu", width=90, height=90)]},
        ],
        ids=["document", "photo"],
    )
    async def test_the_json_in_a_caption_is_the_rule(self, attached: dict[str, Any]) -> None:
        """A file or a photo carries its words as a caption: valid JSON there
        was answered «Невалидный JSON», because only the text was read."""
        deps = _deps()
        state = await _cfg(deps)
        message = Message(
            message_id=900,
            date=datetime(2026, 11, 1, tzinfo=UTC),
            chat=Chat(id=ADMIN_ID, type="private"),
            from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
            caption='{"keywords": ["spam"], "action": "warn_user"}',
            **attached,
        ).as_(AsyncMock())

        # aiogram's own filters pick the handler: no media handler takes it first.
        assert await deliver("message", message, state) == "rules.handle_rule_config_input"
        await deliver("message", message, state, call=True, **_services(deps))

        config = _created(deps)["config"]
        assert (config["keywords"], config["action"]) == (["spam"], "warn_user")

    @pytest.mark.parametrize(
        ("typed", "says"),
        [
            ("not json at all", "Невалидный JSON"),
            ('["a list"]', "Невалидный JSON"),
            ('{"type": "nonsense"}', "<code>nonsense</code>"),
            ('{"keywords": ["kw"], "action": "set_reacton"}', "<code>set_reacton</code>"),
            ('{"keywords": ["kw"], "action": "set_reaction", "emoji": "🚀"}', "emoji"),
            ('{"keywords": ["kw"], "action": "set_reaction", "emoji": 5}', "emoji"),
            ('{"keywords": ["kw"], "action": "<b>x</b>"}', "&lt;b&gt;x&lt;/b&gt;"),
            ('{"action": "notify_admin"}', "нужно поле <code>keywords</code>"),
            ('{"type": "keyword_trigger", "keywords": []}', "нужно поле <code>keywords</code>"),
            ('{"keywords": "spam"}', "нужно поле <code>keywords</code>"),
            ('{"keywords": ["spam", " "]}', "нужно поле <code>keywords</code>"),
            ('{"keywords": [5]}', "нужно поле <code>keywords</code>"),
        ],
        ids=[
            "not-json",
            "not-a-dict",
            "type",
            "action",
            "emoji",
            "emoji-type",
            "escaped",
            "no-keywords",
            "empty-keywords",
            "keywords-a-string",
            "blank-keyword",
            "keyword-not-text",
        ],
    )
    async def test_a_refused_rule_keeps_the_prompt(self, typed: str, says: str) -> None:
        deps = _deps()
        state = await _cfg(deps)
        message = _typed(typed)

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert state.current == AdminStates.awaiting_rule_config.state
        text, markup = _sent(message)
        assert says in text
        assert _datas(markup) == [f"ar_list:ru:{CHAT_ID}:0"]

    @pytest.mark.parametrize(
        ("typed", "says"),
        [
            ('{"type": ["spam_detect"]}', "Неизвестный тип <code>[&quot;spam_detect&quot;]</code>"),
            ('{"type": {"a": 1}}', "Неизвестный тип <code>{&quot;a&quot;: 1}</code>"),
            (
                '{"keywords": ["kw"], "action": ["warn_user"]}',
                "Неизвестное действие <code>[&quot;warn_user&quot;]</code>",
            ),
            (
                '{"keywords": ["kw"], "action": {"x": 1}}',
                "Неизвестное действие <code>{&quot;x&quot;: 1}</code>",
            ),
        ],
        ids=["type-list", "type-object", "action-list", "action-object"],
    )
    async def test_a_type_or_action_that_is_not_text_is_refused_not_a_crash(
        self, typed: str, says: str
    ) -> None:
        """A list or an object is unhashable: a frozenset membership test raised TypeError."""
        deps = _deps()
        state = await _cfg(deps)
        message = _typed(typed)

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert state.current == AdminStates.awaiting_rule_config.state
        text, markup = _sent(message)
        assert text.startswith(says)
        assert _datas(markup) == [f"ar_list:ru:{CHAT_ID}:0"]

    @pytest.mark.parametrize(
        ("config", "error"),
        [
            ({"name": ["x"], "keywords": ["kw"]}, screens.CFG_NOT_TEXT("ru").format(key="name")),
            (
                {"keywords": ["kw"], "action": "warn_user", "warning_message": 5},
                screens.CFG_NOT_TEXT("ru").format(key="warning_message"),
            ),
            (
                {"keywords": ["kw"], "action": "warn_user", "warning_message": "😀" * 2049},
                screens.text_too_long("ru", 2),
            ),
            (
                {
                    "keywords": ["kw"],
                    "action": "custom_response",
                    "response_template": "{first_name}" + "a" * 4000,
                },
                screens.text_too_long("ru", 32, filled=True),
            ),
        ],
        ids=["name-not-text", "warning-not-text", "warning-too-long", "reply-too-long-filled"],
    )
    async def test_a_text_field_that_cannot_be_sent_is_refused(
        self, config: dict[str, Any], error: str
    ) -> None:
        deps = _deps()
        state = await _cfg(deps)
        message = _typed(json.dumps(config, ensure_ascii=False))

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert _sent(message)[0] == error
        assert state.current == AdminStates.awaiting_rule_config.state

    async def test_a_reply_naming_an_unnamed_rule_is_measured_with_its_longest_id(self) -> None:
        """No name: ``{rule_name}`` becomes «Rule #<id>», counted at the longest id."""
        deps = _deps()
        state = await _cfg(deps)
        template = "{rule_name}" + "a" * 4080  # 11 + 4080 typed; 25 + 4080 once filled
        message = _typed(
            json.dumps(
                {"keywords": ["kw"], "action": "custom_response", "response_template": template}
            )
        )

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert _sent(message)[0] == screens.text_too_long("ru", 9, filled=True)

    async def test_a_long_name_is_cut_like_the_builders(self) -> None:
        deps = _deps()
        state = await _cfg(deps)

        await handlers.handle_rule_config_input(
            _typed(json.dumps({"name": "n" * 200, "keywords": ["kw"]})), state, **_services(deps)
        )

        name = _created(deps)["config"]["name"]
        assert len(name) == handlers._NAME_CHARS
        assert name == "n" * (handlers._NAME_CHARS - 1) + "…"

    async def test_its_cancel_returns_to_the_builder_and_ends_the_prompt(self) -> None:
        """The prompt stays armed on a refused answer, so leaving must not need valid JSON."""
        deps = _deps()
        state = await _cfg(deps)

        callback = await _tap(handlers.handle_add_rule, f"ar_add:ru:{CHAT_ID}", state, deps)

        assert state.current is None
        assert _same(_shown(callback), screens.type_screen("ru", CHAT_ID))

    async def test_the_old_cancel_shows_the_list(self) -> None:
        deps = _deps()
        state = await _cfg(deps)

        callback = await _tap(
            handlers.handle_rule_config_cancel, f"ar_cancel:ru:{CHAT_ID}", state, deps
        )

        assert state.current is None
        expected = screens.rules_screen("ru", chat_id=CHAT_ID, title=TITLE, chat=_ON, rules=[])
        assert _same(_shown(callback), expected)


class _Unbindable(Exception):
    """What asyncpg raises, as ``DataError``, for a value its encoder cannot bind."""


def _bind_like_asyncpg(**kwargs: Any) -> int:
    """`RulesRepository.create` as the database answers it, not as a mock does.

    ``mandatory`` is a ``BOOLEAN``: pgproto's ``bool_encode`` takes a bool and
    nothing else. ``weight`` is an ``INT``: ``int4_encode`` truncates anything
    with ``__int__`` (``True`` is 1, ``2.5`` is 2), refuses the rest and anything
    past int32; None is NULL. What was refused here raised after the prompt
    was already claimed; what was taken was stored as typed.
    """
    mandatory, weight = kwargs["mandatory"], kwargs["weight"]
    if not isinstance(mandatory, bool):
        raise _Unbindable(f"a boolean is required (got type {type(mandatory).__name__})")
    if weight is None:
        return RULE_ID
    if type(weight) is not int and hasattr(type(weight), "__int__"):
        weight = int(weight)
    if not isinstance(weight, int):
        raise _Unbindable(f"an integer is required (got type {type(weight).__name__})")
    if not -(2**31) <= weight < 2**31:
        raise _Unbindable("value out of int32 range")
    return RULE_ID


async def _strict_cfg(rule_type: str | None = None) -> tuple[dict[str, Any], _State]:
    deps = _deps()
    deps["rules_repo"].create.side_effect = _bind_like_asyncpg
    return deps, await _cfg(deps, rule_type)


_WEIGHT_SAYS = "Поле <code>weight</code> (приоритет) должно быть целым числом от 1 до"
_MANDATORY_SAYS = (
    "Поле <code>mandatory</code> должно быть <code>true</code> или <code>false</code>."
)


class TestTheJsonColumns:
    """``weight`` and ``mandatory`` are columns, bound by asyncpg's strict
    encoders after the prompt is claimed: one of the wrong type raised there,
    and the admin got no answer and no prompt. A weight below 1 was stored and
    then made a weight mode raise for every rule of the chat's messages."""

    @pytest.mark.parametrize(
        ("field", "says"),
        [
            ('"mandatory": 1', _MANDATORY_SAYS),
            ('"mandatory": "true"', _MANDATORY_SAYS),
            ('"mandatory": null', _MANDATORY_SAYS),
            ('"weight": "5"', _WEIGHT_SAYS),
            ('"weight": 0', _WEIGHT_SAYS),
            ('"weight": -3', _WEIGHT_SAYS),
            ('"weight": null', _WEIGHT_SAYS),
            ('"weight": 3000000000', _WEIGHT_SAYS),
            ('"weight": 2147483648', _WEIGHT_SAYS),
            ('"weight": true', _WEIGHT_SAYS),
            ('"weight": 2.5', _WEIGHT_SAYS),
        ],
        ids=[
            "mandatory-1",
            "mandatory-text",
            "mandatory-null",
            "weight-text",
            "weight-0",
            "weight-negative",
            "weight-null",
            "weight-past-int32",
            "weight-int32-plus-one",
            "weight-true",
            "weight-fraction",
        ],
    )
    async def test_a_column_of_the_wrong_type_is_refused_and_the_prompt_stays(
        self, field: str, says: str
    ) -> None:
        deps, state = await _strict_cfg()
        message = _typed('{"keywords": ["kw"], ' + field + "}")

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert state.current == AdminStates.awaiting_rule_config.state
        assert state.data["rule_chat_id"] == CHAT_ID, "the draft is still there to retry"
        text, markup = _sent(message)
        assert says in text, text
        assert _datas(markup) == [f"ar_list:ru:{CHAT_ID}:0"]

    async def test_the_weight_refusal_reads_in_english_and_names_the_top(self) -> None:
        deps, state = await _strict_cfg()
        await state.update_data(lang="en")
        message = _typed('{"keywords": ["kw"], "weight": 0}')

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        assert _sent(message)[0] == (
            "The <code>weight</code> field (priority) must be a whole number from 1 to "
            "<code>2147483647</code>. Please try again."
        )

    @pytest.mark.parametrize(
        ("field", "weight", "mandatory"),
        [
            ("", 1, False),
            ('"weight": 1', 1, False),
            ('"weight": 2147483647', 2147483647, False),
            ('"mandatory": false', 1, False),
            ('"weight": 7, "mandatory": true', 7, True),
        ],
        ids=["defaults", "weight-1", "weight-int32-top", "not-mandatory", "both"],
    )
    async def test_a_column_the_database_takes_is_not_refused(
        self, field: str, weight: int, mandatory: bool
    ) -> None:
        """Control: the edges of what the columns hold still make the rule."""
        deps, state = await _strict_cfg()
        message = _typed('{"keywords": ["kw"]' + (", " + field if field else "") + "}")

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        created = _created(deps)
        assert (created["weight"], created["mandatory"]) == (weight, mandatory)
        assert state.current is None
        assert _sent(message)[0].startswith("<i>✓ Правило создано</i>")


class TestTheJsonEngineFields:
    """A field the engine compares or looks up, typed so it cannot: the rule
    was «Правило создано» and then raised, quietly, on every message (or never
    matched), so it never fired."""

    @pytest.mark.parametrize(
        ("typed", "says"),
        [
            (
                '{"type": "spam_detect", "max_messages": "20"}',
                "Поле <code>max_messages</code> должно быть целым числом.",
            ),
            (
                '{"type": "spam_detect", "time_window_seconds": true}',
                "Поле <code>time_window_seconds</code> должно быть целым числом.",
            ),
            (
                '{"type": "spam_detect", "max_chars_per_message": null}',
                "Поле <code>max_chars_per_message</code> должно быть целым числом.",
            ),
            (
                '{"type": "sticker_flood", "max_stickers_per_interval": "5"}',
                "Поле <code>max_stickers_per_interval</code> должно быть целым числом.",
            ),
            (
                '{"type": "sticker_flood", "interval_seconds": [60]}',
                "Поле <code>interval_seconds</code> должно быть целым числом.",
            ),
            (
                '{"type": "user_specific", "user_ids": "7"}',
                "Поле <code>user_ids</code> должно быть списком id участников",
            ),
            (
                '{"type": "user_specific", "user_ids": ["7"]}',
                "Поле <code>user_ids</code> должно быть списком id участников",
            ),
            (
                '{"keywords": ["kw"], "target_users": [true]}',
                "Поле <code>target_users</code> должно быть списком id участников",
            ),
            (
                '{"keywords": ["kw"], "exclude_users": 5}',
                "Поле <code>exclude_users</code> должно быть списком id участников",
            ),
            (
                '{"keywords": ["kw"], "match_type": "startswith"}',
                "Неизвестный способ сравнения <code>startswith</code> в поле "
                "<code>match_type</code>. Доступные: <code>contains</code>, "
                "<code>exact</code>, <code>regex</code>.",
            ),
            (
                '{"keywords": ["kw"], "match_type": ["exact"]}',
                "Неизвестный способ сравнения <code>[&quot;exact&quot;]</code>",
            ),
            (
                '{"keywords": ["kw"], "match_type": "<b>"}',
                "Неизвестный способ сравнения <code>&lt;b&gt;</code>",
            ),
            (
                '{"keywords": ["kw"], "case_sensitive": "yes"}',
                "Поле <code>case_sensitive</code> должно быть <code>true</code> или "
                "<code>false</code>.",
            ),
            (
                '{"type": "user_specific", "user_ids": [7], "conditions": ["any_message"]}',
                "Поле <code>conditions</code> должно быть объектом",
            ),
            (
                '{"type": "user_specific", "user_ids": [7], "conditions": {"keywords": "hi"}}',
                "Поле <code>conditions.keywords</code> должно быть списком слов",
            ),
            (
                '{"type": "user_specific", "user_ids": [7], "conditions": {"keywords": [5]}}',
                "Поле <code>conditions.keywords</code> должно быть списком слов",
            ),
        ],
        ids=[
            "spam-count-text",
            "spam-window-bool",
            "spam-length-null",
            "flood-count-text",
            "flood-interval-list",
            "user-ids-text",
            "user-ids-of-text",
            "target-users-bool",
            "exclude-users-number",
            "match-type-unknown",
            "match-type-list",
            "match-type-escaped",
            "case-sensitive-text",
            "conditions-list",
            "condition-words-text",
            "condition-words-numbers",
        ],
    )
    async def test_a_field_the_engine_cannot_read_is_refused(self, typed: str, says: str) -> None:
        deps, state = await _strict_cfg()
        message = _typed(typed)

        await handlers.handle_rule_config_input(message, state, **_services(deps))

        deps["rules_repo"].create.assert_not_awaited()
        assert state.current == AdminStates.awaiting_rule_config.state
        text, markup = _sent(message)
        assert says in text, text
        assert _datas(markup) == [f"ar_list:ru:{CHAT_ID}:0"]

    @pytest.mark.parametrize(
        "typed",
        [
            '{"type": "spam_detect", "max_messages": 5, "time_window_seconds": 60, '
            '"max_chars_per_message": 0}',
            '{"type": "sticker_flood", "max_stickers_per_interval": 3, "interval_seconds": 60}',
            '{"type": "user_specific", "user_ids": [11, 12], "conditions": {"keywords": ["hi"]}}',
            '{"type": "user_specific", "user_ids": [11], "conditions": {"any_message": true}}',
            '{"keywords": ["kw"], "match_type": "regex", "case_sensitive": true, '
            '"target_users": [11], "exclude_users": []}',
            '{"keywords": ["kw"], "match_type": "exact", "case_sensitive": false}',
            '{"type": "spam_detect", "user_ids": "not this type\'s field"}',
        ],
        ids=[
            "spam",
            "flood",
            "user-words",
            "user-any",
            "keyword-all-fields",
            "keyword-exact",
            "another-types-field",
        ],
    )
    async def test_every_field_typed_as_the_engine_reads_it_makes_the_rule(
        self, typed: str
    ) -> None:
        """Control: each check refuses only what the engine cannot read, and
        only for the type that reads it."""
        deps, state = await _strict_cfg()

        await handlers.handle_rule_config_input(_typed(typed), state, **_services(deps))

        created = _created(deps)
        assert created["config"] == {
            k: v for k, v in json.loads(typed).items() if k not in ("type", "weight", "mandatory")
        }
        assert state.current is None


# ---------------------------------------------------------------------------
# The prompt above a typed answer
# ---------------------------------------------------------------------------


class TestRetirePrompt:
    async def test_takes_the_keyboard_off_the_prompt(self, retire: AsyncMock) -> None:
        message = _typed("x")
        await handlers._retire_prompt(message, PROMPT_ID)

        stub = retire.await_args.args[0]
        assert stub.message_id == PROMPT_ID
        assert stub.chat == message.chat
        assert retire.await_args.kwargs == {"reply_markup": None}

    @pytest.mark.parametrize("prompt_id", [None, True, "555"])
    async def test_without_a_prompt_id_does_nothing(
        self, prompt_id: object, retire: AsyncMock
    ) -> None:
        await handlers._retire_prompt(_typed("x"), prompt_id)
        retire.assert_not_awaited()

    async def test_a_refused_edit_is_logged_not_raised(self, retire: AsyncMock) -> None:
        retire.side_effect = TelegramBadRequest(
            method=EditMessageReplyMarkup(chat_id=1, message_id=PROMPT_ID),
            message="message to edit not found",
        )
        with patch.object(handlers, "logger") as logger:
            await handlers._retire_prompt(_typed("x"), PROMPT_ID)

        logger.info.assert_called_once()
        assert logger.info.call_args.args[0] == "rule_prompt_keyboard_kept"


# ---------------------------------------------------------------------------
# A double tap on a last step makes one rule
# ---------------------------------------------------------------------------


def _slow_create(deps: dict[str, Any]) -> None:
    """``create`` yields to the loop, as the real INSERT does, so two handlers interleave."""

    async def create(**_kwargs: Any) -> int:
        await asyncio.sleep(0)
        return RULE_ID

    deps["rules_repo"].create = AsyncMock(side_effect=create)


class TestOneRulePerAnswer:
    """Telegram delivers a double tap as two updates, handled concurrently.

    The draft is claimed before the write, so the second copy finds none.
    """

    async def _to_last_step(self, deps: dict[str, Any], state: _State) -> None:
        await _start(deps, state)
        await _tap(handlers.handle_builder_type, f"ar_b_type:ru:{CHAT_ID}:s", state, deps)
        await _tap(handlers.handle_builder_count, f"ar_b_n:ru:{CHAT_ID}:40", state, deps)

    @pytest.mark.parametrize(
        ("handler", "data", "before"),
        [
            (handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:n", None),
            (handlers.handle_builder_preset_text, f"ar_b_txt:ru:{CHAT_ID}:0", "w"),
            (handlers.handle_builder_preset_reaction, f"ar_b_rx:ru:{CHAT_ID}:0", "r"),
        ],
        ids=["notify-admin", "ready-warning", "ready-reaction"],
    )
    async def test_two_taps_at_once_make_one_rule(
        self, handler: Any, data: str, before: str | None, stale: AsyncMock
    ) -> None:
        deps, state = _deps(), _State()
        await self._to_last_step(deps, state)
        if before is not None:
            await _tap(
                handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:{before}", state, deps
            )
        _slow_create(deps)

        await asyncio.gather(_tap(handler, data, state, deps), _tap(handler, data, state, deps))

        deps["rules_repo"].create.assert_awaited_once()
        stale.assert_awaited_once()

    async def test_a_second_tap_after_the_first_is_stale(self, stale: AsyncMock) -> None:
        deps, state = _deps(), _State()
        await self._to_last_step(deps, state)

        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:n", state, deps)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:n", state, deps)

        deps["rules_repo"].create.assert_awaited_once()
        stale.assert_awaited_once()

    async def test_a_typed_answer_sent_twice_at_once_makes_one_rule(self) -> None:
        deps, state = _deps(), _State()
        await self._to_last_step(deps, state)
        await _tap(handlers.handle_builder_action, f"ar_b_act:ru:{CHAT_ID}:w", state, deps)
        _slow_create(deps)

        await asyncio.gather(
            handlers.handle_rule_text_input(_typed("Тише"), state, **_services(deps)),
            handlers.handle_rule_text_input(_typed("Тише"), state, **_services(deps)),
        )

        deps["rules_repo"].create.assert_awaited_once()

    async def test_json_sent_twice_at_once_makes_one_rule(self) -> None:
        deps = _deps()
        state = await _cfg(deps)
        _slow_create(deps)
        typed = '{"name": "x", "keywords": ["kw"]}'

        await asyncio.gather(
            handlers.handle_rule_config_input(_typed(typed), state, **_services(deps)),
            handlers.handle_rule_config_input(_typed(typed), state, **_services(deps)),
        )

        deps["rules_repo"].create.assert_awaited_once()
