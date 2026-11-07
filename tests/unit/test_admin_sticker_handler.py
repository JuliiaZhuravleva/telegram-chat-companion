"""Admin sticker handlers: the pack catalogue, a sticker's card, its sub-screens.

Every handler runs through the real screen layer (`show()` and the builders),
so the assertions read what the admin would see: the text Telegram receives
and the buttons under it. Only the stale-button screen is patched — it renders
the hub, which belongs to another module.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from structlog.testing import capture_logs

from src.bot.handlers.admin_sticker import (
    _extract_file_unique_id_from_reply,
    _resolve_default_tolerance_level,
    handle_admin_sticker_check,
    handle_admin_sticker_dm_analyze,
    handle_admin_sticker_reply,
    handle_clear,
    handle_clear_ask,
    handle_run_analysis,
    handle_sticker_card,
    handle_sticker_explicitness_cancel,
    handle_sticker_explicitness_edit_prompt,
    handle_sticker_explicitness_input,
    handle_sticker_explicitness_preset,
    handle_sticker_explicitness_reset,
    handle_sticker_pack,
    handle_sticker_score,
    handle_sticker_set_legacy,
    handle_sticker_sets,
    handle_sticker_view,
)
from src.bot.handlers.admin_sticker import router as admin_sticker_router
from src.bot.screens.model import Screen
from src.bot.screens.render import render_text, to_markup
from src.bot.screens.stickers import (
    ERROR_REASONS,
    PRESETS,
    PackPage,
    PackRow,
    SetsPage,
    StickerFacts,
    build_card_sticker,
    build_stk_card,
    build_stk_clear_ask,
    build_stk_error,
    build_stk_score,
    build_stk_score_in,
    build_stk_set,
    build_stk_sets,
    build_stk_unknown,
    facts_from_row,
    pack_key,
    score_line,
)
from src.bot.states.admin import AdminStates
from src.services.ai.base import EmbeddingResult, VisionResult
from src.services.modules.sticker.learning import StickerLearningService
from src.services.modules.sticker.models import ReanalyzeResult, StickerLearningResult
from src.utils.telegram import TelegramFileError

MOD = "src.bot.handlers.admin_sticker"
ADMIN = 12345
FUID = "AgADvh4AAlkbCFI"
PACK = "test_set"
KEY = pack_key(PACK)
PACK_ROW: dict[str, Any] = {"set_name": PACK, "set_title": "Test Pack", "total_count": 5}

Buttons = list[tuple[str, str | None]]


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _row(**overrides: Any) -> dict[str, Any]:
    """One ``sticker_knowledge`` row, analysed, automatically scored."""
    row: dict[str, Any] = {
        "file_id": "CAACAgIAAxkB",
        "file_unique_id": FUID,
        "set_name": PACK,
        "visual_description": "happy cat waving a paw",
        "emotion": "joy",
        "character_or_meme": None,
        "suggested_contexts": None,
        "total_uses": 5,
        "bot_uses": 2,
        "emoji": "😺",
        "analysis_failed": False,
        "explicitness_score": 0.25,
        "explicitness_is_manual": False,
    }
    row.update(overrides)
    return row


def _state(current: str | None = None, data: dict[str, Any] | None = None) -> MagicMock:
    state = MagicMock()
    state.get_state = AsyncMock(return_value=current)
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value=data or {})
    return state


def _sent(message_id: int) -> MagicMock:
    """A message the bot has just sent into the admin's DM."""
    msg = MagicMock()
    msg.message_id = message_id
    msg.chat.id = ADMIN
    return msg


def _callback(data: str, *, message_id: int = 200, reply_to: Any = None) -> MagicMock:
    """A tap on a button under message ``message_id`` in the admin's DM.

    ``reply_to``: what that message replies to — None for a catalogue card,
    the admin's sticker for a notification or a DM check.
    """
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = ADMIN
    cb.answer = AsyncMock()
    msg = MagicMock(spec=Message)
    msg.chat = MagicMock()
    msg.chat.id = ADMIN
    msg.chat.type = "private"
    msg.message_id = message_id
    msg.reply_to_message = reply_to
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    msg.answer = AsyncMock(return_value=_sent(message_id + 2))
    msg.answer_sticker = AsyncMock(return_value=_sent(message_id + 1))
    cb.message = msg
    cb.bot = MagicMock()
    cb.bot.delete_message = AsyncMock()
    return cb


def _sticker_repo(
    row: dict[str, Any] | None = None,
    *,
    missing: bool = False,
    pack: dict[str, Any] | None = PACK_ROW,
    sets: list[dict[str, Any]] | None = None,
    stickers: list[dict[str, Any]] | None = None,
    card_sticker_msg: int | None = None,
) -> MagicMock:
    repo = MagicMock()
    repo.get_by_file_unique_id = AsyncMock(return_value=None if missing else (row or _row()))
    repo.get_sticker_set = AsyncMock(return_value=pack)
    repo.get_set_by_key = AsyncMock(return_value=pack)
    repo.count_sets = AsyncMock(return_value=len(sets or []))
    repo.get_all_sets_with_stats = AsyncMock(return_value=list(sets or []))
    repo.count_stickers_in_set = AsyncMock(return_value=len(stickers or []))
    repo.get_stickers_in_set = AsyncMock(return_value=list(stickers or []))
    repo.get_card_sticker_msg = AsyncMock(return_value=card_sticker_msg)
    repo.release_card = AsyncMock()
    repo.save_notification = AsyncMock(return_value=1)
    repo.clear_analysis = AsyncMock()
    repo.set_manual_explicitness_score = AsyncMock(return_value=True)
    repo.update_explicitness_score = AsyncMock()
    repo.reset_explicitness_to_auto = AsyncMock(return_value=True)
    repo.get_notification_by_reply = AsyncMock(return_value=None)
    repo.upsert_sticker_set = AsyncMock()
    return repo


def _admin_repo(lang: str = "ru", sticker: str = "on") -> MagicMock:
    repo = MagicMock()
    repo.get_admin_language = AsyncMock(return_value=lang)
    repo.get_notification_settings = AsyncMock(return_value={"sticker": sticker})
    return repo


def _bot_config_repo(
    admin_ids: str = str(ADMIN), tolerance_level: float | None = None
) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=admin_ids)
    defaults = {} if tolerance_level is None else {"tolerance_level": tolerance_level}
    repo.get_defaults = AsyncMock(return_value=defaults)
    return repo


def _service(
    *,
    reanalyze: ReanalyzeResult | None = None,
    learn: StickerLearningResult | None = None,
) -> MagicMock:
    svc = MagicMock()
    svc.reanalyze = AsyncMock(return_value=reanalyze or ReanalyzeResult(ok=True))
    svc.learn = AsyncMock(
        return_value=learn
        or StickerLearningResult(
            is_new=True, file_unique_id=FUID, visual_description="happy cat waving a paw"
        )
    )
    svc.merge_admin_description = AsyncMock(return_value="Updated desc")
    return svc


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    return bot


@pytest.fixture()
def stale():
    with patch(f"{MOD}.show_stale", new=AsyncMock()) as mock:
        yield mock


# ---------------------------------------------------------------------------
# Reading what the admin sees
# ---------------------------------------------------------------------------


def _buttons(markup: InlineKeyboardMarkup | None) -> Buttons:
    if markup is None:
        return []
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def _edit(cb: MagicMock, index: int = -1) -> tuple[str, Buttons]:
    """The screen the button's message was edited into (``index``-th edit)."""
    call = cb.message.edit_text.call_args_list[index]
    return call.args[0], _buttons(call.kwargs.get("reply_markup"))


def _new(mock: AsyncMock, index: int = -1) -> tuple[str, Buttons]:
    """The screen sent as a new message through ``answer``/``reply``."""
    call = mock.call_args_list[index]
    return call.args[0], _buttons(call.kwargs.get("reply_markup"))


def _callbacks(buttons: Buttons) -> list[str | None]:
    return [data for _, data in buttons]


CARD_BUTTONS: Buttons = [
    ("🔁 Разобрать заново", f"adm_stk_reanalyze:ru:{FUID}"),
    ("🧹 Очистить", f"adm_stk_clr_ask:ru:{FUID}"),
    ("🔞 Откровенность", f"adm_stk_score:ru:{FUID}"),
    ("⬅️ Test Pack", f"adm_stk_pk:ru:{KEY}:0"),
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _reply_target(text: str | None = None, caption: str | None = None) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.message_id = 100
    return msg


class TestExtractFileUniqueIdFromReply:
    def test_extracts_id_from_text(self) -> None:
        reply = _reply_target(text="🆔 AgADvh4AAlkbCFI\nОписание: test")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"

    def test_extracts_id_from_caption(self) -> None:
        reply = _reply_target(caption="🆔 AgADvh4AAlkbCFI")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"

    def test_prefers_text_over_caption(self) -> None:
        reply = _reply_target(text="🆔 ID_FROM_TEXT", caption="🆔 ID_FROM_CAPTION")
        assert _extract_file_unique_id_from_reply(reply) == "ID_FROM_TEXT"

    def test_returns_none_when_no_id(self) -> None:
        assert _extract_file_unique_id_from_reply(_reply_target(text="no id here")) is None

    def test_returns_none_when_both_none(self) -> None:
        assert _extract_file_unique_id_from_reply(_reply_target()) is None

    def test_handles_id_with_dashes_and_underscores(self) -> None:
        reply = _reply_target(text="🆔 AgAD-vh_4AA-lkb")
        assert _extract_file_unique_id_from_reply(reply) == "AgAD-vh_4AA-lkb"

    def test_handles_no_space_after_emoji(self) -> None:
        reply = _reply_target(text="🆔AgADvh4AAlkbCFI")
        assert _extract_file_unique_id_from_reply(reply) == "AgADvh4AAlkbCFI"


class TestResolveDefaultToleranceLevel:
    @pytest.mark.asyncio()
    async def test_falls_back_to_chatconfig_dataclass_default(self) -> None:
        assert await _resolve_default_tolerance_level(_bot_config_repo()) == 0.5

    @pytest.mark.asyncio()
    async def test_uses_admin_set_default(self) -> None:
        repo = _bot_config_repo(tolerance_level=0.2)
        assert await _resolve_default_tolerance_level(repo) == 0.2

    @pytest.mark.asyncio()
    async def test_coerces_to_float(self) -> None:
        """bot_config values round-trip through JSON: a whole number may come back an int."""
        repo = MagicMock()
        repo.get_defaults = AsyncMock(return_value={"tolerance_level": 1})
        result = await _resolve_default_tolerance_level(repo)
        assert result == 1.0
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# A reply to a sticker card corrects the description
# ---------------------------------------------------------------------------


def _admin_reply(
    text: str = "better description",
    reply_text: str | None = f"🆔 {FUID}",
    reply_caption: str | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat.id = ADMIN
    msg.from_user.id = ADMIN
    msg.reply = AsyncMock()
    msg.reply_to_message = _reply_target(text=reply_text, caption=reply_caption)
    return msg


async def _run_reply(
    msg: MagicMock,
    repo: MagicMock,
    svc: MagicMock,
    *,
    lang: str = "ru",
    bot: MagicMock | None = None,
    thread: int | None = None,
) -> None:
    await handle_admin_sticker_reply(
        msg,
        repo,
        svc,
        _admin_repo(lang),
        _bot_config_repo(),
        bot or _bot(),
        message_thread_id=thread,
    )


class TestHandleAdminStickerReply:
    @pytest.mark.asyncio()
    async def test_the_remembered_card_wins_over_the_text(self) -> None:
        repo = _sticker_repo()
        repo.get_notification_by_reply.return_value = {"file_unique_id": FUID}
        svc = _service()
        msg = _admin_reply(reply_text="🆔 SomeOtherId")

        await _run_reply(msg, repo, svc)

        repo.get_notification_by_reply.assert_awaited_once_with(ADMIN, 100)
        svc.merge_admin_description.assert_awaited_once_with(FUID, "better description")
        text = msg.reply.call_args.args[0]
        assert "✓ <b>Описание обновлено</b>" in text
        assert "Updated desc" in text
        assert msg.reply.call_args.kwargs == {"parse_mode": "HTML"}

    @pytest.mark.asyncio()
    async def test_text_fallback_when_no_card_is_remembered(self) -> None:
        svc = _service()
        await _run_reply(_admin_reply(reply_text="🆔 FallbackID123\nold"), _sticker_repo(), svc)
        svc.merge_admin_description.assert_awaited_once_with("FallbackID123", "better description")

    @pytest.mark.asyncio()
    async def test_caption_fallback_for_a_collage(self) -> None:
        svc = _service()
        msg = _admin_reply(reply_text=None, reply_caption="🆔 CaptionID456")
        await _run_reply(msg, _sticker_repo(), svc)
        svc.merge_admin_description.assert_awaited_once_with("CaptionID456", "better description")

    @pytest.mark.asyncio()
    async def test_a_reply_to_anything_else_says_the_sticker_is_unclear(self) -> None:
        """The update is consumed here, so silence would be the only answer it ever gets."""
        svc = _service()
        msg = _admin_reply(reply_text="No ID here")
        await _run_reply(msg, _sticker_repo(), svc)
        svc.merge_admin_description.assert_not_awaited()
        msg.reply.assert_awaited_once()
        text = msg.reply.call_args.args[0]
        assert text == (
            "⚠️ <b>Описание не обновлено</b>\n\n"
            "Не получилось понять, о каком стикере речь. "
            "Ответьте на карточку стикера или пришлите сам стикер."
        )
        assert msg.reply.call_args.kwargs == {"parse_mode": "HTML"}

    @pytest.mark.asyncio()
    async def test_blank_text_is_ignored(self) -> None:
        svc = _service()
        await _run_reply(_admin_reply(text="   "), _sticker_repo(), svc)
        svc.merge_admin_description.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_merge_without_result_says_the_note_is_kept(self) -> None:
        svc = _service()
        svc.merge_admin_description.return_value = None
        msg = _admin_reply()

        await _run_reply(msg, _sticker_repo(), svc)

        text = msg.reply.call_args.args[0]
        assert "⚠️ <b>Описание не обновлено</b>" in text
        assert "заметка сохранена" in text
        assert "«🔁 Разобрать заново»" in text

    @pytest.mark.asyncio()
    async def test_merge_crash_says_the_note_is_kept(self) -> None:
        svc = _service()
        svc.merge_admin_description.side_effect = RuntimeError("boom")
        msg = _admin_reply()

        await _run_reply(msg, _sticker_repo(), svc)

        assert "заметка сохранена" in msg.reply.call_args.args[0]

    @pytest.mark.asyncio()
    async def test_content_refusal_points_at_the_manual_score(self) -> None:
        svc = _service()
        svc.merge_admin_description.side_effect = ValueError("content_filter")
        msg = _admin_reply()

        await _run_reply(msg, _sticker_repo(), svc)

        text = msg.reply.call_args.args[0]
        assert "отказалась" in text
        assert "🔞 Откровенность" in text

    @pytest.mark.asyncio()
    async def test_other_value_error_says_try_again(self) -> None:
        svc = _service()
        svc.merge_admin_description.side_effect = ValueError("something else")
        msg = _admin_reply()

        await _run_reply(msg, _sticker_repo(), svc)

        assert "Не получилось обновить описание" in msg.reply.call_args.args[0]

    @pytest.mark.asyncio()
    async def test_the_new_description_is_escaped(self) -> None:
        svc = _service()
        svc.merge_admin_description.return_value = "<b>cat</b> & dog"
        msg = _admin_reply()

        await _run_reply(msg, _sticker_repo(), svc)

        assert "&lt;b&gt;cat&lt;/b&gt; &amp; dog" in msg.reply.call_args.args[0]

    @pytest.mark.asyncio()
    async def test_answers_in_the_admin_language(self) -> None:
        msg = _admin_reply()
        await _run_reply(msg, _sticker_repo(), _service(), lang="en")
        assert "Description updated" in msg.reply.call_args.args[0]


class TestHandleAdminStickerReplyTypingIndicator:
    """The merge is an LLM call: it runs under the shared typing indicator."""

    @pytest.mark.asyncio()
    async def test_wraps_merge_call(self) -> None:
        bot = _bot()
        msg = _admin_reply()
        svc = _service()
        with patch(f"{MOD}.typing_indicator") as indicator:
            indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            indicator.return_value.__aexit__ = AsyncMock(return_value=False)
            await _run_reply(msg, _sticker_repo(), svc, bot=bot)

        indicator.assert_called_once_with(bot, ADMIN, None)
        svc.merge_admin_description.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_forwards_message_thread_id(self) -> None:
        bot = _bot()
        with patch(f"{MOD}.typing_indicator") as indicator:
            indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            indicator.return_value.__aexit__ = AsyncMock(return_value=False)
            await _run_reply(_admin_reply(), _sticker_repo(), _service(), bot=bot, thread=777)

        indicator.assert_called_once_with(bot, ADMIN, 777)

    @pytest.mark.asyncio()
    async def test_no_indicator_when_no_sticker_is_addressed(self) -> None:
        with patch(f"{MOD}.typing_indicator") as indicator:
            await _run_reply(_admin_reply(reply_text="No ID here"), _sticker_repo(), _service())
        indicator.assert_not_called()


# ---------------------------------------------------------------------------
# The pack list and one pack
# ---------------------------------------------------------------------------

_SETS = [
    {
        "set_name": PACK,
        "set_title": "Test Pack",
        "total_count": 5,
        "learned_count": 4,
        "analyzed_count": 3,
    }
]


class TestHandleStickerSets:
    @pytest.mark.asyncio()
    async def test_lists_packs_with_the_analysed_count(self) -> None:
        cb = _callback("adm_stk_sets:ru:0")
        repo = _sticker_repo(sets=_SETS)

        await handle_sticker_sets(cb, repo, _state())

        text, buttons = _edit(cb)
        assert "Паки стикеров" in text
        assert buttons[0] == ("Test Pack (3 из 5)", f"adm_stk_pk:ru:{KEY}:0")
        assert buttons[-1] == ("⬅️ Панель", "adm_menu:ru")
        repo.get_all_sets_with_stats.assert_awaited_once_with(limit=4, offset=0)
        cb.message.delete.assert_not_awaited()
        cb.message.answer.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_a_page_past_the_end_is_brought_back_in_range(self) -> None:
        cb = _callback("adm_stk_sets:ru:9")
        repo = _sticker_repo(sets=_SETS)

        await handle_sticker_sets(cb, repo, _state())

        repo.get_all_sets_with_stats.assert_awaited_once_with(limit=4, offset=0)

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("data", ["adm_stk_sets:ru", "adm_stk_sets:ru:x", "adm_stk_sets:"])
    async def test_unparseable_payload_is_stale(self, stale: AsyncMock, data: str) -> None:
        cb = _callback(data)
        state = _state()

        await handle_sticker_sets(cb, _sticker_repo(), state, foo="bar")

        stale.assert_awaited_once()
        assert stale.call_args.kwargs == {"data": {"foo": "bar"}, "state": state}
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_disarms_an_open_score_prompt(self) -> None:
        state = _state(current=AdminStates.awaiting_sticker_score.state)
        await handle_sticker_sets(_callback("adm_stk_sets:ru:0"), _sticker_repo(), state)
        state.clear.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_leaves_another_screens_prompt_alone(self) -> None:
        state = _state(current="AdminStates:awaiting_tolerance")
        await handle_sticker_sets(_callback("adm_stk_sets:ru:0"), _sticker_repo(), state)
        state.clear.assert_not_awaited()


class TestLeavingACard:
    """A card opened from a pack takes its sticker with it; a reply card does not."""

    @pytest.mark.asyncio()
    async def test_a_catalogue_card_removes_its_own_sticker(self) -> None:
        cb = _callback("adm_stk_sets:ru:0")
        repo = _sticker_repo(card_sticker_msg=199)

        await handle_sticker_sets(cb, repo, _state())

        repo.get_card_sticker_msg.assert_awaited_once_with(ADMIN, 200)
        cb.bot.delete_message.assert_awaited_once_with(chat_id=ADMIN, message_id=199)
        repo.release_card.assert_awaited_once_with(ADMIN, 200)

    @pytest.mark.asyncio()
    async def test_a_notification_card_keeps_the_sticker_it_replies_to(self) -> None:
        cb = _callback("adm_stk_sets:ru:0", reply_to=MagicMock())
        repo = _sticker_repo(card_sticker_msg=199)

        await handle_sticker_sets(cb, repo, _state())

        cb.bot.delete_message.assert_not_awaited()
        repo.release_card.assert_awaited_once_with(ADMIN, 200)

    @pytest.mark.asyncio()
    async def test_a_message_that_is_no_card_deletes_nothing(self) -> None:
        cb = _callback("adm_stk_sets:ru:0")
        repo = _sticker_repo(card_sticker_msg=None)

        await handle_sticker_sets(cb, repo, _state())

        cb.bot.delete_message.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_a_record_failure_still_shows_the_list(self) -> None:
        cb = _callback("adm_stk_sets:ru:0")
        repo = _sticker_repo()
        repo.get_card_sticker_msg.side_effect = RuntimeError("db")

        await handle_sticker_sets(cb, repo, _state())

        assert "Паки стикеров" in _edit(cb)[0]


class TestHandleStickerPack:
    @pytest.mark.asyncio()
    async def test_opens_the_pack_by_key(self) -> None:
        cb = _callback(f"adm_stk_pk:ru:{KEY}:0")
        repo = _sticker_repo(stickers=[_row()])

        await handle_sticker_pack(cb, repo, _state())

        repo.get_set_by_key.assert_awaited_once_with(KEY)
        repo.get_stickers_in_set.assert_awaited_once_with(PACK, limit=4, offset=0)
        text, buttons = _edit(cb)
        assert "<b>Test Pack</b> · 1 стикер" in text
        assert buttons[0][1] == f"adm_stk_view:ru:{FUID}"
        assert buttons[0][0].endswith("happy cat waving a paw · 5")
        assert buttons[-1] == ("⬅️ Паки", "adm_stk_sets:ru:0")

    @pytest.mark.asyncio()
    async def test_a_pack_that_is_gone_says_so_on_the_pack_list(self) -> None:
        cb = _callback(f"adm_stk_pk:ru:{KEY}:0")
        repo = _sticker_repo(pack=None, sets=_SETS)

        await handle_sticker_pack(cb, repo, _state())

        text, _ = _edit(cb)
        assert "Этого пака уже нет в списке." in text
        assert "Паки стикеров" in text
        assert "✓" not in text

    @pytest.mark.asyncio()
    @pytest.mark.parametrize(
        "data",
        [
            "adm_stk_pk:ru:NOTAHEXKEY12:0",
            "adm_stk_pk:ru:abc:0",
            f"adm_stk_pk:ru:{KEY}:x",
            f"adm_stk_pk:ru:{KEY}",
        ],
    )
    async def test_unparseable_payload_is_stale(self, stale: AsyncMock, data: str) -> None:
        repo = _sticker_repo()
        await handle_sticker_pack(_callback(data), repo, _state())
        stale.assert_awaited_once()
        repo.get_set_by_key.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_a_button_from_before_keys_still_opens_the_pack(self) -> None:
        cb = _callback(f"adm_stk_set:ru:{PACK}:0")
        repo = _sticker_repo(stickers=[_row()])

        await handle_sticker_set_legacy(cb, repo, _state())

        repo.get_sticker_set.assert_awaited_once_with(PACK)
        assert "<b>Test Pack</b>" in _edit(cb)[0]

    @pytest.mark.asyncio()
    async def test_a_legacy_button_with_a_broken_page_is_stale(self, stale: AsyncMock) -> None:
        await handle_sticker_set_legacy(
            _callback(f"adm_stk_set:ru:{PACK}:x"), _sticker_repo(), _state()
        )
        stale.assert_awaited_once()


# ---------------------------------------------------------------------------
# A sticker's card
# ---------------------------------------------------------------------------


class TestHandleStickerView:
    @pytest.mark.asyncio()
    async def test_the_list_gives_way_to_the_sticker_and_its_card(self) -> None:
        cb = _callback(f"adm_stk_view:ru:{FUID}")
        repo = _sticker_repo()

        await handle_sticker_view(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        cb.answer.assert_awaited_once_with()
        cb.message.delete.assert_awaited_once()
        cb.message.answer_sticker.assert_awaited_once_with("CAACAgIAAxkB")
        cb.message.edit_text.assert_not_awaited()
        text, buttons = _new(cb.message.answer)
        assert "<b>happy cat waving a paw</b>" in text
        assert "Откровенность: 0.25 · пройдёт в чате с допуском 0.50" in text
        assert buttons == CARD_BUTTONS
        repo.save_notification.assert_awaited_once_with(
            file_unique_id=FUID, admin_id=ADMIN, message_id=202, sticker_msg_id=201, chat_id=ADMIN
        )

    @pytest.mark.asyncio()
    async def test_a_sticker_without_a_pack_goes_back_to_the_pack_list(self) -> None:
        cb = _callback(f"adm_stk_view:ru:{FUID}")
        repo = _sticker_repo(pack=None)

        await handle_sticker_view(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        _, buttons = _new(cb.message.answer)
        assert buttons[-1] == ("⬅️ Паки", "adm_stk_sets:ru:0")

    @pytest.mark.asyncio()
    async def test_a_sticker_that_is_gone_keeps_the_list_and_says_so(self) -> None:
        cb = _callback(f"adm_stk_view:ru:{FUID}")
        repo = _sticker_repo(missing=True)

        await handle_sticker_view(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        cb.message.delete.assert_not_awaited()
        cb.message.answer_sticker.assert_not_awaited()
        assert "Этого стикера уже нет в базе." in _edit(cb)[0]
        repo.save_notification.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_a_sticker_telegram_refuses_still_gets_its_card(self) -> None:
        cb = _callback(f"adm_stk_view:ru:{FUID}")
        cb.message.answer_sticker.side_effect = TelegramBadRequest(
            method=MagicMock(), message="wrong file identifier"
        )
        repo = _sticker_repo()

        await handle_sticker_view(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        assert "happy cat" in _new(cb.message.answer)[0]
        assert repo.save_notification.call_args.kwargs["sticker_msg_id"] == 0

    @pytest.mark.asyncio()
    async def test_unparseable_payload_is_stale(self, stale: AsyncMock) -> None:
        await handle_sticker_view(
            _callback("adm_stk_view:ru"),
            _sticker_repo(),
            _admin_repo(),
            _bot_config_repo(),
            _state(),
        )
        stale.assert_awaited_once()


class TestHandleStickerCard:
    @pytest.mark.asyncio()
    async def test_back_to_the_card_in_the_same_message(self) -> None:
        cb = _callback(f"adm_stk_card:ru:{FUID}")
        repo = _sticker_repo()

        await handle_sticker_card(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        text, buttons = _edit(cb)
        assert "<b>happy cat waving a paw</b>" in text
        assert buttons == CARD_BUTTONS
        cb.message.answer.assert_not_awaited()
        repo.save_notification.assert_awaited_once_with(
            file_unique_id=FUID, admin_id=ADMIN, message_id=200, sticker_msg_id=0, chat_id=ADMIN
        )

    @pytest.mark.asyncio()
    async def test_english(self) -> None:
        cb = _callback(f"adm_stk_card:en:{FUID}")

        await handle_sticker_card(cb, _sticker_repo(), _admin_repo(), _bot_config_repo(), _state())

        text, buttons = _edit(cb)
        assert "Explicitness: 0.25" in text
        assert buttons[0] == ("🔁 Re-analyse", f"adm_stk_reanalyze:en:{FUID}")

    @pytest.mark.asyncio()
    async def test_detailed_mode_shows_the_technical_lines(self) -> None:
        cb = _callback(f"adm_stk_card:ru:{FUID}")
        admin_repo = _admin_repo(sticker="detailed")

        await handle_sticker_card(cb, _sticker_repo(), admin_repo, _bot_config_repo(), _state())

        assert f"🆔 <code>{FUID}</code>" in _edit(cb)[0]

    @pytest.mark.asyncio()
    async def test_the_admin_default_tolerance_decides_the_verdict(self) -> None:
        cb = _callback(f"adm_stk_card:ru:{FUID}")
        repo = _bot_config_repo(tolerance_level=0.2)

        await handle_sticker_card(cb, _sticker_repo(), _admin_repo(), repo, _state())

        assert "0.25 · не пройдёт в чате с допуском 0.20" in _edit(cb)[0]

    @pytest.mark.asyncio()
    async def test_an_unrated_sticker_says_the_bot_will_not_send_it(self) -> None:
        cb = _callback(f"adm_stk_card:ru:{FUID}")
        repo = _sticker_repo(_row(explicitness_score=None))

        await handle_sticker_card(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        assert "не оценена · бот его не отправит" in _edit(cb)[0]

    @pytest.mark.asyncio()
    async def test_gone(self) -> None:
        cb = _callback(f"adm_stk_card:ru:{FUID}")
        repo = _sticker_repo(missing=True)

        await handle_sticker_card(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        assert "Этого стикера уже нет в базе." in _edit(cb)[0]
        repo.save_notification.assert_not_awaited()


# ---------------------------------------------------------------------------
# Clear the analysis
# ---------------------------------------------------------------------------


class TestHandleClear:
    @pytest.mark.asyncio()
    async def test_asks_in_place(self) -> None:
        cb = _callback(f"adm_stk_clr_ask:ru:{FUID}")

        await handle_clear_ask(cb, _state())

        text, buttons = _edit(cb)
        assert "Очистить разбор?" in text
        assert buttons == [
            ("🧹 Очистить", f"adm_stk_clr:ru:{FUID}"),
            ("⬅️ Не очищать", f"adm_stk_card:ru:{FUID}"),
        ]

    @pytest.mark.asyncio()
    async def test_keep_it_edits_the_card_back_without_resending(self) -> None:
        """The way back from the question is the card, in the same message."""
        cb = _callback(f"adm_stk_card:ru:{FUID}")

        await handle_sticker_card(cb, _sticker_repo(), _admin_repo(), _bot_config_repo(), _state())

        cb.message.delete.assert_not_awaited()
        cb.message.answer.assert_not_awaited()
        cb.message.answer_sticker.assert_not_awaited()
        assert "<b>happy cat waving a paw</b>" in _edit(cb)[0]

    @pytest.mark.asyncio()
    async def test_clears_and_says_so_on_the_card(self) -> None:
        cb = _callback(f"adm_stk_clr:ru:{FUID}")
        repo = _sticker_repo()

        await handle_clear(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        repo.clear_analysis.assert_awaited_once_with(FUID)
        text, _ = _edit(cb)
        assert text.startswith("<i>✓ Разбор очищен</i>")
        repo.save_notification.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_a_sticker_that_is_gone_is_not_cleared(self) -> None:
        cb = _callback(f"adm_stk_clr:ru:{FUID}")
        repo = _sticker_repo(missing=True)

        await handle_clear(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        repo.clear_analysis.assert_not_awaited()
        assert "Этого стикера уже нет в базе." in _edit(cb)[0]

    @pytest.mark.asyncio()
    async def test_unparseable_payloads_are_stale(self, stale: AsyncMock) -> None:
        await handle_clear_ask(_callback("adm_stk_clr_ask:ru"), _state())
        await handle_clear(
            _callback("adm_stk_clr:ru"),
            _sticker_repo(),
            _admin_repo(),
            _bot_config_repo(),
            _state(),
        )
        assert stale.await_count == 2


# ---------------------------------------------------------------------------
# Explicitness
# ---------------------------------------------------------------------------


class TestHandleStickerScore:
    @pytest.mark.asyncio()
    async def test_the_score_screen(self) -> None:
        cb = _callback(f"adm_stk_score:ru:{FUID}")

        await handle_sticker_score(cb, _sticker_repo(), _admin_repo(), _bot_config_repo(), _state())

        text, buttons = _edit(cb)
        assert "Сейчас 0.25, посчитал бот." in text
        assert _callbacks(buttons) == [
            *(f"adm_stk_expset:ru:{FUID}:{i}" for i in range(len(PRESETS))),
            f"adm_stk_expedit:ru:{FUID}",
            f"adm_stk_expreset:ru:{FUID}",
            f"adm_stk_card:ru:{FUID}",
        ]
        labels = [label for label, _ in buttons]
        assert labels[1].endswith("• 0.25"), "the current value is marked"
        assert labels[0] == "0.00"
        assert labels[-2:] == ["🔁 Пересчитать оценку", "⬅️ Стикер"]

    @pytest.mark.asyncio()
    async def test_gone(self) -> None:
        cb = _callback(f"adm_stk_score:ru:{FUID}")
        await handle_sticker_score(
            cb, _sticker_repo(missing=True), _admin_repo(), _bot_config_repo(), _state()
        )
        assert "Этого стикера уже нет в базе." in _edit(cb)[0]


class TestHandleStickerExplicitnessPreset:
    @pytest.mark.asyncio()
    async def test_one_tap_sets_the_value_and_the_card_says_so(self) -> None:
        cb = _callback(f"adm_stk_expset:ru:{FUID}:3")
        repo = _sticker_repo()
        state = _state(current=AdminStates.awaiting_sticker_score.state)

        await handle_sticker_explicitness_preset(cb, repo, _admin_repo(), _bot_config_repo(), state)

        state.clear.assert_awaited_once()
        repo.set_manual_explicitness_score.assert_awaited_once_with(FUID, 0.75)
        text, buttons = _edit(cb)
        assert text.startswith("<i>✓ Откровенность 0.75</i>")
        assert buttons == CARD_BUTTONS
        cb.answer.assert_awaited_once_with()
        repo.save_notification.assert_awaited_once()

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("tail", ["5", "x", "-1", ""])
    async def test_an_index_outside_the_presets_is_stale(self, stale: AsyncMock, tail: str) -> None:
        repo = _sticker_repo()
        await handle_sticker_explicitness_preset(
            _callback(f"adm_stk_expset:ru:{FUID}:{tail}"),
            repo,
            _admin_repo(),
            _bot_config_repo(),
            _state(),
        )
        stale.assert_awaited_once()
        repo.set_manual_explicitness_score.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_gone(self) -> None:
        cb = _callback(f"adm_stk_expset:ru:{FUID}:0")
        repo = _sticker_repo()
        repo.set_manual_explicitness_score.return_value = False

        await handle_sticker_explicitness_preset(
            cb, repo, _admin_repo(), _bot_config_repo(), _state()
        )

        assert "Этого стикера уже нет в базе." in _edit(cb)[0]


class TestHandleStickerExplicitnessEditPrompt:
    @pytest.mark.asyncio()
    async def test_the_prompt_replaces_the_score_screen(self) -> None:
        cb = _callback(f"adm_stk_expedit:ru:{FUID}")
        state = _state()

        await handle_sticker_explicitness_edit_prompt(
            cb, _sticker_repo(), _admin_repo(), _bot_config_repo(), state
        )

        state.set_state.assert_awaited_once_with(AdminStates.awaiting_sticker_score)
        assert state.update_data.await_args_list[0].kwargs == {
            "exp_file_unique_id": FUID,
            "exp_lang": "ru",
            "exp_sticker_msg": 0,
        }
        assert state.update_data.await_args_list[1].kwargs == {"exp_prompt_id": 200}
        text, buttons = _edit(cb)
        assert "Напишите число от 0.0 до 1.0." in text
        assert buttons == [("✖️ Отмена", f"adm_stk_expcancel:ru:{FUID}")]

    @pytest.mark.asyncio()
    async def test_gone_does_not_arm_the_prompt(self) -> None:
        cb = _callback(f"adm_stk_expedit:ru:{FUID}")
        state = _state()

        await handle_sticker_explicitness_edit_prompt(
            cb, _sticker_repo(missing=True), _admin_repo(), _bot_config_repo(), state
        )

        state.set_state.assert_not_awaited()
        assert "Этого стикера уже нет в базе." in _edit(cb)[0]


def _typed(text: str, *, user_id: int = ADMIN) -> MagicMock:
    """What the admin typed into the score prompt."""
    msg = MagicMock()
    msg.text = text
    msg.from_user.id = user_id
    msg.date = datetime(2026, 1, 1, tzinfo=UTC)
    msg.chat = Chat(id=ADMIN, type="private")
    msg.bot = MagicMock()
    msg.answer = AsyncMock(return_value=_sent(301))
    return msg


_ARMED = {"exp_file_unique_id": FUID, "exp_lang": "ru", "exp_prompt_id": 250}


@pytest.fixture()
def strip():
    """The ✖️ coming off the prompt the admin has just answered."""
    with patch(f"{MOD}.safe_edit_reply_markup", new=AsyncMock()) as mock:
        yield mock


class TestHandleStickerExplicitnessInput:
    @pytest.mark.asyncio()
    async def test_a_valid_value_is_saved_and_the_card_is_one_new_message(
        self, strip: AsyncMock
    ) -> None:
        msg = _typed("0.42")
        state = _state(data=dict(_ARMED))
        repo = _sticker_repo()

        await handle_sticker_explicitness_input(msg, state, repo, _admin_repo(), _bot_config_repo())

        state.clear.assert_awaited_once()
        repo.set_manual_explicitness_score.assert_awaited_once_with(FUID, 0.42)
        msg.answer.assert_awaited_once()
        text, buttons = _new(msg.answer)
        assert text.startswith("<i>✓ Откровенность 0.42</i>")
        assert buttons == CARD_BUTTONS
        repo.save_notification.assert_awaited_once_with(
            file_unique_id=FUID, admin_id=ADMIN, message_id=301, sticker_msg_id=0, chat_id=ADMIN
        )
        strip.assert_awaited_once()
        assert strip.call_args.args[0].message_id == 250
        assert strip.call_args.kwargs == {"reply_markup": None}

    @pytest.mark.asyncio()
    async def test_comma_decimal_accepted(self, strip: AsyncMock) -> None:
        repo = _sticker_repo()
        await handle_sticker_explicitness_input(
            _typed("0,7"), _state(data=dict(_ARMED)), repo, _admin_repo(), _bot_config_repo()
        )
        repo.set_manual_explicitness_score.assert_awaited_once_with(FUID, 0.7)

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("raw", ["abc", "1.5", "-0.1", ""])
    async def test_an_invalid_value_asks_again_in_a_new_message(
        self, strip: AsyncMock, raw: str
    ) -> None:
        msg = _typed(raw)
        state = _state(data=dict(_ARMED))
        repo = _sticker_repo()

        await handle_sticker_explicitness_input(msg, state, repo, _admin_repo(), _bot_config_repo())

        state.clear.assert_not_awaited()
        repo.set_manual_explicitness_score.assert_not_awaited()
        text, buttons = _new(msg.answer)
        assert "⚠️ Нужно число от 0.0 до 1.0" in text
        assert buttons == [("✖️ Отмена", f"adm_stk_expcancel:ru:{FUID}")]
        state.update_data.assert_awaited_once_with(exp_prompt_id=301)
        assert strip.call_args.args[0].message_id == 250

    @pytest.mark.asyncio()
    async def test_no_prompt_id_strips_nothing(self, strip: AsyncMock) -> None:
        data = {"exp_file_unique_id": FUID, "exp_lang": "ru"}
        await handle_sticker_explicitness_input(
            _typed("0.3"), _state(data=data), _sticker_repo(), _admin_repo(), _bot_config_repo()
        )
        strip.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_a_prompt_already_bare_is_not_an_error(self, strip: AsyncMock) -> None:
        strip.side_effect = TelegramBadRequest(method=MagicMock(), message="message not found")
        msg = _typed("0.3")

        await handle_sticker_explicitness_input(
            msg, _state(data=dict(_ARMED)), _sticker_repo(), _admin_repo(), _bot_config_repo()
        )

        msg.answer.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_missing_data_disarms_silently(self, strip: AsyncMock) -> None:
        msg = _typed("0.3")
        state = _state(data={})

        await handle_sticker_explicitness_input(
            msg, state, _sticker_repo(), _admin_repo(), _bot_config_repo()
        )

        state.clear.assert_awaited_once()
        msg.answer.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_not_an_admin_any_more(self, strip: AsyncMock) -> None:
        msg = _typed("0.3", user_id=99999)
        state = _state(data=dict(_ARMED))
        repo = _sticker_repo()

        await handle_sticker_explicitness_input(msg, state, repo, _admin_repo(), _bot_config_repo())

        state.clear.assert_awaited_once()
        repo.set_manual_explicitness_score.assert_not_awaited()
        msg.answer.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_gone_after_a_valid_value(self, strip: AsyncMock) -> None:
        msg = _typed("0.3")
        repo = _sticker_repo()
        repo.set_manual_explicitness_score.return_value = False

        await handle_sticker_explicitness_input(
            msg, _state(data=dict(_ARMED)), repo, _admin_repo(), _bot_config_repo()
        )

        assert "Этого стикера уже нет в базе." in _new(msg.answer)[0]
        repo.save_notification.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_gone_after_an_invalid_value_disarms(self, strip: AsyncMock) -> None:
        msg = _typed("abc")
        state = _state(data=dict(_ARMED))

        await handle_sticker_explicitness_input(
            msg, state, _sticker_repo(missing=True), _admin_repo(), _bot_config_repo()
        )

        state.clear.assert_awaited_once()
        assert "Этого стикера уже нет в базе." in _new(msg.answer)[0]


class TestHandleStickerExplicitnessCancel:
    @pytest.mark.asyncio()
    async def test_back_to_the_score_screen_in_place(self) -> None:
        cb = _callback(f"adm_stk_expcancel:ru:{FUID}")
        state = _state(current=AdminStates.awaiting_sticker_score.state)

        await handle_sticker_explicitness_cancel(
            cb, _sticker_repo(), _admin_repo(), _bot_config_repo(), state
        )

        state.clear.assert_awaited_once()
        text, _ = _edit(cb)
        assert "Сейчас 0.25, посчитал бот." in text
        cb.message.answer.assert_not_awaited()


class TestRescore:
    """``🔁 Пересчитать оценку``: drop the manual value, analyse once more."""

    @pytest.mark.asyncio()
    async def test_reset_then_one_reanalysis_under_the_placeholder(self) -> None:
        cb = _callback(f"adm_stk_expreset:ru:{FUID}")
        bot = MagicMock()
        svc = _service()
        repo = _sticker_repo()
        state = _state(current=AdminStates.awaiting_sticker_score.state)

        await handle_sticker_explicitness_reset(
            cb, bot, svc, repo, _admin_repo(), _bot_config_repo(), state
        )

        state.clear.assert_awaited_once()
        repo.reset_explicitness_to_auto.assert_awaited_once_with(FUID)
        svc.reanalyze.assert_awaited_once_with(bot, FUID)
        cb.answer.assert_awaited_once_with()
        placeholder, placeholder_buttons = _edit(cb, 0)
        assert "⏳ <b>Разбираю стикер…</b>" in placeholder
        assert placeholder_buttons == []
        text, buttons = _edit(cb)
        assert text.startswith("<i>✓ Оценка пересчитана</i>")
        assert buttons == CARD_BUTTONS

    @pytest.mark.asyncio()
    async def test_gone_runs_no_analysis(self) -> None:
        cb = _callback(f"adm_stk_expreset:ru:{FUID}")
        svc = _service()
        repo = _sticker_repo()
        repo.reset_explicitness_to_auto.return_value = False

        await handle_sticker_explicitness_reset(
            cb, MagicMock(), svc, repo, _admin_repo(), _bot_config_repo(), _state()
        )

        svc.reanalyze.assert_not_awaited()
        assert "Этого стикера уже нет в базе." in _edit(cb)[0]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class TestHandleRunAnalysis:
    @pytest.mark.asyncio()
    async def test_success_brings_the_card_back_with_a_notice(self) -> None:
        cb = _callback(f"adm_stk_reanalyze:ru:{FUID}")
        bot = MagicMock()
        svc = _service()
        repo = _sticker_repo()

        await handle_run_analysis(cb, bot, svc, repo, _admin_repo(), _bot_config_repo(), _state())

        svc.reanalyze.assert_awaited_once_with(bot, FUID)
        assert "⏳" in _edit(cb, 0)[0]
        text, buttons = _edit(cb)
        assert text.startswith("<i>✓ Разбор обновлён</i>")
        assert buttons == CARD_BUTTONS
        repo.save_notification.assert_awaited_once()

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("reason", ["download", "vision", "content_filter", "empty"])
    async def test_each_failure_says_why_with_retry_and_back(self, reason: str) -> None:
        cb = _callback(f"adm_stk_reanalyze:ru:{FUID}")
        svc = _service(reanalyze=ReanalyzeResult(ok=False, reason=reason))  # type: ignore[arg-type]
        repo = _sticker_repo()

        await handle_run_analysis(
            cb, MagicMock(), svc, repo, _admin_repo(), _bot_config_repo(), _state()
        )

        text, buttons = _edit(cb)
        assert "⚠️ <b>Не получилось разобрать стикер</b>" in text
        assert ERROR_REASONS[reason]("ru") in text
        assert "Заметки на месте." in text
        expected = [("🔁 Повторить", f"adm_stk_reanalyze:ru:{FUID}")]
        if reason == "content_filter":
            expected.append(("🔞 Откровенность", f"adm_stk_score:ru:{FUID}"))
        expected.append(("⬅️ Стикер", f"adm_stk_card:ru:{FUID}"))
        assert buttons == expected
        repo.save_notification.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_gone_before_runs_nothing(self) -> None:
        cb = _callback(f"adm_stk_reanalyze:ru:{FUID}")
        svc = _service()

        await handle_run_analysis(
            cb,
            MagicMock(),
            svc,
            _sticker_repo(missing=True),
            _admin_repo(),
            _bot_config_repo(),
            _state(),
        )

        svc.reanalyze.assert_not_awaited()
        assert "Этого стикера уже нет в базе." in _edit(cb)[0]

    @pytest.mark.asyncio()
    async def test_gone_during_the_analysis(self) -> None:
        cb = _callback(f"adm_stk_reanalyze:ru:{FUID}")
        repo = _sticker_repo()
        repo.get_by_file_unique_id.side_effect = [_row(), None]

        await handle_run_analysis(
            cb, MagicMock(), _service(), repo, _admin_repo(), _bot_config_repo(), _state()
        )

        assert "Этого стикера уже нет в базе." in _edit(cb)[0]
        cb.answer.assert_awaited_once_with()

    @pytest.mark.asyncio()
    async def test_a_never_analysed_sticker_offers_analyse(self) -> None:
        cb = _callback(f"adm_stk_card:ru:{FUID}")
        repo = _sticker_repo(_row(visual_description=None, explicitness_score=None))

        await handle_sticker_card(cb, repo, _admin_repo(), _bot_config_repo(), _state())

        _, buttons = _edit(cb)
        assert buttons[0] == ("🔁 Разобрать", f"adm_stk_reanalyze:ru:{FUID}")
        assert ("🧹 Очистить", f"adm_stk_clr_ask:ru:{FUID}") not in buttons


# ---------------------------------------------------------------------------
# The admin sends a sticker straight to the bot
# ---------------------------------------------------------------------------


def _tg_sticker(fuid: str = FUID, *, set_name: str | None = PACK) -> MagicMock:
    sticker = MagicMock()
    sticker.file_unique_id = fuid
    sticker.file_id = "CAACAgIAAxkB"
    sticker.set_name = set_name
    sticker.emoji = "😺"
    sticker.is_animated = False
    sticker.is_video = False
    return sticker


def _dm_sticker(sticker: MagicMock | None = None) -> MagicMock:
    msg = MagicMock()
    msg.sticker = sticker or _tg_sticker()
    msg.chat.id = ADMIN
    msg.from_user.id = ADMIN
    msg.reply = AsyncMock(return_value=_sent(400))
    return msg


class TestHandleAdminStickerCheck:
    @pytest.mark.asyncio()
    async def test_a_known_sticker_gets_its_card_as_a_reply(self) -> None:
        msg = _dm_sticker()
        repo = _sticker_repo()

        await handle_admin_sticker_check(msg, repo, _admin_repo(), _bot_config_repo())

        text, buttons = _new(msg.reply)
        assert "<b>happy cat waving a paw</b>" in text
        assert buttons == CARD_BUTTONS
        assert msg.reply.call_args.kwargs["parse_mode"] == "HTML"
        repo.save_notification.assert_awaited_once_with(
            file_unique_id=FUID, admin_id=ADMIN, message_id=400, sticker_msg_id=0, chat_id=ADMIN
        )

    @pytest.mark.asyncio()
    async def test_an_unknown_sticker_gets_one_analyse_button(self) -> None:
        msg = _dm_sticker()
        repo = _sticker_repo(missing=True)

        await handle_admin_sticker_check(msg, repo, _admin_repo(), _bot_config_repo())

        text, buttons = _new(msg.reply)
        assert "<b>Новый стикер</b>" in text
        assert "Его ещё нет в базе." in text
        assert buttons == [("🔍 Разобрать", f"adm_stk_dmchk:ru:{FUID}")]
        repo.save_notification.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_english(self) -> None:
        msg = _dm_sticker()

        await handle_admin_sticker_check(
            msg, _sticker_repo(missing=True), _admin_repo("en"), _bot_config_repo()
        )

        text, buttons = _new(msg.reply)
        assert "<b>New sticker</b>" in text
        assert buttons == [("🔍 Analyse", f"adm_stk_dmchk:en:{FUID}")]


def _dm_tap(
    sticker: MagicMock | None = None, *, data: str = f"adm_stk_dmchk:ru:{FUID}"
) -> MagicMock:
    reply = MagicMock()
    reply.sticker = sticker or _tg_sticker()
    return _callback(data, reply_to=reply)


async def _dm_analyze(
    cb: MagicMock, svc: MagicMock, repo: MagicMock, *, bot: MagicMock | None = None
) -> None:
    await handle_admin_sticker_dm_analyze(
        cb, bot or MagicMock(), svc, repo, _admin_repo(), _bot_config_repo(), _state()
    )


@pytest.fixture()
def download():
    with patch(f"{MOD}.download_telegram_file", new=AsyncMock(return_value=b"png")) as mock:
        yield mock


class TestHandleAdminStickerDmAnalyze:
    @pytest.mark.asyncio()
    async def test_a_different_sticker_under_the_button_is_refused(self) -> None:
        cb = _dm_tap(_tg_sticker("someOtherID"))
        svc = _service()

        await _dm_analyze(cb, svc, _sticker_repo(missing=True))

        cb.answer.assert_awaited_once_with(
            "Стикер недоступен, пришлите его ещё раз", show_alert=True
        )
        svc.learn.assert_not_awaited()
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_no_sticker_under_the_button_is_refused(self) -> None:
        cb = _callback(f"adm_stk_dmchk:ru:{FUID}", reply_to=None)
        svc = _service()

        await _dm_analyze(cb, svc, _sticker_repo(missing=True))

        assert cb.answer.call_args.kwargs == {"show_alert": True}
        svc.learn.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_a_new_sticker_is_learned_into_its_card(self, download: AsyncMock) -> None:
        cb = _dm_tap()
        svc = _service()
        repo = _sticker_repo()
        repo.get_by_file_unique_id.side_effect = [None, _row()]

        await _dm_analyze(cb, svc, repo)

        svc.learn.assert_awaited_once()
        assert "⏳" in _edit(cb, 0)[0]
        text, buttons = _edit(cb)
        assert text.startswith("<i>✓ Стикер разобран</i>")
        assert buttons == CARD_BUTTONS
        repo.save_notification.assert_awaited_once_with(
            file_unique_id=FUID, admin_id=ADMIN, message_id=200, sticker_msg_id=0, chat_id=ADMIN
        )

    @pytest.mark.asyncio()
    async def test_download_failure_offers_retry_of_the_check(self, download: AsyncMock) -> None:
        download.side_effect = TelegramFileError("boom")
        cb = _dm_tap()
        svc = _service()

        await _dm_analyze(cb, svc, _sticker_repo(missing=True))

        svc.learn.assert_not_awaited()
        text, buttons = _edit(cb)
        assert "Не получилось скачать стикер из Telegram." in text
        assert "Заметки на месте." not in text
        assert buttons == [("🔁 Повторить", f"adm_stk_dmchk:ru:{FUID}")]

    @pytest.mark.asyncio()
    async def test_a_content_refusal_offers_the_manual_score(self, download: AsyncMock) -> None:
        cb = _dm_tap()
        svc = _service(
            learn=StickerLearningResult(
                is_new=True,
                file_unique_id=FUID,
                analysis_failed=True,
                failure_reason="content_filter",
            )
        )

        await _dm_analyze(cb, svc, _sticker_repo(missing=True))

        text, buttons = _edit(cb)
        assert ERROR_REASONS["content_filter"]("ru") in text
        assert buttons == [
            ("🔁 Повторить", f"adm_stk_dmchk:ru:{FUID}"),
            ("🔞 Откровенность", f"adm_stk_score:ru:{FUID}"),
        ]

    @pytest.mark.asyncio()
    async def test_a_row_missing_after_learning_is_an_error_not_a_crash(
        self, download: AsyncMock
    ) -> None:
        cb = _dm_tap()

        await _dm_analyze(cb, _service(), _sticker_repo(missing=True))

        text, _ = _edit(cb)
        assert ERROR_REASONS["empty"]("ru") in text

    @pytest.mark.asyncio()
    async def test_registers_an_unknown_pack(self, download: AsyncMock) -> None:
        cb = _dm_tap()
        repo = _sticker_repo(pack=None)
        repo.get_by_file_unique_id.side_effect = [None, _row()]
        bot = MagicMock()
        tg_set = MagicMock()
        tg_set.name = PACK
        tg_set.title = "Test Pack"
        tg_set.thumbnail = None
        tg_set.stickers = [_tg_sticker(), _tg_sticker("second")]
        bot.get_sticker_set = AsyncMock(return_value=tg_set)

        await _dm_analyze(cb, _service(), repo, bot=bot)

        bot.get_sticker_set.assert_awaited_once_with(PACK)
        repo.upsert_sticker_set.assert_awaited_once_with(
            set_name=PACK,
            set_title="Test Pack",
            total_count=2,
            thumbnail_file_id=None,
            is_animated=False,
            is_video=False,
        )

    @pytest.mark.asyncio()
    async def test_pack_registration_failure_still_shows_the_card(
        self, download: AsyncMock
    ) -> None:
        cb = _dm_tap()
        repo = _sticker_repo(pack=None)
        repo.get_by_file_unique_id.side_effect = [None, _row()]
        bot = MagicMock()
        bot.get_sticker_set = AsyncMock(side_effect=RuntimeError("api"))

        await _dm_analyze(cb, _service(), repo, bot=bot)

        assert "Стикер разобран" in _edit(cb)[0]

    @pytest.mark.asyncio()
    async def test_an_already_analysed_sticker_just_shows_its_card(self) -> None:
        cb = _dm_tap()
        svc = _service()
        repo = _sticker_repo()

        await _dm_analyze(cb, svc, repo)

        svc.learn.assert_not_awaited()
        svc.reanalyze.assert_not_awaited()
        assert _edit(cb)[1] == CARD_BUTTONS
        repo.save_notification.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_retry_after_a_stored_failure_reanalyses(self) -> None:
        cb = _dm_tap()
        svc = _service(reanalyze=ReanalyzeResult(ok=False, reason="vision"))
        repo = _sticker_repo(_row(visual_description=None, analysis_failed=True))

        await _dm_analyze(cb, svc, repo)

        svc.learn.assert_not_awaited()
        svc.reanalyze.assert_awaited_once()
        text, buttons = _edit(cb)
        assert ERROR_REASONS["vision"]("ru") in text
        assert buttons == [("🔁 Повторить", f"adm_stk_dmchk:ru:{FUID}")]

    @pytest.mark.asyncio()
    async def test_unparseable_payload_is_stale(self, stale: AsyncMock) -> None:
        await _dm_analyze(_dm_tap(data="adm_stk_dmchk:ru"), _service(), _sticker_repo())
        stale.assert_awaited_once()


# ---------------------------------------------------------------------------
# Every button fits Telegram's 64-byte callback_data, whatever the pack name
# ---------------------------------------------------------------------------

LONG_PACK = "p" * 64  # Telegram's own ceiling for a sticker set name
LONG_FUID = "AgADvh4AAlkbCFIxyz12"


def _long_screens(lang: str) -> list[Screen]:
    facts = facts_from_row(
        _row(file_unique_id=LONG_FUID, set_name=LONG_PACK),
        pack={"set_name": LONG_PACK, "set_title": LONG_PACK},
    )
    return [
        build_stk_sets(
            lang,
            SetsPage(
                packs=(PackRow(key=pack_key(LONG_PACK), title=LONG_PACK, analyzed=1, total=2),),
                total=9,
                page=1,
                pages=3,
            ),
        ),
        build_stk_set(
            lang,
            PackPage(
                key=pack_key(LONG_PACK),
                title=LONG_PACK,
                count=9,
                stickers=(facts,),
                page=1,
                pages=3,
            ),
        ),
        build_stk_card(lang, facts),
        build_stk_score(lang, facts),
        build_stk_score_in(lang, facts),
        build_stk_clear_ask(lang, LONG_FUID),
        build_stk_error(lang, LONG_FUID, "content_filter"),
        build_stk_error(lang, LONG_FUID, "content_filter", dm=True),
        build_stk_unknown(lang, LONG_FUID),
    ]


class TestCallbackBudget:
    def test_the_pack_key_is_the_sql_formula(self) -> None:
        """``left(md5(set_name), 12)`` in the repository must find what the button carries."""
        expected = hashlib.md5(LONG_PACK.encode()).hexdigest()[:12]  # noqa: S324
        assert pack_key(LONG_PACK) == expected

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_every_button_fits_with_a_64_character_pack_name(self, lang: str) -> None:
        for screen in _long_screens(lang):
            for row in screen.rows:
                for button in row:
                    if button.callback is None:
                        continue
                    size = len(button.callback.encode())
                    assert size <= 64, f"{screen.id}: {button.callback!r} is {size} bytes"

    @pytest.mark.asyncio()
    async def test_the_long_named_pack_button_opens_that_pack(self) -> None:
        sets = build_stk_sets(
            "ru",
            SetsPage(
                packs=(PackRow(key=pack_key(LONG_PACK), title=LONG_PACK, analyzed=1, total=2),),
                total=1,
                page=0,
                pages=1,
            ),
        )
        data = sets.rows[0][0].callback
        assert data is not None
        repo = _sticker_repo(pack={"set_name": LONG_PACK, "set_title": "Long"})

        await handle_sticker_pack(_callback(data), repo, _state())

        repo.get_set_by_key.assert_awaited_once_with(pack_key(LONG_PACK))
        repo.get_stickers_in_set.assert_awaited_once_with(LONG_PACK, limit=4, offset=0)


# ---------------------------------------------------------------------------
# The verdict on the card is the search's own comparison
# ---------------------------------------------------------------------------


class TestScoreVerdict:
    def test_a_score_equal_to_the_tolerance_passes(self) -> None:
        """The ceiling is inclusive — a ``<`` anywhere on this path turns it into a block."""
        line = score_line(StickerFacts(fuid=FUID, score=0.5, tolerance=0.5), "ru")

        assert "0.50 · пройдёт в чате с допуском 0.50" in line
        assert "не пройдёт" not in line

    def test_the_card_asks_is_within_tolerance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not a copy of the inequality: whatever the search function says, the card says."""
        verdict = MagicMock(return_value=False)
        monkeypatch.setattr("src.bot.screens.stickers.is_within_tolerance", verdict)

        line = score_line(StickerFacts(fuid=FUID, score=0.1, tolerance=0.5), "ru")

        verdict.assert_called_once_with(0.1, 0.5)
        assert "0.10 · не пройдёт в чате с допуском 0.50" in line


# ---------------------------------------------------------------------------
# A crash in the analysis is an error screen, never a frozen ⏳
# ---------------------------------------------------------------------------


_RETRY_AND_BACK: Buttons = [
    ("🔁 Повторить", f"adm_stk_reanalyze:ru:{FUID}"),
    ("⬅️ Стикер", f"adm_stk_card:ru:{FUID}"),
]


class TestAnalysisCrash:
    @pytest.mark.asyncio()
    async def test_reanalyze_raising_turns_the_placeholder_into_the_error(self) -> None:
        cb = _callback(f"adm_stk_reanalyze:ru:{FUID}")
        svc = _service()
        svc.reanalyze.side_effect = RuntimeError("boom")
        repo = _sticker_repo()

        with patch(f"{MOD}.logger") as log:
            await handle_run_analysis(
                cb, MagicMock(), svc, repo, _admin_repo(), _bot_config_repo(), _state()
            )

        assert "⏳" in _edit(cb, 0)[0]
        text, buttons = _edit(cb)
        assert "⚠️ <b>Не получилось разобрать стикер</b>" in text
        assert ERROR_REASONS["internal"]("ru") in text
        assert buttons == _RETRY_AND_BACK
        assert log.error.call_args.kwargs["exc_info"] is True
        # A plain re-analysis drops no score, so there is nothing to put back.
        repo.update_explicitness_score.assert_not_awaited()
        repo.set_manual_explicitness_score.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_learn_raising_in_the_dm_check_is_the_error(self, download: AsyncMock) -> None:
        cb = _dm_tap()
        svc = _service()
        svc.learn.side_effect = RuntimeError("boom")

        with patch(f"{MOD}.logger") as log:
            await _dm_analyze(cb, svc, _sticker_repo(missing=True))

        assert "⏳" in _edit(cb, 0)[0]
        text, buttons = _edit(cb)
        assert ERROR_REASONS["internal"]("ru") in text
        assert buttons == [("🔁 Повторить", f"adm_stk_dmchk:ru:{FUID}")]
        assert log.error.call_args.kwargs["exc_info"] is True


# ---------------------------------------------------------------------------
# A failed re-score puts the score back
# ---------------------------------------------------------------------------


_RESCORE_AND_BACK: Buttons = [
    ("🔁 Повторить", f"adm_stk_expreset:ru:{FUID}"),
    ("⬅️ Стикер", f"adm_stk_card:ru:{FUID}"),
]


async def _rescore(repo: MagicMock, svc: MagicMock) -> MagicMock:
    cb = _callback(f"adm_stk_expreset:ru:{FUID}")
    await handle_sticker_explicitness_reset(
        cb, MagicMock(), svc, repo, _admin_repo(), _bot_config_repo(), _state()
    )
    return cb


class TestFailedRescoreKeepsTheScore:
    @pytest.mark.asyncio()
    async def test_a_manual_score_is_put_back_as_manual(self) -> None:
        repo = _sticker_repo(_row(explicitness_score=0.8, explicitness_is_manual=True))
        svc = _service(reanalyze=ReanalyzeResult(ok=False, reason="vision"))

        cb = await _rescore(repo, svc)

        repo.reset_explicitness_to_auto.assert_awaited_once_with(FUID)
        repo.set_manual_explicitness_score.assert_awaited_once_with(FUID, 0.8)
        repo.update_explicitness_score.assert_not_awaited()
        text, buttons = _edit(cb)
        assert ERROR_REASONS["vision"]("ru") in text
        assert "Оценка осталась прежней: 0.80." in text
        # Retry repeats the re-score: a bare re-analysis keeps a manual score.
        assert buttons == _RESCORE_AND_BACK

    @pytest.mark.asyncio()
    async def test_an_automatic_score_is_put_back_as_automatic(self) -> None:
        repo = _sticker_repo(_row(explicitness_score=0.25, explicitness_is_manual=False))
        svc = _service()
        svc.reanalyze.side_effect = RuntimeError("boom")

        cb = await _rescore(repo, svc)

        repo.update_explicitness_score.assert_awaited_once_with(FUID, 0.25)
        repo.set_manual_explicitness_score.assert_not_awaited()
        text, buttons = _edit(cb)
        assert ERROR_REASONS["internal"]("ru") in text
        assert "Оценка осталась прежней: 0.25." in text
        assert buttons == _RESCORE_AND_BACK

    @pytest.mark.asyncio()
    async def test_when_putting_it_back_fails_the_screen_says_it_is_gone(self) -> None:
        repo = _sticker_repo(_row(explicitness_score=0.8, explicitness_is_manual=True))
        repo.set_manual_explicitness_score.side_effect = RuntimeError("db down")
        svc = _service(reanalyze=ReanalyzeResult(ok=False, reason="download"))

        cb = await _rescore(repo, svc)

        text, buttons = _edit(cb)
        assert "Оценка сброшена, и пока её нет, бот этот стикер не отправит." in text
        assert "осталась прежней" not in text
        assert buttons == [
            ("🔁 Повторить", f"adm_stk_expreset:ru:{FUID}"),
            ("🔞 Откровенность", f"adm_stk_score:ru:{FUID}"),
            ("⬅️ Стикер", f"adm_stk_card:ru:{FUID}"),
        ]

    @pytest.mark.asyncio()
    async def test_a_refusal_with_a_lost_score_offers_the_manual_score_once(self) -> None:
        repo = _sticker_repo(_row(explicitness_score=0.8, explicitness_is_manual=False))
        repo.update_explicitness_score.side_effect = RuntimeError("db down")
        svc = _service(reanalyze=ReanalyzeResult(ok=False, reason="content_filter"))

        cb = await _rescore(repo, svc)

        _, buttons = _edit(cb)
        assert _callbacks(buttons).count(f"adm_stk_score:ru:{FUID}") == 1

    @pytest.mark.asyncio()
    async def test_an_unscored_sticker_has_nothing_to_put_back(self) -> None:
        repo = _sticker_repo(_row(explicitness_score=None))
        svc = _service(reanalyze=ReanalyzeResult(ok=False, reason="vision"))

        cb = await _rescore(repo, svc)

        repo.update_explicitness_score.assert_not_awaited()
        repo.set_manual_explicitness_score.assert_not_awaited()
        text, buttons = _edit(cb)
        assert "Оценка" not in text.split("</b>", 1)[1]
        assert buttons == _RESCORE_AND_BACK

    @pytest.mark.asyncio()
    async def test_retry_on_a_kept_manual_score_re_scores_it(self) -> None:
        """Tapping Retry, through the real router: the manual score is reset again.

        A re-analysis would not touch it — ``save_sticker`` keeps a score set by
        hand — so a Retry routed there would say "updated" and change nothing.
        """
        failed = await _rescore(
            _sticker_repo(_row(explicitness_score=0.8, explicitness_is_manual=True)),
            _service(reanalyze=ReanalyzeResult(ok=False, reason="vision")),
        )
        retry = _edit(failed)[1][0][1]
        assert retry is not None
        tap = _callback(retry, message_id=failed.message.message_id)
        repo = _sticker_repo(_row(explicitness_score=0.8, explicitness_is_manual=True))

        result = await admin_sticker_router.propagate_event(
            "callback_query",
            tap,
            is_admin=True,
            bot=MagicMock(),
            sticker_service=_service(),
            sticker_repo=repo,
            admin_repo=_admin_repo(),
            bot_config_repo=_bot_config_repo(),
            state=_state(),
            raw_state=None,
            event_from_user=tap.from_user,
        )

        assert result is not UNHANDLED
        repo.reset_explicitness_to_auto.assert_awaited_once_with(FUID)
        assert _edit(tap)[0].startswith("<i>✓ Оценка пересчитана</i>")

    @pytest.mark.asyncio()
    async def test_a_successful_rescore_writes_nothing_back(self) -> None:
        repo = _sticker_repo(_row(explicitness_score=0.8, explicitness_is_manual=True))

        cb = await _rescore(repo, _service())

        repo.set_manual_explicitness_score.assert_not_awaited()
        repo.update_explicitness_score.assert_not_awaited()
        assert _edit(cb)[0].startswith("<i>✓ Оценка пересчитана</i>")


# ---------------------------------------------------------------------------
# The score screen's buttons leave another screen's prompt alone
# ---------------------------------------------------------------------------


_FOREIGN = AdminStates.awaiting_rule_react.state


class TestForeignPromptSurvives:
    @pytest.mark.asyncio()
    async def test_preset(self) -> None:
        state = _state(current=_FOREIGN)
        repo = _sticker_repo()

        await handle_sticker_explicitness_preset(
            _callback(f"adm_stk_expset:ru:{FUID}:3"), repo, _admin_repo(), _bot_config_repo(), state
        )

        repo.set_manual_explicitness_score.assert_awaited_once_with(FUID, 0.75)
        state.clear.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_rescore(self) -> None:
        state = _state(current=_FOREIGN)
        repo = _sticker_repo()

        await handle_sticker_explicitness_reset(
            _callback(f"adm_stk_expreset:ru:{FUID}"),
            MagicMock(),
            _service(),
            repo,
            _admin_repo(),
            _bot_config_repo(),
            state,
        )

        repo.reset_explicitness_to_auto.assert_awaited_once_with(FUID)
        state.clear.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_cancel(self) -> None:
        cb = _callback(f"adm_stk_expcancel:ru:{FUID}")
        state = _state(current=_FOREIGN)

        await handle_sticker_explicitness_cancel(
            cb, _sticker_repo(), _admin_repo(), _bot_config_repo(), state
        )

        state.clear.assert_not_awaited()
        assert "Сейчас 0.25, посчитал бот." in _edit(cb)[0]


# ---------------------------------------------------------------------------
# A sticker or a photo sent into the score prompt
# ---------------------------------------------------------------------------


class TestScorePromptGetsMedia:
    @pytest.mark.asyncio()
    async def test_the_prompt_says_to_send_text_and_how_to_leave(self, strip: AsyncMock) -> None:
        msg = _typed("")
        msg.text = None
        msg.caption = None
        msg.sticker = _tg_sticker()
        state = _state(data=dict(_ARMED))
        repo = _sticker_repo()

        await handle_sticker_explicitness_input(msg, state, repo, _admin_repo(), _bot_config_repo())

        text, buttons = _new(msg.answer)
        assert (
            "⚠️ Сюда нужно число текстом. Чтобы выйти, нажмите «✖️ Отмена» "
            "или отправьте /cancel." in text
        )
        assert "Нужно число от 0.0 до 1.0" not in text
        assert buttons == [("✖️ Отмена", f"adm_stk_expcancel:ru:{FUID}")]
        state.clear.assert_not_awaited()
        repo.set_manual_explicitness_score.assert_not_awaited()
        state.update_data.assert_awaited_once_with(exp_prompt_id=301)
        assert strip.call_args.args[0].message_id == 250

    @pytest.mark.asyncio()
    async def test_english(self, strip: AsyncMock) -> None:
        msg = _typed("")
        msg.text = None
        state = _state(data={**_ARMED, "exp_lang": "en"})

        await handle_sticker_explicitness_input(
            msg, state, _sticker_repo(), _admin_repo("en"), _bot_config_repo()
        )

        text, buttons = _new(msg.answer)
        assert "Send the number as text. To leave, tap" in text
        assert "“✖️ Cancel”" in text
        assert buttons == [("✖️ Cancel", f"adm_stk_expcancel:en:{FUID}")]


# ---------------------------------------------------------------------------
# Stateful doubles for the findings below: the row and the card memory change
# under the handler the way their SQL changes them
# ---------------------------------------------------------------------------


def _live_repo(**row: Any) -> tuple[MagicMock, dict[str, Any]]:
    """`_sticker_repo` whose ``sticker_knowledge`` row is written by the calls on it.

    Each write mirrors its SQL; ``save_sticker`` is the UPSERT's score rule:
    ``CASE WHEN explicitness_is_manual THEN old ELSE COALESCE(EXCLUDED, old)``.
    """
    live = {"is_animated": False, "is_video": False, **_row(**row)}
    repo = _sticker_repo()

    async def read(fuid: str) -> dict[str, Any] | None:
        return dict(live) if fuid == live["file_unique_id"] else None

    async def reset(_fuid: str) -> bool:
        live.update(explicitness_score=None, explicitness_is_manual=False)
        return True

    async def set_manual(_fuid: str, score: float) -> bool:
        live.update(explicitness_score=score, explicitness_is_manual=True)
        return True

    async def set_auto(_fuid: str, score: float) -> None:
        live["explicitness_score"] = score

    async def clear(_fuid: str) -> None:
        for column in ("visual_description", "emotion", "character_or_meme"):
            live[column] = None
        live.update(suggested_contexts=None, analysis_failed=False)

    async def save(**kw: Any) -> int:
        for column in ("visual_description", "emotion", "suggested_contexts", "character_or_meme"):
            live[column] = kw.get(column)
        live["analysis_failed"] = kw.get("analysis_failed", False)
        if not live["explicitness_is_manual"] and kw.get("explicitness_score") is not None:
            live["explicitness_score"] = kw["explicitness_score"]
        return 1

    repo.get_by_file_unique_id = AsyncMock(side_effect=read)
    repo.reset_explicitness_to_auto = AsyncMock(side_effect=reset)
    repo.set_manual_explicitness_score = AsyncMock(side_effect=set_manual)
    repo.update_explicitness_score = AsyncMock(side_effect=set_auto)
    repo.clear_analysis = AsyncMock(side_effect=clear)
    repo.save_sticker = AsyncMock(side_effect=save)
    repo.get_pack_context = AsyncMock(return_value=[])
    repo.get_dedup_candidates = AsyncMock(return_value=[])
    repo.update_embedding = AsyncMock()
    return repo, live


def _card_memory(repo: MagicMock) -> list[dict[str, Any]]:
    """``admin_sticker_notifications`` behind `repo`, by the SQL of its three methods."""
    rows: list[dict[str, Any]] = []

    async def save(**kw: Any) -> int:  # admin_id is stored too, and read by no one here
        row = {
            "fuid": kw["file_unique_id"],
            "chat_id": kw["chat_id"],
            "message_id": kw["message_id"],
            "sticker_msg_id": kw["sticker_msg_id"],
        }
        for i, r in enumerate(rows):  # the existing row for (chat, message, sticker)
            if (r["chat_id"], r["message_id"], r["fuid"]) == (
                row["chat_id"],
                row["message_id"],
                row["fuid"],
            ):
                return i
        rows.append(row)
        return len(rows) - 1

    async def sticker_of(chat_id: int, message_id: int) -> int | None:
        # WHERE chat_id = $1 AND message_id = $2 AND sticker_msg_id > 0 ORDER BY id DESC
        for r in reversed(rows):
            if (r["chat_id"], r["message_id"]) == (chat_id, message_id) and r["sticker_msg_id"] > 0:
                return int(r["sticker_msg_id"])
        return None

    async def release(chat_id: int, message_id: int) -> None:
        for r in rows:
            if (r["chat_id"], r["message_id"]) == (chat_id, message_id):
                r["message_id"] = None

    repo.save_notification = AsyncMock(side_effect=save)
    repo.get_card_sticker_msg = AsyncMock(side_effect=sticker_of)
    repo.release_card = AsyncMock(side_effect=release)
    return rows


def _fsm() -> FSMContext:
    """aiogram's own FSM over memory: what one handler stores, the next one reads."""
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=ADMIN, user_id=ADMIN)
    )


def _vision(text: str) -> MagicMock:
    """The AI router as `StickerLearningService` uses it: vision text, a 768-dim embedding."""
    ai = MagicMock()
    ai.analyze_image = AsyncMock(
        return_value=VisionResult(text=text, model="gemini-3-flash", provider="gemini")
    )
    ai.generate_embedding = AsyncMock(
        return_value=EmbeddingResult(
            embedding=[0.0] * 768, model="gemini-embedding-001", provider="gemini", dimensions=768
        )
    )
    ai.generate_text = AsyncMock()
    return ai


def _downloading_bot() -> MagicMock:
    bot = MagicMock()
    file = MagicMock()
    file.file_path = "stickers/x.webp"
    bot.get_file = AsyncMock(return_value=file)
    bot.download_file = AsyncMock(return_value=io.BytesIO(b"RIFF....WEBPVP8 not-a-real-image"))
    return bot


async def _route(event_type: str, event: MagicMock, **data: Any) -> Any:
    """One update through the real router: filters decide, as in production."""
    return await admin_sticker_router.propagate_event(
        event_type,
        event,
        is_admin=True,
        event_from_user=event.from_user,
        **{
            "bot": MagicMock(),
            "admin_repo": _admin_repo(),
            "bot_config_repo": _bot_config_repo(),
            "state": _state(),
            "raw_state": None,
            **data,
        },
    )


# ---------------------------------------------------------------------------
# A re-score that gives no score keeps the old one (review stickers-services-1)
# ---------------------------------------------------------------------------


_VISION_WITHOUT_A_SCORE = {
    "omitted": (
        '{"visual": "Кот машет лапой", "emotion": "радость", "contexts": [], '
        '"tags": [], "character": null}'
    ),
    "cut_off": (
        '{"visual": "Кот машет лапой, приветствие", "emotion": "радость", '
        '"contexts": ["привет", "пока"], "tags": ["cute"], "character": null, "expl'
    ),
    "out_of_range": (
        '{"visual": "Кот машет лапой", "emotion": "радость", "contexts": [], '
        '"tags": [], "character": null, "explicit": 70}'
    ),
}


class TestRescoreWithoutAScore:
    @pytest.mark.asyncio()
    @pytest.mark.parametrize("vision", list(_VISION_WITHOUT_A_SCORE), ids=str)
    async def test_through_the_real_analysis_the_manual_score_survives(self, vision: str) -> None:
        """🔁 Пересчитать, routed, real `StickerLearningService`: ``ok`` and no score.

        ``learn()`` calls an analysis with a description a success, score or no
        score, and ``save_sticker`` keeps the NULL the reset wrote — so the
        sticker was left unscored, i.e. never sent, under "✓ Оценка пересчитана".
        """
        repo, live = _live_repo(explicitness_score=0.3, explicitness_is_manual=True)
        svc = StickerLearningService(_vision(_VISION_WITHOUT_A_SCORE[vision]), repo)
        tap = _callback(f"adm_stk_expreset:ru:{FUID}")

        result = await _route(
            "callback_query", tap, bot=_downloading_bot(), sticker_service=svc, sticker_repo=repo
        )

        assert result is not UNHANDLED
        assert (live["explicitness_score"], live["explicitness_is_manual"]) == (0.3, True)
        text, buttons = _edit(tap)
        assert text.startswith(
            "<i>Модель не вернула оценку. Оценка осталась прежней: 0.30.</i>\n\n"
        )
        assert "пересчитана" not in text
        assert "не оценена" not in text
        assert buttons == CARD_BUTTONS

    @pytest.mark.asyncio()
    async def test_a_real_score_is_still_the_new_score(self) -> None:
        """The control: a vision answer with a valid score replaces the old one."""
        repo, live = _live_repo(explicitness_score=0.3, explicitness_is_manual=True)
        good = _VISION_WITHOUT_A_SCORE["omitted"][:-1] + ', "explicit": 0.1}'
        svc = StickerLearningService(_vision(good), repo)
        tap = _callback(f"adm_stk_expreset:ru:{FUID}")

        await _route(
            "callback_query", tap, bot=_downloading_bot(), sticker_service=svc, sticker_repo=repo
        )

        assert (live["explicitness_score"], live["explicitness_is_manual"]) == (0.1, False)
        repo.set_manual_explicitness_score.assert_not_awaited()
        assert _edit(tap)[0].startswith("<i>✓ Оценка пересчитана</i>")

    @pytest.mark.asyncio()
    async def test_an_automatic_score_comes_back_automatic(self) -> None:
        repo, live = _live_repo(explicitness_score=0.25, explicitness_is_manual=False)

        cb = await _rescore(repo, _service())  # ok, and nothing wrote a score

        repo.update_explicitness_score.assert_awaited_once_with(FUID, 0.25)
        repo.set_manual_explicitness_score.assert_not_awaited()
        assert (live["explicitness_score"], live["explicitness_is_manual"]) == (0.25, False)
        assert _edit(cb)[0].startswith(
            "<i>Модель не вернула оценку. Оценка осталась прежней: 0.25.</i>"
        )

    @pytest.mark.asyncio()
    async def test_when_putting_it_back_fails_the_card_says_it_is_gone(self) -> None:
        repo, live = _live_repo(explicitness_score=0.8, explicitness_is_manual=True)
        repo.set_manual_explicitness_score.side_effect = RuntimeError("db down")

        cb = await _rescore(repo, _service())

        assert live["explicitness_score"] is None
        text, buttons = _edit(cb)
        assert text.startswith(
            "<i>Модель не вернула оценку. Оценка сброшена, и пока её нет, бот этот стикер "
            "не отправит. Задайте её вручную: 🔞 Откровенность.</i>\n\n"
        )
        assert "пересчитана" not in text
        assert buttons == CARD_BUTTONS

    @pytest.mark.asyncio()
    async def test_an_unscored_sticker_has_nothing_to_put_back(self) -> None:
        repo, _ = _live_repo(explicitness_score=None)

        cb = await _rescore(repo, _service())

        repo.update_explicitness_score.assert_not_awaited()
        repo.set_manual_explicitness_score.assert_not_awaited()
        assert _edit(cb)[0].startswith("<i>Модель не вернула оценку.</i>\n\n")

    @pytest.mark.asyncio()
    async def test_english(self) -> None:
        repo, _ = _live_repo(explicitness_score=0.8, explicitness_is_manual=True)
        cb = _callback(f"adm_stk_expreset:en:{FUID}")

        await handle_sticker_explicitness_reset(
            cb, MagicMock(), _service(), repo, _admin_repo("en"), _bot_config_repo(), _state()
        )

        assert _edit(cb)[0].startswith(
            "<i>The model returned no score. The score is unchanged: 0.80.</i>\n\n"
        )

    @pytest.mark.asyncio()
    async def test_a_plain_reanalysis_without_a_score_keeps_its_notice(self) -> None:
        """Only a re-score drops a score first; a plain 🔁 Разобрать заново has none to lose."""
        repo, _ = _live_repo(explicitness_score=None)
        cb = _callback(f"adm_stk_reanalyze:ru:{FUID}")

        await handle_run_analysis(
            cb, MagicMock(), _service(), repo, _admin_repo(), _bot_config_repo(), _state()
        )

        assert _edit(cb)[0].startswith("<i>✓ Разбор обновлён</i>")
        repo.update_explicitness_score.assert_not_awaited()


# ---------------------------------------------------------------------------
# After a successful analysis, a failure is still an error screen (stickers-services-3)
# ---------------------------------------------------------------------------


class TestAfterTheAnalysis:
    @pytest.mark.asyncio()
    async def test_a_failed_read_is_the_error_screen_not_a_frozen_placeholder(self) -> None:
        repo = _sticker_repo()
        repo.get_by_file_unique_id = AsyncMock(side_effect=[_row(), RuntimeError("pool timeout")])
        cb = _callback(f"adm_stk_reanalyze:ru:{FUID}")

        with capture_logs() as logs:
            await handle_run_analysis(
                cb, MagicMock(), _service(), repo, _admin_repo(), _bot_config_repo(), _state()
            )

        assert "⏳" in _edit(cb, 0)[0]
        text, buttons = _edit(cb)
        assert "⚠️ <b>Не получилось разобрать стикер</b>" in text
        assert ERROR_REASONS["internal"]("ru") in text
        assert buttons == _RETRY_AND_BACK
        failed = [e for e in logs if e["event"] == "sticker card after analysis failed"]
        assert [e["log_level"] for e in failed] == ["error"]
        repo.save_notification.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_a_failed_rescore_card_retries_the_rescore(self) -> None:
        repo = _sticker_repo(_row(explicitness_score=0.8, explicitness_is_manual=True))
        repo.get_by_file_unique_id = AsyncMock(
            side_effect=[_row(explicitness_score=0.8), RuntimeError("pool timeout")]
        )

        cb = await _rescore(repo, _service())

        text, buttons = _edit(cb)
        assert ERROR_REASONS["internal"]("ru") in text
        assert buttons == _RESCORE_AND_BACK

    @pytest.mark.asyncio()
    async def test_a_failed_card_in_the_dm_check_offers_the_check_again(self) -> None:
        """The DM check learns a new sticker on its own path; the same guard holds there."""
        repo = _sticker_repo()
        repo.get_by_file_unique_id = AsyncMock(side_effect=[None, RuntimeError("pool timeout")])
        cb = _dm_tap()

        with (
            patch(f"{MOD}.download_telegram_file", new=AsyncMock(return_value=b"webp")),
            capture_logs() as logs,
        ):
            await _dm_analyze(cb, _service(), repo)

        assert "⏳" in _edit(cb, 0)[0]
        text, buttons = _edit(cb)
        assert ERROR_REASONS["internal"]("ru") in text
        assert buttons == [("🔁 Повторить", f"adm_stk_dmchk:ru:{FUID}")]
        assert any(e["event"] == "sticker card after analysis failed" for e in logs)


# ---------------------------------------------------------------------------
# ✏️ Своё число keeps the pack card's sticker with the card (stickers-services-2)
# ---------------------------------------------------------------------------


def _typed_routed(text: str) -> MagicMock:
    """A typed answer as aiogram sees it: no caption, no sticker, not a reply."""
    msg = _typed(text)
    msg.caption = None
    msg.sticker = None
    msg.reply_to_message = None
    return msg


class TestTypedScoreKeepsThePackSticker:
    async def _type_a_score(self, repo: MagicMock, state: FSMContext, card: MagicMock) -> MagicMock:
        """🔞 Откровенность → ✏️ Своё число on `card` → "0.4", routed; returns the new card 301."""
        await handle_sticker_score(card, repo, _admin_repo(), _bot_config_repo(), state)
        prompt = _callback(f"adm_stk_expedit:ru:{FUID}", message_id=card.message.message_id)
        prompt.message.reply_to_message = card.message.reply_to_message
        await handle_sticker_explicitness_edit_prompt(
            prompt, repo, _admin_repo(), _bot_config_repo(), state
        )
        typed = _typed_routed("0.4")
        with patch(f"{MOD}.safe_edit_reply_markup", new=AsyncMock()):
            result = await _route(
                "message",
                typed,
                sticker_repo=repo,
                state=state,
                raw_state=await state.get_state(),
            )
        assert result is not UNHANDLED
        assert _new(typed.answer)[0].startswith("<i>✓ Откровенность 0.40</i>")
        return typed

    @pytest.mark.asyncio()
    async def test_leaving_the_new_card_takes_the_pack_sticker(self) -> None:
        repo = _sticker_repo()
        rows = _card_memory(repo)
        state = _fsm()
        view = _callback(f"adm_stk_view:ru:{FUID}", message_id=100)  # sticker 101, card 102
        await handle_sticker_view(view, repo, _admin_repo(), _bot_config_repo(), state)
        assert rows == [{"fuid": FUID, "chat_id": ADMIN, "message_id": 102, "sticker_msg_id": 101}]

        await self._type_a_score(repo, state, _callback(f"adm_stk_score:ru:{FUID}", message_id=102))
        leave = _callback(f"adm_stk_pk:ru:{KEY}:0", message_id=301)
        await handle_sticker_pack(leave, repo, state)

        leave.bot.delete_message.assert_awaited_once_with(chat_id=ADMIN, message_id=101)

    @pytest.mark.asyncio()
    async def test_a_notification_card_does_not_hand_its_sticker_on(self) -> None:
        """A reply card's sticker is the admin's: the new card, no reply, would delete it."""
        repo = _sticker_repo()
        rows = _card_memory(repo)
        await repo.save_notification(
            file_unique_id=FUID, admin_id=ADMIN, message_id=501, sticker_msg_id=500, chat_id=ADMIN
        )
        state = _fsm()
        card = _callback(f"adm_stk_score:ru:{FUID}", message_id=501, reply_to=MagicMock())

        await self._type_a_score(repo, state, card)
        assert rows[-1] == {"fuid": FUID, "chat_id": ADMIN, "message_id": 301, "sticker_msg_id": 0}
        leave = _callback(f"adm_stk_pk:ru:{KEY}:0", message_id=301)
        await handle_sticker_pack(leave, repo, state)

        leave.bot.delete_message.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_a_new_prompt_does_not_inherit_the_last_ones_sticker(self) -> None:
        """Every prompt writes its own value, so a stale one cannot ride along in the FSM."""
        state = _fsm()
        await state.update_data(exp_sticker_msg=101)
        cb = _callback(f"adm_stk_expedit:ru:{FUID}", message_id=777)

        await handle_sticker_explicitness_edit_prompt(
            cb, _sticker_repo(card_sticker_msg=None), _admin_repo(), _bot_config_repo(), state
        )

        assert (await state.get_data())["exp_sticker_msg"] == 0


# ---------------------------------------------------------------------------
# A reply to a card whose record is lost (stickers-services-5)
# ---------------------------------------------------------------------------


def _wire(**fields: Any) -> Message:
    """A message as Telegram delivers it in ``reply_to_message``: parsed by aiogram."""
    return Message.model_validate(
        {
            "message_id": 501,
            "date": 1_767_225_600,
            "chat": {"id": ADMIN, "type": "private"},
            "from": {"id": 1, "is_bot": True, "first_name": "Bot"},
            **fields,
        }
    )


def _brief_card_on_the_wire() -> Message:
    """The bot's brief sticker card (notification mode "on"), as Telegram returns it."""
    card = build_card_sticker("ru", facts_from_row(_row(), pack=PACK_ROW))
    markup = to_markup(card)
    assert markup is not None
    plain = html.unescape(re.sub(r"<[^>]+>", "", render_text(card)))
    return _wire(text=plain, reply_markup=markup.model_dump(exclude_none=True))


def _dm_text_reply(reply_to: Message, text: str = "это не кот, а лиса") -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = None
    msg.sticker = None
    msg.message_id = 600
    msg.chat = Chat(id=ADMIN, type="private")
    msg.from_user.id = ADMIN
    msg.reply = AsyncMock()
    msg.reply_to_message = reply_to
    return msg


class TestReplyToACardWithoutARecord:
    @pytest.mark.asyncio()
    async def test_the_brief_card_is_found_by_its_buttons(self) -> None:
        card = _brief_card_on_the_wire()
        assert "🆔" not in (card.text or ""), "precondition: the brief card has no 🆔 line"
        repo = _sticker_repo()  # get_notification_by_reply → None: the record was never saved
        svc = _service()
        msg = _dm_text_reply(card)

        result = await _route("message", msg, bot=_bot(), sticker_repo=repo, sticker_service=svc)

        assert result is not UNHANDLED
        repo.get_notification_by_reply.assert_awaited_once_with(ADMIN, 501)
        svc.merge_admin_description.assert_awaited_once_with(FUID, "это не кот, а лиса")
        assert "✓ <b>Описание обновлено</b>" in msg.reply.call_args.args[0]

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("lang", ["ru", "en"])
    async def test_a_reply_that_names_no_sticker_is_answered(self, lang: str) -> None:
        other = _wire(
            text="💰 Дневной лимит расходов превышен",
            reply_markup={
                "inline_keyboard": [[{"text": "💰", "callback_data": "adm_costs:ru:24h"}]]
            },
        )
        svc = _service()
        msg = _dm_text_reply(other)

        with capture_logs() as logs:
            result = await _route(
                "message",
                msg,
                bot=_bot(),
                sticker_repo=_sticker_repo(),
                sticker_service=svc,
                admin_repo=_admin_repo(lang),
            )

        assert result is not UNHANDLED
        svc.merge_admin_description.assert_not_awaited()
        expected = {
            "ru": "Не получилось понять, о каком стикере речь. "
            "Ответьте на карточку стикера или пришлите сам стикер.",
            "en": "Could not tell which sticker this is about. "
            "Reply to the sticker's card or send the sticker itself.",
        }[lang]
        msg.reply.assert_awaited_once()
        assert msg.reply.call_args.args[0].endswith(f"</b>\n\n{expected}")
        assert [e["log_level"] for e in logs if e["event"] == "sticker reply matched no card"] == [
            "info"
        ]

    def test_buttons_of_another_screen_name_no_sticker(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️", callback_data=f"adm_stk_pk:ru:{KEY}:0")],
                [InlineKeyboardButton(text="🔗", url="https://example.org")],
            ]
        )
        assert _extract_file_unique_id_from_reply(_wire(text="x", reply_markup=markup)) is None

    def test_a_malformed_card_button_names_no_sticker(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔞", callback_data="adm_stk_score:ru:a b")]
            ]
        )
        assert _extract_file_unique_id_from_reply(_wire(text="x", reply_markup=markup)) is None

    def test_the_id_line_still_wins_over_the_buttons(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔞", callback_data="adm_stk_score:ru:Other")]
            ]
        )
        reply = _wire(text=f"🆔 {FUID}", reply_markup=markup)
        assert _extract_file_unique_id_from_reply(reply) == FUID
