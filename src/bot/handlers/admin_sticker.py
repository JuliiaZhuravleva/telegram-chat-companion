"""Admin sticker management handlers.

Handles:
- Admin reply to sticker notification → merge description
- Sticker wizard callbacks (adm_stk_*) → browse sets, view stickers, re-analyze
"""

from __future__ import annotations

import contextlib
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from dishka import AsyncContainer
from dishka.integrations.aiogram import FromDishka

from src.bot.filters.admin import IsAdmin
from src.bot.keyboards.admin_sticker import (
    sticker_detail_keyboard,
    sticker_menu_keyboard,
    sticker_set_detail_keyboard,
    sticker_sets_keyboard,
)
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.stickers import StickerRepository
from src.services.modules.sticker import StickerLearningService

logger = structlog.get_logger(__name__)

router = Router(name="admin_sticker")


# ── Helper ──────────────────────────────────────────────────────────────


def _get_lang(raw: str | None) -> str:
    return raw if raw in ("ru", "en") else "ru"


async def _check_admin_direct(bot_config_repo: BotConfigRepository, user_id: int | None) -> bool:
    """Check if user is admin (for handlers with FromDishka parameters)."""
    if user_id is None:
        return False

    admin_ids_raw = await bot_config_repo.get("admin_ids")
    if not admin_ids_raw:
        return False

    try:
        if isinstance(admin_ids_raw, str):
            ids = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()]
        else:
            ids = [int(x) for x in admin_ids_raw]
    except (ValueError, TypeError):
        return False

    return user_id in ids


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
    if not admin_ids_raw:
        return False

    # Get user ID from parameter or kwargs
    if user_id is None:
        user_obj = kwargs.get("event_from_user")
        if user_obj is None:
            return False
        uid = user_obj.id if hasattr(user_obj, "id") else int(user_obj)
    else:
        uid = user_id

    try:
        if isinstance(admin_ids_raw, str):
            ids = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()]
        else:
            ids = [int(x) for x in admin_ids_raw]
    except (ValueError, TypeError):
        return False

    return uid in ids


def _is_private(callback: CallbackQuery) -> bool:
    return callback.message is not None and callback.message.chat.type == "private"


# ── Admin reply to sticker notification ──────────────────────────────────


@router.message(F.reply_to_message, F.text, IsAdmin())
async def handle_admin_sticker_reply(
    message: Message,
    sticker_repo: FromDishka[StickerRepository],
    sticker_service: FromDishka[StickerLearningService],
) -> None:
    """Admin replies to sticker notification → merge description."""
    if message.chat.type != "private":
        return
    if not message.reply_to_message or not message.text:
        return

    # Look up notification by reply
    notif = await sticker_repo.get_notification_by_reply(
        message.chat.id,
        message.reply_to_message.message_id,
    )
    if not notif:
        return

    file_unique_id = notif["file_unique_id"]
    admin_text = message.text.strip()

    if not admin_text:
        return

    # Merge description via AI
    new_desc = await sticker_service.merge_admin_description(
        file_unique_id, admin_text
    )

    if new_desc:
        await message.reply(
            f"Описание обновлено:\n<i>{new_desc}</i>",
            parse_mode="HTML",
        )
    else:
        await message.reply("Не удалось обновить описание.")


# ── Sticker menu ────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_stk:"))
async def handle_sticker_menu(
    callback: CallbackQuery,
    **kwargs: Any,
) -> None:
    """Sticker management main menu."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await _check_admin(kwargs, callback.from_user.id if callback.from_user else None):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)

    text = "Управление стикерами" if lang == "ru" else "Sticker Management"
    keyboard = sticker_menu_keyboard(lang)

    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


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
    if not await _check_admin_direct(bot_config_repo, callback.from_user.id if callback.from_user else None):
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
        text = (
            f"Стикерпаки ({total}):" if lang == "ru"
            else f"Sticker packs ({total}):"
        )

    keyboard = sticker_sets_keyboard(
        sets, lang=lang, page=page, total=total, per_page=per_page
    )

    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# ── Set detail ───────────────────────────────────────────────────────────


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
    if not await _check_admin_direct(bot_config_repo, callback.from_user.id if callback.from_user else None):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    set_name = parts[2] if len(parts) > 2 else ""
    page = int(parts[3]) if len(parts) > 3 else 0
    per_page = 10

    if not set_name:
        await callback.answer("Missing set name", show_alert=True)
        return

    total = await sticker_repo.count_stickers_in_set(set_name)
    sticker_records = await sticker_repo.get_stickers_in_set(
        set_name, limit=per_page, offset=page * per_page
    )
    stickers = [dict(r) for r in sticker_records]

    text = f"<b>{set_name}</b> ({total} stickers)"

    keyboard = sticker_set_detail_keyboard(
        stickers,
        set_name=set_name,
        lang=lang,
        page=page,
        total=total,
        per_page=per_page,
    )

    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ── Sticker detail ──────────────────────────────────────────────────────


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
    if not await _check_admin_direct(bot_config_repo, callback.from_user.id if callback.from_user else None):
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

    lines = []
    if sticker["visual_description"]:
        lines.append(f"<b>Описание:</b> {sticker['visual_description']}")
    if sticker["emotion"]:
        lines.append(f"<b>Эмоция:</b> {sticker['emotion']}")
    if sticker["character_or_meme"]:
        lines.append(f"<b>Персонаж:</b> {sticker['character_or_meme']}")
    if sticker["suggested_contexts"]:
        contexts = ", ".join(sticker["suggested_contexts"])
        lines.append(f"<b>Контексты:</b> {contexts}")
    lines.append(f"<b>Использований:</b> {sticker['total_uses']} (бот: {sticker['bot_uses']})")
    lines.append(f"<b>Emoji:</b> {sticker['emoji'] or '—'}")
    lines.append(f"<b>Animated:</b> {sticker['is_animated']}")
    lines.append(f"<b>Video:</b> {sticker['is_video']}")
    if sticker["analysis_failed"]:
        lines.append("<b>⚠️ Анализ провалился</b>")

    text = "\n".join(lines) or "No data"

    keyboard = sticker_detail_keyboard(
        file_unique_id,
        lang=lang,
        set_name=sticker["set_name"],
    )

    if callback.message:
        # Send sticker first, then details
        with contextlib.suppress(Exception):
            await callback.message.answer_sticker(sticker["file_id"])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ── Re-analyze ──────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm_stk_reanalyze:"))
async def handle_reanalyze(
    callback: CallbackQuery,
    sticker_repo: FromDishka[StickerRepository],
    bot_config_repo: FromDishka[BotConfigRepository],
) -> None:
    """Clear sticker analysis to force re-analysis on next encounter."""
    if not _is_private(callback):
        await callback.answer()
        return
    if not await _check_admin_direct(bot_config_repo, callback.from_user.id if callback.from_user else None):
        await callback.answer("Not authorized", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    lang = _get_lang(parts[1] if len(parts) > 1 else None)
    file_unique_id = parts[2] if len(parts) > 2 else ""

    if not file_unique_id:
        await callback.answer("Missing sticker ID", show_alert=True)
        return

    await sticker_repo.clear_for_reanalysis(file_unique_id)

    msg = (
        "Анализ сброшен. Стикер будет переанализирован при следующем использовании."
        if lang == "ru"
        else "Analysis cleared. Sticker will be re-analyzed on next use."
    )
    await callback.answer(msg, show_alert=True)
