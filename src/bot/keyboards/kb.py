"""Keyboards of the knowledge base outside the admin panel: ``/remember`` and ``/kb``.

``kb_undo:`` sits under a ``/remember`` confirmation in a group chat;
``kb_view:`` pages the public ``/kb`` view. Neither is an admin-panel prefix,
so neither belongs to the panel's knowledge-base screens. Moved here unchanged
from ``keyboards/admin_kb.py``.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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


def kb_view_keyboard(lang: str, *, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Pagination-only footer for the public ``/kb`` view (DM, not admin-namespaced)."""
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"kb_view:{lang}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"kb_view:{lang}:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav])
