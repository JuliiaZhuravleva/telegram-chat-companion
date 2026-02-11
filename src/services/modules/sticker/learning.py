"""Sticker learning and search service.

Learns sticker meanings via Vision API, generates embeddings for semantic search.
Supports static, animated (.tgs), and video (.webm) stickers.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import structlog
from aiogram import Bot, types

from src.database.repositories.stickers import StickerRepository
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter
from src.services.modules.sticker.models import (
    StickerLearningResult,
    StickerRenderError,
    StickerSearchResult,
)
from src.services.modules.sticker.renderer import render_tgs, render_webm

logger = structlog.get_logger(__name__)

# Edit mode detection patterns for admin merge
_CORRECTION_PATTERNS = re.compile(
    r"(?:это не|неправильно|на самом деле|ошибка|wrong|actually|not)",
    re.IGNORECASE,
)
_ADDITION_PATTERNS = re.compile(
    r"(?:также|ещё|еще|дополнительно|плюс|also|additionally|plus)",
    re.IGNORECASE,
)


class StickerLearningService:
    """Learn and understand stickers via Vision API + embeddings."""

    def __init__(
        self,
        ai_router: AIRouter,
        sticker_repo: StickerRepository,
    ) -> None:
        self._ai = ai_router
        self._repo = sticker_repo

    # ── Learning ─────────────────────────────────────────────────────

    async def learn(
        self,
        *,
        sticker: types.Sticker,
        image_data: bytes,
        preceding_messages: list[str] | None = None,
    ) -> StickerLearningResult:
        """Learn a new sticker or update usage of existing one.

        Args:
            sticker: Telegram Sticker object.
            image_data: Raw file bytes (.webp/.tgs/.webm).
            preceding_messages: Last few chat messages for usage context.

        Returns:
            StickerLearningResult with learning outcome.
        """
        file_unique_id = sticker.file_unique_id

        # Check if sticker already exists
        existing = await self._repo.get_by_file_unique_id(file_unique_id)
        if existing:
            await self._repo.increment_usage(file_unique_id)
            # Accumulate usage context from preceding messages
            if preceding_messages:
                context_text = " | ".join(preceding_messages[:3])
                if len(context_text) >= 5:
                    await self._repo.accumulate_context(
                        file_unique_id, context_text[:200]
                    )
            return StickerLearningResult(
                is_new=False,
                file_unique_id=file_unique_id,
                visual_description=existing["visual_description"],
                emotion=existing["emotion"],
                character_or_meme=existing["character_or_meme"],
            )

        # Determine sticker type and prepare image for Vision API
        sticker_type = _detect_sticker_type(sticker)
        vision_image = image_data

        if sticker_type != "static":
            try:
                if sticker_type == "animated":
                    vision_image = await render_tgs(image_data)
                else:
                    vision_image = await render_webm(image_data)
            except StickerRenderError:
                logger.warning(
                    "Sticker rendering failed, saving without analysis",
                    file_unique_id=file_unique_id,
                    sticker_type=sticker_type,
                )
                await self._repo.save_sticker(
                    file_unique_id=file_unique_id,
                    file_id=sticker.file_id,
                    set_name=sticker.set_name,
                    emoji=sticker.emoji,
                    is_animated=sticker.is_animated,
                    is_video=sticker.is_video,
                    analysis_failed=True,
                )
                return StickerLearningResult(
                    is_new=True,
                    file_unique_id=file_unique_id,
                    analysis_failed=True,
                )

        # Get pack context (other stickers from same set)
        pack_context: list[str] | None = None
        if sticker.set_name:
            pack_records = await self._repo.get_pack_context(
                sticker.set_name, exclude_file_unique_id=file_unique_id
            )
            if pack_records:
                pack_context = [r["visual_description"] for r in pack_records]

        # Web search enrichment: identify character/meme for new packs
        character_hint: str | None = None
        web_context: str | None = None
        if sticker.set_name and not pack_context:
            character_hint, web_context = await self._search_pack_context_via_ai(
                sticker.set_name
            )

        # Vision API analysis
        prompt = self._build_vision_prompt(
            sticker,
            sticker_type=sticker_type,
            pack_context=pack_context,
            character_hint=character_hint,
            web_context=web_context,
        )
        # Determine mime type for the Vision API
        if sticker_type == "static":
            mime_type = "image/webp"
        else:
            mime_type = "image/png"  # rendered collages are PNG

        parsed: dict[str, Any] = {}
        try:
            vision_result = await self._ai.analyze_image(
                image_data=vision_image,
                prompt=prompt,
                mime_type=mime_type,
                response_mime_type="application/json",
            )
            parsed = self._parse_vision_response(vision_result.text)
        except AIProviderError:
            logger.exception(
                "Vision API failed for sticker",
                file_unique_id=file_unique_id,
            )

        visual = parsed.get("visual")
        emotion = parsed.get("emotion")
        contexts = parsed.get("contexts")
        tags = parsed.get("tags") or []
        character = parsed.get("character")
        analysis_failed = not bool(visual)

        # Auto-add format tag
        if sticker_type == "animated" and "animated" not in tags:
            tags.append("animated")
        elif sticker_type == "video" and "video" not in tags:
            tags.append("video")

        # Build usage context from preceding messages
        usage_contexts: list[str] | None = None
        if preceding_messages:
            context_text = " | ".join(preceding_messages[:3])
            if len(context_text) >= 5:
                usage_contexts = [context_text[:200]]

        # Save to database
        await self._repo.save_sticker(
            file_unique_id=file_unique_id,
            file_id=sticker.file_id,
            set_name=sticker.set_name,
            emoji=sticker.emoji,
            is_animated=sticker.is_animated,
            is_video=sticker.is_video,
            visual_description=visual,
            original_vision_description=visual,
            emotion=emotion,
            suggested_contexts=contexts,
            style_tags=tags or None,
            character_or_meme=character,
            usage_contexts=usage_contexts,
            analysis_failed=analysis_failed,
        )

        # Generate embedding if analysis succeeded
        if visual and not analysis_failed:
            await self._generate_and_store_embedding(
                file_unique_id=file_unique_id,
                visual_description=visual,
                emotion=emotion,
                character_or_meme=character,
                suggested_contexts=contexts,
                usage_contexts=usage_contexts,
            )

        logger.info(
            "Learned new sticker",
            file_unique_id=file_unique_id,
            set_name=sticker.set_name,
            sticker_type=sticker_type,
            has_description=bool(visual),
            analysis_failed=analysis_failed,
        )

        return StickerLearningResult(
            is_new=True,
            file_unique_id=file_unique_id,
            visual_description=visual,
            emotion=emotion,
            character_or_meme=character,
            analysis_failed=analysis_failed,
        )

    # ── Search ───────────────────────────────────────────────────────

    async def search(
        self,
        context: str,
        *,
        limit: int = 5,
        min_similarity: float = 0.7,
    ) -> list[StickerSearchResult]:
        """Find stickers relevant to a text context.

        Args:
            context: Text describing what kind of sticker is needed.
            limit: Maximum results.
            min_similarity: Minimum cosine similarity threshold.

        Returns:
            List of StickerSearchResult sorted by descending similarity.
        """
        try:
            embedding_result = await self._ai.generate_embedding(context)
        except AIProviderError:
            logger.exception("Failed to generate search embedding")
            return []

        records = await self._repo.search_by_embedding(
            embedding_result.embedding,
            limit=limit,
            min_similarity=min_similarity,
        )

        return [
            StickerSearchResult(
                file_id=r["file_id"],
                file_unique_id=r["file_unique_id"],
                visual_description=r["visual_description"],
                emotion=r["emotion"],
                character_or_meme=r["character_or_meme"],
                suggested_contexts=r["suggested_contexts"] or [],
                similarity=float(r["similarity"]),
                total_uses=r["total_uses"],
                bot_uses=r["bot_uses"],
            )
            for r in records
        ]

    # ── Admin notifications ──────────────────────────────────────────

    async def notify_admins(
        self,
        bot: Bot,
        sticker: types.Sticker,
        result: StickerLearningResult,
        admin_ids: list[int],
    ) -> None:
        """Send new sticker notification to all admins."""
        if not admin_ids or result.analysis_failed:
            return

        description_parts = []
        if result.visual_description:
            description_parts.append(f"<b>Описание:</b> {result.visual_description}")
        if result.emotion:
            description_parts.append(f"<b>Эмоция:</b> {result.emotion}")
        if result.character_or_meme:
            description_parts.append(f"<b>Персонаж:</b> {result.character_or_meme}")
        if sticker.set_name:
            description_parts.append(f"<b>Пак:</b> {sticker.set_name}")

        description_parts.append(
            "\n<i>Ответь на это сообщение текстом, чтобы уточнить описание стикера.</i>"
        )
        text = "\n".join(description_parts)

        for admin_id in admin_ids:
            try:
                sticker_msg = await bot.send_sticker(admin_id, sticker.file_id)
                desc_msg = await bot.send_message(
                    admin_id, text, parse_mode="HTML",
                    reply_to_message_id=sticker_msg.message_id,
                )
                await self._repo.save_notification(
                    file_unique_id=sticker.file_unique_id,
                    admin_id=admin_id,
                    message_id=desc_msg.message_id,
                    sticker_msg_id=sticker_msg.message_id,
                    chat_id=admin_id,
                )
            except Exception:
                logger.warning(
                    "Failed to notify admin about sticker",
                    admin_id=admin_id,
                    file_unique_id=sticker.file_unique_id,
                )

    # ── Admin merge description ──────────────────────────────────────

    async def merge_admin_description(
        self,
        file_unique_id: str,
        admin_text: str,
    ) -> str | None:
        """Merge admin notes with existing description via AI.

        Returns new merged description or None on failure.
        """
        existing = await self._repo.get_by_file_unique_id(file_unique_id)
        if not existing:
            return None

        original = existing["original_vision_description"] or ""
        current = existing["visual_description"] or ""
        edit_mode = _detect_edit_mode(admin_text)

        prompt = self._build_merge_prompt(original, current, admin_text, edit_mode)

        try:
            ai_result = await self._ai.generate_text(
                prompt=prompt,
                system_prompt="Ты помогаешь редактировать описания стикеров. Отвечай только JSON.",
                max_tokens=300,
            )
            parsed = self._parse_vision_response(ai_result.text)
        except AIProviderError:
            logger.warning("AI merge failed", file_unique_id=file_unique_id)
            return None

        new_visual = parsed.get("visual")
        if not new_visual:
            return None

        # Update database
        await self._repo.update_description_and_fields(
            file_unique_id=file_unique_id,
            visual_description=new_visual,
            emotion=parsed.get("emotion") or existing["emotion"],
            suggested_contexts=parsed.get("contexts") or existing["suggested_contexts"],
            character_or_meme=parsed.get("character") or existing["character_or_meme"],
            admin_notes=admin_text,
        )

        # Regenerate embedding
        await self._generate_and_store_embedding(
            file_unique_id=file_unique_id,
            visual_description=new_visual,
            emotion=parsed.get("emotion") or existing["emotion"],
            character_or_meme=parsed.get("character") or existing["character_or_meme"],
            suggested_contexts=parsed.get("contexts") or existing["suggested_contexts"],
            usage_contexts=existing["usage_contexts"],
        )

        return new_visual

    # ── Web search enrichment ────────────────────────────────────────

    async def _search_pack_context_via_ai(
        self, set_name: str
    ) -> tuple[str | None, str | None]:
        """Use AI knowledge to identify character/meme for a sticker pack.

        Returns (character_hint, cultural_context) or (None, None).
        """
        prompt = (
            f'Стикерпак Telegram: "{set_name}".\n'
            "Если ты знаешь, какой персонаж, мем или культурная отсылка стоит за этим стикерпаком:\n"
            "1) Назови персонажа/мем\n"
            "2) Кратко объясни контекст (особенно если это русский мем)\n"
            'Ответь JSON: {"character": "...", "context": "..."}\n'
            'Если не знаешь — {"character": null, "context": null}'
        )

        try:
            result = await asyncio.wait_for(
                self._ai.generate_text(
                    prompt=prompt,
                    system_prompt="Identify Telegram sticker pack characters. Reply JSON only.",
                    max_tokens=100,
                ),
                timeout=5.0,
            )
            parsed = self._parse_vision_response(result.text)
            character = parsed.get("character")
            context = parsed.get("context") if isinstance(parsed.get("context"), str) else None
            if character or context:
                logger.info(
                    "Pack context enrichment",
                    set_name=set_name,
                    character=character,
                    has_context=bool(context),
                )
            return character, context
        except (TimeoutError, AIProviderError):
            return None, None
        except Exception:
            logger.debug("Pack context search failed", set_name=set_name)
            return None, None

    # ── Private helpers ──────────────────────────────────────────────

    async def _generate_and_store_embedding(
        self,
        *,
        file_unique_id: str,
        visual_description: str,
        emotion: str | None,
        character_or_meme: str | None,
        suggested_contexts: list[str] | None,
        usage_contexts: list[str] | None,
    ) -> None:
        """Generate embedding and store in the database."""
        text = self._build_embedding_text(
            visual_description,
            emotion,
            character_or_meme,
            suggested_contexts,
            usage_contexts,
        )
        try:
            result = await self._ai.generate_embedding(text)
            await self._repo.update_embedding(file_unique_id, result.embedding)
        except AIProviderError:
            logger.warning(
                "Failed to generate sticker embedding",
                file_unique_id=file_unique_id,
            )

    @staticmethod
    def _build_vision_prompt(
        sticker: types.Sticker,
        *,
        sticker_type: str = "static",
        pack_context: list[str] | None = None,
        character_hint: str | None = None,
        web_context: str | None = None,
    ) -> str:
        """Build the Vision API prompt for sticker analysis."""
        # Opening line based on sticker type
        if sticker_type == "animated":
            lines = [
                "Это анимированный стикер Telegram для чата.",
                "На изображении 6 кадров анимации (3x2 сетка), слева направо, сверху вниз.",
            ]
        elif sticker_type == "video":
            lines = [
                "Это видео-стикер Telegram для чата.",
                "На изображении 6 ключевых кадров видео (3x2 сетка), слева направо, сверху вниз.",
            ]
        else:
            lines = [
                "Это статичный стикер Telegram для чата.",
            ]

        if sticker.set_name:
            lines.append(f"Стикерпак: {sticker.set_name}.")

        if character_hint:
            lines.append(f"Возможный персонаж/мем: {character_hint}")
        if web_context:
            lines.append(f"Культурный контекст: {web_context}")

        if pack_context:
            lines.append("Другие стикеры из этого набора:")
            for desc in pack_context[:5]:
                lines.append(f"  - {desc}")

        lines.append("")
        lines.append("## ЗАДАЧА")
        lines.append("Опиши СМЫСЛ и НАЗНАЧЕНИЕ стикера для использования в чате.")

        if sticker_type in ("animated", "video"):
            lines.append(
                "Опиши общий смысл движения/действия, не описывай каждый кадр отдельно."
            )

        lines.append("")
        lines.append("## КРИТИЧЕСКИ ВАЖНО — ТЕКСТ НА СТИКЕРЕ")
        lines.append(
            "Если на стикере есть текст (даже маленький, художественный, на любом языке):"
        )
        lines.append("1. ОБЯЗАТЕЛЬНО процитируй его дословно в visual")
        lines.append("2. Текст — ГЛАВНОЕ для понимания стикера!")
        lines.append("")
        lines.append("## Что должно быть в описании (visual):")
        lines.append("- ТЕКСТ на стикере (если есть) — процитировать")
        lines.append("- Что изображено (кратко)")
        lines.append("- СМЫСЛ стикера — зачем его отправляют")
        lines.append("")
        lines.append("## Что НЕ нужно:")
        lines.append("- Подробные описания одежды/позы если они не несут смысла")
        lines.append("- Технические детали")

        if sticker_type in ("animated", "video"):
            lines.append('- "Кадры почти одинаковые" — БЕСПОЛЕЗНО')

        lines.append("")
        lines.append("## ФОРМАТ ОТВЕТА (JSON):")
        lines.append("{")
        lines.append('  "visual": "[Текст если есть]. Кто/что изображено + смысл",')
        lines.append('  "emotion": "основная эмоция (1 слово)",')
        lines.append(
            '  "contexts": ["когда использовать 1", "когда использовать 2", ... 3-5],'
        )
        lines.append('  "tags": ["meme", "reaction", "cute", ...],')
        lines.append('  "character": "имя персонажа/мема или null"')
        lines.append("}")

        return "\n".join(lines)

    @staticmethod
    def _build_merge_prompt(
        original: str,
        current: str,
        admin_text: str,
        edit_mode: str,
    ) -> str:
        """Build prompt for merging admin notes into sticker description."""
        mode_instructions = {
            "correction": "Замени неправильные части описания на то, что говорит админ.",
            "addition": "Дополни описание информацией от админа, не удаляя существующее.",
            "smart_merge": "Умно объедини существующее описание с заметками админа.",
        }

        return (
            f"Оригинальное описание от Vision API:\n{original}\n\n"
            f"Текущее описание:\n{current}\n\n"
            f"Заметка админа:\n{admin_text}\n\n"
            f"Режим: {mode_instructions.get(edit_mode, mode_instructions['smart_merge'])}\n\n"
            "Верни обновлённое описание в JSON формате:\n"
            "{\n"
            '  "visual": "обновлённое описание",\n'
            '  "emotion": "эмоция (1 слово)",\n'
            '  "contexts": ["когда использовать 1", ...],\n'
            '  "character": "персонаж или null"\n'
            "}"
        )

    @staticmethod
    def _build_embedding_text(
        visual_description: str,
        emotion: str | None,
        character_or_meme: str | None,
        suggested_contexts: list[str] | None,
        usage_contexts: list[str] | None,
    ) -> str:
        """Compose text for embedding generation."""
        parts = [visual_description]
        if emotion:
            parts.append(f"Emotion: {emotion}")
        if character_or_meme:
            parts.append(f"Character: {character_or_meme}")
        if suggested_contexts:
            parts.append(f"Contexts: {', '.join(suggested_contexts)}")
        if usage_contexts:
            parts.append(f"Usage: {'; '.join(usage_contexts)}")
        return ". ".join(parts)

    @staticmethod
    def _parse_vision_response(text: str) -> dict[str, Any]:
        """Parse JSON from Vision API response.

        Handles JSON wrapped in markdown code blocks and malformed responses.
        """
        # Strip markdown code block wrapping
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse vision JSON", raw_text=text[:200])
            return {}

        if not isinstance(data, dict):
            return {}

        result: dict[str, Any] = {}

        # visual description
        visual = data.get("visual")
        if isinstance(visual, str) and visual.strip():
            result["visual"] = visual.strip()

        # emotion
        emotion = data.get("emotion")
        if isinstance(emotion, str) and emotion.strip():
            result["emotion"] = emotion.strip()

        # contexts
        contexts = data.get("contexts")
        if isinstance(contexts, list):
            result["contexts"] = [
                str(c).strip() for c in contexts if c and str(c).strip()
            ]

        # tags
        tags = data.get("tags")
        if isinstance(tags, list):
            result["tags"] = [str(t).strip() for t in tags if t and str(t).strip()]

        # character (filter out "null" string)
        character = data.get("character")
        if isinstance(character, str) and character.strip().lower() not in (
            "null",
            "none",
            "",
        ):
            result["character"] = character.strip()

        # context (for web enrichment response)
        context_val = data.get("context")
        if isinstance(context_val, str) and context_val.strip().lower() not in (
            "null",
            "none",
            "",
        ):
            result["context"] = context_val.strip()

        return result


def _detect_sticker_type(sticker: types.Sticker) -> str:
    """Detect sticker format: static, animated, or video."""
    if sticker.is_video:
        return "video"
    if sticker.is_animated:
        return "animated"
    return "static"


def _detect_edit_mode(admin_text: str) -> str:
    """Detect admin edit intent: correction, addition, or smart_merge."""
    if _CORRECTION_PATTERNS.search(admin_text):
        return "correction"
    if _ADDITION_PATTERNS.search(admin_text):
        return "addition"
    return "smart_merge"
