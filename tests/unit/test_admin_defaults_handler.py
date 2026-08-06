"""Tests for the "settings by default" sub-router (C-1, ADR-0006)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from src.bot.handlers.admin_defaults import (
    handle_defaults_menu,
    handle_defaults_toggle,
    render_defaults_panel,
)

ADMIN_ID = 111


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
    return callback


def _make_bot_config_repo(
    admin_ids: list[int] | None = None, defaults: dict[str, object] | None = None
) -> MagicMock:
    repo = MagicMock()

    async def _get(key: str):
        if key == "admin_ids":
            return admin_ids or [ADMIN_ID]
        return None

    repo.get = AsyncMock(side_effect=_get)
    repo.get_defaults = AsyncMock(return_value=defaults or {})
    repo.set = AsyncMock()
    return repo


def _make_chat_config_service() -> MagicMock:
    service = MagicMock()
    service.invalidate_all = MagicMock()
    service.invalidate = MagicMock()
    return service


class TestRenderDefaultsPanel:
    @pytest.mark.asyncio
    async def test_falls_back_to_chatconfig_defaults_when_no_override(self) -> None:
        bot_config_repo = _make_bot_config_repo(defaults={})

        _, keyboard = await render_defaults_panel(bot_config_repo, "ru")

        callbacks = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        assert "adm_defs_tgl:ru:kb" in callbacks
        btn = next(
            b
            for row in keyboard.inline_keyboard
            for b in row
            if b.callback_data == "adm_defs_tgl:ru:kb"
        )
        # ChatConfig.kb_enabled default is False -- no bot_config override set.
        assert "⚫" in btn.text

    @pytest.mark.asyncio
    async def test_uses_explicit_default_override(self) -> None:
        bot_config_repo = _make_bot_config_repo(defaults={"kb_enabled": True})

        _, keyboard = await render_defaults_panel(bot_config_repo, "ru")

        btn = next(
            b
            for row in keyboard.inline_keyboard
            for b in row
            if b.callback_data == "adm_defs_tgl:ru:kb"
        )
        assert "✅" in btn.text


class TestHandleDefaultsMenu:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback("adm_defs:ru:0", user_id=999)
        bot_config_repo = _make_bot_config_repo()

        await handle_defaults_menu(callback, bot_config_repo)

        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_non_private_chat(self) -> None:
        callback = _make_callback("adm_defs:ru:0", chat_type="group")
        bot_config_repo = _make_bot_config_repo()

        await handle_defaults_menu(callback, bot_config_repo)

        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_renders_screen(self) -> None:
        callback = _make_callback("adm_defs:ru:0")
        bot_config_repo = _make_bot_config_repo()

        await handle_defaults_menu(callback, bot_config_repo)

        callback.message.edit_text.assert_awaited_once()


class TestHandleDefaultsToggle:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback("adm_defs_tgl:ru:re", user_id=999)
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service()

        await handle_defaults_toggle(callback, bot_config_repo, chat_config_service)

        bot_config_repo.set.assert_not_awaited()
        chat_config_service.invalidate_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_flips_bool_field_and_invalidates_all_not_per_chat(self) -> None:
        callback = _make_callback("adm_defs_tgl:ru:re")
        bot_config_repo = _make_bot_config_repo(defaults={"rules_enabled": False})
        chat_config_service = _make_chat_config_service()

        await handle_defaults_toggle(callback, bot_config_repo, chat_config_service)

        bot_config_repo.set.assert_awaited_once_with("default_rules_enabled", True)
        # Must invalidate the shared global-defaults cache, never a per-chat
        # entry -- ADR-0006's C-1 consequence to Decision 2.
        chat_config_service.invalidate_all.assert_called_once_with()
        chat_config_service.invalidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_legacy_field_code(self) -> None:
        """rag_enabled is a legacy (SQL-DEFAULT) field -- excluded from the
        defaults screen entirely (C-2 deferred), even though it's an
        ordinary FieldType.BOOL entry in the shared A-1 registry."""
        callback = _make_callback("adm_defs_tgl:ru:rag")
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service()

        await handle_defaults_toggle(callback, bot_config_repo, chat_config_service)

        bot_config_repo.set.assert_not_awaited()
        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_rejects_non_bool_field_code(self) -> None:
        callback = _make_callback("adm_defs_tgl:ru:rm")  # rules_mode, STR
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service()

        await handle_defaults_toggle(callback, bot_config_repo, chat_config_service)

        bot_config_repo.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_unknown_code(self) -> None:
        callback = _make_callback("adm_defs_tgl:ru:zzzz")
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service()

        await handle_defaults_toggle(callback, bot_config_repo, chat_config_service)

        bot_config_repo.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_re_renders_after_toggle(self) -> None:
        callback = _make_callback("adm_defs_tgl:ru:re")
        bot_config_repo = _make_bot_config_repo(defaults={"rules_enabled": False})
        chat_config_service = _make_chat_config_service()

        await handle_defaults_toggle(callback, bot_config_repo, chat_config_service)

        callback.message.edit_text.assert_awaited_once()
