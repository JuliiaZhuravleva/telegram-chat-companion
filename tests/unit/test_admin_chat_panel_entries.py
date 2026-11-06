"""`show_chat` and `show_chats`, the stable entries other areas call.

Pins what they draw today: the same screens the ``adm_pnl_menu:`` and
``adm_pnl:`` buttons draw. The chats area owns this file — when it redesigns
the screens, it changes these expectations with them. The entries' signatures
and their ``data`` plumbing are the foundation's (``test_screens_entries.py``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from src.bot.handlers.admin_chat_panel import show_chat, show_chats
from src.bot.screens import deps
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

CHAT_ID = -1009999999999  # invented


def _data(**repos: Any) -> dict[str, Any]:
    container = MagicMock()
    container.get = AsyncMock(side_effect=lambda dependency: repos[dependency.__name__])
    return {deps.SCREEN_CONTAINER: container}


@pytest.mark.asyncio
async def test_show_chats_renders_the_picker():
    admin_repo = MagicMock(spec=AdminRepository)
    admin_repo.get_enabled_chats_page_by_activity = AsyncMock(return_value=([], 0))
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await show_chats(message, "en", 3, data=_data(AdminRepository=admin_repo))

    # page 3 of an empty list clamps to the first page, as the button does
    assert admin_repo.get_enabled_chats_page_by_activity.await_args.args[0] == 0
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_show_chat_resolves_its_three_dependencies():
    resolved = {
        "ChatSettingsRepository": MagicMock(spec=ChatSettingsRepository),
        "BotConfigRepository": MagicMock(spec=BotConfigRepository),
        "ChatConfigService": MagicMock(spec=ChatConfigService),
    }
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    with patch(
        "src.bot.handlers.admin_chat_panel.render_chat_panel",
        new=AsyncMock(return_value=("<b>Чат</b>", None)),
    ) as render:
        await show_chat(callback, "ru", CHAT_ID, data=_data(**resolved))

    render.assert_awaited_once_with(
        resolved["ChatSettingsRepository"],
        resolved["BotConfigRepository"],
        resolved["ChatConfigService"],
        "ru",
        CHAT_ID,
    )
    assert callback.message.edit_text.await_args.args[0] == "<b>Чат</b>"
