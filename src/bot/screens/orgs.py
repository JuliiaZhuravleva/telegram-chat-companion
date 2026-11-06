"""Knowledge-base organizers: who may save facts with /remember in one chat.

Four screens, built as plain data (``handlers/admin_kb.py`` sends them):

* ``orgs`` — the organizers of one chat. A tap on ``✖️ {name}`` removes that
  person at once and offers ``↩️ Вернуть`` on the result (undo over confirm).
* ``org.list`` — the chat's participants by activity; a tap adds. Current
  organizers carry ``✓`` and a tap on them only says so.
* ``org.add`` — a NEW message with a reply keyboard: Telegram's own person
  search (``request_users``) and ``✖️ Отмена``. The admin may also forward a
  message or type an ``@username``.
* ``org.fail`` — the answer when a forward or an ``@username`` names nobody
  the bot can add. The prompt stays open; the inline row offers the
  participant list or a way out.

Every name on a button comes from Telegram and is clipped to the phone row
(`clip`), never to a character count: twelve Cyrillic capitals and twelve
Latin dots are very different widths.
"""

from __future__ import annotations

import html
from collections.abc import Collection, Sequence
from typing import Final, Literal

from src.bot.custom_emoji import fence
from src.bot.screens.i18n import Tr
from src.bot.screens.lint import budget, label_width_px
from src.bot.screens.model import Button, ReplyButton, RequestUsers, Screen
from src.bot.screens.render import CANCEL, back, cancel, pager
from src.bot.screens.routes import REQUEST_IDS, cb

ORGS_PER_PAGE: Final = 3
"""Three names, the pager, the add row and the back row: the six-row cap with a pager."""
LIST_PER_PAGE: Final = 4
"""Four names, the pager and one shared last row (``✏️ Нет в списке`` + back)."""
NAME_PX: Final = budget(1) * 0.9
"""Width a name button's label is clipped to: 90 % of a full row.

The width estimate is a per-script average. Rendered at 390 px, long mixed
Cyrillic and Latin names clipped to the full row still overflowed by up to
20 px, and fit from 92 %. A name made of one wide letter repeated (``ммм…``)
overflows at any margin; Telegram's client then cuts it itself.
"""

Reason = Literal["not_found", "hidden", "elsewhere", "channel"]

# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

ORGS_TITLE: Final = Tr("👥 <b>Организаторы</b> · {t}", "👥 <b>Organizers</b> · {t}")
ORGS_BODY: Final = Tr(
    "Могут сохранять факты командой /remember. Нажатие на имя убирает человека.",
    "They can save facts with /remember. Tap a name to remove that person.",
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
"""A name Telegram would not give (the person left, the lookup failed)."""

# Buttons
ADD: Final = Tr("➕ Добавить", "➕ Add")
UNDO: Final = Tr("↩️ Вернуть", "↩️ Undo")
NOT_LISTED: Final = Tr("✏️ Нет в списке", "✏️ Not listed")
FROM_MEMBERS: Final = Tr("👥 Из участников", "👥 From members")
PICK_LABEL: Final = Tr("🔍 Найти в Telegram", "🔍 Find in Telegram")
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
    undo: tuple[int, int] | None = None,
) -> Screen:
    """The organizers of one chat, one page of them (``people`` is that page).

    ``undo`` is ``(user_id, position)`` of the person just removed: the
    ``↩️ Вернуть`` button puts them back where they were.
    """
    body = ORGS_BODY(lang) if people else ORGS_EMPTY(lang)
    rows: list[tuple[Button, ...]] = [
        (Button(clip(f"✖️ {name}", NAME_PX), cb("adm_kb_org_rm:", lang, cid, uid)),)
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
    rows.append((back("memory", cb("adm_pnl_grp:", lang, cid, "memory"), lang),))
    return Screen(
        id="orgs",
        text=f"{_title(ORGS_TITLE, lang, title, cid)}\n\n{body}",
        rows=tuple(rows),
        notice=notice,
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
    """Nobody to add from that forward or ``@username``; the prompt stays open."""
    return Screen(
        id="org.fail",
        text=f"{_title(NEW_TITLE, lang, title, cid)}\n\n{FAIL[reason](lang)}",
        rows=(
            (
                Button(FROM_MEMBERS(lang), cb("adm_kb_org_list:", lang, cid, 0)),
                cancel(cb("adm_kb_org_x:", lang, cid), lang),
            ),
        ),
    )
