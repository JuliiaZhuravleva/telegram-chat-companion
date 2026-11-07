"""What a tap on a chat screen does: the chat list, one chat, its sections,
the switches, sticker explicitness and ``/panel``.

How each screen looks is pinned by its samples and ``test_screen_rules``.
These tests pin the behaviour behind it: which repository call a tap makes,
what it refuses, and which screen it lands on afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageReplyMarkup
from aiogram.types import Message
from magic_filter import MagicFilter

from src.bot.handlers.admin_chat_panel import (
    handle_chat_panel_group,
    handle_chat_panel_menu,
    handle_chat_panel_picker,
    handle_chat_panel_shortcut,
    handle_chat_panel_toggle,
    handle_chat_panel_tolerance_cancel,
    handle_chat_panel_tolerance_input,
    handle_chat_panel_tolerance_prompt,
    handle_chat_panel_tolerance_set,
)
from src.bot.screens import chat as screens
from src.bot.screens.render import render_text
from src.bot.states.admin import AdminStates
from src.models.chat_config import ChatConfig

ADMIN_ID = 111
BOT_ID = 999
CHAT_ID = -1001234567890  # invented
PROMPT_ID = 42
MODULE = "src.bot.handlers.admin_chat_panel"
ROW: dict[str, Any] = {"chat_title": "Тестовый чат", "chat_type": "supergroup"}
CHAT_GONE = "Этого чата нет в настройках"

pytestmark = pytest.mark.asyncio


# ── doubles ───────────────────────────────────────────────────────────────


def _callback(
    data: str, *, user_id: int = ADMIN_ID, chat_type: str = "private", bot: Any = None
) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = chat_type
    callback.message.message_id = PROMPT_ID
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = bot
    return callback


def _bot(status: ChatMemberStatus = ChatMemberStatus.ADMINISTRATOR) -> MagicMock:
    bot = MagicMock()
    bot.me = AsyncMock(return_value=MagicMock(id=BOT_ID))
    bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    return bot


def _bot_config_repo(**defaults: object) -> MagicMock:
    repo = MagicMock()

    async def _get(key: str) -> object:
        if key == "admin_ids":
            return [ADMIN_ID]
        return defaults.get(key)

    repo.get = AsyncMock(side_effect=_get)
    return repo


def _chat_settings_repo(
    row: dict[str, Any] | None, *, toggled: bool | None = False, written: bool = True
) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=row)
    repo.set_field = AsyncMock(return_value=written)
    repo.toggle_bool_field = AsyncMock(return_value=toggled)
    return repo


def _service(**overrides: object) -> MagicMock:
    service = MagicMock()
    config = replace(ChatConfig(chat_id=CHAT_ID), **{"enabled": True, **overrides})
    service.get_config = AsyncMock(return_value=config)
    service.invalidate = MagicMock()
    return service


def _admin_repo(
    chats: list[dict[str, Any]] | None = None,
    total: int | None = None,
    *,
    pending: int = 0,
    rejected: int = 0,
) -> MagicMock:
    repo = MagicMock()
    counts = {"pending": pending, "rejected": rejected}
    repo.count_attempts = AsyncMock(side_effect=lambda status: counts[status])
    rows = list(chats or [])
    repo.get_enabled_chats_page_by_activity = AsyncMock(
        return_value=(rows, len(rows) if total is None else total)
    )
    repo.get_admin_language = AsyncMock(return_value="ru")
    repo.find_enabled_chats_by_title = AsyncMock(return_value=[])
    return repo


def _rules_repo(count: int = 0) -> MagicMock:
    repo = MagicMock()
    repo.count_for_chat = AsyncMock(return_value=count)
    return repo


def _state(data: dict[str, Any] | None = None, current: str | None = None) -> MagicMock:
    state = MagicMock()
    state.get_data = AsyncMock(return_value=dict(data or {}))
    state.get_state = AsyncMock(return_value=current)
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    return state


def _message(text: str, *, user_id: int = ADMIN_ID, chat_type: str = "private") -> MagicMock:
    message = MagicMock()
    message.text = text
    message.chat = MagicMock()
    message.chat.id = user_id
    message.chat.type = chat_type
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.bot = MagicMock()
    message.bot.edit_message_reply_markup = AsyncMock()
    message.reply = AsyncMock()
    message.answer = AsyncMock(return_value=MagicMock(message_id=77))
    return message


def _screen(call: Any) -> tuple[str, list[list[str]]]:
    markup = call.kwargs.get("reply_markup")
    rows = [[b.callback_data for b in row] for row in markup.inline_keyboard] if markup else []
    return call.args[0], rows


def _edited(callback: MagicMock) -> tuple[str, list[list[str]]]:
    """Text and button payloads of the screen a tap edited in."""
    return _screen(callback.message.edit_text.await_args)


def _sent(message: MagicMock) -> tuple[str, list[list[str]]]:
    """Text and button payloads of the last new message."""
    return _screen(message.answer.await_args)


def _labels(callback: MagicMock) -> list[str]:
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    return [b.text for row in markup.inline_keyboard for b in row]


def _flat(rows: list[list[str]]) -> list[str]:
    return [data for row in rows for data in row]


@pytest.fixture
def stale() -> Iterator[AsyncMock]:
    with patch(f"{MODULE}.show_stale", new=AsyncMock()) as mock:
        yield mock


@pytest.fixture
def rules() -> Iterator[AsyncMock]:
    with patch(f"{MODULE}.show_rules", new=AsyncMock()) as mock:
        yield mock


# ── the chat list ─────────────────────────────────────────────────────────


class TestChatList:
    async def test_refuses_a_non_admin(self) -> None:
        callback = _callback("adm_pnl:ru:0", user_id=5)
        admin_repo = _admin_repo()

        await handle_chat_panel_picker(callback, admin_repo, _bot_config_repo())

        callback.answer.assert_awaited_once_with("Access denied.", show_alert=True)
        admin_repo.get_enabled_chats_page_by_activity.assert_not_awaited()

    async def test_ignores_a_tap_outside_the_dm(self) -> None:
        callback = _callback("adm_pnl:ru:0", chat_type="group")
        admin_repo = _admin_repo()

        await handle_chat_panel_picker(callback, admin_repo, _bot_config_repo())

        callback.answer.assert_awaited_once_with()
        admin_repo.get_enabled_chats_page_by_activity.assert_not_awaited()

    async def test_lists_chats_by_activity_with_a_way_back_to_the_hub(self) -> None:
        chats = [
            {"chat_id": -1001, "chat_title": "Один", "message_count_24h": 5},
            {"chat_id": -1002, "chat_title": "Два", "message_count_24h": 0},
        ]
        admin_repo = _admin_repo(chats)
        callback = _callback("adm_pnl:ru:0")

        await handle_chat_panel_picker(callback, admin_repo, _bot_config_repo())

        admin_repo.get_enabled_chats_page_by_activity.assert_awaited_once_with(0, 4)
        _, rows = _edited(callback)
        assert rows == [["adm_pnl_menu:ru:-1001"], ["adm_pnl_menu:ru:-1002"], ["adm_menu:ru"]]
        assert _labels(callback)[0] == "Один · 5"

    async def test_requests_row_comes_first_and_costs_one_chat_per_page(self) -> None:
        admin_repo = _admin_repo([{"chat_id": -1001, "chat_title": "Один"}], pending=2, rejected=1)
        callback = _callback("adm_pnl:ru:0")

        await handle_chat_panel_picker(callback, admin_repo, _bot_config_repo())

        admin_repo.get_enabled_chats_page_by_activity.assert_awaited_once_with(0, 3)
        _, rows = _edited(callback)
        assert rows[0] == ["adm_wl_pending:ru:0"]
        assert rows[-1] == ["adm_wl_rejected:ru:0", "adm_menu:ru"]
        labels = _labels(callback)
        assert labels[0] == "🙏 Запросы доступа · 2"
        assert labels[-2:] == ["🚫 Отклонённые · 1", "⬅️ Панель"]

    async def test_rejected_alone_shares_the_last_row_and_costs_no_chat(self) -> None:
        """«🚫 Отклонённые» sits next to the way back, so it takes no chat's row."""
        admin_repo = _admin_repo([{"chat_id": -1001, "chat_title": "Один"}], rejected=1)
        callback = _callback("adm_pnl:ru:0")

        await handle_chat_panel_picker(callback, admin_repo, _bot_config_repo())

        admin_repo.get_enabled_chats_page_by_activity.assert_awaited_once_with(0, 4)
        _, rows = _edited(callback)
        assert rows == [["adm_pnl_menu:ru:-1001"], ["adm_wl_rejected:ru:0", "adm_menu:ru"]]

    @pytest.mark.parametrize(
        ("page", "listed", "pending", "line"),
        [
            ("0", 3, 1, "Подключено: 5, показаны 1–3."),
            ("1", 2, 1, "Подключено: 5, показаны 4–5."),
            ("1", 1, 0, "Подключено: 5, показан 5-й."),
        ],
    )
    async def test_more_than_one_page_says_which_chats_this_one_lists(
        self, page: str, listed: int, pending: int, line: str
    ) -> None:
        """«Подключено: 5» over three buttons read as three chats missing. The
        range comes from the page size the handler fetched with."""
        chats = [{"chat_id": -1000 - i, "chat_title": f"Чат {i}"} for i in range(listed)]
        admin_repo = _admin_repo(chats, total=5, pending=pending)
        callback = _callback(f"adm_pnl:ru:{page}")

        await handle_chat_panel_picker(callback, admin_repo, _bot_config_repo())

        assert line in _edited(callback)[0]

    async def test_one_page_says_only_how_many(self) -> None:
        chats = [{"chat_id": -1001, "chat_title": "Один"}, {"chat_id": -1002, "chat_title": "Два"}]
        callback = _callback("adm_pnl:ru:0")

        await handle_chat_panel_picker(callback, _admin_repo(chats), _bot_config_repo())

        text = _edited(callback)[0]
        assert "Подключено: 2." in text
        assert "показан" not in text

    async def test_a_page_past_the_end_shows_the_last_page(self) -> None:
        admin_repo = _admin_repo([{"chat_id": -1001, "chat_title": "Один"}], total=5)
        callback = _callback("adm_pnl:ru:9")

        await handle_chat_panel_picker(callback, admin_repo, _bot_config_repo())

        calls = admin_repo.get_enabled_chats_page_by_activity.await_args_list
        assert [c.args for c in calls] == [(9, 4), (1, 4)]

    async def test_an_unreadable_page_is_stale(self, stale: AsyncMock) -> None:
        admin_repo = _admin_repo()
        callback = _callback("adm_pnl:ru:x")

        await handle_chat_panel_picker(callback, admin_repo, _bot_config_repo())

        stale.assert_awaited_once()
        admin_repo.get_enabled_chats_page_by_activity.assert_not_awaited()

    async def test_no_chats_shows_the_empty_screen(self) -> None:
        callback = _callback("adm_pnl:ru:0")

        await handle_chat_panel_picker(callback, _admin_repo(), _bot_config_repo())

        empty = screens.build_chats("ru", [], total=0, page=0, pages=1)
        assert _edited(callback) == (render_text(empty), [["adm_menu:ru"]])


# ── one chat ──────────────────────────────────────────────────────────────


async def _open_chat(
    callback: MagicMock,
    row: dict[str, Any] | None = ROW,
    *,
    service: MagicMock | None = None,
    rules_count: int = 0,
    admin_repo: MagicMock | None = None,
) -> None:
    await handle_chat_panel_menu(
        callback,
        _chat_settings_repo(row),
        _bot_config_repo(),
        service or _service(),
        _rules_repo(rules_count),
        admin_repo or _admin_repo(),
    )


class TestChatRoot:
    async def test_refuses_a_non_admin(self) -> None:
        callback = _callback(f"adm_pnl_menu:ru:{CHAT_ID}", user_id=5)

        await _open_chat(callback)

        callback.answer.assert_awaited_once_with("Access denied.", show_alert=True)
        callback.message.edit_text.assert_not_awaited()

    async def test_an_unreadable_chat_id_is_stale(self, stale: AsyncMock) -> None:
        callback = _callback("adm_pnl_menu:ru:not-an-int")

        await _open_chat(callback)

        stale.assert_awaited_once()
        callback.message.edit_text.assert_not_awaited()

    async def test_names_the_chat_links_it_and_counts_its_rules(self) -> None:
        callback = _callback(f"adm_pnl_menu:ru:{CHAT_ID}")

        await _open_chat(callback, {**ROW, "chat_title": "A & B"}, rules_count=3)

        text, rows = _edited(callback)
        assert text.startswith(f"🗂 <b>A &amp; B</b> <code>{CHAT_ID}</code>")
        assert '<a href="https://t.me/c/1234567890">' in text
        assert f"adm_pnl_grp:ru:{CHAT_ID}:behavior" in _flat(rows)
        assert f"ar_list:ru:{CHAT_ID}:0" in _flat(rows)
        assert "📏 Правила · 3" in _labels(callback)
        assert rows[-1] == [f"adm_wl_rm_ask:ru:{CHAT_ID}:0", "adm_pnl:ru:0"]

    async def test_the_organizers_are_one_tap_from_the_root_with_their_count(self) -> None:
        """Both organizer tasks begin at the chat, not in a setting: removing the
        one who left is this button, then the name."""
        callback = _callback(f"adm_pnl_menu:ru:{CHAT_ID}")

        await _open_chat(callback, {**ROW, "kb_organizer_ids": [11, 12, 13]})

        _, rows = _edited(callback)
        assert "👥 Организаторы · 3" in _labels(callback)
        assert f"adm_kb_orgs:ru:{CHAT_ID}:0" in _flat(rows)

    async def test_the_disconnect_button_reads_as_an_action(self) -> None:
        """«🔌 Отключить» alone was read as the chat's state."""
        callback = _callback(f"adm_pnl_menu:ru:{CHAT_ID}")

        await _open_chat(callback)

        assert "🔌 Отключить бота" in _labels(callback)

    async def test_says_when_the_bot_is_off_in_the_chat(self) -> None:
        callback = _callback(f"adm_pnl_menu:ru:{CHAT_ID}")

        await _open_chat(callback, service=_service(enabled=False))

        assert "Бот выключен в этом чате." in _edited(callback)[0]

    async def test_a_chat_without_settings_falls_back_to_the_list(self) -> None:
        admin_repo = _admin_repo()
        callback = _callback(f"adm_pnl_menu:ru:{CHAT_ID}")

        await _open_chat(callback, None, admin_repo=admin_repo)

        assert CHAT_GONE in _edited(callback)[0]
        admin_repo.get_enabled_chats_page_by_activity.assert_awaited()


# ── sections ──────────────────────────────────────────────────────────────


async def _open_section(
    callback: MagicMock,
    row: dict[str, Any] | None = ROW,
    *,
    bot_config_repo: MagicMock | None = None,
    service: MagicMock | None = None,
    admin_repo: MagicMock | None = None,
    **kwargs: Any,
) -> None:
    await handle_chat_panel_group(
        callback,
        _chat_settings_repo(row),
        bot_config_repo or _bot_config_repo(),
        service or _service(),
        admin_repo or _admin_repo(),
        **kwargs,
    )


class TestSections:
    async def test_memory_has_no_organizers_button_any_more(self) -> None:
        callback = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:memory")

        await _open_section(callback, {**ROW, "kb_organizer_ids": [11, 12]})

        _, rows = _edited(callback)
        assert not [data for data in _flat(rows) if data.startswith("adm_kb_orgs:")]

    @pytest.mark.parametrize(
        ("token", "title"),
        [
            ("memory", "🧠 <b>Память</b>"),
            ("modules", "🧠 <b>Память</b>"),  # an old group button
            ("kb", "🧠 <b>Память</b>"),  # an old group button
            ("voice", "🎙 <b>Голос и фото</b>"),
            ("stickers", "🎨 <b>Стикеры</b>"),
            ("community", "👋 <b>Новички и уходы</b>"),
        ],
    )
    async def test_token_opens_its_section(self, token: str, title: str) -> None:
        callback = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:{token}")

        await _open_section(callback)

        text, rows = _edited(callback)
        assert text.startswith(f"{title} · Тестовый чат")
        assert rows[-1][-1] == f"adm_pnl_menu:ru:{CHAT_ID}"

    async def test_rules_open_the_rules_screen(self, rules: AsyncMock) -> None:
        callback = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:rules")

        await _open_section(callback)

        assert rules.await_args.args == (callback, "ru", CHAT_ID)
        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.parametrize(
        "data",
        ["adm_pnl_grp:ru:x:memory", f"adm_pnl_grp:ru:{CHAT_ID}:nope", f"adm_pnl_grp:ru:{CHAT_ID}"],
    )
    async def test_an_unreadable_tap_is_stale(self, data: str, stale: AsyncMock) -> None:
        callback = _callback(data)

        await _open_section(callback)

        stale.assert_awaited_once()
        callback.message.edit_text.assert_not_awaited()

    async def test_refuses_a_non_admin(self) -> None:
        callback = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:memory", user_id=5)

        await _open_section(callback)

        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.parametrize(
        ("status", "line"),
        [
            (ChatMemberStatus.ADMINISTRATOR, "✓ Бот — админ чата, реакции видны."),
            (ChatMemberStatus.MEMBER, "⚠️ Бот не администратор чата"),
        ],
    )
    async def test_reactions_ask_telegram_every_time(
        self, status: ChatMemberStatus, line: str
    ) -> None:
        """ADR-0004 Decision 5: never cached, since a demotion sends no update."""
        bot = _bot(status)
        callback = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:reactions", bot=bot)

        await _open_section(callback)

        bot.get_chat_member.assert_awaited_once_with(CHAT_ID, BOT_ID)
        assert line in _edited(callback)[0]

    async def test_the_bot_id_singleton_spares_a_me_call(self) -> None:
        bot = _bot()
        callback = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:reactions", bot=bot)

        await _open_section(callback, bot_id=BOT_ID)

        bot.me.assert_not_awaited()
        bot.get_chat_member.assert_awaited_once_with(CHAT_ID, BOT_ID)

    async def test_other_sections_do_not_ask_telegram(self) -> None:
        bot = _bot()
        callback = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:memory", bot=bot)

        await _open_section(callback)

        bot.get_chat_member.assert_not_awaited()

    async def test_switch_values_are_read_fresh_not_from_the_cached_config(self) -> None:
        """The knowledge base and reactions switches read their column plus the
        global default, the same rule admin_kb.py uses, so the two screens
        never disagree. The cached config here says the opposite of both."""
        row = {
            **ROW,
            "kb_enabled": None,
            "reactions_enabled": True,
            "reactions_history_enabled": None,
        }
        defaults = _bot_config_repo(
            default_kb_enabled=True, default_reactions_history_enabled=False
        )
        stale_cache = _service(
            kb_enabled=False, reactions_enabled=False, reactions_history_enabled=True
        )

        memory = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:memory")
        await _open_section(memory, row, bot_config_repo=defaults, service=stale_cache)
        reactions = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:reactions")
        await _open_section(reactions, row, bot_config_repo=defaults, service=stale_cache)

        assert "✅ База знаний" in _labels(memory)
        (common,) = [
            line for line in _edited(memory)[0].split("\n") if line.startswith("Как в общих")
        ]
        assert "База знаний" in common
        assert "✅ Реакции в чате" in _labels(reactions)
        assert any(label.startswith("⚪ Запоминать реакции") for label in _labels(reactions))

    async def test_a_chat_without_settings_falls_back_to_the_list(self) -> None:
        callback = _callback(f"adm_pnl_grp:ru:{CHAT_ID}:memory")

        await _open_section(callback, None)

        assert CHAT_GONE in _edited(callback)[0]


# ── switches ──────────────────────────────────────────────────────────────


async def _toggle(
    callback: MagicMock,
    repo: MagicMock,
    *,
    bot_config_repo: MagicMock | None = None,
    service: MagicMock | None = None,
    admin_repo: MagicMock | None = None,
    **kwargs: Any,
) -> MagicMock:
    service = service or _service()
    await handle_chat_panel_toggle(
        callback,
        repo,
        bot_config_repo or _bot_config_repo(),
        service,
        admin_repo or _admin_repo(),
        **kwargs,
    )
    return service


class TestSwitches:
    async def test_refuses_a_non_admin(self) -> None:
        repo = _chat_settings_repo(ROW)

        await _toggle(_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag", user_id=5), repo)

        repo.toggle_bool_field.assert_not_awaited()

    async def test_flips_in_the_database_invalidates_and_says_what_changed(self) -> None:
        repo = _chat_settings_repo(ROW, toggled=False)
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")

        service = await _toggle(callback, repo)

        repo.toggle_bool_field.assert_awaited_once_with(CHAT_ID, "rag_enabled", True)
        service.invalidate.assert_called_once_with(CHAT_ID)
        text, _ = _edited(callback)
        assert text.startswith("<i>✓ Выключено: Помнить свои диалоги</i>\n\n🧠 <b>Память</b>")

    async def test_an_inherited_switch_flips_from_the_value_the_chat_runs_with(self) -> None:
        repo = _chat_settings_repo({**ROW, "chunks_enabled": None}, toggled=False)

        await _toggle(
            _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:ch"), repo, service=_service(chunks_enabled=True)
        )

        repo.toggle_bool_field.assert_awaited_once_with(CHAT_ID, "chunks_enabled", True)

    @pytest.mark.parametrize(
        ("code", "row", "defaults", "key"),
        [
            ("kb", {"kb_enabled": None}, {"default_kb_enabled": True}, "kb_enabled"),
            (
                "rx",
                {"reactions_enabled": None},
                {"default_reactions_enabled": True},
                "reactions_enabled",
            ),
            (
                "rh",
                {"reactions_enabled": True, "reactions_history_enabled": None},
                {},
                "reactions_history_enabled",
            ),
        ],
    )
    async def test_knowledge_base_and_reactions_switch_here_now(
        self, code: str, row: dict[str, Any], defaults: dict[str, Any], key: str
    ) -> None:
        """These three used to be links to their own areas and were refused
        here (ADR-0010; superseded, see admin-dm-internals.md). Each one now
        writes from the same fresh value its section shows: column, then
        global default, then the bot's fallback (all three effectively on)."""
        repo = _chat_settings_repo({**ROW, **row}, toggled=False)
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:{code}")

        service = await _toggle(
            callback,
            repo,
            bot_config_repo=_bot_config_repo(**defaults),
            service=_service(kb_enabled=False, reactions_enabled=False),
        )

        repo.toggle_bool_field.assert_awaited_once_with(CHAT_ID, key, True)
        service.invalidate.assert_called_once_with(CHAT_ID)

    async def test_a_chat_without_settings_falls_back_to_the_list_and_writes_nothing(
        self,
    ) -> None:
        """The same landing as opening a section of a chat that has gone: the
        list with a line saying so, not a popup over a screen that is wrong."""
        repo = _chat_settings_repo(None)
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")

        await _toggle(callback, repo)

        repo.toggle_bool_field.assert_not_awaited()
        text, _ = _edited(callback)
        assert CHAT_GONE in text
        assert "🗂 <b>Чаты</b>" in text
        assert callback.answer.await_args.kwargs.get("show_alert") is not True

    async def test_a_write_that_matched_no_row_claims_nothing(self) -> None:
        repo = _chat_settings_repo(ROW, toggled=None)
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")

        service = await _toggle(callback, repo)

        service.invalidate.assert_not_called()
        text, _ = _edited(callback)
        assert CHAT_GONE in text
        assert "✓" not in text
        assert callback.answer.await_args.kwargs.get("show_alert") is not True

    @pytest.mark.parametrize(
        "data",
        [
            f"adm_pnl_tgl:ru:{CHAT_ID}:tol",  # a number, not a switch
            f"adm_pnl_tgl:ru:{CHAT_ID}:zz",  # no such field
            f"adm_pnl_tgl:ru:{CHAT_ID}",
            "adm_pnl_tgl:ru:x:rag",
        ],
    )
    async def test_an_unreadable_tap_is_stale(self, data: str, stale: AsyncMock) -> None:
        repo = _chat_settings_repo(ROW)

        await _toggle(_callback(data), repo)

        stale.assert_awaited_once()
        repo.toggle_bool_field.assert_not_awaited()

    async def test_lands_on_the_switchs_own_section(self) -> None:
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:tv")

        await _toggle(callback, _chat_settings_repo(ROW, toggled=True))

        text, rows = _edited(callback)
        assert text.startswith("<i>✓ Включено: Голосовые</i>\n\n🎙 <b>Голос и фото</b>")
        assert f"adm_pnl_tgl:ru:{CHAT_ID}:tv" in _flat(rows)
        assert rows[-1] == [f"adm_pnl_menu:ru:{CHAT_ID}"]

    async def test_the_rules_switch_lands_on_the_rules_screen(self, rules: AsyncMock) -> None:
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:re")

        await _toggle(callback, _chat_settings_repo(ROW, toggled=True))

        assert rules.await_args.args == (callback, "ru", CHAT_ID)
        assert rules.await_args.kwargs["notice"] == "Включено: Правила"

    async def test_a_community_switch_while_the_mode_is_off_changes_nothing(self) -> None:
        """Its button is hidden while the mode is off, so the tap is from an
        older screen; writing it would change something the admin cannot see."""
        repo = _chat_settings_repo(ROW)
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:gf")

        await _toggle(callback, repo, service=_service(community_enabled=False))

        repo.toggle_bool_field.assert_not_awaited()
        text, _ = _edited(callback)
        assert text.startswith(screens.COMMUNITY_OFF_TAP("ru"))
        assert "👋 <b>Новички и уходы</b>" in text

    async def test_a_community_switch_while_the_mode_is_on_writes(self) -> None:
        repo = _chat_settings_repo(ROW, toggled=True)

        await _toggle(
            _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:gf"),
            repo,
            service=_service(community_enabled=True, greet_on_first_message=False),
        )

        repo.toggle_bool_field.assert_awaited_once_with(CHAT_ID, "greet_on_first_message", False)

    async def test_the_advice_switch_works_with_the_mode_off(self) -> None:
        repo = _chat_settings_repo(ROW, toggled=True)

        await _toggle(
            _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:aw"),
            repo,
            service=_service(community_enabled=False),
        )

        repo.toggle_bool_field.assert_awaited_once()

    async def test_the_history_switch_while_reactions_are_off_changes_nothing(self) -> None:
        repo = _chat_settings_repo({**ROW, "reactions_enabled": False})
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rh")

        await _toggle(callback, repo)

        repo.toggle_bool_field.assert_not_awaited()
        assert _edited(callback)[0].startswith(screens.REACTIONS_OFF_TAP("ru"))

    async def test_switching_reactions_on_without_admin_rights_alerts_and_keeps_it(
        self,
    ) -> None:
        """ADR-0004 Decision 5: the check runs when reactions are switched on,
        because nothing else would tell the admin; the switch still goes on."""
        bot = _bot(ChatMemberStatus.MEMBER)
        repo = _chat_settings_repo({**ROW, "reactions_enabled": False}, toggled=True)
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rx", bot=bot)

        service = await _toggle(callback, repo)

        repo.toggle_bool_field.assert_awaited_once_with(CHAT_ID, "reactions_enabled", False)
        service.invalidate.assert_called_once_with(CHAT_ID)
        callback.answer.assert_awaited_once_with(
            screens.REACTIONS_NOT_ADMIN_ALERT("ru"), show_alert=True
        )
        assert _edited(callback)[0].startswith("<i>✓ Включено: Реакции в чате</i>")

    async def test_switching_reactions_on_with_admin_rights_needs_no_popup(self) -> None:
        bot = _bot(ChatMemberStatus.ADMINISTRATOR)
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rx", bot=bot)

        await _toggle(
            callback, _chat_settings_repo({**ROW, "reactions_enabled": False}, toggled=True)
        )

        callback.answer.assert_awaited_once_with()
        bot.get_chat_member.assert_awaited_once_with(CHAT_ID, BOT_ID)

    async def test_switching_reactions_off_needs_no_popup(self) -> None:
        bot = _bot(ChatMemberStatus.MEMBER)
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rx", bot=bot)

        await _toggle(
            callback, _chat_settings_repo({**ROW, "reactions_enabled": True}, toggled=False)
        )

        callback.answer.assert_awaited_once_with()
        assert bot.get_chat_member.await_count == 1  # the section's own status line

    async def test_the_history_switch_is_not_gated_on_admin_rights(self) -> None:
        bot = _bot(ChatMemberStatus.MEMBER)
        repo = _chat_settings_repo(
            {**ROW, "reactions_enabled": True, "reactions_history_enabled": True}, toggled=False
        )
        callback = _callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rh", bot=bot)

        await _toggle(callback, repo)

        repo.toggle_bool_field.assert_awaited_once_with(CHAT_ID, "reactions_history_enabled", True)
        callback.answer.assert_awaited_once_with()


# ── sticker explicitness ──────────────────────────────────────────────────


async def _tolerance(
    callback: MagicMock,
    state: MagicMock,
    row: dict[str, Any] | None = ROW,
    *,
    admin_repo: MagicMock | None = None,
) -> None:
    await handle_chat_panel_tolerance_prompt(
        callback,
        state,
        _chat_settings_repo(row),
        _bot_config_repo(),
        _service(),
        admin_repo or _admin_repo(),
    )


class TestTolerancePrompt:
    async def test_presets_save_on_a_tap_and_a_number_can_be_typed(self) -> None:
        state = _state()
        callback = _callback(f"adm_pnl_tol:ru:{CHAT_ID}")

        await _tolerance(callback, state)

        state.set_state.assert_not_awaited()
        _, rows = _edited(callback)
        assert f"adm_pnl_tolset:ru:{CHAT_ID}:0.25" in _flat(rows)
        assert f"adm_pnl_tol:ru:{CHAT_ID}:in" in _flat(rows)
        assert rows[-1] == [f"adm_pnl_grp:ru:{CHAT_ID}:stickers"]
        assert "• Обычно 0.5" in _labels(callback)  # the current value is marked

    async def test_typing_a_number_arms_the_prompt_in_place(self) -> None:
        state = _state()
        callback = _callback(f"adm_pnl_tol:ru:{CHAT_ID}:in")

        await _tolerance(callback, state)

        state.set_state.assert_awaited_once_with(AdminStates.awaiting_setting_value)
        state.update_data.assert_awaited_once_with(
            tol_chat_id=CHAT_ID, tol_lang="ru", tol_prompt_id=PROMPT_ID
        )
        callback.message.answer.assert_not_awaited()
        assert _edited(callback)[1] == [[f"adm_pnl_tolcancel:ru:{CHAT_ID}"]]

    async def test_refuses_a_non_admin(self) -> None:
        state = _state()

        await _tolerance(_callback(f"adm_pnl_tol:ru:{CHAT_ID}:in", user_id=5), state)

        state.set_state.assert_not_awaited()

    async def test_an_unreadable_chat_id_is_stale(self, stale: AsyncMock) -> None:
        state = _state()

        await _tolerance(_callback("adm_pnl_tol:ru:x:in"), state)

        stale.assert_awaited_once()
        state.set_state.assert_not_awaited()

    async def test_a_chat_without_settings_falls_back_to_the_list(self) -> None:
        state = _state()
        callback = _callback(f"adm_pnl_tol:ru:{CHAT_ID}:in")

        await _tolerance(callback, state, None)

        state.set_state.assert_not_awaited()
        assert CHAT_GONE in _edited(callback)[0]


class TestTolerancePreset:
    @pytest.mark.parametrize(
        ("raw", "value", "label"), [("0.25", 0.25, "0.25, скромно"), ("1", 1.0, "1, всё можно")]
    )
    async def test_saves_at_once_and_returns_to_stickers(
        self, raw: str, value: float, label: str
    ) -> None:
        repo = _chat_settings_repo(ROW)
        service = _service()
        callback = _callback(f"adm_pnl_tolset:ru:{CHAT_ID}:{raw}")

        await handle_chat_panel_tolerance_set(
            callback, _state(), repo, _bot_config_repo(), service, _admin_repo()
        )

        repo.set_field.assert_awaited_once_with(CHAT_ID, "tolerance_level", value)
        service.invalidate.assert_called_once_with(CHAT_ID)
        text, rows = _edited(callback)
        assert text.startswith(f"<i>✓ Откровенность: {label}</i>\n\n🎨 <b>Стикеры</b>")
        assert rows[-1][-1] == f"adm_pnl_menu:ru:{CHAT_ID}"

    @pytest.mark.parametrize("raw", ["1.5", "-0.1", "abc", ""])
    async def test_a_value_out_of_range_is_stale(self, raw: str, stale: AsyncMock) -> None:
        repo = _chat_settings_repo(ROW)
        data = f"adm_pnl_tolset:ru:{CHAT_ID}:{raw}" if raw else f"adm_pnl_tolset:ru:{CHAT_ID}"

        await handle_chat_panel_tolerance_set(
            _callback(data), _state(), repo, _bot_config_repo(), _service(), _admin_repo()
        )

        stale.assert_awaited_once()
        repo.set_field.assert_not_awaited()

    async def test_a_write_that_matched_no_row_claims_nothing(self) -> None:
        callback = _callback(f"adm_pnl_tolset:ru:{CHAT_ID}:0.5")

        await handle_chat_panel_tolerance_set(
            callback,
            _state(),
            _chat_settings_repo(ROW, written=False),
            _bot_config_repo(),
            _service(),
            _admin_repo(),
        )

        text, _ = _edited(callback)
        assert CHAT_GONE in text
        assert "✓" not in text
        assert callback.answer.await_args.kwargs.get("show_alert") is not True


ARMED = {"tol_chat_id": CHAT_ID, "tol_lang": "ru", "tol_prompt_id": PROMPT_ID}


async def _type(
    message: MagicMock, state: MagicMock, repo: MagicMock | None = None
) -> tuple[MagicMock, MagicMock]:
    repo = repo or _chat_settings_repo(ROW)
    service = _service()
    await handle_chat_panel_tolerance_input(
        message, state, repo, _bot_config_repo(), service, _admin_repo()
    )
    return repo, service


class TestToleranceInput:
    async def test_a_number_saves_and_stickers_follow_as_one_new_message(self) -> None:
        message = _message("0.8")
        state = _state(ARMED)

        repo, service = await _type(message, state)

        state.clear.assert_awaited_once()
        repo.set_field.assert_awaited_once_with(CHAT_ID, "tolerance_level", 0.8)
        service.invalidate.assert_called_once_with(CHAT_ID)
        message.answer.assert_awaited_once()
        text, rows = _sent(message)
        assert text.startswith("<i>✓ Откровенность: 0.8</i>\n\n🎨 <b>Стикеры</b>")
        assert rows[-1][-1] == f"adm_pnl_menu:ru:{CHAT_ID}"

    async def test_the_earlier_prompt_loses_its_cancel(self) -> None:
        message = _message("0.8")

        await _type(message, _state(ARMED))

        message.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=ADMIN_ID, message_id=PROMPT_ID, reply_markup=None
        )

    async def test_an_old_prompt_that_cannot_be_edited_does_not_block_the_save(self) -> None:
        message = _message("0.8")
        message.bot.edit_message_reply_markup.side_effect = TelegramBadRequest(
            method=EditMessageReplyMarkup(chat_id=ADMIN_ID, message_id=PROMPT_ID),
            message="Bad Request: message can't be edited",
        )

        repo, _ = await _type(message, _state(ARMED))

        repo.set_field.assert_awaited_once_with(CHAT_ID, "tolerance_level", 0.8)

    async def test_a_comma_works_as_the_decimal_point(self) -> None:
        repo, _ = await _type(_message("0,3"), _state(ARMED))

        repo.set_field.assert_awaited_once_with(CHAT_ID, "tolerance_level", 0.3)

    @pytest.mark.parametrize("raw", ["1.5", "not a number"])
    async def test_anything_else_asks_again_in_one_new_message(self, raw: str) -> None:
        """The state stays armed (reject, never clamp), so the new prompt must
        carry the way out, and it becomes the one prompt that can act."""
        message = _message(raw)
        state = _state(ARMED)

        repo, _ = await _type(message, state)

        state.clear.assert_not_awaited()
        repo.set_field.assert_not_awaited()
        message.answer.assert_awaited_once()
        text, rows = _sent(message)
        assert text.startswith(screens.TOLERANCE_INVALID("ru"))
        assert rows == [[f"adm_pnl_tolcancel:ru:{CHAT_ID}"]]
        state.update_data.assert_awaited_once_with(tol_prompt_id=77)
        message.bot.edit_message_reply_markup.assert_awaited_once()

    async def test_a_non_admin_value_is_dropped(self) -> None:
        message = _message("0.8", user_id=5)
        state = _state(ARMED)

        repo, _ = await _type(message, state)

        state.clear.assert_awaited_once()
        repo.set_field.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_without_a_chat_the_state_is_cleared(self) -> None:
        message = _message("0.8")
        state = _state({})

        repo, _ = await _type(message, state)

        state.clear.assert_awaited_once()
        repo.set_field.assert_not_awaited()

    async def test_a_chat_gone_before_asking_again_shows_the_list(self) -> None:
        message = _message("not a number")
        state = _state(ARMED)

        await _type(message, state, _chat_settings_repo(None))

        state.clear.assert_awaited_once()
        text, _ = _sent(message)
        assert CHAT_GONE in text
        assert "🗂 <b>Чаты</b>" in text

    async def test_a_number_for_a_chat_gone_shows_the_list(self) -> None:
        message = _message("0.8")

        await _type(message, _state(ARMED), _chat_settings_repo(ROW, written=False))

        text, _ = _sent(message)
        assert CHAT_GONE in text
        assert "✓" not in text


async def _cancel(callback: MagicMock, state: MagicMock) -> None:
    await handle_chat_panel_tolerance_cancel(
        callback, state, _chat_settings_repo(ROW), _bot_config_repo(), _service(), _admin_repo()
    )


class TestToleranceCancel:
    async def test_clears_the_prompt_and_returns_to_stickers(self) -> None:
        state = _state(ARMED, AdminStates.awaiting_setting_value.state)
        callback = _callback(f"adm_pnl_tolcancel:ru:{CHAT_ID}")

        await _cancel(callback, state)

        state.clear.assert_awaited_once()
        text, rows = _edited(callback)
        assert text.startswith("🎨 <b>Стикеры</b>")
        assert rows[-1][-1] == f"adm_pnl_menu:ru:{CHAT_ID}"

    async def test_leaves_another_flows_state_alone(self) -> None:
        state = _state({}, AdminStates.awaiting_system_prompt.state)

        await _cancel(_callback(f"adm_pnl_tolcancel:ru:{CHAT_ID}"), state)

        state.clear.assert_not_awaited()

    async def test_refuses_a_non_admin(self) -> None:
        state = _state(ARMED, AdminStates.awaiting_setting_value.state)

        await _cancel(_callback(f"adm_pnl_tolcancel:ru:{CHAT_ID}", user_id=5), state)

        state.clear.assert_not_awaited()

    async def test_a_malformed_chat_id_still_clears_the_prompt(self, stale: AsyncMock) -> None:
        """The way out is the one thing this handler may never refuse."""
        state = _state(ARMED, AdminStates.awaiting_setting_value.state)

        await _cancel(_callback("adm_pnl_tolcancel:ru:not-an-int"), state)

        state.clear.assert_awaited_once()
        stale.assert_awaited_once()


# ── /panel <query> ────────────────────────────────────────────────────────


async def _panel(
    message: MagicMock,
    *,
    row: dict[str, Any] | None = None,
    matches: list[dict[str, Any]] | None = None,
    bot: MagicMock | None = None,
    **kwargs: Any,
) -> MagicMock:
    admin_repo = _admin_repo()
    admin_repo.find_enabled_chats_by_title = AsyncMock(return_value=matches or [])
    await handle_chat_panel_shortcut(
        message,
        bot or MagicMock(),
        admin_repo,
        _chat_settings_repo(row),
        _bot_config_repo(),
        _service(),
        _rules_repo(),
        **kwargs,
    )
    return admin_repo


WHITELISTED = {**ROW, "chat_title": "Target", "enabled": True}


class TestPanelCommand:
    async def test_no_query_says_how_to_use_it(self) -> None:
        message = _message("/panel")

        admin_repo = await _panel(message)

        assert _sent(message) == (
            render_text(screens.build_panel_noquery("ru")),
            [["adm_pnl:ru:0", "adm_menu:ru"]],
        )
        admin_repo.find_enabled_chats_by_title.assert_not_awaited()

    async def test_a_link_to_a_connected_chat_opens_it(self) -> None:
        message = _message("/panel https://t.me/c/1234567890")

        await _panel(message, row=WHITELISTED)

        text, _ = _sent(message)
        assert text.startswith(f"🗂 <b>Target</b> <code>{CHAT_ID}</code>")

    async def test_a_link_to_a_chat_that_is_not_connected_finds_nothing(self) -> None:
        message = _message("/panel https://t.me/c/1234567890")

        await _panel(message, row={**WHITELISTED, "enabled": False})

        assert _sent(message)[0] == render_text(screens.build_panel_notfound("ru"))

    async def test_a_username_is_resolved_through_telegram(self) -> None:
        message = _message("/panel @mychat")
        bot = MagicMock()
        bot.get_chat = AsyncMock(return_value=MagicMock(id=CHAT_ID))

        await _panel(message, row=WHITELISTED, bot=bot)

        bot.get_chat.assert_awaited_once_with("@mychat")
        assert "Target" in _sent(message)[0]

    async def test_a_username_telegram_does_not_know_finds_nothing(self) -> None:
        message = _message("/panel @ghost")
        bot = MagicMock()
        bot.get_chat = AsyncMock(side_effect=Exception("chat not found"))

        await _panel(message, bot=bot)

        assert _sent(message)[0] == render_text(screens.build_panel_notfound("ru"))

    async def test_one_title_match_opens_the_chat(self) -> None:
        message = _message("/panel tar")

        admin_repo = await _panel(
            message, row=WHITELISTED, matches=[{"chat_id": CHAT_ID, "chat_title": "Target"}]
        )

        admin_repo.find_enabled_chats_by_title.assert_awaited_once_with(
            "tar", limit=screens.FIND_SHOWN + 1
        )
        assert _sent(message)[0].startswith("🗂 <b>Target</b>")

    async def test_several_title_matches_offer_a_choice(self) -> None:
        message = _message("/panel foo")
        matches = [
            {"chat_id": -1, "chat_title": "Foo One"},
            {"chat_id": -2, "chat_title": "Foo Two"},
        ]

        await _panel(message, matches=matches)

        text, rows = _sent(message)
        assert rows == [
            ["adm_pnl_menu:ru:-1"],
            ["adm_pnl_menu:ru:-2"],
            ["adm_pnl:ru:0", "adm_menu:ru"],
        ]
        assert "Показаны первые" not in text

    async def test_more_matches_than_fit_say_the_list_was_cut(self) -> None:
        message = _message("/panel foo")
        matches = [{"chat_id": -n, "chat_title": f"Foo {n}"} for n in range(1, 6)]

        await _panel(message, matches=matches)

        text, rows = _sent(message)
        assert len(rows) == screens.FIND_SHOWN + 1
        assert f"Показаны первые {screens.FIND_SHOWN}" in text

    async def test_no_title_match_finds_nothing(self) -> None:
        message = _message("/panel nonexistent")

        await _panel(message)

        assert _sent(message)[0] == render_text(screens.build_panel_notfound("ru"))

    async def test_a_group_chat_never_reaches_this_handler(self) -> None:
        """The chat-type guard is a filter, not a body check.

        A matched handler consumes the update, so a body guard would swallow
        /panel in a group before the text pipeline saw it. What matters is
        that the filter rejects the message, so this checks the registered
        filters against a group chat rather than calling the function.
        """
        from src.bot.handlers.admin_chat_panel import router

        handler = next(
            h for h in router.message.handlers if h.callback is handle_chat_panel_shortcut
        )
        group = _message("/panel foo", chat_type="group")
        private = _message("/panel foo", chat_type="private")

        def chat_type_filters_accept(msg: MagicMock) -> bool:
            checked = 0
            for f in handler.filters or ():
                callback = getattr(f, "callback", f)
                # aiogram wraps a MagicFilter as its bound .resolve method, so
                # the filter object is reachable via __self__. Command()/IsAdmin()
                # need runtime context and are deliberately skipped.
                if not isinstance(getattr(callback, "__self__", None), MagicFilter):
                    continue
                checked += 1
                if not callback(msg):
                    return False
            assert checked == 1, f"expected exactly one MagicFilter, saw {checked}"
            return True

        assert chat_type_filters_accept(private) is True
        assert chat_type_filters_accept(group) is False


# ── leaving the number prompt ─────────────────────────────────────────────


async def _armed(current: State) -> FSMContext:
    """A real FSM context, so `state_of` recognises it in ``**kwargs``."""
    state = FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=BOT_ID, chat_id=ADMIN_ID, user_id=ADMIN_ID)
    )
    await state.set_state(current)
    await state.update_data(ARMED)
    return state


async def _go_to_list(state: FSMContext) -> None:
    await handle_chat_panel_picker(
        _callback("adm_pnl:ru:0"), _admin_repo(), _bot_config_repo(), screen_state=state
    )


async def _go_to_chat(state: FSMContext) -> None:
    await handle_chat_panel_menu(
        _callback(f"adm_pnl_menu:ru:{CHAT_ID}"),
        _chat_settings_repo(ROW),
        _bot_config_repo(),
        _service(),
        _rules_repo(),
        _admin_repo(),
        screen_state=state,
    )


async def _go_to_section(state: FSMContext) -> None:
    await _open_section(_callback(f"adm_pnl_grp:ru:{CHAT_ID}:memory"), screen_state=state)


async def _flip_a_switch(state: FSMContext) -> None:
    await _toggle(_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag"), _chat_settings_repo(ROW), state=state)


async def _go_to_presets(state: FSMContext) -> None:
    await handle_chat_panel_tolerance_prompt(
        _callback(f"adm_pnl_tol:ru:{CHAT_ID}"),
        state,
        _chat_settings_repo(ROW),
        _bot_config_repo(),
        _service(),
        _admin_repo(),
    )


async def _pick_a_preset(state: FSMContext) -> None:
    await handle_chat_panel_tolerance_set(
        _callback(f"adm_pnl_tolset:ru:{CHAT_ID}:0.5"),
        state,
        _chat_settings_repo(ROW),
        _bot_config_repo(),
        _service(),
        _admin_repo(),
    )


async def _send_panel(state: FSMContext) -> None:
    await _panel(_message("/panel"), state=state)


_NAVIGATION = {
    "the chat list": _go_to_list,
    "a chat": _go_to_chat,
    "a section": _go_to_section,
    "a switch": _flip_a_switch,
    "the presets": _go_to_presets,
    "a preset": _pick_a_preset,
    "/panel": _send_panel,
}


class TestLeavingTheNumberPromptClosesIt:
    """``tol.in`` and ``chance.in`` are edited in place, so any older panel
    message still has live buttons. Left armed, the state would read whatever
    the admin types next — a chat's name, a word for /remember — as a value."""

    @pytest.mark.parametrize(
        "armed",
        [AdminStates.awaiting_setting_value, AdminStates.awaiting_response_chance],
        ids=["tolerance", "percentage"],
    )
    @pytest.mark.parametrize("go", _NAVIGATION.values(), ids=_NAVIGATION.keys())
    async def test_the_number_prompt_is_closed(self, go: Any, armed: Any) -> None:
        state = await _armed(armed)

        await go(state)

        assert await state.get_state() is None
        assert await state.get_data() == {}

    @pytest.mark.parametrize("go", _NAVIGATION.values(), ids=_NAVIGATION.keys())
    async def test_the_instruction_draft_is_left_alone(self, go: Any) -> None:
        """The instruction editor's draft waits for its own «Save»; leaving
        it is not this prompt's business."""
        state = await _armed(AdminStates.awaiting_system_prompt)

        await go(state)

        assert await state.get_state() == AdminStates.awaiting_system_prompt.state

    async def test_asking_for_a_number_still_arms_it(self) -> None:
        state = await _armed(AdminStates.awaiting_setting_value)
        await state.clear()

        await handle_chat_panel_tolerance_prompt(
            _callback(f"adm_pnl_tol:ru:{CHAT_ID}:in"),
            state,
            _chat_settings_repo(ROW),
            _bot_config_repo(),
            _service(),
            _admin_repo(),
        )

        assert await state.get_state() == AdminStates.awaiting_setting_value.state
