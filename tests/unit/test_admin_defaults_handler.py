"""Tests for the settings of every chat: ``defs`` and ``defs.section`` (C-1, ADR-0006).

Who may press is the filters' job; ``test_filters_need_an_admin_in_the_private_chat``
asserts the registration instead of calling a handler as a stranger.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from src.bot.filters.admin import IsAdmin
from src.bot.handlers import admin_defaults as D
from src.bot.handlers.admin_defaults import (
    handle_defaults_menu,
    handle_defaults_section,
    handle_defaults_toggle,
)
from src.bot.screens.sections import SECTIONS
from src.bot.screens.settings import SECTION_TOGGLES
from src.bot.settings_fields import FieldType, new_fields

ADMIN_ID = 555000111


def _make_callback(data: str) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = ADMIN_ID
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = "private"
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    return callback


def _make_bot_config_repo(defaults: dict[str, object] | None = None) -> MagicMock:
    repo = MagicMock()
    repo.get_defaults = AsyncMock(return_value=defaults or {})
    repo.set = AsyncMock()
    return repo


def _make_chat_config_service() -> MagicMock:
    service = MagicMock()
    service.invalidate_all = MagicMock()
    service.invalidate = MagicMock()
    return service


def _buttons(callback: MagicMock) -> dict[str, str]:
    """payload -> label of the screen edited into the message."""
    callback.message.edit_text.assert_awaited_once()
    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    return {b.callback_data: b.text for row in markup.inline_keyboard for b in row}


def _probe(chat_type: str) -> SimpleNamespace:
    chat = SimpleNamespace(type=chat_type)
    return SimpleNamespace(data="", chat=chat, message=SimpleNamespace(chat=chat))


class TestRegistration:
    def test_filters_need_an_admin_in_the_private_chat(self):
        handlers = D.router.observers["callback_query"].handlers
        assert len(handlers) == 3  # positive control: the walk sees all three
        for handler in handlers:
            name = handler.callback.__name__
            assert any(isinstance(f.callback, IsAdmin) for f in handler.filters), name
            assert any(
                f.magic is not None
                and f.callback(_probe("private"))
                and not f.callback(_probe("group"))
                for f in handler.filters
            ), name

    @pytest.mark.parametrize(
        ("payload", "handler"),
        [
            ("adm_defs:ru:0", "admin_defaults.handle_defaults_menu"),
            ("adm_defs_sec:ru:memory", "admin_defaults.handle_defaults_section"),
            ("adm_defs_tgl:ru:kb", "admin_defaults.handle_defaults_toggle"),
        ],
    )
    def test_payload_reaches_its_handler(self, payload: str, handler: str):
        """``adm_defs:`` is a prefix of neither sibling, so each lands on its own handler."""
        from src.bot.handlers import router
        from src.bot.screens.lint import first_handler

        assert first_handler(router, payload) == handler

    def test_every_yes_no_field_has_exactly_one_toggle(self):
        codes = [code for rows in SECTION_TOGGLES.values() for row in rows for code in row]
        bools = {f.code for f in new_fields() if f.type is FieldType.BOOL and not f.legacy}
        assert len(codes) == len(set(codes))
        assert set(codes) == bools
        assert set(SECTION_TOGGLES) == set(SECTIONS)


class TestMenu:
    @pytest.mark.asyncio
    async def test_seven_sections_and_back_to_settings(self):
        callback = _make_callback("adm_defs:ru:0")

        await handle_defaults_menu(callback)

        payloads = list(_buttons(callback))
        assert payloads == [*(f"adm_defs_sec:ru:{s}" for s in SECTIONS), "adm_settings:ru"]
        assert len(SECTIONS) == 7
        assert "Для всех чатов" in callback.message.edit_text.call_args.args[0]


class TestSection:
    @pytest.mark.asyncio
    async def test_falls_back_to_chatconfig_defaults_when_no_override(self):
        callback = _make_callback("adm_defs_sec:ru:memory")

        await handle_defaults_section(callback, _make_bot_config_repo())

        # ChatConfig.kb_enabled defaults to False -- no bot_config override set.
        assert _buttons(callback)["adm_defs_tgl:ru:kb"].startswith("⚪")

    @pytest.mark.asyncio
    async def test_uses_explicit_default_override(self):
        callback = _make_callback("adm_defs_sec:ru:memory")

        await handle_defaults_section(callback, _make_bot_config_repo({"kb_enabled": True}))

        buttons = _buttons(callback)
        assert buttons["adm_defs_tgl:ru:kb"].startswith("✅")
        assert list(buttons)[-1] == "adm_defs:ru:0"

    @pytest.mark.asyncio
    async def test_values_that_are_not_yes_no_are_lines_of_text(self):
        callback = _make_callback("adm_defs_sec:en:rules")

        await handle_defaults_section(
            callback, _make_bot_config_repo({"rules_mode": "highest_weight"})
        )

        assert "Mode: one rule, highest weight" in callback.message.edit_text.call_args.args[0]
        assert list(_buttons(callback)) == ["adm_defs_tgl:en:re", "adm_defs:en:0"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", ["adm_defs_sec:ru:nowhere", "adm_defs_sec:ru"])
    async def test_unknown_section_is_stale(self, payload: str):
        callback = _make_callback(payload)
        repo = _make_bot_config_repo()

        with patch.object(D, "show_stale", new=AsyncMock()) as stale:
            await handle_defaults_section(callback, repo, extra=1)

        stale.assert_awaited_once_with(callback, "ru", data={"extra": 1})
        repo.get_defaults.assert_not_awaited()


class TestToggle:
    @pytest.mark.asyncio
    async def test_flips_bool_field_and_invalidates_all_not_per_chat(self):
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
    async def test_shows_its_section_again_with_a_toast(self):
        callback = _make_callback("adm_defs_tgl:ru:kb")
        bot_config_repo = _make_bot_config_repo(defaults={"kb_enabled": False})

        await handle_defaults_toggle(callback, bot_config_repo, _make_chat_config_service())

        buttons = _buttons(callback)
        assert buttons["adm_defs_tgl:ru:kb"].startswith("✅")
        assert "adm_defs_tgl:ru:ch" in buttons  # the memory section, not another one
        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.args[0] == "✓ Включено для всех чатов без своего значения"

    @pytest.mark.asyncio
    async def test_switching_off_says_so(self):
        callback = _make_callback("adm_defs_tgl:en:kb")
        bot_config_repo = _make_bot_config_repo(defaults={"kb_enabled": True})

        await handle_defaults_toggle(callback, bot_config_repo, _make_chat_config_service())

        bot_config_repo.set.assert_awaited_once_with("default_kb_enabled", False)
        assert callback.answer.call_args.args[0] == (
            "✓ Disabled for every chat without its own value"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            # rag_enabled is a legacy (SQL-DEFAULT) field -- excluded from the
            # defaults screen entirely (C-2 deferred), even though it is an
            # ordinary FieldType.BOOL entry in the shared registry.
            "adm_defs_tgl:ru:rag",
            "adm_defs_tgl:ru:rm",  # rules_mode, not a yes/no
            "adm_defs_tgl:ru:zzzz",
            "adm_defs_tgl:ru",
        ],
    )
    async def test_a_code_it_cannot_flip_is_stale_and_writes_nothing(self, payload: str):
        callback = _make_callback(payload)
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service()

        with patch.object(D, "show_stale", new=AsyncMock()) as stale:
            await handle_defaults_toggle(callback, bot_config_repo, chat_config_service)

        stale.assert_awaited_once_with(callback, "ru", data={})
        bot_config_repo.set.assert_not_awaited()
        chat_config_service.invalidate_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_without_a_container_still_answers(self):
        """The integration test calls the handler positionally with no data; a stale
        payload must still reach the user rather than raise."""
        callback = _make_callback("adm_defs_tgl:ru:zzzz")

        await handle_defaults_toggle(callback, _make_bot_config_repo(), _make_chat_config_service())

        callback.answer.assert_awaited_once()
        callback.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_values_cover_every_field(self):
        """Missing defaults fall back to the ChatConfig layer for every field."""
        values = await D._resolve_values(_make_bot_config_repo())

        assert set(values) == {f.key for f in new_fields()}
