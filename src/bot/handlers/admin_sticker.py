"""Admin sticker management handlers.

Handles:
- Admin reply to sticker notification → merge description
- Sticker wizard callbacks (adm_stk_*) → browse sets, view stickers, re-analyze
- Admin sends a sticker directly in DM (B-1) → catalog check: known → show
  description, unknown → "Проанализировать" button. Registered here (not in
  handlers/media.py) so router order (handlers/__init__.py) makes it run
  before media.py's silent auto-learn for the admin's own DM.
"""

from __future__ import annotations

import contextlib
import html as html_lib
import re
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from dishka import AsyncContainer
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.keyboards.admin_sticker import (
    _EXPLICITNESS_PRESETS,
    _status_badge,
    sticker_clear_confirm_keyboard,
    sticker_detail_keyboard,
    sticker_dm_check_keyboard,
    sticker_explicitness_cancel_keyboard,
    sticker_reanalyze_retry_keyboard,
    sticker_set_detail_keyboard,
    sticker_sets_keyboard,
)
from src.bot.states.admin import AdminStates
from src.bot.utils import check_admin_direct, safe_edit_text
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.stickers import StickerRepository
from src.services.modules.sticker import StickerLearningService
from src.services.modules.sticker.tolerance import format_explicitness_line
from src.utils import parse_admin_ids
from src.utils.telegram import TelegramFileError, download_telegram_file, typing_indicator

logger = structlog.get_logger(__name__)

router = Router(name="admin_sticker")


# ── Helper ──────────────────────────────────────────────────────────────


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


async def _check_admin(kwargs: dict[str, Any], user_id: int | None = None) -> bool:
    """Verify admin status via Dishka container (for handlers without FromDishka params).

    Args:
        kwargs: Handler kwargs (must contain dishka_container).
        user_id: Optional user ID to check. If None, tries to get from kwargs['event_from_user'].
    """
    container: AsyncContainer | None = kwargs.get("dishka_container")
    if container is None:
        return False

    bot_config_repo = await container.get(BotConfigRepository)
    admin_ids_raw = await bot_config_repo.get("admin_ids")

    # Get user ID from parameter or kwargs
    if user_id is None:
        user_obj = kwargs.get("event_from_user")
        if user_obj is None:
            return False
        uid = user_obj.id if hasattr(user_obj, "id") else int(user_obj)
    else:
        uid = user_id

    return uid in parse_admin_ids(admin_ids_raw)


def _is_private(callback: CallbackQuery) -> bool:
    return isinstance(callback.message, Message) and callback.message.chat.type == "private"


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


# ── Admin reply to sticker notification ──────────────────────────────────


# StateFilter(None): this router precedes every FSM-owning router, so while
# an FSM dialog is active (e.g. the tolerance prompt, which the admin may
# answer as a *reply*) this handler must yield instead of swallowing the
# input as a description correction (2026-08-07 review).
@router.message(F.reply_to_message, F.text, F.chat.type == "private", IsAdmin(), StateFilter(None))
async def handle_admin_sticker_reply(
    message: Message,
    sticker_repo: FromDishka[StickerRepository],
    sticker_service: FromDishka[StickerLearningService],
    bot: Bot,
    message_thread_id: int | None = None,
) -> None:
    """Admin replies to sticker notification → merge description."""
    if not message.reply_to_message or not message.text:
        return

    # Look up notification by reply (primary: DB, fallback: text parsing)
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

    # Merge description via AI (fallback: save note directly)
    try:
        async with typing_indicator(bot, message.chat.id, message_thread_id):
            new_desc = await sticker_service.merge_admin_description(file_unique_id, admin_text)
    except ValueError as e:
        if str(e) == "content_filter":
            await message.reply(
                "Фильтр контента заблокировал запрос. Попробуй переформулировать текст.",
            )
        else:
            await message.reply("Произошла ошибка. Попробуй ещё раз.")
        return
    except Exception:
        logger.exception("Sticker merge failed", file_unique_id=file_unique_id)
        new_desc = None

    if new_desc:
        await message.reply(
            f"Описание обновлено:\n<i>{html_lib.escape(new_desc)}</i>",
            parse_mode="HTML",
        )
    else:
        await message.reply(
            "AI не смог объединить описание. Заметка сохранена. "
            "Попробуй ещё раз или используй Re-analyze.",
        )


# ── Set list ─────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_stk_sets:"))
async def handle_sticker_sets(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Browse sticker sets with pagination."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    page = int(parts[2]) if len(parts) > 2 else 0
    per_page = 10

    total = await sticker_repo.count_sets()
    sets_records = await sticker_repo.get_all_sets_with_stats(
        limit=per_page, offset=page * per_page
    )
    sets = [dict(r) for r in sets_records]

    if not sets:
        text = "Нет изученных стикерпаков" if lang == "ru" else "No learned sticker packs"
    else:
        text = f"Стикерпаки ({total}):" if lang == "ru" else f"Sticker packs ({total}):"

    keyboard = sticker_sets_keyboard(sets, lang=lang, page=page, total=total, per_page=per_page)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# ── Set detail ───────────────────────────────────────────────────────────


async def _build_set_view(
    sticker_repo: StickerRepository,
    set_name: str,
    lang: str,
    page: int,
    per_page: int = 10,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build text + keyboard for a sticker set detail view."""
    total = await sticker_repo.count_stickers_in_set(set_name)
    sticker_records = await sticker_repo.get_stickers_in_set(
        set_name, limit=per_page, offset=page * per_page
    )
    stickers = [dict(r) for r in sticker_records]

    text = f"<b>{html_lib.escape(set_name)}</b> ({total} stickers)"
    keyboard = sticker_set_detail_keyboard(
        stickers,
        set_name=set_name,
        lang=lang,
        page=page,
        total=total,
        per_page=per_page,
    )
    return text, keyboard


@router.callback_query(F.data.startswith("adm_stk_set:"))
async def handle_sticker_set_view(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """View stickers in a specific set."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    set_name = parts[2] if len(parts) > 2 else ""
    page = int(parts[3]) if len(parts) > 3 else 0

    if not set_name:
        await callback.answer("Missing set name", show_alert=True)
        return

    text, keyboard = await _build_set_view(sticker_repo, set_name, lang, page)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ── Back from sticker detail (cleanup sticker message) ──────────────────


@router.callback_query(F.data.startswith("adm_stk_back:"))
async def handle_sticker_back(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Navigate back from sticker detail, cleaning up sticker + description messages."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    # adm_stk_back:{lang}:{set_name}:{page}
    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    set_name = parts[2] if len(parts) > 2 else ""
    page = int(parts[3]) if len(parts) > 3 else 0

    if not set_name:
        await callback.answer("Missing set name", show_alert=True)
        return

    # Delete the sticker message via DB lookup (not callback_data)
    admin_id = callback.from_user.id if callback.from_user else None
    if admin_id and isinstance(callback.message, Message) and callback.message.bot:
        sticker_msg_id = await sticker_repo.get_latest_sticker_msg(
            admin_id,
            callback.message.chat.id,
        )
        if sticker_msg_id:
            with contextlib.suppress(Exception):
                await callback.message.bot.delete_message(
                    chat_id=callback.message.chat.id,
                    message_id=sticker_msg_id,
                )

    # Delete the description message (the one with this callback)
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.delete()

    # Send fresh set list
    text, keyboard = await _build_set_view(sticker_repo, set_name, lang, page)

    if isinstance(callback.message, Message):
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ── Sticker detail ──────────────────────────────────────────────────────


def _build_detail_text(
    sticker: dict[str, Any],
    file_unique_id: str,
    lang: str,
    tolerance_level: float,
) -> str:
    """Build the HTML detail body for a single sticker.

    Shared by the detail view and the post-clear re-render so both render the
    sticker identically — including the ⏳ not-analyzed badge when the visual
    description is absent.

    ``tolerance_level`` (A-1): threaded in by every caller via
    ``_resolve_default_tolerance_level()`` (or, for a card tied to a real
    chat, that chat's own resolved ``ChatConfig.tolerance_level``) so the
    оценка откровенности line's pass/fail verdict is computed the same way
    everywhere.
    """
    lines = [f"🆔 <code>{html_lib.escape(file_unique_id)}</code>"]
    if sticker["visual_description"]:
        lines.append(f"<b>Описание:</b> {html_lib.escape(sticker['visual_description'])}")
    else:
        lines.append(f"<b>{html_lib.escape(_status_badge(sticker, lang, short=False))}</b>")
    if sticker["emotion"]:
        lines.append(f"<b>Эмоция:</b> {html_lib.escape(sticker['emotion'])}")
    if sticker["character_or_meme"]:
        lines.append(f"<b>Персонаж:</b> {html_lib.escape(sticker['character_or_meme'])}")
    if sticker["suggested_contexts"]:
        contexts = ", ".join(html_lib.escape(c) for c in sticker["suggested_contexts"])
        lines.append(f"<b>Контексты:</b> {contexts}")
    lines.append(f"<b>Использований:</b> {sticker['total_uses']} (бот: {sticker['bot_uses']})")
    lines.append(f"<b>Emoji:</b> {html_lib.escape(sticker['emoji'] or '—')}")
    lines.append(f"<b>Animated:</b> {sticker['is_animated']}")
    lines.append(f"<b>Video:</b> {sticker['is_video']}")
    # Explicitness line (A-1) only once the sticker has been through vision
    # analysis at all — an entirely un-analyzed sticker's explicitness_score
    # is always NULL too (same vision call, ADR-0008 Decision 4), and the
    # ⏳/⚠️ status badge above already covers that state; repeating "не
    # оценён" here would just be noise on a card the PRD asked to keep tight.
    if sticker["visual_description"]:
        lines.append(
            format_explicitness_line(
                sticker.get("explicitness_score"),
                tolerance_level,
                lang,
                is_manual=bool(sticker.get("explicitness_is_manual", False)),
            )
        )
    if sticker.get("admin_notes"):
        lines.append(f"<b>Заметки:</b> <i>{html_lib.escape(sticker['admin_notes'])}</i>")
    lines.append("\n<i>Ответь на это сообщение текстом, чтобы уточнить описание стикера.</i>")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("adm_stk_view:"))
async def handle_sticker_detail(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """View details of a single sticker."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not file_unique_id:
        await callback.answer("Missing sticker ID", show_alert=True)
        return

    sticker = await sticker_repo.get_by_file_unique_id(file_unique_id)
    if not sticker:
        await callback.answer("Sticker not found", show_alert=True)
        return

    tolerance_level = await _resolve_default_tolerance_level(bot_config_repo)
    text = _build_detail_text(sticker, file_unique_id, lang, tolerance_level)

    if isinstance(callback.message, Message):
        # Clean up previous sticker message (if any) to prevent orphans
        admin_id = callback.from_user.id if callback.from_user else None
        if admin_id and callback.message.bot:
            old_sticker_msg_id = await sticker_repo.get_latest_sticker_msg(
                admin_id,
                callback.message.chat.id,
            )
            if old_sticker_msg_id:
                with contextlib.suppress(Exception):
                    await callback.message.bot.delete_message(
                        chat_id=callback.message.chat.id,
                        message_id=old_sticker_msg_id,
                    )

        # Delete the old message (set list or previous detail) so sticker
        # and description appear adjacent as new messages.
        with contextlib.suppress(Exception):
            await callback.message.delete()

        # Send sticker, then description right after
        sticker_msg = None
        with contextlib.suppress(Exception):
            sticker_msg = await callback.message.answer_sticker(sticker["file_id"])

        keyboard = sticker_detail_keyboard(
            file_unique_id,
            lang=lang,
            set_name=sticker["set_name"],
            explicitness_is_manual=bool(sticker.get("explicitness_is_manual", False)),
        )
        desc_msg = await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)

        # Save notification so admin can reply to edit description
        admin_id = callback.from_user.id if callback.from_user else None
        if admin_id:
            with contextlib.suppress(Exception):
                await sticker_repo.save_notification(
                    file_unique_id=file_unique_id,
                    admin_id=admin_id,
                    message_id=desc_msg.message_id,
                    sticker_msg_id=sticker_msg.message_id if sticker_msg else 0,
                    chat_id=callback.message.chat.id,
                )
    await callback.answer()


# ── Clear analysis (confirm + commit) ───────────────────────────────────


@router.callback_query(F.data.startswith("adm_stk_clr_ask:"))
async def handle_clear_ask(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Show confirm dialog before clearing sticker analysis."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not file_unique_id:
        await callback.answer("Missing sticker ID", show_alert=True)
        return

    text = (
        "Очистить анализ? Описание, эмоция, персонаж и контексты будут сброшены. "
        "Ручные заметки и статистика использований сохранятся."
        if lang == "ru"
        else "Clear analysis? Description, emotion, character and contexts will be reset. "
        "Admin notes and usage counters are preserved."
    )
    keyboard = sticker_clear_confirm_keyboard(file_unique_id, lang=lang)

    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_stk_clr:"))
async def handle_clear(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Clear all vision-generated fields for a sticker."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not file_unique_id:
        await callback.answer("Missing sticker ID", show_alert=True)
        return

    await sticker_repo.clear_analysis(file_unique_id)

    # Re-render the detail in place so the admin lands back on the (now
    # ⏳ not-analyzed) sticker detail instead of being stranded on the confirm
    # prompt. Matches the edit-in-place idiom used by handle_run_analysis.
    sticker = await sticker_repo.get_by_file_unique_id(file_unique_id)
    if sticker and isinstance(callback.message, Message):
        tolerance_level = await _resolve_default_tolerance_level(bot_config_repo)
        text = _build_detail_text(sticker, file_unique_id, lang, tolerance_level)
        keyboard = sticker_detail_keyboard(
            file_unique_id,
            lang=lang,
            set_name=sticker["set_name"],
            explicitness_is_manual=bool(sticker.get("explicitness_is_manual", False)),
        )
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    msg = "Анализ очищен" if lang == "ru" else "Analysis cleared"
    await callback.answer(msg, show_alert=True)


# ── Manual explicitness override (A-4, ADR-0009) ────────────────────────

_EXPLICITNESS_PROMPT = {
    "ru": "Введите оценку откровенности стикера (0.0–1.0):",
    "en": "Enter the sticker's explicitness score (0.0–1.0):",
}
_EXPLICITNESS_NO_ROW = {
    "ru": "Стикер не найден в каталоге — оценка не изменена.",
    "en": "Sticker not found in the catalog — score unchanged.",
}
_EXPLICITNESS_INVALID = {
    "ru": "Нужно число от 0.0 до 1.0. Попробуйте ещё раз.",
    "en": "Enter a number between 0.0 and 1.0. Try again.",
}
_EXPLICITNESS_SAVED = {
    "ru": "Оценка откровенности установлена вручную: {value}",
    "en": "Explicitness score manually set: {value}",
}
_EXPLICITNESS_CANCELLED = {"ru": "Отменено", "en": "Cancelled"}
_EXPLICITNESS_RESET = {
    "ru": "Сброшено к автоматической оценке (не оценён до следующего анализа)",
    "en": "Reset to automatic (not scored until the next analysis)",
}


async def _render_and_show_detail(
    callback: CallbackQuery,
    sticker_repo: StickerRepository,
    bot_config_repo: BotConfigRepository,
    lang: str,
    file_unique_id: str,
) -> None:
    """Re-render the sticker detail card in place (shared by the preset/
    reset/cancel callbacks below) -- same edit-in-place idiom as
    ``handle_clear``.
    """
    if not isinstance(callback.message, Message):
        return
    sticker = await sticker_repo.get_by_file_unique_id(file_unique_id)
    if not sticker:
        return
    tolerance_level = await _resolve_default_tolerance_level(bot_config_repo)
    text = _build_detail_text(sticker, file_unique_id, lang, tolerance_level)
    keyboard = sticker_detail_keyboard(
        file_unique_id,
        lang=lang,
        set_name=sticker["set_name"],
        explicitness_is_manual=bool(sticker.get("explicitness_is_manual", False)),
    )
    # safe_edit_text, not a blanket suppress(TelegramBadRequest): the DB write
    # has already happened and the caller is about to announce success, so a
    # re-render that fails for any *other* reason (message too long once a long
    # visual_description and admin_notes are on the card, message gone) must
    # surface rather than leave a stale card under a "saved" toast.
    await safe_edit_text(callback.message, text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("adm_stk_expset:"))
async def handle_sticker_explicitness_preset(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
) -> None:
    """Set a preset explicitness value with one tap (ADR-0009 Decision 7,
    closing paragraph) -- the button already fixes a known-valid value, so
    no FSM input is needed.

    It still has to *clear* the FSM, though: the "✏️ Указать число" prompt is
    sent as a new message, so the card above it keeps its preset buttons live.
    An admin who opens the prompt and then taps a preset instead would leave
    ``awaiting_sticker_score`` set, and the next plain DM text — a description
    correction, say — would be eaten by the score-input handler (which also
    outranks ``handle_admin_sticker_reply``'s ``StateFilter(None)``).
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not file_unique_id:
        await callback.answer("Missing sticker ID", show_alert=True)
        return

    try:
        value = _EXPLICITNESS_PRESETS[int(parts[3])]
    except (ValueError, IndexError):
        await callback.answer("Invalid preset", show_alert=True)
        return

    await state.clear()
    if not await sticker_repo.set_manual_explicitness_score(file_unique_id, value):
        await callback.answer(_EXPLICITNESS_NO_ROW[lang], show_alert=True)
        return
    await _render_and_show_detail(callback, sticker_repo, bot_config_repo, lang, file_unique_id)

    msg = f"Оценка: {value:.2f} (вручную)" if lang == "ru" else f"Score: {value:.2f} (manual)"
    await callback.answer(msg)


@router.callback_query(F.data.startswith("adm_stk_expedit:"))
async def handle_sticker_explicitness_edit_prompt(
    callback: CallbackQuery,
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
) -> None:
    """Prompt for a free-text explicitness value (ADR-0009 Decision 7).

    Builds against ``AdminStates.awaiting_sticker_score`` rather than the
    two already-spoken-for scaffold states (see the state's own docstring).
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not file_unique_id:
        await callback.answer("Missing sticker ID", show_alert=True)
        return

    await state.set_state(AdminStates.awaiting_sticker_score)
    await state.update_data(exp_file_unique_id=file_unique_id, exp_lang=lang)

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _EXPLICITNESS_PROMPT[lang],
            reply_markup=sticker_explicitness_cancel_keyboard(file_unique_id, lang=lang),
        )


@router.message(
    AdminStates.awaiting_sticker_score,
    F.chat.type == "private",
    ~F.text.startswith("/"),
)
async def handle_sticker_explicitness_input(
    message: Message,
    state: FSMContext,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Validate and persist the manually-typed explicitness score
    (ADR-0009 Decisions 5 and 7).

    Reject-not-clamp on invalid input (same posture as
    ``admin_chat_panel.py``'s tolerance FSM) -- re-prompts, state stays set.
    Escape hatches: admin commands pass through untouched
    (``~F.text.startswith("/")``), and the prompt carries a dedicated
    cancel button.
    """
    data = await state.get_data()
    file_unique_id = data.get("exp_file_unique_id")
    lang = _get_lang(data.get("exp_lang"))
    if not file_unique_id:
        await state.clear()
        return

    if not await check_admin_direct(
        bot_config_repo, message.from_user.id if message.from_user else None
    ):
        await state.clear()
        return

    raw = (message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        await message.reply(
            _EXPLICITNESS_INVALID[lang],
            reply_markup=sticker_explicitness_cancel_keyboard(file_unique_id, lang=lang),
        )
        return
    if not 0.0 <= value <= 1.0:
        await message.reply(
            _EXPLICITNESS_INVALID[lang],
            reply_markup=sticker_explicitness_cancel_keyboard(file_unique_id, lang=lang),
        )
        return

    await state.clear()
    if not await sticker_repo.set_manual_explicitness_score(file_unique_id, value):
        await message.reply(_EXPLICITNESS_NO_ROW[lang])
        return

    await message.reply(_EXPLICITNESS_SAVED[lang].format(value=f"{value:.2f}"))

    sticker = await sticker_repo.get_by_file_unique_id(file_unique_id)
    if sticker:
        tolerance_level = await _resolve_default_tolerance_level(bot_config_repo)
        text = _build_detail_text(sticker, file_unique_id, lang, tolerance_level)
        keyboard = sticker_detail_keyboard(
            file_unique_id,
            lang=lang,
            set_name=sticker["set_name"],
            explicitness_is_manual=True,
        )
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("adm_stk_expcancel:"))
async def handle_sticker_explicitness_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Escape hatch for ``awaiting_sticker_score`` -- the input handler
    keeps the state set on invalid input (reject-not-clamp), so cancelling
    must be reachable without typing a valid float. Turns the prompt bubble
    into a fresh detail card, same idiom as
    ``admin_chat_panel.handle_chat_panel_tolerance_cancel``.
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    await state.clear()
    await callback.answer(_EXPLICITNESS_CANCELLED[lang])
    if file_unique_id:
        await _render_and_show_detail(callback, sticker_repo, bot_config_repo, lang, file_unique_id)


@router.callback_query(F.data.startswith("adm_stk_expreset:"))
async def handle_sticker_explicitness_reset(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
    state: FSMContext,
) -> None:
    """Reset a manual score back to automatic (ADR-0009 Decision 5) -- NULLs
    both the value and the flag; the card re-renders as "не оценён" until
    the next (re-)analysis, reusing A-1's existing unscored rendering.

    Clears the FSM for the same reason the preset buttons do: this row stays
    tappable on a card sitting above an open "введите число" prompt.
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not file_unique_id:
        await callback.answer("Missing sticker ID", show_alert=True)
        return

    await state.clear()
    if not await sticker_repo.reset_explicitness_to_auto(file_unique_id):
        await callback.answer(_EXPLICITNESS_NO_ROW[lang], show_alert=True)
        return
    await _render_and_show_detail(callback, sticker_repo, bot_config_repo, lang, file_unique_id)
    await callback.answer(_EXPLICITNESS_RESET[lang])


# ── Localized failure-reason copy ───────────────────────────────────────

_REANALYZE_REASON_COPY: dict[str, dict[str, str]] = {
    "download": {"ru": "Ошибка загрузки", "en": "Download error"},
    "vision": {"ru": "Ошибка API", "en": "API error"},
    "content_filter": {"ru": "Контент заблокирован", "en": "Content blocked"},
    "empty": {"ru": "Пустой ответ", "en": "Empty response"},
}


# ── Run analysis now ────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_stk_reanalyze:"))
async def handle_run_analysis(
    callback: CallbackQuery,
    sticker_service: FromDishka[StickerLearningService],
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Run vision analysis on a sticker right now (admin action).

    Lifecycle (edit-in-place):
    1. Edit message → ⏳ Анализирую… (hide buttons) before blocking vision call.
    2. Call reanalyze().
    3. Edit message → ✅ Анализ обновлён + new description + restored buttons,
       OR ⚠️ Ошибка анализа: <reason> + Retry button.

    Edge-cases:
    - If the ⏳ edit fails (e.g. network glitch), log and continue — still emit result.
    - TelegramBadRequest "message is not modified" on any status edit is suppressed.
    - Double-tap: first tap removes the buttons; second tap hits an unmodified message
      (suppressed) or arrives after the analysis has already finished.
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not file_unique_id:
        await callback.answer("Missing sticker ID", show_alert=True)
        return

    if not (isinstance(callback.message, Message) and callback.message.bot):
        await callback.answer("Bot unavailable", show_alert=True)
        return

    # Dismiss the callback spinner immediately
    await callback.answer()

    # ── ⏳ in-progress edit: hide buttons so the admin knows work is underway ──
    in_progress_text = "⏳ Анализирую…" if lang == "ru" else "⏳ Analyzing…"
    try:
        await callback.message.edit_text(
            in_progress_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
    except TelegramBadRequest as exc:
        # "message is not modified" = double-tap; another network issue = log + continue
        logger.warning(
            "handle_run_analysis: in-progress edit failed, continuing analysis",
            file_unique_id=file_unique_id,
            error=str(exc),
        )

    # ── Blocking vision call ─────────────────────────────────────────────────
    result = await sticker_service.reanalyze(callback.message.bot, file_unique_id)

    # ── Build result text + keyboard ─────────────────────────────────────────
    if result.ok:
        # Fetch the updated sticker record to show its new description
        updated = await sticker_repo.get_by_file_unique_id(file_unique_id)
        desc_part = ""
        set_name: str | None = None
        is_manual = False
        if updated:
            set_name = str(updated["set_name"]) if updated.get("set_name") else None
            is_manual = bool(updated.get("explicitness_is_manual", False))
            raw_desc = updated.get("visual_description")
            if raw_desc:
                desc_part = f"\n<b>Описание:</b> {html_lib.escape(str(raw_desc))}"
                # A-1: same explicitness line as every other DM sticker card,
                # only once the fresh analysis actually produced a description.
                # A-4: a sticky manual override (ADR-0009 Decision 4) survives
                # this re-analysis unchanged -- is_manual reflects that.
                tolerance_level = await _resolve_default_tolerance_level(bot_config_repo)
                explicitness_line = format_explicitness_line(
                    updated.get("explicitness_score"),
                    tolerance_level,
                    lang,
                    is_manual=is_manual,
                )
                desc_part = f"{desc_part}\n{explicitness_line}"
        result_text = (
            f"✅ Анализ обновлён{desc_part}" if lang == "ru" else f"✅ Analysis updated{desc_part}"
        )
        keyboard = sticker_detail_keyboard(
            file_unique_id, lang=lang, set_name=set_name, explicitness_is_manual=is_manual
        )
    else:
        reason_key = result.reason or "empty"
        reason_copy = _REANALYZE_REASON_COPY.get(reason_key, _REANALYZE_REASON_COPY["empty"])
        reason_label = reason_copy.get(lang, reason_copy["ru"])
        result_text = (
            f"⚠️ Ошибка анализа: {reason_label}"
            if lang == "ru"
            else f"⚠️ Analysis error: {reason_label}"
        )
        keyboard = sticker_reanalyze_retry_keyboard(file_unique_id, lang=lang)

    # ── Edit message with result (suppress "not modified" on double-tap) ─────
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=keyboard)


# ── DM sticker check (admin sends a sticker directly, no command) ───────


@router.message(F.sticker, F.chat.type == "private", IsAdmin())
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
    fires for an admin's own DM check. Known sticker → the existing detail
    view (same renderer as the sets browser). Unknown → an explicit
    "🔍 Проанализировать" button; analysis only runs on that tap, never
    silently (ADR-0003 — analysis stays a visible, synchronous admin action).
    """
    sticker = message.sticker
    if sticker is None:
        return

    lang = _get_lang(await admin_repo.get_admin_language(bot_config_repo))

    existing = await sticker_repo.get_by_file_unique_id(sticker.file_unique_id)
    if existing:
        tolerance_level = await _resolve_default_tolerance_level(bot_config_repo)
        text = _build_detail_text(existing, sticker.file_unique_id, lang, tolerance_level)
        keyboard = sticker_detail_keyboard(
            sticker.file_unique_id,
            lang=lang,
            set_name=existing["set_name"],
            explicitness_is_manual=bool(existing.get("explicitness_is_manual", False)),
        )
        await message.reply(text, parse_mode="HTML", reply_markup=keyboard)
        return

    text = (
        "Такого стикера ещё нет в базе."
        if lang == "ru"
        else "This sticker isn't in the catalog yet."
    )
    # Reply (not answer): handle_admin_sticker_dm_analyze() below reads the
    # sticker back off callback.message.reply_to_message on the button tap —
    # no extra cache or DB row needed to carry file_id/set_name/emoji across
    # the tap (nothing to persist per ADR-0003's transient-UI-state stance).
    await message.reply(
        text, reply_markup=sticker_dm_check_keyboard(sticker.file_unique_id, lang=lang)
    )


@router.callback_query(F.data.startswith("adm_stk_dmchk:"))
async def handle_admin_sticker_dm_analyze(
    callback: CallbackQuery,
    sticker_service: FromDishka[StickerLearningService],
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Run vision analysis for a sticker checked via DM that wasn't known yet.

    Mirrors handle_run_analysis()'s edit-in-place lifecycle (ADR-0003), but
    there is no existing sticker_knowledge row to re-analyze: the Sticker
    object (file_id, emoji, set_name, ...) comes from the message this
    prompt replied to, not from a DB lookup.
    """
    if not _is_private(callback):
        await callback.answer()
        return
    if not await check_admin_direct(
        bot_config_repo, callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not (isinstance(callback.message, Message) and callback.message.bot):
        await callback.answer("Bot unavailable", show_alert=True)
        return

    reply_msg = callback.message.reply_to_message
    sticker = reply_msg.sticker if reply_msg else None
    if sticker is None or sticker.file_unique_id != file_unique_id:
        await callback.answer(
            "Стикер недоступен, пришли его ещё раз"
            if lang == "ru"
            else "Sticker unavailable, please resend it",
            show_alert=True,
        )
        return

    # Dismiss the callback spinner immediately
    await callback.answer()

    # ── ⏳ in-progress edit: hide buttons so the admin knows work is underway ──
    in_progress_text = "⏳ Анализирую…" if lang == "ru" else "⏳ Analyzing…"
    try:
        await callback.message.edit_text(
            in_progress_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
    except TelegramBadRequest as exc:
        logger.warning(
            "handle_admin_sticker_dm_analyze: in-progress edit failed, continuing analysis",
            file_unique_id=file_unique_id,
            error=str(exc),
        )

    # ── Download + learn ──────────────────────────────────────────────────
    try:
        image_data = await download_telegram_file(callback.message.bot, sticker.file_id)
    except TelegramFileError:
        logger.warning(
            "handle_admin_sticker_dm_analyze: download failed",
            file_unique_id=file_unique_id,
        )
        reason_copy = _REANALYZE_REASON_COPY["download"]
        result_text = (
            f"⚠️ Ошибка анализа: {reason_copy['ru']}"
            if lang == "ru"
            else f"⚠️ Analysis error: {reason_copy['en']}"
        )
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(
                result_text,
                parse_mode="HTML",
                reply_markup=sticker_reanalyze_retry_keyboard(file_unique_id, lang=lang),
            )
        return

    learning_result = await sticker_service.learn(sticker=sticker, image_data=image_data)

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
                tg_set = await callback.message.bot.get_sticker_set(sticker.set_name)
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

    # ── Build result text + keyboard (mirrors handle_run_analysis) ─────────
    if learning_result.analysis_failed:
        reason_key = learning_result.failure_reason or "empty"
        reason_copy = _REANALYZE_REASON_COPY.get(reason_key, _REANALYZE_REASON_COPY["empty"])
        reason_label = reason_copy.get(lang, reason_copy["ru"])
        result_text = (
            f"⚠️ Ошибка анализа: {reason_label}"
            if lang == "ru"
            else f"⚠️ Analysis error: {reason_label}"
        )
        keyboard = sticker_reanalyze_retry_keyboard(file_unique_id, lang=lang)
    else:
        updated = await sticker_repo.get_by_file_unique_id(file_unique_id)
        # ADR-0009 Decision 6 edge case: a freshly-learned duplicate can
        # inherit an existing manual score+flag from its canonical row, so
        # this first-ever card for the file can legitimately already show
        # the "(вручную)" badge -- not a bug, see the ADR's own note.
        is_manual = bool(updated.get("explicitness_is_manual", False)) if updated else False
        if updated:
            tolerance_level = await _resolve_default_tolerance_level(bot_config_repo)
            result_text = _build_detail_text(updated, file_unique_id, lang, tolerance_level)
        else:
            result_text = "✅ Анализ завершён" if lang == "ru" else "✅ Analysis complete"
        keyboard = sticker_detail_keyboard(
            file_unique_id, lang=lang, set_name=sticker.set_name, explicitness_is_manual=is_manual
        )

    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=keyboard)
