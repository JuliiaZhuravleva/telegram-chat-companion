"""Screens people meet outside the panel: /start, /help, the group hints, /kb, /callme, /summary.

Pure builders: they take plain values and return `Screen` data, so the
handlers stay thin and the samples draw the very same screens. Every value
that comes from a chat or a person (a name, a fact, a trigger word) is escaped
here, once, because the bot's default parse mode is HTML.
"""

from __future__ import annotations

import html
from typing import Final

from src.bot.command_registry import CommandScope, specs_for_scope
from src.bot.screens.i18n import Lang, Tr, plural_ru
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import back, pager
from src.bot.screens.routes import cb

PANEL_DEEP_LINK: Final = "admin"
"""``/start admin``: the payload of the group hint's link to the panel."""

SUMMARY_COUNTS: Final = (100, 500)
"""The two counts the summary buttons offer."""


def add_to_chat_url(bot_username: str) -> str:
    """Telegram's "pick a group to add this bot to" link."""
    return f"https://t.me/{bot_username}?startgroup=true"


def panel_url(bot_username: str) -> str:
    """A link that opens the DM with ``/start admin`` — the panel, for a bot admin."""
    return f"https://t.me/{bot_username}?start={PANEL_DEEP_LINK}"


def pick_trigger(trigger_words: tuple[str, ...] | list[str], lang: Lang) -> str:
    """The trigger word to show: one in the chat's script if there is one."""
    words = [w for w in trigger_words if w and w.strip()]
    if not words:
        return "бот" if lang == "ru" else "bot"
    cyrillic = [w for w in words if any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in w)]
    latin = [w for w in words if w not in cyrillic]
    preferred = cyrillic if lang == "ru" else latin
    return (preferred or words)[0]


# ---------------------------------------------------------------------------
# /start and /help
# ---------------------------------------------------------------------------

_NAME: Final = Tr("🤖 <b>Чат-компаньон</b>", "🤖 <b>Chat companion</b>")
_ADD: Final = Tr("➕ Добавить в свой чат", "➕ Add to your chat")
_WHAT: Final = Tr("📖 Что я умею", "📖 What I can do")
_PANEL: Final = Tr("🛠 Панель", "🛠 Panel")
_START: Final = Tr("Старт", "Start")
_CLOSE: Final = Tr("✖️ Закрыть", "✖️ Close")

_MEMBER_BODY: Final = Tr(
    "Я живу в групповых чатах: отвечаю, когда меня зовут, расшифровываю голосовые "
    "и помню, что обсуждали.",
    "I live in group chats: I answer when called, transcribe voice messages "
    "and remember what was discussed.",
)
_ADMIN_BODY: Final = Tr(
    "Панель открывается кнопкой или командой /admin.",
    "The panel opens with the button or the /admin command.",
)
_HOW_TO_CALL: Final = Tr(
    "Напишите «{trigger}» в сообщении или ответьте на моё сообщение, иногда отвечаю и сам 😉",
    "Write “{trigger}” in a message or reply to one of mine; sometimes I join in on my own 😉",
)
_IN_GROUP: Final = Tr("Я участник чата.", "I am a member of this chat.")
_IN_DM: Final = Tr("Я работаю в групповых чатах.", "I work in group chats.")
_WHAT_TITLE: Final = Tr("📖 <b>Что я умею</b>", "📖 <b>What I can do</b>")

_VOICE_AND_ROUND: Final = Tr(
    "🎙 Расшифровываю голосовые и кружочки", "🎙 I transcribe voice and video notes"
)
_VOICE_ONLY: Final = Tr("🎙 Расшифровываю голосовые", "🎙 I transcribe voice messages")
_ROUND_ONLY: Final = Tr("🎙 Расшифровываю кружочки", "🎙 I transcribe video notes")
_MEMORY: Final = Tr("🧠 Помню, о чём говорили", "🧠 I remember what was discussed")
_GROUP_SUMMARY: Final = Tr(
    "📋 /summary — саммари, можно с числом: <code>/summary 300</code>",
    "📋 /summary — a summary, with a count if you like: <code>/summary 300</code>",
)
_GROUP_KB: Final = Tr("📚 /kb — база знаний чата", "📚 /kb — the chat's knowledge base")
_GROUP_CALLME: Final = Tr("🏷 /callme — как мне вас называть", "🏷 /callme — what I call you")
_DM_FEATURES: Final = Tr(
    "🎙 Расшифровываю голосовые и кружочки\n"
    "🧠 Помню, о чём говорили\n"
    "📋 Делаю саммари в чате: /summary\n"
    "📚 Показываю базу знаний: /kb\n"
    "🏷 Запоминаю, как вас называть: /callme",
    "🎙 I transcribe voice and video notes\n"
    "🧠 I remember what was discussed\n"
    "📋 I summarise the chat: /summary\n"
    "📚 I show the knowledge base: /kb\n"
    "🏷 I remember what to call you: /callme",
)
_SUMMARY_BUTTON: Final = Tr("📋 Саммари {count}", "📋 Summary {count}")


def _what_button(lang: Lang) -> Button:
    return Button(_WHAT(lang), callback=cb("help_dm:", lang, "help"))


def _add_row(lang: Lang, bot_username: str | None) -> tuple[tuple[Button, ...], ...]:
    if not bot_username:
        return ()
    return ((Button(_ADD(lang), url=add_to_chat_url(bot_username)),),)


def start_member(lang: Lang, *, bot_username: str | None) -> Screen:
    """``/start`` in a DM for anyone who is not a bot admin."""
    return Screen(
        id="start.member",
        text=f"{_NAME(lang)}\n\n{_MEMBER_BODY(lang)}",
        rows=(*_add_row(lang, bot_username), (_what_button(lang),)),
    )


def start_admin(lang: Lang) -> Screen:
    """``/start`` in a bot admin's DM: the panel is one tap away."""
    return Screen(
        id="start.admin",
        text=f"{_NAME(lang)}\n\n{_ADMIN_BODY(lang)}",
        rows=(
            (Button(_PANEL(lang), callback=cb("adm_menu:", lang)),),
            (_what_button(lang),),
        ),
    )


def help_dm(lang: Lang, *, trigger: str, bot_username: str | None, admin: bool) -> Screen:
    """``/help`` in a DM, or the start screen's "What I can do"."""
    how = _HOW_TO_CALL(lang).format(trigger=html.escape(trigger))
    start_id = "start.admin" if admin else "start.member"
    return Screen(
        id="help.dm",
        text=f"{_WHAT_TITLE(lang)}\n\n{_IN_DM(lang)} {how}\n\n{_DM_FEATURES(lang)}",
        rows=(
            *_add_row(lang, bot_username),
            (back(start_id, cb("help_dm:", lang, "start"), lang, name=_START(lang)),),
        ),
    )


def summary_buttons(lang: Lang, user_id: int) -> tuple[Button, ...]:
    """``📋 Саммари 100 · 📋 Саммари 500``: each posts a new summary for the presser's owner."""
    return tuple(
        Button(_SUMMARY_BUTTON(lang).format(count=n), callback=cb("help_summary:", user_id, n))
        for n in SUMMARY_COUNTS
    )


def help_group(
    lang: Lang,
    *,
    user_id: int,
    trigger: str,
    voice: bool,
    video_notes: bool,
    memory: bool,
    summary: bool,
    kb: bool,
) -> Screen:
    """``/help`` in a group: only what this chat has switched on."""
    how = _HOW_TO_CALL(lang).format(trigger=html.escape(trigger))
    lines: list[str] = []
    if voice and video_notes:
        lines.append(_VOICE_AND_ROUND(lang))
    elif voice:
        lines.append(_VOICE_ONLY(lang))
    elif video_notes:
        lines.append(_ROUND_ONLY(lang))
    if memory:
        lines.append(_MEMORY(lang))
    if summary:
        lines.append(_GROUP_SUMMARY(lang))
    if kb:
        lines.append(_GROUP_KB(lang))
    lines.append(_GROUP_CALLME(lang))
    rows: list[tuple[Button, ...]] = []
    if summary:
        rows.append(summary_buttons(lang, user_id))
    rows.append((Button(_CLOSE(lang), callback=cb("help_close:", user_id), kind="exit"),))
    return Screen(
        id="help.group",
        text=f"{_NAME(lang)}\n\n{_IN_GROUP(lang)} {how}\n\n" + "\n".join(lines),
        rows=tuple(rows),
    )


# ---------------------------------------------------------------------------
# Command menus (what Telegram lists under "/" and the Menu button)
# ---------------------------------------------------------------------------


def command_menu(lang: Lang, scope: CommandScope) -> Screen:
    """The command list a scope advertises, one ``/command — description`` per line.

    Drawn from the registry itself, so the contact sheet shows exactly what a
    deploy pushes to Telegram.
    """
    screen_id = "menu.group" if scope is CommandScope.GROUPS else "menu.dm"
    lines = [
        f"/{spec.command} — {html.escape(spec.description_for(lang))}"
        for spec in specs_for_scope(scope)
    ]
    return Screen(id=screen_id, text="\n".join(lines))


# ---------------------------------------------------------------------------
# Group-side moments
# ---------------------------------------------------------------------------

_HINT: Final = Tr("Панель открывается в личке.", "The panel opens in a private chat with me.")
_OPEN_PANEL: Final = Tr("🛠 Открыть панель", "🛠 Open the panel")
_ADDED: Final = Tr(
    "Привет! Я начну работать, когда админ бота одобрит этот чат.",
    "Hi! I will start working once the bot's admin approves this chat.",
)


def admin_hint(lang: Lang, *, bot_username: str | None) -> Screen:
    """``/admin`` or ``/settings`` from a bot admin in a group: the panel lives in the DM."""
    rows = ((Button(_OPEN_PANEL(lang), url=panel_url(bot_username)),),) if bot_username else ()
    return Screen(id="group.admin_hint", text=_HINT(lang), rows=rows)


def group_added(lang: Lang) -> Screen:
    """The one line a chat sees when the bot joins it before anyone approved it."""
    return Screen(id="group.added", text=_ADDED(lang))


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

_SUMMARY_NAME: Final = Tr("Саммари чата", "Chat summary")
_SUMMARY_INVALID: Final = Tr(
    "🤔 Не понял количество сообщений. Выберите или напишите <code>/summary 300</code>: "
    "от {min} до {max}.",
    "🤔 I did not get the message count. Pick one or write <code>/summary 300</code>: "
    "from {min} to {max}.",
)


def _messages(lang: Lang, n: int) -> str:
    if lang == "ru":
        return f"{n} {plural_ru(n, 'сообщение', 'сообщения', 'сообщений')}"
    return f"{n} message" if n == 1 else f"{n} messages"


def summary_heading(lang: Lang, count: int, *, bold: tuple[str, str] = ("<b>", "</b>")) -> str:
    """``📋 <b>Саммари чата</b> · 2 сообщения``: the one source of the summary's heading.

    ``bold`` is the markup around the name. The summary service writes its
    heading in Markdown (``("**", "**")``) because it is converted together with
    the model's text; the words, the count and the layout come from here either way.
    """
    opening, closing = bold
    return f"📋 {opening}{_SUMMARY_NAME(lang)}{closing} · {_messages(lang, count)}"


def summary_result(lang: Lang, *, count: int, body_html: str) -> Screen:
    """A summary: the heading names the count, the body is the model's (escaped) HTML."""
    return Screen(
        id="summary.result",
        text=f"{summary_heading(lang, count)}\n\n{body_html}",
    )


def summary_invalid(lang: Lang, *, user_id: int, low: int, high: int) -> Screen:
    """``/summary abc`` or a count out of range: two counts to tap, or the shape to type."""
    return Screen(
        id="summary.invalid",
        text=_SUMMARY_INVALID(lang).format(min=low, max=high),
        rows=(summary_buttons(lang, user_id),),
    )


# ---------------------------------------------------------------------------
# /kb
# ---------------------------------------------------------------------------

_KB_TITLE: Final = Tr("📚 <b>База знаний</b>", "📚 <b>Knowledge base</b>")


def _facts(lang: Lang, n: int) -> str:
    if lang == "ru":
        return f"{n} {plural_ru(n, 'факт', 'факта', 'фактов')}"
    return f"{n} fact" if n == 1 else f"{n} facts"


def kb_pager(lang: Lang, page: int, pages: int) -> tuple[Button, ...] | None:
    """The ``◀ · 1/2 · ▶`` row under /kb, or None when everything fits on one page."""
    return pager(page, pages, lambda p: cb("kb_view:", lang, p))


KB_TITLE_UNITS: Final = 40
"""How much of a chat's name the DM heading shows, in UTF-16 units as Telegram
counts them: a phone line, and a fixed cost the handler's length budget can
count on (a title may be 128 characters, each an astral emoji)."""


def _units(text: str) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _kb_label(title: str | None, lang: Lang, total: int) -> str:
    """The chat's name, escaped and capped; the fact count when the chat has no name."""
    name = " ".join((title or "").split())
    if not name:
        return _facts(lang, total)
    if _units(name) > KB_TITLE_UNITS:
        kept: list[str] = []
        room = KB_TITLE_UNITS - 1  # the "…"
        for ch in name:
            room -= _units(ch)
            if room < 0:
                break
            kept.append(ch)
        name = "".join(kept).rstrip() + "…"
    return html.escape(name)


def _kb(screen_id: str, lang: Lang, *, label: str, body: str, page: int, pages: int) -> Screen:
    title = f"{_KB_TITLE(lang)} · {label}"
    row = kb_pager(lang, page, pages)
    return Screen(
        id=screen_id,
        text=f"{title}\n\n{body}" if body else title,
        rows=(row,) if row else (),
    )


def kb_group(lang: Lang, *, total: int, body: str, page: int, pages: int) -> Screen:
    """``/kb`` in a group: one line per fact; the pager only when there is a second page.

    ``body`` is already-escaped HTML (the handler fits it under Telegram's limit).
    """
    return _kb("kb.group", lang, label=_facts(lang, total), body=body, page=page, pages=pages)


_KB_PRIVATE: Final = Tr("личный чат", "private chat")


def kb_dm(
    lang: Lang,
    *,
    title: str | None,
    total: int,
    body: str,
    page: int,
    pages: int,
    private: bool = False,
) -> Screen:
    """``/kb`` in a DM: which chat on top, facts by topic with who and when; same pager.

    ``private``: the facts are the private chat's own, and the heading says
    «личный чат» -- the chat's only name there is the reader's own. Otherwise
    ``title`` is the chat's name as Telegram gives it (raw, escaped here); a
    chat with no name shows the fact count instead.
    """
    label = _KB_PRIVATE(lang) if private else _kb_label(title, lang, total)
    return _kb("kb.dm", lang, label=label, body=body, page=page, pages=pages)


# ---------------------------------------------------------------------------
# /callme
# ---------------------------------------------------------------------------

_CALLME_TITLE: Final = Tr("🏷 <b>Как мне вас называть</b>", "🏷 <b>What I call you</b>")
_CALLME_HOW: Final = Tr(
    "Своё имя: <code>/callme Костя</code>\n"
    "Админы чата могут задать имя другому: ответом на его сообщение "
    "или <code>/callme @ник Костя</code>.",
    "Your own name: <code>/callme Kostya</code>\n"
    "Chat admins can name someone else: reply to their message "
    "or <code>/callme @handle Kostya</code>.",
)
_CALLME_NOW_YOU: Final = Tr(
    "Сейчас бот зовёт вас <b>{name}</b>.", "Right now the bot calls you <b>{name}</b>."
)
_CALLME_NOW_OTHER: Final = Tr(
    "Сейчас для бота {who} — <b>{name}</b>.", "Right now the bot calls {who} <b>{name}</b>."
)
_CALLME_ACCOUNT: Final = Tr("🏷 Как в аккаунте", "🏷 As in my account")
_CALLME_CLEAR: Final = Tr("✖️ Убрать имя", "✖️ Remove the name")
_CALLME_SET_ACCOUNT: Final = Tr("🏷 Буду звать вас {name}.", "🏷 I will call you {name}.")
_CALLME_OTHER: Final = Tr(
    "Готово: теперь {who} для бота <b>{name}</b>.", "Done: the bot now calls {who} <b>{name}</b>."
)
_CALLME_CLEARED_OTHER: Final = Tr(
    "Убрал имя. {who} теперь по имени из аккаунта.",
    "Name removed. The bot calls {who} by their account name again.",
)
_CALLME_NOTHING_OTHER: Final = Tr(
    "У этого участника имя не задано.", "This member has no name set."
)


def callme_usage(lang: Lang, *, current: str | None, other: str | None = None) -> Screen:
    """``/callme`` with no name: how it works, the name in force, two buttons for the reader.

    The buttons act on whoever presses them, so they appear only when the
    screen is about the reader (``other`` is None); "remove" only when there
    is a name to remove.
    """
    parts = [_CALLME_TITLE(lang)]
    if current:
        name = html.escape(current)
        if other is None:
            parts.append(_CALLME_NOW_YOU(lang).format(name=name))
        else:
            parts.append(_CALLME_NOW_OTHER(lang).format(who=html.escape(other), name=name))
    parts.append(_CALLME_HOW(lang))
    rows: tuple[tuple[Button, ...], ...] = ()
    if other is None:
        account = (Button(_CALLME_ACCOUNT(lang), callback=cb("help_callme:", lang, "acc")),)
        clear = (Button(_CALLME_CLEAR(lang), callback=cb("help_callme:", lang, "clr")),)
        rows = (account, clear) if current else (account,)
    return Screen(id="callme.usage", text="\n\n".join(parts), rows=rows)


def callme_set_account(lang: Lang, *, name: str) -> Screen:
    """After "As in my account": the name the bot now uses for the presser."""
    return Screen(
        id="callme.set_account", text=_CALLME_SET_ACCOUNT(lang).format(name=html.escape(name))
    )


def callme_set_account_toast(lang: Lang, *, name: str) -> str:
    """The same words as a plain-text toast: a toast is not parsed as HTML."""
    return _CALLME_SET_ACCOUNT(lang).format(name=name)


_CALLME_CLEARED_TOAST: Final = Tr(
    "Убрал имя. Бот снова зовёт вас по аккаунту.",
    "Name removed. The bot calls you by your account name again.",
)
_CALLME_NOTHING_TOAST: Final = Tr("У вас и не было имени.", "You had no name set.")


def callme_cleared_toast(lang: Lang, *, had_name: bool) -> str:
    """After "Remove the name": a plain-text toast."""
    return (_CALLME_CLEARED_TOAST if had_name else _CALLME_NOTHING_TOAST)(lang)


def callme_other(lang: Lang, *, who: str, name: str) -> Screen:
    """An admin named another member."""
    text = _CALLME_OTHER(lang).format(who=html.escape(who), name=html.escape(name))
    return Screen(id="callme.other", text=text)


def callme_cleared_other(lang: Lang, *, who: str) -> Screen:
    """An admin removed another member's name."""
    return Screen(
        id="callme.cleared_other", text=_CALLME_CLEARED_OTHER(lang).format(who=html.escape(who))
    )


def callme_nothing_other(lang: Lang) -> Screen:
    """An admin tried to remove a name another member never had."""
    return Screen(id="callme.nothing_other", text=_CALLME_NOTHING_OTHER(lang))
