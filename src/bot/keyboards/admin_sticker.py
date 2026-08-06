"""Inline keyboards for admin sticker management.

Callback data pattern: ``adm_stk_{action}:{lang}:{params...}``
"""

from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} ({learned}/{total_count})",
                    callback_data=f"adm_stk_set:{lang}:{set_name}:0",
                ),
            ]
        )

    # Pagination
    total_pages = max(1, math.ceil(total / per_page))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"adm_stk_sets:{lang}:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="noop",
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"adm_stk_sets:{lang}:{page + 1}",
            )
        )
    rows.append(nav)

    # Back → main admin menu (no intermediate sticker menu)
    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back",
                callback_data=f"adm_menu:{lang}",
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _status_badge(sticker: dict[str, object], lang: str, *, short: bool) -> str:
    """Return a localized status string for sticker analysis state.

    Three states: ✅ analyzed / ⏳ not analyzed / ⚠️ failed.
    Derived from ``visual_description`` (NULL ⇒ not analyzed),
    ``analysis_failed`` (bool), and ``analyzed_at`` (timestamp).

    Args:
        sticker: Row dict with at least ``visual_description`` and
            ``analysis_failed`` keys.
        lang: UI language — ``"ru"`` or ``"en"``.
        short: ``True`` → concise form for char-limited keyboard buttons.
            ``False`` → fuller form for detail-view lines.
    """
    failed = bool(sticker.get("analysis_failed", False))
    visual = sticker.get("visual_description")

    if failed:
        if short:
            return "⚠️ Ошибка" if lang == "ru" else "⚠️ Failed"
        return "⚠️ Анализ провалился" if lang == "ru" else "⚠️ Analysis failed"
    if not visual:
        if short:
            return "⏳ Не выполнен" if lang == "ru" else "⏳ Not analyzed"
        return (
            "⏳ Визуальный анализ не выполнен"
            if lang == "ru"
            else "⏳ Visual analysis not performed"
        )
    # Analyzed — return description (truncated for keyboard buttons)
    desc = str(visual)
    return desc[:25] if short else desc


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
        uses = s.get("total_uses", 0)
        status = _status_badge(s, lang, short=True)
        label = f"{emoji} {status} ({uses}x)"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"adm_stk_view:{lang}:{fuid}",
                ),
            ]
        )

    # Pagination
    total_pages = max(1, math.ceil(total / per_page))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"adm_stk_set:{lang}:{set_name}:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="noop",
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"adm_stk_set:{lang}:{set_name}:{page + 1}",
            )
        )
    rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back",
                callback_data=f"adm_stk_sets:{lang}:0",
            ),
        ]
    )

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
                text="🔄 Запустить заново" if lang == "ru" else "🔄 Run analysis",
                callback_data=f"adm_stk_reanalyze:{lang}:{file_unique_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🧹 Очистить анализ" if lang == "ru" else "🧹 Clear analysis",
                callback_data=f"adm_stk_clr_ask:{lang}:{file_unique_id}",
            ),
        ],
    ]

    # Back button: sticker message cleanup is handled via DB lookup in handlers
    back_data = f"adm_stk_back:{lang}:{set_name}:0" if set_name else f"adm_stk_sets:{lang}:0"

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back",
                callback_data=back_data,
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def sticker_dm_check_keyboard(
    file_unique_id: str,
    *,
    lang: str,
) -> InlineKeyboardMarkup:
    """Single "analyze" button shown when an admin's DM-checked sticker (B-1)
    isn't in the catalog yet. Analysis only runs on this explicit tap
    (ADR-0003) — the DM sticker check itself never learns silently.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Проанализировать" if lang == "ru" else "🔍 Analyze",
                    callback_data=f"adm_stk_dmchk:{lang}:{file_unique_id}",
                ),
            ],
        ]
    )


def sticker_reanalyze_retry_keyboard(
    file_unique_id: str,
    *,
    lang: str,
) -> InlineKeyboardMarkup:
    """Single Retry button shown after a failed re-analysis."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Повторить" if lang == "ru" else "🔄 Retry",
                    callback_data=f"adm_stk_reanalyze:{lang}:{file_unique_id}",
                ),
            ],
        ]
    )


def sticker_clear_confirm_keyboard(
    file_unique_id: str,
    *,
    lang: str,
) -> InlineKeyboardMarkup:
    """Yes/Cancel for confirming analysis clear."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧹 Очистить" if lang == "ru" else "🧹 Clear",
                    callback_data=f"adm_stk_clr:{lang}:{file_unique_id}",
                ),
                InlineKeyboardButton(
                    text="✖ Отмена" if lang == "ru" else "✖ Cancel",
                    callback_data=f"adm_stk_view:{lang}:{file_unique_id}",
                ),
            ],
        ]
    )
