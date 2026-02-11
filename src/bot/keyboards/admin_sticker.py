"""Inline keyboards for admin sticker management.

Callback data pattern: ``adm_stk_{action}:{lang}:{params...}``
"""

from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def sticker_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Main sticker management menu."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Просмотр паков" if lang == "ru" else "Browse packs",
                callback_data=f"adm_stk_sets:{lang}:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Назад" if lang == "ru" else "Back",
                callback_data=f"adm_menu:{lang}",
            ),
        ],
    ])


def sticker_sets_keyboard(
    sets: list[dict[str, object]],
    *,
    lang: str,
    page: int,
    total: int,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of sticker sets."""
    rows: list[list[InlineKeyboardButton]] = []

    for s in sets:
        set_name = str(s.get("set_name", ""))
        title = str(s.get("set_title") or set_name)[:30]
        learned = s.get("learned_count", 0)
        total_count = s.get("total_count", 0)
        rows.append([
            InlineKeyboardButton(
                text=f"{title} ({learned}/{total_count})",
                callback_data=f"adm_stk_set:{lang}:{set_name}:0",
            ),
        ])

    # Pagination
    total_pages = max(1, math.ceil(total / per_page))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️",
            callback_data=f"adm_stk_sets:{lang}:{page - 1}",
        ))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}",
        callback_data="noop",
    ))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(
            text="▶️",
            callback_data=f"adm_stk_sets:{lang}:{page + 1}",
        ))
    rows.append(nav)

    # Back
    rows.append([
        InlineKeyboardButton(
            text="Назад" if lang == "ru" else "Back",
            callback_data=f"adm_stk:{lang}:0",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def sticker_set_detail_keyboard(
    stickers: list[dict[str, object]],
    *,
    set_name: str,
    lang: str,
    page: int,
    total: int,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of stickers in a set."""
    rows: list[list[InlineKeyboardButton]] = []

    for s in stickers:
        fuid = str(s.get("file_unique_id", ""))
        emoji = str(s.get("emoji") or "")
        desc = str(s.get("visual_description") or "no desc")[:25]
        uses = s.get("total_uses", 0)
        failed = s.get("analysis_failed", False)
        label = f"{emoji} {desc} ({uses}x)" if not failed else f"{emoji} [FAILED] ({uses}x)"
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"adm_stk_view:{lang}:{fuid}",
            ),
        ])

    # Pagination
    total_pages = max(1, math.ceil(total / per_page))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️",
            callback_data=f"adm_stk_set:{lang}:{set_name}:{page - 1}",
        ))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}",
        callback_data="noop",
    ))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(
            text="▶️",
            callback_data=f"adm_stk_set:{lang}:{set_name}:{page + 1}",
        ))
    rows.append(nav)

    rows.append([
        InlineKeyboardButton(
            text="Назад" if lang == "ru" else "Back",
            callback_data=f"adm_stk_sets:{lang}:0",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def sticker_detail_keyboard(
    file_unique_id: str,
    *,
    lang: str,
    set_name: str | None = None,
) -> InlineKeyboardMarkup:
    """Detail view for a single sticker."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Переанализировать" if lang == "ru" else "Re-analyze",
                callback_data=f"adm_stk_reanalyze:{lang}:{file_unique_id}",
            ),
        ],
    ]

    back_data = f"adm_stk_set:{lang}:{set_name}:0" if set_name else f"adm_stk_sets:{lang}:0"
    rows.append([
        InlineKeyboardButton(
            text="Назад" if lang == "ru" else "Back",
            callback_data=back_data,
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)
