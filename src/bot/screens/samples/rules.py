"""Rules samples: a chat's list, a rule's card, the builder, and what a fired rule sends.

One sample per state that changes the buttons: the list on, off and inherited,
empty, and paged under a priority mode; the card on, paused, just created while
the chat's rules are off, and with its JSON open; every builder step; and the
two typed-answer errors (a builder step, the JSON prompt).
"""

from __future__ import annotations

from functools import partial
from typing import Final

from src.bot.screens import rules as r
from src.bot.screens.i18n import Lang, Tr
from src.bot.screens.model import Screen
from src.bot.screens.registry import sample
from src.bot.screens.routes import cb
from src.bot.screens.samples import fake

_CID: Final = fake.CHAT_ID
_TITLE: Final = fake.CHAT_TITLE
WORDS: Final = ("реклама", "купи")
"""The builder's words: the rule a reader is asked to make, so no list sample holds it."""
WORDS_NAME: Final = ", ".join(WORDS)
LIST_WORDS: Final = ("спойлер", "финал")
"""A keyword rule the chat already has, unlike the one the builder makes."""
LIST_NAME: Final = ", ".join(LIST_WORDS)
LIST_WARN: Final = "Давайте без спойлеров"
FLOOD_NAME: Final = "Много стикеров"
WARN_TEXT: Final = "Давайте без спама"
EXCERPT: Final = "тут реклама и ещё текст"
SPAM_NAME: Final = "Сообщения: 20 за минуту"
LONG_NAME: Final = "Следит за спойлерами к сериалам и пишет админу сразу"

_ON: Final = r.ChatRules(enabled=True, common=False)
_OFF_COMMON: Final = r.ChatRules(enabled=False, common=True)
_WEIGHTED: Final = r.ChatRules(enabled=True, common=False, mode="highest_weight")

_KEYWORDS: Final = r.RuleView(
    id=fake.RULE_ID,
    chat_id=_CID,
    rule_type="keyword_trigger",
    config={
        "name": LIST_NAME,
        "keywords": list(LIST_WORDS),
        "match_type": "contains",
        "action": "warn_user",
        "warning_message": LIST_WARN,
    },
    trigger_count=4,
)
_FLOOD: Final = r.RuleView(
    id=fake.RULE_ID + 1,
    chat_id=_CID,
    rule_type="sticker_flood",
    config={
        "name": FLOOD_NAME,
        "max_stickers_per_interval": 10,
        "interval_seconds": 60,
        "action": "notify_admin",
    },
    enabled=False,
)
_SPAM: Final = r.RuleView(
    id=fake.RULE_ID + 2,
    chat_id=_CID,
    rule_type="spam_detect",
    config={
        "name": SPAM_NAME,
        "max_messages": 20,
        "time_window_seconds": 60,
        "max_chars_per_message": 0,
        "action": "set_reaction",
        "emoji": "🤡",
    },
    weight=3,
    mandatory=True,
)
_LONG: Final = r.RuleView(
    id=fake.RULE_ID + 3,
    chat_id=_CID,
    rule_type="keyword_trigger",
    config={"name": LONG_NAME, "keywords": list(LIST_WORDS), "action": "notify_admin"},
    weight=2,
)

_USER_DATA: Final = (
    _TITLE,
    WORDS_NAME,
    *WORDS,
    LIST_NAME,
    *LIST_WORDS,
    LIST_WARN,
    FLOOD_NAME,
    WARN_TEXT,
    EXCERPT,
    SPAM_NAME,
    LONG_NAME,
    *(r.row_name(_LONG, _WEIGHTED, lang) for lang in ("ru", "en")),
    fake.PERSON,
    fake.PERSON_OTHER,
)
"""Every invented string that stands for a chat's own data (titles, words, names),
the long name also as the list's row cuts it."""


def _list(
    lang: Lang, chat: r.ChatRules, *rules: r.RuleView, page: int = 0, pages: int = 1
) -> Screen:
    return r.rules_screen(
        lang, chat_id=_CID, title=_TITLE, chat=chat, rules=rules, page=page, pages=pages
    )


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

_panel = partial(sample, section="Чаты", nav="panel", user_data=_USER_DATA)


@_panel("rules", title="Правила: включены")
def rules_on(lang: Lang) -> Screen:
    return _list(lang, _ON, _KEYWORDS, _FLOOD)


@_panel("rules", title="Правила: выключены, общее значение")
def rules_off(lang: Lang) -> Screen:
    return _list(lang, _OFF_COMMON, _KEYWORDS, _FLOOD)


@_panel("rules", title="Правила: пусто")
def rules_empty(lang: Lang) -> Screen:
    return _list(lang, _ON)


@_panel("rules", title="Правила: режим по приоритету, страницы")
def rules_weighted(lang: Lang) -> Screen:
    return _list(lang, _WEIGHTED, _SPAM, _LONG, _KEYWORDS, page=0, pages=2)


@_panel("rules", title="Правила: после удаления")
def rules_deleted(lang: Lang) -> Screen:
    return r.rules_screen(
        lang,
        chat_id=_CID,
        title=_TITLE,
        chat=_ON,
        rules=(_FLOOD,),
        notice=Tr("Правило удалено", "Rule deleted")(lang),
    )


@_panel("rule.gone", title="Правила: правила уже нет")
def rule_gone(lang: Lang) -> Screen:
    return r.gone_screen(lang, chat_id=_CID, title=_TITLE, chat=_ON, rules=(_KEYWORDS, _FLOOD))


@_panel("rules", title="Правила: все выключены с карточки")
def rules_switched_off(lang: Lang) -> Screen:
    return r.rules_screen(
        lang,
        chat_id=_CID,
        title=_TITLE,
        chat=r.ChatRules(enabled=False, common=False),
        rules=(_KEYWORDS, _FLOOD),
        notice=r.RULES_OFF(lang),
    )


@_panel("rule.gone", title="Правила: включены, а правила уже нет")
def rule_gone_switched(lang: Lang) -> Screen:
    return r.gone_screen(
        lang,
        chat_id=_CID,
        title=_TITLE,
        chat=_ON,
        rules=(_KEYWORDS, _FLOOD),
        notice=r.RULES_ON(lang),
    )


# ---------------------------------------------------------------------------
# rule, rule.del_ask
# ---------------------------------------------------------------------------


@_panel("rule", title="Правило: работает")
def rule_on(lang: Lang) -> Screen:
    return r.rule_screen(lang, _KEYWORDS, chat=_ON)


@_panel("rule", title="Правило: на паузе")
def rule_paused(lang: Lang) -> Screen:
    return r.rule_screen(lang, _FLOOD, chat=_ON)


@_panel("rule", title="Правило: создано, правила чата выключены")
def rule_created_off(lang: Lang) -> Screen:
    return r.rule_screen(
        lang, _KEYWORDS, chat=_OFF_COMMON, notice=Tr("Правило создано", "Rule created")(lang)
    )


@_panel("rule", title="Правило: режим по приоритету, JSON открыт")
def rule_json(lang: Lang) -> Screen:
    return r.rule_screen(lang, _SPAM, chat=_WEIGHTED, show_json=True)


@_panel("rule", title="Правило: правила чата включены с карточки")
def rule_switched_on(lang: Lang) -> Screen:
    return r.rule_screen(lang, _KEYWORDS, chat=_ON, notice=r.RULES_ON(lang))


@_panel(
    "rule.del_ask",
    title="Удалить правило?",
    exempt={
        "R4": "the way back from a delete question says what it does to the rule "
        "(«Не удалять»); it still goes to the rule's card"
    },
)
def rule_del_ask(lang: Lang) -> Screen:
    return r.delete_ask_screen(lang, _KEYWORDS)


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------

_prompt = partial(sample, section="Чаты", nav="prompt", user_data=_USER_DATA)
_ERROR_EXEMPT: Final = {
    "R5": "an error starts with a letter, not a title: it is not a screen, "
    "and the prompt above it stays armed"
}


@sample("rule.type", title="Новое правило: тип", section="Чаты", nav="panel")
def rule_type(lang: Lang) -> Screen:
    return r.type_screen(lang, _CID)


@_prompt("rule.words", title="Новое правило: слова")
def rule_words(lang: Lang) -> Screen:
    return r.words_screen(lang, _CID)


@_prompt("rule.words", title="Новое правило: слов слишком много", exempt=_ERROR_EXEMPT)
def rule_words_bad(lang: Lang) -> Screen:
    return r.error_screen(
        "rule.words",
        r.WORDS_TOO_MANY(lang).format(n=r.MAX_WORDS),
        cb("ar_list:", lang, _CID, 0),
        lang,
    )


@_prompt("rule.match", title="Новое правило: как искать слова")
def rule_match(lang: Lang) -> Screen:
    return r.match_screen(lang, _CID, WORDS)


_PEOPLE: Final = (
    r.Person(fake.USER_ID, fake.PERSON, fake.USERNAME.lstrip("@")),
    r.Person(fake.USER_ID_OTHER, fake.PERSON_OTHER),
    r.Person(fake.USER_ID + 2, None, "anon_user"),
    r.Person(fake.USER_ID + 3),
)


_FIND_EXEMPT: Final = {
    "R9": "«Найти в Telegram» names the app whose own search the button opens; "
    "no Russian word says which search it is",
}


@_prompt("rule.who", title="Новое правило: чьи сообщения", exempt=_FIND_EXEMPT)
def rule_who(lang: Lang) -> Screen:
    return r.who_screen(lang, _CID, _PEOPLE, page=0, pages=3)


@_prompt("rule.who", title="Новое правило: в чате никто не писал", exempt=_FIND_EXEMPT)
def rule_who_empty(lang: Lang) -> Screen:
    return r.who_screen(lang, _CID, ())


@_prompt("rule.who", title="Новое правило: приложение без поиска")
def rule_who_no_find(lang: Lang) -> Screen:
    return r.who_screen(lang, _CID, _PEOPLE, find=False)


@_prompt("rule.who_find", title="Новое правило: поиск в Telegram", exempt=_FIND_EXEMPT)
def rule_who_find(lang: Lang) -> Screen:
    return r.who_find_screen(lang)


@_prompt("rule.flood", title="Новое правило: сколько стикеров")
def rule_flood(lang: Lang) -> Screen:
    return r.count_screen(lang, _CID, "f")


@_prompt("rule.spam", title="Новое правило: сколько сообщений")
def rule_spam(lang: Lang) -> Screen:
    return r.count_screen(lang, _CID, "s")


@_prompt("rule.action", title="Новое правило: что делать")
def rule_action(lang: Lang) -> Screen:
    return r.action_screen(lang, _CID, WORDS_NAME)


@_prompt("rule.text", title="Новое правило: текст предупреждения")
def rule_text_warn(lang: Lang) -> Screen:
    return r.text_screen(lang, _CID, WORDS_NAME, action="warn_user")


@_prompt("rule.text", title="Новое правило: свой ответ")
def rule_text_custom(lang: Lang) -> Screen:
    return r.text_screen(lang, _CID, WORDS_NAME, action="custom_response")


@_prompt("rule.text", title="Новое правило: ответ не поместится с именем", exempt=_ERROR_EXEMPT)
def rule_text_too_long(lang: Lang) -> Screen:
    return r.error_screen(
        "rule.text",
        r.text_too_long(lang, 62, filled=True),
        cb("ar_list:", lang, _CID, 0),
        lang,
    )


@_prompt("rule.react", title="Новое правило: реакция")
def rule_react(lang: Lang) -> Screen:
    return r.react_screen(lang, _CID, WORDS_NAME)


@_prompt("rule.react_in", title="Новое правило: своя реакция")
def rule_react_in(lang: Lang) -> Screen:
    return r.react_in_screen(lang, _CID, WORDS_NAME)


@_prompt("rule.cfg", title="Новое правило: JSON")
def rule_cfg(lang: Lang) -> Screen:
    return r.cfg_screen(lang, _CID)


@_prompt("rule.cfg_bad", title="Новое правило: это не JSON", exempt=_ERROR_EXEMPT)
def rule_cfg_bad(lang: Lang) -> Screen:
    return r.error_screen("rule.cfg_bad", r.CFG_NOT_JSON(lang), cb("ar_list:", lang, _CID, 0), lang)


# ---------------------------------------------------------------------------
# What a fired rule sends
# ---------------------------------------------------------------------------


@sample(
    "card.rule",
    section="Карточки от бота",
    title="Сработало правило",
    nav="card",
    user_data=_USER_DATA,
)
def card_rule(lang: Lang) -> Screen:
    return r.rule_card(
        lang,
        rule_id=fake.RULE_ID,
        chat_id=_CID,
        name=WORDS_NAME,
        chat_title=_TITLE,
        first_name=fake.PERSON,
        username=fake.USERNAME.lstrip("@"),
        match_detail="contains:реклама",
        excerpt=EXCERPT,
    )


@sample(
    "rules.warn",
    section="В чатах",
    title="Предупреждение по правилу",
    nav="group",
    exempt={"R5": "posted in the chat as plain text, like a person's remark: no title"},
)
def rules_warn(lang: Lang) -> Screen:
    return r.warn_screen(lang)
