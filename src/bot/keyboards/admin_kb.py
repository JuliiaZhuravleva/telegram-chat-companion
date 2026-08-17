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

from src.bot.nav import back_callback, origin_suffix


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


def kb_menu_keyboard(
    lang: str, *, chat_id: int, kb_enabled: bool, origin: str = ""
) -> InlineKeyboardMarkup:
    """Per-chat KB submenu: organizers + kb_enabled toggle.

    Toggle copy per docs/design/kb-copy-register.md §4 (reuses the
    notifications_keyboard boolean-toggle convention verbatim).

    ``origin`` (see ``bot/nav.py``) decides where Back goes and is
    carried into every button that leads to a screen which comes back
    *here* — otherwise toggling or visiting organizers would silently reset
    the return path the admin arrived on.
    """
    status = "✅" if kb_enabled else "⚫"
    label = "Сбор фактов" if lang == "ru" else "Fact collection"
    toggle_text = f"📚 {label}: {status}"

    orgs_text = "👥 Организаторы" if lang == "ru" else "👥 Organizers"
    suffix = origin_suffix(origin)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=orgs_text,
                    callback_data=f"adm_kb_orgs:{lang}:{chat_id}:0{suffix}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=f"adm_kb_toggle:{lang}:{chat_id}{suffix}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад" if lang == "ru" else "◀️ Back",
                    callback_data=back_callback(
                        origin, lang=lang, chat_id=chat_id, default=f"adm_kb:{lang}:0"
                    ),
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
    origin: str = "",
) -> InlineKeyboardMarkup:
    """Paginated organizer list with per-row remove + add-organizer row.

    Per docs/design/kb-copy-register.md §5: tapping a row removes that
    organizer directly (no confirm dialog — low-blast-radius admin edit).

    ``origin`` rides through every button here because they all lead back to
    the KB submenu, whose Back target it decides (``bot/nav.py``).
    """
    rows: list[list[InlineKeyboardButton]] = []
    suffix = origin_suffix(origin)

    for org in organizers:
        user_id = org.get("user_id")
        display_name = str(org.get("display_name") or user_id)[:30]
        rows.append(
            [
                InlineKeyboardButton(
                    text=display_name,
                    callback_data=f"adm_kb_org_rm:{lang}:{chat_id}:{user_id}{suffix}",
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
                    callback_data=f"adm_kb_orgs:{lang}:{chat_id}:{page - 1}{suffix}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"adm_kb_orgs:{lang}:{chat_id}:{page + 1}{suffix}",
                )
            )
        rows.append(nav)

    add_text = "➕ Добавить организатора" if lang == "ru" else "➕ Add organizer"
    rows.append(
        [
            InlineKeyboardButton(
                text=add_text,
                callback_data=f"adm_kb_org_add:{lang}:{chat_id}{suffix}",
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back",
                callback_data=f"adm_kb_menu:{lang}:{chat_id}{suffix}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_org_add_prompt_keyboard(
    lang: str, *, chat_id: int, origin: str = ""
) -> InlineKeyboardMarkup:
    """Single-button footer on the add-organizer prompt (B-2 picker entry point).

    Offers "show participants" as an alternative to the forward/@username
    reply the prompt text asks for -- browsing the picker doesn't cancel
    the pending reply, both paths stay live until one resolves it.
    """
    text = "👥 Показать участников" if lang == "ru" else "👥 Show participants"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"adm_kb_org_list:{lang}:{chat_id}:0{origin_suffix(origin)}",
                ),
            ],
        ]
    )


def _candidate_label(candidate: dict[str, object]) -> str:
    """Format a picker candidate as ``Name (@nick)`` per the B-2 review spec.

    Falls back to whichever of first_name/username is present, then the
    raw user_id -- mirrors ``kb_organizers_keyboard``'s existing
    organizer-row fallback chain.
    """
    first_name = candidate.get("first_name")
    username = candidate.get("username")
    if first_name and username:
        label = f"{first_name} (@{username})"
    elif username:
        label = f"@{username}"
    elif first_name:
        label = str(first_name)
    else:
        label = str(candidate.get("user_id"))
    return label[:35]


def kb_organizer_picker_keyboard(
    candidates: list[dict[str, object]],
    *,
    lang: str,
    chat_id: int,
    page: int,
    total: int,
    per_page: int = 5,
    origin: str = "",
) -> InlineKeyboardMarkup:
    """Paginated participant picker for adding an organizer (B-2).

    Candidates arrive pre-sorted by message count desc (``MessageRepository.
    get_top_active_users``); tapping a row adds that user directly -- same
    single-tap, no-confirm convention as ``kb_organizers_keyboard``'s remove
    row (low-blast-radius: easy to undo via that same remove button).
    """
    rows: list[list[InlineKeyboardButton]] = []
    suffix = origin_suffix(origin)

    for candidate in candidates:
        user_id = candidate.get("user_id")
        rows.append(
            [
                InlineKeyboardButton(
                    text=_candidate_label(candidate),
                    callback_data=f"adm_kb_org_pick:{lang}:{chat_id}:{user_id}{suffix}",
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
                    callback_data=f"adm_kb_org_list:{lang}:{chat_id}:{page - 1}{suffix}",
                )
            )
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"adm_kb_org_list:{lang}:{chat_id}:{page + 1}{suffix}",
                )
            )
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад" if lang == "ru" else "◀️ Back",
                callback_data=f"adm_kb_orgs:{lang}:{chat_id}:0{suffix}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
