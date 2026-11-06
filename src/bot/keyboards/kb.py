"""Keyboards of the knowledge base outside the admin panel: ``/remember`` and ``/kb``.

``kb_undo:`` sits under a ``/remember`` confirmation in a group chat;
``kb_view:`` pages the public ``/kb`` view. Neither is an admin-panel prefix,
so neither belongs to the panel's knowledge-base screens. The ``/kb`` view
itself is a screen (``screens.entry.kb_group`` / ``kb_dm``); its pager row is
built there, and ``kb_view_keyboard`` only renders that same row as markup.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.screens.entry import kb_pager
from src.bot.screens.i18n import lang_of


def kb_undo_keyboard(lang: str, *, fact_id: int, owner_id: int) -> InlineKeyboardMarkup:
    """One button on a `/remember` confirmation: retire the fact just written.

    `owner_id` travels in the payload because this is the first write-capable
    button the project puts in a **group** chat, where Telegram lets any member
    press any inline button. The handler compares it against the presser and
    re-resolves KB authority as well — the payload proves who was offered the
    button, not that they may still use it.

    Trailing-colon prefix (`kb_undo:`) per the callback-prefix rule, so it cannot
    be matched by a future `kb_undo_all:`-style sibling. Widest realistic
    payload: `kb_undo:` + a bigint fact id + a 10-digit user id — well inside
    Telegram's 64-byte budget, pinned by
    `tests/unit/test_admin_kb_keyboards.py`.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Убрать" if lang == "ru" else "↩️ Remove",
                    callback_data=f"kb_undo:{fact_id}:{owner_id}",
                )
            ]
        ]
    )


def kb_view_keyboard(lang: str, *, page: int, total_pages: int) -> InlineKeyboardMarkup | None:
    """The ``/kb`` pager as markup — the row ``entry.kb_group``/``kb_dm`` draw — or None.

    ``◀ · 2/3 · ▶`` wraps around; the middle button is the shared no-op.
    """
    row = kb_pager(lang_of(lang), page, total_pages)
    if row is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=b.label, callback_data=b.callback) for b in row]
        ]
    )
