"""Admin sticker handlers: the pack catalogue, a sticker's card, its sub-screens.

Every handler runs through the real screen layer (`show()` and the builders),
so the assertions read what the admin would see: the text Telegram receives
and the buttons under it. Only the stale-button screen is patched — it renders
the hub, which belongs to another module.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, InlineKeyboardMarkup, Message

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
from src.bot.screens.model import Screen
from src.bot.screens.stickers import (
    ERROR_REASONS,
    PRESETS,
    PackPage,
    PackRow,
    SetsPage,
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
)
from src.bot.states.admin import AdminStates
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
    async def test_a_reply_to_anything_else_is_left_alone(self) -> None:
        svc = _service()
        msg = _admin_reply(reply_text="No ID here")
        await _run_reply(msg, _sticker_repo(), svc)
        svc.merge_admin_description.assert_not_awaited()
        msg.reply.assert_not_awaited()

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
        state = _state()

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
        state = _state()

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
