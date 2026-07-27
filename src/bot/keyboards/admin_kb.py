"""Inline keyboards for the Knowledge Base admin sub-router (A4).

Callback data pattern: ``adm_kb_{action}:{lang}:{params...}``

KB (``chat_facts``, ADR-0003) is per-chat, so — unlike the sticker sub-router
(``admin_sticker.py``), which manages a single global sticker library — the
KB admin flow needs a chat-picker step before showing the per-chat submenu.
This mirrors the whitelist chat-list pattern (``chats_list_keyboard`` in
``admin.py``) rather than G2's originally-documented chat-agnostic
``adm_kb:{lang}:0`` submenu, which has no way to address a specific chat's
``kb_enabled``/``kb_organizer_ids`` — see A4's verdict notes for the
rationale (docs/design/kb-copy-register.md gates the *copy*, not this
chat-scoping mechanism).
"""

from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb_chat_picker_keyboard(
    chats: list[dict[str, object]],
    *,
    lang: str,
    page: int,
    total: int,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated list of whitelisted chats to pick one to manage KB for."""
    rows: list[list[InlineKeyboardButton]] = []

    for chat in chats:
        chat_id = chat.get("chat_id")
        title = str(chat.get("chat_title") or chat_id)[:35]
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"adm_kb_menu:{lang}:{chat_id}",
                ),
            ]
        )

    total_pages = max(1, math.ceil(total / per_page))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm_kb:{lang}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm_kb:{lang}:{page + 1}"))
    rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back",
                callback_data=f"adm_menu:{lang}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_menu_keyboard(lang: str, *, chat_id: int, kb_enabled: bool) -> InlineKeyboardMarkup:
    """Per-chat KB submenu: organizers + kb_enabled toggle.

    Toggle copy per docs/design/kb-copy-register.md §4 (reuses the
    notifications_keyboard boolean-toggle convention verbatim).
    """
    status = "✅" if kb_enabled else "⚫"
    label = "Сбор фактов" if lang == "ru" else "Fact collection"
    toggle_text = f"📚 {label}: {status}"

    orgs_text = "👥 Организаторы" if lang == "ru" else "👥 Organizers"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=orgs_text,
                    callback_data=f"adm_kb_orgs:{lang}:{chat_id}:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=f"adm_kb_toggle:{lang}:{chat_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад" if lang == "ru" else "◀️ Back",
                    callback_data=f"adm_kb:{lang}:0",
                ),
            ],
        ]
    )


def kb_organizers_keyboard(
    organizers: list[dict[str, object]],
    *,
    lang: str,
    chat_id: int,
    page: int,
    total: int,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated organizer list with per-row remove + add-organizer row.

    Per docs/design/kb-copy-register.md §5: tapping a row removes that
    organizer directly (no confirm dialog — low-blast-radius admin edit).
    """
    rows: list[list[InlineKeyboardButton]] = []

    for org in organizers:
        user_id = org.get("user_id")
        display_name = str(org.get("display_name") or user_id)[:30]
        rows.append(
            [
                InlineKeyboardButton(
                    text=display_name,
                    callback_data=f"adm_kb_org_rm:{lang}:{chat_id}:{user_id}",
                ),
            ]
        )

    total_pages = max(1, math.ceil(total / per_page)) if total else 1
    if total > per_page:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"adm_kb_orgs:{lang}:{chat_id}:{page - 1}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"adm_kb_orgs:{lang}:{chat_id}:{page + 1}",
                )
            )
        rows.append(nav)

    add_text = "➕ Добавить организатора" if lang == "ru" else "➕ Add organizer"
    rows.append(
        [
            InlineKeyboardButton(
                text=add_text,
                callback_data=f"adm_kb_org_add:{lang}:{chat_id}",
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back",
                callback_data=f"adm_kb_menu:{lang}:{chat_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_view_keyboard(lang: str, *, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Pagination-only footer for the public ``/kb`` view (DM, not admin-namespaced)."""
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"kb_view:{lang}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"kb_view:{lang}:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav])
