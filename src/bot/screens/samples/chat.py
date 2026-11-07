"""Samples for the chat list, one chat's root and its sections.

One sample per state that changes which rows a screen shows (a requests row or
none, community mode on or off, reactions on or off, the bot an admin or not)
or what its text says (the «Берутся из общих настроек» line or none, a preset or a
custom number).
Every value is invented (``fake.py``).
"""

from __future__ import annotations

from dataclasses import replace
from html import escape
from typing import Any, Final

from src.bot.screens import chat as screens
from src.bot.screens.chat import ChatView, config_values, fit_label
from src.bot.screens.hub import (
    TURNED_OFF,
    TURNED_ON,
    UNDO_BOT_GONE,
    UNDO_REJECTED,
    UNDO_UNCHECKED,
)
from src.bot.screens.i18n import Lang
from src.bot.screens.model import Screen
from src.bot.screens.orgs import UNDO
from src.bot.screens.registry import sample
from src.bot.screens.samples import fake
from src.models.chat_config import ChatConfig

LONG_TITLE: Final = "Очень длинное название тестового чата, которое не помещается"
LONG_TITLE_CUTS: Final = (
    fit_label(LONG_TITLE, " · 3").removesuffix("… · 3"),
    fit_label(LONG_TITLE, "").removesuffix("…"),
)
"""How the long title reads once cut to fit a button: still a chat's own words (R9)."""
WORDS: Final = ("бот", "помощник")
PROMPT: Final = "Отвечай коротко и по делу, без лишних вступлений и извинений в начале ответа"
_SECTION: Final = "Чаты"
_TITLES: Final = (fake.CHAT_TITLE,)
_BEHAVIOR_DATA: Final = (fake.CHAT_TITLE, *WORDS, PROMPT[: screens.PROMPT_PREVIEW_CHARS])

_OWN_ROW: Final[dict[str, Any]] = {
    "chunks_enabled": True,
    "link_comments_enabled": False,
    "relevancy_gate_enabled": True,
    "transcribe_expandable": False,
    "kb_enabled": True,
    "reactions_enabled": True,
    "reactions_history_enabled": True,
    "community_enabled": True,
    "greet_on_first_message": True,
    "welcome_back_enabled": True,
    "farewell_public": False,
    "farewell_dm": True,
    "returner_joke_enabled": False,
    "advice_watch_enabled": False,
    "sticker_reply_to_sticker_enabled": True,
    "image_comment_sticker_enabled": False,
    "tolerance_level": 0.6,
}
"""A chat that set every non-legacy toggle itself: no «Берутся из общих настроек» line."""


def _view(
    *,
    row: dict[str, Any] | None = None,
    rules: int = 0,
    organizers: int = 0,
    bot_is_admin: bool | None = None,
    **overrides: Any,
) -> ChatView:
    own = dict(row or {})
    values = config_values(
        ChatConfig(chat_id=fake.CHAT_ID, enabled=True, trigger_words=WORDS),
        **{**own, **overrides},
    )
    return ChatView(
        chat_id=fake.CHAT_ID,
        title=fake.CHAT_TITLE,
        chat_type="supergroup",
        values=values,
        row=own,
        rules=rules,
        organizers=organizers,
        bot_is_admin=bot_is_admin,
    )


# ── the chat list ─────────────────────────────────────────────────────────


def _chat_rows(*titles_counts: tuple[int, str, int]) -> list[dict[str, Any]]:
    return [
        {"chat_id": cid, "chat_title": title, "chat_type": "supergroup", "message_count_24h": n}
        for cid, title, n in titles_counts
    ]


@sample(
    "chats",
    section=_SECTION,
    title="Запрос доступа и отклонённые, две страницы",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER, *LONG_TITLE_CUTS),
)
def chats_with_requests(lang: Lang) -> Screen:
    rows = _chat_rows(
        (fake.CHAT_ID, fake.CHAT_TITLE, 148),
        (fake.CHAT_ID_OTHER, fake.CHAT_TITLE_OTHER, 12),
        (fake.CHAT_ID_SHORT, LONG_TITLE, 3),
    )
    return screens.build_chats(lang, rows, total=5, page=0, pages=2, requests=1, rejected=2)


@sample(
    "chats",
    section=_SECTION,
    title="Без запросов доступа",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER, fake.CHAT_TITLE_NEW),
)
def chats_plain(lang: Lang) -> Screen:
    rows = _chat_rows(
        (fake.CHAT_ID, fake.CHAT_TITLE, 148),
        (fake.CHAT_ID_OTHER, fake.CHAT_TITLE_OTHER, 12),
        (fake.CHAT_ID_NEW, fake.CHAT_TITLE_NEW, 0),
    )
    return screens.build_chats(lang, rows, total=3, page=0, pages=1)


@sample(
    "chats",
    section=_SECTION,
    title="Только отклонённые, последняя страница",
    nav="panel",
    user_data=(fake.CHAT_TITLE_NEW,),
)
def chats_last_page_rejected_only(lang: Lang) -> Screen:
    rows = _chat_rows((fake.CHAT_ID_NEW, fake.CHAT_TITLE_NEW, 0))
    return screens.build_chats(lang, rows, total=5, page=1, pages=2, rejected=1)


@sample(
    "chats",
    section=_SECTION,
    title="Бот отключён в чате: «↩️ Вернуть», запрос доступа и отклонённые",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER, fake.CHAT_TITLE_NEW),
)
def chats_after_turn_off(lang: Lang) -> Screen:
    """What `handle_wl_remove` shows: four chats left on, the fifth just turned off.

    The undo row takes one chat's place, so with a requests row the page holds two.
    Four chats are built and the page size cuts them, as the handler's fetch
    does: a page size that grew back would show here as a row too many.
    """
    per_page = screens.chats_per_page(1, 2, undo=True)
    rows = _chat_rows(
        (fake.CHAT_ID, fake.CHAT_TITLE, 148),
        (fake.CHAT_ID_OTHER, fake.CHAT_TITLE_OTHER, 12),
        (fake.CHAT_ID_SHORT, fake.CHAT_TITLE_EN, 7),
        (fake.CHAT_ID_SHORT - 1, fake.CHAT_TITLE + " 2", 0),
    )[:per_page]
    screen = screens.build_chats(
        lang, rows, total=4, page=0, pages=2, requests=1, rejected=2, undo=fake.CHAT_ID_NEW
    )
    return replace(screen, notice=TURNED_OFF(lang).format(t=fake.CHAT_TITLE_NEW))


@sample(
    "chats",
    section=_SECTION,
    title="Бот снова работает в чате",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER, fake.CHAT_TITLE_NEW),
)
def chats_after_undo(lang: Lang) -> Screen:
    """What `handle_wl_on` shows: the chat is back in the list and nothing offers to undo."""
    rows = _chat_rows(
        (fake.CHAT_ID, fake.CHAT_TITLE, 148),
        (fake.CHAT_ID_OTHER, fake.CHAT_TITLE_OTHER, 12),
        (fake.CHAT_ID_NEW, fake.CHAT_TITLE_NEW, 0),
    )
    screen = screens.build_chats(lang, rows, total=5, page=0, pages=2, requests=1, rejected=2)
    return replace(screen, notice=TURNED_ON(lang).format(t=fake.CHAT_TITLE_NEW))


def _late_undo(lang: Lang, notice_raw: str, *, offer_again: bool = False) -> Screen:
    """What `handle_wl_on` shows when «↩️ Вернуть» is tapped after the chat changed."""
    rows = _chat_rows(
        (fake.CHAT_ID, fake.CHAT_TITLE, 148),
        (fake.CHAT_ID_OTHER, fake.CHAT_TITLE_OTHER, 12),
    )
    screen = screens.build_chats(
        lang,
        rows,
        total=4,
        page=0,
        pages=2,
        requests=1,
        rejected=2,
        undo=fake.CHAT_ID_NEW if offer_again else None,
        undo_rejected=offer_again,
    )
    return replace(screen, notice_raw=notice_raw)


@sample(
    "chats",
    section=_SECTION,
    title="«↩️ Вернуть» после отклонения: ничего не изменилось",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER, fake.CHAT_TITLE_NEW),
)
def chats_undo_after_reject(lang: Lang) -> Screen:
    return _late_undo(lang, UNDO_REJECTED(lang).format(t=escape(fake.CHAT_TITLE_NEW)))


@sample(
    "chats",
    section=_SECTION,
    title="«↩️ Вернуть», а бота уже нет в чате",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER, fake.CHAT_TITLE_NEW),
)
def chats_undo_bot_gone(lang: Lang) -> Screen:
    return _late_undo(lang, UNDO_BOT_GONE(lang).format(t=escape(fake.CHAT_TITLE_NEW)))


@sample(
    "chats",
    section=_SECTION,
    title="«↩️ Вернуть», а Telegram не ответил: кнопка остаётся",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER, fake.CHAT_TITLE_NEW),
)
def chats_undo_unchecked(lang: Lang) -> Screen:
    """The undo is offered again, with the mark of a rejection older than the turn-off."""
    notice_raw = UNDO_UNCHECKED(lang).format(t=escape(fake.CHAT_TITLE_NEW), undo=UNDO(lang))
    return _late_undo(lang, notice_raw, offer_again=True)


@sample("chats.empty", section=_SECTION, title="Ни одного чата", nav="panel")
def chats_empty(lang: Lang) -> Screen:
    return screens.build_chats(lang, [], total=0, page=0, pages=1)


@sample("chats.empty", section=_SECTION, title="Ни одного чата, есть запрос доступа", nav="panel")
def chats_empty_with_request(lang: Lang) -> Screen:
    return screens.build_chats(lang, [], total=0, page=0, pages=1, requests=1)


# ── /panel ────────────────────────────────────────────────────────────────


@sample("panel.noquery", section=_SECTION, title="/panel без слов", nav="panel")
def panel_noquery(lang: Lang) -> Screen:
    return screens.build_panel_noquery(lang)


@sample("panel.notfound", section=_SECTION, title="/panel ничего не нашёл", nav="panel")
def panel_notfound(lang: Lang) -> Screen:
    return screens.build_panel_notfound(lang)


@sample(
    "panel.many",
    section=_SECTION,
    title="Несколько подходящих",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER),
)
def panel_many(lang: Lang) -> Screen:
    rows = _chat_rows(
        (fake.CHAT_ID, fake.CHAT_TITLE, 0), (fake.CHAT_ID_OTHER, fake.CHAT_TITLE_OTHER, 0)
    )
    return screens.build_panel_many(lang, rows)


@sample(
    "panel.many",
    section=_SECTION,
    title="Больше, чем помещается",
    nav="panel",
    user_data=(fake.CHAT_TITLE, fake.CHAT_TITLE_OTHER, fake.CHAT_TITLE_NEW, *LONG_TITLE_CUTS),
)
def panel_many_cut(lang: Lang) -> Screen:
    rows = _chat_rows(
        (fake.CHAT_ID, fake.CHAT_TITLE, 0),
        (fake.CHAT_ID_OTHER, fake.CHAT_TITLE_OTHER, 0),
        (fake.CHAT_ID_NEW, fake.CHAT_TITLE_NEW, 0),
        (fake.CHAT_ID_SHORT, LONG_TITLE, 0),
        (fake.CHAT_ID_SHORT - 1, fake.CHAT_TITLE + " 2", 0),
    )
    return screens.build_panel_many(lang, rows)


# ── one chat ──────────────────────────────────────────────────────────────


@sample("chat", section=_SECTION, title="Бот включён", nav="panel", user_data=_TITLES)
def chat_on(lang: Lang) -> Screen:
    return screens.build_chat(_view(rules=2, organizers=3), lang)


@sample("chat", section=_SECTION, title="Бот выключен", nav="panel", user_data=_TITLES)
def chat_off(lang: Lang) -> Screen:
    return screens.build_chat(_view(enabled=False), lang)


@sample(
    "behavior",
    section=_SECTION,
    title="Инструкция задана, значения общие",
    nav="panel",
    user_data=_BEHAVIOR_DATA,
)
def behavior_common(lang: Lang) -> Screen:
    return screens.build_behavior(_view(system_prompt=PROMPT), lang)


@sample(
    "behavior",
    section=_SECTION,
    title="Не встревает, значения свои",
    nav="panel",
    user_data=_BEHAVIOR_DATA,
)
def behavior_own(lang: Lang) -> Screen:
    return screens.build_behavior(_view(row=_OWN_ROW, random_response_chance=0.0), lang)


@sample("memory", section=_SECTION, title="Значения общие", nav="panel", user_data=_TITLES)
def memory_common(lang: Lang) -> Screen:
    return screens.build_memory(_view(organizers=3), lang)


@sample(
    "memory", section=_SECTION, title="Сообщения не сохраняются", nav="panel", user_data=_TITLES
)
def memory_no_archive(lang: Lang) -> Screen:
    return screens.build_memory(_view(row=_OWN_ROW, save_messages=False), lang)


@sample("voice", section=_SECTION, title="Голос и фото", nav="panel", user_data=_TITLES)
def voice(lang: Lang) -> Screen:
    return screens.build_voice(_view(), lang)


@sample("chat.stickers", section=_SECTION, title="Значения общие", nav="panel", user_data=_TITLES)
def stickers_common(lang: Lang) -> Screen:
    return screens.build_stickers(_view(sticker_learning_enabled=True), lang)


@sample(
    "chat.stickers",
    section=_SECTION,
    title="Своё число откровенности",
    nav="panel",
    user_data=_TITLES,
)
def stickers_custom(lang: Lang) -> Screen:
    return screens.build_stickers(_view(row={**_OWN_ROW, "sticker_learning_enabled": True}), lang)


@sample(
    "chat.stickers",
    section=_SECTION,
    title="Учиться на стикерах выключено",
    nav="panel",
    user_data=_TITLES,
)
def stickers_no_learning(lang: Lang) -> Screen:
    """The default: learning off, so neither sticker answer fires whatever its switch says."""
    return screens.build_stickers(_view(), lang)


@sample("tol.prompt", section=_SECTION, title="Сейчас пресет", nav="panel")
def tolerance_preset(lang: Lang) -> Screen:
    return screens.build_tolerance_prompt(_view(), lang)


@sample("tol.prompt", section=_SECTION, title="Сейчас своё число", nav="panel")
def tolerance_custom(lang: Lang) -> Screen:
    return screens.build_tolerance_prompt(_view(row=_OWN_ROW), lang)


@sample("tol.in", section=_SECTION, title="Ввод числа", nav="prompt")
def tolerance_input(lang: Lang) -> Screen:
    return screens.build_tolerance_input(_view(), lang)


@sample("tol.invalid", section=_SECTION, title="Не число", nav="prompt")
def tolerance_invalid(lang: Lang) -> Screen:
    return screens.build_tolerance_input(_view(), lang, invalid=True)


# The editor shows the admin's instruction verbatim: user data, exempt from R8.
_SP_DATA: Final = (fake.CHAT_TITLE, PROMPT)
_SP_REV: Final = 2147483647
"""The widest draft version a button carries: a Telegram message id."""


@sample("sp.prompt", section=_SECTION, title="Своя инструкция", nav="prompt", user_data=_SP_DATA)
def sp_prompt_own(lang: Lang) -> Screen:
    return screens.build_sp_prompt(fake.CHAT_ID, fake.CHAT_TITLE, PROMPT, lang)


@sample("sp.prompt", section=_SECTION, title="Инструкции нет", nav="prompt", user_data=_TITLES)
def sp_prompt_none(lang: Lang) -> Screen:
    return screens.build_sp_prompt(fake.CHAT_ID, fake.CHAT_TITLE, "", lang)


@sample("sp.preview", section=_SECTION, title="Новая инструкция", nav="prompt", user_data=_SP_DATA)
def sp_preview(lang: Lang) -> Screen:
    return screens.build_sp_preview(fake.CHAT_ID, PROMPT, _SP_REV, lang)


@sample("sp.refused", section=_SECTION, title="Слишком длинно", nav="prompt")
def sp_too_long(lang: Lang) -> Screen:
    return screens.build_sp_refused(
        fake.CHAT_ID, _SP_REV, lang, length=screens.SP_MAX_CHARS + len(PROMPT)
    )


@sample("sp.refused", section=_SECTION, title="Черновик неполный", nav="prompt")
def sp_incomplete(lang: Lang) -> Screen:
    return screens.build_sp_refused(fake.CHAT_ID, _SP_REV, lang)


@sample("sp.redo", section=_SECTION, title="Черновик очищен", nav="prompt")
def sp_redo(lang: Lang) -> Screen:
    return screens.build_sp_redo(fake.CHAT_ID, lang)


@sample("reactions", section=_SECTION, title="Выключены", nav="panel", user_data=_TITLES)
def reactions_off(lang: Lang) -> Screen:
    return screens.build_reactions(_view(), lang)


@sample("reactions", section=_SECTION, title="Включены, бот админ", nav="panel", user_data=_TITLES)
def reactions_admin(lang: Lang) -> Screen:
    return screens.build_reactions(_view(row=_OWN_ROW, bot_is_admin=True), lang)


@sample(
    "reactions",
    section=_SECTION,
    title="Включены, бот не админ",
    nav="panel",
    user_data=_TITLES,
)
def reactions_not_admin(lang: Lang) -> Screen:
    return screens.build_reactions(_view(reactions_enabled=True, bot_is_admin=False), lang)


@sample("community", section=_SECTION, title="Режим выключен", nav="panel", user_data=_TITLES)
def community_off(lang: Lang) -> Screen:
    return screens.build_community(_view(), lang)


@sample("community", section=_SECTION, title="Режим включён", nav="panel", user_data=_TITLES)
def community_on(lang: Lang) -> Screen:
    return screens.build_community(_view(row=_OWN_ROW), lang)


@sample(
    "community",
    section=_SECTION,
    title="Режим включён общими настройками",
    nav="panel",
    user_data=_TITLES,
)
def community_inherited(lang: Lang) -> Screen:
    """Every part from the general settings: the one line that says so, and the
    facts of a chat where the newcomer greeting comes on joining."""
    return screens.build_community(_view(community_enabled=True), lang)


# ── percentages and the reply language ───────────────────────────────────


@sample("chance.prompt", section=_SECTION, title="Встревает: сейчас пресет", nav="panel")
def chance_joins_preset(lang: Lang) -> Screen:
    return screens.build_chance_prompt(_view(), "rc", lang)


@sample("chance.prompt", section=_SECTION, title="Встревает: своё число", nav="panel")
def chance_joins_custom(lang: Lang) -> Screen:
    """0.3 % is no preset: the text says it, and no button is marked."""
    return screens.build_chance_prompt(_view(random_response_chance=0.003), "rc", lang)


@sample("chance.prompt", section=_SECTION, title="Стикер на стикер выключен", nav="panel")
def chance_sticker_off(lang: Lang) -> Screen:
    row = {"sticker_reply_to_sticker_enabled": False, "sticker_learning_enabled": True}
    return screens.build_chance_prompt(_view(row=row), "sx", lang)


@sample("chance.prompt", section=_SECTION, title="Учиться на стикерах выключено", nav="panel")
def chance_sticker_no_learning(lang: Lang) -> Screen:
    return screens.build_chance_prompt(_view(), "sx", lang)


@sample("chance.stickers", section=_SECTION, title="Значения общие", nav="panel", user_data=_TITLES)
def chance_stickers_common(lang: Lang) -> Screen:
    return screens.build_sticker_chances(_view(sticker_learning_enabled=True), lang)


@sample(
    "chance.stickers",
    section=_SECTION,
    title="Значения свои, одно выключено",
    nav="panel",
    user_data=_TITLES,
)
def chance_stickers_own(lang: Lang) -> Screen:
    own = {
        **_OWN_ROW,
        "sticker_learning_enabled": True,
        "sticker_reply_to_sticker_chance": 0.25,
        "image_comment_sticker_chance": 0.2,
    }
    return screens.build_sticker_chances(_view(row=own), lang)


@sample(
    "chance.stickers",
    section=_SECTION,
    title="Учиться на стикерах выключено",
    nav="panel",
    user_data=_TITLES,
)
def chance_stickers_no_learning(lang: Lang) -> Screen:
    return screens.build_sticker_chances(_view(), lang)


@sample("chance.in", section=_SECTION, title="Встревает: ввод числа", nav="prompt")
def chance_joins_input(lang: Lang) -> Screen:
    return screens.build_chance_input(_view(), "rc", lang)


@sample("chance.in", section=_SECTION, title="Стикер на стикер: ввод числа", nav="prompt")
def chance_sticker_input(lang: Lang) -> Screen:
    return screens.build_chance_input(_view(), "sx", lang)


@sample("chance.invalid", section=_SECTION, title="Не число", nav="prompt")
def chance_not_a_number(lang: Lang) -> Screen:
    return screens.build_chance_input(_view(), "rc", lang, invalid="number")


@sample("chance.invalid", section=_SECTION, title="Больше предела", nav="prompt")
def chance_above_cap(lang: Lang) -> Screen:
    return screens.build_chance_input(_view(), "rc", lang, invalid="high")


@sample("chat.lang", section=_SECTION, title="Сейчас русский", nav="panel")
def language_ru(lang: Lang) -> Screen:
    return screens.build_language_prompt(_view(), lang)


@sample("chat.lang", section=_SECTION, title="Сейчас неизвестный код", nav="panel")
def language_unknown(lang: Lang) -> Screen:
    """A code the panel does not offer: shown as stored, no button marked."""
    return screens.build_language_prompt(_view(language="de"), lang)
