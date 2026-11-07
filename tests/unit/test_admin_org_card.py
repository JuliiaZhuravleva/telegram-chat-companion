"""The organizer card and the invite, through the REAL router.

Every handler test delivers the tap to ``src.bot.handlers.router`` with
``deliver(call=True)``: aiogram's own filters pick the handler and run it, so a
prefix that a neighbour (``adm_kb_orgs:``, ``adm_kb_org_rm:`` …) swallowed would
fail here, not pass on a function called by hand. Telegram is a double that
answers the way the Bot API does: ``getChatMember`` per membership state,
``getChat`` with a private chat or «chat not found», ``createChatInviteLink``
with a ``ChatInviteLink``, ``sendMessage`` with Telegram's own 403.

Before 2026-11 a tap on a name in the list REMOVED the person. The first test
here takes the name button off the list the handler really draws and taps it;
pointed back at ``adm_kb_org_rm:`` it fails (the mutation control in the PR).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import CreateChatInviteLink, GetChat, GetChatMember, SendMessage
from aiogram.types import (
    AcceptedGiftTypes,
    ChatFullInfo,
    ChatInviteLink,
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberRestricted,
    InlineKeyboardMarkup,
    User,
)
from structlog.testing import capture_logs

from src.bot.handlers import admin_kb
from src.bot.screens import orgs
from src.bot.screens.model import Screen
from src.bot.screens.registry import load_all
from src.bot.screens.routes import BACK_NAMES, cb
from src.bot.screens.samples import fake
from src.database.repositories.organizer_names import KnownName
from tests.unit.screen_routing import ADMIN_ID, FakeState, admin_tap, deliver, first_match

CID = fake.CHAT_ID
ORG = fake.USER_ID
OTHER = fake.USER_ID_OTHER
TITLE = fake.CHAT_TITLE
NICK = fake.USERNAME.lstrip("@")
FIRST, LAST = fake.PERSON.split(" ")

# Invented links in the shape Telegram returns; one per createChatInviteLink.
LINKS = [f"https://t.me/+AAAAAAAAAAAAAAA{n}" for n in range(1, 10)]

MEMBER_NOT_FOUND = "Bad Request: member not found"
CHAT_NOT_FOUND = "Bad Request: chat not found"
"""``getChat`` about a person who never opened a chat with the bot (measured 2026-11-07)."""
RIGHTS_ERRORS = [
    "Bad Request: not enough rights to manage chat invite links",
    "Bad Request: CHAT_ADMIN_REQUIRED",
]


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _no_rights(cls: type[Any]) -> dict[str, bool]:
    """Every required ``can_*`` field of a ChatMember type, all False: the
    type's own field list, so a field aiogram adds does not break the double."""
    return {
        name: False
        for name, field in cls.model_fields.items()
        if field.is_required() and name.startswith("can_")
    }


class Telegram:
    """The four Bot API calls the card makes, answered like Telegram, recorded.

    ``status`` is what ``getChatMember`` says about the organizer: ``member``,
    ``restricted`` (restricted and still a member), ``restricted_out``
    (restricted, ``is_member`` false), ``administrator``, ``creator``,
    ``left``, ``kicked``, ``never`` (Telegram's "member not found") or
    ``unknown`` (a network failure: nothing is known). ``private`` is what
    ``getChat`` on the person's id gives — ``(first, last, username)`` of a
    private chat, which Telegram has only for someone who opened the bot —
    else it fails with ``chat_error`` («chat not found», as measured).
    ``link_error`` makes ``createChatInviteLink`` fail with that text;
    ``dm_error`` is the class ``sendMessage`` raises, carrying ``dm_message``.
    """

    def __init__(
        self,
        status: str = "member",
        *,
        username: str | None = NICK,
        first: str = FIRST,
        last: str | None = LAST,
        link_error: str | None = None,
        dm_error: type[Exception] | None = None,
        dm_message: str = "Forbidden: bot can't initiate conversation with a user",
        private: tuple[str, str | None, str | None] | None = None,
        chat_error: str = CHAT_NOT_FOUND,
    ) -> None:
        self.status = status
        self.private = private
        self.chat_error = chat_error
        self.chats: list[int] = []
        self.username = username
        self.first = first
        self.last = last
        self.link_error = link_error
        self.dm_error = dm_error
        self.dm_message = dm_message
        self.links: list[dict[str, Any]] = []
        self.made: list[str] = []
        self.sent: list[tuple[int, str]] = []

    async def get_chat_member(self, chat_id: int, user_id: int) -> object:
        method = GetChatMember(chat_id=chat_id, user_id=user_id)
        if user_id != ORG:
            # Everyone else on the list is a plain member with a name of their own.
            other = User(id=user_id, is_bot=False, first_name="Другой", username="other_user")
            return ChatMemberMember(user=other)
        if self.status == "never":
            raise TelegramBadRequest(method=method, message=MEMBER_NOT_FOUND)
        if self.status == "unknown":
            raise TelegramNetworkError(method=method, message="timeout")
        user = User(
            id=user_id,
            is_bot=False,
            first_name=self.first,
            last_name=self.last,
            username=self.username,
        )
        if self.status == "left":
            return ChatMemberLeft(user=user)
        if self.status == "kicked":
            return ChatMemberBanned(user=user, until_date=datetime(2030, 1, 1, tzinfo=UTC))
        if self.status in ("restricted", "restricted_out"):
            return ChatMemberRestricted(
                user=user,
                is_member=self.status == "restricted",
                until_date=datetime(2030, 1, 1, tzinfo=UTC),
                **_no_rights(ChatMemberRestricted),
            )
        if self.status == "administrator":
            return ChatMemberAdministrator(
                user=user, is_anonymous=False, **_no_rights(ChatMemberAdministrator)
            )
        if self.status == "creator":
            return ChatMemberOwner(user=user, is_anonymous=False)
        assert self.status == "member", self.status
        return ChatMemberMember(user=user)

    async def get_chat(self, chat_id: int) -> ChatFullInfo:
        self.chats.append(chat_id)
        if self.private is None:
            raise TelegramBadRequest(method=GetChat(chat_id=chat_id), message=self.chat_error)
        first, last, username = self.private
        return ChatFullInfo(
            id=chat_id,
            type="private",
            first_name=first,
            last_name=last,
            username=username,
            accent_color_id=0,
            max_reaction_count=11,
            accepted_gift_types=AcceptedGiftTypes(
                unlimited_gifts=False,
                limited_gifts=False,
                unique_gifts=False,
                premium_subscription=False,
                gifts_from_channels=False,
            ),
        )

    async def create_chat_invite_link(self, chat_id: int, **kwargs: Any) -> ChatInviteLink:
        self.links.append({"chat_id": chat_id, **kwargs})
        if self.link_error is not None:
            method = CreateChatInviteLink(chat_id=chat_id, **kwargs)
            raise TelegramBadRequest(method=method, message=self.link_error)
        link = LINKS[len(self.made)]
        self.made.append(link)
        return ChatInviteLink(
            invite_link=link,
            creator=User(id=1, is_bot=True, first_name="Bot"),
            creates_join_request=False,
            is_primary=False,
            is_revoked=False,
            name=kwargs.get("name"),
            member_limit=kwargs.get("member_limit"),
        )

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> object:
        self.sent.append((chat_id, text))
        if self.dm_error is not None:
            # The request rides on the exception, link and all: a log that
            # quoted the method would leak it, which the log test would see.
            method = SendMessage(chat_id=chat_id, text=text)
            raise self.dm_error(method=method, message=self.dm_message)  # type: ignore[call-arg]
        return MagicMock()


class Store:
    """The organizer-name store: what the bot remembered about each person."""

    def __init__(self, rows: dict[int, KnownName] | None = None) -> None:
        self.rows = dict(rows or {})

    async def names(self, chat_id: int, user_ids: Iterable[int]) -> dict[int, KnownName]:
        assert chat_id == CID
        return {uid: self.rows[uid] for uid in user_ids if uid in self.rows}


def _settings(ids: list[int], title: str | None = TITLE) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value={"chat_title": title, "kb_organizer_ids": json.dumps(ids)})
    repo.set_field = AsyncMock()
    return repo


def _bot_config(admins: list[int] | None = None) -> MagicMock:
    repo = MagicMock()

    async def _get(key: str) -> object:
        return (admins if admins is not None else [ADMIN_ID]) if key == "admin_ids" else None

    repo.get = AsyncMock(side_effect=_get)
    return repo


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Screen]]:
    """Every Screen the handlers hand to the REAL ``show``, in order."""
    screens: list[Screen] = []
    real = admin_kb.show

    async def spy(target: Any, screen: Screen, **kwargs: Any) -> Any:
        screens.append(screen)
        return await real(target, screen, **kwargs)

    monkeypatch.setattr(admin_kb, "show", spy)
    yield screens


class Tap:
    """One tap delivered through the real router, and what it left behind."""

    def __init__(
        self,
        data: str,
        bot: Telegram,
        settings: MagicMock,
        *,
        edit: AsyncMock | None = None,
        answer: AsyncMock | None = None,
    ) -> None:
        self.data = data
        self.bot = bot
        self.settings = settings
        self.callback = admin_tap(data)
        self.callback.bot = bot
        self.callback.message.edit_text = edit or AsyncMock()
        self.callback.message.answer = answer or AsyncMock()

    @property
    def text(self) -> str:
        self.callback.message.edit_text.assert_awaited_once()
        return str(self.callback.message.edit_text.await_args.args[0])

    @property
    def markup(self) -> InlineKeyboardMarkup:
        markup = self.callback.message.edit_text.await_args.kwargs["reply_markup"]
        assert isinstance(markup, InlineKeyboardMarkup)
        return markup

    @property
    def buttons(self) -> list[tuple[str, str]]:
        return [
            (b.text, b.callback_data or b.url or "")
            for row in self.markup.inline_keyboard
            for b in row
        ]

    @property
    def labels(self) -> list[str]:
        return [label for label, _ in self.buttons]

    def target(self, label: str) -> str:
        found = [to for text, to in self.buttons if text == label]
        assert len(found) == 1, (label, self.buttons)
        return found[0]


async def _tap(
    data: str,
    bot: Telegram,
    *,
    ids: list[int] | None = None,
    store: Store | None = None,
    settings: MagicMock | None = None,
    admins: list[int] | None = None,
    edit: AsyncMock | None = None,
    answer: AsyncMock | None = None,
) -> Tap:
    settings = settings or _settings(ids if ids is not None else [ORG])
    tap = Tap(data, bot, settings, edit=edit, answer=answer)
    await deliver(
        "callback_query",
        tap.callback,
        FakeState(),
        call=True,
        chat_settings_repo=settings,
        bot_config_repo=_bot_config(admins),
        organizer_names=store or Store(),
    )
    return tap


def _card(uid: int = ORG, lang: str = "ru") -> str:
    return cb("adm_kb_org:", lang, CID, uid)


def _inv(uid: int = ORG) -> str:
    return cb("adm_kb_org_inv:", "ru", CID, uid)


def _dm(uid: int = ORG) -> str:
    return cb("adm_kb_org_dm:", "ru", CID, uid)


# ---------------------------------------------------------------------------
# Routing: each new prefix reaches its own handler, the old ones theirs
# ---------------------------------------------------------------------------

WORST_CID = -1009999999999
WORST_UID = 9999999999

PREFIX_HANDLERS = {
    "adm_kb_org:": ("admin_kb.handle_kb_organizer_card", (WORST_UID,)),
    "adm_kb_org_inv:": ("admin_kb.handle_kb_organizer_invite", (WORST_UID,)),
    "adm_kb_org_dm:": ("admin_kb.handle_kb_organizer_invite_dm", (WORST_UID,)),
    "adm_kb_orgs:": ("admin_kb.handle_kb_organizers", (0,)),
    "adm_kb_org_rm:": ("admin_kb.handle_kb_organizer_remove", (WORST_UID,)),
    "adm_kb_org_undo:": ("admin_kb.handle_kb_organizer_undo", (WORST_UID, 9999)),
    "adm_kb_org_list:": ("admin_kb.handle_kb_organizer_list", (0,)),
    "adm_kb_org_pick:": ("admin_kb.handle_kb_organizer_pick", (WORST_UID,)),
    "adm_kb_org_add:": ("admin_kb.handle_kb_organizer_add_prompt", ()),
}


@pytest.mark.parametrize("prefix", list(PREFIX_HANDLERS))
def test_every_organizer_prefix_reaches_its_own_handler(prefix: str) -> None:
    """No cross-capture: ``adm_kb_org:`` is a prefix of none of its neighbours'
    payloads and none of theirs is a prefix of it, in both directions."""
    handler, rest = PREFIX_HANDLERS[prefix]
    for lang in ("ru", "en"):
        assert first_match(cb(prefix, lang, WORST_CID, *rest)) == handler


@pytest.mark.parametrize("prefix", ["adm_kb_org:", "adm_kb_org_inv:", "adm_kb_org_dm:"])
def test_the_worst_case_payload_fits_telegrams_64_bytes(prefix: str) -> None:
    data = cb(prefix, "ru", WORST_CID, WORST_UID)  # cb() itself raises past 64
    assert len(data.encode()) <= 64


# ---------------------------------------------------------------------------
# The list: a name opens the card and writes nothing
# ---------------------------------------------------------------------------


async def test_a_tap_on_a_name_opens_the_card_and_removes_nobody(drawn: list[Screen]) -> None:
    """The bug this card exists for: the tap removed the person at once.

    The callback is taken off the list the handler really draws, so pointing
    the list's name button anywhere else is caught here, not in a sample.
    """
    ids = [ORG, OTHER]
    listed = await _tap(cb("adm_kb_orgs:", "ru", CID, 0), Telegram(), ids=ids)
    name = listed.labels[0]
    assert name == f"👤 @{NICK}"

    settings = _settings(ids)
    card = await _tap(listed.target(name), Telegram(), settings=settings)

    settings.set_field.assert_not_awaited()
    assert json.loads(settings.get.return_value["kb_organizer_ids"]) == ids
    assert drawn[-1].id == "org.card"
    assert card.text.startswith(f"👤 <b>@{NICK}</b> · {TITLE}")


async def test_the_list_says_what_organizers_do_and_keeps_the_absent_legend() -> None:
    listed = await _tap(cb("adm_kb_orgs:", "ru", CID, 0), Telegram("left"), ids=[ORG])

    assert orgs.ORGS_BODY("ru") in listed.text
    assert orgs.ORGS_ABSENT("ru") in listed.text
    assert "Нажатие" not in listed.text and "сразу убирает" not in listed.text
    assert listed.labels == [f"👤 @{NICK} · не в чате", "➕ Добавить", "⬅️ Чат"]


# ---------------------------------------------------------------------------
# The card: one per membership state
# ---------------------------------------------------------------------------

WRITE = "💬 Написать лично"
INVITE = "✉️ Пригласить в чат"
REMOVE = "✖️ Убрать из организаторов"
BACK_TO_LIST = "⬅️ Организаторы"

IN = "Сейчас в чате."
OUT = "Сейчас не в чате."
NEVER = "В чат не вступал."
BANNED = "Удалён из чата и заблокирован в нём."
SILENT = "Telegram не ответил, в чате ли человек сейчас."
CAN = "Может сохранять факты командой /remember в этом чате."
WILL = "Сохранять факты командой /remember сможет, когда будет в чате."

BY_ID = f"tg://openmessage?user_id={ORG}"
"""The person link with no username Telegram gave now. Measured 2026-11-07: a
URL button with it is accepted for a person the bot has seen and for one it
has never seen, and on Julia's phone a tap opened the private chat in both."""

CARD_STATES = [
    # status, username (Telegram's, or for never/unknown the store's), labels,
    # state line, role line. The person link is first in every state.
    ("member", NICK, [WRITE, REMOVE, BACK_TO_LIST], IN, CAN),
    ("member", None, [WRITE, REMOVE, BACK_TO_LIST], IN, CAN),
    # Restricted and still a member: in the chat, a link would change nothing.
    ("restricted", NICK, [WRITE, REMOVE, BACK_TO_LIST], IN, CAN),
    # Restricted and NOT a member: out, and a link lets them back in.
    ("restricted_out", NICK, [WRITE, INVITE, REMOVE, BACK_TO_LIST], OUT, WILL),
    ("administrator", NICK, [WRITE, REMOVE, BACK_TO_LIST], IN, CAN),
    ("creator", NICK, [WRITE, REMOVE, BACK_TO_LIST], IN, CAN),
    ("left", NICK, [WRITE, INVITE, REMOVE, BACK_TO_LIST], OUT, WILL),
    ("never", None, [WRITE, INVITE, REMOVE, BACK_TO_LIST], NEVER, WILL),
    # A stored username is never a link: it may be someone else's by now.
    ("never", NICK, [WRITE, INVITE, REMOVE, BACK_TO_LIST], NEVER, WILL),
    ("kicked", NICK, [WRITE, REMOVE, BACK_TO_LIST], BANNED, WILL),
    ("unknown", None, [WRITE, REMOVE, BACK_TO_LIST], SILENT, CAN),
    ("unknown", NICK, [WRITE, REMOVE, BACK_TO_LIST], SILENT, CAN),
]


@pytest.mark.parametrize(("status", "username", "labels", "line", "role"), CARD_STATES)
async def test_the_card_per_membership_state(
    status: str, username: str | None, labels: list[str], line: str, role: str
) -> None:
    # Telegram names nobody it cannot see: those come from the store.
    store = Store({ORG: KnownName(FIRST, LAST, username)})
    card = await _tap(_card(), Telegram(status, username=username), store=store)

    assert card.labels == labels
    assert line in card.text
    assert role in card.text and ({CAN, WILL} - {role}).pop() not in card.text
    assert (orgs.BANNED_HINT("ru") in card.text) is (status == "kicked")
    assert card.target(REMOVE) == cb("adm_kb_org_rm:", "ru", CID, ORG)
    live = username is not None and status not in ("never", "unknown")
    if live:
        assert card.target(WRITE) == f"https://t.me/{username}"
        assert f"\n{fake.PERSON} · @{username}\n" in card.text
    else:
        assert card.target(WRITE) == BY_ID, "a stored username never becomes a t.me link"
        assert " · @" not in card.text, "no @nick beside the name without a live one"
        assert not any("t.me/" in to for _, to in card.buttons), card.buttons
    if INVITE in labels:
        assert card.target(INVITE) == _inv()


async def test_removed_and_unknown_have_no_invite_button() -> None:
    for status in ("kicked", "unknown"):
        card = await _tap(_card(), Telegram(status))
        assert INVITE not in card.labels, status


async def test_the_card_names_the_person_and_says_what_they_can_do() -> None:
    card = await _tap(_card(), Telegram("member"))

    first, *body = card.text.split("\n")
    assert first == f"👤 <b>@{NICK}</b> · {TITLE}"
    assert body[1] == f"{fake.PERSON} · @{NICK}"
    assert f"ID: <code>{ORG}</code>" in body
    assert orgs.CARD_ROLE("ru") in body
    # The body's name is plain text, not a link that works for some people only;
    # the one way to the person is the labelled button, first on the card.
    assert "<a " not in card.text and "tg://" not in card.text
    assert card.labels[0] == WRITE and card.target(WRITE) == f"https://t.me/{NICK}"
    assert not any(to.startswith("tg://user") for _, to in card.buttons)


async def test_with_no_name_ever_known_the_id_is_in_code_and_said_once(
    drawn: list[Screen],
) -> None:
    """«участник 555000111» bare in a title autolinks as a phone number that
    leads nowhere; the id goes in ``<code>`` and the ID line, now a repeat, goes.

    The complaint of 2026-11-07: this card showed «участник <id>» twice as plain
    text and offered no way to reach the person. Now the body does not repeat
    the title, and the person link (by id: there is no username) is first."""
    card = await _tap(_card(), Telegram("never"))  # Telegram has no name, the store none

    first, *body = card.text.split("\n")
    assert first == f"👤 <b>участник</b> <code>{ORG}</code> · {TITLE}"
    assert not any(line.startswith("ID:") for line in body), body
    assert card.text.count(f"<code>{ORG}</code>") == 1, "the title's, not a second one"
    assert body == ["", NEVER, WILL]
    assert card.labels == [WRITE, INVITE, REMOVE, BACK_TO_LIST]
    assert card.target(WRITE) == BY_ID
    assert drawn[-1].id == "org.card"

    invite = await _tap(_inv(), Telegram("never"))
    assert invite.text.startswith(f"✉️ <b>Приглашение</b> · участник <code>{ORG}</code>\n")


async def test_back_returns_to_the_page_the_person_is_on() -> None:
    ids = [100 + i for i in range(orgs.ORGS_PER_PAGE)] + [ORG]
    card = await _tap(_card(), Telegram(), ids=ids)

    assert card.labels[-1] == BACK_TO_LIST
    assert card.target(BACK_TO_LIST) == cb("adm_kb_orgs:", "ru", CID, 1)


# ---------------------------------------------------------------------------
# getChat: the second live source of a name
# ---------------------------------------------------------------------------

FRESH = "fresh_nick"
"""An invented username Telegram gives now, unlike the store's ``NICK``."""
OTHER_FIRST, OTHER_LAST = fake.PERSON_OTHER.split(" ")
GET_CHAT_FAILED = "Organizer name: getChat failed"


async def test_get_chat_is_asked_only_when_get_chat_member_named_nobody() -> None:
    """``getChatMember`` names everyone it answers for. ``getChat`` is asked only
    when Telegram answered with an error and no user; a timeout is not asked
    twice, since there was no answer and a second call would only wait again."""
    refused = Telegram("never", private=(OTHER_FIRST, OTHER_LAST, FRESH))
    await _tap(_card(), refused)
    assert refused.chats == [ORG], "control: the double is asked when nobody was named"

    for status in ("member", "left", "kicked", "unknown"):
        bot = Telegram(status, private=(OTHER_FIRST, OTHER_LAST, FRESH))
        await _tap(_card(), bot)
        assert bot.chats == [], status


async def test_get_chat_names_the_person_and_its_username_makes_the_link() -> None:
    """Someone who opened the bot but never joined the group: ``getChat`` is
    Telegram's answer now, so it names them over the store, and its username is
    as live as one from ``getChatMember`` — the ``t.me`` link and ``· @nick``."""
    store = Store({ORG: KnownName(FIRST, LAST, NICK)})
    bot = Telegram("never", private=(OTHER_FIRST, OTHER_LAST, FRESH))
    card = await _tap(_card(), bot, store=store)

    first, *body = card.text.split("\n")
    assert first == f"👤 <b>@{FRESH}</b> · {TITLE}"
    assert body[1] == f"{fake.PERSON_OTHER} · @{FRESH}"
    assert card.target(WRITE) == f"https://t.me/{FRESH}"
    assert NEVER in card.text and INVITE in card.labels, "still not in the group"
    assert NICK not in card.text

    # No username in the private chat: the name still comes from it, the link by id.
    bare = await _tap(_card(), Telegram("never", private=(OTHER_FIRST, None, None)), store=store)
    assert bare.text.startswith(f"👤 <b>{OTHER_FIRST}</b> · {TITLE}\n")
    assert bare.target(WRITE) == BY_ID


async def test_a_failed_get_chat_falls_through_to_the_stored_name() -> None:
    """«chat not found» is what ``getChat`` says about anyone who never opened the
    bot (measured 2026-11-07), so it is not logged as a failure; any other error
    is. Either way the stored name stands, and its username is never linked."""
    store = Store({ORG: KnownName(FIRST, LAST, NICK)})
    for error, warned in ((CHAT_NOT_FOUND, 0), ("Bad Request: something else", 1)):
        bot = Telegram("never", chat_error=error)
        with capture_logs() as logs:
            card = await _tap(_card(), bot, store=store)

        assert bot.chats == [ORG], error
        assert card.text.startswith(f"👤 <b>@{NICK}</b> · {TITLE}\n"), error
        assert card.target(WRITE) == BY_ID, "the stored username is never linked"
        found = [e for e in logs if e["event"] == GET_CHAT_FAILED]
        assert len(found) == warned, (error, logs)
        if found:
            assert found[0]["log_level"] == "warning" and found[0]["error"] == error


async def test_with_nothing_from_get_chat_or_the_store_the_id_stands() -> None:
    card = await _tap(_card(), Telegram("never"))  # getChat: «chat not found», no store row

    assert card.text.startswith(f"👤 <b>участник</b> <code>{ORG}</code> · {TITLE}\n")
    assert card.bot.chats == [ORG]


# ---------------------------------------------------------------------------
# The guard: a refused person link costs that button, not the card
# ---------------------------------------------------------------------------

REFUSED = "Organizer card: link button refused"
NOT_MODIFIED = (
    "Bad Request: message is not modified: specified new message content and reply "
    "markup are exactly the same as a current content and reply markup of the message"
)


def _refusing(error: str) -> AsyncMock:
    """``edit_text`` / ``answer`` as Telegram answers a markup carrying the person
    link it refuses: ``error`` while ``💬 Написать лично`` is on it, accepted after."""

    async def call(text: str, **kwargs: Any) -> MagicMock:
        markup = kwargs.get("reply_markup")
        rows = markup.inline_keyboard if isinstance(markup, InlineKeyboardMarkup) else []
        if any(b.text == WRITE for row in rows for b in row):
            raise TelegramBadRequest(method=SendMessage(chat_id=CID, text=text), message=error)
        return MagicMock()

    return AsyncMock(side_effect=call)


def _sent_labels(call: Any) -> list[str]:
    markup = call.kwargs["reply_markup"]
    return [b.text for row in markup.inline_keyboard for b in row]


@pytest.mark.parametrize(
    "error",
    [
        "Bad Request: BUTTON_USER_INVALID",
        "Bad Request: BUTTON_USER_PRIVACY_RESTRICTED",
        "Bad Request: BUTTON_URL_INVALID",
    ],
)
async def test_a_refused_person_link_is_dropped_and_the_card_still_arrives(
    error: str, drawn: list[Screen]
) -> None:
    edit, answer = _refusing(error), _refusing(error)
    with capture_logs() as logs:
        card = await _tap(_card(), Telegram("never"), edit=edit, answer=answer)

    # The card with the link was offered and refused, as an edit and as a new message.
    assert WRITE in _sent_labels(edit.await_args_list[0])
    assert answer.await_count == 1 and WRITE in _sent_labels(answer.await_args_list[0])
    # Then the same card once more, in place, without that one button.
    assert edit.await_count == 2
    assert _sent_labels(edit.await_args_list[1]) == [INVITE, REMOVE, BACK_TO_LIST]
    assert edit.await_args_list[1].args[0] == edit.await_args_list[0].args[0]
    assert [screen.id for screen in drawn] == ["org.card", "org.card"]
    assert drawn[1] == orgs.without_person_link(drawn[0])
    card.callback.answer.assert_awaited_once()  # the query is answered once, not per try
    warned = [e for e in logs if e["event"] == REFUSED]
    assert len(warned) == 1 and warned[0]["log_level"] == "warning"
    assert warned[0]["error"] == error
    assert (warned[0]["chat_id"], warned[0]["user_id"]) == (CID, ORG)


async def test_an_unchanged_card_is_not_a_refusal(drawn: list[Screen]) -> None:
    """«message is not modified» (the same card tapped twice) means the card is
    already there: nothing is sent again and nothing is logged. The control is a
    refusal through the same doubles, in this test."""
    refusing = _refusing("Bad Request: BUTTON_USER_INVALID")
    with capture_logs() as logs:
        await _tap(_card(), Telegram("never"), edit=refusing, answer=_refusing("BUTTON_X"))
    assert [e["event"] for e in logs].count(REFUSED) == 1, "control: a refusal is seen"
    assert refusing.await_count == 2 and len(drawn) == 2

    drawn.clear()
    unchanged, answer = _refusing(NOT_MODIFIED), AsyncMock()
    with capture_logs() as logs:
        card = await _tap(_card(), Telegram("never"), edit=unchanged, answer=answer)

    assert unchanged.await_count == 1 and WRITE in _sent_labels(unchanged.await_args_list[0])
    answer.assert_not_awaited()
    assert len(drawn) == 1
    card.callback.answer.assert_awaited_once()
    assert REFUSED not in [e["event"] for e in logs]


async def test_another_error_is_not_answered_by_dropping_the_link(drawn: list[Screen]) -> None:
    """An error that names no button and no URL is not the link's fault: the card
    without it would fail the same way, so it propagates after one try, as every
    screen's does. Telegram's codes are upper case: a lower-case "url" in an
    entity error is not one. The control is a refusal in this test."""
    with capture_logs() as logs:
        await _tap(
            _card(),
            Telegram("never"),
            edit=_refusing("Bad Request: BUTTON_URL_INVALID"),
            answer=_refusing("Bad Request: BUTTON_URL_INVALID"),
        )
    assert [e["event"] for e in logs].count(REFUSED) == 1, "control: a refusal is seen"

    drawn.clear()
    entity_error = (
        'Bad Request: can\'t parse entities: unsupported start tag "url" at byte offset 0'
    )
    edit = _refusing("Bad Request: message to edit not found")
    answer = _refusing(entity_error)
    with capture_logs() as logs, pytest.raises(TelegramBadRequest, match="parse entities"):
        await _tap(_card(), Telegram("never"), edit=edit, answer=answer)

    assert edit.await_count == 1 and answer.await_count == 1
    assert len(drawn) == 1
    assert REFUSED not in [e["event"] for e in logs]


async def test_card_then_remove_gives_the_list_with_undo() -> None:
    ids = [OTHER, ORG]
    card = await _tap(_card(), Telegram(), ids=ids)

    settings = _settings(ids)
    removed = await _tap(card.target(REMOVE), Telegram(), settings=settings)

    settings.set_field.assert_awaited_once()
    assert json.loads(settings.set_field.await_args.args[2]) == [OTHER]
    assert "↩️ Вернуть" in removed.labels
    assert removed.target("↩️ Вернуть") == cb("adm_kb_org_undo:", "ru", CID, ORG, 1)


async def test_a_remove_button_from_before_the_card_still_removes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chats are full of the old list's buttons: ``adm_kb_org_rm:…:p``, the
    origin token trailing. It must still remove (that is what the button said),
    not land on the stale screen."""
    stale = AsyncMock()
    monkeypatch.setattr(admin_kb, "show_stale", stale)
    ids = [OTHER, ORG]
    settings = _settings(ids)
    removed = await _tap(cb("adm_kb_org_rm:", "ru", CID, ORG) + ":p", Telegram(), settings=settings)

    stale.assert_not_awaited()
    settings.set_field.assert_awaited_once()
    assert json.loads(settings.set_field.await_args.args[2]) == [OTHER]
    assert removed.target("↩️ Вернуть") == cb("adm_kb_org_undo:", "ru", CID, ORG, 1)


# ---------------------------------------------------------------------------
# Someone no longer on the list, a malformed payload, a non-admin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("data", [_card(), _inv(), _dm()])
async def test_someone_no_longer_an_organizer_gets_the_list_with_a_line(
    data: str, drawn: list[Screen]
) -> None:
    bot = Telegram("left")
    settings = _settings([OTHER])
    tap = await _tap(data, bot, settings=settings)

    assert drawn[-1].id == "orgs"
    assert orgs.NOT_ORGANIZER("ru") in tap.text
    assert bot.links == [] and bot.sent == []
    settings.set_field.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [
        f"adm_kb_org:ru:{CID}:x",
        "adm_kb_org:ru",
        f"adm_kb_org_inv:ru:{CID}:",
        "adm_kb_org_inv:en:abc:1",
        f"adm_kb_org_dm:ru:{CID}",
        "adm_kb_org_dm:en::5",
    ],
)
async def test_a_malformed_payload_shows_the_stale_screen(
    data: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = AsyncMock()
    monkeypatch.setattr(admin_kb, "show_stale", stale)
    bot = Telegram("left")
    tap = await _tap(data, bot)

    stale.assert_awaited_once()
    assert stale.await_args.args[0] is tap.callback
    assert stale.await_args.args[1] == ("en" if ":en:" in data else "ru")
    assert bot.links == [] and bot.sent == []
    tap.callback.message.edit_text.assert_not_awaited()


@pytest.mark.parametrize("data", [_card(), _inv(), _dm()])
async def test_a_tap_from_someone_not_an_admin_does_nothing(data: str) -> None:
    bot = Telegram("left")
    tap = await _tap(data, bot, admins=[ADMIN_ID + 1])

    tap.callback.answer.assert_awaited_once()
    assert tap.callback.answer.await_args.kwargs.get("show_alert") is True
    tap.callback.message.edit_text.assert_not_awaited()
    assert bot.links == [] and bot.sent == []


# ---------------------------------------------------------------------------
# The invite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["left", "restricted_out", "never"])
async def test_invite_makes_one_link_for_one_person_for_seven_days(
    status: str, drawn: list[Screen]
) -> None:
    bot = Telegram(status)
    store = Store({ORG: KnownName(FIRST, LAST, NICK)})
    before = datetime.now(UTC)
    tap = await _tap(_inv(), bot, store=store)

    assert len(bot.links) == 1
    made = bot.links[0]
    assert made["chat_id"] == CID
    assert made["member_limit"] == 1
    assert abs(made["expire_date"] - (before + timedelta(days=7))) < timedelta(minutes=1)
    assert made["name"] == f"Организатор · @{NICK}"
    assert len(made["name"].encode("utf-16-le")) // 2 <= 32
    assert bot.sent == [], "showing the link sends nothing"

    link = bot.made[0]
    assert drawn[-1].id == "org.invite"
    assert tap.text.startswith(f"✉️ <b>Приглашение</b> · @{NICK}")
    assert f"<code>{link}</code>" in tap.text
    assert "для одного человека, действует 7 дней" in tap.text
    assert tap.labels == ["📤 Переслать", "🤖 Отправить от бота", "⬅️ Организатор"]
    share = tap.target("📤 Переслать")
    assert share.startswith(f"https://t.me/share/url?url={quote(link, safe='')}&text=")
    assert parse_qs(urlsplit(share).query) == {
        "url": [link],
        "text": [f"Приглашение в «{TITLE}»"],
    }
    assert tap.target("🤖 Отправить от бота") == _dm()
    assert tap.target("⬅️ Организатор") == _card()
    tap.settings.set_field.assert_not_awaited()


async def test_a_long_name_is_clipped_to_telegrams_32_for_the_link_name() -> None:
    bot = Telegram("left", username=None, first="Очень Длинное Имя Участника", last="Чата")
    await _tap(_inv(), bot)

    name = bot.links[0]["name"]
    assert len(name.encode("utf-16-le")) // 2 <= 32
    assert name.startswith("Организатор · Очень") and name.endswith("…")


@pytest.mark.parametrize("error", RIGHTS_ERRORS)
async def test_without_the_invite_right_the_card_says_so_and_nothing_changes(
    error: str, drawn: list[Screen]
) -> None:
    bot = Telegram("left", link_error=error)
    settings = _settings([ORG])
    tap = await _tap(_inv(), bot, settings=settings)

    assert drawn[-1].id == "org.card"
    assert drawn[-1].notice_raw == orgs.NO_INVITE_RIGHT("ru")
    assert orgs.NO_INVITE_RIGHT("ru") in tap.text
    assert INVITE in tap.labels, "the repair is to grant the right and tap again"
    assert bot.sent == []
    settings.set_field.assert_not_awaited()


async def test_another_link_failure_is_logged_and_said_honestly(drawn: list[Screen]) -> None:
    bot = Telegram("left", link_error="Bad Request: CHAT_NOT_MODIFIED")
    with capture_logs() as logs:
        tap = await _tap(_inv(), bot)

    assert drawn[-1].notice_raw == orgs.LINK_FAILED("ru")
    assert orgs.LINK_FAILED("ru") in tap.text
    failed = [e for e in logs if e["event"] == "Organizer invite: link failed"]
    assert failed and failed[0]["chat_id"] == CID and failed[0]["user_id"] == ORG


async def test_an_untitled_chat_gets_the_untitled_wording(drawn: list[Screen]) -> None:
    """No stored title: neither the share text nor the DM may say «Приглашение
    в «None»» or quote an empty name."""
    bot = Telegram("left")
    invite = await _tap(_inv(), bot, settings=_settings([ORG], title=None))
    share = parse_qs(urlsplit(invite.target("📤 Переслать")).query)
    assert share == {"url": [bot.made[0]], "text": ["Приглашение в чат"]}

    bot = Telegram("left")
    await _tap(_dm(), bot, settings=_settings([ORG], title=None))
    [(to, text)] = bot.sent
    assert to == ORG
    assert text == (
        f"Вас приглашают в чат. Ссылка для одного человека, действует 7 дней:\n{bot.made[0]}"
    )
    assert drawn[-1].notice == orgs.SENT("ru")


@pytest.mark.parametrize(
    ("status", "line"),
    [
        ("member", orgs.ALREADY_IN_CHAT),
        # An organizer who is an admin, the owner, or restricted but still a
        # member is in the chat: no link, whatever the old button said.
        ("restricted", orgs.ALREADY_IN_CHAT),
        ("administrator", orgs.ALREADY_IN_CHAT),
        ("creator", orgs.ALREADY_IN_CHAT),
        ("kicked", orgs.NO_LINK),
        ("unknown", orgs.NO_LINK),
    ],
)
async def test_an_old_invite_button_rechecks_and_makes_no_link(
    status: str, line: Any, drawn: list[Screen]
) -> None:
    """The card may be minutes old: the person joined, was banned, or Telegram
    cannot say. Each re-check lands on the card with a line, and no link."""
    for data in (_inv(), _dm()):
        bot = Telegram(status)
        tap = await _tap(data, bot)

        assert bot.links == [] and bot.sent == [], data
        assert drawn[-1].id == "org.card"
        assert line("ru") in tap.text


# ---------------------------------------------------------------------------
# Sending from the bot
# ---------------------------------------------------------------------------


async def test_send_from_the_bot_makes_a_fresh_link_and_sends_it(drawn: list[Screen]) -> None:
    bot = Telegram("left")
    tap = await _tap(_dm(), bot)

    assert len(bot.made) == 1 and bot.links[0]["member_limit"] == 1
    link = bot.made[0]
    assert len(bot.sent) == 1
    to, text = bot.sent[0]
    assert to == ORG
    assert link in text and f"«{TITLE}»" in text
    assert drawn[-1].id == "org.invite"
    assert drawn[-1].notice == orgs.SENT("ru")
    assert tap.text.startswith("<i>✓ Ссылка отправлена в личные сообщения</i>")
    assert f"<code>{link}</code>" in tap.text


async def test_a_title_with_markup_is_escaped_in_the_message() -> None:
    bot = Telegram("left")
    await _tap(_dm(), bot, settings=_settings([ORG], title="<b>A</b> & B"))

    assert "«&lt;b&gt;A&lt;/b&gt; &amp; B»" in bot.sent[0][1]


CANNOT_WRITE_FIRST = [
    # A 403: never opened a chat with the bot, or blocked it.
    (TelegramForbiddenError, "Forbidden: bot can't initiate conversation with a user"),
    (TelegramForbiddenError, "Forbidden: bot was blocked by the user"),
    # A 400 for someone the bot never met at all.
    (TelegramBadRequest, "Bad Request: chat not found"),
    (TelegramBadRequest, "Bad Request: PEER_ID_INVALID"),
]


@pytest.mark.parametrize(("error", "message"), CANNOT_WRITE_FIRST)
async def test_a_refusal_to_write_first_says_so_and_points_to_share(
    error: type[Exception], message: str, drawn: list[Screen]
) -> None:
    bot = Telegram("left", dm_error=error, dm_message=message)
    with capture_logs() as logs:
        tap = await _tap(_dm(), bot)

    assert len(bot.sent) == 1, "the send was attempted"
    assert drawn[-1].id == "org.invite"
    assert drawn[-1].notice_raw == orgs.DM_FORBIDDEN("ru")
    assert tap.text.startswith(
        "<i>Бот не может написать первым: у этого человека нет чата с ботом или бот у него "
        "заблокирован. Ссылка ниже действует, её можно отправить через «📤 Переслать».</i>"
    )
    assert f"<code>{bot.made[0]}</code>" in tap.text, "the link is still there to forward"
    assert "📤 Переслать" in tap.labels
    events = [e["event"] for e in logs]
    assert "Organizer invite: bot cannot write first" in events, events
    assert "Organizer invite: message failed" not in events, events


async def test_another_send_failure_is_logged_and_said_honestly(drawn: list[Screen]) -> None:
    bot = Telegram(
        "left", dm_error=TelegramBadRequest, dm_message="Bad Request: message is too long"
    )
    with capture_logs() as logs:
        tap = await _tap(_dm(), bot)

    assert drawn[-1].notice_raw == orgs.DM_FAILED("ru")
    assert orgs.DM_FAILED("ru") in tap.text
    failed = [e for e in logs if e["event"] == "Organizer invite: message failed"]
    assert failed and failed[0]["error"] == "Bad Request: message is too long"


# ---------------------------------------------------------------------------
# The link is a credential: it never reaches a log
# ---------------------------------------------------------------------------


async def test_the_link_never_reaches_a_log() -> None:
    """Every path that holds a link, logged at once; no entry carries it.

    The control is the other half: the events are there with the chat and the
    person (so the capture saw this logger), and one failure's own text DOES
    contain the link (so a scrub was needed, not merely absent).
    """
    leaky = f"Bad Request: cannot deliver {LINKS[3]}"
    runs = [
        (_inv(), Telegram("left")),
        (_dm(), Telegram("left")),
        (_dm(), Telegram("left", dm_error=TelegramForbiddenError)),
        (_dm(), Telegram("left", dm_error=TelegramBadRequest, dm_message=leaky)),
    ]
    with capture_logs() as logs:
        for data, bot in runs:
            bot.made = []  # each run's own first link
            await _tap(data, bot)

    made = [link for _, bot in runs for link in bot.made]
    assert set(made) == {LINKS[0]}, made
    assert LINKS[3] in leaky
    events = [e["event"] for e in logs]
    assert events.count("Organizer invite link made") == 4
    for event in (
        "Organizer invite sent",
        "Organizer invite: bot cannot write first",
        "Organizer invite: message failed",
    ):
        assert event in events, events
    for entry in logs:
        if entry["event"].startswith("Organizer invite"):
            assert entry["chat_id"] == CID and entry["user_id"] == ORG, entry
        assert "exc_info" not in entry, entry
        for link in [*made, LINKS[3]]:
            assert link not in repr(entry), entry


def test_the_error_scrub_removes_the_link() -> None:
    exc = TelegramBadRequest(
        method=SendMessage(chat_id=ORG, text="x"), message=f"Bad Request: no {LINKS[0]}"
    )
    assert admin_kb._error_text(exc, LINKS[0]) == "Bad Request: no <link>"
    # A link the caller did not hand in is caught by its shape.
    assert admin_kb._error_text(exc) == "Bad Request: no <link>"
    other = TelegramBadRequest(
        method=SendMessage(chat_id=ORG, text="x"),
        message="Bad Request: t.me/joinchat/AbC-d_9 and https://t.me/test_user stay?",
    )
    assert admin_kb._error_text(other) == (
        "Bad Request: <link> and https://t.me/test_user stay?"
    ), "a profile link is not a credential and is left alone"
    assert admin_kb._error_text(ValueError(LINKS[0])) == "ValueError"


# ---------------------------------------------------------------------------
# The builders: labels as lists, back destinations
# ---------------------------------------------------------------------------


class TestCardBuilder:
    def test_labels_in_each_state(self) -> None:
        """The person link first in every state, with or without a username:
        at most four action rows and the way back."""
        expected = {
            "in_chat": [WRITE, REMOVE, BACK_TO_LIST],
            "left": [WRITE, INVITE, REMOVE, BACK_TO_LIST],
            "never_joined": [WRITE, INVITE, REMOVE, BACK_TO_LIST],
            "removed": [WRITE, REMOVE, BACK_TO_LIST],
            "unknown": [WRITE, REMOVE, BACK_TO_LIST],
        }
        for state, labels in expected.items():
            for username in (NICK, None):
                screen = orgs.org_card_screen(
                    "ru",
                    CID,
                    TITLE,
                    ORG,
                    "Аня",
                    state=state,  # type: ignore[arg-type]
                    username=username,
                )
                assert [b.label for row in screen.rows for b in row] == labels, state
                assert all(len(row) == 1 for row in screen.rows)
                assert len(screen.rows) <= 5

    def test_the_person_link_goes_by_username_or_by_id(self) -> None:
        """``t.me`` for a username Telegram gave now, the id otherwise. The label
        promises the private chat, where both open: ``t.me/{username}`` for a
        person, and ``tg://openmessage`` as measured on Julia's phone 2026-11-07."""
        cases = [
            (NICK, f"https://t.me/{NICK}"),
            (f"@{NICK}", f"https://t.me/{NICK}"),
            (None, BY_ID),
            ("", BY_ID),
        ]
        for lang, label in (("ru", WRITE), ("en", "💬 Message privately")):
            for username, url in cases:
                screen = orgs.org_card_screen(
                    lang, CID, TITLE, ORG, "Ann", state="left", username=username
                )
                (link,) = screen.rows[0]
                assert (link.label, link.url, link.callback) == (label, url, None), username
                assert orgs.person_url(ORG, username) == url

    def test_an_invalid_username_links_by_id(self) -> None:
        for bad in ("abc", "has space", "кириллица", "x" * 33):
            screen = orgs.org_card_screen(
                "ru", CID, TITLE, ORG, "Аня", state="in_chat", username=bad
            )
            assert screen.rows[0][0].label == WRITE
            assert screen.rows[0][0].url == BY_ID, bad
            assert " · @" not in screen.text, bad

    def test_a_four_character_username_gets_the_t_me_link(self) -> None:
        """Collectible usernames can be four characters long."""
        for nick in ("abcd", "@abcd", "x" * 32):
            screen = orgs.org_card_screen(
                "ru", CID, TITLE, ORG, "Аня", state="in_chat", username=nick
            )
            bare = nick.lstrip("@")
            assert screen.rows[0][0].label == WRITE, nick
            assert screen.rows[0][0].url == f"https://t.me/{bare}"
            assert f"\nАня · @{bare}\n" in screen.text

    def test_the_body_name_is_plain_text(self) -> None:
        """No link in the body: a ``tg://user`` mention is dropped for a person the
        bot cannot see, so it was a link for some and plain text for others."""
        for username in (NICK, None):
            screen = orgs.org_card_screen(
                "ru",
                CID,
                TITLE,
                ORG,
                f"@{NICK}",
                state="never_joined",
                full=fake.PERSON,
                username=username,
            )
            name_line = screen.text.split("\n")[2]
            assert name_line == (f"{fake.PERSON} · @{NICK}" if username else fake.PERSON)
            assert "<a " not in screen.text and "tg://" not in screen.text

    def test_without_the_person_link_only_that_row_goes(self) -> None:
        for lang in ("ru", "en"):
            screen = orgs.org_card_screen(
                lang, CID, TITLE, ORG, "Ann", state="left", notice_raw="<i>x</i>"
            )
            bare = orgs.without_person_link(screen)
            assert bare is not None
            assert bare.rows == screen.rows[1:]
            assert (bare.id, bare.text, bare.notice_raw) == (screen.id, screen.text, "<i>x</i>")
            assert orgs.without_person_link(bare) is None, "nothing left to drop"

    @pytest.mark.parametrize(
        ("state", "role"),
        [
            ("in_chat", CAN),
            ("unknown", CAN),
            ("left", WILL),
            ("never_joined", WILL),
            ("removed", WILL),
        ],
    )
    def test_the_role_line_follows_the_state(self, state: str, role: str) -> None:
        screen = orgs.org_card_screen("ru", CID, TITLE, ORG, "Аня", state=state)  # type: ignore[arg-type]
        lines = screen.text.split("\n")
        assert lines[-1] == role
        assert ({CAN, WILL} - {role}).pop() not in screen.text
        english = orgs.org_card_screen("en", CID, TITLE, ORG, "Ann", state=state)  # type: ignore[arg-type]
        assert english.text.split("\n")[-1] == (
            "Can save facts with /remember in this chat."
            if role == CAN
            else "Can save facts with /remember once in the chat."
        )

    def test_the_id_fallback_title_in_both_languages(self) -> None:
        for lang, word in (("ru", "участник"), ("en", "member")):
            name = orgs.MEMBER(lang).format(id=ORG)
            card = orgs.org_card_screen(lang, CID, TITLE, ORG, name, state="never_joined")
            assert card.text.startswith(f"👤 <b>{word}</b> <code>{ORG}</code> · {TITLE}\n")
            assert "ID:" not in card.text
            invite = orgs.org_invite_screen(lang, CID, TITLE, ORG, name, LINKS[0])
            assert f"</b> · {word} <code>{ORG}</code>\n" in invite.text
        # A real name that merely contains digits is a name, not the fallback.
        named = orgs.org_card_screen("ru", CID, TITLE, ORG, "Аня 2000", state="in_chat")
        assert named.text.startswith(f"👤 <b>Аня 2000</b> · {TITLE}\n")
        assert f"ID: <code>{ORG}</code>" in named.text

    def test_back_goes_to_the_list_page(self) -> None:
        screen = orgs.org_card_screen("en", CID, TITLE, ORG, "Ann", state="in_chat", page=2)
        back = screen.rows[-1][-1]
        assert back.goes_to == "orgs"
        assert back.label == f"⬅️ {BACK_NAMES['orgs']('en')}"
        assert back.callback == cb("adm_kb_orgs:", "en", CID, 2)

    def test_a_name_with_markup_is_escaped(self) -> None:
        screen = orgs.org_card_screen(
            "ru", CID, TITLE, ORG, "<b>x</b>", state="in_chat", full="<i>y</i>"
        )
        assert screen.text.startswith("👤 <b>&lt;b&gt;x&lt;/b&gt;</b>")
        assert "\n&lt;i&gt;y&lt;/i&gt;\n" in screen.text


class TestInviteBuilder:
    def test_labels_and_back_to_the_card(self) -> None:
        screen = orgs.org_invite_screen("ru", CID, TITLE, ORG, "Аня", LINKS[0])
        assert [b.label for row in screen.rows for b in row] == [
            "📤 Переслать",
            "🤖 Отправить от бота",
            "⬅️ Организатор",
        ]
        back = screen.rows[-1][-1]
        assert back.goes_to == "org.card"
        assert back.callback == _card()
        assert screen.rows[0][0].url is not None and screen.rows[0][0].callback is None

    def test_english(self) -> None:
        screen = orgs.org_invite_screen("en", CID, TITLE, ORG, "Ann", LINKS[0])
        assert screen.text.startswith("✉️ <b>Invitation</b> · Ann")
        assert [b.label for row in screen.rows for b in row] == [
            "📤 Share",
            "🤖 Send from the bot",
            "⬅️ Organizer",
        ]


def test_the_link_name_fits_32_utf16_units() -> None:
    assert orgs.invite_link_name("Аня", "ru") == "Организатор · Аня"
    long = orgs.invite_link_name("Очень Длинное Имя Участника Чата", "ru")
    assert len(long.encode("utf-16-le")) // 2 == 32 and long.endswith("…")
    astral = orgs.invite_link_name("😀" * 20, "ru")
    assert len(astral.encode("utf-16-le")) // 2 <= 32


# ---------------------------------------------------------------------------
# The samples are what the handler draws
# ---------------------------------------------------------------------------

SAMPLE_INPUTS: dict[str, tuple[str, str | None, bool, tuple[str, str, str] | None]] = {
    # sample title: (status, username Telegram or the store gives, stored at all,
    # what getChat gives: None for «chat not found»)
    "В чате, есть @username": ("member", NICK, True, None),
    "В чате, без @username": ("member", None, True, None),
    "Вышел из чата": ("left", NICK, True, None),
    "Не вступал в чат": ("never", NICK, True, None),
    "Не вступал, писал боту": ("never", None, False, (FIRST, LAST, NICK)),
    "Заблокирован в чате": ("kicked", NICK, True, None),
    "Telegram не ответил": ("unknown", None, True, None),
    "Имя неизвестно": ("never", None, False, None),
}


@pytest.mark.parametrize("title", list(SAMPLE_INPUTS))
async def test_each_card_sample_is_the_screen_the_handler_draws(
    title: str, drawn: list[Screen]
) -> None:
    """Samples built from imagined inputs drift from the handler's real ones;
    each card sample must equal the handler's screen for the same person."""
    status, username, stored, private = SAMPLE_INPUTS[title]
    sample = next(item for item in load_all()["org.card"] if item.title == title)
    store = Store({ORG: KnownName(FIRST, LAST, username)} if stored else {})
    bot = Telegram(status, username=username, private=private)
    await _tap(_card(), bot, ids=[OTHER, ORG], store=store)

    assert drawn[-1] == sample.build("ru")


async def test_the_invite_sample_is_the_screen_the_handler_draws(drawn: list[Screen]) -> None:
    sample = next(item for item in load_all()["org.invite"] if item.title == "Приглашение")
    bot = Telegram("left")
    await _tap(_inv(), bot)

    expected = sample.build("ru")
    sample_link = "https://t.me/+AAAAAAAAAAAAAAAA"
    assert drawn[-1].text == expected.text.replace(sample_link, bot.made[0])
    assert [b.label for row in drawn[-1].rows for b in row] == [
        b.label for row in expected.rows for b in row
    ]
