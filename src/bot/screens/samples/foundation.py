"""The foundation's samples: the stale fallback, and ten design screens copied as data.

``stale`` is registered: it is the one screen the foundation ships. It shows
TODAY's hub (``hub.build_hub``) under the stale line, so it carries the
exemptions today's hub needs until the hub area replaces it.

`DESIGN_COPIES` is NOT registered. It is the redesign's target look for ten
screens, rebuilt as `Screen` data from invented values, and it exists for one
assertion in the rules test: the lint passes them. A rule that flags the
design itself is wrong, not the design ("does not wrongly refuse"). Keeping
them out of the registry keeps them out of the dump, where they would sit
next to the real screens the areas build.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

from src.bot.screens.hub import build_hub
from src.bot.screens.i18n import Lang, Tr
from src.bot.screens.model import Button, Screen
from src.bot.screens.registry import Sample, make_sample, sample
from src.bot.screens.render import back, pager
from src.bot.screens.routes import cb
from src.bot.screens.samples import fake
from src.bot.screens.stale import STALE_NOTICE

TODAYS_HUB: Final = "today's hub is kept until the hub area replaces it (E1/E2)"

# ---------------------------------------------------------------------------
# The stale fallback (registered)
# ---------------------------------------------------------------------------


@sample(
    "stale",
    section="В чатах",
    title="Устаревшая кнопка",
    nav="root",
    exempt={
        "R2": f"nine rows: {TODAYS_HUB}",
        "R5": f"title has no emoji: {TODAYS_HUB}",
        "R9": f"'Whitelist' and 'Language / Язык' are mixed-language: {TODAYS_HUB}",
        "R10": f"'Глобальные настройки' truncates on a phone: {TODAYS_HUB}",
    },
)
def stale(lang: Lang) -> Screen:
    return replace(build_hub(lang, notice_raw=STALE_NOTICE(lang)), id="stale")


# ---------------------------------------------------------------------------
# Ten design screens (unregistered; see the module docstring)
# ---------------------------------------------------------------------------

NOT_YET_ROUTED: Final = "a new prefix; its area lands the handler, until then the stale catch-all"
"""R7 exemption for a design copy that uses a prefix no live handler takes yet."""

_CID = fake.CHAT_ID
_CLOSE = Tr("✖️ Закрыть", "✖️ Close")


def _hub(lang: Lang) -> Screen:
    return Screen(
        id="hub",
        text=Tr(
            "🛠 <b>Панель</b>\n\n💚 Всё работает · 💰 расход за сутки $0.04",
            "🛠 <b>Panel</b>\n\n💚 All good · 💰 spent in 24 h $0.04",
        )(lang),
        rows=(
            (
                Button(
                    Tr("🗂 Чаты · 1 заявка", "🗂 Chats · 1 request")(lang), cb("adm_pnl:", lang, 0)
                ),
            ),
            (
                Button(
                    Tr("📦 Паки стикеров", "📦 Sticker packs")(lang), cb("adm_stk_sets:", lang, 0)
                ),
            ),
            (Button(Tr("📊 Состояние", "📊 Status")(lang), cb("adm_health:", lang)),),
            (
                Button(
                    Tr("⚙️ Общие настройки", "⚙️ General settings")(lang), cb("adm_settings:", lang)
                ),
            ),
            (Button(_CLOSE(lang), cb("adm_close:", lang), kind="exit"),),
        ),
    )


def _chats(lang: Lang) -> Screen:
    nav = pager(0, 2, lambda page: cb("adm_pnl:", lang, page))
    assert nav is not None
    return Screen(
        id="chats",
        text=Tr(
            "🗂 <b>Чаты</b>\n\nПодключено: 2. Рядом с названием — сообщений за сутки.",
            "🗂 <b>Chats</b>\n\nConnected: 2. Next to each name: messages in 24 h.",
        )(lang),
        rows=(
            (
                Button(
                    Tr("🙏 Заявки · 1", "🙏 Requests · 1")(lang), cb("adm_wl_pending:", lang, 0)
                ),
                Button(
                    Tr("🚫 Отклонённые · 2", "🚫 Rejected · 2")(lang),
                    cb("adm_wl_rejected:", lang, 0),
                ),
            ),
            (Button(f"{fake.CHAT_TITLE} · 148", cb("adm_pnl_menu:", lang, _CID)),),
            (
                Button(
                    f"{fake.CHAT_TITLE_OTHER} · 12", cb("adm_pnl_menu:", lang, fake.CHAT_ID_OTHER)
                ),
            ),
            nav,
            (back("hub", cb("adm_menu:", lang), lang),),
        ),
    )


def _requests(lang: Lang) -> Screen:
    nav = pager(0, 2, lambda page: cb("adm_wl_pending:", lang, page))
    assert nav is not None
    return Screen(
        id="requests",
        text=Tr(
            "🙏 <b>Заявка 1 из 2</b>\n\n<b>{t}</b> <code>{c}</code>\n👤 {u}\n💬 Привет, можно к вам?",
            "🙏 <b>Request 1 of 2</b>\n\n<b>{t}</b> <code>{c}</code>\n👤 {u}\n💬 Привет, можно к вам?",
        )(lang).format(t=fake.CHAT_TITLE_NEW, c=str(fake.CHAT_ID_NEW)[4:], u=fake.USERNAME),
        rows=(
            (
                Button(
                    Tr("👍 Одобрить", "👍 Approve")(lang),
                    cb("adm_wl_apr:", lang, fake.ATTEMPT_ID, 0),
                    style="success",
                ),
                Button(
                    Tr("❌ Отклонить", "❌ Reject")(lang),
                    cb("adm_wl_rej:", lang, fake.ATTEMPT_ID, 0),
                ),
            ),
            nav,
            (back("chats", cb("adm_pnl:", lang, 0), lang),),
        ),
    )


def _chat(lang: Lang) -> Screen:
    def section(label: Tr, token: str) -> Button:
        return Button(label(lang), cb("adm_pnl_grp:", lang, _CID, token))

    return Screen(
        id="chat",
        text=Tr(
            "🗂 <b>{t}</b> <code>100200300</code>\n\n"
            "Бот включён. Отвечает, когда зовут · помнит диалоги · расшифровывает голосовые",
            "🗂 <b>{t}</b> <code>100200300</code>\n\n"
            "The bot is on. Answers when called · remembers conversations · transcribes voice",
        )(lang).format(t=fake.CHAT_TITLE),
        rows=(
            (
                section(Tr("🗣 Поведение", "🗣 Behaviour"), "behavior"),
                section(Tr("🧠 Память", "🧠 Memory"), "memory"),
            ),
            (
                section(Tr("🎙 Голос и фото", "🎙 Voice and photos"), "voice"),
                section(Tr("🎨 Стикеры", "🎨 Stickers"), "stickers"),
            ),
            (
                section(Tr("😀 Реакции", "😀 Reactions"), "reactions"),
                section(Tr("👋 Сообщество", "👋 Community"), "community"),
            ),
            (
                Button(Tr("📏 Правила · 2", "📏 Rules · 2")(lang), cb("ar_list:", lang, _CID, 0)),
                Button(
                    Tr("🔌 Отключить", "🔌 Disconnect")(lang), cb("adm_wl_rm_ask:", lang, _CID, 0)
                ),
            ),
            (back("chats", cb("adm_pnl:", lang, 0), lang),),
        ),
    )


def _memory(lang: Lang) -> Screen:
    def toggle(on: bool, label: Tr, code: str) -> Button:
        return Button(f"{'✅' if on else '⚪'} {label(lang)}", cb("adm_pnl_tgl:", lang, _CID, code))

    return Screen(
        id="memory",
        text=Tr(
            "🧠 <b>Память</b> · {t}\n\nНажатие включает или выключает.",
            "🧠 <b>Memory</b> · {t}\n\nA tap turns a setting on or off.",
        )(lang).format(t=fake.CHAT_TITLE),
        rows=(
            (toggle(True, Tr("Сохранять сообщения", "Save messages"), "sm"),),
            (toggle(False, Tr("Искать по архиву чата", "Search the chat archive"), "ch"),),
            (toggle(True, Tr("Помнить свои диалоги", "Remember its conversations"), "rag"),),
            (
                toggle(True, Tr("База знаний", "Knowledge base"), "kb"),
                Button(
                    Tr("👥 Организаторы · 2", "👥 Organizers · 2")(lang),
                    cb("adm_kb_orgs:", lang, _CID, 0),
                ),
            ),
            (back("chat", cb("adm_pnl_menu:", lang, _CID), lang),),
        ),
    )


def _orgs(lang: Lang) -> Screen:
    return Screen(
        id="orgs",
        text=Tr(
            "👥 <b>Организаторы</b> · {t}\n\n"
            "Могут сохранять факты командой /remember. Нажатие на имя убирает человека.",
            "👥 <b>Organizers</b> · {t}\n\n"
            "They can save facts with /remember. Tap a name to remove that person.",
        )(lang).format(t=fake.CHAT_TITLE),
        rows=(
            (Button(f"✖️ {fake.PERSON}", cb("adm_kb_org_rm:", lang, _CID, fake.USER_ID)),),
            (
                Button(
                    f"✖️ {fake.PERSON_OTHER}", cb("adm_kb_org_rm:", lang, _CID, fake.USER_ID_OTHER)
                ),
            ),
            (Button(Tr("➕ Добавить", "➕ Add")(lang), cb("adm_kb_org_list:", lang, _CID, 0)),),
            (back("memory", cb("adm_pnl_grp:", lang, _CID, "memory"), lang),),
        ),
    )


RULE_WORDS: Final = "реклама, купи"
RULE_NAME: Final = "Много стикеров"


def _rules(lang: Lang) -> Screen:
    return Screen(
        id="rules",
        text=Tr(
            "📏 <b>Правила</b> · {t}\n\n"
            "Нажмите правило, чтобы открыть. Верхняя кнопка ставит на паузу все сразу.",
            "📏 <b>Rules</b> · {t}\n\nTap a rule to open it. The top button pauses all of them at once.",
        )(lang).format(t=fake.CHAT_TITLE),
        rows=(
            (
                Button(
                    Tr("✅ Все правила включены", "✅ All rules on")(lang),
                    cb("ar_all:", lang, _CID),
                ),
            ),
            (
                Button(
                    f"📏 {RULE_WORDS} · {Tr('работает', 'on')(lang)}",
                    cb("ar_view:", lang, fake.RULE_ID, _CID),
                ),
            ),
            (
                Button(
                    f"⏸ {RULE_NAME} · {Tr('на паузе', 'paused')(lang)}",
                    cb("ar_view:", lang, fake.RULE_ID + 1, _CID),
                ),
            ),
            (
                Button(Tr("➕ Добавить", "➕ Add")(lang), cb("ar_add:", lang, _CID)),
                back("chat", cb("adm_pnl_menu:", lang, _CID), lang),
            ),
        ),
    )


STICKER_FACTS: Final = ("Кот в очках машет лапой", "радость", "мем «кот»", "приветствие, радость")


def _sticker_card(lang: Lang) -> Screen:
    description, emotion, character, when = STICKER_FACTS
    return Screen(
        id="stk.card",
        text=Tr(
            "😺 <b>{d}</b>\n\nЭмоция: {e}\nПерсонаж: {c}\nКогда уместен: {w}\n"
            "Использован: 12 раз, ботом 3\nОткровенность: 0.25 · пройдёт в чате с допуском 0.50\n\n"
            "<i>Ответьте на это сообщение текстом, чтобы уточнить описание.</i>",
            "😺 <b>{d}</b>\n\nEmotion: {e}\nCharacter: {c}\nFits: {w}\n"
            "Used: 12 times, 3 by the bot\nExplicitness: 0.25 · passes in a chat that allows 0.50\n\n"
            "<i>Reply to this message with text to refine the description.</i>",
        )(lang).format(d=description, e=emotion, c=character, w=when),
        rows=(
            (
                Button(
                    Tr("🔁 Разобрать заново", "🔁 Re-analyse")(lang),
                    cb("adm_stk_reanalyze:", lang, fake.FUID),
                ),
                Button(
                    Tr("🧹 Очистить", "🧹 Clear")(lang), cb("adm_stk_clr_ask:", lang, fake.FUID)
                ),
            ),
            (
                Button(
                    Tr("🔞 Откровенность", "🔞 Explicitness")(lang),
                    cb("adm_stk_score:", lang, fake.FUID),
                ),
            ),
            (
                back(
                    "stk.set", cb("adm_stk_pk:", lang, fake.PACK_REF, 0), lang, name=fake.PACK_TITLE
                ),
            ),
        ),
    )


def _status(lang: Lang) -> Screen:
    return Screen(
        id="status",
        text=Tr(
            "📊 <b>Состояние</b> · 14:00\n\n💚 <b>Всё работает</b>\n✓ База данных\n"
            "💬 Сообщений за 30 минут: 120\n🛟 Запасной ИИ за 15 минут: 0",
            "📊 <b>Status</b> · 14:00\n\n💚 <b>All good</b>\n✓ Database\n"
            "💬 Messages in 30 minutes: 120\n🛟 Fallback AI in 15 minutes: 0",
        )(lang),
        rows=(
            (Button(Tr("🔁 Обновить", "🔁 Refresh")(lang), cb("adm_health:", lang)),),
            (
                Button(Tr("📈 Статистика", "📈 Statistics")(lang), cb("adm_stats:", lang, "24h")),
                Button(Tr("💰 Расходы", "💰 Costs")(lang), cb("adm_costs:", lang, "24h")),
            ),
            (back("hub", cb("adm_menu:", lang), lang),),
        ),
    )


def _settings(lang: Lang) -> Screen:
    return Screen(
        id="settings",
        text=Tr(
            "⚙️ <b>Общие настройки</b>\n\nТо, что не относится к одному чату.",
            "⚙️ <b>General settings</b>\n\nWhat does not belong to a single chat.",
        )(lang),
        rows=(
            (Button(Tr("🌍 Для всех чатов", "🌍 All chats")(lang), cb("adm_defs:", lang, 0)),),
            (Button(Tr("🔔 Уведомления", "🔔 Notifications")(lang), cb("adm_notif:", lang)),),
            (Button(Tr("🌐 Язык: Русский", "🌐 Language: English")(lang), cb("adm_lang:", lang)),),
            (back("hub", cb("adm_menu:", lang), lang),),
        ),
    )


DESIGN_COPIES: Final[tuple[Sample, ...]] = (
    make_sample(
        "hub",
        _hub,
        section="Панель",
        title="Панель",
        nav="root",
        exempt={"R7": f"⚙️ Общие настройки (adm_settings:) — {NOT_YET_ROUTED}"},
    ),
    make_sample(
        "chats",
        _chats,
        section="Чаты",
        title="Список чатов",
        nav="panel",
        user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER),
    ),
    make_sample(
        "requests",
        _requests,
        section="Чаты",
        title="Заявка",
        nav="panel",
        user_data=(fake.CHAT_TITLE_NEW, fake.USERNAME, "Привет, можно к вам?"),
    ),
    make_sample(
        "chat", _chat, section="Чаты", title="Чат", nav="panel", user_data=(fake.CHAT_TITLE,)
    ),
    make_sample(
        "memory", _memory, section="Чаты", title="Память", nav="panel", user_data=(fake.CHAT_TITLE,)
    ),
    make_sample(
        "orgs",
        _orgs,
        section="Чаты",
        title="Организаторы",
        nav="panel",
        user_data=(fake.CHAT_TITLE, fake.PERSON, fake.PERSON_OTHER),
    ),
    make_sample(
        "rules",
        _rules,
        section="Чаты",
        title="Правила",
        nav="panel",
        user_data=(fake.CHAT_TITLE, RULE_WORDS, RULE_NAME),
        exempt={"R7": f"✅ Все правила (ar_all:) — {NOT_YET_ROUTED}"},
    ),
    make_sample(
        "stk.card",
        _sticker_card,
        section="Стикеры",
        title="Карточка стикера",
        nav="panel",
        user_data=(*STICKER_FACTS, fake.PACK_TITLE),
        exempt={"R7": f"adm_stk_score: and adm_stk_pk: — {NOT_YET_ROUTED}"},
    ),
    make_sample("status", _status, section="Состояние", title="Состояние", nav="panel"),
    make_sample("settings", _settings, section="Настройки", title="Общие настройки", nav="panel"),
)
