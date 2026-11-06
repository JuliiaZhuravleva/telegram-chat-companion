"""Samples of the organizer screens: ``orgs``, ``org.list``, ``org.add``, ``org.fail``.

One sample per state that changes the rows: a list, an empty list, a removal
with ``↩️ Вернуть`` on a paged list (the six-row worst case), the participant
list with current organizers marked, and every reason ``org.fail`` tells apart.
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
    orgs.clip(f"✖️ {_LONG}", orgs.NAME_PX).removeprefix("✖️ "),
    orgs.clip(_LONG, orgs.NAME_PX),
)

_ADD_EXEMPT: Final = {
    "R9": "«Найти в Telegram» names the app whose own search the button opens; "
    "no Russian word says which search it is",
}


@sample("orgs", section="Чаты", title="Организаторы", nav="panel", user_data=_PEOPLE)
def orgs_list(lang: Lang) -> Screen:
    people = [(fake.USER_ID, fake.PERSON), (fake.USER_ID_OTHER, fake.PERSON_OTHER)]
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


@sample("orgs", section="Чаты", title="Добавлен", nav="panel", user_data=_PEOPLE)
def orgs_added(lang: Lang) -> Screen:
    people = [(fake.USER_ID, fake.PERSON), (fake.USER_ID_OTHER, fake.PERSON_OTHER)]
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


@sample("org.fail", section="Чаты", title="Не нашёл в чате", nav="prompt", user_data=_PEOPLE)
def org_fail_not_found(lang: Lang) -> Screen:
    return orgs.org_fail_screen(lang, _CID, fake.CHAT_TITLE, "not_found")


@sample(
    "org.fail", section="Чаты", title="Автор скрыт приватностью", nav="prompt", user_data=_PEOPLE
)
def org_fail_hidden(lang: Lang) -> Screen:
    return orgs.org_fail_screen(lang, _CID, fake.CHAT_TITLE, "hidden")


@sample(
    "org.fail", section="Чаты", title="@username не из этого чата", nav="prompt", user_data=_PEOPLE
)
def org_fail_elsewhere(lang: Lang) -> Screen:
    return orgs.org_fail_screen(lang, _CID, fake.CHAT_TITLE, "elsewhere")


@sample("org.fail", section="Чаты", title="Переслано из канала", nav="prompt", user_data=_PEOPLE)
def org_fail_channel(lang: Lang) -> Screen:
    return orgs.org_fail_screen(lang, _CID, fake.CHAT_TITLE, "channel")
