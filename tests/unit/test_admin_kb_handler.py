"""Tests for the Knowledge Base admin sub-router (A4)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import (
    Chat,
    Message,
    MessageOriginChannel,
    MessageOriginHiddenUser,
    MessageOriginUser,
    User,
)

from src.bot.handlers.admin_kb import (
    _extract_username,
    handle_kb_menu,
    handle_kb_organizer_add_prompt,
    handle_kb_organizer_add_reply,
    handle_kb_organizer_list,
    handle_kb_organizer_pick,
    handle_kb_organizer_remove,
    handle_kb_toggle,
)
from src.bot.keyboards.admin_chat_panel import chat_panel_root_keyboard
from src.models.chat_config import ChatConfig

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


def _make_bot_config_repo(
    admin_ids: list[int] | None = None, default_kb_enabled: bool | None = None
) -> MagicMock:
    repo = MagicMock()

    # BotConfigRepository.get() already returns json.loads() output (a parsed
    # list/bool), not a raw JSON string -- see src/utils/parse_admin_ids docstring.
    # Key-aware: _effective_kb_enabled() queries "default_kb_enabled" too.
    async def _get(key: str):
        if key == "admin_ids":
            return admin_ids or [ADMIN_ID]
        if key == "default_kb_enabled":
            return default_kb_enabled
        return None

    repo.get = AsyncMock(side_effect=_get)
    return repo


def _make_chat_settings_repo(
    kb_enabled: bool | None = False, organizer_ids: list[int] | None = None
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


def _make_chat_config_service() -> MagicMock:
    """``ChatConfigService`` mock -- ``invalidate`` is sync, not a coroutine."""
    service = MagicMock()
    service.invalidate = MagicMock()
    return service


class TestExtractUsername:
    def test_strips_leading_at(self) -> None:
        assert _extract_username("@newbie") == "newbie"

    def test_accepts_bare_username(self) -> None:
        assert _extract_username("newbie") == "newbie"

    def test_strips_surrounding_whitespace(self) -> None:
        assert _extract_username("  @newbie  ") == "newbie"

    def test_none_when_no_text(self) -> None:
        assert _extract_username(None) is None

    def test_none_when_too_short(self) -> None:
        assert _extract_username("@abc") is None

    def test_none_when_too_long(self) -> None:
        assert _extract_username("@" + "a" * 33) is None

    def test_none_when_not_a_single_token(self) -> None:
        assert _extract_username("please add @newbie") is None

    def test_none_when_invalid_characters(self) -> None:
        assert _extract_username("@new-bie!") is None


class TestHandleKbToggle:
    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_kb_toggle:ru:{CHAT_ID}", user_id=999)
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service()

        await handle_kb_toggle(callback, chat_settings_repo, bot_config_repo, chat_config_service)

        chat_settings_repo.set_field.assert_not_awaited()
        chat_config_service.invalidate.assert_not_called()
        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_flips_disabled_to_enabled(self) -> None:
        callback = _make_callback(f"adm_kb_toggle:ru:{CHAT_ID}")
        chat_settings_repo = _make_chat_settings_repo(kb_enabled=False)
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service()

        await handle_kb_toggle(callback, chat_settings_repo, bot_config_repo, chat_config_service)

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "kb_enabled", True)

    @pytest.mark.asyncio
    async def test_null_column_with_global_default_on_toggles_off(self) -> None:
        """Review fix: NULL column + default_kb_enabled=True means the chat is
        effectively ON — the first tap must turn it OFF, not 're-enable' it."""
        callback = _make_callback(f"adm_kb_toggle:ru:{CHAT_ID}")
        chat_settings_repo = _make_chat_settings_repo(kb_enabled=None)
        bot_config_repo = _make_bot_config_repo(default_kb_enabled=True)
        chat_config_service = _make_chat_config_service()

        await handle_kb_toggle(callback, chat_settings_repo, bot_config_repo, chat_config_service)

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "kb_enabled", False)

    @pytest.mark.asyncio
    async def test_null_column_without_global_default_toggles_on(self) -> None:
        callback = _make_callback(f"adm_kb_toggle:ru:{CHAT_ID}")
        chat_settings_repo = _make_chat_settings_repo(kb_enabled=None)
        bot_config_repo = _make_bot_config_repo(default_kb_enabled=None)
        chat_config_service = _make_chat_config_service()

        await handle_kb_toggle(callback, chat_settings_repo, bot_config_repo, chat_config_service)

        chat_settings_repo.set_field.assert_awaited_once_with(CHAT_ID, "kb_enabled", True)

    @pytest.mark.asyncio
    async def test_invalidates_cache_for_this_chat_after_write(self) -> None:
        """E-1 regression: without this, the flip applied with up to 60s of
        delay because ChatConfigService kept serving the pre-toggle cached
        config -- the PRD's documented delayed-toggle bug."""
        callback = _make_callback(f"adm_kb_toggle:ru:{CHAT_ID}")
        chat_settings_repo = _make_chat_settings_repo(kb_enabled=False)
        bot_config_repo = _make_bot_config_repo()
        chat_config_service = _make_chat_config_service()

        await handle_kb_toggle(callback, chat_settings_repo, bot_config_repo, chat_config_service)

        chat_config_service.invalidate.assert_called_once_with(CHAT_ID)


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


def _make_message_repo(
    found: dict[str, object] | None = None,
    known_elsewhere: bool = False,
    top_active_users: tuple[list[dict[str, object]], int] = ([], 0),
) -> MagicMock:
    repo = MagicMock()
    repo.find_by_username = AsyncMock(return_value=found)
    repo.username_seen_elsewhere = AsyncMock(return_value=known_elsewhere)
    repo.get_top_active_users = AsyncMock(return_value=top_active_users)
    return repo


def _make_state(text_state_data: dict[str, object] | None = None) -> MagicMock:
    state = MagicMock()
    state.get_data = AsyncMock(
        return_value=text_state_data or {"kb_chat_id": CHAT_ID, "kb_lang": "ru"}
    )
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    return state


def _make_add_reply_message(*, forward_origin: object = None, text: str | None = None) -> MagicMock:
    message = MagicMock()
    message.from_user = MagicMock(id=ADMIN_ID)
    message.forward_origin = forward_origin
    message.text = text
    message.reply = AsyncMock()
    return message


class TestHandleKbOrganizerAddReply:
    @pytest.mark.asyncio
    async def test_adds_forwarded_user(self) -> None:
        state = _make_state()
        origin = MessageOriginUser(
            date=datetime.now(),
            sender_user=User(id=888, is_bot=False, first_name="New", username="newbie"),
        )
        message = _make_add_reply_message(forward_origin=origin)

        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID])
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo()

        await handle_kb_organizer_add_reply(
            message, chat_settings_repo, bot_config_repo, message_repo, state
        )

        chat_settings_repo.set_field.assert_awaited_once_with(
            CHAT_ID, "kb_organizer_ids", json.dumps([ORG_USER_ID, 888])
        )
        message.reply.assert_awaited_once()
        assert "@newbie" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_forward_privacy_hidden_gets_dedicated_copy(self) -> None:
        """MessageOriginHiddenUser (forward privacy on) is not the generic not_found."""
        state = _make_state()
        origin = MessageOriginHiddenUser(date=datetime.now(), sender_user_name="Hidden Someone")
        message = _make_add_reply_message(forward_origin=origin)

        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo()

        await handle_kb_organizer_add_reply(
            message, chat_settings_repo, bot_config_repo, message_repo, state
        )

        chat_settings_repo.set_field.assert_not_awaited()
        message.reply.assert_awaited_once()
        reply_text = message.reply.call_args[0][0]
        assert "приватность" in reply_text
        assert "Не нашёл" not in reply_text

    @pytest.mark.asyncio
    async def test_forwarded_from_channel_falls_back_to_not_found(self) -> None:
        state = _make_state()
        origin = MessageOriginChannel(
            date=datetime.now(),
            chat=Chat(id=-1009999, type="channel"),
            message_id=1,
        )
        message = _make_add_reply_message(forward_origin=origin)

        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo()

        await handle_kb_organizer_add_reply(
            message, chat_settings_repo, bot_config_repo, message_repo, state
        )

        chat_settings_repo.set_field.assert_not_awaited()
        assert "Не нашёл" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_resolves_username_from_chat_history(self) -> None:
        state = _make_state()
        message = _make_add_reply_message(text="@newbie")

        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID])
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo(found={"user_id": 888, "first_name": "New"})

        await handle_kb_organizer_add_reply(
            message, chat_settings_repo, bot_config_repo, message_repo, state
        )

        message_repo.find_by_username.assert_awaited_once_with(CHAT_ID, "newbie")
        chat_settings_repo.set_field.assert_awaited_once_with(
            CHAT_ID, "kb_organizer_ids", json.dumps([ORG_USER_ID, 888])
        )
        assert "@newbie" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_username_known_elsewhere_gets_dedicated_copy(self) -> None:
        """Seen in another chat's history, but not this one -- distinct from not_found."""
        state = _make_state()
        message = _make_add_reply_message(text="@elsewhere_user")

        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo(found=None, known_elsewhere=True)

        await handle_kb_organizer_add_reply(
            message, chat_settings_repo, bot_config_repo, message_repo, state
        )

        chat_settings_repo.set_field.assert_not_awaited()
        reply_text = message.reply.call_args[0][0]
        assert "не видел его в этом чате" in reply_text
        assert "Не нашёл" not in reply_text

    @pytest.mark.asyncio
    async def test_not_found_when_no_forward_and_unknown_username(self) -> None:
        state = _make_state()
        message = _make_add_reply_message(text="@totally_unknown")

        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo(found=None, known_elsewhere=False)

        await handle_kb_organizer_add_reply(
            message, chat_settings_repo, bot_config_repo, message_repo, state
        )

        chat_settings_repo.set_field.assert_not_awaited()
        message.reply.assert_awaited_once()
        assert "Не нашёл" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_not_found_when_no_forward_and_no_text(self) -> None:
        """Non-text, non-forward reply (e.g. a sticker) -- can't extract a username."""
        state = _make_state()
        message = _make_add_reply_message(text=None)

        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo()

        await handle_kb_organizer_add_reply(
            message, chat_settings_repo, bot_config_repo, message_repo, state
        )

        chat_settings_repo.set_field.assert_not_awaited()
        message_repo.find_by_username.assert_not_awaited()
        assert "Не нашёл" in message.reply.call_args[0][0]


class TestHandleKbOrganizerAddPrompt:
    """B-2: the add-organizer prompt now offers a picker entry point too."""

    @pytest.mark.asyncio
    async def test_prompt_includes_show_participants_button(self) -> None:
        callback = _make_callback(f"adm_kb_org_add:ru:{CHAT_ID}")
        callback.message.answer = AsyncMock()
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_kb_organizer_add_prompt(callback, bot_config_repo, state)

        callback.message.answer.assert_awaited_once()
        _, kwargs = callback.message.answer.call_args
        keyboard = kwargs["reply_markup"]
        callbacks = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        assert f"adm_kb_org_list:ru:{CHAT_ID}:0" in callbacks

    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_kb_org_add:ru:{CHAT_ID}", user_id=999)
        callback.message.answer = AsyncMock()
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_kb_organizer_add_prompt(callback, bot_config_repo, state)

        callback.message.answer.assert_not_awaited()
        state.set_state.assert_not_awaited()


class TestHandleKbOrganizerList:
    """B-2: paginated participant picker, ranked by message count."""

    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_kb_org_list:ru:{CHAT_ID}:0", user_id=999)
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo()

        await handle_kb_organizer_list(callback, bot_config_repo, message_repo)

        message_repo.get_top_active_users.assert_not_awaited()
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_renders_candidates_ranked_page(self) -> None:
        callback = _make_callback(f"adm_kb_org_list:ru:{CHAT_ID}:1")
        bot_config_repo = _make_bot_config_repo()
        candidates = [{"user_id": 111, "first_name": "Alice", "username": "alice"}]
        message_repo = _make_message_repo(top_active_users=(candidates, 12))

        await handle_kb_organizer_list(callback, bot_config_repo, message_repo)

        message_repo.get_top_active_users.assert_awaited_once_with(CHAT_ID, 1, 5)
        callback.message.edit_text.assert_awaited_once()
        _, kwargs = callback.message.edit_text.call_args
        keyboard = kwargs["reply_markup"]
        labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        assert "Alice (@alice)" in labels

    @pytest.mark.asyncio
    async def test_empty_state_when_chat_has_no_participants(self) -> None:
        callback = _make_callback(f"adm_kb_org_list:ru:{CHAT_ID}:0")
        bot_config_repo = _make_bot_config_repo()
        message_repo = _make_message_repo(top_active_users=([], 0))

        await handle_kb_organizer_list(callback, bot_config_repo, message_repo)

        text = callback.message.edit_text.call_args[0][0]
        assert "Не нашёл" in text


class TestHandleKbOrganizerPick:
    """B-2: tapping a picker candidate adds them as organizer directly."""

    @pytest.mark.asyncio
    async def test_denies_non_admin(self) -> None:
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:888", user_id=999)
        chat_settings_repo = _make_chat_settings_repo()
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_kb_organizer_pick(callback, chat_settings_repo, bot_config_repo, state)

        chat_settings_repo.set_field.assert_not_awaited()
        state.clear.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_adds_picked_user_as_organizer(self) -> None:
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:888")
        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID])
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_kb_organizer_pick(callback, chat_settings_repo, bot_config_repo, state)

        chat_settings_repo.set_field.assert_awaited_once_with(
            CHAT_ID, "kb_organizer_ids", json.dumps([ORG_USER_ID, 888])
        )

    @pytest.mark.asyncio
    async def test_clears_awaiting_organizer_state(self) -> None:
        """Picking from the list must not leave a stray awaiting-reply state
        that would misread a later text message as a username."""
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:888")
        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID])
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_kb_organizer_pick(callback, chat_settings_repo, bot_config_repo, state)

        state.clear.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_when_already_organizer(self) -> None:
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:{ORG_USER_ID}")
        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID])
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_kb_organizer_pick(callback, chat_settings_repo, bot_config_repo, state)

        chat_settings_repo.set_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_re_renders_organizers_list(self) -> None:
        callback = _make_callback(f"adm_kb_org_pick:ru:{CHAT_ID}:888")
        chat_settings_repo = _make_chat_settings_repo(organizer_ids=[ORG_USER_ID, 888])
        bot_config_repo = _make_bot_config_repo()
        state = _make_state()

        await handle_kb_organizer_pick(callback, chat_settings_repo, bot_config_repo, state)

        callback.message.edit_text.assert_awaited_once()


class TestKbSubmenuOriginWiring:
    """The producer/consumer stick together, not just each on its own.

    The keyboard tests prove kb_menu_keyboard() honours an origin, and the
    panel tests prove the link row emits one. Neither notices if the handler
    reads the token from the wrong segment — which is the whole bug class
    here, since every one of these payloads is positional.
    """

    @pytest.mark.asyncio
    async def test_panel_link_round_trips_into_a_panel_back_button(self) -> None:
        """Take the callback the panel actually renders, feed it to the real
        handler, and read the Back button off what the handler drew."""
        link = _row_for_kb_link(
            chat_panel_root_keyboard(
                "ru",
                chat_id=CHAT_ID,
                row=None,
                config=replace(ChatConfig(chat_id=CHAT_ID)),
                kb_status=True,
                reactions_status=(False, True),
            )
        )
        callback = _make_callback(link)

        await handle_kb_menu(callback, _make_chat_settings_repo(), _make_bot_config_repo())

        keyboard = callback.message.edit_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[-1][0].callback_data == f"adm_pnl_menu:ru:{CHAT_ID}"

    @pytest.mark.asyncio
    async def test_kb_pickers_own_link_still_goes_back_to_the_picker(self) -> None:
        callback = _make_callback(f"adm_kb_menu:ru:{CHAT_ID}")

        await handle_kb_menu(callback, _make_chat_settings_repo(), _make_bot_config_repo())

        keyboard = callback.message.edit_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[-1][0].callback_data == "adm_kb:ru:0"


def _row_for_kb_link(keyboard) -> str:
    for row in keyboard.inline_keyboard:
        for btn in row:
            if (btn.callback_data or "").startswith("adm_kb_menu:"):
                return btn.callback_data
    raise AssertionError("panel rendered no KB link row")
