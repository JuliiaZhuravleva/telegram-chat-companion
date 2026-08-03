"""Admin sticker management handlers.

Handles:
- Admin reply to sticker notification → merge description
- Sticker wizard callbacks (adm_stk_*) → browse sets, view stickers, re-analyze
"""

from __future__ import annotations

import contextlib
import html as html_lib
import re
from typing import Any

import structlog
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
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
from src.utils import parse_admin_ids
from src.utils.telegram import typing_indicator

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
    return user_id in parse_admin_ids(admin_ids_raw)


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


@router.message(F.reply_to_message, F.text, F.chat.type == "private", IsAdmin())
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

    if isinstance(callback.message, Message):
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
    if not await _check_admin_direct(
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
    if not await _check_admin_direct(
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
    if not await _check_admin_direct(
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
    if not await _check_admin_direct(
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

    lines = [f"🆔 <code>{html_lib.escape(file_unique_id)}</code>"]
    if sticker["visual_description"]:
        lines.append(f"<b>Описание:</b> {html_lib.escape(sticker['visual_description'])}")
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
    if sticker["analysis_failed"]:
        lines.append("<b>⚠️ Анализ провалился</b>")
    if sticker.get("admin_notes"):
        lines.append(f"<b>Заметки:</b> <i>{html_lib.escape(sticker['admin_notes'])}</i>")
    lines.append("\n<i>Ответь на это сообщение текстом, чтобы уточнить описание стикера.</i>")

    text = "\n".join(lines)

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
    if not await _check_admin_direct(
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

    await sticker_repo.clear_for_reanalysis(file_unique_id)

    msg = (
        "Анализ сброшен. Стикер будет переанализирован при следующем использовании."
        if lang == "ru"
        else "Analysis cleared. Sticker will be re-analyzed on next use."
    )
    await callback.answer(msg, show_alert=True)
