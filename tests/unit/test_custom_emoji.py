"""The drawn emoji swap: only the bot's own screens, never data or model text."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from src.bot.custom_emoji import FENCE, MAX_PER_MESSAGE, CustomEmoji, bare_label, fence

BTN = {"⚙": "b-settings", "⬅": "b-back", "✅": "b-on", "❌": "b-decline", "🔥": "b-alarm"}
MARK = {"⚙": "m-settings", "✅": "m-on", "⚠": "m-warn", "🔥": "m-alarm"}


def _set(table: dict[str, str], vs16: bool = True) -> MagicMock:
    stickers = []
    for glyph, cid in table.items():
        s = MagicMock()
        s.emoji = glyph + ("️" if vs16 and glyph in "⚙⬅⚠" else "")
        s.custom_emoji_id = cid
        stickers.append(s)
    found = MagicMock()
    found.stickers = stickers
    return found


def _bot(*, fail: Exception | None = None) -> MagicMock:
    bot = MagicMock()
    bot.default = DefaultBotProperties(parse_mode="HTML")

    async def get_sticker_set(name: str) -> MagicMock:
        if fail is not None:
            raise fail
        return _set(BTN if name == "buttons" else MARK)

    bot.get_sticker_set = AsyncMock(side_effect=get_sticker_set)
    return bot


async def _on() -> tuple[CustomEmoji, MagicMock]:
    bot = _bot()
    mw = CustomEmoji()
    await mw.load(bot, "buttons", "marks")
    assert mw.on
    return mw, bot


def _kb(*labels: str, prefix: str = "adm_x:", url: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, url=url)
                if url
                else InlineKeyboardButton(text=label, callback_data=f"{prefix}{i}")
            ]
            for i, label in enumerate(labels)
        ]
    )


async def _send(mw: CustomEmoji, bot: MagicMock, method: Any) -> Any:
    sent: list[Any] = []

    async def make_request(_bot: Any, m: Any) -> str:
        sent.append(m)
        return "ok"

    await mw(make_request, bot, method)
    assert len(sent) == 1
    return sent[0]


def _buttons(method: Any) -> list[tuple[str, str | None]]:
    return [
        (b.text, b.icon_custom_emoji_id) for r in method.reply_markup.inline_keyboard for b in r
    ]


class TestButtons:
    @pytest.mark.asyncio
    async def test_a_screen_button_gets_the_drawn_icon(self) -> None:
        mw, bot = await _on()
        out = await _send(
            mw,
            bot,
            SendMessage(
                chat_id=1,
                text="x",
                reply_markup=_kb("⚙️ Настройки", "◀️ Назад", "1 ❌", "12 ⚙️", "1 ❌ x", "✅"),
            ),
        )
        assert _buttons(out) == [
            ("Настройки", "b-settings"),
            ("Назад", "b-back"),  # ◀️ borrows the ⬅️ picture
            ("1", "b-decline"),  # a numbered row button keeps its number
            ("12", "b-settings"),
            ("1 ❌ x", None),  # more than a number and a glyph: left alone
            ("✅", None),  # only a glyph: a client without icons would show nothing
        ]

    @pytest.mark.asyncio
    async def test_a_label_built_from_a_chat_title_is_never_an_icon(self) -> None:
        mw, bot = await _on()
        out = await _send(
            mw,
            bot,
            SendMessage(chat_id=1, text="x", reply_markup=_kb(fence("🔥 Чат друзей"), "✅ Да")),
        )
        assert _buttons(out) == [("🔥 Чат друзей", None), ("Да", "b-on")]

    @pytest.mark.asyncio
    async def test_the_fence_never_leaves_the_bot_even_when_off(self) -> None:
        mw = CustomEmoji()
        await mw.load(_bot(), None, None)
        assert not mw.on
        out = await _send(
            mw, _bot(), SendMessage(chat_id=1, text="x", reply_markup=_kb(fence("🔥 чат")))
        )
        assert _buttons(out) == [("🔥 чат", None)]
        assert FENCE not in out.reply_markup.inline_keyboard[0][0].text

    def test_fence_leaves_ordinary_titles_alone(self) -> None:
        assert fence("Companion Test") == "Companion Test"
        assert fence("-1001234") == "-1001234"
        assert fence("Чат") == "Чат"
        assert fence("🔥 чат") == FENCE + "🔥 чат"

    @pytest.mark.asyncio
    async def test_url_buttons_are_left_alone(self) -> None:
        mw, bot = await _on()
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Сайт", url="https://example.org")],
                [InlineKeyboardButton(text="⚙️ Меню", callback_data="adm_menu:ru")],
            ]
        )
        out = await _send(mw, bot, SendMessage(chat_id=1, text="x", reply_markup=markup))
        assert _buttons(out) == [("⚙️ Сайт", None), ("Меню", "b-settings")]


class TestScope:
    @pytest.mark.asyncio
    async def test_a_message_without_a_screen_keyboard_goes_out_unchanged(self) -> None:
        """Model replies, transcripts, summaries: the user's or the model's ✅ stays theirs."""
        mw, bot = await _on()
        for method in (
            SendMessage(chat_id=1, text="✅ готово\n🔥 огонь"),
            SendMessage(
                chat_id=1, text="✅ готово", reply_markup=_kb("✅ Да", prefix="sum_refresh:")
            ),
            AnswerCallbackQuery(callback_query_id="q", text="⚠️ Это старое превью"),
        ):
            out = await _send(mw, bot, method)
            assert out is method

    @pytest.mark.asyncio
    async def test_known_screen_prefixes(self) -> None:
        mw, bot = await _on()
        for prefix in ("adm_pnl:", "ar_list:", "help_close:", "kb_undo:"):
            out = await _send(
                mw,
                bot,
                SendMessage(chat_id=1, text="✅ ok", reply_markup=_kb("✅ Да", prefix=prefix)),
            )
            assert out.text.startswith("<tg-emoji"), prefix


class TestText:
    @pytest.mark.asyncio
    async def test_only_line_leading_marks_are_drawn(self) -> None:
        mw, bot = await _on()
        text = (
            "⚙️ <b>Настройки</b>\n"
            "Статус: ✅\n"
            "<b>Модуль:</b> ✅\n"
            "<b>✅ Включено</b>\n"
            "  ⚠️ с отступом\n"
            "<blockquote expandable>🔥 чужой текст\n✅ тоже чужой</blockquote>\n"
            "<code>✅ код</code>"
        )
        out = await _send(
            mw, bot, EditMessageText(chat_id=1, message_id=2, text=text, reply_markup=_kb("✅ Да"))
        )
        assert out.text == (
            '<tg-emoji emoji-id="m-settings">⚙️</tg-emoji> <b>Настройки</b>\n'
            "Статус: ✅\n"
            "<b>Модуль:</b> ✅\n"
            '<b><tg-emoji emoji-id="m-on">✅</tg-emoji> Включено</b>\n'
            '  <tg-emoji emoji-id="m-warn">⚠️</tg-emoji> с отступом\n'
            "<blockquote expandable>🔥 чужой текст\n✅ тоже чужой</blockquote>\n"
            "<code>✅ код</code>"
        )

    @pytest.mark.asyncio
    async def test_a_fenced_title_at_line_start_stays_plain_and_loses_the_fence(self) -> None:
        """A chat titled «🔥 …» in a panel header is the chat's emoji, not the bot's mark."""
        mw, bot = await _on()
        text = f"⚙️ <b>Настройки чата</b>\n\n{fence('🔥 Чат друзей')} <code>-100</code>"
        out = await _send(
            mw, bot, EditMessageText(chat_id=1, message_id=2, text=text, reply_markup=_kb("✅ Да"))
        )
        assert out.text == (
            '<tg-emoji emoji-id="m-settings">⚙️</tg-emoji> <b>Настройки чата</b>\n\n'
            "🔥 Чат друзей <code>-100</code>"
        )

    @pytest.mark.asyncio
    async def test_the_fence_leaves_message_text_even_when_off(self) -> None:
        mw = CustomEmoji()
        await mw.load(_bot(), None, None)
        method = SendMessage(chat_id=1, text=f"{fence('🔥 чат')}\n✅ ok")
        out = await _send(mw, _bot(), method)
        assert out.text == "🔥 чат\n✅ ok"

    @pytest.mark.asyncio
    async def test_plain_parse_mode_keeps_text_but_buttons_still_get_icons(self) -> None:
        mw, bot = await _on()
        out = await _send(
            mw,
            bot,
            SendMessage(chat_id=1, text="✅ ok", parse_mode=None, reply_markup=_kb("✅ Да")),
        )
        assert out.text == "✅ ok"
        assert _buttons(out) == [("Да", "b-on")]

    @pytest.mark.asyncio
    async def test_at_most_one_hundred_per_message(self) -> None:
        mw, bot = await _on()
        text = "\n".join(["✅ строка"] * (MAX_PER_MESSAGE + 5))
        out = await _send(mw, bot, SendMessage(chat_id=1, text=text, reply_markup=_kb("x")))
        assert out.text.count("<tg-emoji") == MAX_PER_MESSAGE


class TestFailures:
    @pytest.mark.asyncio
    async def test_an_unreadable_set_leaves_the_bot_plain(self) -> None:
        mw = CustomEmoji()
        await mw.load(_bot(fail=RuntimeError("boom")), "buttons", "marks")
        assert not mw.on
        assert "RuntimeError" in mw.off_reason

    @pytest.mark.asyncio
    async def test_a_refused_send_is_resent_plain_and_the_module_turns_off(self) -> None:
        mw, bot = await _on()
        method = SendMessage(chat_id=1, text="✅ ok", reply_markup=_kb("✅ Да"))
        sent: list[Any] = []

        async def make_request(_bot: Any, m: Any) -> str:
            sent.append(m)
            if len(sent) == 1:
                raise TelegramBadRequest(method=m, message="Bad Request: DOCUMENT_INVALID")
            return "ok"

        assert await mw(make_request, bot, method) == "ok"
        assert sent[0].text.startswith("<tg-emoji") and sent[1] is method
        assert not mw.on and "DOCUMENT_INVALID" in mw.off_reason

    @pytest.mark.asyncio
    async def test_any_other_refusal_is_raised_and_the_module_stays_on(self) -> None:
        mw, bot = await _on()

        async def make_request(_bot: Any, m: Any) -> str:
            raise TelegramBadRequest(method=m, message="Bad Request: chat not found")

        with pytest.raises(TelegramBadRequest):
            await mw(
                make_request, bot, SendMessage(chat_id=1, text="✅", reply_markup=_kb("✅ Да"))
            )
        assert mw.on


class TestReplyKeyboards:
    """The organizer picker's prompt carries a REPLY keyboard, its answers remove it.

    Before this, ``_is_screen`` knew only inline keyboards, so the prompt and every answer
    went out with plain emoji while the screen they came from was drawn.
    """

    @staticmethod
    def _prompt() -> SendMessage:
        return SendMessage(
            chat_id=1,
            text="⚙️ <b>Новый</b>",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [
                        KeyboardButton(
                            text="✅ Найти",
                            request_users=KeyboardButtonRequestUsers(request_id=1),
                        )
                    ],
                    [KeyboardButton(text="❌ Отмена")],
                    [KeyboardButton(text="без значка")],
                ],
                one_time_keyboard=True,
            ),
        )

    @pytest.mark.asyncio
    async def test_a_reply_button_gets_the_icon_and_keeps_its_request(self) -> None:
        mw, bot = await _on()
        out = await _send(mw, bot, self._prompt())
        rows = out.reply_markup.keyboard
        assert [(b.text, b.icon_custom_emoji_id) for r in rows for b in r] == [
            ("Найти", "b-on"),
            ("Отмена", "b-decline"),
            ("без значка", None),
        ]
        assert rows[0][0].request_users.request_id == 1
        assert out.reply_markup.one_time_keyboard is True
        assert out.text.startswith('<tg-emoji emoji-id="m-settings">')

    @pytest.mark.asyncio
    async def test_an_answer_that_removes_the_keyboard_is_drawn(self) -> None:
        mw, bot = await _on()
        out = await _send(
            mw, bot, SendMessage(chat_id=1, text="✅ готово", reply_markup=ReplyKeyboardRemove())
        )
        assert out.text.startswith('<tg-emoji emoji-id="m-on">')
        assert isinstance(out.reply_markup, ReplyKeyboardRemove)

    @pytest.mark.asyncio
    async def test_off_the_reply_keyboard_goes_out_unchanged(self) -> None:
        mw = CustomEmoji()
        await mw.load(_bot(), None, None)
        method = self._prompt()
        assert await _send(mw, _bot(), method) is method


class TestBareLabel:
    def test_strips_one_leading_glyph_with_its_selector(self) -> None:
        assert bare_label("✖️ Отмена") == "Отмена"
        assert bare_label("🔍 Найти в Telegram") == "Найти в Telegram"

    def test_an_iconed_label_is_already_bare(self) -> None:
        assert bare_label("Отмена") == "Отмена"

    def test_digits_and_words_are_not_glyphs(self) -> None:
        assert bare_label("3 ❌") == "3 ❌"
        assert bare_label("@anya") == "@anya"
