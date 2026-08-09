"""Tests for admin sticker reply handler, detail/back navigation, and helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from src.bot.handlers.admin_sticker import (
    _build_detail_text,
    _extract_file_unique_id_from_reply,
    _resolve_default_tolerance_level,
    handle_admin_sticker_check,
    handle_admin_sticker_dm_analyze,
    handle_admin_sticker_reply,
    handle_clear,
    handle_clear_ask,
    handle_run_analysis,
    handle_sticker_back,
    handle_sticker_detail,
)
from src.bot.keyboards.admin_sticker import (
    _status_badge,
    sticker_clear_confirm_keyboard,
    sticker_detail_keyboard,
    sticker_dm_check_keyboard,
    sticker_reanalyze_retry_keyboard,
    sticker_set_detail_keyboard,
)
from src.services.modules.sticker.models import ReanalyzeResult, StickerLearningResult
from src.utils.telegram import TelegramFileError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reply_message(
    text: str | None = None,
    caption: str | None = None,
) -> MagicMock:
    """Mock a replied-to message with optional text/caption."""
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.message_id = 100
    return msg


def _make_message(
    text: str = "new description",
    user_id: int = 12345,
    chat_type: str = "private",
    reply_text: str | None = "🆔 AgADvh4AAlkbCFI\n<b>Описание:</b> test",
    reply_caption: str | None = None,
) -> MagicMock:
    """Mock aiogram Message for admin sticker reply tests."""
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.reply = AsyncMock()

    reply = _make_reply_message(text=reply_text, caption=reply_caption)
    msg.reply_to_message = reply
    return msg


def _make_bot() -> MagicMock:
    """Mock aiogram Bot for typing_indicator wiring (I-5)."""
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    return bot


# ---------------------------------------------------------------------------
# _extract_file_unique_id_from_reply
# ---------------------------------------------------------------------------


class TestExtractFileUniqueIdFromReply:
    def test_extracts_id_from_text(self) -> None:
        reply = _make_reply_message(text="🆔 AgADvh4AAlkbCFI\nОписание: test")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"

    def test_extracts_id_from_caption(self) -> None:
        reply = _make_reply_message(caption="🆔 AgADvh4AAlkbCFI")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"

    def test_prefers_text_over_caption(self) -> None:
        reply = _make_reply_message(
            text="🆔 ID_FROM_TEXT",
            caption="🆔 ID_FROM_CAPTION",
        )
        assert _extract_file_unique_id_from_reply(reply) == "ID_FROM_TEXT"

    def test_returns_none_when_no_id(self) -> None:
        reply = _make_reply_message(text="Just some text without ID")
        assert _extract_file_unique_id_from_reply(reply) is None

    def test_returns_none_when_both_none(self) -> None:
        reply = _make_reply_message(text=None, caption=None)
        assert _extract_file_unique_id_from_reply(reply) is None

    def test_handles_id_with_dashes_and_underscores(self) -> None:
        reply = _make_reply_message(text="🆔 AgAD-vh_4AA-lkb")
        assert _extract_file_unique_id_from_reply(reply) == "AgAD-vh_4AA-lkb"

    def test_handles_no_space_after_emoji(self) -> None:
        reply = _make_reply_message(text="🆔AgADvh4AAlkbCFI")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"


# ---------------------------------------------------------------------------
# handle_admin_sticker_reply
# ---------------------------------------------------------------------------


class TestHandleAdminStickerReply:
    @pytest.fixture()
    def sticker_repo(self) -> MagicMock:
        repo = MagicMock()
        repo.get_notification_by_reply = AsyncMock(return_value=None)
        return repo

    @pytest.fixture()
    def sticker_service(self) -> MagicMock:
        svc = MagicMock()
        svc.merge_admin_description = AsyncMock(return_value="Updated desc")
        return svc

    @pytest.mark.asyncio()
    async def test_db_lookup_success(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = {"file_unique_id": "AgADvh4AAlkbCFI"}
        msg = _make_message(text="better description")

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, _make_bot())

        sticker_service.merge_admin_description.assert_awaited_once_with(
            "AgADvh4AAlkbCFI", "better description"
        )
        msg.reply.assert_awaited_once()
        assert "обновлено" in msg.reply.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_text_fallback_when_db_returns_none(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = None
        msg = _make_message(
            text="better description",
            reply_text="🆔 FallbackID123\nОписание: old",
        )

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, _make_bot())

        sticker_service.merge_admin_description.assert_awaited_once_with(
            "FallbackID123", "better description"
        )

    @pytest.mark.asyncio()
    async def test_caption_fallback_for_collage_reply(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = None
        msg = _make_message(
            text="better description",
            reply_text=None,
            reply_caption="🆔 CaptionID456",
        )

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, _make_bot())

        sticker_service.merge_admin_description.assert_awaited_once_with(
            "CaptionID456", "better description"
        )

    @pytest.mark.asyncio()
    async def test_silent_return_when_no_id_found(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = None
        msg = _make_message(
            text="better description",
            reply_text="No ID here",
        )

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, _make_bot())

        sticker_service.merge_admin_description.assert_not_awaited()
        msg.reply.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_merge_failure_shows_error(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = {"file_unique_id": "AgADvh4AAlkbCFI"}
        sticker_service.merge_admin_description.return_value = None
        msg = _make_message(text="better description")

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, _make_bot())

        msg.reply.assert_awaited_once()
        assert "re-analyze" in msg.reply.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_content_filter_shows_rephrase(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = {"file_unique_id": "AgADvh4AAlkbCFI"}
        sticker_service.merge_admin_description.side_effect = ValueError("content_filter")
        msg = _make_message(text="better description")

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, _make_bot())

        msg.reply.assert_awaited_once()
        assert "переформулировать" in msg.reply.call_args[0][0].lower()

    # Note: the non-private-chat case is guarded at router-registration time
    # by `F.chat.type == "private"` in the @router.message decorator
    # (src/bot/handlers/admin_sticker.py). aiogram drops the update before it
    # ever reaches this handler, so a handler-level unit test cannot exercise
    # that path — it is verified by inspection of the decorator.

    @pytest.mark.asyncio()
    async def test_ignores_empty_text(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        sticker_repo.get_notification_by_reply.return_value = {"file_unique_id": "AgADvh4AAlkbCFI"}
        msg = _make_message(text="   ")

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, _make_bot())

        sticker_service.merge_admin_description.assert_not_awaited()


class TestHandleAdminStickerReplyTypingIndicator:
    """Regression guard: the merge_admin_description() LLM call must run
    under the shared typing_indicator helper (I-5).
    """

    @pytest.fixture()
    def sticker_repo(self) -> MagicMock:
        repo = MagicMock()
        repo.get_notification_by_reply = AsyncMock(
            return_value={"file_unique_id": "AgADvh4AAlkbCFI"}
        )
        return repo

    @pytest.fixture()
    def sticker_service(self) -> MagicMock:
        svc = MagicMock()
        svc.merge_admin_description = AsyncMock(return_value="Updated desc")
        return svc

    @pytest.mark.asyncio()
    async def test_wraps_merge_call(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        msg = _make_message(text="better description")
        bot = _make_bot()

        with patch("src.bot.handlers.admin_sticker.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, bot)

        mock_indicator.assert_called_once_with(bot, msg.chat.id, None)
        sticker_service.merge_admin_description.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_forwards_message_thread_id(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        msg = _make_message(text="better description")
        bot = _make_bot()

        with patch("src.bot.handlers.admin_sticker.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_admin_sticker_reply(
                msg, sticker_repo, sticker_service, bot, message_thread_id=777
            )

        mock_indicator.assert_called_once_with(bot, msg.chat.id, 777)

    @pytest.mark.asyncio()
    async def test_no_indicator_when_no_file_unique_id(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        """Guard-clause rejection (no id found) never reaches the merge
        call -- the indicator must not fire."""
        sticker_repo.get_notification_by_reply.return_value = None
        msg = _make_message(text="better description", reply_text="No ID here")
        bot = _make_bot()

        with patch("src.bot.handlers.admin_sticker.typing_indicator") as mock_indicator:
            await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, bot)

        mock_indicator.assert_not_called()

    @pytest.mark.asyncio()
    async def test_indicator_stops_even_if_merge_raises(
        self, sticker_repo: MagicMock, sticker_service: MagicMock
    ) -> None:
        """The real typing_indicator (ChatActionSender) guarantees the
        action stops on exception; here we assert a generic exception from
        inside the async-with block still reaches the handler's own
        ``except Exception`` fallback (i.e. we don't accidentally swallow
        or short-circuit it earlier by wrapping the call)."""
        sticker_service.merge_admin_description.side_effect = RuntimeError("boom")
        msg = _make_message(text="better description")
        bot = _make_bot()

        await handle_admin_sticker_reply(msg, sticker_repo, sticker_service, bot)

        msg.reply.assert_awaited_once()
        assert "не смог объединить" in msg.reply.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Callback test helpers
# ---------------------------------------------------------------------------

_SAMPLE_STICKER = {
    "file_id": "CAACAgIAAxkB",
    "file_unique_id": "AgADvh4AAlkbCFI",
    "set_name": "test_set",
    "visual_description": "happy cat",
    "emotion": "joy",
    "character_or_meme": None,
    "suggested_contexts": None,
    "total_uses": 5,
    "bot_uses": 2,
    "emoji": "😺",
    "is_animated": False,
    "is_video": False,
    "analysis_failed": False,
}


def _make_callback(
    data: str,
    user_id: int = 12345,
    chat_type: str = "private",
    message_id: int = 200,
) -> MagicMock:
    """Mock aiogram CallbackQuery for sticker admin tests."""
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.answer = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.chat.type = chat_type
    msg.message_id = message_id
    msg.delete = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.answer = AsyncMock()

    sticker_sent = MagicMock()
    sticker_sent.message_id = message_id + 1
    msg.answer_sticker = AsyncMock(return_value=sticker_sent)

    bot = MagicMock()
    bot.delete_message = AsyncMock()
    bot.send_message = AsyncMock()
    msg.bot = bot

    cb.message = msg
    return cb


def _make_bot_config_repo(
    admin_ids: str = "12345", tolerance_level: float | None = None
) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=admin_ids)
    # A-1: _resolve_default_tolerance_level() reads defaults["tolerance_level"];
    # None (the default here) exercises the ChatConfig dataclass fallback (0.5).
    defaults = {} if tolerance_level is None else {"tolerance_level": tolerance_level}
    repo.get_defaults = AsyncMock(return_value=defaults)
    return repo


def _make_sticker_repo(
    sticker: dict | None = None,
    set_count: int = 0,
    sticker_count: int = 0,
    latest_sticker_msg: int | None = None,
) -> MagicMock:
    repo = MagicMock()
    repo.get_by_file_unique_id = AsyncMock(return_value=sticker or _SAMPLE_STICKER)
    repo.count_stickers_in_set = AsyncMock(return_value=sticker_count)
    repo.get_stickers_in_set = AsyncMock(return_value=[])
    repo.count_sets = AsyncMock(return_value=set_count)
    repo.get_all_sets_with_stats = AsyncMock(return_value=[])
    repo.get_latest_sticker_msg = AsyncMock(return_value=latest_sticker_msg)
    repo.save_notification = AsyncMock(return_value=1)
    return repo


# ---------------------------------------------------------------------------
# handle_sticker_detail — delete + re-send pattern
# ---------------------------------------------------------------------------


class TestHandleStickerDetail:
    @pytest.mark.asyncio()
    async def test_deletes_old_message_and_sends_sticker_then_description(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        # Old message deleted
        cb.message.delete.assert_awaited_once()
        # Sticker sent
        cb.message.answer_sticker.assert_awaited_once_with("CAACAgIAAxkB")
        # Description sent as new message (not edit_text)
        cb.message.answer.assert_awaited_once()
        call_kwargs = cb.message.answer.call_args
        assert "Описание" in call_kwargs[0][0]
        # edit_text should NOT be called
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_back_button_uses_adm_stk_back_format(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        call_kwargs = cb.message.answer.call_args
        keyboard = call_kwargs[1]["reply_markup"]
        back_button = keyboard.inline_keyboard[-1][0]
        # Back button uses adm_stk_back without sticker_msg_id (DB lookup instead)
        assert back_button.callback_data == "adm_stk_back:ru:test_set:0"

    @pytest.mark.asyncio()
    async def test_cleans_up_previous_sticker_via_db(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker_repo = _make_sticker_repo(latest_sticker_msg=150)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        # Previous sticker message deleted via DB lookup
        cb.message.bot.delete_message.assert_awaited_once_with(
            chat_id=cb.message.chat.id,
            message_id=150,
        )

    @pytest.mark.asyncio()
    async def test_not_authorized(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI", user_id=99999)
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo("12345")

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        cb.answer.assert_awaited_once()
        assert cb.answer.call_args.kwargs.get("show_alert") is True
        cb.message.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_sticker_back — cleanup sticker + description, send set list
# ---------------------------------------------------------------------------


class TestHandleStickerBack:
    @pytest.mark.asyncio()
    async def test_deletes_sticker_and_description_messages(self) -> None:
        cb = _make_callback("adm_stk_back:ru:test_set:0")
        sticker_repo = _make_sticker_repo(sticker_count=5, latest_sticker_msg=150)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_back(cb, sticker_repo, bot_config_repo)

        # Sticker message deleted via DB lookup
        cb.message.bot.delete_message.assert_awaited_once_with(
            chat_id=cb.message.chat.id,
            message_id=150,
        )
        # Description message deleted
        cb.message.delete.assert_awaited_once()
        # New set list message sent
        cb.message.answer.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_works_without_sticker_msg_in_db(self) -> None:
        """Graceful handling when no sticker message found in DB."""
        cb = _make_callback("adm_stk_back:ru:test_set:0")
        sticker_repo = _make_sticker_repo(sticker_count=5, latest_sticker_msg=None)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_back(cb, sticker_repo, bot_config_repo)

        # No sticker deletion attempted
        cb.message.bot.delete_message.assert_not_awaited()
        # Description still deleted, set list still sent
        cb.message.delete.assert_awaited_once()
        cb.message.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# sticker_detail_keyboard — callback_data format
# ---------------------------------------------------------------------------


class TestStickerDetailKeyboard:
    def test_back_button_with_set_name(self) -> None:
        kb = sticker_detail_keyboard(
            "AgADvh4AAlkbCFI",
            lang="ru",
            set_name="test_set",
        )
        back_btn = kb.inline_keyboard[-1][0]
        # Always uses adm_stk_back (cleanup via DB, not callback_data)
        assert back_btn.callback_data == "adm_stk_back:ru:test_set:0"

    def test_long_set_name_no_truncation_issue(self) -> None:
        long_set_name = "a" * 50
        kb = sticker_detail_keyboard(
            "AgADvh4AAlkbCFI",
            lang="ru",
            set_name=long_set_name,
        )
        back_btn = kb.inline_keyboard[-1][0]
        # No sticker_msg_id in callback_data, so no 64-byte overflow
        assert back_btn.callback_data == f"adm_stk_back:ru:{long_set_name}:0"

    def test_without_set_name(self) -> None:
        kb = sticker_detail_keyboard("AgADvh4AAlkbCFI", lang="ru")
        back_btn = kb.inline_keyboard[-1][0]
        assert back_btn.callback_data == "adm_stk_sets:ru:0"

    def test_has_clear_and_reanalyze_buttons(self) -> None:
        kb = sticker_detail_keyboard("AgADvh4AAlkbCFI", lang="ru", set_name="s")
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        # Run-now button actually triggers analysis
        assert "adm_stk_reanalyze:ru:AgADvh4AAlkbCFI" in callbacks
        # Clear button routes through confirm step (adm_stk_clr_ask:)
        assert "adm_stk_clr_ask:ru:AgADvh4AAlkbCFI" in callbacks

    def test_clear_confirm_keyboard_has_yes_and_cancel(self) -> None:
        kb = sticker_clear_confirm_keyboard("AgADvh4AAlkbCFI", lang="ru")
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "adm_stk_clr:ru:AgADvh4AAlkbCFI" in callbacks
        # Cancel returns to sticker detail (no destructive action on cancel)
        assert "adm_stk_view:ru:AgADvh4AAlkbCFI" in callbacks

    def test_reanalyze_retry_keyboard_ru(self) -> None:
        kb = sticker_reanalyze_retry_keyboard("AgADvh4AAlkbCFI", lang="ru")
        assert len(kb.inline_keyboard) == 1
        btn = kb.inline_keyboard[0][0]
        assert "Повторить" in btn.text
        assert btn.callback_data == "adm_stk_reanalyze:ru:AgADvh4AAlkbCFI"

    def test_reanalyze_retry_keyboard_en(self) -> None:
        kb = sticker_reanalyze_retry_keyboard("AgADvh4AAlkbCFI", lang="en")
        btn = kb.inline_keyboard[0][0]
        assert "Retry" in btn.text
        assert btn.callback_data == "adm_stk_reanalyze:en:AgADvh4AAlkbCFI"


# ---------------------------------------------------------------------------
# handle_clear_ask — shows confirm dialog, does NOT clear
# ---------------------------------------------------------------------------


class TestHandleClearAsk:
    @pytest.mark.asyncio()
    async def test_shows_confirm_does_not_clear(self) -> None:
        cb = _make_callback("adm_stk_clr_ask:ru:AgADvh4AAlkbCFI")
        bot_config_repo = _make_bot_config_repo()

        await handle_clear_ask(cb, bot_config_repo)

        cb.message.edit_text.assert_awaited_once()
        text_arg = cb.message.edit_text.call_args[0][0]
        # Confirm text mentions both what's cleared AND what's preserved
        assert "описание" in text_arg.lower() or "описания" in text_arg.lower()
        assert "заметки" in text_arg.lower() or "заметк" in text_arg.lower()

    @pytest.mark.asyncio()
    async def test_not_authorized(self) -> None:
        cb = _make_callback("adm_stk_clr_ask:ru:AgADvh4AAlkbCFI", user_id=99999)
        bot_config_repo = _make_bot_config_repo("12345")

        await handle_clear_ask(cb, bot_config_repo)

        cb.message.edit_text.assert_not_awaited()
        assert cb.answer.call_args.kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# handle_clear — calls repo.clear_analysis (broad scope)
# ---------------------------------------------------------------------------


class TestHandleClear:
    @pytest.mark.asyncio()
    async def test_clears_analysis(self) -> None:
        cb = _make_callback("adm_stk_clr:ru:AgADvh4AAlkbCFI")
        sticker_repo = _make_sticker_repo()
        sticker_repo.clear_analysis = AsyncMock()
        bot_config_repo = _make_bot_config_repo()

        await handle_clear(cb, sticker_repo, bot_config_repo)

        sticker_repo.clear_analysis.assert_awaited_once_with("AgADvh4AAlkbCFI")
        # Re-renders the detail in place rather than leaving the confirm prompt.
        cb.message.edit_text.assert_awaited_once()
        cb.answer.assert_awaited_once()
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio()
    async def test_rerenders_not_analyzed_detail_after_clear(self) -> None:
        # After clearing, the repo returns the sticker with visual fields nulled;
        # handle_clear must edit the message back to the detail view showing the
        # ⏳ not-analyzed badge (regression: it used to stay on the confirm prompt).
        cleared = {
            **_SAMPLE_STICKER,
            "visual_description": None,
            "emotion": None,
            "character_or_meme": None,
            "suggested_contexts": None,
        }
        cb = _make_callback("adm_stk_clr:ru:AgADvh4AAlkbCFI")
        sticker_repo = _make_sticker_repo(sticker=cleared)
        sticker_repo.clear_analysis = AsyncMock()
        bot_config_repo = _make_bot_config_repo()

        await handle_clear(cb, sticker_repo, bot_config_repo)

        cb.message.edit_text.assert_awaited_once()
        text_arg = cb.message.edit_text.call_args[0][0]
        assert "не выполнен" in text_arg
        kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
        buttons = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Запустить заново" in b for b in buttons)

    @pytest.mark.asyncio()
    async def test_not_authorized_does_not_clear(self) -> None:
        cb = _make_callback("adm_stk_clr:ru:AgADvh4AAlkbCFI", user_id=99999)
        sticker_repo = _make_sticker_repo()
        sticker_repo.clear_analysis = AsyncMock()
        bot_config_repo = _make_bot_config_repo("12345")

        await handle_clear(cb, sticker_repo, bot_config_repo)

        sticker_repo.clear_analysis.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_run_analysis — edit-in-place lifecycle (A-2)
# ---------------------------------------------------------------------------


def _make_sticker_service_success(
    desc: str = "happy cat",
) -> MagicMock:
    """Sticker service mock returning a successful ReanalyzeResult."""
    svc = MagicMock()
    svc.reanalyze = AsyncMock(return_value=ReanalyzeResult(ok=True, visual_description=desc))
    return svc


def _make_sticker_service_failure(
    reason: str = "vision",
) -> MagicMock:
    """Sticker service mock returning a failed ReanalyzeResult with the given reason."""
    svc = MagicMock()
    svc.reanalyze = AsyncMock(
        return_value=ReanalyzeResult(ok=False, reason=reason)  # type: ignore[arg-type]
    )
    return svc


class TestHandleRunAnalysis:
    # ── helpers ────────────────────────────────────────────────────────

    def _make_repo_with_desc(self, desc: str = "happy cat") -> MagicMock:
        """Sticker repo that returns a sticker with a description (post-analysis)."""
        repo = MagicMock()
        repo.get_by_file_unique_id = AsyncMock(
            return_value={**_SAMPLE_STICKER, "visual_description": desc}
        )
        return repo

    # ── success path ───────────────────────────────────────────────────

    @pytest.mark.asyncio()
    async def test_success_edits_message_in_place_ru(self) -> None:
        """Success: message is edited (not sent) with ✅ and description (ru)."""
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_success("happy cat")
        sticker_repo = self._make_repo_with_desc("happy cat")
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        # Must edit in-place — no new message
        cb.message.bot.send_message.assert_not_awaited()
        assert cb.message.edit_text.await_count >= 1

    @pytest.mark.asyncio()
    async def test_success_result_text_contains_checkmark_ru(self) -> None:
        """Success result text contains ✅ in Russian."""
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_success()
        sticker_repo = self._make_repo_with_desc()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        # Last edit_text call = the result message
        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "✅" in result_text
        assert "Анализ обновлён" in result_text

    @pytest.mark.asyncio()
    async def test_success_result_text_contains_checkmark_en(self) -> None:
        """Success result text contains ✅ in English."""
        cb = _make_callback("adm_stk_reanalyze:en:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_success()
        sticker_repo = self._make_repo_with_desc()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "✅" in result_text
        assert "Analysis updated" in result_text

    @pytest.mark.asyncio()
    async def test_success_result_html_escapes_description(self) -> None:
        """Description with HTML chars is escaped before insertion."""
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_success("<b>evil</b>")
        sticker_repo = self._make_repo_with_desc("<b>evil</b>")
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "<b>evil</b>" not in result_text
        assert "&lt;b&gt;evil&lt;/b&gt;" in result_text

    @pytest.mark.asyncio()
    async def test_success_restores_full_keyboard(self) -> None:
        """On success the sticker detail keyboard (with re-analyze + clear) is restored."""
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_success()
        sticker_repo = self._make_repo_with_desc()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_kwargs = cb.message.edit_text.call_args_list[-1][1]
        keyboard = result_kwargs["reply_markup"]
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        assert any("adm_stk_reanalyze" in c for c in callbacks)
        assert any("adm_stk_clr_ask" in c for c in callbacks)

    # ── in-progress ⏳ edit ────────────────────────────────────────────

    @pytest.mark.asyncio()
    async def test_shows_in_progress_edit_before_analysis(self) -> None:
        """⏳ edit happens as the FIRST edit_text call, before reanalyze is awaited."""
        edit_calls: list[str] = []
        reanalyze_called_after: list[int] = []

        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")

        async def _mock_reanalyze(_bot: object, _fuid: str) -> ReanalyzeResult:
            reanalyze_called_after.append(len(edit_calls))
            return ReanalyzeResult(ok=True, visual_description="cat")

        async def _mock_edit_text(text: str, **_kwargs: object) -> None:
            edit_calls.append(text)

        cb.message.edit_text = AsyncMock(side_effect=_mock_edit_text)

        sticker_service = MagicMock()
        sticker_service.reanalyze = AsyncMock(side_effect=_mock_reanalyze)
        sticker_repo = self._make_repo_with_desc("cat")
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        # ⏳ edit came before reanalyze
        assert reanalyze_called_after[0] == 1
        assert "⏳" in edit_calls[0]

    @pytest.mark.asyncio()
    async def test_in_progress_edit_hides_buttons(self) -> None:
        """⏳ edit uses empty keyboard to disable buttons."""
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_success()
        sticker_repo = self._make_repo_with_desc()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        first_call = cb.message.edit_text.call_args_list[0]
        keyboard = first_call[1]["reply_markup"]
        assert keyboard.inline_keyboard == []

    @pytest.mark.asyncio()
    async def test_analysis_continues_when_in_progress_edit_fails(self) -> None:
        """If the ⏳ edit raises TelegramBadRequest, analysis still runs and result is shown."""
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")

        call_count = 0

        async def _edit_text_side_effect(_text: str, **_kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate in-progress edit failing (double-tap / network)
                raise TelegramBadRequest(
                    method="editMessageText", message="message is not modified"
                )

        cb.message.edit_text = AsyncMock(side_effect=_edit_text_side_effect)

        sticker_service = _make_sticker_service_success()
        sticker_repo = self._make_repo_with_desc()
        bot_config_repo = _make_bot_config_repo()

        # Should not raise; analysis must still run and result edit must be attempted
        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        sticker_service.reanalyze.assert_awaited_once()
        assert call_count == 2  # ⏳ attempt + ✅ result

    # ── failure paths ──────────────────────────────────────────────────

    @pytest.mark.asyncio()
    async def test_failure_download_shows_reason_ru(self) -> None:
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_failure("download")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "⚠️" in result_text
        assert "Ошибка загрузки" in result_text

    @pytest.mark.asyncio()
    async def test_failure_vision_shows_reason_ru(self) -> None:
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_failure("vision")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "⚠️" in result_text
        assert "Ошибка API" in result_text

    @pytest.mark.asyncio()
    async def test_failure_content_filter_shows_reason_ru(self) -> None:
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_failure("content_filter")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "⚠️" in result_text
        assert "Контент заблокирован" in result_text

    @pytest.mark.asyncio()
    async def test_failure_empty_shows_reason_ru(self) -> None:
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_failure("empty")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "⚠️" in result_text
        assert "Пустой ответ" in result_text

    @pytest.mark.asyncio()
    async def test_failure_shows_reasons_in_english(self) -> None:
        cb = _make_callback("adm_stk_reanalyze:en:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_failure("download")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "Download error" in result_text

    @pytest.mark.asyncio()
    async def test_failure_shows_retry_button(self) -> None:
        """Failure result keyboard contains the Retry button pointing to adm_stk_reanalyze."""
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")
        sticker_service = _make_sticker_service_failure("vision")
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        result_kwargs = cb.message.edit_text.call_args_list[-1][1]
        keyboard = result_kwargs["reply_markup"]
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        assert any("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI" in c for c in callbacks)

    # ── double-tap guard ───────────────────────────────────────────────

    @pytest.mark.asyncio()
    async def test_result_edit_not_modified_is_suppressed(self) -> None:
        """TelegramBadRequest 'message is not modified' on result edit is silently suppressed."""
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI")

        call_count = 0

        async def _edit_text_side_effect(text: str, **_kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if "✅" in text or "⚠️" in text:
                # Result edit raises "not modified" (double-tap: already showed result)
                raise TelegramBadRequest(
                    method="editMessageText", message="message is not modified"
                )

        cb.message.edit_text = AsyncMock(side_effect=_edit_text_side_effect)
        sticker_service = _make_sticker_service_success()
        sticker_repo = self._make_repo_with_desc()
        bot_config_repo = _make_bot_config_repo()

        # Must not raise
        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)
        assert call_count == 2  # both edits attempted, second suppressed

    # ── not authorized ─────────────────────────────────────────────────

    @pytest.mark.asyncio()
    async def test_not_authorized_does_not_run(self) -> None:
        cb = _make_callback("adm_stk_reanalyze:ru:AgADvh4AAlkbCFI", user_id=99999)
        sticker_service = MagicMock()
        sticker_service.reanalyze = AsyncMock()
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo("12345")

        await handle_run_analysis(cb, sticker_service, sticker_repo, bot_config_repo)

        sticker_service.reanalyze.assert_not_awaited()


# ---------------------------------------------------------------------------
# _status_badge — unified status vocabulary helper
# ---------------------------------------------------------------------------


class TestStatusBadgeHelper:
    """Tests for the unified _status_badge helper (A-1)."""

    # ── ⏳ not-analyzed ─────────────────────────────────────────────────

    def test_not_analyzed_short_ru(self) -> None:
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": False}
        badge = _status_badge(sticker, "ru", short=True)
        assert "⏳" in badge
        assert "Не выполнен" in badge

    def test_not_analyzed_short_en(self) -> None:
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": False}
        badge = _status_badge(sticker, "en", short=True)
        assert "⏳" in badge
        assert "Not analyzed" in badge

    def test_not_analyzed_long_ru(self) -> None:
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": False}
        badge = _status_badge(sticker, "ru", short=False)
        assert "⏳" in badge
        assert "Визуальный анализ не выполнен" in badge

    def test_not_analyzed_long_en(self) -> None:
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": False}
        badge = _status_badge(sticker, "en", short=False)
        assert "⏳" in badge
        assert "Visual analysis" in badge

    # ── ⚠️ failed ─────────────────────────────────────────────────────

    def test_failed_short_ru(self) -> None:
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": True}
        badge = _status_badge(sticker, "ru", short=True)
        assert "⚠️" in badge
        assert "Ошибка" in badge

    def test_failed_short_en(self) -> None:
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": True}
        badge = _status_badge(sticker, "en", short=True)
        assert "⚠️" in badge
        assert "Failed" in badge

    def test_failed_long_ru(self) -> None:
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": True}
        badge = _status_badge(sticker, "ru", short=False)
        assert "⚠️" in badge
        assert "Анализ провалился" in badge

    def test_failed_long_en(self) -> None:
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": True}
        badge = _status_badge(sticker, "en", short=False)
        assert "⚠️" in badge
        assert "Analysis failed" in badge

    # ── ✅ analyzed ───────────────────────────────────────────────────

    def test_analyzed_short_truncates_at_25_chars(self) -> None:
        long_desc = "a" * 30
        sticker: dict[str, object] = {"visual_description": long_desc, "analysis_failed": False}
        badge = _status_badge(sticker, "ru", short=True)
        assert badge == "a" * 25

    def test_analyzed_short_no_truncation_under_25(self) -> None:
        sticker: dict[str, object] = {"visual_description": "short", "analysis_failed": False}
        badge = _status_badge(sticker, "ru", short=True)
        assert badge == "short"

    def test_analyzed_long_returns_full_description(self) -> None:
        desc = "a happy cat with a big smile"
        sticker: dict[str, object] = {"visual_description": desc, "analysis_failed": False}
        badge = _status_badge(sticker, "ru", short=False)
        assert badge == desc

    # ── priority: failed takes precedence over not-analyzed ──────────

    def test_failed_takes_priority_over_not_analyzed(self) -> None:
        """analysis_failed=True + visual_description=None → ⚠️ (not ⏳)."""
        sticker: dict[str, object] = {"visual_description": None, "analysis_failed": True}
        badge_long = _status_badge(sticker, "ru", short=False)
        assert "⚠️" in badge_long
        assert "⏳" not in badge_long

    # ── missing keys are treated as falsy ────────────────────────────

    def test_missing_keys_treated_as_not_analyzed(self) -> None:
        badge = _status_badge({}, "ru", short=True)
        assert "⏳" in badge


# ---------------------------------------------------------------------------
# sticker_set_detail_keyboard — now uses _status_badge vocabulary
# ---------------------------------------------------------------------------

_STICKERS_FOR_KB: list[dict[str, object]] = [
    {
        "file_unique_id": "analyzed_id",
        "emoji": "😀",
        "total_uses": 5,
        "analysis_failed": False,
        "visual_description": "a happy cat face with big eyes",
    },
    {
        "file_unique_id": "pending_id",
        "emoji": "😢",
        "total_uses": 3,
        "analysis_failed": False,
        "visual_description": None,
    },
    {
        "file_unique_id": "failed_id",
        "emoji": "😠",
        "total_uses": 1,
        "analysis_failed": True,
        "visual_description": None,
    },
]


class TestStickerSetDetailKeyboardStatusBadge:
    """Keyboard uses _status_badge — one vocabulary for ✅/⏳/⚠️ (A-1)."""

    def _labels(self, lang: str) -> list[str]:
        kb = sticker_set_detail_keyboard(
            _STICKERS_FOR_KB,
            set_name="test_set",
            lang=lang,
            page=0,
            total=3,
        )
        return [btn.text for row in kb.inline_keyboard for btn in row]

    def test_analyzed_shows_truncated_description_ru(self) -> None:
        labels = self._labels("ru")
        assert any("a happy cat face with big" in label for label in labels)

    def test_not_analyzed_shows_pending_badge_ru(self) -> None:
        labels = self._labels("ru")
        assert any("⏳" in label and "Не выполнен" in label for label in labels)

    def test_not_analyzed_shows_pending_badge_en(self) -> None:
        labels = self._labels("en")
        assert any("⏳" in label and "Not analyzed" in label for label in labels)

    def test_failed_shows_warning_badge_ru(self) -> None:
        labels = self._labels("ru")
        assert any("⚠️" in label and "Ошибка" in label for label in labels)

    def test_failed_shows_warning_badge_en(self) -> None:
        labels = self._labels("en")
        assert any("⚠️" in label and "Failed" in label for label in labels)

    def test_old_failed_bracket_label_not_present(self) -> None:
        """Regression: [FAILED] label must no longer appear."""
        for lang in ("ru", "en"):
            labels = self._labels(lang)
            assert not any("[FAILED]" in label for label in labels)

    def test_old_awaits_label_not_present(self) -> None:
        """Regression: 'ожидает анализа' must no longer appear (replaced by ⏳ badge)."""
        labels = self._labels("ru")
        assert not any("ожидает анализа" in label for label in labels)


# ---------------------------------------------------------------------------
# handle_sticker_detail — status badge used for ⏳ / ⚠️ display (A-1)
# ---------------------------------------------------------------------------


class TestHandleStickerDetailStatusBadge:
    """Detail view uses _status_badge for the not-analyzed and failed cases."""

    @pytest.mark.asyncio()
    async def test_not_analyzed_shows_pending_badge_ru(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker = {**_SAMPLE_STICKER, "visual_description": None, "analysis_failed": False}
        sticker_repo = _make_sticker_repo(sticker=sticker)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        text = cb.message.answer.call_args[0][0]
        assert "⏳" in text
        assert "Визуальный анализ не выполнен" in text

    @pytest.mark.asyncio()
    async def test_not_analyzed_shows_pending_badge_en(self) -> None:
        cb = _make_callback("adm_stk_view:en:AgADvh4AAlkbCFI")
        sticker = {**_SAMPLE_STICKER, "visual_description": None, "analysis_failed": False}
        sticker_repo = _make_sticker_repo(sticker=sticker)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        text = cb.message.answer.call_args[0][0]
        assert "⏳" in text
        assert "Visual analysis" in text

    @pytest.mark.asyncio()
    async def test_failed_shows_warning_badge_ru(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker = {**_SAMPLE_STICKER, "visual_description": None, "analysis_failed": True}
        sticker_repo = _make_sticker_repo(sticker=sticker)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        text = cb.message.answer.call_args[0][0]
        assert "⚠️" in text
        assert "Анализ провалился" in text

    @pytest.mark.asyncio()
    async def test_failed_shows_warning_badge_en(self) -> None:
        cb = _make_callback("adm_stk_view:en:AgADvh4AAlkbCFI")
        sticker = {**_SAMPLE_STICKER, "visual_description": None, "analysis_failed": True}
        sticker_repo = _make_sticker_repo(sticker=sticker)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        text = cb.message.answer.call_args[0][0]
        assert "⚠️" in text
        assert "Analysis failed" in text

    @pytest.mark.asyncio()
    async def test_failed_does_not_also_show_pending_badge(self) -> None:
        """Regression: failed state must show only ⚠️, not both ⚠️ and ⏳."""
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker = {**_SAMPLE_STICKER, "visual_description": None, "analysis_failed": True}
        sticker_repo = _make_sticker_repo(sticker=sticker)
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        text = cb.message.answer.call_args[0][0]
        assert "⏳" not in text

    @pytest.mark.asyncio()
    async def test_analyzed_shows_description_not_badge(self) -> None:
        """Analyzed sticker: description shown inline, no ⏳ or ⚠️."""
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        # _SAMPLE_STICKER has visual_description="happy cat"
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        text = cb.message.answer.call_args[0][0]
        assert "Описание" in text
        assert "happy cat" in text
        assert "⏳" not in text
        assert "⚠️" not in text


# ---------------------------------------------------------------------------
# handle_admin_sticker_check / handle_admin_sticker_dm_analyze — DM check (B-1)
# ---------------------------------------------------------------------------


def _make_sticker(
    file_unique_id: str = "AgADvh4AAlkbCFI",
    file_id: str = "CAACAgIAAxkB",
    set_name: str | None = "test_set",
    emoji: str | None = "😺",
    is_animated: bool = False,
    is_video: bool = False,
) -> MagicMock:
    """Mock aiogram Sticker with just the attributes the handlers touch."""
    sticker = MagicMock()
    sticker.file_unique_id = file_unique_id
    sticker.file_id = file_id
    sticker.set_name = set_name
    sticker.emoji = emoji
    sticker.is_animated = is_animated
    sticker.is_video = is_video
    return sticker


def _make_sticker_dm_message(
    sticker: MagicMock | None = None,
    user_id: int = 12345,
    chat_type: str = "private",
) -> MagicMock:
    """Mock aiogram Message carrying a sticker, sent by the admin in DM."""
    msg = MagicMock()
    msg.sticker = sticker if sticker is not None else _make_sticker()
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.reply = AsyncMock()
    return msg


def _make_admin_repo(lang: str = "ru") -> MagicMock:
    repo = MagicMock()
    repo.get_admin_language = AsyncMock(return_value=lang)
    return repo


def _cb_with_reply(
    callback_data: str = "adm_stk_dmchk:ru:AgADvh4AAlkbCFI",
    sticker: MagicMock | None = None,
    user_id: int = 12345,
) -> MagicMock:
    """Callback whose message.reply_to_message carries `sticker` (default:
    a matching _make_sticker()) -- mirrors the real flow, where
    handle_admin_sticker_check() sent the analyze prompt as a reply to the
    admin's original sticker message.
    """
    cb = _make_callback(callback_data, user_id=user_id)
    reply_msg = MagicMock()
    reply_msg.sticker = sticker if sticker is not None else _make_sticker()
    cb.message.reply_to_message = reply_msg
    return cb


def _cb_without_reply(
    callback_data: str = "adm_stk_dmchk:ru:AgADvh4AAlkbCFI",
    user_id: int = 12345,
) -> MagicMock:
    cb = _make_callback(callback_data, user_id=user_id)
    cb.message.reply_to_message = None
    return cb


class TestHandleAdminStickerCheck:
    """Admin sends a sticker in DM (no command) -- B-1 catalog check."""

    @pytest.mark.asyncio()
    async def test_known_sticker_replies_with_detail(self) -> None:
        msg = _make_sticker_dm_message()
        sticker_repo = _make_sticker_repo()  # returns _SAMPLE_STICKER
        admin_repo = _make_admin_repo("ru")
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_sticker_check(msg, sticker_repo, admin_repo, bot_config_repo)

        msg.reply.assert_awaited_once()
        text = msg.reply.call_args[0][0]
        assert "happy cat" in text
        assert msg.reply.call_args.kwargs["parse_mode"] == "HTML"
        keyboard = msg.reply.call_args.kwargs["reply_markup"]
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        assert "adm_stk_reanalyze:ru:AgADvh4AAlkbCFI" in callbacks

    @pytest.mark.asyncio()
    async def test_unknown_sticker_shows_analyze_button_ru(self) -> None:
        msg = _make_sticker_dm_message(sticker=_make_sticker(file_unique_id="unknownID"))
        sticker_repo = _make_sticker_repo()
        sticker_repo.get_by_file_unique_id = AsyncMock(return_value=None)
        admin_repo = _make_admin_repo("ru")
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_sticker_check(msg, sticker_repo, admin_repo, bot_config_repo)

        msg.reply.assert_awaited_once()
        text = msg.reply.call_args[0][0]
        assert "нет в базе" in text
        keyboard = msg.reply.call_args.kwargs["reply_markup"]
        btn = keyboard.inline_keyboard[0][0]
        assert btn.callback_data == "adm_stk_dmchk:ru:unknownID"
        assert "Проанализировать" in btn.text

    @pytest.mark.asyncio()
    async def test_unknown_sticker_shows_analyze_button_en(self) -> None:
        msg = _make_sticker_dm_message(sticker=_make_sticker(file_unique_id="unknownID"))
        sticker_repo = _make_sticker_repo()
        sticker_repo.get_by_file_unique_id = AsyncMock(return_value=None)
        admin_repo = _make_admin_repo("en")
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_sticker_check(msg, sticker_repo, admin_repo, bot_config_repo)

        text = msg.reply.call_args[0][0]
        assert "isn't in the catalog" in text
        btn = msg.reply.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert "Analyze" in btn.text

    @pytest.mark.asyncio()
    async def test_no_sticker_on_message_is_noop(self) -> None:
        """Defensive guard: message.sticker is None (shouldn't happen given
        the F.sticker filter, but the handler must not blow up)."""
        msg = _make_sticker_dm_message()
        msg.sticker = None
        sticker_repo = _make_sticker_repo()
        admin_repo = _make_admin_repo("ru")
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_sticker_check(msg, sticker_repo, admin_repo, bot_config_repo)

        msg.reply.assert_not_awaited()
        sticker_repo.get_by_file_unique_id.assert_not_awaited()

    # Note: admin+private scoping is enforced at router-registration time by
    # `F.sticker, F.chat.type == "private", IsAdmin()` on the @router.message
    # decorator (src/bot/handlers/admin_sticker.py) -- same pattern as
    # handle_admin_sticker_reply above. aiogram drops the update before it
    # reaches this handler for a non-admin or a non-DM chat, so that path is
    # verified by inspection of the decorator, not by a handler-level test.


class TestHandleAdminStickerDmAnalyze:
    """The "🔍 Проанализировать" button -- B-1's not-found branch."""

    @pytest.mark.asyncio()
    async def test_success_edits_in_place_with_detail(self) -> None:
        cb = _cb_with_reply()
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock(
            return_value=StickerLearningResult(
                is_new=True,
                file_unique_id="AgADvh4AAlkbCFI",
                visual_description="happy cat",
                analysis_failed=False,
            )
        )
        sticker_repo = _make_sticker_repo()
        sticker_repo.get_sticker_set = AsyncMock(return_value={"set_name": "test_set"})
        bot_config_repo = _make_bot_config_repo()

        with patch(
            "src.bot.handlers.admin_sticker.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await handle_admin_sticker_dm_analyze(
                cb, sticker_service, sticker_repo, bot_config_repo
            )

        sticker_service.learn.assert_awaited_once()
        cb.message.bot.send_message.assert_not_awaited()
        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "happy cat" in result_text

    @pytest.mark.asyncio()
    async def test_shows_in_progress_edit_before_download(self) -> None:
        cb = _cb_with_reply()
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock(
            return_value=StickerLearningResult(
                is_new=True,
                file_unique_id="AgADvh4AAlkbCFI",
                visual_description="cat",
                analysis_failed=False,
            )
        )
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        with patch(
            "src.bot.handlers.admin_sticker.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await handle_admin_sticker_dm_analyze(
                cb, sticker_service, sticker_repo, bot_config_repo
            )

        first_call = cb.message.edit_text.call_args_list[0]
        assert "⏳" in first_call[0][0]
        assert first_call[1]["reply_markup"].inline_keyboard == []

    @pytest.mark.asyncio()
    async def test_missing_reply_to_message_shows_alert_and_skips_learn(self) -> None:
        cb = _cb_without_reply()
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock()
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_sticker_dm_analyze(cb, sticker_service, sticker_repo, bot_config_repo)

        sticker_service.learn.assert_not_awaited()
        cb.answer.assert_awaited_once()
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio()
    async def test_mismatched_file_unique_id_shows_alert_and_skips_learn(self) -> None:
        # Reply carries a DIFFERENT sticker than callback_data references --
        # e.g. the admin sent a second sticker before tapping the first
        # prompt's button. Must not analyze the wrong sticker.
        cb = _cb_with_reply(sticker=_make_sticker(file_unique_id="someOtherID"))
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock()
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        await handle_admin_sticker_dm_analyze(cb, sticker_service, sticker_repo, bot_config_repo)

        sticker_service.learn.assert_not_awaited()
        cb.answer.assert_awaited_once()
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio()
    async def test_download_failure_shows_reason_and_retry_button(self) -> None:
        cb = _cb_with_reply()
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock()
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        with patch(
            "src.bot.handlers.admin_sticker.download_telegram_file",
            new=AsyncMock(side_effect=TelegramFileError("boom")),
        ):
            await handle_admin_sticker_dm_analyze(
                cb, sticker_service, sticker_repo, bot_config_repo
            )

        sticker_service.learn.assert_not_awaited()
        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "⚠️" in result_text
        assert "Ошибка загрузки" in result_text
        keyboard = cb.message.edit_text.call_args_list[-1][1]["reply_markup"]
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        assert "adm_stk_reanalyze:ru:AgADvh4AAlkbCFI" in callbacks

    @pytest.mark.asyncio()
    async def test_analysis_failed_shows_retry_button(self) -> None:
        cb = _cb_with_reply()
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock(
            return_value=StickerLearningResult(
                is_new=True,
                file_unique_id="AgADvh4AAlkbCFI",
                analysis_failed=True,
                failure_reason="vision",
            )
        )
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo()

        with patch(
            "src.bot.handlers.admin_sticker.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await handle_admin_sticker_dm_analyze(
                cb, sticker_service, sticker_repo, bot_config_repo
            )

        result_text = cb.message.edit_text.call_args_list[-1][0][0]
        assert "⚠️" in result_text
        assert "Ошибка API" in result_text
        keyboard = cb.message.edit_text.call_args_list[-1][1]["reply_markup"]
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        assert "adm_stk_reanalyze:ru:AgADvh4AAlkbCFI" in callbacks

    @pytest.mark.asyncio()
    async def test_registers_new_sticker_set(self) -> None:
        cb = _cb_with_reply()
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock(
            return_value=StickerLearningResult(
                is_new=True,
                file_unique_id="AgADvh4AAlkbCFI",
                visual_description="cat",
                analysis_failed=False,
            )
        )
        sticker_repo = _make_sticker_repo()
        sticker_repo.get_sticker_set = AsyncMock(return_value=None)
        sticker_repo.upsert_sticker_set = AsyncMock()
        bot_config_repo = _make_bot_config_repo()

        tg_set = MagicMock()
        tg_set.name = "test_set"
        tg_set.title = "Test Set"
        tg_set.thumbnail = None
        set_sticker = MagicMock()
        set_sticker.is_animated = False
        set_sticker.is_video = False
        tg_set.stickers = [set_sticker]
        cb.message.bot.get_sticker_set = AsyncMock(return_value=tg_set)

        with patch(
            "src.bot.handlers.admin_sticker.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await handle_admin_sticker_dm_analyze(
                cb, sticker_service, sticker_repo, bot_config_repo
            )

        sticker_repo.upsert_sticker_set.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_skips_set_registration_when_already_known(self) -> None:
        cb = _cb_with_reply()
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock(
            return_value=StickerLearningResult(
                is_new=True,
                file_unique_id="AgADvh4AAlkbCFI",
                visual_description="cat",
                analysis_failed=False,
            )
        )
        sticker_repo = _make_sticker_repo()
        sticker_repo.get_sticker_set = AsyncMock(return_value={"set_name": "test_set"})
        sticker_repo.upsert_sticker_set = AsyncMock()
        bot_config_repo = _make_bot_config_repo()

        with patch(
            "src.bot.handlers.admin_sticker.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await handle_admin_sticker_dm_analyze(
                cb, sticker_service, sticker_repo, bot_config_repo
            )

        sticker_repo.upsert_sticker_set.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_not_authorized_does_not_run(self) -> None:
        cb = _cb_with_reply(user_id=99999)
        sticker_service = MagicMock()
        sticker_service.learn = AsyncMock()
        sticker_repo = _make_sticker_repo()
        bot_config_repo = _make_bot_config_repo("12345")

        await handle_admin_sticker_dm_analyze(cb, sticker_service, sticker_repo, bot_config_repo)

        sticker_service.learn.assert_not_awaited()


# ---------------------------------------------------------------------------
# sticker_dm_check_keyboard — callback_data format
# ---------------------------------------------------------------------------


class TestStickerDmCheckKeyboard:
    def test_single_analyze_button_ru(self) -> None:
        kb = sticker_dm_check_keyboard("AgADvh4AAlkbCFI", lang="ru")
        assert len(kb.inline_keyboard) == 1
        btn = kb.inline_keyboard[0][0]
        assert "Проанализировать" in btn.text
        assert btn.callback_data == "adm_stk_dmchk:ru:AgADvh4AAlkbCFI"

    def test_single_analyze_button_en(self) -> None:
        kb = sticker_dm_check_keyboard("AgADvh4AAlkbCFI", lang="en")
        btn = kb.inline_keyboard[0][0]
        assert "Analyze" in btn.text
        assert btn.callback_data == "adm_stk_dmchk:en:AgADvh4AAlkbCFI"


# ---------------------------------------------------------------------------
# _resolve_default_tolerance_level (A-1)
# ---------------------------------------------------------------------------


class TestResolveDefaultToleranceLevel:
    @pytest.mark.asyncio()
    async def test_falls_back_to_chatconfig_dataclass_default(self) -> None:
        """No admin-set default_tolerance_level -> the same 0.5 fallback
        ChatConfig itself uses (ADR-0008 Decision 1/8)."""
        bot_config_repo = _make_bot_config_repo()
        assert await _resolve_default_tolerance_level(bot_config_repo) == 0.5

    @pytest.mark.asyncio()
    async def test_uses_admin_set_default(self) -> None:
        bot_config_repo = _make_bot_config_repo(tolerance_level=0.2)
        assert await _resolve_default_tolerance_level(bot_config_repo) == 0.2

    @pytest.mark.asyncio()
    async def test_coerces_to_float(self) -> None:
        """bot_config values round-trip through JSON -- an admin-entered
        whole number could come back as an int; must still compare cleanly
        against explicitness_score (float)."""
        bot_config_repo = MagicMock()
        bot_config_repo.get_defaults = AsyncMock(return_value={"tolerance_level": 1})
        result = await _resolve_default_tolerance_level(bot_config_repo)
        assert result == 1.0
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# _build_detail_text — explicitness line, three states (A-1)
# ---------------------------------------------------------------------------


class TestBuildDetailTextExplicitnessLine:
    def test_scored_and_passes_shows_pass_verdict(self) -> None:
        sticker = {**_SAMPLE_STICKER, "explicitness_score": 0.3}
        text = _build_detail_text(sticker, "fuid", "ru", tolerance_level=0.5)
        assert "Оценка откровенности" in text
        assert "0.30" in text
        assert "0.50" in text
        assert "✅ пройдёт" in text

    def test_scored_and_fails_shows_fail_verdict(self) -> None:
        sticker = {**_SAMPLE_STICKER, "explicitness_score": 0.9}
        text = _build_detail_text(sticker, "fuid", "ru", tolerance_level=0.5)
        assert "❌ не пройдёт" in text

    def test_analyzed_but_unscored_shows_not_scored(self) -> None:
        """State 2: visual_description present, explicitness_score NULL
        (e.g. legacy row pending ADR-0008 backfill, or vision omitted the
        field) -- must say "не оценён", never fabricate a pass/fail."""
        sticker = {**_SAMPLE_STICKER, "explicitness_score": None}
        text = _build_detail_text(sticker, "fuid", "ru", tolerance_level=0.5)
        assert "не оценён" in text
        assert "✅" not in text
        assert "❌" not in text

    def test_not_analyzed_at_all_omits_explicitness_line(self) -> None:
        """State 3: sticker never went through vision at all -- the ⏳
        not-analyzed badge already covers this; no redundant explicitness
        line (keeps the card from bloating per the source plan)."""
        sticker = {
            **_SAMPLE_STICKER,
            "visual_description": None,
            "explicitness_score": None,
        }
        text = _build_detail_text(sticker, "fuid", "ru", tolerance_level=0.5)
        assert "Оценка откровенности" not in text
        assert "⏳" in text

    def test_missing_explicitness_score_key_treated_as_unscored(self) -> None:
        """A sticker row from before this column existed (or a dict that
        just doesn't carry the key) must not KeyError."""
        sticker = {k: v for k, v in _SAMPLE_STICKER.items() if k != "explicitness_score"}
        text = _build_detail_text(sticker, "fuid", "ru", tolerance_level=0.5)
        assert "не оценён" in text


# ---------------------------------------------------------------------------
# handle_sticker_detail — explicitness line wired end-to-end (A-1)
# ---------------------------------------------------------------------------


class TestHandleStickerDetailExplicitnessLine:
    @pytest.mark.asyncio()
    async def test_shows_explicitness_line_using_resolved_default(self) -> None:
        cb = _make_callback("adm_stk_view:ru:AgADvh4AAlkbCFI")
        sticker = {**_SAMPLE_STICKER, "explicitness_score": 0.3}
        sticker_repo = _make_sticker_repo(sticker=sticker)
        bot_config_repo = _make_bot_config_repo(tolerance_level=0.6)

        await handle_sticker_detail(cb, sticker_repo, bot_config_repo)

        text = cb.message.answer.call_args[0][0]
        assert "0.30" in text
        assert "0.60" in text
        assert "✅ пройдёт" in text
