"""Knowledge-base organizers of one chat: who may save facts with /remember.

The screens are built in ``screens/orgs.py``; this module reads and writes
``chat_settings.kb_organizer_ids`` and decides which screen answers what.

Callbacks (all ``adm_kb_*``, the chat id in the payload):

- ``adm_kb_orgs:`` — the organizer list, a page of it.
- ``adm_kb_org:`` — one organizer's card. A tap on a name opens it and writes
  nothing; until 2026-11 the same tap removed the person at once.
- ``adm_kb_org_inv:`` — make a one-person, seven-day invite link for an
  organizer who left or never joined, and show it.
- ``adm_kb_org_dm:`` — make a fresh link and send it to the person from the
  bot. Telegram refuses when the bot may not write first: a 403 (never opened
  a chat with the bot, or blocked it) or a 400 «chat not found» (the bot never
  met them). That answer is the only reliable signal, so the button is always
  there, and both refusals get the same line pointing to ``📤 Переслать``.
- ``adm_kb_org_rm:`` — remove one at once (from the card), the list that comes
  back offers ``↩️ Вернуть``.
- ``adm_kb_org_undo:`` — put the removed person back where they were.
- ``adm_kb_org_list:`` — the chat's participants by activity, a page of them.
- ``adm_kb_org_pick:`` — add the tapped participant.
- ``adm_kb_org_add:`` — the add prompt: a new message with Telegram's person
  search under the input (``request_users``), which also reads a forwarded
  message or a typed ``@username``.

The prompt is the one place with a reply keyboard, and every way out of it —
an add, ``✖️ Отмена``, the list's back button — clears the FSM state, sends
``ReplyKeyboardRemove`` on a short line of its own and then the organizer list
as a new message. A failed lookup keeps the prompt open.

Names. Telegram's person picker offers anyone, so an organizer is often not
in the group, and ``getChatMember`` then answers with an error instead of a
name. Every add path therefore stores the name it was given
(``kb_organizer_names``, migration 038), and the list names a person from, in
order: Telegram now, that stored name, the latest name in the chat's history,
and only then «участник <id>». When Telegram says the person is not in the
group, the name carries `` · не в чате``.

One name per person: the line that confirms an add, the person's row in the
list, the card and the notices after a remove or an undo all take the name
from that same lookup (``_names``), so they cannot disagree about what to call
someone. The store is ``OrganizerNameRepository`` from Dishka
(``RepositoryProvider``). The same single ``getChatMember`` (``_seen``) also
gives the card its membership state, so the list's `` · не в чате`` and the
card's state line cannot disagree either. When that call names nobody — the
person never joined, or Telegram answered with another error, a 400 or a 403
(not a timeout, a 429 or a 5xx) — ``getChat`` on their id is the second live
source: it names anyone who has opened the bot (measured 2026-11-07), and says
«chat not found» for everyone else. A stored ``@username`` may still name someone, but
only one Telegram gives now becomes the card's ``t.me`` link (``💬 Написать
лично``): a freed username can belong to another person months later. Without
one the link goes by id, ``tg://openmessage?user_id=`` (`orgs.person_url`),
which opened the private chat on Julia's phone even for a person the bot had
never seen. Should Telegram refuse that button anyway, the card is shown once
more without it (``_show_card``), so the refusal costs the button, not the card.

An invite link is a credential: whoever holds it joins. It is never logged —
events carry ``chat_id`` and ``user_id`` only, and an error text is scrubbed
of the link before it is logged (``_error_text``).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from html import escape as html_escape
from typing import Any, NamedTuple

import structlog
from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ChatMemberRestricted,
    Message,
    MessageOriginHiddenUser,
    MessageOriginUser,
)
from dishka.integrations.aiogram import FromDishka

from src.bot.custom_emoji import bare_label
from src.bot.keyboards.admin_kb import ORG_CANCEL_LABEL, ORG_PICK_LABEL, ORG_PICK_REQUEST_ID
from src.bot.screens import orgs
from src.bot.screens.i18n import lang_of
from src.bot.screens.model import Screen
from src.bot.screens.render import page_slice, show, take_reply_keyboard_away
from src.bot.screens.routes import parse
from src.bot.screens.stale import NO_ACCESS, show_stale
from src.bot.states.admin import AdminStates
from src.bot.utils import check_admin_direct
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.messages import MessageRepository
from src.database.repositories.organizer_names import OrganizerNameRepository
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


# What Telegram answers `getChatMember` for someone who is not in the group, as
# reported for organizers added from the picker: the person never joined, or the
# bot cannot see them as a participant. "user not found" (the bot has never met
# the id) is NOT here: it says nothing about membership.
_NOT_A_MEMBER = re.compile(r"member not found|PARTICIPANT_ID_INVALID", re.IGNORECASE)

# What `getChat` on a user id answers when the person never opened the bot: the
# expected answer for most organizers who never joined, so not a failure.
_NO_PRIVATE_CHAT = re.compile(r"chat not found", re.IGNORECASE)

# Failures of `getChatMember` that are no answer about the person: no reply at
# all, "too many requests", Telegram's own trouble. Every other Bot API error is
# an answer that named nobody, so `getChat` is worth one call.
_NO_ANSWER = (TelegramNetworkError, TelegramRetryAfter, TelegramServerError)

# Telegram's codes for a button it will not take (``BUTTON_USER_INVALID``,
# ``BUTTON_USER_PRIVACY_RESTRICTED``, ``BUTTON_URL_INVALID``) and for a bad URL.
# Case-sensitive on purpose: the codes are upper case, and a lower-case "url"
# turns up in entity errors that have nothing to do with buttons.
_LINK_REFUSED = re.compile(r"BUTTON_[A-Z_]+|\bURL\b")


class _Name(NamedTuple):
    label: str
    state: orgs.Membership = "unknown"
    full: str | None = None
    """First and last name, the card's name line."""
    username: str | None = None
    """Without the ``@``, and only from Telegram's answer now. A stored one stays
    out: Telegram frees a username on a rename or a deleted account and anyone
    may take it, so the card's ``t.me`` button would open a stranger's profile."""

    @property
    def absent(self) -> bool:
        """Telegram says the person is not in the group (left, removed, never joined)."""
        return self.state in orgs.ABSENT


class _Seen(NamedTuple):
    """What Telegram said: the state from ``getChatMember``, and the name when it
    or ``getChat`` gave one."""

    state: orgs.Membership
    name: str | None = None
    full: str | None = None
    username: str | None = None


async def _seen(bot: Bot | None, chat_id: int, user_id: int) -> _Seen:
    """What Telegram says about the person now, from one ``getChatMember``.

    The pick can be someone who never was in this chat, which is worth a mark,
    not a refusal: the organizer list is an authority list, and someone about to
    be invited may be on it on purpose. Only an answer that SAYS "not a member"
    reads as ``never_joined``; any other failure is ``unknown``, with no name.
    A restricted person who is not a member reads as ``left``: a link lets them
    back in, restrictions and all.

    When Telegram answers with an error — a 400 or a 403 alike (say, the bot
    was removed from the group) — it named nobody, and ``getChat`` is asked for
    the name (`_private`): the person's own chat with the bot does not depend
    on the group. Not after `_NO_ANSWER`, nor a failure outside the Bot API:
    the second call would only wait or be refused again.
    """
    if bot is None:
        return _Seen("unknown")
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except _NO_ANSWER as exc:
        logger.warning(
            "Organizer name: lookup failed", chat_id=chat_id, user_id=user_id, error=str(exc)
        )
        return _Seen("unknown")
    except TelegramAPIError as exc:
        if isinstance(exc, TelegramBadRequest) and _NOT_A_MEMBER.search(exc.message or ""):
            return await _private(bot, chat_id, user_id, "never_joined")
        logger.warning(
            "Organizer name: lookup failed", chat_id=chat_id, user_id=user_id, error=str(exc)
        )
        return await _private(bot, chat_id, user_id, "unknown")
    except Exception as exc:  # the list renders whatever the lookup does
        logger.warning(
            "Organizer name: lookup failed", chat_id=chat_id, user_id=user_id, error=str(exc)
        )
        return _Seen("unknown")
    state: orgs.Membership
    if member.status == ChatMemberStatus.KICKED:
        state = "removed"
    elif member.status == ChatMemberStatus.LEFT or (
        isinstance(member, ChatMemberRestricted) and not member.is_member
    ):
        state = "left"
    else:
        state = "in_chat"
    user = member.user
    return _Seen(
        state,
        orgs.display_name(user.username, user.first_name, user.last_name),
        orgs.full_name(user.first_name, user.last_name),
        orgs.username_of(user.username),
    )


async def _private(bot: Bot, chat_id: int, user_id: int, state: orgs.Membership) -> _Seen:
    """``state``, with the name ``getChat`` gives for the person's own chat.

    Telegram has a private chat for anyone who opened the bot, and ``getChat``
    on their id returns it with their name and username, live like
    ``getChatMember``'s. For anyone else it answers «chat not found» (measured
    2026-11-07): expected, so logged at debug only; any other failure is a
    warning. Either way ``state`` stands and the store names the person.
    """
    try:
        chat = await bot.get_chat(user_id)
    except TelegramBadRequest as exc:
        if _NO_PRIVATE_CHAT.search(exc.message or ""):
            logger.debug("Organizer name: no private chat", chat_id=chat_id, user_id=user_id)
        else:
            logger.warning(
                "Organizer name: getChat failed",
                chat_id=chat_id,
                user_id=user_id,
                error=exc.message,
            )
        return _Seen(state)
    except Exception as exc:  # the name is a nicety: the card renders without it
        logger.warning(
            "Organizer name: getChat failed", chat_id=chat_id, user_id=user_id, error=str(exc)
        )
        return _Seen(state)
    if getattr(chat, "type", None) != ChatType.PRIVATE:
        return _Seen(state)
    return _Seen(
        state,
        orgs.display_name(chat.username, chat.first_name, chat.last_name),
        orgs.full_name(chat.first_name, chat.last_name),
        orgs.username_of(chat.username),
    )


async def _show_card(callback: CallbackQuery, screen: Screen, chat_id: int, user_id: int) -> None:
    """``show`` an organizer card, and once more without ``💬 Написать лично`` if
    Telegram refuses that button.

    A refused button takes the whole message with it, as an edit and as a new
    message alike (``render.show`` tries both), so one bad link would leave the
    organizer with no card at all. The link is the most likely part to be
    refused: ``tg://user`` buttons were for both people tried (measured
    2026-11-07 — one the bot had never seen, ``BUTTON_USER_INVALID``; one it had,
    with privacy settings, ``BUTTON_USER_PRIVACY_RESTRICTED``; a seen person
    without such settings was not tried), and ``tg://openmessage`` was tried on
    one phone only. Only an error that names a
    button or a URL is answered this way: any other would fail the bare card
    just the same, so it propagates like every screen's. A try that raised has
    not answered the query (``render`` answers only once a screen is out), so
    the retry answers it, once.
    """
    try:
        await show(callback, screen)
    except TelegramBadRequest as exc:
        bare = orgs.without_person_link(screen)
        if bare is None or not _LINK_REFUSED.search(exc.message or ""):
            raise
        logger.warning(
            "Organizer card: link button refused",
            chat_id=chat_id,
            user_id=user_id,
            error=exc.message,
        )
        await show(callback, bare)


async def _names(
    bot: Bot | None,
    chat_id: int,
    user_ids: Iterable[int],
    lang: str,
    store: OrganizerNameRepository,
    known: Mapping[int, _Name] | None = None,
) -> dict[int, _Name]:
    """Names for ``user_ids``: Telegram (concurrently), then the store for those it
    did not name, then «участник <id>». ``known`` ones are not looked up again.

    The only place a person's name is chosen: every screen and line that names
    an organizer reads it from here (``orgs.display_name`` formats it)."""
    known = dict(known or {})
    wanted = [uid for uid in dict.fromkeys(user_ids) if uid not in known]
    live = await asyncio.gather(*(_seen(bot, chat_id, uid) for uid in wanted))
    unnamed = [uid for uid, seen in zip(wanted, live, strict=True) if seen.name is None]
    stored = await store.names(chat_id, unnamed) if unnamed else {}
    for uid, seen in zip(wanted, live, strict=True):
        name, full, username = seen.name, seen.full, seen.username
        if name is None and uid in stored:
            # The stored username still names the person (the list has always
            # shown it), but it is not handed on as theirs: see `_Name.username`.
            row = stored[uid]
            name = orgs.display_name(row.username, row.first_name, row.last_name)
            full = orgs.full_name(row.first_name, row.last_name)
        if name is None:
            name = orgs.MEMBER(lang).format(id=uid)
        known[uid] = _Name(name, seen.state, full, username)
    return known


async def _orgs_view(
    bot: Bot | None,
    lang: str,
    chat_id: int,
    title: str | None,
    ids: list[int],
    store: OrganizerNameRepository,
    *,
    page: int = 0,
    notice: str | None = None,
    notice_raw: str | None = None,
    undo: tuple[int, int] | None = None,
    known: Mapping[int, _Name] | None = None,
) -> Screen:
    """The organizer list on ``page`` (clamped); names only for the people shown."""
    page, pages, offset = page_slice(len(ids), page, orgs.ORGS_PER_PAGE)
    shown = ids[offset : offset + orgs.ORGS_PER_PAGE]
    names = await _names(bot, chat_id, shown, lang, store, known)
    return orgs.orgs_screen(
        lang,
        chat_id,
        title,
        [(uid, names[uid].label) for uid in shown],
        page=page,
        pages=pages,
        notice=notice,
        notice_raw=notice_raw,
        undo=undo,
        absent={uid for uid in shown if names[uid].absent},
    )


async def _card_view(
    bot: Bot | None,
    lang: str,
    chat_id: int,
    title: str | None,
    ids: list[int],
    user_id: int,
    store: OrganizerNameRepository,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
    known: Mapping[int, _Name] | None = None,
) -> Screen:
    """One organizer's card; ``user_id`` must be in ``ids`` (its position picks the back page)."""
    person = (await _names(bot, chat_id, [user_id], lang, store, known))[user_id]
    return orgs.org_card_screen(
        lang,
        chat_id,
        title,
        user_id,
        person.label,
        state=person.state,
        full=person.full,
        username=person.username,
        page=orgs.page_of(ids.index(user_id)),
        notice=notice,
        notice_raw=notice_raw,
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
    await take_reply_keyboard_away(anchor, line)
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
    organizer_names: FromDishka[OrganizerNameRepository],
    **kwargs: Any,
) -> None:
    """Show a page of the chat's organizers.

    Reached from the chat's root, from the list's back button, and from an
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
    screen = await _orgs_view(callback.bot, lang, chat_id, title, ids, organizer_names, page=page)
    if await _in_prompt(state):
        await _leave_prompt(callback, state, _ADD_CANCELLED[lang], screen)
        return
    await show(callback, screen)


@router.callback_query(F.data.startswith("adm_kb_org_rm:"))
async def handle_kb_organizer_remove(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    organizer_names: FromDishka[OrganizerNameRepository],
    **kwargs: Any,
) -> None:
    """Remove an organizer at once and offer ``↩️ Вернуть`` (undo over confirm).

    Reached from the card's ``✖️ Убрать из организаторов`` (and from list
    buttons sent before the card existed). The page that comes back is the one
    the person was on, so the admin does not lose their place in a long list.
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
        screen = await _orgs_view(callback.bot, lang, chat_id, title, ids, organizer_names)
        await show(callback, screen)
        return
    position = ids.index(user_id)
    ids.remove(user_id)
    await _save(chat_settings_repo, chat_id, ids)
    logger.info("Organizer removed", chat_id=chat_id, user_id=user_id)

    # The stored name stays behind on purpose: ↩️ Вернуть brings it back.
    removed = await _names(callback.bot, chat_id, [user_id], lang, organizer_names)
    screen = await _orgs_view(
        callback.bot,
        lang,
        chat_id,
        title,
        ids,
        organizer_names,
        page=orgs.page_of(position),
        notice=orgs.REMOVED(lang).format(n=removed[user_id].label),
        undo=(user_id, position),
    )
    await show(callback, screen)


@router.callback_query(F.data.startswith("adm_kb_org_undo:"))
async def handle_kb_organizer_undo(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    organizer_names: FromDishka[OrganizerNameRepository],
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

    names = await _names(callback.bot, chat_id, [user_id], lang, organizer_names)
    screen = await _orgs_view(
        callback.bot,
        lang,
        chat_id,
        title,
        ids,
        organizer_names,
        page=orgs.page_of(position),
        notice=orgs.RESTORED(lang).format(n=names[user_id].label),
        known=names,
    )
    await show(callback, screen)


# ---------------------------------------------------------------------------
# One organizer: the card and the invite
# ---------------------------------------------------------------------------

# What Telegram answers createChatInviteLink when the bot is not an admin or
# lacks can_invite_users: CHAT_ADMIN_REQUIRED, or "not enough rights to …".
_NO_INVITE_RIGHT = re.compile(r"CHAT_ADMIN_REQUIRED|not enough rights|rights", re.IGNORECASE)

# Any invite link's shape, so a scrub does not depend on knowing which link a
# failure text happens to quote.
_INVITE_LINK = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:\+|joinchat/)[\w-]+")


def _error_text(exc: BaseException, link: str | None = None) -> str:
    """A failure as it may be logged: Telegram's description or the class name, never a link.

    ``str(exc)`` of a Telegram error does not carry the request, but a message
    composed elsewhere might quote it; the scrub is what makes "never logged"
    hold whatever the text says: the link in hand by value, any other by shape.
    """
    text = exc.message if isinstance(exc, TelegramAPIError) else type(exc).__name__
    if link:
        text = text.replace(link, "<link>")
    return _INVITE_LINK.sub("<link>", text)


class _Link(NamedTuple):
    url: str | None
    failure: str | None = None
    """The card's line when no link was made (HTML, a `notice_raw`)."""


async def _create_link(bot: Bot, chat_id: int, user_id: int, name: str, lang: str) -> _Link:
    """A link for one person, valid `orgs.INVITE_DAYS` days, named after them."""
    try:
        created = await bot.create_chat_invite_link(
            chat_id,
            name=orgs.invite_link_name(name, lang),
            expire_date=datetime.now(UTC) + timedelta(days=orgs.INVITE_DAYS),
            member_limit=1,
        )
    except TelegramBadRequest as exc:
        if _NO_INVITE_RIGHT.search(exc.message or ""):
            logger.info("Organizer invite: no invite right", chat_id=chat_id, user_id=user_id)
            return _Link(None, orgs.NO_INVITE_RIGHT(lang))
        logger.warning(
            "Organizer invite: link failed",
            chat_id=chat_id,
            user_id=user_id,
            error=_error_text(exc),
        )
        return _Link(None, orgs.LINK_FAILED(lang))
    except Exception as exc:  # the card answers whatever Telegram does
        logger.warning(
            "Organizer invite: link failed",
            chat_id=chat_id,
            user_id=user_id,
            error=_error_text(exc),
        )
        return _Link(None, orgs.LINK_FAILED(lang))
    logger.info("Organizer invite link made", chat_id=chat_id, user_id=user_id)
    return _Link(created.invite_link)


# What sendMessage answers a bot that may not write to the person first, besides
# the 403 (a block, or a person who never opened the chat): 400 «chat not found»
# or PEER_ID_INVALID for someone the bot has never met at all — the same answer
# `src/bot/commands.py` gets for a per-admin command scope.
_NO_PRIVATE_CHAT = re.compile(r"chat not found|PEER_ID_INVALID", re.IGNORECASE)


def _cannot_write_first(exc: BaseException) -> bool:
    """Telegram refused because the bot may not start a chat with this person."""
    if isinstance(exc, TelegramForbiddenError):
        return True
    return isinstance(exc, TelegramBadRequest) and bool(_NO_PRIVATE_CHAT.search(exc.message or ""))


async def _send_link(
    bot: Bot, chat_id: int, user_id: int, lang: str, title: str | None, link: str
) -> tuple[str | None, str | None]:
    """DM ``link`` to the person: ``(notice, notice_raw)`` for the invite screen."""
    try:
        await bot.send_message(user_id, orgs.invite_dm_text(lang, title, link))
    except Exception as exc:  # the link exists and is on the screen either way
        if _cannot_write_first(exc):
            logger.info(
                "Organizer invite: bot cannot write first", chat_id=chat_id, user_id=user_id
            )
            return None, orgs.DM_FORBIDDEN(lang)
        logger.warning(
            "Organizer invite: message failed",
            chat_id=chat_id,
            user_id=user_id,
            error=_error_text(exc, link),
        )
        return None, orgs.DM_FAILED(lang)
    logger.info("Organizer invite sent", chat_id=chat_id, user_id=user_id)
    return orgs.SENT(lang), None


async def _not_an_organizer(
    callback: CallbackQuery,
    lang: str,
    chat_id: int,
    title: str | None,
    ids: list[int],
    store: OrganizerNameRepository,
) -> None:
    """The person left the list meanwhile (another admin, a second tap): the list, with a line."""
    screen = await _orgs_view(
        callback.bot,
        lang,
        chat_id,
        title,
        ids,
        store,
        notice_raw=orgs.NOT_ORGANIZER(lang),
    )
    await show(callback, screen)


@router.callback_query(F.data.startswith("adm_kb_org:"))
async def handle_kb_organizer_card(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    organizer_names: FromDishka[OrganizerNameRepository],
    **kwargs: Any,
) -> None:
    """One organizer's card. Opening it writes nothing."""
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
        await _not_an_organizer(callback, lang, chat_id, title, ids, organizer_names)
        return
    screen = await _card_view(callback.bot, lang, chat_id, title, ids, user_id, organizer_names)
    await _show_card(callback, screen, chat_id, user_id)


async def _invite(
    callback: CallbackQuery,
    chat_settings_repo: ChatSettingsRepository,
    organizer_names: OrganizerNameRepository,
    data: dict[str, Any],
    *,
    send: bool,
) -> None:
    """Make a link for an organizer outside the chat; with ``send``, DM it from the bot.

    Both re-check what the card showed: the person may have been removed from
    the list, or joined, since the card was drawn. Only ``left`` and
    ``never_joined`` get a link (`orgs.INVITABLE`).
    """
    lang = _lang_of_payload(callback.data)
    fields = _ints(callback.data, 2)
    if fields is None:
        await show_stale(callback, lang, data=data)
        return
    chat_id, user_id = fields

    ids, title = await _load(chat_settings_repo, chat_id)
    if user_id not in ids:
        await _not_an_organizer(callback, lang, chat_id, title, ids, organizer_names)
        return
    bot = callback.bot
    names = await _names(bot, chat_id, [user_id], lang, organizer_names)
    person = names[user_id]
    if bot is None or person.state not in orgs.INVITABLE:
        line = orgs.ALREADY_IN_CHAT if person.state == "in_chat" else orgs.NO_LINK
        screen = await _card_view(
            bot,
            lang,
            chat_id,
            title,
            ids,
            user_id,
            organizer_names,
            notice_raw=line(lang),
            known=names,
        )
        await _show_card(callback, screen, chat_id, user_id)
        return

    link = await _create_link(bot, chat_id, user_id, person.label, lang)
    if link.url is None:
        screen = await _card_view(
            bot,
            lang,
            chat_id,
            title,
            ids,
            user_id,
            organizer_names,
            notice_raw=link.failure,
            known=names,
        )
        await _show_card(callback, screen, chat_id, user_id)
        return

    notice, notice_raw = (
        await _send_link(bot, chat_id, user_id, lang, title, link.url) if send else (None, None)
    )
    screen = orgs.org_invite_screen(
        lang,
        chat_id,
        title,
        user_id,
        person.label,
        link.url,
        notice=notice,
        notice_raw=notice_raw,
        username=person.username,
    )
    await show(callback, screen)


@router.callback_query(F.data.startswith("adm_kb_org_inv:"))
async def handle_kb_organizer_invite(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    organizer_names: FromDishka[OrganizerNameRepository],
    **kwargs: Any,
) -> None:
    """A one-person, seven-day link for an organizer who left or never joined."""
    if not await _admin_tap(callback, bot_config_repo):
        return
    await _invite(callback, chat_settings_repo, organizer_names, kwargs, send=False)


@router.callback_query(F.data.startswith("adm_kb_org_dm:"))
async def handle_kb_organizer_invite_dm(
    callback: CallbackQuery,
    chat_settings_repo: FromDishka[ChatSettingsRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    organizer_names: FromDishka[OrganizerNameRepository],
    **kwargs: Any,
) -> None:
    """A fresh link, sent to the person from the bot; a 403 says to forward it instead."""
    if not await _admin_tap(callback, bot_config_repo):
        return
    await _invite(callback, chat_settings_repo, organizer_names, kwargs, send=True)


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
    organizer_names: FromDishka[OrganizerNameRepository],
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

    # The list's name came from the chat's history: keep it with the organizer.
    await organizer_names.remember_seen(chat_id, user_id)
    names = await _names(callback.bot, chat_id, [user_id], lang, organizer_names)
    notice = orgs.ADDED(lang).format(n=names[user_id].label)
    page = orgs.page_of(ids.index(user_id))
    if await _in_prompt(state):
        screen = await _orgs_view(
            callback.bot, lang, chat_id, title, ids, organizer_names, page=page, known=names
        )
        await _leave_prompt(callback, state, f"✓ {html_escape(notice)}.", screen)
        return
    screen = await _orgs_view(
        callback.bot,
        lang,
        chat_id,
        title,
        ids,
        organizer_names,
        page=page,
        notice=notice,
        known=names,
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


async def _added(
    message: Message,
    state: FSMContext,
    chat_settings_repo: ChatSettingsRepository,
    store: OrganizerNameRepository,
    lang: str,
    chat_id: int,
    user_id: int,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> None:
    """Write the organizer, keep the name this add path was given, and answer from
    the prompt: the line, then the list.

    Telegram is asked once about the new organizer; the same answer names them in
    the line and in the list, and says whether to warn that they are not in the
    chat. The line does not format the parts it was given on its own: then a
    pick would read «Аня Ким (@anya)» above a list row «@anya».
    """
    added, ids, title = await _add_organizer(chat_settings_repo, chat_id, user_id)
    # Before the list is drawn, and also on a repeat add: the newest name wins.
    await store.remember(
        chat_id, user_id, first_name=first_name, last_name=last_name, username=username
    )
    names = await _names(message.bot, chat_id, [user_id], lang, store)
    template = orgs.ADDED if added else orgs.ALREADY
    line = f"✓ {html_escape(template(lang).format(n=names[user_id].label))}."
    if added:
        logger.info("Organizer added from the prompt", chat_id=chat_id, user_id=user_id)
        if names[user_id].absent:
            line = f"{line}\n{_PICK_NOT_IN_CHAT[lang]}"
    screen = await _orgs_view(
        message.bot,
        lang,
        chat_id,
        title,
        ids,
        store,
        page=orgs.page_of(ids.index(user_id)),
        known=names,
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
    organizer_names: FromDishka[OrganizerNameRepository],
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
        await take_reply_keyboard_away(message, _PICK_STALE[lang])
        return

    person = shared.users[0]
    await _added(
        message,
        state,
        chat_settings_repo,
        organizer_names,
        lang,
        chat_id,
        person.user_id,
        first_name=person.first_name,
        last_name=person.last_name,
        username=person.username,
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
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    organizer_names: FromDishka[OrganizerNameRepository],
) -> None:
    """«✖️ Отмена» under the prompt. A label is user text, so it is routed
    before the reader below, which would look it up as a username.

    The way back shows the organizer list, names included, so it asks the
    same question every panel tap does: is this still a bot admin? Someone
    removed from the admins while the prompt was open gets the prompt closed
    and its keyboard taken away, and no list.
    """
    data = await state.get_data()
    lang = _get_lang(data.get("kb_lang"))
    chat_id = data.get("kb_chat_id")
    if not isinstance(chat_id, int) or not await check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        await state.clear()
        await take_reply_keyboard_away(message, _ADD_CANCELLED[lang])
        return
    ids, title = await _load(chat_settings_repo, chat_id)
    screen = await _orgs_view(message.bot, lang, chat_id, title, ids, organizer_names)
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
    organizer_names: FromDishka[OrganizerNameRepository],
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
        await _added(
            message, state, chat_settings_repo, organizer_names, lang, chat_id, sender.id,
            first_name=sender.first_name, last_name=sender.last_name, username=sender.username,
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
    await _added(
        message, state, chat_settings_repo, organizer_names, lang, chat_id, int(found["user_id"]),
        first_name=found.get("first_name"), username=username,
    )  # fmt: skip
