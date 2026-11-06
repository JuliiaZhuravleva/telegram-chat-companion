"""Samples of the screens people meet outside the panel, and of the cards the bot sends.

Every sample calls the real builder in ``screens/entry.py`` or
``screens/cards.py`` with invented values, so the contact sheet shows exactly
what the handlers send. One sample per state that changes the rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from src.bot.command_registry import CommandScope
from src.bot.screens import cards, entry
from src.bot.screens.i18n import Lang, Tr
from src.bot.screens.model import Screen
from src.bot.screens.registry import sample
from src.bot.screens.samples import fake

_BOT: Final = "example_companion_bot"
"""An invented bot username: the links only need a shape."""

_AT: Final = datetime(2026, 11, 3, 14, 5, tzinfo=UTC)

_NOT_A_SCREEN: Final = "a one-line reply in the chat, not a screen with a title"
_MENU: Final = "Telegram's own command list, drawn as text; it has no title line"
_PERSON_DATA: Final = (fake.PERSON, fake.PERSON_OTHER, fake.CHAT_TITLE)


def _trigger(lang: Lang) -> str:
    return "бот" if lang == "ru" else "bot"


# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------


@sample("start.member", section="Вход", title="/start у участника", nav="card")
def start_member(lang: Lang) -> Screen:
    return entry.start_member(lang, bot_username=_BOT)


@sample("start.admin", section="Вход", title="/start у админа бота", nav="card")
def start_admin(lang: Lang) -> Screen:
    return entry.start_admin(lang)


@sample("help.dm", section="Вход", title="/help в личке", nav="panel")
def help_dm(lang: Lang) -> Screen:
    return entry.help_dm(lang, trigger=_trigger(lang), bot_username=_BOT, admin=False)


@sample("help.group", section="Вход", title="/help в чате: всё включено", nav="root")
def help_group_all(lang: Lang) -> Screen:
    return entry.help_group(
        lang,
        user_id=fake.USER_ID,
        trigger=_trigger(lang),
        voice=True,
        video_notes=True,
        memory=True,
        summary=True,
        kb=True,
    )


@sample("help.group", section="Вход", title="/help в чате: сообщения не хранятся", nav="root")
def help_group_no_summary(lang: Lang) -> Screen:
    return entry.help_group(
        lang,
        user_id=fake.USER_ID,
        trigger=_trigger(lang),
        voice=True,
        video_notes=False,
        memory=False,
        summary=False,
        kb=False,
    )


@sample(
    "menu.dm", section="Вход", title="Меню команд: админ бота", nav="card", exempt={"R5": _MENU}
)
def menu_admin(lang: Lang) -> Screen:
    return entry.command_menu(lang, CommandScope.ADMIN)


@sample("menu.dm", section="Вход", title="Меню команд: личка", nav="card", exempt={"R5": _MENU})
def menu_private(lang: Lang) -> Screen:
    return entry.command_menu(lang, CommandScope.PRIVATE)


@sample("menu.group", section="Вход", title="Меню команд: чат", nav="card", exempt={"R5": _MENU})
def menu_group(lang: Lang) -> Screen:
    return entry.command_menu(lang, CommandScope.GROUPS)


@sample(
    "group.admin_hint",
    section="Вход",
    title="/admin в чате",
    nav="group",
    exempt={"R5": _NOT_A_SCREEN},
)
def admin_hint(lang: Lang) -> Screen:
    return entry.admin_hint(lang, bot_username=_BOT)


@sample(
    "group.added",
    section="Вход",
    title="Бота добавили в чат",
    nav="group",
    exempt={"R5": _NOT_A_SCREEN},
)
def group_added(lang: Lang) -> Screen:
    return entry.group_added(lang)


# ---------------------------------------------------------------------------
# Карточки от бота
# ---------------------------------------------------------------------------


def _applicant(lang: Lang, *, added: bool = False) -> cards.Applicant:
    return cards.Applicant(
        chat_id=fake.CHAT_ID,
        chat_title=fake.CHAT_TITLE if lang == "ru" else fake.CHAT_TITLE_EN,
        chat_type="supergroup",
        user_id=fake.USER_ID,
        user_first_name=fake.PERSON,
        user_username=fake.USERNAME.lstrip("@"),
        message_text=None if added else Tr("бот, привет", "hi bot")(lang),
        added=added,
    )


@sample(
    "card.access",
    section="Карточки от бота",
    title="Заявка: написали в чате",
    nav="card",
    user_data=_PERSON_DATA,
)
def access_asked(lang: Lang) -> Screen:
    return cards.access_card(lang, _applicant(lang), attempt_id=fake.ATTEMPT_ID)


@sample(
    "card.access",
    section="Карточки от бота",
    title="Заявка: бота добавили",
    nav="card",
    user_data=_PERSON_DATA,
)
def access_added(lang: Lang) -> Screen:
    return cards.access_card(lang, _applicant(lang, added=True), attempt_id=fake.ATTEMPT_ID)


@sample(
    "card.access.ok",
    section="Карточки от бота",
    title="Заявка одобрена",
    nav="card",
    user_data=_PERSON_DATA,
)
def access_ok(lang: Lang) -> Screen:
    return cards.access_approved(lang, _applicant(lang))


@sample(
    "card.access.no",
    section="Карточки от бота",
    title="Заявка отклонена",
    nav="card",
    user_data=_PERSON_DATA,
)
def access_no(lang: Lang) -> Screen:
    return cards.access_rejected(lang, _applicant(lang))


_ISSUES: Final = {
    "ru": (
        cards.IssueLine(
            critical=True,
            message="Основной ИИ не отвечает",
            detail="429: quota exceeded",
            hint="Пополните баланс у провайдера, бот пока отвечает запасным",
        ),
        cards.IssueLine(critical=False, message="Эмбеддинги запаздывают на 40 минут"),
    ),
    "en": (
        cards.IssueLine(
            critical=True,
            message="The main AI does not answer",
            detail="429: quota exceeded",
            hint="Top up the provider balance; the bot answers with the fallback meanwhile",
        ),
        cards.IssueLine(critical=False, message="Embeddings lag 40 minutes behind"),
    ),
}


@sample("card.health", section="Карточки от бота", title="Сбой", nav="card")
def health(lang: Lang) -> Screen:
    return cards.health_card(
        lang, at=_AT, critical=True, issues=_ISSUES[lang], messages_30m=42, fallbacks_15m=7
    )


@sample("card.health", section="Карточки от бота", title="Перебои: напоминание", nav="card")
def health_reminder(lang: Lang) -> Screen:
    return cards.health_card(
        lang,
        at=_AT,
        critical=False,
        issues=_ISSUES[lang][1:],
        messages_30m=42,
        fallbacks_15m=0,
        reminder=True,
    )


@sample("card.recovered", section="Карточки от бота", title="Снова здоров", nav="card")
def recovered(lang: Lang) -> Screen:
    cleared = (Tr("Основной ИИ отвечает", "The main AI answers")(lang),)
    return cards.recovered_card(lang, at=_AT, cleared=cleared)


@sample("card.spend", section="Карточки от бота", title="Дневной лимит превышен", nav="card")
def spend(lang: Lang) -> Screen:
    return cards.spend_card(lang, total=Decimal("1.27"), limit=Decimal("1.00"))


# ---------------------------------------------------------------------------
# В чатах
# ---------------------------------------------------------------------------

_SUMMARY_BODY: Final = Tr(
    "🗣 Аня и Боря обсуждали поездку на выходные.\n🚗 Решили ехать в субботу утром.",
    "🗣 Ann and Bob talked about a weekend trip.\n🚗 They will leave on Saturday morning.",
)


@sample("summary.result", section="В чатах", title="Саммари", nav="group")
def summary_result(lang: Lang) -> Screen:
    return entry.summary_result(lang, count=100, body_html=_SUMMARY_BODY(lang))


@sample(
    "summary.invalid",
    section="В чатах",
    title="/summary с непонятным числом",
    nav="group",
    exempt={"R5": _NOT_A_SCREEN},
)
def summary_invalid(lang: Lang) -> Screen:
    return entry.summary_invalid(lang, user_id=fake.USER_ID, low=20, high=1000)


_FACTS: Final = Tr(
    "• Созвон по вторникам в 19:00\n"
    "• Пароль от вайфая на даче — у Бори\n"
    "• Взнос на подарок до 15.11 ⏳ до 15.11.2026",
    "• The call is on Tuesdays at 19:00\n"
    "• Bob has the cottage wifi password\n"
    "• The gift fund closes on 15.11 ⏳ until 15.11.2026",
)


@sample("kb.group", section="В чатах", title="/kb в чате: несколько страниц", nav="group")
def kb_group_pages(lang: Lang) -> Screen:
    return entry.kb_group(lang, total=12, body=_FACTS(lang), page=0, pages=2)


@sample("kb.group", section="В чатах", title="/kb в чате: одна страница", nav="group")
def kb_group_single(lang: Lang) -> Screen:
    return entry.kb_group(lang, total=3, body=_FACTS(lang), page=0, pages=1)


_DM_FACTS: Final = Tr(
    "<b>Общее</b>\n"
    "• Созвон по вторникам в 19:00\n"
    "  <i>обновлено 03.11.2026, Аня</i>\n"
    "<b>Дача</b>\n"
    "• Пароль от вайфая на даче — у Бори\n"
    "  <i>обновлено 01.11.2026, Боря</i>",
    "<b>General</b>\n"
    "• The call is on Tuesdays at 19:00\n"
    "  <i>updated 03.11.2026, Ann</i>\n"
    "<b>Cottage</b>\n"
    "• Bob has the cottage wifi password\n"
    "  <i>updated 01.11.2026, Bob</i>",
)


@sample("kb.dm", section="В чатах", title="/kb в личке", nav="card")
def kb_dm(lang: Lang) -> Screen:
    return entry.kb_dm(lang, total=7, body=_DM_FACTS(lang), page=1, pages=2)


_NAME: Final = Tr("Костя", "Kostya")


@sample("callme.usage", section="В чатах", title="/callme: имя задано", nav="group")
def callme_with_name(lang: Lang) -> Screen:
    return entry.callme_usage(lang, current=_NAME(lang))


@sample("callme.usage", section="В чатах", title="/callme: имени нет", nav="group")
def callme_without_name(lang: Lang) -> Screen:
    return entry.callme_usage(lang, current=None)


@sample(
    "callme.usage",
    section="В чатах",
    title="/callme ответом на чужое сообщение",
    nav="group",
    user_data=(fake.PERSON_OTHER,),
)
def callme_about_other(lang: Lang) -> Screen:
    return entry.callme_usage(lang, current=_NAME(lang), other=fake.PERSON_OTHER)


@sample(
    "callme.set_account",
    section="В чатах",
    title="Имя как в аккаунте",
    nav="group",
    user_data=(fake.PERSON,),
    exempt={"R5": "the same words go out as a toast to whoever pressed; a toast has no title"},
)
def callme_set_account(lang: Lang) -> Screen:
    return entry.callme_set_account(lang, name=fake.PERSON)


@sample(
    "callme.other",
    section="В чатах",
    title="Админ задал имя другому",
    nav="group",
    user_data=(fake.PERSON_OTHER,),
    exempt={"R5": _NOT_A_SCREEN},
)
def callme_other(lang: Lang) -> Screen:
    return entry.callme_other(lang, who=fake.PERSON_OTHER, name=_NAME(lang))


@sample(
    "callme.cleared_other",
    section="В чатах",
    title="Админ убрал имя другому",
    nav="group",
    user_data=(fake.PERSON_OTHER,),
    exempt={"R5": _NOT_A_SCREEN},
)
def callme_cleared_other(lang: Lang) -> Screen:
    return entry.callme_cleared_other(lang, who=fake.PERSON_OTHER)


@sample(
    "callme.nothing_other",
    section="В чатах",
    title="У другого имени нет",
    nav="group",
    exempt={"R5": _NOT_A_SCREEN},
)
def callme_nothing_other(lang: Lang) -> Screen:
    return entry.callme_nothing_other(lang)
