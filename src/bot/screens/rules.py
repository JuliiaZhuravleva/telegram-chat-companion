"""Rules screens: one chat's rule list, a rule's card, the button builder, and the
admin card a fired rule sends.

Pure builders: each takes plain values and returns a `Screen`. The handlers in
``src/bot/handlers/rules.py`` read the database and call these; the samples in
``samples/rules.py`` call them with invented values. Nothing here touches
Telegram or the database, so the rule executor can import this module lazily.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from html import escape
from typing import Any, Final

from src.bot.custom_emoji import fence
from src.bot.screens.chat import COMMON_NOTE, LABELS
from src.bot.screens.i18n import Tr, plural_ru
from src.bot.screens.lint import clip_room, label_width_px
from src.bot.screens.model import Button, ReplyButton, RequestUsers, Screen
from src.bot.screens.render import CANCEL, back, cancel, pager
from src.bot.screens.routes import REQUEST_IDS, cb

PER_PAGE: Final = 3
"""Rules per list page: three rows of rules keep the list to six rows with its pager."""

WHO_PER_PAGE: Final = 4
"""People per picker page: four names, the pager and the Find/Cancel row are six rows."""

FLOOD_CHOICES: Final = (5, 10, 20)
FLOOD_SUGGESTED: Final = 10
SPAM_CHOICES: Final = (10, 20, 40)
SPAM_SUGGESTED: Final = 20
REACTIONS: Final = ("👀", "🤡", "💩", "👍", "😁")
WARN_PRESETS: Final = (
    Tr("Давайте без спама", "Let's skip the spam"),
    Tr("Давайте потише", "Let's keep it down"),
)

MAX_WORDS: Final = 20
MAX_WORD_CHARS: Final = 64

_JSON_SHOWN_CHARS: Final = 3000
JSON_SHOW: Final = Tr("🧩 Как записано (JSON)", "🧩 As stored (JSON)")
JSON_HIDE: Final = Tr("🧩 Скрыть запись (JSON)", "🧩 Hide the record (JSON)")
JSON_CAPTION: Final = Tr(
    "Это запись правила для бота; выше — главное из неё словами. Изменить правило нельзя, "
    "можно создать новое: «{add}» → «{cfg}».",
    "This is the bot's record of the rule; the lines above give the gist in words. A rule "
    "cannot be edited, only made anew: «{add}» → «{cfg}».",
)
"""The line above a rule's ``<pre>``. "The gist", not "the same": the words
leave out fields the engine reads (``exclude_users``, ``case_sensitive``,
``target_users``…). The record is `rule_record`: the type, the config, and
the weight and "required" columns when they are not the defaults, so pasted
into the JSON prompt it makes the same rule. No path edits a stored rule's
config; the JSON prompt only creates one."""
_EXCERPT_CHARS: Final = 100
_CAUGHT_CHARS: Final = 200

KINDS: Final = {
    "k": "keyword_trigger",
    "u": "user_specific",
    "f": "sticker_flood",
    "s": "spam_detect",
}
"""Builder kind token → rule type. One letter keeps the builder's callbacks short."""

ACTIONS: Final = {
    "n": "notify_admin",
    "w": "warn_user",
    "c": "custom_response",
    "r": "set_reaction",
}
"""Builder action token → rule action."""

PRIORITY: Final = Tr("приоритет", "priority")
"""A rule's ``weight``, by a word readers know: one name on every rules screen.

«вес» / "weight" read as nothing to a reader who has not seen the JSON, so both
languages say priority; only the JSON hint names the field ``weight``."""
MANDATORY: Final = Tr("обязательное", "required")
"""A ``mandatory`` rule: it acts whatever the mode (`RuleEngine.evaluate`)."""

MODES: Final = {
    "highest_weight": Tr(
        "все обязательные и одно с наибольшим приоритетом",
        "every required one and the one with the highest priority",
    ),
    "weighted_random": Tr(
        "все обязательные и одно случайное по приоритету",
        "every required one and one at random by priority",
    ),
}
"""Rules modes other than ``all``. Only these change what a rule's weight means.

The name says the mandatory rules too: «одно правило…» on the line above
«срабатывают все обязательные и одно…» read as a contradiction to all five
readers of round 4."""
_HIGHER: Final = Tr("больше число — выше приоритет", "a bigger number is a higher priority")
"""Which way a priority points: no reader of round 4 could tell whether 2 beats 1.

`RuleEngine._apply_mode` keeps the maximum ``weight`` (and weighs the draw by it)."""
MODE_ALL: Final = Tr("все подходящие правила", "every matching rule")
_MODE: Final = Tr("Режим", "Mode")

_SEVERAL: Final = {
    "all": Tr(
        "Если сообщение подходит под несколько правил, срабатывают все.",
        "If a message matches several rules, all of them act.",
    ),
    "highest_weight": Tr(
        "Если сообщение подходит под несколько правил, срабатывают все обязательные "
        "и одно из остальных — с наибольшим приоритетом ({higher}).",
        "If a message matches several rules, every required one acts, "
        "and of the others only the one with the highest priority ({higher}).",
    ),
    "weighted_random": Tr(
        "Если сообщение подходит под несколько правил, срабатывают все обязательные "
        "и одно случайное из остальных: чем выше приоритет, тем вероятнее ({higher}).",
        "If a message matches several rules, every required one acts, "
        "and of the others one at random: the higher the priority, the likelier ({higher}).",
    ),
}


def mode_line(mode: str, lang: str) -> str:
    """``Режим: …``: the one line that names a rules mode, on a chat's rules and on the defaults."""
    if mode == "all":
        name = MODE_ALL(lang)
    elif mode in MODES:
        name = MODES[mode](lang)
    else:
        name = escape(mode)
    return f"{_MODE(lang)}: {name}"


def several_line(mode: str, lang: str) -> str:
    """What acts when one message matches several rules, as `RuleEngine.evaluate` decides.

    Every matching mandatory rule acts; the mode picks only among the others;
    a mode the engine does not know runs as ``all``, and reads so here.
    """
    return _SEVERAL.get(mode, _SEVERAL["all"])(lang).format(higher=_HIGHER(lang))


WARN_FALLBACK: Final = Tr(
    "⚠️ Сработало автоматическое правило чата.",
    "⚠️ An automated chat rule was triggered.",
)
"""What ``warn_user`` posts when the rule has no text of its own."""

_TITLE: Final = Tr("Правила", "Rules")
_ADD: Final = Tr("➕ Добавить", "➕ Add")
_CFG_BUTTON: Final = Tr("🧩 Настроить в JSON", "🧩 Set up in JSON")
_ON: Final = Tr("работает", "on")
_PAUSED: Final = Tr("на паузе", "paused")
_NEW: Final = Tr("Новое правило:", "New rule:")
_ELLIPSIS: Final = "…"


# ---------------------------------------------------------------------------
# Values the screens read
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatRules:
    """A chat's rule settings as the screens show them."""

    enabled: bool
    common: bool
    """The value is inherited (``chat_settings.rules_enabled`` is NULL): `common_line` says so."""
    mode: str = "all"


@dataclass(frozen=True)
class RuleView:
    """One ``custom_rules`` row, with its config parsed."""

    id: int
    chat_id: int
    rule_type: str
    config: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    weight: int = 1
    mandatory: bool = False
    trigger_count: int = 0


def parse_config(raw: object) -> dict[str, Any]:
    """A rule's config as a dict: asyncpg hands JSONB back as a string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def rule_view(row: Mapping[str, Any]) -> RuleView:
    return RuleView(
        id=int(row["id"]),
        chat_id=int(row.get("chat_id") or 0),
        rule_type=str(row.get("rule_type") or ""),
        config=parse_config(row.get("config")),
        enabled=bool(row.get("enabled", True)),
        weight=int(row.get("weight") or 1),
        mandatory=bool(row.get("mandatory", False)),
        trigger_count=int(row.get("trigger_count") or 0),
    )


_TYPE_NAMES: Final = {
    "keyword_trigger": Tr("Ключевые слова", "Keywords"),
    "user_specific": Tr("Сообщения одного человека", "One person's messages"),
    "sticker_flood": Tr("Много стикеров", "Many stickers"),
    "spam_detect": Tr("Много сообщений", "Many messages"),
}


def rule_name(rule: RuleView, lang: str) -> str:
    """The name a rule is shown by: its own, else its words, else its type."""
    name = rule.config.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    words = _str_list(rule.config.get("keywords"))
    if rule.rule_type == "keyword_trigger" and words:
        return ", ".join(words)
    kind = _TYPE_NAMES.get(rule.rule_type)
    return kind(lang) if kind else f"#{rule.id}"


def _room(n: int) -> float:
    """Estimated width a cut label may take when its row holds ``n`` buttons."""
    return clip_room(n)


def fit(text: str, width: float) -> str:
    """``text`` cut with ``…`` so its rendered width stays within ``width``."""
    if label_width_px(text) <= width:
        return text
    while text and label_width_px(text + _ELLIPSIS) > width:
        text = text[:-1]
    return text.rstrip() + _ELLIPSIS


def chat_id_text(chat_id: int) -> str:
    """The id an admin can match against ``t.me/c/…``: without the ``-100``."""
    raw = str(chat_id)
    return raw[4:] if raw.startswith("-100") else raw.lstrip("-")


def _str_list(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


# ---------------------------------------------------------------------------
# rules: the list
# ---------------------------------------------------------------------------


_SWITCH_STATE: Final = {True: Tr("включены", "on"), False: Tr("выключены", "off")}

SWITCH: Final = Tr("выключатель", "switch")
"""What the text calls the top button, so it reads as the thing a tap flips, not a state."""
_SWITCH_HINT: Final = {
    True: Tr(
        "{top} — общий {switch}: после нажатия все правила будут {after}.",
        "{top} is the master {switch}: tap it and all rules are {after}.",
    ),
    False: Tr(
        "{top} — общий {switch}: после нажатия правила будут {after}, кроме тех, что {paused}.",
        "{top} is the master {switch}: tap it and rules are {after}, except the {paused} ones.",
    ),
}
"""By the switch's state now. Turning rules on flips the chat's switch only, so a
paused rule stays paused, and the line says so rather than promise all of them."""
_TOP: Final = Tr("Верхняя кнопка", "The top button")


def switch_hint(enabled: bool, lang: str) -> str:
    """The line that names the top button a switch and says what a tap leaves."""
    return _SWITCH_HINT[enabled](lang).format(
        top=_TOP(lang),
        switch=SWITCH(lang),
        after=_SWITCH_STATE[not enabled](lang),
        paused=_PAUSED(lang),
    )


def switch_name(enabled: bool, lang: str) -> str:
    """The rules switch by name: the panel's noun and one state word, on every surface."""
    return f"{LABELS['re'](lang)} {_SWITCH_STATE[enabled](lang)}"


def master_label(chat: ChatRules, lang: str) -> str:
    """The switch for all of a chat's rules: its mark and its state, nothing else."""
    glyph = "✅" if chat.enabled else "⚪"
    return f"{glyph} {switch_name(chat.enabled, lang)}"


def common_line(chat: ChatRules, lang: str) -> str | None:
    """Where the switch's value comes from, in plain text, as the chat sections say it.

    A suffix on the button read as part of the state (``⚪ … · общее`` as "off
    for everyone"); the origin goes in the text, once, or not at all."""
    return COMMON_NOTE(lang).format(names=LABELS["re"](lang)) if chat.common else None


def _row_parts(rule: RuleView, chat: ChatRules, lang: str) -> tuple[str, str, str]:
    """A row's glyph, its name cut to the room left, and one tag.

    The tag is «на паузе» for a paused rule; under a priority mode a working rule
    shows the fact the mode reads (mandatory, or its priority) and not «работает»,
    which the 📏 glyph already says. Two tags cut the builder's own names
    («Сообщения: 20 за…»); a paused rule's priority does not matter while it is
    paused, and «обязательное» on it would read as "always acts".
    """
    if not rule.enabled:
        head, tail = "⏸ ", f" · {_PAUSED(lang)}"
    elif chat.mode in MODES:
        head = "📏 "
        tail = f" · {MANDATORY(lang)}" if rule.mandatory else f" · {PRIORITY(lang)} {rule.weight}"
    else:
        head, tail = "📏 ", f" · {_ON(lang)}" if chat.enabled else ""
    room = _room(1) - label_width_px(head + tail)
    return head, fit(rule_name(rule, lang), room), tail


def row_name(rule: RuleView, chat: ChatRules, lang: str) -> str:
    """The rule's name as its row in the list shows it: cut to leave room for the state."""
    return _row_parts(rule, chat, lang)[1]


def _row_label(rule: RuleView, chat: ChatRules, lang: str) -> str:
    return "".join(_row_parts(rule, chat, lang))


def rules_screen(
    lang: str,
    *,
    chat_id: int,
    title: str,
    chat: ChatRules,
    rules: Sequence[RuleView],
    page: int = 0,
    pages: int = 1,
    notice: str | None = None,
    notice_raw: str | None = None,
) -> Screen:
    """``rules``: one chat's rules, three a page, with the switch for all of them.

    Under a list with rules, what acts when one message matches several of them,
    in words. The mode's name is not shown here: the sentence says all of it, and
    the two lines together read as a contradiction (round 4). The defaults screen,
    where the mode is the subject, still names it.
    """
    listed = bool(rules or page)
    lines = [f"📏 <b>{_TITLE(lang)}</b> · {escape(title)}", ""]
    if listed:
        lines += [switch_hint(chat.enabled, lang), _OPEN_HINT(lang), ""]
    else:
        lines.append(Tr("Правил пока нет.", "No rules yet.")(lang))
    if listed:
        lines.append(several_line(chat.mode, lang))
    common = common_line(chat, lang)
    if common:
        lines += ["", common]

    rows: list[tuple[Button, ...]] = [
        (Button(master_label(chat, lang), cb("ar_all:", lang, chat_id)),)
    ]
    rows.extend(
        (Button(_row_label(rule, chat, lang), cb("ar_view:", lang, rule.id, chat_id)),)
        for rule in rules
    )
    nav = pager(page, pages, lambda p: cb("ar_list:", lang, chat_id, p))
    if nav is not None:
        rows.append(nav)
    rows.append(
        (
            Button(_ADD(lang), cb("ar_add:", lang, chat_id)),
            back("chat", cb("adm_pnl_menu:", lang, chat_id), lang),
        )
    )
    return Screen(
        id="rules", text="\n".join(lines), rows=tuple(rows), notice=notice, notice_raw=notice_raw
    )


_OPEN_HINT: Final = Tr("Нажмите правило, чтобы открыть его.", "Tap a rule to open it.")

GONE_NOTICE: Final = Tr("<i>Этого правила уже нет.</i>", "<i>This rule is gone.</i>")
"""The line ``rule.gone`` puts over the list (trusted HTML, no ✓: nothing was done)."""

RULES_ON: Final = Tr(switch_name(True, "ru"), switch_name(True, "en"))
RULES_OFF: Final = Tr(switch_name(False, "ru"), switch_name(False, "en"))
"""The notice after the switch for all of a chat's rules: the switch's own words."""


def gone_screen(
    lang: str,
    *,
    chat_id: int,
    title: str,
    chat: ChatRules,
    rules: Sequence[RuleView],
    page: int = 0,
    pages: int = 1,
    notice: str | None = None,
) -> Screen:
    """``rule.gone``: the rule a button pointed at was deleted meanwhile; its chat's list.

    ``notice`` is what the tap did anyway (the switch for all rules still
    flips), shown as the ✓ line above the gone line.
    """
    gone = GONE_NOTICE(lang)
    screen = rules_screen(
        lang,
        chat_id=chat_id,
        title=title,
        chat=chat,
        rules=rules,
        page=page,
        pages=pages,
        notice_raw=f"<i>✓ {escape(notice)}</i>\n{gone}" if notice is not None else gone,
    )
    return replace(screen, id="rule.gone")


# ---------------------------------------------------------------------------
# rule: one rule's card
# ---------------------------------------------------------------------------


def _quote(text: str, limit: int = _CAUGHT_CHARS) -> str:
    text = text if len(text) <= limit else text[: limit - 1] + _ELLIPSIS
    return f"«{escape(text)}»"


def catches(rule: RuleView, lang: str) -> str:
    """What sets a rule off, in words."""
    cfg = rule.config
    if rule.rule_type == "keyword_trigger":
        words = ", ".join(_str_list(cfg.get("keywords")))
        how = {
            "exact": Tr("только слово, без текста", "the word alone"),
            "regex": Tr("регулярное выражение", "regular expression"),
        }.get(
            str(cfg.get("match_type", "contains")),
            Tr("слово внутри сообщения", "a word inside a message"),
        )
        return f"{_quote(words)} — {how(lang)}"
    if rule.rule_type == "user_specific":
        conditions = cfg.get("conditions")
        conditions = conditions if isinstance(conditions, Mapping) else {}
        if conditions.get("any_message"):
            return Tr("все сообщения одного человека", "every message from one person")(lang)
        words = ", ".join(_str_list(conditions.get("keywords")))
        return Tr("слова одного человека: {w}", "one person's words: {w}")(lang).format(
            w=_quote(words)
        )
    if rule.rule_type == "sticker_flood":
        n = _int(cfg.get("max_stickers_per_interval"), 10)
        return _per_window(
            lang,
            n,
            _int(cfg.get("interval_seconds"), 60),
            Tr(
                f"{n} {plural_ru(n, 'стикер', 'стикера', 'стикеров')} в чате",
                f"{n} stickers in the chat",
            ),
        )
    if rule.rule_type == "spam_detect":
        n = _int(cfg.get("max_messages"), 20)
        text = _per_window(
            lang,
            n,
            _int(cfg.get("time_window_seconds"), 60),
            Tr(
                f"{n} {plural_ru(n, 'сообщение', 'сообщения', 'сообщений')} одного человека",
                f"{n} messages from one person",
            ),
        )
        chars = _int(cfg.get("max_chars_per_message"), 4000)
        if chars > 0:
            text += Tr(" или сообщение от {c} символов", " or a message of {c} characters or more")(
                lang
            ).format(c=chars)
        return text
    return escape(rule.rule_type)


def _per_window(lang: str, _n: int, seconds: int, what: Tr) -> str:
    if seconds == 60:
        return f"{what(lang)} {Tr('за минуту', 'a minute')(lang)}"
    return f"{what(lang)} {Tr('за {s} с', 'in {s} s')(lang).format(s=seconds)}"


_REPLIES: Final = Tr("отвечает автору", "replies to the author")


def does(rule: RuleView, lang: str) -> str:
    """What a rule does when it fires, in words."""
    cfg = rule.config
    action = str(cfg.get("action", "notify_admin"))
    if action == "notify_admin":
        return Tr("пишет админу", "messages the admin")(lang)
    # One verb for both: the builder offers them as one choice, «Ответить автору».
    if action == "warn_user":
        text = cfg.get("warning_message")
        if not isinstance(text, str) or not text:
            return Tr(
                "отвечает автору стандартным предупреждением",
                "replies to the author with the default warning",
            )(lang)
        return f"{_REPLIES(lang)}: {_quote(text)}"
    if action == "custom_response":
        text = cfg.get("response_template")
        if not isinstance(text, str) or not text:
            # `_custom_response` sends nothing for an empty template.
            return Tr("ничего: текст ответа пуст", "nothing: the reply text is empty")(lang)
        return f"{_REPLIES(lang)}: {_quote(text)}"
    if action == "set_reaction":
        emoji = cfg.get("emoji")
        return f"{Tr('ставит реакцию', 'reacts with')(lang)} {escape(str(emoji or '—'))}"
    return Tr("ничего: неизвестное действие {a}", "nothing: unknown action {a}")(lang).format(
        a=f"<code>{escape(action)}</code>"
    )


def _fired(n: int, lang: str) -> str:
    if n <= 0:
        return Tr("Ещё не срабатывало", "Not fired yet")(lang)
    if lang == "en":
        return f"Fired: {n} {'time' if n == 1 else 'times'}"
    return f"Сработало: {n} {plural_ru(n, 'раз', 'раза', 'раз')}"


_COLUMNS: Final = ("type", "weight", "mandatory")
"""The record's keys that are ``custom_rules`` columns, not config: the JSON
prompt pops each of them before it stores the rest as the config."""


def rule_record(rule: RuleView) -> dict[str, Any]:
    """The rule as the JSON prompt reads it: ``type`` first, then the config,
    then ``weight`` and ``mandatory`` when they are not the defaults (1, false).

    Without ``type`` the prompt opened from «🧩 Настроить в JSON» took the paste
    for keywords, and a user or flood rule came back as a keyword rule with no
    words, which never fires.
    """
    record: dict[str, Any] = {"type": rule.rule_type}
    record.update((k, v) for k, v in rule.config.items() if k not in _COLUMNS)
    if rule.weight != 1:
        record["weight"] = rule.weight
    if rule.mandatory:
        record["mandatory"] = True
    return record


def config_json(config: Mapping[str, Any]) -> str:
    """A record, pretty-printed and escaped for ``<pre>``, cut to fit a message."""
    raw = json.dumps(dict(config), ensure_ascii=False, indent=2, default=str)
    if len(raw) > _JSON_SHOWN_CHARS:
        raw = raw[: _JSON_SHOWN_CHARS - 1] + _ELLIPSIS
    return escape(raw)


def rule_screen(
    lang: str,
    rule: RuleView,
    *,
    chat: ChatRules,
    show_json: bool = False,
    notice: str | None = None,
) -> Screen:
    """``rule``: one rule in words, with pause, delete and its stored form behind
    «🧩 Как записано (JSON)», captioned with what it is and what can be done with it."""
    if not rule.enabled:
        status = _PAUSED(lang)
    elif chat.enabled:
        status = _ON(lang)
    else:
        status = Tr("не работает", "not firing")(lang)
    lines = [
        f"📏 <b>{escape(rule_name(rule, lang))}</b> · {status}",
        "",
        f"{Tr('Ловит', 'Catches')(lang)}: {catches(rule, lang)}",
        f"{Tr('Делает', 'Does')(lang)}: {does(rule, lang)}",
        _fired(rule.trigger_count, lang),
    ]
    if chat.mode in MODES:
        required = Tr("да", "yes")(lang) if rule.mandatory else Tr("нет", "no")(lang)
        lines.append(
            f"{PRIORITY(lang).capitalize()}: {rule.weight} · {MANDATORY(lang)}: {required}"
        )
    if not chat.enabled:
        lines += [
            "",
            Tr(
                "Правила этого чата выключены, поэтому оно не сработает.",
                "Rules are off in this chat, so it will not fire.",
            )(lang),
            switch_hint(chat.enabled, lang),
        ]
        common = common_line(chat, lang)
        if common:
            lines.append(common)
    if show_json:
        caption = JSON_CAPTION(lang).format(add=_ADD(lang), cfg=_CFG_BUTTON(lang))
        lines += ["", caption, f"<pre>{config_json(rule_record(rule))}</pre>"]

    rows: list[tuple[Button, ...]] = []
    if not chat.enabled:
        rows.append((Button(master_label(chat, lang), cb("ar_all:", lang, rule.chat_id, rule.id)),))
    toggle = (Tr("⏸ На паузу", "⏸ Pause") if rule.enabled else Tr("▶ Возобновить", "▶ Resume"))(
        lang
    )
    rows.append(
        (
            Button(toggle, cb("ar_tog:", lang, rule.id, rule.chat_id, 0)),
            Button(
                Tr("🗑 Удалить", "🗑 Delete")(lang), cb("ar_del_ask:", lang, rule.id, rule.chat_id)
            ),
        )
    )
    rows.append(
        (
            Button(
                (JSON_HIDE if show_json else JSON_SHOW)(lang),
                cb("ar_view:", lang, rule.id, rule.chat_id, *(() if show_json else ("j",))),
            ),
        )
    )
    rows.append((back("rules", cb("ar_list:", lang, rule.chat_id, 0), lang),))
    return Screen(id="rule", text="\n".join(lines), rows=tuple(rows), notice=notice)


def delete_ask_screen(lang: str, rule: RuleView) -> Screen:
    """``rule.del_ask``: the one confirmation in the area — deleting cannot be undone."""
    return Screen(
        id="rule.del_ask",
        text=(
            f"🗑 <b>{Tr('Удалить правило?', 'Delete the rule?')(lang)}</b>\n\n"
            f"{escape(rule_name(rule, lang))}\n"
            f"{Tr('Вернуть будет нельзя.', 'It cannot be brought back.')(lang)}"
        ),
        rows=(
            (
                Button(
                    Tr("🗑 Удалить", "🗑 Delete")(lang), cb("ar_del:", lang, rule.id, rule.chat_id)
                ),
                back("rule", cb("ar_view:", lang, rule.id, rule.chat_id), lang, name=_KEEP(lang)),
            ),
        ),
    )


_KEEP: Final = Tr("Не удалять", "Keep it")
"""``rule.del_ask``'s way back says what it does: the rule stays."""


# ---------------------------------------------------------------------------
# The builder: rule.type → … → rule
# ---------------------------------------------------------------------------


def _list_cb(lang: str, chat_id: int) -> str:
    return cb("ar_list:", lang, chat_id, 0)


def type_screen(lang: str, chat_id: int) -> Screen:
    """``rule.type``: what the new rule reacts to."""

    def kind(label: Tr, token: str) -> Button:
        return Button(label(lang), cb("ar_b_type:", lang, chat_id, token))

    return Screen(
        id="rule.type",
        text=(
            f"➕ <b>{Tr('Новое правило', 'New rule')(lang)}</b>\n\n"
            f"{Tr('На что оно срабатывает?', 'What sets it off?')(lang)}"
        ),
        rows=(
            (kind(Tr("🔑 Ключевые слова", "🔑 Keywords"), "k"),),
            (kind(Tr("👤 Сообщения одного человека", "👤 One person's messages"), "u"),),
            (
                kind(Tr("🎭 Много стикеров", "🎭 Many stickers"), "f"),
                kind(Tr("🛡 Много сообщений", "🛡 Many messages"), "s"),
            ),
            (Button(_CFG_BUTTON(lang), cb("ar_cfg:", lang, chat_id)),),
            (back("rules", _list_cb(lang, chat_id), lang),),
        ),
    )


def _new_title(glyph: str, what: str, lang: str) -> str:
    return f"{glyph} <b>{_NEW(lang)}</b> {escape(what)}"


def words_screen(lang: str, chat_id: int) -> Screen:
    """``rule.words``: the words, typed."""
    return Screen(
        id="rule.words",
        text=(
            f"{_new_title('🔑', Tr('ключевые слова', 'keywords')(lang), lang)}\n\n"
            + Tr(
                "Напишите слова через запятую. До {n} слов.",
                "Type the words, separated by commas. Up to {n} words.",
            )(lang).format(n=MAX_WORDS)
        ),
        rows=((cancel(_list_cb(lang, chat_id), lang),),),
    )


def match_screen(lang: str, chat_id: int, words: Sequence[str]) -> Screen:
    """``rule.match``: a word anywhere in a message, or the message being just the word."""
    first = words[0] if words else ""
    example = Tr(
        "Слово внутри: «тут {w} и ещё текст»\nТолько слово: «{w}»",
        "Word inside: “there is {w} and more text”\nWord alone: “{w}”",
    )(lang).format(w=escape(first))
    return Screen(
        id="rule.match",
        text=(
            f"{_new_title('🔑', ', '.join(words), lang)}\n\n"
            f"{Tr('Когда срабатывать? Хватит любого из слов.', 'When should it fire? Any one of the words is enough.')(lang)}"
            f"\n\n{example}"
        ),
        rows=(
            (
                Button(
                    Tr("Слово внутри сообщения", "Word inside a message")(lang),
                    cb("ar_b_match:", lang, chat_id, "c"),
                ),
            ),
            (
                Button(
                    Tr("Только слово, без текста", "The word alone")(lang),
                    cb("ar_b_match:", lang, chat_id, "e"),
                ),
            ),
            (cancel(_list_cb(lang, chat_id), lang),),
        ),
    )


@dataclass(frozen=True)
class Person:
    user_id: int
    first_name: str | None = None
    username: str | None = None

    @property
    def display(self) -> str:
        """``Имя @ник``, or whichever of them exists, or the id."""
        parts = [p for p in (self.first_name, f"@{self.username}" if self.username else None) if p]
        return " ".join(parts) if parts else str(self.user_id)


FIND: Final = Tr("🔍 Найти в Telegram", "🔍 Find in Telegram")
"""Telegram's own person search (``request_users``): anyone, not only who has written."""
FIND_PLACEHOLDER: Final = Tr("Выберите человека", "Pick a person")


def who_screen(
    lang: str,
    chat_id: int,
    people: Sequence[Person],
    *,
    page: int = 0,
    pages: int = 1,
    find: bool = True,
) -> Screen:
    """``rule.who``: whose messages, picked from the chat's most active people.

    «🔍 Найти в Telegram» opens ``rule.who_find`` for anyone else, and is the
    way on when nobody has written in the chat yet. ``find=False`` drops it,
    for an app that sent the button's label back instead of opening the search.
    """
    if find:
        hint = Tr(
            "Чьи? Нажмите имя или найдите человека в Telegram.",
            "Whose? Tap a name or find the person in Telegram.",
        )
        nobody = Tr(
            "В этом чате пока никто не писал. Найдите человека в Telegram.",
            "Nobody has written in this chat yet. Find the person in Telegram.",
        )
    else:
        hint = Tr("Чьи? Нажмите имя.", "Whose? Tap a name.")
        nobody = Tr(
            "В этом чате пока никто не писал, а поиск в этом приложении Telegram не открывается.",
            "Nobody has written in this chat yet, and this Telegram app can't open the search.",
        )
    text = (hint if people else nobody)(lang)
    rows: list[tuple[Button, ...]] = [
        (
            Button(
                fence(fit(person.display, _room(1))),
                cb("ar_b_who:", lang, chat_id, person.user_id),
            ),
        )
        for person in people
    ]
    nav = pager(page, pages, lambda p: cb("ar_b_type:", lang, chat_id, "u", p))
    if nav is not None:
        rows.append(nav)
    way_back = cancel(_list_cb(lang, chat_id), lang)
    rows.append(
        (Button(FIND(lang), cb("ar_b_find:", lang, chat_id)), way_back) if find else (way_back,)
    )
    return Screen(
        id="rule.who",
        text=f"{_who_title(lang)}\n\n{text}",
        rows=tuple(rows),
    )


def _who_title(lang: str) -> str:
    return _new_title("👤", Tr("сообщения одного человека", "one person’s messages")(lang), lang)


def who_find_screen(lang: str) -> Screen:
    """``rule.who_find``: Telegram's person search under the input.

    A new message, since only a sent message can carry a reply keyboard. Its
    ``request_id`` is the rules picker's own, so a pick lands here and never
    in the knowledge base's organizer picker, and the other way round.
    """
    return Screen(
        id="rule.who_find",
        text=(
            f"{_who_title(lang)}\n\n"
            + Tr(
                "Нажмите «🔍 Найти в Telegram» внизу и выберите человека.",
                "Tap «🔍 Find in Telegram» below and pick the person.",
            )(lang)
        ),
        reply=(
            (ReplyButton(FIND(lang), RequestUsers(request_id=REQUEST_IDS.RULE_WHO)),),
            (ReplyButton(CANCEL(lang)),),
        ),
        placeholder=FIND_PLACEHOLDER(lang),
    )


WHO_PICKED: Final = Tr("Выбран человек: {name}", "Person picked: {name}")
WHO_CANCELLED: Final = Tr("Правило не создано.", "The rule was not created.")
WHO_STALE: Final = Tr(
    "Этот поиск уже закрыт. Начните правило заново.",
    "This search is closed. Start the rule again.",
)
WHO_UNSUPPORTED: Final = Tr(
    "Это приложение Telegram не открывает поиск.",
    "This Telegram app can't open the search.",
)
"""The short lines that take the reply keyboard away (a message carrying
``ReplyKeyboardRemove`` cannot carry inline buttons too)."""


def count_screen(lang: str, chat_id: int, kind: str) -> Screen:
    """``rule.flood`` / ``rule.spam``: how many in a minute is too many."""
    if kind == "f":
        screen_id, glyph = "rule.flood", "🎭"
        what = Tr("много стикеров", "many stickers")
        question = Tr(
            "Сколько стикеров в чате за минуту уже слишком?",
            "How many stickers in the chat in a minute is too many?",
        )
        choices, suggested = FLOOD_CHOICES, FLOOD_SUGGESTED
    else:
        screen_id, glyph = "rule.spam", "🛡"
        what = Tr("много сообщений", "many messages")
        question = Tr(
            "Сколько сообщений от одного человека за минуту уже слишком?",
            "How many messages from one person in a minute is too many?",
        )
        choices, suggested = SPAM_CHOICES, SPAM_SUGGESTED
    return Screen(
        id=screen_id,
        text=f"{_new_title(glyph, what(lang), lang)}\n\n{question(lang)}",
        rows=(
            tuple(
                Button(f"• {n}" if n == suggested else str(n), cb("ar_b_n:", lang, chat_id, n))
                for n in choices
            ),
            (cancel(_list_cb(lang, chat_id), lang),),
        ),
    )


_ACTION_CHOICES: Final = (
    (
        "n",
        Tr("🔔 Написать админу", "🔔 Message the admin"),
        Tr(
            "бот пришлёт админам бота в личку, кто и что написал, а в чате промолчит.",
            "the bot tells its admins in a private message who wrote what, "
            "and says nothing in the chat.",
        ),
    ),
    (
        "c",
        Tr("💬 Ответить автору", "💬 Reply to the author"),
        Tr(
            "бот ответит автору в чате: готовым текстом («{preset}») или вашим, "
            "можно с именем автора.",
            "the bot replies to the author in the chat: with a ready text («{preset}») "
            "or your own, which can name the author.",
        ),
    ),
    (
        "r",
        Tr("😀 Поставить реакцию", "😀 Add a reaction"),
        Tr(
            "бот поставит эмодзи на сообщение и ничего не напишет.",
            "the bot puts an emoji on the message and writes nothing.",
        ),
    ),
)
"""Builder action token, its button, and what the bot then sends, one line each.

A warning and a reply are one choice here. Both are the same plain-text reply
to the author (`RuleExecutor._warn_user`, `_custom_response`), and two buttons
for it read as "very similar" to all five readers of rounds 3 and 4. A ready
text is still stored as ``warn_user`` and a typed one as ``custom_response``
(its name placeholders need the template), so nothing stored changes meaning.
The ``w`` token is no longer offered but still answers: a keyboard sent before
this change keeps working."""


def action_screen(lang: str, chat_id: int, name: str) -> Screen:
    """``rule.action``: what the rule does, a line each. Messaging the admin needs nothing more."""
    said = [
        f"{label(lang)} — {what(lang).format(preset=WARN_PRESETS[0](lang))}"
        for _, label, what in _ACTION_CHOICES
    ]
    return Screen(
        id="rule.action",
        text=(
            f"{_new_title('📏', name, lang)}\n\n"
            + Tr(
                "Что сделать, когда сработает? Выберите одно. «Написать админу» сразу сохранит правило.",
                "What should happen when it fires? Pick one. “Message the admin” saves the rule at once.",
            )(lang)
            + "\n\n"
            + "\n".join(said)
        ),
        rows=(
            *(
                (Button(label(lang), cb("ar_b_act:", lang, chat_id, token)),)
                for token, label, _ in _ACTION_CHOICES
            ),
            (cancel(_list_cb(lang, chat_id), lang),),
        ),
    )


def text_screen(lang: str, chat_id: int, name: str, *, action: str) -> Screen:
    """``rule.text``: the reply to the author, a ready text or a typed one.

    ``warn_user`` is the screen the retired ⚠️ choice opened (still reachable
    from an old keyboard): the same ready texts, without the name placeholders.
    """
    title = _new_title("📏", name, lang)
    if action == "warn_user":
        body = Tr(
            "Напишите текст сообщением или нажмите готовый. Правило сразу сохранится.",
            "Type the text as a message or tap a ready one. The rule is saved at once.",
        )(lang)
    else:
        body = Tr(
            "Нажмите готовый текст или напишите свой сообщением. Правило сразу сохранится.\n"
            "В свой текст можно подставить: <code>{first_name}</code> — имя автора, "
            "<code>{username}</code> — его ник, <code>{rule_name}</code> — название правила.",
            "Tap a ready text or type your own as a message. The rule is saved at once.\n"
            "Your own text can carry: <code>{first_name}</code> — the author's name, "
            "<code>{username}</code> — their username, <code>{rule_name}</code> — the rule's name.",
        )(lang)
    rows: list[tuple[Button, ...]] = [
        (Button(preset(lang), cb("ar_b_txt:", lang, chat_id, i)),)
        for i, preset in enumerate(WARN_PRESETS)
    ]
    rows.append((cancel(_list_cb(lang, chat_id), lang),))
    return Screen(id="rule.text", text=f"{title}\n\n{body}", rows=tuple(rows))


def react_screen(lang: str, chat_id: int, name: str) -> Screen:
    """``rule.react``: five common reactions, or any other one typed."""
    buttons = [Button(emoji, cb("ar_b_rx:", lang, chat_id, i)) for i, emoji in enumerate(REACTIONS)]
    other = Button(
        Tr("✏️ Другая", "✏️ Other")(lang),
        cb("ar_b_act:", lang, chat_id, "ri"),
        goes_to="rule.react_in",
    )
    return Screen(
        id="rule.react",
        text=f"{_new_title('😀', name, lang)}\n\n{Tr('Какую реакцию ставить?', 'Which reaction?')(lang)}",
        rows=(
            tuple(buttons[:3]),
            (*buttons[3:], other),
            (cancel(_list_cb(lang, chat_id), lang),),
        ),
    )


def react_in_screen(lang: str, chat_id: int, name: str) -> Screen:
    """``rule.react_in``: one emoji, typed; only Telegram's reaction set is accepted."""
    return Screen(
        id="rule.react_in",
        text=(
            f"{_new_title('😀', name, lang)}\n\n"
            + Tr(
                "Пришлите эмодзи. Подойдёт только то, что Telegram разрешает как реакцию.",
                "Send an emoji. Only what Telegram allows as a reaction will do.",
            )(lang)
        ),
        rows=(
            (
                Button(
                    Tr("✖️ Отмена", "✖️ Cancel")(lang),
                    cb("ar_b_act:", lang, chat_id, "r"),
                    kind="cancel",
                    goes_to="rule.react",
                ),
            ),
        ),
    )


_CFG_EXAMPLE: Final = Tr(
    '{"name": "spam-words", "keywords": ["реклама", "купи"], "match_type": "contains", '
    '"action": "warn_user", "warning_message": "Давайте без спама"}',
    '{"name": "spam-words", "keywords": ["ad", "buy"], "match_type": "contains", '
    '"action": "warn_user", "warning_message": "Let\'s skip the spam"}',
)


def cfg_screen(lang: str, chat_id: int, rule_type: str | None = None) -> Screen:
    """``rule.cfg``: the whole rule as JSON, for what the buttons do not cover.

    Cancel goes back to the type step, which also ends the prompt: a refused answer
    keeps the prompt armed, so leaving it must not depend on sending valid JSON.
    """
    kind = (
        Tr("Тип: <code>{t}</code>.", "Type: <code>{t}</code>.")(lang).format(t=escape(rule_type))
        if rule_type
        else Tr(
            "Тип — в поле <code>type</code>: {types}; без него — ключевые слова.",
            "The type goes in <code>type</code>: {types}; without it, keywords.",
        )(lang).format(types=", ".join(f"<code>{t}</code>" for t in KINDS.values()))
    )
    actions = Tr("Действия: {a}.", "Actions: {a}.")(lang).format(
        a=", ".join(f"<code>{a}</code>" for a in ACTIONS.values())
    )
    return Screen(
        id="rule.cfg",
        text=(
            f"🧩 <b>{Tr('Правило в JSON', 'Rule in JSON')(lang)}</b>\n\n"
            + Tr(
                "Для того, чего нет в кнопках: регулярные выражения, исключения, "
                "{priority} (<code>weight</code>), {mandatory} правило "
                "(<code>mandatory</code>). Пример:",
                "For what the buttons do not cover: regular expressions, exclusions, "
                "{priority} (<code>weight</code>), a {mandatory} rule (<code>mandatory</code>). "
                "Example:",
            )(lang).format(priority=PRIORITY(lang), mandatory=MANDATORY(lang))
            + f"\n<code>{escape(_CFG_EXAMPLE(lang))}</code>\n\n{kind}\n{actions}"
        ),
        rows=(
            (
                Button(
                    Tr("✖️ Отмена", "✖️ Cancel")(lang),
                    cb("ar_add:", lang, chat_id),
                    kind="cancel",
                ),
            ),
        ),
    )


CFG_NOT_JSON: Final = Tr("Невалидный JSON. Попробуйте ещё раз.", "Invalid JSON. Please try again.")
CFG_BAD_TYPE: Final = Tr(
    "Неизвестный тип <code>{t}</code>. Доступные: {valid}. Попробуйте ещё раз.",
    "Unknown type <code>{t}</code>. Available: {valid}. Please try again.",
)
CFG_BAD_ACTION: Final = Tr(
    "Неизвестное действие <code>{action}</code>. Доступные: {valid}.",
    "Unknown action <code>{action}</code>. Available: {valid}.",
)
CFG_BAD_EMOJI: Final = Tr(
    "Для <code>set_reaction</code> поле <code>emoji</code> должно быть "
    "одним из стандартных эмодзи-реакций Telegram (например 💊, 🔥, 🤡). "
    "Попробуйте ещё раз.",
    "For <code>set_reaction</code>, <code>emoji</code> must be one of "
    "Telegram's standard reaction emoji (e.g. 💊, 🔥, 🤡). "
    "Please try again.",
)
WORDS_NONE: Final = Tr(
    "Слов не нашлось. Напишите их через запятую.", "No words found. Type them separated by commas."
)
WORDS_TOO_MANY: Final = Tr(
    "Слов больше {n}. Оставьте до {n} и отправьте ещё раз.",
    "More than {n} words. Keep up to {n} and send again.",
)
WORDS_TOO_LONG: Final = Tr(
    "Слово длиннее {n} символов: {w}. Попробуйте ещё раз.",
    "A word is longer than {n} characters: {w}. Please try again.",
)


def text_too_long(lang: str, over: int, *, filled: bool = False) -> str:
    """The text, as the rule would send it, does not fit one Telegram message.

    ``over`` is how much to cut, in Telegram's own count. ``filled`` says the
    overflow comes from a name the rule puts in, not from the text as typed.
    """
    if lang == "ru":
        unit = plural_ru(over, "знак", "знака", "знаков")
        head = (
            "С подставленным именем текст может не поместиться в одно сообщение Telegram."
            if filled
            else "Текст не поместится в одно сообщение Telegram."
        )
        return f"{head} Сократите его на {over} {unit} и отправьте ещё раз."
    unit = "character" if over == 1 else "characters"
    head = (
        "With a name filled in, the text may not fit one Telegram message."
        if filled
        else "The text does not fit one Telegram message."
    )
    return f"{head} Shorten it by {over} {unit} and send again."


CFG_NO_KEYWORDS: Final = Tr(
    "В правиле на слова нужно поле <code>keywords</code> со списком слов, например "
    '<code>"keywords": ["привет"]</code>: без него правило не сработает. Попробуйте ещё раз.',
    "A keyword rule needs a <code>keywords</code> field with a list of words, e.g. "
    '<code>"keywords": ["hello"]</code>: without it the rule never fires. Please try again.',
)
"""The engine's `_eval_keyword` reads ``keywords`` and nothing else: no list, an
empty one, or an empty word (``""`` is in every text) is a rule that never
fires, or fires on everything."""
CFG_NOT_TEXT: Final = Tr(
    "Поле <code>{key}</code> должно быть текстом. Попробуйте ещё раз.",
    "The <code>{key}</code> field must be text. Please try again.",
)
TEXT_EMPTY: Final = Tr(
    "Нужен текст. Напишите его сообщением.", "Text is needed. Type it as a message."
)
EMOJI_BAD: Final = Tr(
    "Такую реакцию Telegram не разрешает. Пришлите другой эмодзи.",
    "Telegram does not allow that reaction. Send another emoji.",
)


def error_screen(screen_id: str, text: str, cancel_cb: str, lang: str) -> Screen:
    """A typed answer that cannot be used: the reason, and the way out.

    Starts with a letter, not a glyph: it is an error, not a screen. The prompt
    stays armed, so the next message is a new try.
    """
    return Screen(id=screen_id, text=text, rows=((cancel(cancel_cb, lang),),))


# ---------------------------------------------------------------------------
# What a fired rule sends
# ---------------------------------------------------------------------------


def _match_line(detail: str, lang: str) -> str | None:
    kind, _, rest = detail.partition(":")
    if kind in ("contains", "exact", "regex"):
        return f"{Tr('Совпало', 'Matched')(lang)}: {escape(rest)}"
    if kind == "user":
        _, _, keyword = rest.partition(",kw:")
        return f"{Tr('Совпало', 'Matched')(lang)}: {escape(keyword)}" if keyword else None
    if kind == "stickers":
        count, _, limit = rest.partition("/")
        return Tr("Стикеров: {n}, порог {m}", "Stickers: {n}, limit {m}")(lang).format(
            n=escape(count), m=escape(limit)
        )
    if kind == "spam":
        what, _, numbers = rest.partition("=")
        count, _, limit = numbers.partition("/")
        label = (
            Tr("Длина сообщения: {n}, порог {m}", "Message length: {n}, limit {m}")
            if what == "chars"
            else Tr("Сообщений: {n}, порог {m}", "Messages: {n}, limit {m}")
        )
        return label(lang).format(n=escape(count), m=escape(limit))
    return escape(detail) if detail else None


def rule_card(
    lang: str,
    *,
    rule_id: int,
    chat_id: int,
    name: str,
    chat_title: str | None,
    first_name: str | None,
    username: str | None,
    match_detail: str,
    excerpt: str,
) -> Screen:
    """``card.rule``: sent to each admin when a ``notify_admin`` rule fires.

    Its button opens the rule as a new message (``n``): the card stays as the
    record of what fired, instead of being edited into the rule's screen.
    """
    where = f"<code>{chat_id_text(chat_id)}</code>"
    lines = [
        f"📏 <b>{Tr('Сработало правило:', 'Rule fired:')(lang)}</b> {escape(name)}",
        "",
        f"{Tr('Чат', 'Chat')(lang)}: {escape(chat_title) + ' ' if chat_title else ''}{where}",
    ]
    who = " ".join(
        p for p in (escape(first_name or ""), f"@{escape(username)}" if username else "") if p
    )
    if who:
        lines.append(f"👤 {who}")
    match = _match_line(match_detail, lang)
    if match:
        lines.append(match)
    if excerpt:
        cut = excerpt if len(excerpt) <= _EXCERPT_CHARS else excerpt[:_EXCERPT_CHARS] + _ELLIPSIS
        lines.append(f"💬 {escape(cut)}")
    return Screen(
        id="card.rule",
        text="\n".join(lines),
        rows=(
            (
                Button(
                    Tr("📏 Открыть правило", "📏 Open the rule")(lang),
                    cb("ar_view:", lang, rule_id, chat_id, "n"),
                ),
            ),
        ),
    )


def warn_screen(lang: str) -> Screen:
    """``rules.warn``: the default warning, posted in the chat as plain text."""
    return Screen(id="rules.warn", text=WARN_FALLBACK(lang))
