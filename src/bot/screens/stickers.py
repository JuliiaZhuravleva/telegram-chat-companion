"""Sticker catalogue screens (``stk.*``) and the bot's new-sticker card (``card.sticker``).

Pure builders: ``build_<id>(lang, data) -> Screen``. Nothing here touches the
database or Telegram; `handlers/admin_sticker.py` fetches the rows, turns them
into `StickerFacts` / `PackRow` with the helpers below and calls a builder.
`StickerLearningService.notify_admins` builds ``card.sticker`` the same way,
so every place a sticker is described renders it with ONE function
(`_facts_lines`).

A pack is addressed by `pack_key` — the first 12 hex digits of md5 over its
name — because a pack name alone can push a payload past Telegram's 64 bytes.
The repository finds it again with ``left(md5(set_name), 12)``.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from src.bot.custom_emoji import fence
from src.bot.screens.i18n import Lang, Tr, plural_ru
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import CANCEL, back, cancel, pager
from src.bot.screens.routes import cb
from src.services.modules.sticker.tolerance import is_within_tolerance

PRESETS: Final[tuple[float, ...]] = (0.0, 0.25, 0.5, 0.75, 1.0)
"""One-tap explicitness values. The INDEX rides in ``adm_stk_expset:`` — shorter
than the float and immune to formatting drift between button and handler."""

PER_PAGE: Final = 4
"""Rows per list page: four items + pager + back is the six-row ceiling."""

PACK_LABEL_CHARS: Final = 24
ROW_LABEL_CHARS: Final = 22
TITLE_CHARS: Final = 40
DESCRIPTION_CHARS: Final = 1000
NOTES_CHARS: Final = 600
CONTEXTS_SHOWN: Final = 5

DEFAULT_EMOJI: Final = "🖼"

ErrorReason = Literal["download", "vision", "content_filter", "empty", "internal"]
ReplyKind = Literal["ok", "note_saved", "content_filter", "failed", "unknown"]

# ── Copy ────────────────────────────────────────────────────────────────

_SETS_TITLE = Tr("Паки стикеров", "Sticker packs")
_SETS_SOME = Tr(
    "Бот выучил стикеры из {n} {packs}. В скобках: разобрано из всего.",
    "The bot has learned stickers from {n} {packs}. In brackets: analysed out of all.",
)
_SETS_NONE = Tr("Бот ещё не выучил ни одного пака.", "The bot has not learned any pack yet.")
_SETS_CHECK = Tr(
    "Чтобы проверить один стикер, пришлите его сюда.",
    "To check one sticker, send it here.",
)
_OF = Tr("из", "of")

_SET_COUNT = Tr("{n} {stickers}", "{n} {stickers}")
_SET_HINT = Tr("Справа: сколько раз использовали.", "On the right: how many times it was used.")
_SET_EMPTY = Tr(
    "Бот ещё не выучил ни одного стикера из этого пака.",
    "The bot has not learned any sticker from this pack yet.",
)
_ROW_FAILED = Tr("Ошибка разбора", "Analysis failed")
_ROW_NEW = Tr("Ещё не разобран", "Not analysed yet")

_EMOTION = Tr("Эмоция", "Emotion")
_CHARACTER = Tr("Персонаж", "Character")
_FITS = Tr("Когда уместен", "Fits")
_PACK = Tr("Пак", "Pack")
_NOTES = Tr("Заметки", "Notes")
_TAGS = Tr("Теги", "Tags")
_EMOJI = Tr("Эмодзи", "Emoji")
_EMBEDDING = Tr("Эмбеддинг", "Embedding")
_USED_NEVER = Tr("Использован: ещё ни разу", "Used: not yet")
_USED = Tr("Использован: {n} {times}, ботом {b}", "Used: {n} {times}, {b} by the bot")
_SCORE = Tr("Откровенность", "Explicitness")
_SCORE_NONE = Tr("не оценена · бот его не отправит", "not rated · the bot will not send it")
_PASSES = Tr("пройдёт в чате с допуском {t}", "passes in a chat that allows {t}")
_BLOCKED = Tr("не пройдёт в чате с допуском {t}", "blocked in a chat that allows {t}")
_MANUAL = Tr(" (вручную)", " (set by hand)")
_CARD_HINT = Tr(
    "<i>Ответьте на это сообщение текстом, чтобы уточнить описание.</i>",
    "<i>Reply to this message with text to refine the description.</i>",
)
_NOTIFY_HINT = Tr(
    "<i>Ответьте текстом, чтобы уточнить описание.</i>",
    "<i>Reply with text to refine the description.</i>",
)
_NOTIFY_TITLE = Tr("Новый стикер:", "New sticker:")
_FAILED_BODY = Tr(
    "Прошлый разбор не удался. Его можно запустить заново.",
    "The last analysis failed. You can run it again.",
)
_NEW_BODY = Tr(
    "Бот ещё не разбирал этот стикер, поэтому не знает, когда его отправлять.",
    "The bot has not analysed this sticker yet, so it does not know when to send it.",
)

_REANALYZE = Tr("🔁 Разобрать заново", "🔁 Re-analyse")
_ANALYZE = Tr("🔁 Разобрать", "🔁 Analyse")
_CLEAR = Tr("🧹 Очистить", "🧹 Clear")
_SCORE_BTN = Tr("🔞 Откровенность", "🔞 Explicitness")
_RETRY = Tr("🔁 Повторить", "🔁 Retry")
_DM_ANALYZE = Tr("🔍 Разобрать", "🔍 Analyse")

_SCORE_NOW_AUTO = Tr("Сейчас {v}, посчитал бот.", "Now {v}, set by the bot.")
_SCORE_NOW_MANUAL = Tr("Сейчас {v}, задано вручную.", "Now {v}, set by hand.")
_SCORE_NOW_NONE = Tr(
    "Оценки пока нет, поэтому бот его не отправит.",
    "Not rated yet, so the bot will not send it.",
)
_SCORE_HOW = Tr(
    "Чем выше, тем меньше чатов его увидят.",
    "The higher it is, the fewer chats will see it.",
)
_SCORE_RESCORE_NOTE = Tr(
    "«Пересчитать оценку» разберёт стикер заново: оценку посчитает бот, описание тоже обновится.",
    "“Re-score” analyses the sticker again: the bot sets the score, and the description "
    "is refreshed too.",
)
_CUSTOM = Tr("✏️ Своё число", "✏️ Custom")
_RESCORE = Tr("🔁 Пересчитать оценку", "🔁 Re-score")

_SCORE_IN_ASK = Tr("Напишите число от 0.0 до 1.0.", "Send a number from 0.0 to 1.0.")
_SCORE_IN_BAD = Tr(
    "⚠️ Нужно число от 0.0 до 1.0, например 0.3.",
    "⚠️ It has to be a number from 0.0 to 1.0, for example 0.3.",
)
_SCORE_IN_NOT_TEXT = Tr(
    "⚠️ Сюда нужно число текстом. Чтобы выйти, нажмите «{cancel}» или отправьте /cancel.",
    "⚠️ Send the number as text. To leave, tap “{cancel}” or send /cancel.",
)

_CLEAR_TITLE = Tr("Очистить разбор?", "Clear the analysis?")
_CLEAR_BODY = Tr(
    "Описание, эмоция, персонаж и контексты сбросятся. Заметки и счётчики останутся.",
    "The description, emotion, character and contexts are reset. Notes and counters stay.",
)
_CLEAR_KEEP = Tr("Не очищать", "Keep it")

_ANALYZING = Tr("Разбираю стикер…", "Analysing the sticker…")
_ANALYZING_BODY = Tr("Это займёт несколько секунд.", "This takes a few seconds.")

_ERROR_TITLE = Tr("Не получилось разобрать стикер", "Could not analyse the sticker")
_ERROR_NOTES_KEPT = Tr("Заметки на месте.", "Notes are kept.")
_ERROR_SCORE_KEPT = Tr("Оценка осталась прежней: {v}.", "The score is unchanged: {v}.")
_ERROR_SCORE_LOST = Tr(
    "Оценка сброшена, и пока её нет, бот этот стикер не отправит. "
    "Задайте её вручную: 🔞 Откровенность.",
    "The score was reset, and until it has one the bot will not send this sticker. "
    "Set it by hand: 🔞 Explicitness.",
)
ERROR_REASONS: Final[Mapping[str, Tr]] = {
    "download": Tr(
        "Не получилось скачать стикер из Telegram.",
        "Could not download the sticker from Telegram.",
    ),
    "vision": Tr("Модель не ответила.", "The model did not answer."),
    "content_filter": Tr(
        "Модель отказалась его разбирать из-за содержания. "
        "Оценку откровенности можно задать вручную: 🔞 Откровенность.",
        "The model refused to analyse it because of its content. "
        "You can set the explicitness score by hand: 🔞 Explicitness.",
    ),
    "empty": Tr("Модель вернула пустой ответ.", "The model returned an empty answer."),
    "internal": Tr(
        "Что-то пошло не так на стороне бота.",
        "Something went wrong on the bot's side.",
    ),
}

_UNKNOWN_TITLE = Tr("Новый стикер", "New sticker")
_UNKNOWN_BODY = Tr(
    "Его ещё нет в базе. Бот разберёт его по кнопке ниже.",
    "It is not in the catalogue yet. The bot analyses it when you tap the button below.",
)

_REPLY_OK = Tr("Описание обновлено", "Description updated")
_REPLY_NOT = Tr("Описание не обновлено", "Description not updated")
_REPLY_BODY: Final[Mapping[str, Tr]] = {
    "note_saved": Tr(
        "Не получилось объединить описание, заметка сохранена. "
        "Попробуйте ещё раз или нажмите «🔁 Разобрать заново» на карточке.",
        "Could not merge the description; the note is saved. "
        "Try again or tap “🔁 Re-analyse” on the card.",
    ),
    "content_filter": Tr(
        "Модель отказалась объединять описание из-за содержания, заметка сохранена. "
        "Оценку откровенности можно задать вручную: 🔞 Откровенность.",
        "The model refused to merge the description because of its content; the note is "
        "saved. You can set the explicitness score by hand: 🔞 Explicitness.",
    ),
    "failed": Tr(
        "Не получилось обновить описание. Попробуйте ещё раз.",
        "Could not update the description. Try again.",
    ),
    # The reply's target is neither a remembered card nor one that names its
    # sticker: answered, so the correction is not dropped in silence.
    "unknown": Tr(
        "Не получилось понять, о каком стикере речь. "
        "Ответьте на карточку стикера или пришлите сам стикер.",
        "Could not tell which sticker this is about. "
        "Reply to the sticker's card or send the sticker itself.",
    ),
}

NOTICE_SCORE = Tr("Откровенность {v}", "Explicitness {v}")
NOTICE_CLEARED = Tr("Разбор очищен", "Analysis cleared")
NOTICE_ANALYZED = Tr("Разбор обновлён", "Analysis updated")
NOTICE_LEARNED = Tr("Стикер разобран", "Sticker analysed")
NOTICE_RESCORED = Tr("Оценка пересчитана", "Score recalculated")
_RESCORE_NO_SCORE = Tr("Модель не вернула оценку.", "The model returned no score.")

# "Object gone" is not a stale button: the parent screen says what happened,
# without the ✓ a notice carries (nothing was done).
GONE_STICKER = Tr(
    "<i>Этого стикера уже нет в базе.</i>",
    "<i>This sticker is no longer in the catalogue.</i>",
)
GONE_PACK = Tr(
    "<i>Этого пака уже нет в списке.</i>",
    "<i>This pack is no longer in the list.</i>",
)
ALERT_RESEND = Tr(
    "Стикер недоступен, пришлите его ещё раз",
    "The sticker is unavailable, send it again",
)


# ── Data ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PackRow:
    """One line of ``stk.sets``."""

    key: str
    title: str
    analyzed: int
    total: int


@dataclass(frozen=True, slots=True)
class StickerFacts:
    """Everything a sticker screen may say about one sticker."""

    fuid: str
    emoji: str | None = None
    description: str | None = None
    emotion: str | None = None
    character: str | None = None
    contexts: tuple[str, ...] = ()
    style_tags: tuple[str, ...] = ()
    total_uses: int = 0
    bot_uses: int = 0
    score: float | None = None
    score_manual: bool = False
    failed: bool = False
    notes: str | None = None
    set_name: str | None = None
    pack_key: str | None = None
    """Set only when the pack is registered, i.e. ``stk.set`` can open it."""
    pack_title: str | None = None
    has_embedding: bool | None = None
    tolerance: float = 0.5
    detailed: bool = False
    """The admin chose detailed sticker notifications: tags, emoji, embedding, id."""

    @property
    def analyzed(self) -> bool:
        return bool(self.description) and not self.failed


@dataclass(frozen=True, slots=True)
class SetsPage:
    packs: tuple[PackRow, ...]
    total: int
    page: int = 0
    pages: int = 1


@dataclass(frozen=True, slots=True)
class PackPage:
    key: str
    title: str
    count: int
    stickers: tuple[StickerFacts, ...]
    page: int = 0
    pages: int = 1


# ── Helpers ─────────────────────────────────────────────────────────────


def pack_key(set_name: str) -> str:
    """12 hex digits of md5 over the pack name; SQL twin: ``left(md5(set_name), 12)``."""
    return hashlib.md5(set_name.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def clip(text: str, limit: int) -> str:
    """Collapse whitespace and cut to ``limit`` characters with ``…``, at a word
    boundary when one falls in the second half."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:—–-") + "…"


_CLAUSE_RE: Final = re.compile(r"[.,;:!?(—–]|\s-\s")


def card_title(description: str, limit: int = TITLE_CHARS) -> str:
    """The first clause of a description, at most ``limit`` characters."""
    text = description.strip()
    first = _CLAUSE_RE.split(text, maxsplit=1)[0].strip() or text
    return clip(first, limit)


def _title_of(facts: StickerFacts, lang: str) -> str:
    if facts.failed:
        return _ROW_FAILED(lang)
    if facts.description:
        return card_title(facts.description)
    if facts.character:
        return clip(facts.character, TITLE_CHARS)
    return _ROW_NEW(lang)


def _emoji(facts: StickerFacts) -> str:
    return (facts.emoji or "").strip() or DEFAULT_EMOJI


def _score(value: float) -> str:
    return f"{value:.2f}"


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(item) for item in value if item)


def facts_from_row(
    row: Mapping[str, Any],
    *,
    tolerance: float = 0.5,
    detailed: bool = False,
    pack: Mapping[str, Any] | None = None,
) -> StickerFacts:
    """`StickerFacts` from a ``sticker_knowledge`` row (and its pack row, if registered).

    Reads with ``.get`` so the narrower rows of ``get_stickers_in_set`` work too.
    """
    pack_name = str(pack["set_name"]) if pack and pack.get("set_name") else None
    pack_title = None
    if pack_name is not None and pack is not None:
        pack_title = str(pack.get("set_title") or pack_name)
    embedding = row.get("description_embedding") if "description_embedding" in row else None
    score = row.get("explicitness_score")
    return StickerFacts(
        fuid=str(row.get("file_unique_id") or ""),
        emoji=row.get("emoji"),
        description=row.get("visual_description"),
        emotion=row.get("emotion"),
        character=row.get("character_or_meme"),
        contexts=_as_tuple(row.get("suggested_contexts")),
        style_tags=_as_tuple(row.get("style_tags")),
        total_uses=int(row.get("total_uses") or 0),
        bot_uses=int(row.get("bot_uses") or 0),
        score=float(score) if score is not None else None,
        score_manual=bool(row.get("explicitness_is_manual") or False),
        failed=bool(row.get("analysis_failed") or False),
        notes=row.get("admin_notes"),
        set_name=row.get("set_name"),
        pack_key=pack_key(pack_name) if pack_name else None,
        pack_title=pack_title,
        has_embedding=(embedding is not None) if "description_embedding" in row else None,
        tolerance=tolerance,
        detailed=detailed,
    )


def score_line(facts: StickerFacts, lang: str) -> str:
    """``Откровенность: 0.25 · пройдёт в чате с допуском 0.50`` (or "not rated").

    Pass/fail is ``is_within_tolerance`` itself — the one comparison the
    sticker search applies (in SQL), so the card cannot disagree with it.
    """
    if facts.score is None:
        return f"{_SCORE(lang)}: {_SCORE_NONE(lang)}"
    verdict = _PASSES if is_within_tolerance(facts.score, facts.tolerance) else _BLOCKED
    manual = _MANUAL(lang) if facts.score_manual else ""
    return f"{_SCORE(lang)}: {_score(facts.score)}{manual} · {verdict(lang).format(t=_score(facts.tolerance))}"


def _used_line(facts: StickerFacts, lang: str) -> str:
    if facts.total_uses <= 0:
        return _USED_NEVER(lang)
    n = facts.total_uses
    times = plural_ru(n, "раз", "раза", "раз") if lang != "en" else ("time" if n == 1 else "times")
    return _USED(lang).format(n=n, times=times, b=facts.bot_uses)


def _facts_lines(facts: StickerFacts, lang: str, *, pack_line: bool, used: bool) -> list[str]:
    """The fact block every sticker card shares, already HTML-escaped."""
    esc = html.escape
    lines: list[str] = []
    if facts.emotion:
        lines.append(f"{_EMOTION(lang)}: {esc(facts.emotion)}")
    if facts.character:
        lines.append(f"{_CHARACTER(lang)}: {esc(facts.character)}")
    if facts.contexts and (used or facts.detailed):
        joined = ", ".join(facts.contexts[:CONTEXTS_SHOWN])
        lines.append(f"{_FITS(lang)}: {esc(joined)}")
    if pack_line and facts.set_name:
        lines.append(f"{_PACK(lang)}: {esc(facts.pack_title or facts.set_name)}")
    if used:
        lines.append(_used_line(facts, lang))
    lines.append(score_line(facts, lang))
    if facts.notes:
        lines.append(f"{_NOTES(lang)}: <i>{esc(clip(facts.notes, NOTES_CHARS))}</i>")
    if facts.detailed:
        if facts.style_tags:
            lines.append(f"{_TAGS(lang)}: {esc(', '.join(facts.style_tags))}")
        if facts.emoji:
            lines.append(f"{_EMOJI(lang)}: {esc(facts.emoji)}")
        if facts.has_embedding is not None:
            lines.append(f"{_EMBEDDING(lang)}: {'✅' if facts.has_embedding else '❌'}")
        lines.append(f"🆔 <code>{esc(facts.fuid)}</code>")
    return lines


def _card_rows(facts: StickerFacts, lang: str) -> list[tuple[Button, ...]]:
    """``[🔁 Разобрать заново | 🧹 Очистить] [🔞 Откровенность]`` — shared by both cards."""
    if facts.description or facts.failed:
        first: tuple[Button, ...] = (
            Button(_REANALYZE(lang), cb("adm_stk_reanalyze:", lang, facts.fuid)),
            Button(_CLEAR(lang), cb("adm_stk_clr_ask:", lang, facts.fuid)),
        )
    else:
        first = (Button(_ANALYZE(lang), cb("adm_stk_reanalyze:", lang, facts.fuid)),)
    return [first, (Button(_SCORE_BTN(lang), cb("adm_stk_score:", lang, facts.fuid)),)]


def _card_back(facts: StickerFacts, lang: str) -> Button:
    """To the sticker's pack; a sticker without a registered pack goes to the pack list."""
    if facts.pack_key:
        name = clip(facts.pack_title or facts.set_name or facts.pack_key, PACK_LABEL_CHARS)
        return back("stk.set", cb("adm_stk_pk:", lang, facts.pack_key, 0), lang, name=name)
    return back("stk.sets", cb("adm_stk_sets:", lang, 0), lang)


# ── Builders ────────────────────────────────────────────────────────────


def build_stk_sets(
    lang: Lang,
    data: SetsPage,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
) -> Screen:
    """The pack list: ``Тестовый пак (12 из 20)`` per row, most-learned first.

    ``notice_raw``: `GONE_STICKER` / `GONE_PACK` when a button pointed at
    something that no longer exists."""
    if data.total:
        packs = plural_ru(data.total, "пака", "паков", "паков")
        if lang == "en":
            packs = "pack" if data.total == 1 else "packs"
        body = _SETS_SOME(lang).format(n=data.total, packs=packs)
    else:
        body = _SETS_NONE(lang)
    rows: list[tuple[Button, ...]] = [
        (
            Button(
                fence(f"{clip(p.title, PACK_LABEL_CHARS)} ({p.analyzed} {_OF(lang)} {p.total})"),
                cb("adm_stk_pk:", lang, p.key, 0),
            ),
        )
        for p in data.packs
    ]
    nav = pager(data.page, data.pages, lambda p: cb("adm_stk_sets:", lang, p))
    if nav:
        rows.append(nav)
    rows.append((back("hub", cb("adm_menu:", lang), lang),))
    return Screen(
        id="stk.sets",
        text=f"📦 <b>{_SETS_TITLE(lang)}</b>\n\n{body} {_SETS_CHECK(lang)}",
        rows=tuple(rows),
        notice=notice,
        notice_raw=notice_raw,
    )


def sticker_row_label(facts: StickerFacts, lang: str) -> str:
    """``😺 Кот в очках машет… · 12`` — the clipped first clause and the use count."""
    if facts.failed:
        what = _ROW_FAILED(lang)
    elif facts.description:
        what = card_title(facts.description, ROW_LABEL_CHARS)
    else:
        what = _ROW_NEW(lang)
    return fence(f"{_emoji(facts)} {what} · {facts.total_uses}")


def build_stk_set(lang: Lang, data: PackPage, *, notice: str | None = None) -> Screen:
    """One pack: a row per sticker (most used first), pager, back to the pack list."""
    if lang == "en":
        stickers = "sticker" if data.count == 1 else "stickers"
    else:
        stickers = plural_ru(data.count, "стикер", "стикера", "стикеров")
    head = f"📦 <b>{html.escape(data.title)}</b> · {_SET_COUNT(lang).format(n=data.count, stickers=stickers)}"
    body = _SET_HINT(lang) if data.stickers else _SET_EMPTY(lang)
    rows: list[tuple[Button, ...]] = [
        (Button(sticker_row_label(s, lang), cb("adm_stk_view:", lang, s.fuid)),)
        for s in data.stickers
    ]
    nav = pager(data.page, data.pages, lambda p: cb("adm_stk_pk:", lang, data.key, p))
    if nav:
        rows.append(nav)
    rows.append((back("stk.sets", cb("adm_stk_sets:", lang, 0), lang),))
    return Screen(id="stk.set", text=f"{head}\n\n{body}", rows=tuple(rows), notice=notice)


def rescore_without_score(lang: Lang, *, kept: float | None, lost: bool) -> str:
    """The card's top line after a re-score whose analysis gave no usable score.

    No ✓: the score was not recalculated. ``kept``: the score the re-score had
    dropped was put back (and is this value); ``lost``: putting it back failed
    too, so the sticker is unscored and the bot will not send it.
    """
    line = _RESCORE_NO_SCORE(lang)
    if lost:
        line += f" {_ERROR_SCORE_LOST(lang)}"
    elif kept is not None:
        line += f" {_ERROR_SCORE_KEPT(lang).format(v=_score(kept))}"
    return f"<i>{line}</i>"


def build_stk_card(
    lang: Lang,
    facts: StickerFacts,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
) -> Screen:
    """A sticker in the catalogue: title, full description, facts, actions, back to its pack.

    ``notice_raw``: a trusted, already-formatted top line instead of the ✓ notice
    (`rescore_without_score`).
    """
    title = _title_of(facts, lang)
    lines = [f"{fence(_emoji(facts))} <b>{html.escape(title)}</b>"]
    if facts.failed:
        lines += ["", _FAILED_BODY(lang)]
    elif not facts.description:
        lines += ["", _NEW_BODY(lang)]
    else:
        full = clip(facts.description, DESCRIPTION_CHARS)
        if full != title:
            lines += ["", fence(html.escape(full))]
    lines += ["", *_facts_lines(facts, lang, pack_line=False, used=True), "", _CARD_HINT(lang)]
    rows = _card_rows(facts, lang)
    rows.append((_card_back(facts, lang),))
    return Screen(
        id="stk.card",
        text="\n".join(lines),
        rows=tuple(rows),
        notice=notice,
        notice_raw=notice_raw,
    )


def build_card_sticker(lang: Lang, facts: StickerFacts) -> Screen:
    """The bot's card about a sticker it has just learned in a chat; no back."""
    description = clip(facts.description or "", DESCRIPTION_CHARS)
    lines = [
        f"{fence(_emoji(facts))} <b>{_NOTIFY_TITLE(lang)}</b> {html.escape(description)}".rstrip()
    ]
    lines += ["", *_facts_lines(facts, lang, pack_line=True, used=False), "", _NOTIFY_HINT(lang)]
    return Screen(id="card.sticker", text="\n".join(lines), rows=tuple(_card_rows(facts, lang)))


def build_stk_score(lang: Lang, facts: StickerFacts, *, notice: str | None = None) -> Screen:
    """Explicitness: five presets (the current one marked), a custom number, re-score."""
    if facts.score is None:
        now = _SCORE_NOW_NONE(lang)
    else:
        now = (_SCORE_NOW_MANUAL if facts.score_manual else _SCORE_NOW_AUTO)(lang).format(
            v=_score(facts.score)
        )
    title = html.escape(_title_of(facts, lang))
    text = (
        f"🔞 <b>{_SCORE(lang)}</b> · {title}\n\n{now} {_SCORE_HOW(lang)}\n\n"
        f"{_SCORE_RESCORE_NOTE(lang)}"
    )

    def preset(idx: int) -> Button:
        value = PRESETS[idx]
        current = facts.score is not None and abs(facts.score - value) < 1e-9
        label = fence(f"• {_score(value)}") if current else _score(value)
        return Button(label, cb("adm_stk_expset:", lang, facts.fuid, idx))

    rows: tuple[tuple[Button, ...], ...] = (
        (preset(0), preset(1), preset(2)),
        (preset(3), preset(4), Button(_CUSTOM(lang), cb("adm_stk_expedit:", lang, facts.fuid))),
        (Button(_RESCORE(lang), cb("adm_stk_expreset:", lang, facts.fuid)),),
        (back("stk.card", cb("adm_stk_card:", lang, facts.fuid), lang),),
    )
    return Screen(id="stk.score", text=text, rows=rows, notice=notice)


def build_stk_score_in(
    lang: Lang, facts: StickerFacts, *, invalid: bool = False, not_text: bool = False
) -> Screen:
    """The typed-number prompt; ``invalid`` repeats it under a line saying what was wrong.

    ``not_text``: the answer was a sticker, a photo or other media — the line
    says the number goes in as text and how to leave the prompt instead.
    """
    title = html.escape(_title_of(facts, lang))
    text = f"🔞 <b>{_SCORE(lang)}</b> · {title}\n\n"
    if not_text:
        text += f"{_SCORE_IN_NOT_TEXT(lang).format(cancel=CANCEL(lang))}\n\n"
    elif invalid:
        text += f"{_SCORE_IN_BAD(lang)}\n\n"
    text += _SCORE_IN_ASK(lang)
    return Screen(
        id="stk.score_in",
        text=text,
        rows=((cancel(cb("adm_stk_expcancel:", lang, facts.fuid), lang),),),
    )


def build_stk_clear_ask(lang: Lang, fuid: str) -> Screen:
    """Confirm before clearing; "keep it" is the way back to the card."""
    return Screen(
        id="stk.clear_ask",
        text=f"🧹 <b>{_CLEAR_TITLE(lang)}</b>\n\n{_CLEAR_BODY(lang)}",
        rows=(
            (
                Button(_CLEAR(lang), cb("adm_stk_clr:", lang, fuid)),
                Button(
                    f"⬅️ {_CLEAR_KEEP(lang)}",
                    cb("adm_stk_card:", lang, fuid),
                    kind="back",
                    goes_to="stk.card",
                ),
            ),
        ),
    )


def build_stk_analyzing(lang: Lang) -> Screen:
    """The placeholder a card turns into while the vision call runs; no buttons."""
    return Screen(
        id="stk.analyzing", text=f"⏳ <b>{_ANALYZING(lang)}</b>\n\n{_ANALYZING_BODY(lang)}"
    )


def build_stk_error(
    lang: Lang,
    fuid: str,
    reason: str | None,
    *,
    dm: bool = False,
    score_kept: float | None = None,
    score_lost: bool = False,
    retry_rescore: bool = False,
) -> Screen:
    """Analysis failed: why, Retry, and for a content refusal the way to score by hand.

    ``dm``: the sticker the admin sent to the bot — Retry re-runs the DM check
    (``adm_stk_dmchk:``), and there is no catalogue parent to go back to.

    ``score_kept`` / ``score_lost``: the failed analysis was a re-score, which
    had already dropped the score — it was put back (and is this value), or
    putting it back failed too and the sticker is now unscored.

    ``retry_rescore``: the failed analysis was a re-score, so Retry repeats the
    re-score (``adm_stk_expreset:``). A plain re-analysis would not do: it
    never overwrites a score set by hand, and the one put back may be one.
    """
    copy = ERROR_REASONS.get(reason or "empty", ERROR_REASONS["empty"])
    body = copy(lang) if dm else f"{copy(lang)} {_ERROR_NOTES_KEPT(lang)}"
    if score_lost:
        body += f" {_ERROR_SCORE_LOST(lang)}"
    elif score_kept is not None:
        body += f" {_ERROR_SCORE_KEPT(lang).format(v=_score(score_kept))}"
    if dm:
        retry = cb("adm_stk_dmchk:", lang, fuid)
    elif retry_rescore:
        retry = cb("adm_stk_expreset:", lang, fuid)
    else:
        retry = cb("adm_stk_reanalyze:", lang, fuid)
    rows: list[tuple[Button, ...]] = [(Button(_RETRY(lang), retry),)]
    if reason == "content_filter" or score_lost:
        rows.append((Button(_SCORE_BTN(lang), cb("adm_stk_score:", lang, fuid)),))
    if not dm:
        rows.append((back("stk.card", cb("adm_stk_card:", lang, fuid), lang),))
    return Screen(id="stk.error", text=f"⚠️ <b>{_ERROR_TITLE(lang)}</b>\n\n{body}", rows=tuple(rows))


def build_stk_unknown(lang: Lang, fuid: str, emoji: str | None = None) -> Screen:
    """The answer to a sticker sent to the bot that it has never learned."""
    face = (emoji or "").strip() or "😺"
    return Screen(
        id="stk.unknown",
        text=f"{fence(face)} <b>{_UNKNOWN_TITLE(lang)}</b>\n\n{_UNKNOWN_BODY(lang)}",
        rows=((Button(_DM_ANALYZE(lang), cb("adm_stk_dmchk:", lang, fuid)),),),
    )


def build_stk_reply(lang: Lang, kind: ReplyKind, description: str | None = None) -> Screen:
    """The answer to an admin's reply on a sticker card (a description correction)."""
    if kind == "ok":
        body = f"<i>{fence(html.escape(clip(description or '', DESCRIPTION_CHARS)))}</i>"
        return Screen(id="stk.reply", text=f"✓ <b>{_REPLY_OK(lang)}</b>\n\n{body}")
    copy = _REPLY_BODY.get(kind, _REPLY_BODY["failed"])
    return Screen(id="stk.reply", text=f"⚠️ <b>{_REPLY_NOT(lang)}</b>\n\n{copy(lang)}")


def pack_rows(records: Sequence[Mapping[str, Any]]) -> tuple[PackRow, ...]:
    """`PackRow`s from ``get_all_sets_with_stats`` records."""
    out: list[PackRow] = []
    for r in records:
        name = str(r.get("set_name") or "")
        learned = int(r.get("learned_count") or 0)
        out.append(
            PackRow(
                key=pack_key(name),
                title=str(r.get("set_title") or name),
                analyzed=int(r.get("analyzed_count") or 0),
                total=max(int(r.get("total_count") or 0), learned),
            )
        )
    return tuple(out)
