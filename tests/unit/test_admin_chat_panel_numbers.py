"""The chat panel's numbers: how often the bot joins in, its reply language, and
how often it answers with a sticker.

Each of these used to be shown and not changeable («меняются только в базе»),
or shown with a clause the bot does not honour (``random_response_min_interval``
is stored, and nothing in the reply path reads it). These tests pin the
screens that change them and what a tap or a typed number does: the screens
through their builders, the taps through the real router (`deliver`), the
writes through the handlers with repository doubles.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.bot.handlers.admin_chat_panel as panel
from src.bot.screens import chat as screens
from src.bot.screens.chat import ChatView, config_values
from src.bot.screens.lint import clip_room, label_width_px
from src.bot.screens.model import Screen
from src.bot.screens.registry import load_all
from src.bot.screens.render import render_text
from src.bot.states.admin import AdminStates
from src.models.chat_config import ChatConfig
from tests.unit.screen_routing import FakeState, admin_tap, admin_text, deliver, first_match
from tests.unit.test_admin_chat_panel_handler import (
    CHAT_GONE,
    CHAT_ID,
    PROMPT_ID,
    ROW,
    _admin_repo,
    _bot_config_repo,
    _callback,
    _chat_settings_repo,
    _edited,
    _flat,
    _labels,
    _message,
    _sent,
    _service,
    _state,
)

CID = -1001234567890  # invented
PANEL = "admin_chat_panel."
ARMED_STATE = AdminStates.awaiting_response_chance


@pytest.fixture
def stale() -> Iterator[AsyncMock]:
    with patch("src.bot.handlers.admin_chat_panel.show_stale", new=AsyncMock()) as mock:
        yield mock


def _view(row: dict[str, Any] | None = None, **overrides: Any) -> ChatView:
    own = dict(row or {})
    return ChatView(
        chat_id=CID,
        title="Тестовый чат",
        chat_type="supergroup",
        values=config_values(
            ChatConfig(chat_id=CID, enabled=True, trigger_words=("бот",)), **{**own, **overrides}
        ),
        row=own,
    )


def _all_labels(screen: Screen) -> list[str]:
    return [b.label for row in screen.rows for b in row]


def _payloads(screen: Screen) -> list[str]:
    return [b.callback or "" for row in screen.rows for b in row]


def _italic_spans(text: str) -> str:
    return " ".join(re.findall(r"<i>(.*?)</i>", text, flags=re.S))


# ── How often it joins in: the switch, the line and the button ─────────────


class TestTheRelevanceSwitchSaysWhatOnMeans:
    """«✅ Не встревать без повода» reads as "on = stay out": the double negative
    hid that ✅ is the one that makes it speak less."""

    @pytest.mark.parametrize(
        ("lang", "label"),
        [("ru", "Встревать только по делу"), ("en", "Join in only when useful")],
    )
    def test_the_label_names_the_on_state(self, lang: str, label: str) -> None:
        assert screens.LABELS["rg"](lang) == label
        rows = screens.build_behavior(_view({"relevancy_gate_enabled": True}), lang).rows
        assert f"✅ {label}" in [b.label for row in rows for b in row]


class TestBehaviorSaysHowOftenInOneLine:
    def test_no_interval_clause_the_bot_does_not_honour(self) -> None:
        """``random_response_min_interval`` is not read by the reply path, so
        «не чаще раза в 5 минут» was a promise nothing keeps."""
        view = _view(random_response_chance=0.05, random_response_min_interval=300)
        assert "не чаще" not in screens.build_behavior(view, "ru").text
        assert "at most once" not in screens.build_behavior(view, "en").text

    def test_with_the_check_on_it_speaks_only_with_something_to_add(self) -> None:
        text = screens.build_behavior(
            _view({"relevancy_gate_enabled": True}, random_response_chance=0.05), "ru"
        ).text
        assert "Встревает: до 5 % сообщений, без зова и только когда есть что добавить" in text

    def test_with_the_check_off_it_is_the_roll_alone(self) -> None:
        text = screens.build_behavior(
            _view({"relevancy_gate_enabled": False}, random_response_chance=0.05), "ru"
        ).text
        assert "Встревает: в 5 % сообщений, без зова" in text
        assert "добавить" not in text

    def test_zero_says_never(self) -> None:
        text = screens.build_behavior(_view(random_response_chance=0.0), "ru").text
        assert "Встревает: никогда" in text

    def test_the_frequency_button_comes_first_and_shares_the_lines_name(self) -> None:
        screen = screens.build_behavior(_view(random_response_chance=0.05), "ru")
        first = screen.rows[0][0]
        assert first.label == "🎲 Встревает · 5 %"
        assert first.callback == f"adm_pnl_pct:ru:{CID}:rc"
        assert "Встревает:" in screen.text

    def test_the_language_button_shares_the_first_row(self) -> None:
        screen = screens.build_behavior(_view(), "ru")
        assert [b.callback for b in screen.rows[0]] == [
            f"adm_pnl_pct:ru:{CID}:rc",
            f"adm_pnl_lg:ru:{CID}",
        ]
        assert screen.rows[0][1].label == "🌐 Язык ответов"
        assert "Язык ответов: русский" in screen.text

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_widest_frequency_button_fits_half_a_row(self, lang: str) -> None:
        screen = screens.build_behavior(_view(random_response_chance=0.175), lang)
        assert label_width_px(screen.rows[0][0].label) <= clip_room(2)
        assert label_width_px(screen.rows[0][1].label) <= clip_room(2)


class TestOnlyTheTriggerWordsStayFixed:
    def test_behavior_says_it_in_plain_text(self) -> None:
        text = screens.build_behavior(_view(), "ru").text
        assert "Слова, которыми зовут бота, здесь не меняются." in text
        assert "здесь не меняются" not in _italic_spans(text)
        assert "в базе" not in text

    def test_stickers_no_longer_claim_the_database(self) -> None:
        assert "в базе" not in screens.build_stickers(_view(), "ru").text
        assert "database" not in screens.build_stickers(_view(), "en").text


# ── The picker ────────────────────────────────────────────────────────────


class TestFrequencyPicker:
    def test_presets_save_on_a_tap_and_the_current_one_is_marked(self) -> None:
        screen = screens.build_chance_prompt(_view(random_response_chance=0.05), "rc", "ru")
        assert _all_labels(screen)[:4] == ["Никогда 0 %", "Редко 2 %", "• Иногда 5 %", "Часто 10 %"]
        assert _payloads(screen)[:4] == [f"adm_pnl_pctset:ru:{CID}:rc:{p}" for p in (0, 2, 5, 10)]
        assert f"adm_pnl_pct:ru:{CID}:rc:in" in _payloads(screen)

    def test_the_back_button_is_named_by_where_it_goes(self) -> None:
        screen = screens.build_chance_prompt(_view(), "rc", "ru")
        last = screen.rows[-1][-1]
        assert last.label == "⬅️ Поведение"
        assert last.callback == f"adm_pnl_grp:ru:{CID}:behavior"

    def test_an_off_preset_value_shows_as_current(self) -> None:
        screen = screens.build_chance_prompt(_view(random_response_chance=0.003), "rc", "ru")
        assert "Сейчас 0.3 %" in screen.text
        assert not any(label.startswith("• ") for label in _all_labels(screen))

    def test_it_says_why_there_is_a_limit(self) -> None:
        text = screens.build_chance_prompt(_view(), "rc", "ru").text
        assert "платный запрос к модели" in text
        assert "20 %" in text

    def test_the_typed_prompt_states_the_range(self) -> None:
        screen = screens.build_chance_input(_view(random_response_chance=0.05), "rc", "ru")
        assert screen.id == "chance.in"
        assert "от 0 до 20" in screen.text
        assert _payloads(screen) == [f"adm_pnl_pctx:ru:{CID}:rc"]


class TestStickerChances:
    def test_the_stickers_screen_leads_to_how_often(self) -> None:
        screen = screens.build_stickers(_view(), "ru")
        assert len(screen.rows) <= 5
        last = screen.rows[-1]
        assert [b.callback for b in last] == [f"adm_pnl_pct:ru:{CID}", f"adm_pnl_menu:ru:{CID}"]
        assert last[0].label == "🎲 Как часто"

    def test_the_dead_sticker_chance_is_not_shown(self) -> None:
        """``sticker_response_chance`` is read by nothing: a number for it is a
        setting that does nothing."""
        assert "Отвечает стикером" not in screens.build_stickers(_view(), "ru").text

    def test_how_often_lists_both_chances_with_their_markers(self) -> None:
        screen = screens.build_sticker_chances(_view(), "ru")
        assert screen.id == "chance.stickers"
        assert _all_labels(screen)[:2] == [
            "🎲 Стикер на стикер · 50 % · общее",
            "🎲 Стикер к фото · 30 % · общее",
        ]
        assert _payloads(screen)[:2] == [
            f"adm_pnl_pct:ru:{CID}:sx",
            f"adm_pnl_pct:ru:{CID}:ix",
        ]
        assert screen.rows[-1][-1].label == "⬅️ Стикеры"

    def test_a_sticker_picker_goes_back_to_how_often(self) -> None:
        screen = screens.build_chance_prompt(_view(), "sx", "ru")
        assert "• Через раз 50 %" in _all_labels(screen)
        last = screen.rows[-1][-1]
        assert (last.label, last.callback) == ("⬅️ Как часто", f"adm_pnl_pct:ru:{CID}")

    def test_a_switched_off_answer_says_the_number_waits(self) -> None:
        screen = screens.build_chance_prompt(
            _view({"sticker_reply_to_sticker_enabled": False}), "sx", "ru"
        )
        assert "выключен" in screen.text

    def test_a_sticker_answer_may_be_typed_up_to_100(self) -> None:
        assert "от 0 до 100" in screens.build_chance_input(_view(), "ix", "ru").text


class TestLanguagePicker:
    def test_marks_the_current_language_and_saves_on_a_tap(self) -> None:
        screen = screens.build_language_prompt(_view(language="ru"), "ru")
        assert screen.id == "chat.lang"
        assert _all_labels(screen) == ["• Русский", "Английский", "⬅️ Поведение"]
        assert _payloads(screen) == [
            f"adm_pnl_lgset:ru:{CID}:ru",
            f"adm_pnl_lgset:ru:{CID}:en",
            f"adm_pnl_grp:ru:{CID}:behavior",
        ]

    def test_an_unknown_stored_value_is_shown_and_not_marked(self) -> None:
        screen = screens.build_language_prompt(_view(language="<de>"), "ru")
        assert "&lt;de&gt;" in screen.text
        assert not any(label.startswith("• ") for label in _all_labels(screen))


@pytest.mark.parametrize(
    "screen_id",
    ["chance.stickers", "chance.prompt", "chance.in", "chance.invalid", "chat.lang"],
)
def test_every_new_screen_has_a_sample(screen_id: str) -> None:
    """``test_screen_rules`` lints every sample in both languages; a screen
    without one is a screen nobody checked."""
    assert screen_id in load_all()


# ── Taps through the real router ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "handler"),
    [
        (f"adm_pnl_pct:ru:{CID}", "handle_chat_panel_chance_prompt"),
        (f"adm_pnl_pct:ru:{CID}:rc", "handle_chat_panel_chance_prompt"),
        (f"adm_pnl_pct:ru:{CID}:rc:in", "handle_chat_panel_chance_prompt"),
        (f"adm_pnl_pctset:ru:{CID}:rc:5", "handle_chat_panel_chance_set"),
        (f"adm_pnl_pctx:ru:{CID}:rc", "handle_chat_panel_chance_cancel"),
        (f"adm_pnl_lg:ru:{CID}", "handle_chat_panel_language_prompt"),
        (f"adm_pnl_lgset:ru:{CID}:en", "handle_chat_panel_language_set"),
    ],
)
def test_each_tap_reaches_its_handler(data: str, handler: str) -> None:
    assert first_match(data) == PANEL + handler


async def test_a_typed_number_in_the_prompt_is_its_answer() -> None:
    msg, _ = admin_text("5")
    assert await deliver("message", msg, FakeState(ARMED_STATE)) == (
        PANEL + "handle_chat_panel_chance_input"
    )


@pytest.mark.parametrize(
    "data", [f"adm_pnl_pct:ru:{CID}:rc:in", f"adm_pnl_pct:ru:{CID}:rc", f"adm_pnl_pctx:ru:{CID}:rc"]
)
async def test_the_prompts_own_taps_keep_it(data: str) -> None:
    fsm = FakeState(ARMED_STATE)
    await deliver("callback_query", admin_tap(data), fsm)
    assert await fsm.get_state() == ARMED_STATE.state


async def test_a_tap_elsewhere_ends_it_and_the_next_number_is_just_text() -> None:
    fsm = FakeState(ARMED_STATE)
    await deliver("callback_query", admin_tap(f"adm_pnl_pctset:ru:{CID}:rc:5"), fsm)
    assert await fsm.get_state() is None
    msg, _ = admin_text("5")
    assert await deliver("message", msg, fsm) == "message.handle_text_message"


# ── What a tap or a typed number writes ───────────────────────────────────


async def _set(callback: MagicMock, repo: MagicMock | None = None, **kw: Any) -> MagicMock:
    service = _service(**kw)
    await panel.handle_chat_panel_chance_set(
        callback,
        _state(),
        repo or _chat_settings_repo(ROW),
        _bot_config_repo(),
        service,
        _admin_repo(),
    )
    return service


class TestPresetSave:
    async def test_saves_and_returns_to_behavior_with_a_notice(self) -> None:
        repo = _chat_settings_repo(ROW)
        callback = _callback(f"adm_pnl_pctset:ru:{CHAT_ID}:rc:2")

        service = await _set(callback, repo, random_response_chance=0.02)

        repo.set_field.assert_awaited_once_with(CHAT_ID, "random_response_chance", 0.02)
        service.invalidate.assert_called_once_with(CHAT_ID)
        text, rows = _edited(callback)
        assert text.startswith("<i>✓ Встревает: 2 %</i>\n\n🗣 <b>Поведение</b>")
        assert rows[0][0] == f"adm_pnl_pct:ru:{CHAT_ID}:rc"

    async def test_a_sticker_chance_returns_to_how_often(self) -> None:
        repo = _chat_settings_repo(ROW)
        callback = _callback(f"adm_pnl_pctset:ru:{CHAT_ID}:sx:75")

        await _set(callback, repo)

        repo.set_field.assert_awaited_once_with(CHAT_ID, "sticker_reply_to_sticker_chance", 0.75)
        text, _ = _edited(callback)
        assert text.startswith("<i>✓ Стикер на стикер: 75 %</i>\n\n🎲 <b>Как часто</b>")

    @pytest.mark.parametrize(
        "data",
        [
            f"adm_pnl_pctset:ru:{CHAT_ID}:rc:25",  # above the cap
            f"adm_pnl_pctset:ru:{CHAT_ID}:rc:-1",
            f"adm_pnl_pctset:ru:{CHAT_ID}:rc:nan",
            f"adm_pnl_pctset:ru:{CHAT_ID}:sc:5",  # a column nothing reads
            f"adm_pnl_pctset:ru:{CHAT_ID}:rc",
            "adm_pnl_pctset:ru:x:rc:5",
        ],
    )
    async def test_an_unreadable_tap_is_stale(self, data: str, stale: AsyncMock) -> None:
        repo = _chat_settings_repo(ROW)

        await _set(_callback(data), repo)

        stale.assert_awaited_once()
        repo.set_field.assert_not_awaited()

    async def test_a_write_that_matched_no_row_claims_nothing(self) -> None:
        callback = _callback(f"adm_pnl_pctset:ru:{CHAT_ID}:rc:5")

        await _set(callback, _chat_settings_repo(ROW, written=False))

        text, _ = _edited(callback)
        assert CHAT_GONE in text
        assert "✓" not in text


async def _open(callback: MagicMock, state: MagicMock, row: dict[str, Any] | None = ROW) -> None:
    await panel.handle_chat_panel_chance_prompt(
        callback, state, _chat_settings_repo(row), _bot_config_repo(), _service(), _admin_repo()
    )


class TestOpening:
    async def test_without_a_code_it_is_how_often(self) -> None:
        callback = _callback(f"adm_pnl_pct:ru:{CHAT_ID}")

        await _open(callback, _state())

        assert _edited(callback)[0].startswith("🎲 <b>Как часто</b>")

    async def test_the_picker_arms_nothing(self) -> None:
        state = _state()
        callback = _callback(f"adm_pnl_pct:ru:{CHAT_ID}:rc")

        await _open(callback, state)

        state.set_state.assert_not_awaited()
        assert "• Иногда 5 %" in _labels(callback)

    async def test_typing_arms_its_own_state_before_the_prompt_goes_up(self) -> None:
        """The prompt's message is recorded only if the state is armed when it is
        delivered (`note_screen`), so the order is part of the contract."""
        order: list[str] = []
        state = _state()
        state.set_state.side_effect = lambda *_: order.append("armed")
        callback = _callback(f"adm_pnl_pct:ru:{CHAT_ID}:rc:in")
        callback.message.edit_text.side_effect = lambda *_a, **_k: order.append("shown")

        await _open(callback, state)

        state.set_state.assert_awaited_once_with(AdminStates.awaiting_response_chance)
        state.update_data.assert_awaited_once_with(
            pct_chat_id=CHAT_ID, pct_lang="ru", pct_code="rc", pct_prompt_id=PROMPT_ID
        )
        assert order == ["armed", "shown"]
        assert _edited(callback)[1] == [[f"adm_pnl_pctx:ru:{CHAT_ID}:rc"]]

    @pytest.mark.parametrize("code", ["zz", "sc", "tol"])
    async def test_an_unknown_code_is_stale(self, code: str, stale: AsyncMock) -> None:
        state = _state()

        await _open(_callback(f"adm_pnl_pct:ru:{CHAT_ID}:{code}:in"), state)

        stale.assert_awaited_once()
        state.set_state.assert_not_awaited()

    async def test_a_chat_without_settings_falls_back_to_the_list(self) -> None:
        state = _state()
        callback = _callback(f"adm_pnl_pct:ru:{CHAT_ID}:rc:in")

        await _open(callback, state, None)

        state.set_state.assert_not_awaited()
        assert CHAT_GONE in _edited(callback)[0]


def _armed(code: str = "rc") -> dict[str, Any]:
    return {"pct_chat_id": CHAT_ID, "pct_lang": "ru", "pct_code": code, "pct_prompt_id": PROMPT_ID}


async def _type(message: MagicMock, state: MagicMock, repo: MagicMock | None = None) -> MagicMock:
    repo = repo or _chat_settings_repo(ROW)
    await panel.handle_chat_panel_chance_input(
        message, state, repo, _bot_config_repo(), _service(), _admin_repo()
    )
    return repo


class TestTypedNumber:
    @pytest.mark.parametrize(
        ("code", "raw", "key", "value"),
        [
            ("rc", "20", "random_response_chance", 0.2),
            ("rc", "0", "random_response_chance", 0.0),
            ("rc", " 5,5 % ", "random_response_chance", 0.055),
            ("sx", "100", "sticker_reply_to_sticker_chance", 1.0),
            ("ix", "40", "image_comment_sticker_chance", 0.4),
        ],
    )
    async def test_a_number_in_range_saves(
        self, code: str, raw: str, key: str, value: float
    ) -> None:
        message = _message(raw)
        state = _state(_armed(code))

        repo = await _type(message, state)

        state.clear.assert_awaited_once()
        repo.set_field.assert_awaited_once_with(CHAT_ID, key, pytest.approx(value))
        message.answer.assert_awaited_once()
        assert _sent(message)[0].startswith("<i>✓ ")

    async def test_above_the_limit_it_says_what_the_limit_is(self) -> None:
        message = _message("21")
        state = _state(_armed("rc"))

        repo = await _type(message, state)

        repo.set_field.assert_not_awaited()
        state.clear.assert_not_awaited()
        text, rows = _sent(message)
        assert text.startswith(
            "<i>Больше 20 % нельзя: каждый такой ответ — платный запрос к модели.</i>"
        )
        assert text.count("Больше 20 % нельзя") == 1  # the notice says it; the body does not
        assert rows == [[f"adm_pnl_pctx:ru:{CHAT_ID}:rc"]]
        state.update_data.assert_awaited_once_with(pct_prompt_id=77)
        message.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=message.chat.id, message_id=PROMPT_ID, reply_markup=None
        )

    @pytest.mark.parametrize(
        ("code", "raw"), [("rc", "abc"), ("rc", "-1"), ("rc", "nan"), ("rc", "inf"), ("sx", "101")]
    )
    async def test_anything_else_asks_again(self, code: str, raw: str) -> None:
        message = _message(raw)
        state = _state(_armed(code))

        repo = await _type(message, state)

        repo.set_field.assert_not_awaited()
        cap = 20 if code == "rc" else 100
        assert _sent(message)[0].startswith(f"<i>Нужно число от 0 до {cap}")

    async def test_a_non_admin_value_is_dropped(self) -> None:
        message = _message("5", user_id=5)
        state = _state(_armed())

        repo = await _type(message, state)

        state.clear.assert_awaited_once()
        repo.set_field.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_without_a_known_field_the_state_is_cleared(self) -> None:
        message = _message("5")
        state = _state({**_armed(), "pct_code": "sc"})

        repo = await _type(message, state)

        state.clear.assert_awaited_once()
        repo.set_field.assert_not_awaited()

    async def test_a_number_for_a_chat_gone_shows_the_list(self) -> None:
        message = _message("5")

        await _type(message, _state(_armed()), _chat_settings_repo(ROW, written=False))

        text, _ = _sent(message)
        assert CHAT_GONE in text
        assert "✓" not in text


class TestCancel:
    @pytest.mark.parametrize(
        ("code", "title"), [("rc", "🗣 <b>Поведение</b>"), ("ix", "🎲 <b>Как часто</b>")]
    )
    async def test_clears_the_prompt_and_goes_home(self, code: str, title: str) -> None:
        state = _state(_armed(code), ARMED_STATE.state)
        callback = _callback(f"adm_pnl_pctx:ru:{CHAT_ID}:{code}")

        await panel.handle_chat_panel_chance_cancel(
            callback,
            state,
            _chat_settings_repo(ROW),
            _bot_config_repo(),
            _service(),
            _admin_repo(),
        )

        state.clear.assert_awaited_once()
        assert _edited(callback)[0].startswith(title)

    async def test_leaves_another_flows_state_alone(self) -> None:
        state = _state({}, AdminStates.awaiting_setting_value.state)

        await panel.handle_chat_panel_chance_cancel(
            _callback(f"adm_pnl_pctx:ru:{CHAT_ID}:rc"),
            state,
            _chat_settings_repo(ROW),
            _bot_config_repo(),
            _service(),
            _admin_repo(),
        )

        state.clear.assert_not_awaited()

    async def test_a_malformed_payload_still_clears_the_prompt(self, stale: AsyncMock) -> None:
        state = _state(_armed(), ARMED_STATE.state)

        await panel.handle_chat_panel_chance_cancel(
            _callback("adm_pnl_pctx:ru:x"),
            state,
            _chat_settings_repo(ROW),
            _bot_config_repo(),
            _service(),
            _admin_repo(),
        )

        state.clear.assert_awaited_once()
        stale.assert_awaited_once()


class TestLanguageSave:
    async def test_saves_and_returns_to_behavior_with_a_notice(self) -> None:
        repo = _chat_settings_repo(ROW)
        service = _service(language="en")
        callback = _callback(f"adm_pnl_lgset:ru:{CHAT_ID}:en")

        await panel.handle_chat_panel_language_set(
            callback, repo, _bot_config_repo(), service, _admin_repo()
        )

        repo.set_field.assert_awaited_once_with(CHAT_ID, "language", "en")
        service.invalidate.assert_called_once_with(CHAT_ID)
        text, _ = _edited(callback)
        assert text.startswith("<i>✓ Язык ответов: английский</i>\n\n🗣 <b>Поведение</b>")

    @pytest.mark.parametrize(
        "data", [f"adm_pnl_lgset:ru:{CHAT_ID}:de", f"adm_pnl_lgset:ru:{CHAT_ID}"]
    )
    async def test_a_language_the_bot_cannot_answer_in_is_stale(
        self, data: str, stale: AsyncMock
    ) -> None:
        repo = _chat_settings_repo(ROW)

        await panel.handle_chat_panel_language_set(
            _callback(data), repo, _bot_config_repo(), _service(), _admin_repo()
        )

        stale.assert_awaited_once()
        repo.set_field.assert_not_awaited()

    async def test_the_picker_opens_with_the_current_language_marked(self) -> None:
        callback = _callback(f"adm_pnl_lg:ru:{CHAT_ID}")

        await panel.handle_chat_panel_language_prompt(
            callback, _chat_settings_repo(ROW), _bot_config_repo(), _service(), _admin_repo()
        )

        text, rows = _edited(callback)
        assert text.startswith("🌐 <b>Язык ответов</b>")
        assert "• Русский" in _labels(callback)
        assert f"adm_pnl_lgset:ru:{CHAT_ID}:en" in _flat(rows)


def test_render_keeps_the_notice_above_the_title() -> None:
    """The notice renders as the ``✓`` line the handler tests read."""
    screen = screens.build_behavior(_view(), "ru")
    assert render_text(Screen(id=screen.id, text=screen.text, rows=(), notice="x")).startswith(
        "<i>✓ x</i>"
    )
