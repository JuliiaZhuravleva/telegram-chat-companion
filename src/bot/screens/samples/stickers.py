"""Sticker samples: the pack list, a pack, a sticker and its sub-screens, the bot's card.

One sample per state that changes the rows: an empty list, a sticker that was
never analysed (one action instead of two), a failed one, a sticker without a
registered pack (back goes to the pack list), each error reason (a content
refusal adds the score button; the DM variant has no parent to go back to).

The sticker is the design copy's (`foundation.STICKER_FACTS`), so the dump
shows the same sticker the design does.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Final

from src.bot.screens.i18n import Lang
from src.bot.screens.model import Screen
from src.bot.screens.registry import sample
from src.bot.screens.samples import fake
from src.bot.screens.samples.foundation import STICKER_FACTS
from src.bot.screens.stickers import (
    GONE_STICKER,
    NOTICE_SCORE,
    PACK_LABEL_CHARS,
    PER_PAGE,
    ROW_LABEL_CHARS,
    PackPage,
    PackRow,
    SetsPage,
    StickerFacts,
    build_card_sticker,
    build_stk_analyzing,
    build_stk_card,
    build_stk_clear_ask,
    build_stk_error,
    build_stk_reply,
    build_stk_score,
    build_stk_score_in,
    build_stk_set,
    build_stk_sets,
    build_stk_unknown,
    card_title,
    clip,
)

_S: Final = "Стикеры"
_CARDS: Final = "Карточки от бота"

DESCRIPTION, EMOTION, CHARACTER, WHEN = STICKER_FACTS
CONTEXTS: Final = tuple(part.strip() for part in WHEN.split(","))
LONG_DESCRIPTION: Final = (
    "Кот в круглых очках машет лапой, сидя за столом с чашкой чая; "
    "фон светлый, рисунок в мультяшном стиле"
)
NOTES: Final = "Это приветствие, а не прощание"
TAGS: Final = ("мультяшный", "яркий")
OTHER_PACK: Final = "Другой пак"
LONG_PACK: Final = "Очень длинное название тестового пака со стикерами"
OTHER_DESCRIPTION: Final = "Собака спит на диване"

_ROW_TITLE: Final = card_title(DESCRIPTION, ROW_LABEL_CHARS)
_ROW_OTHER: Final = card_title(OTHER_DESCRIPTION, ROW_LABEL_CHARS)
_LONG_TITLE: Final = card_title(LONG_DESCRIPTION)
_LONG_PACK_CLIP: Final = clip(LONG_PACK, PACK_LABEL_CHARS)

STICKER: Final = StickerFacts(
    fuid=fake.FUID,
    emoji="😺",
    description=DESCRIPTION,
    emotion=EMOTION,
    character=CHARACTER,
    contexts=CONTEXTS,
    style_tags=TAGS,
    total_uses=12,
    bot_uses=3,
    score=0.25,
    set_name=fake.PACK,
    pack_key=fake.PACK_REF,
    pack_title=fake.PACK_TITLE,
    has_embedding=True,
)
STICKER_DATA: Final = (*STICKER_FACTS, *CONTEXTS, fake.PACK_TITLE, fake.PACK)

NEW: Final = StickerFacts(
    fuid=fake.FUID,
    emoji="😀",
    set_name=fake.PACK,
    pack_key=fake.PACK_REF,
    pack_title=fake.PACK_TITLE,
)
FAILED: Final = StickerFacts(
    fuid=fake.FUID,
    emoji="🙈",
    failed=True,
    total_uses=2,
    notes=NOTES,
    set_name=fake.PACK,
    pack_key=fake.PACK_REF,
    pack_title=fake.PACK_TITLE,
)


def _sticker(**changes: Any) -> StickerFacts:
    return replace(STICKER, **changes)


# ── stk.sets ───────────────────────────────────────────────────────────


@sample(
    "stk.sets",
    section=_S,
    title="Паки стикеров",
    nav="panel",
    user_data=(fake.PACK_TITLE, OTHER_PACK, LONG_PACK, _LONG_PACK_CLIP),
)
def sets(lang: Lang) -> Screen:
    packs = (
        PackRow(fake.PACK_REF, fake.PACK_TITLE, 12, 20),
        PackRow("0123456789ac", OTHER_PACK, 3, 10),
        PackRow("0123456789ad", LONG_PACK, 0, 120),
        PackRow("0123456789ae", fake.PACK, 1, 1),
    )
    return build_stk_sets(lang, SetsPage(packs=packs, total=PER_PAGE + 2, page=0, pages=2))


@sample("stk.sets", section=_S, title="Паки стикеров: пусто", nav="panel")
def sets_empty(lang: Lang) -> Screen:
    return build_stk_sets(lang, SetsPage(packs=(), total=0))


@sample(
    "stk.sets",
    section=_S,
    title="Паки стикеров: стикера уже нет",
    nav="panel",
    user_data=(fake.PACK_TITLE, OTHER_PACK),
)
def sets_gone(lang: Lang) -> Screen:
    packs = (
        PackRow(fake.PACK_REF, fake.PACK_TITLE, 12, 20),
        PackRow("0123456789ac", OTHER_PACK, 3, 10),
    )
    return build_stk_sets(lang, SetsPage(packs=packs, total=2), notice_raw=GONE_STICKER(lang))


# ── stk.set ────────────────────────────────────────────────────────────


@sample(
    "stk.set",
    section=_S,
    title="Пак",
    nav="panel",
    user_data=(fake.PACK_TITLE, _ROW_TITLE, _ROW_OTHER),
)
def pack(lang: Lang) -> Screen:
    stickers = (
        STICKER,
        NEW,
        FAILED,
        _sticker(emoji="🐶", description=OTHER_DESCRIPTION, total_uses=0),
    )
    return build_stk_set(
        lang,
        PackPage(
            key=fake.PACK_REF, title=fake.PACK_TITLE, count=20, stickers=stickers, page=0, pages=5
        ),
    )


@sample("stk.set", section=_S, title="Пак: пусто", nav="panel", user_data=(fake.PACK_TITLE,))
def pack_empty(lang: Lang) -> Screen:
    return build_stk_set(
        lang, PackPage(key=fake.PACK_REF, title=fake.PACK_TITLE, count=0, stickers=())
    )


# ── stk.card ───────────────────────────────────────────────────────────


@sample("stk.card", section=_S, title="Стикер", nav="panel", user_data=STICKER_DATA)
def card(lang: Lang) -> Screen:
    return build_stk_card(lang, STICKER)


@sample(
    "stk.card",
    section=_S,
    title="Стикер: длинное описание и заметки, оценка вручную",
    nav="panel",
    user_data=(*STICKER_DATA, LONG_DESCRIPTION, _LONG_TITLE, NOTES),
)
def card_long(lang: Lang) -> Screen:
    facts = _sticker(description=LONG_DESCRIPTION, notes=NOTES, score=0.75, score_manual=True)
    return build_stk_card(lang, facts, notice=NOTICE_SCORE(lang).format(v="0.75"))


@sample(
    "stk.card",
    section=_S,
    title="Стикер: подробный режим",
    nav="panel",
    user_data=(*STICKER_DATA, *TAGS, fake.FUID),
)
def card_detailed(lang: Lang) -> Screen:
    return build_stk_card(lang, _sticker(detailed=True))


@sample(
    "stk.card",
    section=_S,
    title="Стикер: ещё не разобран",
    nav="panel",
    user_data=(fake.PACK_TITLE,),
)
def card_new(lang: Lang) -> Screen:
    return build_stk_card(lang, NEW)


@sample(
    "stk.card",
    section=_S,
    title="Стикер: ошибка разбора",
    nav="panel",
    user_data=(fake.PACK_TITLE, NOTES),
)
def card_failed(lang: Lang) -> Screen:
    return build_stk_card(lang, FAILED)


@sample(
    "stk.card",
    section=_S,
    title="Стикер без пака в списке",
    nav="panel",
    user_data=STICKER_DATA,
)
def card_no_pack(lang: Lang) -> Screen:
    return build_stk_card(lang, _sticker(set_name=None, pack_key=None, pack_title=None, score=None))


# ── stk.score / stk.score_in ───────────────────────────────────────────


@sample(
    "stk.score",
    section=_S,
    title="Откровенность: посчитал бот",
    nav="panel",
    user_data=(DESCRIPTION,),
)
def score_auto(lang: Lang) -> Screen:
    return build_stk_score(lang, STICKER)


@sample(
    "stk.score", section=_S, title="Откровенность: вручную", nav="panel", user_data=(DESCRIPTION,)
)
def score_manual(lang: Lang) -> Screen:
    return build_stk_score(lang, _sticker(score=0.6, score_manual=True))


@sample(
    "stk.score",
    section=_S,
    title="Откровенность: нет оценки",
    nav="panel",
    user_data=(DESCRIPTION,),
)
def score_none(lang: Lang) -> Screen:
    return build_stk_score(lang, _sticker(score=None))


@sample("stk.score_in", section=_S, title="Своё число", nav="prompt", user_data=(DESCRIPTION,))
def score_in(lang: Lang) -> Screen:
    return build_stk_score_in(lang, STICKER)


@sample(
    "stk.score_in", section=_S, title="Своё число: не число", nav="prompt", user_data=(DESCRIPTION,)
)
def score_in_bad(lang: Lang) -> Screen:
    return build_stk_score_in(lang, STICKER, invalid=True)


# ── stk.clear_ask / stk.analyzing / stk.error ──────────────────────────


@sample(
    "stk.clear_ask",
    section=_S,
    title="Очистить разбор?",
    nav="panel",
    exempt={
        "R4": "the way back from a confirmation says what it does: «Не очищать» returns to "
        "the sticker unchanged",
    },
)
def clear_ask(lang: Lang) -> Screen:
    return build_stk_clear_ask(lang, fake.FUID)


@sample("stk.analyzing", section=_S, title="Разбираю стикер", nav="card")
def analyzing(lang: Lang) -> Screen:
    return build_stk_analyzing(lang)


@sample("stk.error", section=_S, title="Ошибка: модель не ответила", nav="panel")
def error_vision(lang: Lang) -> Screen:
    return build_stk_error(lang, fake.FUID, "vision")


@sample("stk.error", section=_S, title="Ошибка: не скачался", nav="panel")
def error_download(lang: Lang) -> Screen:
    return build_stk_error(lang, fake.FUID, "download")


@sample("stk.error", section=_S, title="Ошибка: пустой ответ", nav="panel")
def error_empty(lang: Lang) -> Screen:
    return build_stk_error(lang, fake.FUID, "empty")


@sample("stk.error", section=_S, title="Ошибка: отказ из-за содержания", nav="panel")
def error_filter(lang: Lang) -> Screen:
    return build_stk_error(lang, fake.FUID, "content_filter")


@sample("stk.error", section=_S, title="Ошибка в личке", nav="card")
def error_dm(lang: Lang) -> Screen:
    return build_stk_error(lang, fake.FUID, "download", dm=True)


@sample("stk.error", section=_S, title="Ошибка в личке: отказ из-за содержания", nav="card")
def error_dm_filter(lang: Lang) -> Screen:
    return build_stk_error(lang, fake.FUID, "content_filter", dm=True)


# ── stk.unknown / stk.reply ────────────────────────────────────────────


@sample("stk.unknown", section=_S, title="Стикера нет в базе", nav="card")
def unknown(lang: Lang) -> Screen:
    return build_stk_unknown(lang, fake.FUID, "😺")


@sample(
    "stk.reply",
    section=_S,
    title="Описание уточнено",
    nav="card",
    user_data=(f"{DESCRIPTION}, радостно",),
)
def reply_ok(lang: Lang) -> Screen:
    return build_stk_reply(lang, "ok", f"{DESCRIPTION}, радостно")


@sample("stk.reply", section=_S, title="Описание не объединилось", nav="card")
def reply_note_saved(lang: Lang) -> Screen:
    return build_stk_reply(lang, "note_saved")


@sample("stk.reply", section=_S, title="Описание: отказ из-за содержания", nav="card")
def reply_filter(lang: Lang) -> Screen:
    return build_stk_reply(lang, "content_filter")


@sample("stk.reply", section=_S, title="Описание: ошибка", nav="card")
def reply_failed(lang: Lang) -> Screen:
    return build_stk_reply(lang, "failed")


# ── card.sticker ───────────────────────────────────────────────────────


@sample(
    "card.sticker",
    section=_CARDS,
    title="Новый стикер",
    nav="card",
    user_data=(*STICKER_DATA, DESCRIPTION.lower()),
)
def notify(lang: Lang) -> Screen:
    return build_card_sticker(lang, _sticker(description=DESCRIPTION.lower()))


@sample(
    "card.sticker",
    section=_CARDS,
    title="Новый стикер: подробно",
    nav="card",
    user_data=(*STICKER_DATA, DESCRIPTION.lower(), *TAGS, fake.FUID),
)
def notify_detailed(lang: Lang) -> Screen:
    return build_card_sticker(lang, _sticker(detailed=True))
