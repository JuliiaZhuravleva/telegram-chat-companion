"""Knowledge-base organizers of one chat: who may save facts with /remember.

The screens are built in ``screens/orgs.py``; this module reads and writes
``chat_settings.kb_organizer_ids`` and decides which screen answers what.

Callbacks (all ``adm_kb_*``, the chat id in the payload):

- ``adm_kb_orgs:`` — the organizer list, a page of it.
- ``adm_kb_org_rm:`` — remove one at once, the result offers ``↩️ Вернуть``.
- ``adm_kb_org_undo:`` — put the removed person back where they were.
- ``adm_kb_org_list:`` — the chat's participants by activity, a page of them.
- ``adm_kb_org_pick:`` — add the tapped participant.
- ``adm_kb_org_add:`` — the add prompt: a new message with Telegram's person
  search under the input (``request_users``), which also reads a forwarded
  message or a typed ``@username``.
- ``adm_kb_org_x:`` — leave the prompt from its failure answer.

The prompt is the one place with a reply keyboard, and every way out of it —
an add, ``✖️ Отмена``, the inline cancel, the list's back button — clears the
FSM state, sends ``ReplyKeyboardRemove`` on a short line of its own and then
the organizer list as a new message. A failed lookup keeps the prompt open.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Iterable
from html import escape as html_escape
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Message,
    MessageOriginHiddenUser,
    MessageOriginUser,
    ReplyKeyboardRemove,
    SharedUser,
)
from dishka.integrations.aiogram import FromDishka

from src.bot.custom_emoji import bare_label
from src.bot.keyboards.admin_kb import ORG_CANCEL_LABEL, ORG_PICK_LABEL, ORG_PICK_REQUEST_ID
from src.bot.screens import orgs
from src.bot.screens.i18n import lang_of
from src.bot.screens.model import Screen
from src.bot.screens.render import page_slice, show
from src.bot.screens.routes import parse
from src.bot.screens.stale import NO_ACCESS, show_stale
from src.bot.states.admin import AdminStates
from src.bot.utils import check_admin_direct, resolve_display_name
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.messages import MessageRepository
from src.utils import parse_user_id_list

logger = structlog.get_logger(__name__)

router = Router(name="admin_kb")

_ADD_CANCELLED = {
    "ru": "Добавление организатора отменено.",
    "en": "Adding an organizer cancelled.",
}
# The pick arrived but the prompt it answers is gone (cleared by /cancel, a
# pick from the participant list, a restart — MemoryStorage) — the keyboard
# outlives it behind the input's keyboard icon. Nothing is written: without
# the state there is no chat to write to.
_PICK_STALE = {
    "ru": "Этот выбор уже закрыт, никто не добавлен. "
    "Организаторы — в панели: чат → «🧠 Память» → «👥 Организаторы».",
    "en": "This pick is closed, nobody was added. "
    "Organizers are in the panel: chat → «🧠 Memory» → «👥 Organizers».",
}
# The picker's label arrived as TEXT: only a client that cannot show the
# picker sends it that way (a request_users button is never sent as text).
_PICK_UNSUPPORTED = {
    "ru": "Это приложение Telegram не открывает поиск. "
    "Перешлите сообщение этого человека или напишите его @username.",
    "en": "This Telegram app can't open the search. "
    "Forward a message from the person, or send their @username.",
}
_PICK_NOT_IN_CHAT = {
    "ru": "⚠️ Сейчас этого человека в чате нет.",
    "en": "⚠️ This person is not in the chat right now.",
}


def _is_label(labels: dict[str, str]) -> Callable[[str], bool]:
    """Match a reply-keyboard tap with or without its glyph.

    With drawn emoji on, ``CustomEmoji`` moves the glyph into the button's icon, so the tap
    sends ``"Отмена"``; with them off it sends ``"✖️ Отмена"``. Matching only the full label
    would send every tap of an iconed button to the @username reader as «не нашёл».
    """
    wanted = {bare_label(v) for v in labels.values()}
    return lambda text: bare_label(text) in wanted


# A plain @username is resolvable only through this chat's own message
# history: the Bot API has no username lookup, and get_chat_member needs a
# numeric id. Telegram usernames: letters, digits, underscores, 5-32 chars.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def _get_lang(raw: str | None) -> str:
    return lang_of(raw)


def _extract_username(text: str | None) -> str | None:
    """Parse a plain `@username` (or bare username) reply into a valid handle.

    Returns None if the text doesn't look like a single Telegram username —
    those replies get the "not found" answer.
    """
    if text is None:
        return None
    candidate = text.strip().lstrip("@")
    return candidate if _USERNAME_RE.match(candidate) else None


def _is_private(callback: CallbackQuery) -> bool:
    return isinstance(callback.message, Message) and callback.message.chat.type == "private"


def _parse_organizer_ids(raw: Any) -> list[int]:
    """Defensively parse kb_organizer_ids. Thin alias over the shared parser.

    The parse itself lives in `src/utils` so the `/remember` authority gate
    and this screen cannot disagree about who an organizer is.
    """
    return parse_user_id_list(raw)


async def _load(repo: ChatSettingsRepository, chat_id: int) -> tuple[list[int], str | None]:
    """The chat's organizer ids, in order, and its title (None when unknown)."""
    row = await repo.get(chat_id)
    if not row:
        return [], None
    title = row.get("chat_title")
    return _parse_organizer_ids(row.get("kb_organizer_ids")), title if title else None


async def _save(repo: ChatSettingsRepository, chat_id: int, ids: list[int]) -> None:
    await repo.set_field(chat_id, "kb_organizer_ids", json.dumps(ids))


async def _add_organizer(
    chat_settings_repo: ChatSettingsRepository, chat_id: int, user_id: int
) -> tuple[bool, list[int], str | None]:
    """Append ``user_id`` to the chat's organizers; a repeat writes nothing.

    Returns ``(added, organizer ids after, chat title)``: the caller renders
    the list from these ids instead of reading the row back.
    """
    ids, title = await _load(chat_settings_repo, chat_id)
    if user_id in ids:
        return False, ids, title
    ids.append(user_id)
    await _save(chat_settings_repo, chat_id, ids)
    return True, ids, title


def _shared_user_label(user: SharedUser) -> str:
    """``First Last (@nick)``, falling back to whichever part exists, then the id."""
    name = " ".join(part for part in (user.first_name, user.last_name) if part)
    if user.username:
        return f"{name} (@{user.username})" if name else f"@{user.username}"
    return name or str(user.user_id)


def _short_name(username: str | None, first_name: str | None, user_id: int, lang: str) -> str:
    """The name the organizer list shows: ``@nick``, else the first name."""
    if username:
        return f"@{username}"
    return first_name or orgs.MEMBER(lang).format(id=user_id)


async def _left_the_chat(bot: Bot | None, chat_id: int, user_id: int) -> bool:
    """True only when Telegram SAYS the person is not in the chat.

    Telegram's picker offers anyone — contacts, recent chats, global search —
    so the pick can be someone who never was in this chat. That is worth a
    line in the answer, not a refusal: the organizer list is an authority
    list, and someone about to be invited may be on it on purpose. Any API
    failure answers False: an unverifiable membership is not reported as
    absence.
    """
    if bot is None:
        return False
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError as exc:
        logger.warning(
            "Organizer pick: membership check failed",
            chat_id=chat_id,
            user_id=user_id,
            error=str(exc),
        )
        return False
    return member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)


async def _names(
    bot: Bot | None,
    chat_id: int,
    user_ids: Iterable[int],
    lang: str,
    known: dict[int, str] | None = None,
) -> dict[int, str]:
    """Display names for ``user_ids``, looked up concurrently; ``known`` ones are not."""
    known = known or {}
    wanted = [uid for uid in dict.fromkeys(user_ids) if uid not in known]
    found = await asyncio.gather(
        *(
            resolve_display_name(bot, chat_id, uid, orgs.MEMBER(lang).format(id=uid))
            for uid in wanted
        )
    )
    return {**known, **dict(zip(wanted, found, strict=True))}


async def _orgs_view(
    bot: Bot | None,
    lang: str,
    chat_id: int,
    title: str | None,
    ids: list[int],
    *,
    page: int = 0,
    notice: str | None = None,
    undo: tuple[int, int] | None = None,
    known: dict[int, str] | None = None,
) -> Screen:
    """The organizer list on ``page`` (clamped); names only for the people shown."""
    page, pages, offset = page_slice(len(ids), page, orgs.ORGS_PER_PAGE)
    shown = ids[offset : offset + orgs.ORGS_PER_PAGE]
    names = await _names(bot, chat_id, shown, lang, known)
    return orgs.orgs_screen(
        lang,
        chat_id,
        title,
        [(uid, names[uid]) for uid in shown],
        page=page,
        pages=pages,
        notice=notice,
        undo=undo,
    )


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def _lang_of_payload(data: str | None) -> str:
    parts = (data or "").split(":")
    return lang_of(parts[1] if len(parts) > 1 else None)


def _ints(data: str | None, n: int) -> list[int] | None:
    """The ``n`` integer fields after the language, or None when any is missing or garbled."""
    fields = parse(data, n + 1)
    if fields is None:
        return None
    try:
        return [int(field) for field in fields[1:]]
    except ValueError:
        return None


def _optional_int(data: str | None, index: int) -> int | None:
    """Field ``index`` (the language is 1) as an int, or None.

    Old buttons carry an origin token (``…:p``) where newer ones carry a page
    or a position; a field that is not a number is simply absent.
    """
    parts = (data or "").split(":")
    if len(parts) <= index:
        return None
    try:
        return int(parts[index])
    except ValueError:
        return None


async def _admin_tap(callback: CallbackQuery, bot_config_repo: BotConfigRepository) -> bool:
    """Gate a panel tap: the admin DM, a bot admin. Answers the query when it refuses."""
    if not _is_private(callback):
        await callback.answer()
        return False
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer(NO_ACCESS(_lang_of_payload(callback.data)), show_alert=True)
        return False
    return True


async def _in_prompt(state: FSMContext | None) -> bool:
    return state is not None and await state.get_state() == AdminStates.awaiting_kb_organizer.state


async def _leave_prompt(
    target: CallbackQuery | Message,
    state: FSMContext,
    line: str,
    screen: Screen,
) -> None:
    """End the add prompt: clear it, take the reply keyboard away on ``line``, then
    send the organizer list as a new message (a message carrying
    ``ReplyKeyboardRemove`` cannot carry inline buttons too)."""
    await state.clear()
    if isinstance(target, CallbackQuery):
        await target.answer()
        anchor = target.message
        if not isinstance(anchor, Message):
            return
    else:
        anchor = target
    await anchor.answer(line, reply_markup=ReplyKeyboardRemove())
    await show(anchor, screen)


# ---------------------------------------------------------------------------
# The organizer list
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_kb_orgs:"))
async def handle_kb_organizers(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Show a page of the chat's organizers.

    Reached from the memory screen, from the list's back button, and from an
    old message while the add prompt is open — then it is a way out of the
    prompt and ends it like ``✖️ Отмена``.
    """
    if not await _admin_tap(callback, bot_config_repo):
        return
    lang = _lang_of_payload(callback.data)
    head = _ints(callback.data, 1)
    if head is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    (chat_id,) = head
    page = _optional_int(callback.data, 3) or 0

    ids, title = await _load(chat_settings_repo, chat_id)
    screen = await _orgs_view(callback.bot, lang, chat_id, title, ids, page=page)
    if await _in_prompt(state):
        await _leave_prompt(callback, state, _ADD_CANCELLED[lang], screen)
        return
    await show(callback, screen)


@router.callback_query(F.data.startswith("adm_kb_org_rm:"))
async def handle_kb_organizer_remove(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """Remove an organizer at once and offer ``↩️ Вернуть`` (undo over confirm).

    The page that comes back is the one the person was on, so the admin does
    not lose their place in a long list.
    """
    if not await _admin_tap(callback, bot_config_repo):
        return
    lang = _lang_of_payload(callback.data)
    fields = _ints(callback.data, 2)
    if fields is None:
        await show_stale(callback, lang, data=kwargs)
        return
    chat_id, user_id = fields

    ids, title = await _load(chat_settings_repo, chat_id)
    if user_id not in ids:
        # Already gone (a second tap, another admin): show the list as it is.
        screen = await _orgs_view(callback.bot, lang, chat_id, title, ids)
        await show(callback, screen)
        return
    position = ids.index(user_id)
    ids.remove(user_id)
    await _save(chat_settings_repo, chat_id, ids)
    logger.info("Organizer removed", chat_id=chat_id, user_id=user_id)

    removed = await _names(callback.bot, chat_id, [user_id], lang)
    screen = await _orgs_view(
        callback.bot,
        lang,
        chat_id,
        title,
        ids,
        page=orgs.page_of(position),
        notice=orgs.REMOVED(lang).format(n=removed[user_id]),
        undo=(user_id, position),
    )
    await show(callback, screen)


@router.callback_query(F.data.startswith("adm_kb_org_undo:"))
async def handle_kb_organizer_undo(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    **kwargs: Any,
) -> None:
    """Put a just-removed organizer back at the position they had."""
    if not await _admin_tap(callback, bot_config_repo):
        return
    lang = _lang_of_payload(callback.data)
    fields = _ints(callback.data, 2)
    if fields is None:
        await show_stale(callback, lang, data=kwargs)
        return
    chat_id, user_id = fields

    ids, title = await _load(chat_settings_repo, chat_id)
    position = ids.index(user_id) if user_id in ids else None
    if position is None:
        wanted = _optional_int(callback.data, 4)
        position = len(ids) if wanted is None else min(max(wanted, 0), len(ids))
        ids.insert(position, user_id)
        await _save(chat_settings_repo, chat_id, ids)
        logger.info("Organizer restored", chat_id=chat_id, user_id=user_id)

    names = await _names(callback.bot, chat_id, [user_id], lang)
    screen = await _orgs_view(
        callback.bot,
        lang,
        chat_id,
        title,
        ids,
        page=orgs.page_of(position),
        notice=orgs.RESTORED(lang).format(n=names[user_id]),
        known=names,
    )
    await show(callback, screen)


# ---------------------------------------------------------------------------
# Adding: the participant list
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_kb_org_list:"))
async def handle_kb_organizer_list(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    message_repo: FromDishka[MessageRepository],
    **kwargs: Any,
) -> None:
    """The chat's participants, most active first: add by tapping a name instead
    of guessing a forward or an @username. Current organizers are marked."""
    if not await _admin_tap(callback, bot_config_repo):
        return
    lang = _lang_of_payload(callback.data)
    head = _ints(callback.data, 1)
    if head is None:
        await show_stale(callback, lang, data=kwargs)
        return
    (chat_id,) = head
    page = max(_optional_int(callback.data, 3) or 0, 0)

    per_page = orgs.LIST_PER_PAGE
    candidates, total = await message_repo.get_top_active_users(chat_id, page, per_page)
    clamped, pages, _ = page_slice(total, page, per_page)
    if clamped != page:
        # The list shrank since the button was drawn: show its last real page.
        page = clamped
        candidates, total = await message_repo.get_top_active_users(chat_id, page, per_page)
    ids, title = await _load(chat_settings_repo, chat_id)

    rows: list[tuple[int, str]] = []
    for candidate in candidates:
        uid = int(candidate["user_id"])
        rows.append(
            (uid, orgs.candidate_name(candidate.get("first_name"), candidate.get("username"), uid))
        )
    await show(
        callback,
        orgs.org_list_screen(lang, chat_id, title, rows, current=ids, page=page, pages=pages),
    )


@router.callback_query(F.data.startswith("adm_kb_org_pick:"))
async def handle_kb_organizer_pick(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Add the tapped participant. A tap on someone already on the list only says so.

    With the add prompt open (the list was opened from its failure answer),
    the pick also ends the prompt and takes its keyboard away.
    """
    if not await _admin_tap(callback, bot_config_repo):
        return
    lang = _lang_of_payload(callback.data)
    fields = _ints(callback.data, 2)
    if fields is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    chat_id, user_id = fields

    added, ids, title = await _add_organizer(chat_settings_repo, chat_id, user_id)
    if not added:
        await callback.answer(orgs.ALREADY_TOAST(lang))
        return
    logger.info("Organizer added from the participant list", chat_id=chat_id, user_id=user_id)

    names = await _names(callback.bot, chat_id, [user_id], lang)
    notice = orgs.ADDED(lang).format(n=names[user_id])
    page = orgs.page_of(ids.index(user_id))
    if await _in_prompt(state):
        screen = await _orgs_view(callback.bot, lang, chat_id, title, ids, page=page, known=names)
        await _leave_prompt(callback, state, f"✓ {html_escape(notice)}.", screen)
        return
    screen = await _orgs_view(
        callback.bot, lang, chat_id, title, ids, page=page, notice=notice, known=names
    )
    await show(callback, screen)


# ---------------------------------------------------------------------------
# Adding: the prompt (forward, @username, Telegram's person search)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("adm_kb_org_add:"))
async def handle_kb_organizer_add_prompt(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Send the add prompt with Telegram's person search under the input.

    A NEW message, not an edit: a reply keyboard can only be attached by
    sending (the edit methods take inline markup only).
    """
    if not await _admin_tap(callback, bot_config_repo):
        return
    lang = _lang_of_payload(callback.data)
    head = _ints(callback.data, 1)
    if head is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    (chat_id,) = head

    await state.set_state(AdminStates.awaiting_kb_organizer)
    await state.update_data(kb_chat_id=chat_id, kb_lang=lang)
    _, title = await _load(chat_settings_repo, chat_id)
    await show(callback, orgs.org_add_screen(lang, title, chat_id))


@router.callback_query(F.data.startswith("adm_kb_org_x:"))
async def handle_kb_organizer_add_cancel(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """``✖️ Отмена`` on the prompt's failure answer: the same exit as the reply button."""
    if not await _admin_tap(callback, bot_config_repo):
        return
    lang = _lang_of_payload(callback.data)
    head = _ints(callback.data, 1)
    if head is None:
        await show_stale(callback, lang, data=kwargs, state=state)
        return
    (chat_id,) = head

    ids, title = await _load(chat_settings_repo, chat_id)
    screen = await _orgs_view(callback.bot, lang, chat_id, title, ids)
    current = await state.get_state()
    if current not in (None, AdminStates.awaiting_kb_organizer.state):
        # An old answer tapped while another dialog is open: that dialog is
        # not this button's to end, so only the list is shown.
        await show(callback, screen)
        return
    await _leave_prompt(callback, state, _ADD_CANCELLED[lang], screen)


async def _added(
    message: Message,
    state: FSMContext,
    chat_settings_repo: ChatSettingsRepository,
    lang: str,
    chat_id: int,
    user_id: int,
    *,
    label: str,
    short_name: str,
    check_membership: bool = False,
) -> None:
    """Write the organizer and answer from the prompt: the line, then the list."""
    added, ids, title = await _add_organizer(chat_settings_repo, chat_id, user_id)
    template = orgs.ADDED if added else orgs.ALREADY
    line = f"✓ {html_escape(template(lang).format(n=label))}."
    if added:
        logger.info("Organizer added from the prompt", chat_id=chat_id, user_id=user_id)
        if check_membership and await _left_the_chat(message.bot, chat_id, user_id):
            line = f"{line}\n{_PICK_NOT_IN_CHAT[lang]}"
    screen = await _orgs_view(
        message.bot,
        lang,
        chat_id,
        title,
        ids,
        page=orgs.page_of(ids.index(user_id)),
        known={user_id: short_name},
    )
    await _leave_prompt(message, state, line, screen)


@router.message(
    F.users_shared,
    F.users_shared.request_id == ORG_PICK_REQUEST_ID,
    F.chat.type == "private",
)
async def handle_kb_organizer_shared(
    message: Message,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    admin_repo: FromDishka[AdminRepository],
    state: FSMContext,
) -> None:
    """The admin picked a person with «🔍 Найти в Telegram» (``request_users``).

    Telegram delivers the pick as a service message from the admin carrying
    ``users_shared``. Matched in ANY state, ahead of the forward/@username
    reader: that reader's filter would take this message too (no text, no
    forward) and answer "not found". Out of the prompt's state it is a pick
    from a keyboard that outlived its prompt, answered as stale. A pick from
    another person picker (another ``request_id``) is not this one's.

    Every answer removes the keyboard. Mirrors sweet-home's sitter pick and
    nutricore's invite (``KeyboardButtonRequestUsers``).
    """
    shared = message.users_shared
    if shared is None or not await check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        logger.warning("Organizer pick ignored: not an admin")
        return

    current = await state.get_state()
    data = await state.get_data()
    chat_id = data.get("kb_chat_id") if current == AdminStates.awaiting_kb_organizer else None
    lang = (
        _get_lang(data.get("kb_lang"))
        if chat_id is not None
        else _get_lang(await admin_repo.get_admin_language(bot_config_repo))
    )

    if not shared.users or chat_id is None:
        logger.info("Organizer pick is stale", has_state=chat_id is not None)
        await message.answer(_PICK_STALE[lang], reply_markup=ReplyKeyboardRemove())
        return

    person = shared.users[0]
    await _added(
        message,
        state,
        chat_settings_repo,
        lang,
        chat_id,
        person.user_id,
        label=_shared_user_label(person),
        short_name=_short_name(person.username, person.first_name, person.user_id, lang),
        check_membership=True,
    )


@router.message(
    AdminStates.awaiting_kb_organizer,
    F.chat.type == "private",
    F.forward_origin.is_(None),
    F.text.func(_is_label(ORG_CANCEL_LABEL)),
)
async def handle_kb_organizer_cancel_label(
    message: Message,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    state: FSMContext,
) -> None:
    """«✖️ Отмена» under the prompt. A label is user text, so it is routed
    before the reader below, which would look it up as a username."""
    data = await state.get_data()
    lang = _get_lang(data.get("kb_lang"))
    chat_id = data.get("kb_chat_id")
    if not isinstance(chat_id, int):
        await state.clear()
        await message.answer(_ADD_CANCELLED[lang], reply_markup=ReplyKeyboardRemove())
        return
    ids, title = await _load(chat_settings_repo, chat_id)
    screen = await _orgs_view(message.bot, lang, chat_id, title, ids)
    await _leave_prompt(message, state, _ADD_CANCELLED[lang], screen)


@router.message(
    AdminStates.awaiting_kb_organizer,
    F.chat.type == "private",
    F.forward_origin.is_(None),
    F.text.func(_is_label(ORG_PICK_LABEL)),
)
async def handle_kb_organizer_pick_label_as_text(message: Message, state: FSMContext) -> None:
    """The picker's label arrived as text: this client cannot show the picker.
    The prompt stays open for the fallback (forward or @username)."""
    lang = _get_lang((await state.get_data()).get("kb_lang"))
    await message.answer(_PICK_UNSUPPORTED[lang])


@router.message(
    AdminStates.awaiting_kb_organizer,
    F.chat.type == "private",
    # A person picked from another picker is no forward and no @username.
    F.users_shared.is_(None),
    # A slash guard that does NOT break the forward path (TD-049). This
    # handler serves two inputs: a forwarded message, and a typed `@username`.
    # A bare `~F.text.startswith("/")` -- the pattern used elsewhere -- would
    # also reject a FORWARDED message whose own text happens to start with a
    # slash, and that is a legitimate organizer add: measured, forwarding a
    # message reading "/kb" currently adds the organizer, and the bare guard
    # sends it to the /kb command handler instead while leaving the state set.
    # So the guard applies only when the admin is TYPING. Captions count too:
    # aiogram reads a command from `text or caption`, so `/help` under a photo
    # is a real command that a text-only guard lets through.
    F.forward_origin.is_not(None) | (~F.text.startswith("/") & ~F.caption.startswith("/")),
)
async def handle_kb_organizer_add_reply(
    message: Message,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    message_repo: FromDishka[MessageRepository],
    state: FSMContext,
) -> None:
    """Resolve a forwarded message or a plain `@username` into a new organizer.

    Two independent resolution paths:
    - Forward: uses `forward_origin` (not the legacy `forward_from`, which
      Telegram stopped populating). `MessageOriginHiddenUser` means the sender
      has forward privacy on; a forward from a channel or a group names no
      person. Each gets its own answer.
    - Plain `@username`: resolved via this chat's message history (the Bot
      API has no username lookup). Only works for usernames the bot has
      already seen post in this chat; distinguishes "never seen this
      username anywhere" from "seen it, just not in this chat".

    A failed lookup answers with ``org.fail`` and keeps the prompt open: the
    admin's next forward or @username is read again.
    """
    # Read the context, then clear BEFORE the authority check. Hoisted
    # deliberately (TD-049): with the clear below the early return, an admin
    # removed from `bot_config.admin_ids` between opening this prompt and
    # answering it left the state set for the lifetime of the process --
    # `MemoryStorage` has no TTL, so only a restart cleared it.
    data = await state.get_data()
    chat_id = data.get("kb_chat_id")
    lang = _get_lang(data.get("kb_lang"))
    await state.clear()

    if not await check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        return

    if not isinstance(chat_id, int):
        return

    async def fail(reason: orgs.Reason) -> None:
        # Re-armed: a typo or a hidden forward is not the end of the add.
        await state.set_state(AdminStates.awaiting_kb_organizer)
        await state.update_data(kb_chat_id=chat_id, kb_lang=lang)
        _, title = await _load(chat_settings_repo, chat_id)
        await show(message, orgs.org_fail_screen(lang, chat_id, title, reason))

    origin = message.forward_origin
    if origin is not None:
        if isinstance(origin, MessageOriginHiddenUser):
            await fail("hidden")
            return
        if not isinstance(origin, MessageOriginUser):
            await fail("channel")
            return
        sender = origin.sender_user
        name = _short_name(sender.username, sender.first_name, sender.id, lang)
        await _added(
            message, state, chat_settings_repo, lang, chat_id, sender.id,
            label=name, short_name=name,
        )  # fmt: skip
        return

    username = _extract_username(message.text)
    if username is None:
        await fail("not_found")
        return
    found = await message_repo.find_by_username(chat_id, username)
    if found is None:
        elsewhere = await message_repo.username_seen_elsewhere(chat_id, username)
        await fail("elsewhere" if elsewhere else "not_found")
        return
    name = f"@{username}"
    await _added(
        message, state, chat_settings_repo, lang, chat_id, int(found["user_id"]),
        label=name, short_name=name,
    )  # fmt: skip
