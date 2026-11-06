"""Inline keyboards the chat panel still builds by hand: the instruction editor's.

Every other chat screen is `Screen` data now (``src/bot/screens/chat.py``).
The editor's prompt and preview are verbatim user text split over several
messages, which a `Screen` does not model yet, so its three keyboards stay
here.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_SP_CANCEL = {"ru": "❌ Отмена", "en": "❌ Cancel"}
_SP_SAVE = {"ru": "✅ Сохранить", "en": "✅ Save"}
_SP_REDO = {"ru": "🔄 Заново", "en": "🔄 Start over"}


def system_prompt_cancel_keyboard(lang: str, chat_id: int) -> InlineKeyboardMarkup:
    """The exit from the system-prompt input state, under the current prompt."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_SP_CANCEL[lang], callback_data=f"adm_pnl_spx:{lang}:{chat_id}"
                )
            ]
        ]
    )


def system_prompt_confirm_keyboard(
    lang: str, chat_id: int, rev: int, *, can_save: bool = True
) -> InlineKeyboardMarkup:
    """Save / start over / cancel, under the preview of a new prompt.

    ``rev`` is the draft version this preview shows (the id of the last
    message added to it). Save and «Start over» carry it, so a button under an
    older preview -- or under a preview from an earlier editing session --
    cannot act on a draft it does not show. ``can_save=False`` drops Save for a
    draft that lost a part to the length cap.

    The prefixes differ from ``adm_pnl_sp:`` at the character after ``sp``,
    so ``startswith("adm_pnl_sp:")`` cannot swallow them.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if can_save:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_SP_SAVE[lang],
                    callback_data=f"adm_pnl_spok:{lang}:{chat_id}:{rev}",
                    style="success",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=_SP_REDO[lang], callback_data=f"adm_pnl_spre:{lang}:{chat_id}:{rev}"
            ),
            InlineKeyboardButton(
                text=_SP_CANCEL[lang], callback_data=f"adm_pnl_spx:{lang}:{chat_id}"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
