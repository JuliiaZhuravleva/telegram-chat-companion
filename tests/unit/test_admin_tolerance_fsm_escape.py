"""Router-level proof of the tolerance-FSM escape hatches (2026-08-07 review).

The trap lived in FILTERS and ROUTER ORDER, which direct handler calls cannot
exercise: ``admin_chat_panel_router`` precedes ``commands_router``, so while
``AdminStates.awaiting_setting_value`` was active the input handler consumed
EVERY private message — ``/admin``, ``/help``, everything — and re-prompted
«Нужно число…» until a valid float arrived, with no cancel path anywhere.
Separately, ``admin_sticker``'s reply handler (an even earlier router)
swallowed the value when the admin sent it as a *reply* to the prompt.

These tests drive the REAL ``main_router`` via ``propagate_event`` — the same
technique as test_admin_sticker_dm_router_order.py (see its DI note: mocks
ride the ``data`` dict under exact parameter names; ``IsAdmin()`` uses its
documented ``is_admin`` fast-path). FSM state is injected via ``raw_state``,
exactly what aiogram's FSMContextMiddleware supplies at runtime.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED

from src.bot.handlers import router as main_router
from src.bot.handlers.admin_chat_panel import router as panel_router
from src.bot.states.admin import AdminStates
from src.models.chat_config import ChatConfig

ADMIN_ID = 555
CHAT_ID = -1001234567890
RAW_STATE = AdminStates.awaiting_setting_value.state


def _make_fsm_message(
    text: str, *, reply_to: bool = False
) -> tuple[MagicMock, MagicMock, dict[str, object]]:
    """A private admin text message + data dict, with the tolerance FSM state
    active. Sets every filter-relevant attribute for the routers between
    admin_sticker and admin_chat_panel (see the router-order file's docstring
    for why unset MagicMock attributes are a trap here)."""
    msg = MagicMock()
    msg.text = text
    msg.caption = None
    msg.sticker = None
    msg.voice = None
    msg.video_note = None
    msg.photo = None
    msg.reply_to_message = MagicMock() if reply_to else None
    msg.message_id = 1
    msg.chat = MagicMock()
    msg.chat.id = ADMIN_ID
    msg.chat.type = "private"
    msg.from_user = MagicMock()
    msg.from_user.id = ADMIN_ID
    msg.reply = AsyncMock()
    msg.answer = AsyncMock()

    state = MagicMock()
    state.get_data = AsyncMock(return_value={"tol_chat_id": CHAT_ID, "tol_lang": "ru"})
    state.clear = AsyncMock()

    chat_settings_repo = MagicMock()
    chat_settings_repo.get = AsyncMock(return_value={"chat_title": "Chat"})
    chat_settings_repo.set_field = AsyncMock()

    bot_config_repo = MagicMock()

    async def _get(key: str) -> object:
        if key == "admin_ids":
            return [ADMIN_ID]
        return None

    bot_config_repo.get = AsyncMock(side_effect=_get)

    chat_config_service = MagicMock()
    chat_config_service.get_config = AsyncMock(return_value=replace(ChatConfig(chat_id=CHAT_ID)))
    chat_config_service.invalidate = MagicMock()

    # Mocks for admin_sticker's reply handler so that, if it (wrongly)
    # matches, it fails on assertions rather than on missing kwargs.
    sticker_repo = MagicMock()
    sticker_repo.get_by_file_unique_id = AsyncMock(return_value=None)
    sticker_service = MagicMock()
    sticker_service.merge_admin_description = AsyncMock()

    data: dict[str, object] = {
        "is_admin": True,
        "raw_state": RAW_STATE,
        "state": state,
        "chat_settings_repo": chat_settings_repo,
        "bot_config_repo": bot_config_repo,
        "chat_config_service": chat_config_service,
        "sticker_repo": sticker_repo,
        "sticker_service": sticker_service,
        "admin_repo": MagicMock(),
        "bot": MagicMock(),
        "message_thread_id": None,
        "event_from_user": msg.from_user,
    }
    return msg, state, data


class TestToleranceFsmEscape:
    @pytest.mark.asyncio
    async def test_plain_value_is_consumed_by_tolerance_input(self) -> None:
        """Harness control: a plain float in the active state reaches the
        real input handler through the full router chain and is persisted —
        proves the escapes below are measured against a working path, not a
        vacuously silent one."""
        msg, _state, data = _make_fsm_message("0.8")

        await main_router.propagate_event("message", msg, **data)

        chat_settings_repo = data["chat_settings_repo"]
        assert isinstance(chat_settings_repo, MagicMock)
        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "tolerance_level", 0.8)

    @pytest.mark.asyncio
    async def test_command_escapes_the_state(self) -> None:
        """A command sent mid-state must leave the panel router UNHANDLED —
        free to reach commands_router and everything after it. Pre-fix this
        was the trap: the input handler consumed /admin, /help, every
        command, and replied «Нужно число…». Asserted against the panel
        router alone: what unrelated handler ultimately serves an unknown
        command downstream is pre-existing behaviour outside this fix."""
        msg, state, data = _make_fsm_message("/some_command")

        result = await panel_router.propagate_event("message", msg, **data)

        assert result is UNHANDLED
        chat_settings_repo = data["chat_settings_repo"]
        assert isinstance(chat_settings_repo, MagicMock)
        chat_settings_repo.set_field.assert_not_awaited()
        msg.reply.assert_not_awaited()  # no «Нужно число…» re-prompt
        state.clear.assert_not_awaited()  # state intact, dialog resumable

    @pytest.mark.asyncio
    async def test_non_command_invalid_text_is_still_consumed(self) -> None:
        """Anti-vacuity control for the UNHANDLED assertion above: the same
        panel-router harness DOES consume a non-command invalid value (and
        re-prompts) — so UNHANDLED for a command measures the filter, not a
        broken harness."""
        msg, state, data = _make_fsm_message("abc")

        result = await panel_router.propagate_event("message", msg, **data)

        assert result is not UNHANDLED
        msg.reply.assert_awaited_once()
        state.clear.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reply_to_prompt_reaches_tolerance_input(self) -> None:
        """The admin answering the prompt as a *reply* must land in the
        tolerance input handler, not in admin_sticker's reply-to-notification
        handler (an earlier router that matches F.reply_to_message + F.text +
        private + IsAdmin) — that's what StateFilter(None) on the sticker
        handler guarantees."""
        msg, _state, data = _make_fsm_message("0.8", reply_to=True)

        await main_router.propagate_event("message", msg, **data)

        chat_settings_repo = data["chat_settings_repo"]
        sticker_service = data["sticker_service"]
        assert isinstance(chat_settings_repo, MagicMock)
        assert isinstance(sticker_service, MagicMock)
        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "tolerance_level", 0.8)
        sticker_service.merge_admin_description.assert_not_awaited()
