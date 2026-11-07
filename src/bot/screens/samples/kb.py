"""Samples of the organizer screens: ``orgs``, ``org.card``, ``org.invite``,
``org.list``, ``org.add``, ``org.fail``.

One sample per state that changes the rows: a list, an empty list, a removal
with ``↩️ Вернуть`` on a paged list (the six-row worst case), the card in every
membership state Telegram can report and after a refused link, the invite as
shown and after each answer to «🤖 Отправить от бота», the participant list
with current organizers marked, and every reason ``org.fail`` tells apart.

The card and invite samples take their arguments in the shape the handler
passes them (``_card`` mirrors ``admin_kb._card_view``): the name the list uses,
the full name for the mention, a username without ``@`` or None, the page.

Every one-page list shows three people, the count on the chat's root sample:
a reader walking from «👥 Организаторы · 3» must land on three names.
"""

from __future__ import annotations

from typing import Final

from src.bot.screens import orgs
from src.bot.screens.i18n import Lang
from src.bot.screens.model import Screen
from src.bot.screens.registry import sample
from src.bot.screens.samples import fake

_CID = fake.CHAT_ID
_THIRD_ID: Final = 555000333
_FOURTH_ID: Final = 555000444
_THIRD: Final = "Третий Участник"
_LONG: Final = "Очень Длинное Имя Участника Чата Которое Не Влезет"
_NICK_OTHER: Final = "other_user"
_NICK: Final = fake.USERNAME.lstrip("@")

_FIRST: Final = "Тест"
_FIRST_OTHER: Final = "Другой"
_LINK: Final = "https://t.me/+AAAAAAAAAAAAAAAA"
"""An invented invite link: the shape Telegram returns, no real chat behind it."""

_PEOPLE: Final = (
    fake.CHAT_TITLE,
    fake.PERSON,
    fake.PERSON_OTHER,
    _THIRD,
    _FIRST,
    _FIRST_OTHER,
    fake.USERNAME,
    f"@{_NICK_OTHER}",
    _LONG,
    # The long name as the buttons show it, clipped to the row: still user data.
    orgs.clip(f"👤 {_LONG}", orgs.NAME_PX).removeprefix("👤 "),
    orgs.clip(_LONG, orgs.NAME_PX),
    # ...and clipped shorter, to leave room for the « · не в чате» mark.
    *(
        orgs.organizer_label(_LONG, lang, absent=True)
        .removeprefix("👤 ")
        .removesuffix(orgs.NOT_IN_CHAT(lang))
        for lang in ("ru", "en")
    ),
    _LINK,
)

_ADD_EXEMPT: Final = {
    "R9": "«Найти в Telegram» names the app whose own search the button opens; "
    "no Russian word says which search it is",
}

_FAIL_EXEMPT: Final = {
    "R3": "the prompt's ✖️ Отмена is the reply keyboard still under the input, sent "
    "with the prompt; an inline one here as well showed two «✖️ Отмена»",
}


@sample("orgs", section="Чаты", title="Организаторы", nav="panel", user_data=_PEOPLE)
def orgs_list(lang: Lang) -> Screen:
    people = [
        (fake.USER_ID, fake.PERSON),
        (fake.USER_ID_OTHER, fake.PERSON_OTHER),
        (_THIRD_ID, _THIRD),
    ]
    return orgs.orgs_screen(lang, _CID, fake.CHAT_TITLE, people)


@sample("orgs", section="Чаты", title="Организаторов нет", nav="panel", user_data=_PEOPLE)
def orgs_empty(lang: Lang) -> Screen:
    return orgs.orgs_screen(lang, _CID, fake.CHAT_TITLE, [])


@sample(
    "orgs",
    section="Чаты",
    title="Убран, можно вернуть; страниц несколько",
    nav="panel",
    user_data=_PEOPLE,
)
def orgs_removed(lang: Lang) -> Screen:
    people = [(fake.USER_ID, fake.PERSON), (_THIRD_ID, _THIRD), (_FOURTH_ID, _LONG)]
    return orgs.orgs_screen(
        lang,
        _CID,
        fake.CHAT_TITLE,
        people,
        page=0,
        pages=2,
        notice=orgs.REMOVED(lang).format(n=fake.PERSON_OTHER),
        undo=(fake.USER_ID_OTHER, 1),
    )


@sample("orgs", section="Чаты", title="Двое не в чате", nav="panel", user_data=_PEOPLE)
def orgs_not_in_chat(lang: Lang) -> Screen:
    people = [(fake.USER_ID, fake.PERSON), (_THIRD_ID, _THIRD), (_FOURTH_ID, _LONG)]
    return orgs.orgs_screen(lang, _CID, fake.CHAT_TITLE, people, absent={_THIRD_ID, _FOURTH_ID})


@sample("orgs", section="Чаты", title="Добавлен", nav="panel", user_data=_PEOPLE)
def orgs_added(lang: Lang) -> Screen:
    people = [
        (fake.USER_ID, fake.PERSON),
        (_THIRD_ID, _THIRD),
        (fake.USER_ID_OTHER, fake.PERSON_OTHER),
    ]
    return orgs.orgs_screen(
        lang,
        _CID,
        fake.CHAT_TITLE,
        people,
        notice=orgs.ADDED(lang).format(n=fake.PERSON_OTHER),
    )


@sample("org.list", section="Чаты", title="Участники", nav="panel", user_data=_PEOPLE)
def org_list(lang: Lang) -> Screen:
    candidates = [
        (fake.USER_ID, orgs.candidate_name(_FIRST, _NICK, fake.USER_ID)),
        (fake.USER_ID_OTHER, orgs.candidate_name(_FIRST_OTHER, _NICK_OTHER, fake.USER_ID_OTHER)),
        (_THIRD_ID, orgs.candidate_name(_THIRD, None, _THIRD_ID)),
        (_FOURTH_ID, orgs.candidate_name(_LONG, None, _FOURTH_ID)),
    ]
    return orgs.org_list_screen(
        lang,
        _CID,
        fake.CHAT_TITLE,
        candidates,
        current={fake.USER_ID},
        page=0,
        pages=3,
    )


@sample(
    "org.list",
    section="Чаты",
    title="Участников бот не видел",
    nav="panel",
    user_data=_PEOPLE,
)
def org_list_empty(lang: Lang) -> Screen:
    return orgs.org_list_screen(lang, _CID, fake.CHAT_TITLE, [])


@sample(
    "org.add",
    section="Чаты",
    title="Новый организатор",
    nav="prompt",
    user_data=_PEOPLE,
    exempt=_ADD_EXEMPT,
)
def org_add(lang: Lang) -> Screen:
    return orgs.org_add_screen(lang, fake.CHAT_TITLE, _CID)


@sample(
    "org.fail",
    section="Чаты",
    title="Не нашёл в чате",
    nav="prompt",
    user_data=_PEOPLE,
    exempt=_FAIL_EXEMPT,
)
def org_fail_not_found(lang: Lang) -> Screen:
    return orgs.org_fail_screen(lang, _CID, fake.CHAT_TITLE, "not_found")


@sample(
    "org.fail",
    section="Чаты",
    title="Автор скрыт приватностью",
    nav="prompt",
    user_data=_PEOPLE,
    exempt=_FAIL_EXEMPT,
)
def org_fail_hidden(lang: Lang) -> Screen:
    return orgs.org_fail_screen(lang, _CID, fake.CHAT_TITLE, "hidden")


@sample(
    "org.fail",
    section="Чаты",
    title="@username не из этого чата",
    nav="prompt",
    user_data=_PEOPLE,
    exempt=_FAIL_EXEMPT,
)
def org_fail_elsewhere(lang: Lang) -> Screen:
    return orgs.org_fail_screen(lang, _CID, fake.CHAT_TITLE, "elsewhere")


@sample(
    "org.fail",
    section="Чаты",
    title="Переслано из канала",
    nav="prompt",
    user_data=_PEOPLE,
    exempt=_FAIL_EXEMPT,
)
def org_fail_channel(lang: Lang) -> Screen:
    return orgs.org_fail_screen(lang, _CID, fake.CHAT_TITLE, "channel")


# ---------------------------------------------------------------------------
# org.card: one per membership state, plus a refused link
# ---------------------------------------------------------------------------


def _card(
    lang: Lang,
    state: orgs.Membership,
    *,
    username: str | None = _NICK,
    notice_raw: str | None = None,
) -> Screen:
    """The card as ``_card_view`` draws it: the list's name (``@nick`` when
    Telegram gave a username), the full name for the mention, page of position 1."""
    name = f"@{username}" if username else fake.PERSON
    return orgs.org_card_screen(
        lang,
        _CID,
        fake.CHAT_TITLE,
        fake.USER_ID,
        name,
        state=state,
        full=fake.PERSON,
        username=username,
        page=orgs.page_of(1),
        notice_raw=notice_raw,
    )


@sample("org.card", section="Чаты", title="В чате, есть @username", nav="panel", user_data=_PEOPLE)
def org_card_in_chat(lang: Lang) -> Screen:
    return _card(lang, "in_chat")


@sample("org.card", section="Чаты", title="В чате, без @username", nav="panel", user_data=_PEOPLE)
def org_card_in_chat_no_username(lang: Lang) -> Screen:
    return _card(lang, "in_chat", username=None)


@sample("org.card", section="Чаты", title="Вышел из чата", nav="panel", user_data=_PEOPLE)
def org_card_left(lang: Lang) -> Screen:
    return _card(lang, "left")


@sample("org.card", section="Чаты", title="Не вступал в чат", nav="panel", user_data=_PEOPLE)
def org_card_never_joined(lang: Lang) -> Screen:
    # Telegram answers "member not found" with no user: the name comes from
    # the store, which may hold no username at all.
    return _card(lang, "never_joined", username=None)


@sample("org.card", section="Чаты", title="Заблокирован в чате", nav="panel", user_data=_PEOPLE)
def org_card_removed(lang: Lang) -> Screen:
    return _card(lang, "removed")


@sample("org.card", section="Чаты", title="Telegram не ответил", nav="panel", user_data=_PEOPLE)
def org_card_unknown(lang: Lang) -> Screen:
    return _card(lang, "unknown", username=None)


@sample(
    "org.card",
    section="Чаты",
    title="У бота нет права приглашать",
    nav="panel",
    user_data=_PEOPLE,
)
def org_card_no_right(lang: Lang) -> Screen:
    return _card(lang, "left", notice_raw=orgs.NO_INVITE_RIGHT(lang))


# ---------------------------------------------------------------------------
# org.invite: as made, after a 403, after a sent DM
# ---------------------------------------------------------------------------


def _invite(lang: Lang, *, notice: str | None = None, notice_raw: str | None = None) -> Screen:
    return orgs.org_invite_screen(
        lang,
        _CID,
        fake.CHAT_TITLE,
        fake.USER_ID,
        fake.USERNAME,
        _LINK,
        notice=notice,
        notice_raw=notice_raw,
    )


@sample("org.invite", section="Чаты", title="Приглашение", nav="panel", user_data=_PEOPLE)
def org_invite(lang: Lang) -> Screen:
    return _invite(lang)


@sample(
    "org.invite",
    section="Чаты",
    title="Бот не может написать первым",
    nav="panel",
    user_data=_PEOPLE,
)
def org_invite_forbidden(lang: Lang) -> Screen:
    return _invite(lang, notice_raw=orgs.DM_FORBIDDEN(lang))


@sample("org.invite", section="Чаты", title="Отправлено от бота", nav="panel", user_data=_PEOPLE)
def org_invite_sent(lang: Lang) -> Screen:
    return _invite(lang, notice=orgs.SENT(lang))
