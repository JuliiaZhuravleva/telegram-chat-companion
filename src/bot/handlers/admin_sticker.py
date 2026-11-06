"""Admin sticker management: the pack catalogue, a sticker's card, its score.

Handles:
- ``adm_stk_*`` callbacks: the pack list, one pack, a sticker's card and the
  sub-screens it turns into (score, clear, analysing, error).
- A reply to a sticker card → merge the reply into the description.
- An admin sending a sticker straight to the bot → catalogue check: known →
  the card, unknown → one "🔍 Разобрать" button. Registered here (not in
  handlers/media.py) so router order (handlers/__init__.py) makes it run
  before media.py's silent auto-learn for the admin's own DM. Only with no
  prompt open: a sticker sent into another screen's prompt is that prompt's
  answer, and in this module's own score prompt it gets a hint instead.

Every card the bot shows about a sticker — opened from a pack, after a
re-analysis, after a score change, the DM check, the bot's own notification —
is remembered in ``admin_sticker_notifications`` so that a reply to it is read
as a description correction. Leaving a card for a list forgets it again.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Mapping
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.screens.i18n import Lang, lang_of
from src.bot.screens.model import Screen
from src.bot.screens.render import page_slice, render_text, show, to_markup
from src.bot.screens.routes import parse
from src.bot.screens.stale import show_stale
from src.bot.screens.stickers import (
    ALERT_RESEND,
    GONE_PACK,
    GONE_STICKER,
    NOTICE_ANALYZED,
    NOTICE_CLEARED,
    NOTICE_LEARNED,
    NOTICE_RESCORED,
    NOTICE_SCORE,
    PER_PAGE,
    PRESETS,
    PackPage,
    SetsPage,
    StickerFacts,
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
    facts_from_row,
    pack_key,
    pack_rows,
)
from src.bot.states.admin import AdminStates
from src.bot.utils import check_admin_direct, safe_edit_reply_markup
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.stickers import StickerRepository
from src.services.modules.sticker import StickerLearningService
from src.utils.telegram import TelegramFileError, download_telegram_file, typing_indicator

logger = structlog.get_logger(__name__)

router = Router(name="admin_sticker")

_PACK_KEY_RE = re.compile(r"[0-9a-f]{12}")
_PAGE_RE = re.compile(r"-?\d{1,6}")


# ── Helpers ─────────────────────────────────────────────────────────────


async def _resolve_default_tolerance_level(bot_config_repo: BotConfigRepository) -> float:
    """Уровень приличия used as the comparison ceiling for DM sticker cards
    that aren't tied to one specific chat (catalog browsing, DM sticker
    check, re-analyze) — every card outside ``notify_admins()`` (which has
    the real originating chat's ``ChatConfig.tolerance_level`` in scope).

    Resolves the same two layers ``ChatConfigService`` would for a chat
    that never set its own override: ``bot_config.default_tolerance_level``
    if an admin has set one via the defaults screen, else the
    ``ChatConfig.tolerance_level`` dataclass fallback (``0.5``, ADR-0008
    Decision 1/8) — never a per-chat override, since no specific chat
    applies here.
    """
    defaults = await bot_config_repo.get_defaults()
    raw = defaults.get("tolerance_level")
    return float(raw) if raw is not None else 0.5


# Regex to extract file_unique_id from notification text (🆔 line)
_STICKER_ID_RE = re.compile(r"🆔\s*([A-Za-z0-9_-]+)")


def _extract_file_unique_id_from_reply(reply_msg: Message) -> str | None:
    """Try to extract file_unique_id from replied-to message text or caption."""
    for content in (reply_msg.text, reply_msg.caption):
        if content:
            match = _STICKER_ID_RE.search(content)
            if match:
                return match.group(1)
    return None


def _lang_hint(callback: CallbackQuery) -> Lang:
    """The language token of a payload that did not parse, for the stale screen."""
    fields = (callback.data or "").split(":")
    return lang_of(fields[1] if len(fields) > 1 else None)


async def _facts_of(
    row: Mapping[str, Any],
    sticker_repo: StickerRepository,
    admin_repo: AdminRepository,
    bot_config_repo: BotConfigRepository,
) -> StickerFacts:
    """Everything a card shows about one ``sticker_knowledge`` row."""
    pack = None
    set_name = row.get("set_name")
    if set_name:
        pack = await sticker_repo.get_sticker_set(str(set_name))
    tolerance = await _resolve_default_tolerance_level(bot_config_repo)
    notifications = await admin_repo.get_notification_settings(bot_config_repo)
    return facts_from_row(
        row,
        tolerance=tolerance,
        detailed=notifications.get("sticker") == "detailed",
        pack=dict(pack) if pack else None,
    )


async def _card_facts(
    fuid: str,
    sticker_repo: StickerRepository,
    admin_repo: AdminRepository,
    bot_config_repo: BotConfigRepository,
) -> StickerFacts | None:
    """`_facts_of` the sticker, or None when it is no longer in the catalogue."""
    row = await sticker_repo.get_by_file_unique_id(fuid)
    if row is None:
        return None
    return await _facts_of(dict(row), sticker_repo, admin_repo, bot_config_repo)


async def _remember(
    sticker_repo: StickerRepository,
    msg: Message | None,
    fuid: str,
    admin_id: int | None,
    *,
    sticker_msg_id: int = 0,
) -> None:
    """Record ``msg`` as a card about ``fuid``, so a reply to it edits the description.

    Idempotent: re-showing a card in the same message keeps the one row.
    Best-effort — the card is already on screen, and losing the record only
    costs the reply shortcut for that message.
    """
    if msg is None or admin_id is None:
        return
    try:
        await sticker_repo.save_notification(
            file_unique_id=fuid,
            admin_id=admin_id,
            message_id=msg.message_id,
            sticker_msg_id=sticker_msg_id,
            chat_id=msg.chat.id,
        )
    except Exception:
        logger.warning("sticker card not remembered", file_unique_id=fuid, exc_info=True)


async def _leave_card(callback: CallbackQuery, sticker_repo: StickerRepository) -> None:
    """The message under this button stops being a sticker card.

    A card opened from a pack sits under the sticker the bot sent for it;
    that sticker goes with it. Only that card's own sticker, and only when the
    card is not a reply: the bot's notification and the DM check reply to a
    sticker the admin may still want, and nothing else in the chat is touched.
    Then the record is released, so a reply to what is now a list is not read
    as a description correction.
    """
    msg = callback.message
    if not isinstance(msg, Message):
        return
    try:
        sticker_msg_id = await sticker_repo.get_card_sticker_msg(msg.chat.id, msg.message_id)
        if sticker_msg_id and msg.reply_to_message is None and callback.bot is not None:
            with contextlib.suppress(TelegramBadRequest):
                await callback.bot.delete_message(chat_id=msg.chat.id, message_id=sticker_msg_id)
        await sticker_repo.release_card(msg.chat.id, msg.message_id)
    except Exception:
        logger.warning("sticker card not released", message_id=msg.message_id, exc_info=True)


async def _disarm(state: FSMContext) -> None:
    """Drop an open score prompt: any other sticker button means the admin moved on.

    Only the score prompt's own state — a prompt owned by another screen is
    not this module's to clear.
    """
    if await state.get_state() == AdminStates.awaiting_sticker_score.state:
        await state.clear()


async def _restore_score(
    sticker_repo: StickerRepository, fuid: str, score: float, manual: bool
) -> bool:
    """Put back the score a failed re-score had already dropped; False when that failed.

    A re-score resets the score before it analyses, and an unscored sticker is
    never sent — so a failed analysis must not leave it that way.
    """
    try:
        if manual:
            return await sticker_repo.set_manual_explicitness_score(fuid, score)
        await sticker_repo.update_explicitness_score(fuid, score)
        return True
    except Exception:
        logger.error("sticker score not restored", file_unique_id=fuid, exc_info=True)
        return False


async def _sets_screen(
    lang: Lang,
    sticker_repo: StickerRepository,
    page: int = 0,
    *,
    notice_raw: str | None = None,
) -> Screen:
    total = await sticker_repo.count_sets()
    page, pages, offset = page_slice(total, page, PER_PAGE)
    records = await sticker_repo.get_all_sets_with_stats(limit=PER_PAGE, offset=offset)
    data = SetsPage(
        packs=pack_rows([dict(r) for r in records]), total=total, page=page, pages=pages
    )
    return build_stk_sets(lang, data, notice_raw=notice_raw)


async def _pack_screen(
    lang: Lang,
    sticker_repo: StickerRepository,
    pack: Mapping[str, Any],
    page: int,
) -> Screen:
    name = str(pack["set_name"])
    total = await sticker_repo.count_stickers_in_set(name)
    page, pages, offset = page_slice(total, page, PER_PAGE)
    records = await sticker_repo.get_stickers_in_set(name, limit=PER_PAGE, offset=offset)
    stickers = tuple(facts_from_row(dict(r), pack=pack) for r in records)
    data = PackPage(
        key=pack_key(name),
        title=str(pack.get("set_title") or name),
        count=total,
        stickers=stickers,
        page=page,
        pages=pages,
    )
    return build_stk_set(lang, data)


async def _show_gone(
    callback: CallbackQuery,
    lang: Lang,
    sticker_repo: StickerRepository,
    notice_raw: str,
    *,
    answered: bool = False,
) -> None:
    """The sticker or pack a button points at is gone: the pack list says so."""
    await _leave_card(callback, sticker_repo)
    await show(
        callback,
        await _sets_screen(lang, sticker_repo, notice_raw=notice_raw),
        answered=answered,
    )


async def _strip_prompt(message: Message, prompt_id: Any) -> None:
    """Take the ✖️ button off the score prompt the admin has just answered.

    The prompt is not the message the update came with, so it is addressed by
    id; a prompt already gone or already bare is not an error.
    """
    if not isinstance(prompt_id, int) or message.bot is None:
        return
    prompt = Message(message_id=prompt_id, date=message.date, chat=message.chat).as_(message.bot)
    with contextlib.suppress(TelegramBadRequest):
        await safe_edit_reply_markup(prompt, reply_markup=None)


# ── Admin reply to a sticker card ───────────────────────────────────────


# StateFilter(None): this router precedes every FSM-owning router, so while
# an FSM dialog is active (e.g. the tolerance prompt, which the admin may
# answer as a *reply*) this handler must yield instead of swallowing the
# input as a description correction (2026-08-07 review).
#
# ~F.text.startswith("/"): same reason one step further out. This router is
# included first, so for a bot admin in a DM this handler matches *any* reply
# carrying text -- including a slash command. A matched handler consumes the
# update even when its body decides to do nothing, so an admin who replied to
# something with `/remember ...` or `/kb` in a DM got silence, and the command's
# own handler never ran. Commands belong to the command handlers; the
# description-correction path only ever wants free text (same filter shape as
# `handle_sticker_explicitness_input`).
@router.message(
    F.reply_to_message,
    F.text,
    ~F.text.startswith("/"),
    F.chat.type == "private",
    IsAdmin(),
    StateFilter(None),
)
async def handle_admin_sticker_reply(
    message: Message,
    sticker_repo: FromDishka[StickerRepository],
    sticker_service: FromDishka[StickerLearningService],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    bot: Bot,
    message_thread_id: int | None = None,
) -> None:
    """Admin replies to a sticker card → merge the reply into the description."""
    if not message.reply_to_message or not message.text:
        return

    # Look up the card by reply (primary: DB, fallback: the 🆔 line)
    notif = await sticker_repo.get_notification_by_reply(
        message.chat.id,
        message.reply_to_message.message_id,
    )

    file_unique_id: str | None = None
    if notif:
        file_unique_id = notif["file_unique_id"]
    else:
        file_unique_id = _extract_file_unique_id_from_reply(message.reply_to_message)

    if not file_unique_id:
        return

    logger.debug(
        "Admin sticker reply",
        file_unique_id=file_unique_id,
        lookup_source="db" if notif else "text_parse",
        admin_id=message.from_user.id if message.from_user else None,
    )

    admin_text = message.text.strip()
    if not admin_text:
        return

    lang = lang_of(await admin_repo.get_admin_language(bot_config_repo))

    # Merge description via AI (fallback: the note is saved as it is)
    try:
        async with typing_indicator(bot, message.chat.id, message_thread_id):
            new_desc = await sticker_service.merge_admin_description(file_unique_id, admin_text)
    except ValueError as e:
        failed = build_stk_reply(lang, "content_filter" if str(e) == "content_filter" else "failed")
        await message.reply(render_text(failed), parse_mode="HTML")
        return
    except Exception:
        logger.exception("Sticker merge failed", file_unique_id=file_unique_id)
        new_desc = None

    screen = (
        build_stk_reply(lang, "ok", new_desc) if new_desc else build_stk_reply(lang, "note_saved")
    )
    await message.reply(render_text(screen), parse_mode="HTML")


# ── Pack list and one pack ──────────────────────────────────────────────


@router.callback_query(
    F.data.startswith("adm_stk_sets:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_sets(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """The pack list, a page at a time; also the way back from a card without a pack."""
    fields = parse(callback.data, 2)
    if fields is None or not _PAGE_RE.fullmatch(fields[1]):
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang = lang_of(fields[0])
    await _disarm(state)
    await _leave_card(callback, sticker_repo)
    await show(callback, await _sets_screen(lang, sticker_repo, int(fields[1])))


async def _open_pack(
    callback: CallbackQuery,
    lang: Lang,
    sticker_repo: StickerRepository,
    pack: Mapping[str, Any] | None,
    page: int,
) -> None:
    if pack is None:
        await _show_gone(callback, lang, sticker_repo, GONE_PACK(lang))
        return
    await _leave_card(callback, sticker_repo)
    await show(callback, await _pack_screen(lang, sticker_repo, dict(pack), page))


@router.callback_query(
    F.data.startswith("adm_stk_pk:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_pack(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """One pack, addressed by the short key of its name (names overflow a button)."""
    fields = parse(callback.data, 3)
    if fields is None or not _PACK_KEY_RE.fullmatch(fields[1]) or not _PAGE_RE.fullmatch(fields[2]):
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang = lang_of(fields[0])
    await _disarm(state)
    pack = await sticker_repo.get_set_by_key(fields[1])
    await _open_pack(callback, lang, sticker_repo, pack, int(fields[2]))


@router.callback_query(
    F.data.startswith("adm_stk_set:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_set_legacy(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """A pack button sent before packs were addressed by key: the name itself."""
    fields = parse(callback.data, 3)
    if fields is None or not _PAGE_RE.fullmatch(fields[2]):
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang = lang_of(fields[0])
    await _disarm(state)
    pack = await sticker_repo.get_sticker_set(fields[1])
    await _open_pack(callback, lang, sticker_repo, pack, int(fields[2]))


# ── A sticker's card ────────────────────────────────────────────────────


@router.callback_query(
    F.data.startswith("adm_stk_view:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_view(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Open a sticker from its pack: the sticker itself, then its card under it.

    A text message cannot hold a sticker, so the pack list gives way to two new
    messages; the card remembers its sticker so leaving the card removes both.
    """
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    await _disarm(state)

    row = await sticker_repo.get_by_file_unique_id(fuid)
    if row is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    record = dict(row)
    facts = await _facts_of(record, sticker_repo, admin_repo, bot_config_repo)

    await callback.answer()
    sticker_msg: Message | None = None
    if isinstance(callback.message, Message):
        await _leave_card(callback, sticker_repo)
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.delete()
        with contextlib.suppress(TelegramBadRequest):
            sticker_msg = await callback.message.answer_sticker(str(record["file_id"]))
    msg = await show(callback, build_stk_card(lang, facts), answered=True, new_message=True)
    await _remember(
        sticker_repo,
        msg,
        fuid,
        callback.from_user.id,
        sticker_msg_id=sticker_msg.message_id if sticker_msg else 0,
    )


@router.callback_query(
    F.data.startswith("adm_stk_card:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_card(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Back to the card from one of its sub-screens, in the same message."""
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    await _disarm(state)
    facts = await _card_facts(fuid, sticker_repo, admin_repo, bot_config_repo)
    if facts is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    msg = await show(callback, build_stk_card(lang, facts))
    await _remember(sticker_repo, msg, fuid, callback.from_user.id)


# ── Clear the analysis ──────────────────────────────────────────────────


@router.callback_query(
    F.data.startswith("adm_stk_clr_ask:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_clear_ask(
    callback: CallbackQuery,
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Ask before clearing; "keep it" goes back to the card in the same message."""
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    await _disarm(state)
    await show(callback, build_stk_clear_ask(lang_of(fields[0]), fields[1]))


@router.callback_query(
    F.data.startswith("adm_stk_clr:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_clear(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Clear the vision fields; notes, usage and the score stay."""
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    await _disarm(state)
    if await sticker_repo.get_by_file_unique_id(fuid) is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    await sticker_repo.clear_analysis(fuid)
    facts = await _card_facts(fuid, sticker_repo, admin_repo, bot_config_repo)
    if facts is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    msg = await show(callback, build_stk_card(lang, facts, notice=NOTICE_CLEARED(lang)))
    await _remember(sticker_repo, msg, fuid, callback.from_user.id)


# ── Explicitness ────────────────────────────────────────────────────────


@router.callback_query(
    F.data.startswith("adm_stk_score:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_score(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """The explicitness screen: five presets, a typed number, a fresh automatic score."""
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    await _disarm(state)
    facts = await _card_facts(fuid, sticker_repo, admin_repo, bot_config_repo)
    if facts is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    await show(callback, build_stk_score(lang, facts))


@router.callback_query(
    F.data.startswith("adm_stk_expset:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_explicitness_preset(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Set a preset explicitness value with one tap (ADR-0009 Decision 7,
    closing paragraph) -- the button already fixes a known-valid value, so
    no FSM input is needed. The card comes back with a line saying so.

    It still has to drop the score prompt, though: an older score screen
    elsewhere in the chat keeps its preset buttons live. An admin who opens the
    "type a number" prompt and then taps a preset instead would leave
    ``awaiting_sticker_score`` set, and the next plain DM text — a description
    correction, say — would be eaten by the score-input handler (which also
    outranks ``handle_admin_sticker_reply``'s ``StateFilter(None)``). Only that
    state (`_disarm`): another screen's prompt stays open.
    """
    fields = parse(callback.data, 3)
    index = int(fields[2]) if fields is not None and fields[2].isdigit() else -1
    if fields is None or not 0 <= index < len(PRESETS):
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    value = PRESETS[index]

    await _disarm(state)
    if not await sticker_repo.set_manual_explicitness_score(fuid, value):
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    facts = await _card_facts(fuid, sticker_repo, admin_repo, bot_config_repo)
    if facts is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    notice = NOTICE_SCORE(lang).format(v=f"{value:.2f}")
    msg = await show(callback, build_stk_card(lang, facts, notice=notice))
    await _remember(sticker_repo, msg, fuid, callback.from_user.id)


@router.callback_query(
    F.data.startswith("adm_stk_expedit:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_explicitness_edit_prompt(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Prompt for a typed explicitness value (ADR-0009 Decision 7), in place
    of the score screen.

    Builds against ``AdminStates.awaiting_sticker_score`` rather than the
    two already-spoken-for scaffold states (see the state's own docstring).
    The prompt's id is kept so the answer can take its ✖️ button away.
    """
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    facts = await _card_facts(fuid, sticker_repo, admin_repo, bot_config_repo)
    if facts is None:
        await _disarm(state)
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return

    await state.set_state(AdminStates.awaiting_sticker_score)
    await state.update_data(exp_file_unique_id=fuid, exp_lang=lang)
    msg = await show(callback, build_stk_score_in(lang, facts))
    if msg is not None:
        await state.update_data(exp_prompt_id=msg.message_id)


@router.message(
    AdminStates.awaiting_sticker_score,
    F.chat.type == "private",
    ~F.text.startswith("/"),
    ~F.caption.startswith("/"),
)
async def handle_sticker_explicitness_input(
    message: Message,
    state: FSMContext,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Validate and persist the manually-typed explicitness score
    (ADR-0009 Decisions 5 and 7).

    Reject-not-clamp on invalid input (same posture as
    ``admin_chat_panel.py``'s tolerance FSM) -- re-prompts, state stays set.
    Escape hatches: admin commands pass through untouched
    (``~F.text.startswith("/")``), and the prompt carries a dedicated
    cancel button.

    Each answer is one new message: the repeated prompt, or the card. The
    previous prompt loses its ✖️ so only one live way out exists.

    A sticker, a photo or any other answer without text is not a wrong number:
    the prompt comes back saying the number goes in as text, and how to leave.
    """
    data = await state.get_data()
    file_unique_id = data.get("exp_file_unique_id")
    lang = lang_of(data.get("exp_lang"))
    prompt_id = data.get("exp_prompt_id")
    if not file_unique_id:
        await state.clear()
        return

    admin_id = message.from_user.id if message.from_user else None
    if not await check_admin_direct(bot_config_repo, admin_id):
        await state.clear()
        return

    not_text = message.text is None
    raw = (message.text or "").strip().replace(",", ".")
    value: float | None
    try:
        value = float(raw)
    except ValueError:
        value = None

    if value is None or not 0.0 <= value <= 1.0:
        facts = await _card_facts(file_unique_id, sticker_repo, admin_repo, bot_config_repo)
        await _strip_prompt(message, prompt_id)
        if facts is None:
            await state.clear()
            gone = await _sets_screen(lang, sticker_repo, notice_raw=GONE_STICKER(lang))
            await show(message, gone)
            return
        again = build_stk_score_in(lang, facts, invalid=True, not_text=not_text)
        msg = await show(message, again)
        if msg is not None:
            await state.update_data(exp_prompt_id=msg.message_id)
        return

    await state.clear()
    await _strip_prompt(message, prompt_id)
    facts = None
    if await sticker_repo.set_manual_explicitness_score(file_unique_id, value):
        facts = await _card_facts(file_unique_id, sticker_repo, admin_repo, bot_config_repo)
    if facts is None:
        gone = await _sets_screen(lang, sticker_repo, notice_raw=GONE_STICKER(lang))
        await show(message, gone)
        return
    notice = NOTICE_SCORE(lang).format(v=f"{value:.2f}")
    msg = await show(message, build_stk_card(lang, facts, notice=notice))
    await _remember(sticker_repo, msg, file_unique_id, admin_id)


@router.callback_query(
    F.data.startswith("adm_stk_expcancel:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_explicitness_cancel(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Escape hatch for ``awaiting_sticker_score`` -- the input handler
    keeps the state set on invalid input (reject-not-clamp), so cancelling
    must be reachable without typing a valid float. The prompt turns back
    into the score screen it came from. Only this prompt's state is dropped
    (`_disarm`): a stale ✖️ tapped while another screen's prompt is open
    leaves that prompt alone.
    """
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    await _disarm(state)
    facts = await _card_facts(fuid, sticker_repo, admin_repo, bot_config_repo)
    if facts is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    await show(callback, build_stk_score(lang, facts))


# ── Analysis ────────────────────────────────────────────────────────────


async def _analyse_into_card(
    callback: CallbackQuery,
    lang: Lang,
    fuid: str,
    bot: Bot,
    sticker_service: StickerLearningService,
    sticker_repo: StickerRepository,
    admin_repo: AdminRepository,
    bot_config_repo: BotConfigRepository,
    *,
    notice: str,
    dm: bool = False,
    rescore: bool = False,
    prior_score: tuple[float, bool] | None = None,
) -> None:
    """Run one re-analysis behind the ⏳ placeholder, then the card or the error.

    The callback must already be answered: the vision call takes seconds. An
    unexpected exception is an error screen too — the placeholder has no
    buttons, so leaving it would strand the admin.

    ``rescore``: this analysis is the second half of a re-score, so the error
    screen's Retry repeats the re-score, not a bare re-analysis.

    ``prior_score`` (value, set by hand): the score that re-score had already
    dropped. On failure it is put back, and the error screen says whether
    that worked.
    """
    await show(callback, build_stk_analyzing(lang), answered=True)
    reason: str | None
    try:
        result = await sticker_service.reanalyze(bot, fuid)
        reason = None if result.ok else result.reason
        ok = result.ok
    except Exception:
        logger.error("sticker re-analysis crashed", file_unique_id=fuid, exc_info=True)
        ok, reason = False, "internal"
    if not ok:
        kept: float | None = None
        lost = False
        if prior_score is not None:
            score, manual = prior_score
            if await _restore_score(sticker_repo, fuid, score, manual):
                kept = score
            else:
                lost = True
        error = build_stk_error(
            lang, fuid, reason, dm=dm, score_kept=kept, score_lost=lost, retry_rescore=rescore
        )
        await show(callback, error, answered=True)
        return
    facts = await _card_facts(fuid, sticker_repo, admin_repo, bot_config_repo)
    if facts is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang), answered=True)
        return
    msg = await show(callback, build_stk_card(lang, facts, notice=notice), answered=True)
    await _remember(sticker_repo, msg, fuid, callback.from_user.id)


@router.callback_query(
    F.data.startswith("adm_stk_reanalyze:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_run_analysis(
    callback: CallbackQuery,
    bot: Bot,
    sticker_service: FromDishka[StickerLearningService],
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Analyse the sticker again (or for the first time); the card waits under ⏳."""
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    await _disarm(state)
    if await sticker_repo.get_by_file_unique_id(fuid) is None:
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    await callback.answer()
    await _analyse_into_card(
        callback,
        lang,
        fuid,
        bot,
        sticker_service,
        sticker_repo,
        admin_repo,
        bot_config_repo,
        notice=NOTICE_ANALYZED(lang),
    )


@router.callback_query(
    F.data.startswith("adm_stk_expreset:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_sticker_explicitness_reset(
    callback: CallbackQuery,
    bot: Bot,
    sticker_service: FromDishka[StickerLearningService],
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Recalculate the score: drop the manual value and analyse once more.

    Resetting alone (ADR-0009 Decision 5) would leave the score empty, and an
    empty score means the bot never sends the sticker — so the reset is
    followed by the one re-analysis that writes a fresh automatic score.

    If that analysis fails, the score it replaced is put back: the reset alone
    would leave the sticker unscored, i.e. never sent, behind an error screen
    that says nothing about it. That screen's Retry comes back here: a plain
    re-analysis would keep a score that was put back as set by hand.

    Drops the score prompt for the same reason the preset buttons do: this row
    stays tappable on a score screen elsewhere in the chat. Only that state.
    """
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    await _disarm(state)
    row = await sticker_repo.get_by_file_unique_id(fuid)
    prior: tuple[float, bool] | None = None
    if row is not None and row.get("explicitness_score") is not None:
        prior = (
            float(row["explicitness_score"]),
            bool(row.get("explicitness_is_manual") or False),
        )
    if row is None or not await sticker_repo.reset_explicitness_to_auto(fuid):
        await _show_gone(callback, lang, sticker_repo, GONE_STICKER(lang))
        return
    await callback.answer()
    await _analyse_into_card(
        callback,
        lang,
        fuid,
        bot,
        sticker_service,
        sticker_repo,
        admin_repo,
        bot_config_repo,
        notice=NOTICE_RESCORED(lang),
        rescore=True,
        prior_score=prior,
    )


# ── Admin sends a sticker straight to the bot ───────────────────────────


# StateFilter(None): this router runs before every other FSM-owning router, so
# without it a sticker sent as the answer to another screen's prompt (the rules
# reaction prompt, say) would be taken for a catalogue check. In this module's
# own score prompt `handle_sticker_explicitness_input`, registered above, has
# already answered it.
@router.message(F.sticker, F.chat.type == "private", IsAdmin(), StateFilter(None))
async def handle_admin_sticker_check(
    message: Message,
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Admin sends a sticker directly in DM (no command) → catalog check (B-1).

    Registered on the admin_sticker router, which handlers/__init__.py wires
    in BEFORE the media router — so this consumes the update first and
    handlers/media.py's silent auto-learn (handle_sticker_message) never
    fires for an admin's own DM check. Known sticker → the card (same
    renderer as the pack browser). Unknown → an explicit "🔍 Разобрать"
    button; analysis only runs on that tap, never silently (ADR-0003 —
    analysis stays a visible, synchronous admin action).
    """
    sticker = message.sticker
    if sticker is None:
        return

    lang = lang_of(await admin_repo.get_admin_language(bot_config_repo))

    existing = await sticker_repo.get_by_file_unique_id(sticker.file_unique_id)
    if existing:
        facts = await _facts_of(dict(existing), sticker_repo, admin_repo, bot_config_repo)
        card = build_stk_card(lang, facts)
        msg = await message.reply(
            render_text(card), reply_markup=to_markup(card), parse_mode="HTML"
        )
        await _remember(
            sticker_repo,
            msg,
            sticker.file_unique_id,
            message.from_user.id if message.from_user else None,
        )
        return

    # Reply (not answer): handle_admin_sticker_dm_analyze() below reads the
    # sticker back off callback.message.reply_to_message on the button tap —
    # no extra cache or DB row needed to carry file_id/set_name/emoji across
    # the tap (nothing to persist per ADR-0003's transient-UI-state stance).
    unknown = build_stk_unknown(lang, sticker.file_unique_id, sticker.emoji)
    await message.reply(render_text(unknown), reply_markup=to_markup(unknown), parse_mode="HTML")


@router.callback_query(
    F.data.startswith("adm_stk_dmchk:"), F.message.chat.type == "private", IsAdmin()
)
async def handle_admin_sticker_dm_analyze(
    callback: CallbackQuery,
    bot: Bot,
    sticker_service: FromDishka[StickerLearningService],
    sticker_repo: FromDishka[StickerRepository],
    admin_repo: FromDishka[AdminRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
    **kwargs: Any,
) -> None:
    """Analyse a sticker the admin sent to the bot; also the Retry of its error.

    The Sticker object (file_id, emoji, set_name, ...) comes from the message
    this reply is attached to, not from a DB lookup: on first contact there
    is no ``sticker_knowledge`` row yet. By the time Retry is tapped there may
    be one (a failed first analysis is stored), and then this is a plain
    re-analysis.
    """
    fields = parse(callback.data, 2)
    if fields is None:
        await show_stale(callback, _lang_hint(callback), data=kwargs, state=state)
        return
    lang, fuid = lang_of(fields[0]), fields[1]
    await _disarm(state)

    reply_msg = callback.message.reply_to_message if isinstance(callback.message, Message) else None
    sticker = reply_msg.sticker if reply_msg else None
    if sticker is None or sticker.file_unique_id != fuid:
        await callback.answer(ALERT_RESEND(lang), show_alert=True)
        return

    await callback.answer()

    existing = await sticker_repo.get_by_file_unique_id(fuid)
    if existing is not None:
        record = dict(existing)
        if record.get("visual_description") and not record.get("analysis_failed"):
            facts = await _facts_of(record, sticker_repo, admin_repo, bot_config_repo)
            msg = await show(callback, build_stk_card(lang, facts), answered=True)
            await _remember(sticker_repo, msg, fuid, callback.from_user.id)
            return
        await _analyse_into_card(
            callback,
            lang,
            fuid,
            bot,
            sticker_service,
            sticker_repo,
            admin_repo,
            bot_config_repo,
            notice=NOTICE_ANALYZED(lang),
            dm=True,
        )
        return

    await show(callback, build_stk_analyzing(lang), answered=True)
    try:
        image_data = await download_telegram_file(bot, sticker.file_id)
    except TelegramFileError:
        logger.warning("handle_admin_sticker_dm_analyze: download failed", file_unique_id=fuid)
        await show(callback, build_stk_error(lang, fuid, "download", dm=True), answered=True)
        return

    try:
        learning_result = await sticker_service.learn(sticker=sticker, image_data=image_data)
    except Exception:
        logger.error(
            "handle_admin_sticker_dm_analyze: learn crashed", file_unique_id=fuid, exc_info=True
        )
        await show(callback, build_stk_error(lang, fuid, "internal", dm=True), answered=True)
        return

    # Best-effort sticker-set registration so the admin panel's "browse by
    # set" stays consistent for manually-checked stickers too — parity with
    # the automatic group-chat learn path (handlers/media.py). notify_admins
    # and sticker-to-sticker reply are intentionally skipped here: the admin
    # is already looking at the synchronous result, and there is no chat
    # context to reply into.
    if sticker.set_name:
        try:
            existing_set = await sticker_repo.get_sticker_set(sticker.set_name)
            if not existing_set:
                tg_set = await bot.get_sticker_set(sticker.set_name)
                await sticker_repo.upsert_sticker_set(
                    set_name=tg_set.name,
                    set_title=tg_set.title,
                    total_count=len(tg_set.stickers),
                    thumbnail_file_id=(tg_set.thumbnail.file_id if tg_set.thumbnail else None),
                    is_animated=any(s.is_animated for s in tg_set.stickers[:1]),
                    is_video=any(s.is_video for s in tg_set.stickers[:1]),
                )
        except Exception:
            logger.warning(
                "handle_admin_sticker_dm_analyze: set registration failed",
                set_name=sticker.set_name,
            )

    if learning_result.analysis_failed:
        error = build_stk_error(lang, fuid, learning_result.failure_reason, dm=True)
        await show(callback, error, answered=True)
        return

    # ADR-0009 Decision 6 edge case: a freshly-learned duplicate can
    # inherit an existing manual score+flag from its canonical row, so
    # this first-ever card for the file can legitimately already show
    # the "(вручную)" badge -- not a bug, see the ADR's own note.
    learned = await _card_facts(fuid, sticker_repo, admin_repo, bot_config_repo)
    if learned is None:
        await show(callback, build_stk_error(lang, fuid, None, dm=True), answered=True)
        return
    card = build_stk_card(lang, learned, notice=NOTICE_LEARNED(lang))
    msg = await show(callback, card, answered=True)
    await _remember(sticker_repo, msg, fuid, callback.from_user.id)
