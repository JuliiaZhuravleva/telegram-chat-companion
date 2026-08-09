"""Tests for the chat settings panel sub-router (B-1, ADR-0006; grouped
navigation, B-2, ADR-0010)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from src.bot.handlers.admin_chat_panel import (
    handle_chat_panel_group,
    handle_chat_panel_menu,
    handle_chat_panel_picker,
    handle_chat_panel_shortcut,
    handle_chat_panel_toggle,
    handle_chat_panel_tolerance_cancel,
    handle_chat_panel_tolerance_input,
    handle_chat_panel_tolerance_prompt,
    render_chat_panel,
    render_chat_panel_group,
)
from src.bot.settings_fields import FieldGroup
from src.models.chat_config import ChatConfig

ADMIN_ID = 111
CHAT_ID = -1001234567890


def _make_callback(data: str, user_id: int = ADMIN_ID, chat_type: str = "private") -> MagicMock:
    callback = MagicMock()
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


def _make_bot_config_repo(admin_ids: list[int] | None = None, **defaults: object) -> MagicMock:
    repo = MagicMock()

    async def _get(key: str):
        if key == "admin_ids":
            return admin_ids or [ADMIN_ID]
        return defaults.get(key)

    repo.get = AsyncMock(side_effect=_get)
    return repo


def _make_chat_settings_repo(
    row: dict[str, object] | None, toggle_result: bool | None = False
) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=row)
    repo.set_field = AsyncMock()
    # Mirrors the repo contract: returns the value now stored, or None when no
    # row matched. Default False matches the rag_enabled flip the tests below use.
    repo.toggle_bool_field = AsyncMock(return_value=toggle_result)
    return repo


def _make_chat_config_service(config: ChatConfig) -> MagicMock:
    service = MagicMock()
    service.get_config = AsyncMock(return_value=config)
    service.invalidate = MagicMock()
    return service


def _make_admin_repo(chats: list[dict[str, object]] | None = None, total: int = 0) -> MagicMock:
    repo = MagicMock()
    # C-1: the picker uses its own activity-sorted method, not the
    # title-sorted one shared by KB/Reactions/whitelist.
    repo.get_enabled_chats_page_by_activity = AsyncMock(return_value=(chats or [], total))
    return repo


def _base_config(**overrides: object) -> ChatConfig:
    return replace(ChatConfig(chat_id=CHAT_ID), **overrides)


class TestRenderChatPanel:
    @pytest.mark.asyncio
    async def test_uses_chat_title_in_header(self) -> None:
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "My <Chat>"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        text, _ = await render_chat_panel(
            chat_settings_repo, bot_config_repo, chat_config_service, "ru", CHAT_ID
        )

        # HTML parse_mode is the bot-wide default (CLAUDE.md gotcha) -- the
        # title must be escaped.
        assert "My &lt;Chat&gt;" in text
        assert str(CHAT_ID) in text

    @pytest.mark.asyncio
    async def test_falls_back_to_chat_id_when_no_title(self) -> None:
        chat_settings_repo = _make_chat_settings_repo(None)
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        text, _ = await render_chat_panel(
            chat_settings_repo, bot_config_repo, chat_config_service, "ru", CHAT_ID
        )

        assert str(CHAT_ID) in text

    @pytest.mark.asyncio
    async def test_kb_reactions_status_is_fresh_not_cached(self) -> None:
        """Regression: KB/Reactions status must come from a direct read, not
        ChatConfigService's cache, even though their toggle handlers
        (admin_kb.py/admin_reactions.py) now self-invalidate on write (E-1)
        -- the direct read is defense in depth, not a requirement lifted by
        E-1, and this render path must not silently start trusting the
        cache again.
        """
        row = {
            "chat_title": "Chat",
            "kb_enabled": None,  # inherits from bot_config default below
            "reactions_enabled": True,  # explicit override
            "reactions_history_enabled": None,  # inherits, default False
        }
        chat_settings_repo = _make_chat_settings_repo(row)
        bot_config_repo = _make_bot_config_repo(
            default_kb_enabled=True,
            default_reactions_history_enabled=False,
        )
        # Deliberately stale/contradictory cached config: cache says the
        # opposite of what the fresh read must produce.
        stale_config = _base_config(
            kb_enabled=False,
            reactions_enabled=False,
            reactions_history_enabled=True,
        )
        chat_config_service = _make_chat_config_service(stale_config)

        _, keyboard = await render_chat_panel(
            chat_settings_repo, bot_config_repo, chat_config_service, "ru", CHAT_ID
        )

        kb_btn = next(
            btn
            for row_ in keyboard.inline_keyboard
            for btn in row_
            if btn.callback_data == f"adm_kb_menu:ru:{CHAT_ID}:p"
        )
        assert "✅" in kb_btn.text  # fresh (True), not the stale cached False

        react_btn = next(
            btn
            for row_ in keyboard.inline_keyboard
            for btn in row_
            if btn.callback_data == f"adm_react_menu:ru:{CHAT_ID}:p"
        )
        # reactions_enabled fresh=True, reactions_history_enabled fresh=False
        assert react_btn.text.count("✅") == 1
        assert react_btn.text.count("⚫") == 1

    @pytest.mark.asyncio
    async def test_root_has_no_individual_field_rows(self) -> None:
        """B-2/ADR-0010: the root screen is the section list now -- field
        rows (and their inherited marker) live on the group screen instead;
        see TestRenderChatPanelGroup below."""
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        _, keyboard = await render_chat_panel(
            chat_settings_repo, bot_config_repo, chat_config_service, "ru", CHAT_ID
        )

        callbacks = [btn.callback_data for row_ in keyboard.inline_keyboard for btn in row_]
        assert not any(cb.startswith("adm_pnl_tgl:") for cb in callbacks)
        assert f"adm_pnl_grp:ru:{CHAT_ID}:modules" in callbacks


class TestRenderChatPanelGroup:
    """ADR-0010 Decision 4: one screen per field-owning group."""

    @pytest.mark.asyncio
    async def test_breadcrumb_shows_group_label(self) -> None:
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        text, _ = await render_chat_panel_group(
            chat_settings_repo,
            bot_config_repo,
            chat_config_service,
            "ru",
            CHAT_ID,
            FieldGroup.STICKERS,
        )

        assert "›" in text
        assert "Стикеры" in text

    @pytest.mark.asyncio
    async def test_inherited_marker_threaded_from_raw_row(self) -> None:
        """B-2: render_chat_panel_group must pass the raw row through to the
        keyboard, not just the effective config -- a new field whose raw
        column is NULL gets the marker even though the effective value
        (from the global default) is a concrete bool.
        """
        row = {
            "chat_title": "Chat",
            "link_comments_enabled": None,  # inherited
            "relevancy_gate_enabled": False,  # explicit override
        }
        chat_settings_repo = _make_chat_settings_repo(row)
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(
            _base_config(link_comments_enabled=True, relevancy_gate_enabled=False)
        )

        _, keyboard = await render_chat_panel_group(
            chat_settings_repo,
            bot_config_repo,
            chat_config_service,
            "ru",
            CHAT_ID,
            FieldGroup.MODULES,
        )

        lc_btn = next(
            btn
            for row_ in keyboard.inline_keyboard
            for btn in row_
            if btn.callback_data == f"adm_pnl_tgl:ru:{CHAT_ID}:lc"
        )
        assert "унаследовано" in lc_btn.text

        rg_btn = next(
            btn
            for row_ in keyboard.inline_keyboard
            for btn in row_
            if btn.callback_data == f"adm_pnl_tgl:ru:{CHAT_ID}:rg"
        )
        assert "унаследовано" not in rg_btn.text

    @pytest.mark.asyncio
    async def test_back_row_returns_to_root_menu(self) -> None:
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        _, keyboard = await render_chat_panel_group(
            chat_settings_repo,
            bot_config_repo,
            chat_config_service,
            "ru",
            CHAT_ID,
            FieldGroup.RULES,
        )

        assert keyboard.inline_keyboard[-1][0].callback_data == f"adm_pnl_menu:ru:{CHAT_ID}"


class TestHandleChatPanelPicker:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback("adm_pnl:ru:0", user_id=999)
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_chat_panel_picker(callback, admin_repo, bot_config_repo)

        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_non_private_chat(self) -> None:
        callback = _make_callback("adm_pnl:ru:0", chat_type="group")
        admin_repo = _make_admin_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_chat_panel_picker(callback, admin_repo, bot_config_repo)

        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_renders_chat_rows(self) -> None:
        callback = _make_callback("adm_pnl:ru:0")
        admin_repo = _make_admin_repo(
            chats=[{"chat_id": CHAT_ID, "chat_title": "My Chat"}], total=1
        )
        bot_config_repo = _make_bot_config_repo()

        await handle_chat_panel_picker(callback, admin_repo, bot_config_repo)

        callback.message.edit_text.assert_awaited_once()
        keyboard = callback.message.edit_text.call_args.kwargs["reply_markup"]
        callbacks = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert f"adm_pnl_menu:ru:{CHAT_ID}" in callbacks


class TestHandleChatPanelMenu:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_pnl_menu:ru:{CHAT_ID}", user_id=999)
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_menu(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_chat_id_shows_alert(self) -> None:
        callback = _make_callback("adm_pnl_menu:ru:notanumber")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_menu(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_renders_panel_with_html_parse_mode(self) -> None:
        callback = _make_callback(f"adm_pnl_menu:ru:{CHAT_ID}")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_menu(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.message.edit_text.assert_awaited_once()
        assert callback.message.edit_text.call_args.kwargs.get("parse_mode") == "HTML"


class TestHandleChatPanelGroup:
    """ADR-0010 Decisions 1, 2, 4: ``adm_pnl_grp:`` opens one group's screen."""

    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_pnl_grp:ru:{CHAT_ID}:modules", user_id=999)
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_group(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_chat_id_shows_alert(self) -> None:
        callback = _make_callback("adm_pnl_grp:ru:notanumber:modules")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_group(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_group_shows_alert(self) -> None:
        callback = _make_callback(f"adm_pnl_grp:ru:{CHAT_ID}:not_a_group")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_group(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        callback.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_renders_group_screen_with_html_parse_mode(self) -> None:
        callback = _make_callback(f"adm_pnl_grp:ru:{CHAT_ID}:stickers")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_group(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.message.edit_text.assert_awaited_once()
        assert callback.message.edit_text.call_args.kwargs.get("parse_mode") == "HTML"
        text = callback.message.edit_text.call_args.args[0]
        assert "Стикеры" in text


class TestHandleChatPanelToggle:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag", user_id=999)
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        chat_settings_repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flips_bool_field_and_invalidates_cache(self) -> None:
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config(rag_enabled=True))

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        # The flip is delegated to SQL (atomic); the handler only supplies the
        # effective starting value, and never computes the new one itself.
        chat_settings_repo.toggle_bool_field.assert_awaited_once_with(CHAT_ID, "rag_enabled", True)
        chat_settings_repo.set_field.assert_not_awaited()
        chat_config_service.invalidate.assert_called_once_with(CHAT_ID)

    @pytest.mark.asyncio
    async def test_inherited_null_column_flips_from_effective_value(self) -> None:
        """A NULL column means "inherited": the flip must start from what the
        chat actually behaves like, not from SQL NULL."""
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:lc")  # link_comments_enabled
        chat_settings_repo = _make_chat_settings_repo(
            {"chat_title": "Chat", "link_comments_enabled": None}, toggle_result=True
        )
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config(link_comments_enabled=False))

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        chat_settings_repo.toggle_bool_field.assert_awaited_once_with(
            CHAT_ID, "link_comments_enabled", False
        )

    @pytest.mark.asyncio
    async def test_missing_row_reports_failure_instead_of_success_toast(self) -> None:
        """toggle_bool_field returns None when no row matched. The admin must be
        told nothing changed rather than shown an 'Enabled'/'Disabled' toast."""
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"}, toggle_result=None)
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config(rag_enabled=True))

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        chat_config_service.invalidate.assert_not_called()

    @pytest.mark.parametrize("code", ["kb", "rx", "rh"])
    @pytest.mark.asyncio
    async def test_rejects_kb_reactions_link_only_codes(self, code: str) -> None:
        """Decision 2: KB/Reactions fields must never be reachable through the
        generic toggle path, even though the A-1 registry marks them BOOL."""
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:{code}")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        chat_settings_repo.set_field.assert_not_awaited()
        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_rejects_non_bool_field_code(self) -> None:
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:sp")  # system_prompt, STR
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        chat_settings_repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_unknown_code(self) -> None:
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:zzzz")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        chat_settings_repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_re_renders_panel_after_toggle(self) -> None:
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config(rag_enabled=False))

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        callback.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_re_renders_the_fields_own_group_not_root(self) -> None:
        """ADR-0010 Decision 5: a toggle inside MODULES re-renders that group
        screen, not the root section list -- "predictable return"."""
        callback = _make_callback(f"adm_pnl_tgl:ru:{CHAT_ID}:rag")  # rag_enabled, MODULES
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config(rag_enabled=False))

        await handle_chat_panel_toggle(
            callback, chat_settings_repo, bot_config_repo, chat_config_service
        )

        keyboard = callback.message.edit_text.call_args.kwargs["reply_markup"]
        # The MODULES group screen's back row points to root, not the picker
        # (which is where a root re-render's own back row would point).
        assert keyboard.inline_keyboard[-1][0].callback_data == f"adm_pnl_menu:ru:{CHAT_ID}"
        text = callback.message.edit_text.call_args.args[0]
        assert "Модули" in text


def _make_state(data: dict[str, object] | None = None) -> MagicMock:
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value=data or {})
    state.clear = AsyncMock()
    return state


def _make_message(text: str, user_id: int = ADMIN_ID) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.reply = AsyncMock()
    message.answer = AsyncMock()
    return message


def _make_shortcut_message(
    text: str, user_id: int = ADMIN_ID, chat_type: str = "private"
) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.chat = MagicMock()
    message.chat.type = chat_type
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.reply = AsyncMock()
    message.answer = AsyncMock()
    return message


def _make_shortcut_admin_repo(
    lang: str = "ru", matches: list[dict[str, object]] | None = None
) -> MagicMock:
    repo = MagicMock()
    repo.get_admin_language = AsyncMock(return_value=lang)
    repo.find_enabled_chats_by_title = AsyncMock(return_value=matches or [])
    return repo


class TestHandleChatPanelShortcut:
    """D-1: ``/panel <query>`` shortcut -- open a chat's panel by link/title,
    skipping the picker."""

    @pytest.mark.asyncio
    async def test_no_query_shows_usage(self) -> None:
        message = _make_shortcut_message("/panel")
        admin_repo = _make_shortcut_admin_repo()

        await handle_chat_panel_shortcut(
            message,
            MagicMock(),
            admin_repo,
            _make_chat_settings_repo(None),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        message.reply.assert_awaited_once()
        message.answer.assert_not_awaited()
        admin_repo.find_enabled_chats_by_title.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_c_link_resolves_to_whitelisted_chat_opens_panel(self) -> None:
        message = _make_shortcut_message("/panel https://t.me/c/1234567890")
        admin_repo = _make_shortcut_admin_repo()
        row = {"chat_id": -1001234567890, "chat_title": "Target", "enabled": True}

        await handle_chat_panel_shortcut(
            message,
            MagicMock(),
            admin_repo,
            _make_chat_settings_repo(row),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        message.answer.assert_awaited_once()
        assert "Target" in message.answer.call_args.args[0]
        message.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_link_resolves_but_not_whitelisted_reports_not_found(self) -> None:
        message = _make_shortcut_message("/panel https://t.me/c/1234567890")
        admin_repo = _make_shortcut_admin_repo()
        row = {"chat_id": -1001234567890, "chat_title": "Target", "enabled": False}

        await handle_chat_panel_shortcut(
            message,
            MagicMock(),
            admin_repo,
            _make_chat_settings_repo(row),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        message.reply.assert_awaited_once()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_username_link_resolved_via_bot_get_chat(self) -> None:
        message = _make_shortcut_message("/panel @mychat")
        admin_repo = _make_shortcut_admin_repo()
        row = {"chat_id": -555, "chat_title": "MyChat", "enabled": True}
        bot = MagicMock()
        resolved = MagicMock()
        resolved.id = -555
        bot.get_chat = AsyncMock(return_value=resolved)

        await handle_chat_panel_shortcut(
            message,
            bot,
            admin_repo,
            _make_chat_settings_repo(row),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        bot.get_chat.assert_awaited_once_with("@mychat")
        message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_username_lookup_failure_reports_not_found(self) -> None:
        message = _make_shortcut_message("/panel @ghost")
        admin_repo = _make_shortcut_admin_repo()
        bot = MagicMock()
        bot.get_chat = AsyncMock(side_effect=Exception("chat not found"))

        await handle_chat_panel_shortcut(
            message,
            bot,
            admin_repo,
            _make_chat_settings_repo(None),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        message.reply.assert_awaited_once()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_title_search_single_match_opens_panel_directly(self) -> None:
        message = _make_shortcut_message("/panel foo")
        matches = [{"chat_id": -777, "chat_title": "Foobar", "chat_type": "group"}]
        admin_repo = _make_shortcut_admin_repo(matches=matches)

        await handle_chat_panel_shortcut(
            message,
            MagicMock(),
            admin_repo,
            _make_chat_settings_repo({"chat_id": -777, "chat_title": "Foobar"}),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        message.answer.assert_awaited_once()
        message.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_title_search_multiple_matches_shows_candidates(self) -> None:
        message = _make_shortcut_message("/panel foo")
        matches = [
            {"chat_id": -1, "chat_title": "Foo One", "chat_type": "group"},
            {"chat_id": -2, "chat_title": "Foo Two", "chat_type": "group"},
        ]
        admin_repo = _make_shortcut_admin_repo(matches=matches)

        await handle_chat_panel_shortcut(
            message,
            MagicMock(),
            admin_repo,
            _make_chat_settings_repo(None),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        message.answer.assert_awaited_once()
        keyboard = message.answer.call_args.kwargs["reply_markup"]
        assert len(keyboard.inline_keyboard) == 2

    @pytest.mark.asyncio
    async def test_title_search_no_matches_reports_not_found(self) -> None:
        message = _make_shortcut_message("/panel nonexistent")
        admin_repo = _make_shortcut_admin_repo(matches=[])

        await handle_chat_panel_shortcut(
            message,
            MagicMock(),
            admin_repo,
            _make_chat_settings_repo(None),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        message.reply.assert_awaited_once()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_chat_is_ignored(self) -> None:
        message = _make_shortcut_message("/panel foo", chat_type="group")
        admin_repo = _make_shortcut_admin_repo()

        await handle_chat_panel_shortcut(
            message,
            MagicMock(),
            admin_repo,
            _make_chat_settings_repo(None),
            _make_bot_config_repo(),
            _make_chat_config_service(_base_config()),
        )

        message.answer.assert_not_awaited()
        message.reply.assert_not_awaited()
        admin_repo.get_admin_language.assert_not_awaited()


class TestHandleChatPanelTolerancePrompt:
    @pytest.mark.asyncio
    async def test_sets_state_and_prompts(self) -> None:
        callback = _make_callback(f"adm_pnl_tol:ru:{CHAT_ID}")
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_chat_panel_tolerance_prompt(callback, bot_config_repo, state)

        state.set_state.assert_awaited_once()
        state.update_data.assert_awaited_once_with(tol_chat_id=CHAT_ID, tol_lang="ru")
        callback.message.answer.assert_awaited_once()
        # The prompt must carry the cancel button — without it the FSM state
        # has no exit (invalid input deliberately re-prompts, 2026-08-07 review).
        assert callback.message.answer.call_args.kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_pnl_tol:ru:{CHAT_ID}", user_id=999)
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_chat_panel_tolerance_prompt(callback, bot_config_repo, state)

        state.set_state.assert_not_awaited()


class TestHandleChatPanelToleranceInput:
    @pytest.mark.asyncio
    async def test_valid_value_writes_and_clears_state(self) -> None:
        message = _make_message("0.8")
        state = _make_state({"tol_chat_id": CHAT_ID, "tol_lang": "ru"})
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_tolerance_input(
            message, state, chat_settings_repo, bot_config_repo, chat_config_service
        )

        state.clear.assert_awaited_once()
        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "tolerance_level", 0.8)
        chat_config_service.invalidate.assert_called_once_with(CHAT_ID)
        message.answer.assert_awaited_once()
        # ADR-0010 Decision 5: re-renders the STICKERS group screen, not root.
        keyboard = message.answer.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[-1][0].callback_data == f"adm_pnl_menu:ru:{CHAT_ID}"
        text = message.answer.call_args.args[0]
        assert "Стикеры" in text

    @pytest.mark.asyncio
    async def test_out_of_range_reprompts_without_writing(self) -> None:
        message = _make_message("1.5")
        state = _make_state({"tol_chat_id": CHAT_ID, "tol_lang": "ru"})
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_tolerance_input(
            message, state, chat_settings_repo, bot_config_repo, chat_config_service
        )

        state.clear.assert_not_awaited()
        chat_settings_repo.set_field.assert_not_awaited()
        message.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_numeric_reprompts_without_writing(self) -> None:
        message = _make_message("not a number")
        state = _make_state({"tol_chat_id": CHAT_ID, "tol_lang": "ru"})
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_tolerance_input(
            message, state, chat_settings_repo, bot_config_repo, chat_config_service
        )

        state.clear.assert_not_awaited()
        chat_settings_repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_input_reprompt_carries_cancel_button(self) -> None:
        """The re-prompt must keep the escape hatch visible — the state stays
        set on invalid input by design (reject-not-clamp), so every re-prompt
        without a cancel button is another turn of the trap (2026-08-07)."""
        message = _make_message("not a number")
        state = _make_state({"tol_chat_id": CHAT_ID, "tol_lang": "ru"})
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_tolerance_input(
            message, state, chat_settings_repo, bot_config_repo, chat_config_service
        )

        assert message.reply.call_args.kwargs.get("reply_markup") is not None


class TestHandleChatPanelToleranceCancel:
    @pytest.mark.asyncio
    async def test_clears_state_and_rerenders_panel(self) -> None:
        callback = _make_callback(f"adm_pnl_tolcancel:ru:{CHAT_ID}")
        state = _make_state({"tol_chat_id": CHAT_ID, "tol_lang": "ru"})
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_tolerance_cancel(
            callback, state, chat_settings_repo, bot_config_repo, chat_config_service
        )

        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once()
        # ADR-0010 Decision 5: re-renders the STICKERS group screen, not root.
        keyboard = callback.message.edit_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[-1][0].callback_data == f"adm_pnl_menu:ru:{CHAT_ID}"

    @pytest.mark.asyncio
    async def test_non_admin_cannot_cancel(self) -> None:
        callback = _make_callback(f"adm_pnl_tolcancel:ru:{CHAT_ID}", user_id=999)
        state = _make_state()
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_tolerance_cancel(
            callback, state, chat_settings_repo, bot_config_repo, chat_config_service
        )

        state.clear.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_malformed_chat_id_still_clears_state(self) -> None:
        """Escaping the trap must not depend on well-formed callback data —
        clearing the state is the one thing this handler may never skip."""
        callback = _make_callback("adm_pnl_tolcancel:ru:not-an-int")
        state = _make_state()
        chat_settings_repo = _make_chat_settings_repo({"chat_title": "Chat"})
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service(_base_config())

        await handle_chat_panel_tolerance_cancel(
            callback, state, chat_settings_repo, bot_config_repo, chat_config_service
        )

        state.clear.assert_awaited_once()
