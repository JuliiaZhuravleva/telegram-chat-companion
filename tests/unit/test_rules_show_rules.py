"""`show_rules`, the stable entry other areas call to open one chat's rules list.

Pins what it draws today: the same list the ``ar_list:`` button draws. The
rules area owns this file — when it redesigns the list, it changes these
expectations with it. The entry's signature and its ``data`` plumbing are the
foundation's (``test_screens_entries.py``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.handlers.rules import show_rules
from src.bot.keyboards.rules import rules_list_keyboard
from src.bot.screens import deps
from src.bot.states.admin import AdminStates
from src.database.repositories.rules import RulesRepository

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
    container = MagicMock()
    container.get = AsyncMock(side_effect=lambda dependency: {RulesRepository: repo}[dependency])
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
    assert kwargs["reply_markup"] == rules_list_keyboard("ru", [], 0, 1, CHAT_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["state", deps.SCREEN_STATE])
async def test_show_rules_leaves_a_rule_config_prompt(key: str):
    """As the ``ar_list:`` button does: the list has no Cancel, so a prompt left
    armed would take the admin's next message as a rule config."""
    state = _state(AdminStates.awaiting_rule_config.state)
    data, _ = _data(**{key: state})
    await show_rules(_callback(), "ru", CHAT_ID, data=data)
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_show_rules_leaves_another_prompt_alone():
    state = _state(AdminStates.awaiting_rule_words.state)
    data, _ = _data(state=state)
    await show_rules(_callback(), "ru", CHAT_ID, data=data)
    state.clear.assert_not_awaited()
