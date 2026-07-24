"""Tests for the Knowledge Base admin sub-router (A4)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from src.bot.handlers.admin_kb import (
    handle_kb_organizer_add_reply,
    handle_kb_organizer_remove,
    handle_kb_toggle,
)

ADMIN_ID = 111
CHAT_ID = -1001234567890
ORG_USER_ID = 555


def _make_callback(data: str, user_id: int = ADMIN_ID, chat_type: str = "private") -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = chat_type
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = None
    return callback


def _make_bot_config_repo(admin_ids: list[int] | None = None) -> MagicMock:
    repo = MagicMock()
    # BotConfigRepository.get() already returns json.loads() output (a parsed
    # list), not a raw JSON string -- see src/utils/parse_admin_ids docstring.
    repo.get = AsyncMock(return_value=admin_ids or [ADMIN_ID])
    return repo


def _make_chat_settings_repo(
    kb_enabled: bool = False, organizer_ids: list[int] | None = None
) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(
        return_value={
            "kb_enabled": kb_enabled,
            "kb_organizer_ids": json.dumps(
                organizer_ids if organizer_ids is not None else [ORG_USER_ID]
            ),
        }
    )
    repo.set_field = AsyncMock()
    return repo


class TestHandleKbToggle:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_kb_toggle:ru:{CHAT_ID}", user_id=999)
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_kb_toggle(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_not_awaited()
        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_flips_disabled_to_enabled(self) -> None:
        callback = _make_callback(f"adm_kb_toggle:ru:{CHAT_ID}")
        chat_settings_repo = _make_chat_settings_repo(kb_enabled=False)
        bot_config_repo = _make_bot_config_repo()

        await handle_kb_toggle(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "kb_enabled", True)


class TestHandleKbOrganizerRemove:
    @pytest.mark.asyncio
    async def test_removes_existing_organizer(self) -> None:
        callback = _make_callback(f"adm_kb_org_rm:ru:{CHAT_ID}:{ORG_USER_ID}")
        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID, 777])
        bot_config_repo = _make_bot_config_repo()

        await handle_kb_organizer_remove(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_awaited_once_with(
            CHAT_ID, "kb_organizer_ids", json.dumps([777])
        )

    @pytest.mark.asyncio
    async def test_noop_when_organizer_not_present(self) -> None:
        callback = _make_callback(f"adm_kb_org_rm:ru:{CHAT_ID}:999999")
        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID])
        bot_config_repo = _make_bot_config_repo()

        await handle_kb_organizer_remove(callback, chat_settings_repo, bot_config_repo)

        chat_settings_repo.set_field.assert_not_awaited()


class TestHandleKbOrganizerAddReply:
    @pytest.mark.asyncio
    async def test_adds_forwarded_user(self) -> None:
        state = MagicMock()
        state.get_data = AsyncMock(return_value={"kb_chat_id": CHAT_ID, "kb_lang": "ru"})
        state.clear = AsyncMock()

        message = MagicMock()
        message.from_user = MagicMock(id=ADMIN_ID)
        message.forward_from = MagicMock(id=888, username="newbie", first_name="New")
        message.reply = AsyncMock()

        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID])
        bot_config_repo = _make_bot_config_repo()

        await handle_kb_organizer_add_reply(message, chat_settings_repo, bot_config_repo, state)

        chat_settings_repo.set_field.assert_awaited_once_with(
            CHAT_ID, "kb_organizer_ids", json.dumps([ORG_USER_ID, 888])
        )
        message.reply.assert_awaited_once()
        assert "@newbie" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_not_found_when_no_forward(self) -> None:
        state = MagicMock()
        state.get_data = AsyncMock(return_value={"kb_chat_id": CHAT_ID, "kb_lang": "ru"})
        state.clear = AsyncMock()

        message = MagicMock()
        message.from_user = MagicMock(id=ADMIN_ID)
        message.forward_from = None
        message.reply = AsyncMock()

        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_kb_organizer_add_reply(message, chat_settings_repo, bot_config_repo, state)

        chat_settings_repo.set_field.assert_not_awaited()
        message.reply.assert_awaited_once()
        assert "Не нашёл" in message.reply.call_args[0][0]
