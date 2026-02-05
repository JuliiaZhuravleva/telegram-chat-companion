"""Sticker learning and search service.

Learns sticker meanings via Vision API, generates embeddings for semantic search.
Phase 2 scope: static stickers only (no animated/video rendering).
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from aiogram import types

from src.database.repositories.stickers import StickerRepository
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter
from src.services.modules.sticker.models import (
    StickerLearningResult,
    StickerSearchResult,
)

logger = structlog.get_logger(__name__)


class StickerLearningService:
    """Learn and understand stickers via Vision API + embeddings."""

    def __init__(
        self,
        ai_router: AIRouter,
        sticker_repo: StickerRepository,
    ) -> None:
        self._ai = ai_router
        self._repo = sticker_repo

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
            image_data: Raw image bytes (PNG/WebP for static stickers).
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

        # Skip analysis for animated/video stickers (Phase 2 = static only)
        if sticker.is_animated or sticker.is_video:
            await self._repo.save_sticker(
                file_unique_id=file_unique_id,
                file_id=sticker.file_id,
                set_name=sticker.set_name,
                emoji=sticker.emoji,
                is_animated=sticker.is_animated,
                is_video=sticker.is_video,
                analysis_failed=True,
            )
            logger.info(
                "Skipped animated/video sticker analysis",
                file_unique_id=file_unique_id,
                is_animated=sticker.is_animated,
                is_video=sticker.is_video,
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

        # Vision API analysis
        prompt = self._build_vision_prompt(sticker, pack_context=pack_context)
        parsed: dict[str, Any] = {}
        try:
            vision_result = await self._ai.analyze_image(
                image_data=image_data,
                prompt=prompt,
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
        tags = parsed.get("tags")
        character = parsed.get("character")
        analysis_failed = not bool(visual)

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
            is_animated=False,
            is_video=False,
            visual_description=visual,
            original_vision_description=visual,
            emotion=emotion,
            suggested_contexts=contexts,
            style_tags=tags,
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
        pack_context: list[str] | None = None,
    ) -> str:
        """Build the Vision API prompt for sticker analysis."""
        lines = [
            "Это статичный стикер Telegram для чата.",
        ]

        if sticker.set_name:
            lines.append(f"Стикерпак: {sticker.set_name}.")

        if pack_context:
            lines.append("Другие стикеры из этого набора:")
            for desc in pack_context[:5]:
                lines.append(f"  - {desc}")

        lines.append("")
        lines.append("## ЗАДАЧА")
        lines.append("Опиши СМЫСЛ и НАЗНАЧЕНИЕ стикера для использования в чате.")
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
        lines.append("")
        lines.append("## ФОРМАТ ОТВЕТА (JSON):")
        lines.append('{')
        lines.append('  "visual": "[Текст если есть]. Кто/что изображено + смысл",')
        lines.append('  "emotion": "основная эмоция (1 слово)",')
        lines.append(
            '  "contexts": ["когда использовать 1", "когда использовать 2", ... 3-5],'
        )
        lines.append('  "tags": ["meme", "reaction", "cute", ...],')
        lines.append('  "character": "имя персонажа/мема или null"')
        lines.append('}')

        return "\n".join(lines)

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

        return result
