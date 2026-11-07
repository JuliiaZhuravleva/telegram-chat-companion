"""Knowledge-base organizers: who may save facts with /remember in one chat.

Six screens, built as plain data (``handlers/admin_kb.py`` sends them):

* ``orgs`` — the organizers of one chat, reached from the chat's root. A tap
  on ``👤 {name}`` opens that person's card and changes nothing. Someone
  Telegram says is not in the group carries `` · не в чате``, explained in a
  line of its own while the page shows one.
* ``org.card`` — one organizer: a text mention (``tg://user?id=`` lives in the
  BODY only: as a URL button it can fail with BUTTON_USER_PRIVACY_RESTRICTED
  and lose the whole message), ``@username`` when known, the id, and what
  Telegram says about membership right now (`Membership`). The actions:
  ``👤 Профиль`` (a ``t.me/{username}`` link, only for a valid username),
  ``✉️ Пригласить в чат`` (only for someone who left or never joined: a banned
  person cannot use a link, and when Telegram did not answer nobody knows),
  ``✖️ Убрать из организаторов`` — removes at once and offers ``↩️ Вернуть``
  on the list (undo over confirm) — and back to the person's page.
* ``org.invite`` — a one-person link valid for seven days, in ``<code>``,
  with ``📤 Переслать`` (Telegram's share sheet) and ``🤖 Отправить от бота``.
  The second is always offered: nothing the bot stores says whether it may
  write to that person first, so a refusal (403) is answered on this screen.
* ``org.list`` — the chat's participants by activity; a tap adds. Current
  organizers carry ``✓`` and a tap on them only says so.
* ``org.add`` — a NEW message with a reply keyboard: Telegram's own person
  search (``request_users``) and ``✖️ Отмена``. The admin may also forward a
  message or type an ``@username``.
* ``org.fail`` — the answer when a forward or an ``@username`` names nobody
  the bot can add. The prompt stays open: its ``✖️ Отмена`` is still on the
  reply keyboard under the input, so the inline row offers only the
  participant list. A second, inline ``✖️ Отмена`` showed two of them.

Every name on a button comes from Telegram and is clipped to the phone row
(`clip`), never to a character count: twelve Cyrillic capitals and twelve
Latin dots are very different widths.
"""

from __future__ import annotations

import html
import re
from collections.abc import Collection, Sequence
from typing import Final, Literal
from urllib.parse import quote

from src.bot.custom_emoji import fence
from src.bot.screens.i18n import Tr
from src.bot.screens.lint import clip_room, label_width_px
from src.bot.screens.model import Button, ReplyButton, RequestUsers, Screen
from src.bot.screens.render import CANCEL, back, pager
from src.bot.screens.routes import REQUEST_IDS, cb

ORGS_PER_PAGE: Final = 3
"""Three names, the pager, the add row and the back row: the six-row cap with a pager."""
LIST_PER_PAGE: Final = 4
"""Four names, the pager and one shared last row (``✏️ Нет в списке`` + back)."""
NAME_PX: Final = clip_room(1)
"""Width a name button's label is clipped to: a full row, less the estimator's
error bound. The estimate is measured against the renderer per letter, so a
long mixed Cyrillic and Latin name no longer needs a margin of its own; a script
missing from the table reads wide and is cut early, never late."""

Reason = Literal["not_found", "hidden", "elsewhere", "channel"]

Membership = Literal["in_chat", "left", "removed", "never_joined", "unknown"]
"""What one ``getChatMember`` says about a person now. ``removed`` is a ban
(``kicked``); a removal without a ban reads as ``left``. ``unknown``: the lookup
failed, which says nothing either way."""
ABSENT: Final[frozenset[Membership]] = frozenset({"left", "removed", "never_joined"})
"""States that carry `` · не в чате`` on the list."""
INVITABLE: Final[frozenset[Membership]] = frozenset({"left", "never_joined"})
"""States a link can help: a banned person is refused at the door, and an
unknown state might be someone already inside."""

INVITE_DAYS: Final = 7
INVITE_NAME_LIMIT: Final = 32
"""Telegram's cap on an invite link's name, counted here in UTF-16 units."""
USERNAME_RE: Final = re.compile(r"^[A-Za-z0-9_]{5,32}$")
"""A username a ``t.me/`` link may be built from; anything else gets no link."""

# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

ORGS_TITLE: Final = Tr("👥 <b>Организаторы</b> · {t}", "👥 <b>Organizers</b> · {t}")
ORGS_BODY: Final = Tr(
    "Могут сохранять факты командой /remember в этом чате.",
    "They can save facts with /remember in this chat.",
)
ORGS_ABSENT: Final = Tr(
    "«Не в чате» — человека сейчас нет в группе, но он ещё организатор.",
    "«Not in chat»: the person is not in the group now but is still an organizer.",
)
ORGS_EMPTY: Final = Tr(
    "Могут сохранять факты командой /remember.\n\n"
    "Пока никого. Админы бота могут сохранять факты всегда.",
    "They can save facts with /remember.\n\nNobody yet. Bot admins can always save facts.",
)
NEW_TITLE: Final = Tr("👥 <b>Новый организатор</b> · {t}", "👥 <b>New organizer</b> · {t}")
LIST_BODY: Final = Tr(
    "Нажмите имя, чтобы добавить. Сверху те, кто пишет чаще.",
    "Tap a name to add that person. The most active are on top.",
)
LIST_MARKED: Final = Tr("✓ — уже организатор.", "✓ means already an organizer.")
LIST_EMPTY: Final = Tr(
    "Бот пока не видел здесь ни одного сообщения. Человека можно найти через «✏️ Нет в списке».",
    "The bot has not seen anyone write here yet. «✏️ Not listed» finds a person anyway.",
)
ADD_BODY: Final = Tr(
    "Перешлите сообщение этого человека или напишите его @username. "
    "Кнопка внизу ищет по всему Telegram.",
    "Forward a message from that person or type their @username. "
    "The button below searches all of Telegram.",
)
FAIL: Final[dict[Reason, Tr]] = {
    "not_found": Tr(
        "Не нашёл такого участника в этом чате. "
        "Попробуйте переслать его сообщение или выберите из участников.",
        "Couldn't find that member in this chat. "
        "Try forwarding their message, or pick from the members.",
    ),
    "hidden": Tr(
        "Пересланное сообщение не показывает автора: у человека включена приватность "
        "пересылки. Напишите его @username или выберите из участников.",
        "The forward hides its author: that person has forward privacy on. "
        "Type their @username, or pick from the members.",
    ),
    "elsewhere": Tr(
        "Знаю такой @username, но не видел его в этом чате. "
        "Перешлите сообщение этого человека или выберите из участников.",
        "I know that @username, but haven't seen them in this chat. "
        "Forward a message from them, or pick from the members.",
    ),
    "channel": Tr(
        "Это сообщение переслано из канала или группы, а не от человека. "
        "Перешлите сообщение самого человека или напишите его @username.",
        "That message was forwarded from a channel or a group, not from a person. "
        "Forward a message from the person, or type their @username.",
    ),
}

# Notices (plain text: `render.with_notice` escapes them and adds the ✓).
REMOVED: Final = Tr("{n} больше не организатор", "{n} is no longer an organizer")
ADDED: Final = Tr("{n} теперь организатор", "{n} is now an organizer")
RESTORED: Final = Tr("{n} снова организатор", "{n} is an organizer again")
ALREADY: Final = Tr("{n} уже организатор", "{n} is already an organizer")
ALREADY_TOAST: Final = Tr("Уже организатор", "Already an organizer")
MEMBER: Final = Tr("участник {id}", "member {id}")
"""The id, when no name was ever known: not from Telegram, not stored, not in the chat's history."""
NOT_IN_CHAT: Final = Tr(" · не в чате", " · not in chat")
"""Appended to an organizer's name when Telegram says the person is not in the group."""
NOT_ORGANIZER: Final = Tr(
    "<i>Этого человека уже нет среди организаторов.</i>",
    "<i>This person is no longer an organizer.</i>",
)
"""On the list, after a tap on a card or an invite of someone removed meanwhile."""

# The card
CARD_TITLE: Final = Tr("👤 <b>{n}</b> · {t}", "👤 <b>{n}</b> · {t}")
CARD_ID: Final = Tr("ID: <code>{id}</code>", "ID: <code>{id}</code>")
STATE: Final[dict[Membership, Tr]] = {
    "in_chat": Tr("Сейчас в чате.", "In the chat now."),
    "left": Tr("Сейчас не в чате.", "Not in the chat now."),
    "removed": Tr(
        "Удалён из чата и заблокирован в нём.",
        "Removed from the chat and banned there.",
    ),
    "never_joined": Tr("В чат не вступал.", "Never joined the chat."),
    "unknown": Tr(
        "Telegram не ответил, в чате ли человек сейчас.",
        "Telegram did not say whether this person is in the chat now.",
    ),
}
BANNED_HINT: Final = Tr(
    "Приглашение его не впустит, пока блокировку не снимут в настройках группы.",
    "An invitation will not let them in until the ban is lifted in the group's settings.",
)
CARD_ROLE: Final = Tr(
    "Может сохранять факты командой /remember в этом чате.",
    "Can save facts with /remember in this chat.",
)
NO_INVITE_RIGHT: Final = Tr(
    "<i>У бота нет права приглашать участников, ссылка не создана. "
    "Право выдают в настройках администраторов группы. Больше ничего не изменилось.</i>",
    "<i>The bot has no right to invite members, so no link was made. "
    "That right is granted in the group's administrator settings. Nothing else changed.</i>",
)
LINK_FAILED: Final = Tr(
    "<i>Telegram не создал ссылку. Ничего не изменилось, можно попробовать ещё раз.</i>",
    "<i>Telegram did not make the link. Nothing changed; it can be tried again.</i>",
)
ALREADY_IN_CHAT: Final = Tr(
    "<i>Этот человек уже в чате, ссылка не создана.</i>",
    "<i>This person is already in the chat, so no link was made.</i>",
)
NO_LINK: Final = Tr("<i>Ссылка не создана.</i>", "<i>No link was made.</i>")
"""An invite tapped for someone banned or unknown meanwhile; the card says why."""

# The invite
INVITE_TITLE: Final = Tr("✉️ <b>Приглашение</b> · {n}", "✉️ <b>Invitation</b> · {n}")
INVITE_BODY: Final = Tr(
    "Ссылка в «{t}» для одного человека, действует {d} дней.",
    "A link to «{t}» for one person, valid for {d} days.",
)
INVITE_LINK_NAME: Final = Tr("Организатор · {n}", "Organizer · {n}")
"""The link's name in the group's own list of invite links."""
SHARE_TEXT: Final = Tr("Приглашение в «{t}»", "An invitation to «{t}»")
SHARE_TEXT_UNTITLED: Final = Tr("Приглашение в чат", "An invitation to the chat")
DM_TEXT: Final = Tr(
    "Вас приглашают в чат «{t}». Ссылка для одного человека, действует {d} дней:\n{link}",
    "You are invited to the chat «{t}». The link is for one person, valid for {d} days:\n{link}",
)
DM_TEXT_UNTITLED: Final = Tr(
    "Вас приглашают в чат. Ссылка для одного человека, действует {d} дней:\n{link}",
    "You are invited to a chat. The link is for one person, valid for {d} days:\n{link}",
)
SENT: Final = Tr("Ссылка отправлена в личные сообщения", "The link was sent as a private message")
DM_FORBIDDEN: Final = Tr(
    "<i>Бот не может написать этому человеку первым: тот ещё не открывал с ним "
    "личный чат. Ссылку можно отправить через «📤 Переслать».</i>",
    "<i>The bot cannot write to this person first: they have not opened a private "
    "chat with it. The link can be sent with «📤 Share».</i>",
)
DM_FAILED: Final = Tr(
    "<i>Telegram не доставил сообщение. Ссылка действует, её можно отправить "
    "через «📤 Переслать».</i>",
    "<i>Telegram did not deliver the message. The link works and can be sent with «📤 Share».</i>",
)

# Buttons
ADD: Final = Tr("➕ Добавить", "➕ Add")
UNDO: Final = Tr("↩️ Вернуть", "↩️ Undo")
NOT_LISTED: Final = Tr("✏️ Нет в списке", "✏️ Not listed")
FROM_MEMBERS: Final = Tr("👥 Из участников", "👥 From members")
PICK_LABEL: Final = Tr("🔍 Найти в Telegram", "🔍 Find in Telegram")
PROFILE: Final = Tr("👤 Профиль", "👤 Profile")
INVITE: Final = Tr("✉️ Пригласить в чат", "✉️ Invite to the chat")
REMOVE: Final = Tr("✖️ Убрать из организаторов", "✖️ Remove from organizers")
SHARE: Final = Tr("📤 Переслать", "📤 Share")
SEND_FROM_BOT: Final = Tr("🤖 Отправить от бота", "🤖 Send from the bot")
PLACEHOLDER: Final = "@username"

_ELLIPSIS: Final = "…"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def clip(label: str, max_px: float) -> str:
    """``label`` cut to fit ``max_px`` of a button (lint R10's estimate), with ``…``."""
    if label_width_px(label) <= max_px:
        return label
    cut = label
    while cut and label_width_px(cut + _ELLIPSIS) > max_px:
        cut = cut[:-1]
    return cut.rstrip() + _ELLIPSIS


def chat_label(title: str | None, cid: int) -> str:
    """The chat's title as HTML, or its id when the bot never learned the title."""
    return html.escape(title) if title else str(cid)


def _part(value: object) -> str:
    """A name part as text. Only text counts: whatever else arrives (a test
    double, a None) is no name rather than its ``repr``."""
    return value.strip() if isinstance(value, str) else ""


def display_name(username: object, first_name: object, last_name: object = None) -> str | None:
    """``@nick``, else ``First Last``; None when Telegram gave no part of a name."""
    if nick := _part(username):
        return f"@{nick}"
    return full_name(first_name, last_name)


def full_name(first_name: object, last_name: object = None) -> str | None:
    """``First Last``, the words a mention shows; None when neither part is text."""
    return " ".join(p for p in (_part(first_name), _part(last_name)) if p) or None


def username_of(username: object) -> str | None:
    """A username as text without its ``@``, or None."""
    return _part(username).lstrip("@") or None


def organizer_label(name: str, lang: str, *, absent: bool = False) -> str:
    """``👤 {name}`` clipped to the row, with `` · не в чате`` kept whole after the clip."""
    if not absent:
        return clip(f"👤 {name}", NAME_PX)
    mark = NOT_IN_CHAT(lang)
    return clip(f"👤 {name}", NAME_PX - label_width_px(mark)) + mark


def profile_username(username: str | None) -> str | None:
    """``username`` without its ``@`` when a ``t.me/`` link can be built from it, else None."""
    if not username:
        return None
    nick = username.strip().lstrip("@")
    return nick if USERNAME_RE.match(nick) else None


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def invite_link_name(name: str, lang: str) -> str:
    """``Организатор · {name}`` within Telegram's 32 units, the name cut with ``…``."""
    full = INVITE_LINK_NAME(lang).format(n=name)
    if _utf16(full) <= INVITE_NAME_LIMIT:
        return full
    cut = name
    while cut and _utf16(INVITE_LINK_NAME(lang).format(n=cut.rstrip() + _ELLIPSIS)) > (
        INVITE_NAME_LIMIT
    ):
        cut = cut[:-1]
    return INVITE_LINK_NAME(lang).format(n=cut.rstrip() + _ELLIPSIS)


def invite_dm_text(lang: str, title: str | None, link: str) -> str:
    """What the bot sends the person: the chat's title and the link, both escaped
    (the bot's default parse mode is HTML)."""
    if title:
        return DM_TEXT(lang).format(t=html.escape(title), d=INVITE_DAYS, link=html.escape(link))
    return DM_TEXT_UNTITLED(lang).format(d=INVITE_DAYS, link=html.escape(link))


def share_url(lang: str, title: str | None, link: str) -> str:
    """Telegram's share sheet with the link and a line naming the chat."""
    text = SHARE_TEXT(lang).format(t=title) if title else SHARE_TEXT_UNTITLED(lang)
    return f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(text, safe='')}"


def candidate_name(first_name: object, username: object, uid: int) -> str:
    """``Имя @nick`` for the participant list, falling back part by part to the id."""
    first = str(first_name).strip() if first_name else ""
    nick = f"@{username}" if username else ""
    return " ".join(part for part in (first, nick) if part) or str(uid)


def page_of(index: int, per_page: int = ORGS_PER_PAGE) -> int:
    """The page a list position falls on."""
    return max(index, 0) // per_page


def _title(template: Tr, lang: str, title: str | None, cid: int) -> str:
    return template(lang).format(t=chat_label(title, cid))


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def orgs_screen(
    lang: str,
    cid: int,
    title: str | None,
    people: Sequence[tuple[int, str]],
    *,
    page: int = 0,
    pages: int = 1,
    notice: str | None = None,
    notice_raw: str | None = None,
    undo: tuple[int, int] | None = None,
    absent: Collection[int] = (),
) -> Screen:
    """The organizers of one chat, one page of them (``people`` is that page).

    A name opens the person's card (``adm_kb_org:``); nothing is written by
    the tap. ``undo`` is ``(user_id, position)`` of the person just removed:
    the ``↩️ Вернуть`` button puts them back where they were. ``absent`` are
    the ids Telegram says are not in the group: still organizers, marked.
    """
    body = ORGS_BODY(lang) if people else ORGS_EMPTY(lang)
    if any(uid in absent for uid, _ in people):
        body += f"\n{ORGS_ABSENT(lang)}"
    rows: list[tuple[Button, ...]] = [
        (
            Button(
                organizer_label(name, lang, absent=uid in absent),
                cb("adm_kb_org:", lang, cid, uid),
            ),
        )
        for uid, name in people
    ]
    arrows = pager(page, pages, lambda p: cb("adm_kb_orgs:", lang, cid, p))
    if arrows is not None:
        rows.append(arrows)
    add = Button(ADD(lang), cb("adm_kb_org_list:", lang, cid, 0))
    if undo is not None:
        uid, position = undo
        rows.append((Button(UNDO(lang), cb("adm_kb_org_undo:", lang, cid, uid, position)), add))
    else:
        rows.append((add,))
    rows.append((back("chat", cb("adm_pnl_menu:", lang, cid), lang),))
    return Screen(
        id="orgs",
        text=f"{_title(ORGS_TITLE, lang, title, cid)}\n\n{body}",
        rows=tuple(rows),
        notice=notice,
        notice_raw=notice_raw,
    )


def org_card_screen(
    lang: str,
    cid: int,
    title: str | None,
    uid: int,
    name: str,
    *,
    state: Membership,
    full: str | None = None,
    username: str | None = None,
    page: int = 0,
    notice: str | None = None,
    notice_raw: str | None = None,
) -> Screen:
    """One organizer: who they are, whether they are in the chat, what can be done.

    ``name`` is the list's name for the person (``_names`` in the handler),
    ``full`` their first and last name for the mention, ``username`` without
    or with its ``@``. ``page`` is the list page the person is on: the back
    button returns there.
    """
    shown = html.escape(full or name)
    mention = f'<a href="tg://user?id={uid}">{shown}</a>'
    nick = profile_username(username)
    head = f"{mention} · @{nick}" if nick else mention
    lines = [head, CARD_ID(lang).format(id=uid), "", STATE[state](lang)]
    if state == "removed":
        lines.append(BANNED_HINT(lang))
    lines.append(CARD_ROLE(lang))
    rows: list[tuple[Button, ...]] = []
    if nick:
        rows.append((Button(PROFILE(lang), url=f"https://t.me/{nick}"),))
    if state in INVITABLE:
        rows.append((Button(INVITE(lang), cb("adm_kb_org_inv:", lang, cid, uid)),))
    rows.append((Button(REMOVE(lang), cb("adm_kb_org_rm:", lang, cid, uid)),))
    rows.append((back("orgs", cb("adm_kb_orgs:", lang, cid, page), lang),))
    heading = CARD_TITLE(lang).format(n=html.escape(name), t=chat_label(title, cid))
    return Screen(
        id="org.card",
        text=f"{heading}\n\n" + "\n".join(lines),
        rows=tuple(rows),
        notice=notice,
        notice_raw=notice_raw,
    )


def org_invite_screen(
    lang: str,
    cid: int,
    title: str | None,
    uid: int,
    name: str,
    link: str,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
) -> Screen:
    """A fresh one-person link for ``uid``, valid `INVITE_DAYS` days, and two ways to send it."""
    body = INVITE_BODY(lang).format(t=chat_label(title, cid), d=INVITE_DAYS)
    return Screen(
        id="org.invite",
        text=(
            f"{INVITE_TITLE(lang).format(n=html.escape(name))}\n\n"
            f"{body}\n<code>{html.escape(link)}</code>"
        ),
        rows=(
            (Button(SHARE(lang), url=share_url(lang, title, link)),),
            (Button(SEND_FROM_BOT(lang), cb("adm_kb_org_dm:", lang, cid, uid)),),
            (back("org.card", cb("adm_kb_org:", lang, cid, uid), lang),),
        ),
        notice=notice,
        notice_raw=notice_raw,
    )


def org_list_screen(
    lang: str,
    cid: int,
    title: str | None,
    candidates: Sequence[tuple[int, str]],
    *,
    current: Collection[int] = (),
    page: int = 0,
    pages: int = 1,
) -> Screen:
    """One page of the chat's participants, most active first; a tap adds."""
    rows: list[tuple[Button, ...]] = []
    for uid, name in candidates:
        label = clip(f"✓ {name}", NAME_PX) if uid in current else fence(clip(name, NAME_PX))
        rows.append((Button(label, cb("adm_kb_org_pick:", lang, cid, uid)),))
    arrows = pager(page, pages, lambda p: cb("adm_kb_org_list:", lang, cid, p))
    if arrows is not None:
        rows.append(arrows)
    rows.append(
        (
            Button(NOT_LISTED(lang), cb("adm_kb_org_add:", lang, cid)),
            back("orgs", cb("adm_kb_orgs:", lang, cid, 0), lang),
        )
    )
    if not candidates:
        body = LIST_EMPTY(lang)
    elif any(uid in current for uid, _ in candidates):
        body = f"{LIST_BODY(lang)}\n{LIST_MARKED(lang)}"
    else:
        body = LIST_BODY(lang)
    return Screen(
        id="org.list",
        text=f"{_title(NEW_TITLE, lang, title, cid)}\n\n{body}",
        rows=tuple(rows),
    )


def org_add_screen(lang: str, title: str | None = None, cid: int | None = None) -> Screen:
    """The add prompt: a new message whose keyboard sits under the input field.

    ``✖️ Отмена`` is a plain reply button: its label comes back as the admin's
    text and is routed ahead of the forward/@username reader.
    """
    if title is not None:
        head = NEW_TITLE(lang).format(t=html.escape(title))
    elif cid is not None:
        head = _title(NEW_TITLE, lang, None, cid)
    else:
        head = NEW_TITLE(lang).split(" · ", 1)[0]
    return Screen(
        id="org.add",
        text=f"{head}\n\n{ADD_BODY(lang)}",
        reply=(
            (ReplyButton(PICK_LABEL(lang), RequestUsers(request_id=REQUEST_IDS.ORG_PICK)),),
            (ReplyButton(CANCEL(lang)),),
        ),
        placeholder=PLACEHOLDER,
    )


def org_fail_screen(lang: str, cid: int, title: str | None, reason: Reason) -> Screen:
    """Nobody to add from that forward or ``@username``; the prompt stays open.

    No inline ``✖️ Отмена``: the prompt's own is still on the reply keyboard
    under the input, and a message cannot take that keyboard away and carry
    inline buttons at once, so the one way out stays the one already there.
    """
    return Screen(
        id="org.fail",
        text=f"{_title(NEW_TITLE, lang, title, cid)}\n\n{FAIL[reason](lang)}",
        rows=((Button(FROM_MEMBERS(lang), cb("adm_kb_org_list:", lang, cid, 0)),),),
    )
