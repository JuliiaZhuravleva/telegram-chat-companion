"""`show_rules`, the stable entry other areas call to open one chat's rules list.

Pins what it draws: the same list the ``ar_list:`` button draws. The rules
area owns this file. The entry's signature and its ``data`` plumbing are the
foundation's (``test_screens_entries.py``).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import Message

from src.bot.handlers.rules import show_rules
from src.bot.screens import deps
from src.bot.screens import rules as screens
from src.bot.screens.render import to_markup
from src.bot.states.admin import AdminStates
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.rules import RulesRepository
from src.models.chat_config import ChatConfig
from src.services.chat_config import ChatConfigService

CHAT_ID = -1009999999999  # invented


def _callback() -> MagicMock:
    callback = MagicMock()
    callback.data = "ar_list:ru:-1009999999999"
    callback.answer = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    return callback


def _data(**extra: Any) -> tuple[dict[str, Any], MagicMock]:
    repo = MagicMock(spec=RulesRepository)
    repo.get_rules_page = AsyncMock(return_value=([], 0))
    service = MagicMock(spec=ChatConfigService)
    service.get_config = AsyncMock(
        return_value=replace(ChatConfig(chat_id=CHAT_ID), rules_enabled=True)
    )
    settings = MagicMock(spec=ChatSettingsRepository)
    settings.get = AsyncMock(return_value={"chat_id": CHAT_ID, "rules_enabled": True})
    found = {RulesRepository: repo, ChatConfigService: service, ChatSettingsRepository: settings}
    container = MagicMock()
    container.get = AsyncMock(side_effect=lambda dependency: found[dependency])
    return {deps.SCREEN_CONTAINER: container, **extra}, repo


def _state(current: str | None) -> MagicMock:
    state = MagicMock(spec=FSMContext)
    state.get_state = AsyncMock(return_value=current)
    state.clear = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_show_rules_renders_the_list_with_a_notice():
    data, repo = _data()
    callback = _callback()

    await show_rules(callback, "ru", CHAT_ID, notice="Правило удалено", data=data)

    repo.get_rules_page.assert_awaited_once()
    assert repo.get_rules_page.await_args.args[:2] == (CHAT_ID, 0)
    args, kwargs = callback.message.edit_text.await_args
    assert args[0].startswith("<i>✓ Правило удалено</i>\n\n")
    expected = screens.rules_screen(
        "ru",
        chat_id=CHAT_ID,
        title=screens.chat_id_text(CHAT_ID),
        chat=screens.ChatRules(enabled=True, common=False),
        rules=(),
    )
    assert kwargs["reply_markup"] == to_markup(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["state", deps.SCREEN_STATE])
@pytest.mark.parametrize(
    "prompt",
    [
        AdminStates.awaiting_rule_config,
        AdminStates.awaiting_rule_words,
        AdminStates.awaiting_rule_text,
        AdminStates.awaiting_rule_react,
    ],
    ids=lambda s: s.state,
)
async def test_show_rules_leaves_a_rule_prompt(key: str, prompt: State):
    """As the ``ar_list:`` button does: the list has no Cancel, so a prompt left
    armed would take the admin's next message as a rule's input."""
    state = _state(prompt.state)
    data, _ = _data(**{key: state})
    await show_rules(_callback(), "ru", CHAT_ID, data=data)
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_show_rules_leaves_another_prompt_alone():
    state = _state(AdminStates.awaiting_system_prompt.state)
    data, _ = _data(state=state)
    await show_rules(_callback(), "ru", CHAT_ID, data=data)
    state.clear.assert_not_awaited()
