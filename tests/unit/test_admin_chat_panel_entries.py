"""`show_chat` and `show_chats`, the stable entries other areas call.

Pins what they draw: the same screens the ``adm_pnl_menu:`` and ``adm_pnl:``
buttons draw, with the repositories taken from the request container. The
entries' signatures and their ``data`` plumbing are the foundation's
(``test_screens_entries.py``).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from src.bot.handlers.admin_chat_panel import show_chat, show_chats
from src.bot.screens import deps
from src.bot.states.admin import AdminStates
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.rules import RulesRepository
from src.models.chat_config import ChatConfig
from src.services.chat_config import ChatConfigService

CHAT_ID = -1009999999999  # invented

pytestmark = pytest.mark.asyncio


def _data(**repos: Any) -> dict[str, Any]:
    container = MagicMock()
    container.get = AsyncMock(side_effect=lambda dependency: repos[dependency.__name__])
    return {deps.SCREEN_CONTAINER: container}


def _admin_repo(chats: list[dict[str, Any]], total: int) -> MagicMock:
    repo = MagicMock(spec=AdminRepository)
    repo.count_attempts = AsyncMock(return_value=0)
    repo.get_enabled_chats_page_by_activity = AsyncMock(return_value=(chats, total))
    return repo


async def test_show_chats_renders_the_list_as_a_new_message() -> None:
    admin_repo = _admin_repo([], 0)
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await show_chats(message, "en", 3, data=_data(AdminRepository=admin_repo))

    # page 3 of an empty list shows the first page, as the button does
    assert admin_repo.get_enabled_chats_page_by_activity.await_args.args[0] == 0
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0].startswith("🗂 <b>Chats</b>")


async def test_show_chats_puts_a_notice_above_the_list() -> None:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await show_chats(
        message, "ru", notice="Чат отключён", data=_data(AdminRepository=_admin_repo([], 0))
    )

    assert message.answer.await_args.args[0].startswith("<i>✓ Чат отключён</i>\n\n🗂 <b>Чаты</b>")


async def test_show_chat_resolves_its_five_dependencies_and_edits_in_place() -> None:
    settings = MagicMock(spec=ChatSettingsRepository)
    settings.get = AsyncMock(return_value={"chat_title": "Тест", "chat_type": "supergroup"})
    bot_config = MagicMock(spec=BotConfigRepository)
    bot_config.get = AsyncMock(return_value=None)
    service = MagicMock(spec=ChatConfigService)
    service.get_config = AsyncMock(return_value=replace(ChatConfig(chat_id=CHAT_ID), enabled=True))
    rules = MagicMock(spec=RulesRepository)
    rules.count_for_chat = AsyncMock(return_value=2)
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()

    await show_chat(
        callback,
        "ru",
        CHAT_ID,
        notice="Правило добавлено",
        data=_data(
            ChatSettingsRepository=settings,
            BotConfigRepository=bot_config,
            ChatConfigService=service,
            RulesRepository=rules,
            AdminRepository=_admin_repo([], 0),
        ),
    )

    settings.get.assert_awaited_once_with(CHAT_ID)
    rules.count_for_chat.assert_awaited_once_with(CHAT_ID)
    text = callback.message.edit_text.await_args.args[0]
    assert text.startswith(f"<i>✓ Правило добавлено</i>\n\n🗂 <b>Тест</b> <code>{CHAT_ID}</code>")
    labels = [
        b.text
        for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert "📏 Правила · 2" in labels


async def test_show_chat_for_a_chat_without_settings_shows_the_list() -> None:
    admin_repo = _admin_repo([], 0)
    settings = MagicMock(spec=ChatSettingsRepository)
    settings.get = AsyncMock(return_value=None)
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await show_chat(
        message,
        "ru",
        CHAT_ID,
        data=_data(
            ChatSettingsRepository=settings,
            BotConfigRepository=MagicMock(spec=BotConfigRepository),
            ChatConfigService=MagicMock(spec=ChatConfigService),
            RulesRepository=MagicMock(spec=RulesRepository),
            AdminRepository=admin_repo,
        ),
    )

    admin_repo.get_enabled_chats_page_by_activity.assert_awaited()
    assert "Этого чата нет в настройках" in message.answer.await_args.args[0]


async def _number_prompt() -> FSMContext:
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    await state.set_state(AdminStates.awaiting_setting_value)
    await state.update_data(tol_chat_id=CHAT_ID, tol_lang="ru")
    return state


async def test_show_chats_closes_an_open_number_prompt() -> None:
    """Another area landing the admin on the list is leaving the prompt too."""
    state = await _number_prompt()
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await show_chats(
        message,
        "ru",
        data={**_data(AdminRepository=_admin_repo([], 0)), deps.SCREEN_STATE: state},
    )

    assert await state.get_state() is None


async def test_show_chat_closes_an_open_number_prompt() -> None:
    state = await _number_prompt()
    settings = MagicMock(spec=ChatSettingsRepository)
    settings.get = AsyncMock(return_value=None)
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await show_chat(
        message,
        "ru",
        CHAT_ID,
        data={
            **_data(
                ChatSettingsRepository=settings,
                BotConfigRepository=MagicMock(spec=BotConfigRepository),
                ChatConfigService=MagicMock(spec=ChatConfigService),
                RulesRepository=MagicMock(spec=RulesRepository),
                AdminRepository=_admin_repo([], 0),
            ),
            deps.SCREEN_STATE: state,
        },
    )

    assert await state.get_state() is None
