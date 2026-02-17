"""Sticker response service — selection and prompt formatting.

Handles sticker candidate selection for AI-conscious sticker responses,
sticker-to-sticker replies, and sticker marker extraction from AI output.
"""

from __future__ import annotations

import re

import structlog

from src.database.repositories.stickers import StickerRepository
from src.services.modules.sticker.learning import StickerLearningService
from src.services.modules.sticker.models import StickerSearchResult

logger = structlog.get_logger(__name__)

# Pattern to match STICKER:<file_id> in AI responses
_STICKER_MARKER_RE = re.compile(r"STICKER:([A-Za-z0-9_-]+)")

# Emoji sentiment sets for reranking sticker candidates
_NEGATIVE_EMOJI: frozenset[str] = frozenset({
    "\U0001f621",  # pouting face
    "\U0001f620",  # angry face
    "\U0001f92c",  # face with symbols on mouth
    "\U0001f47f",  # angry face with horns
    "\U0001f4a2",  # anger symbol
    "\U0001f44e",  # thumbs down
    "\U0001f622",  # crying face
    "\U0001f62d",  # loudly crying face
    "\U0001f616",  # confounded face
    "\U0001f624",  # face with steam from nose
})

_POSITIVE_CONTEXT_RE = re.compile(
    r"(?:привет|здравств|добр|hello|hi\b|hey\b|good morning|доброе утро|"
    r"спасибо|благодар|thank|congrat|поздрав|ура\b|класс\b|отлично|круто)",
    re.IGNORECASE,
)

_SENTIMENT_PENALTY = 0.05


class StickerResponderService:
    """Select stickers for bot responses and format prompt candidates."""

    def __init__(
        self,
        sticker_service: StickerLearningService,
        sticker_repo: StickerRepository,
    ) -> None:
        self._sticker = sticker_service
        self._repo = sticker_repo

    async def get_sticker_candidates(
        self,
        context: str,
        *,
        limit: int = 3,
        min_similarity: float = 0.6,
    ) -> list[StickerSearchResult]:
        """Get top sticker candidates for injection into AI prompt.

        Fetches extra candidates and applies emoji sentiment reranking
        to avoid sending angry stickers with friendly messages.
        """
        raw = await self._sticker.search(
            context, limit=limit + 3, min_similarity=min_similarity
        )

        if not raw:
            return []

        # Apply emoji sentiment reranking
        scored: list[tuple[float, StickerSearchResult]] = []
        for candidate in raw:
            penalty = (
                _SENTIMENT_PENALTY
                if _has_sentiment_mismatch(candidate, context)
                else 0.0
            )
            scored.append((candidate.similarity - penalty, candidate))
        scored.sort(key=lambda x: x[0], reverse=True)

        return [c for _, c in scored[:limit]]

    async def find_sticker_for_sticker_reply(
        self,
        incoming_file_unique_id: str,
    ) -> StickerSearchResult | None:
        """Find a response sticker when user sends a sticker.

        Uses the incoming sticker's description as the search context.
        Excludes the same sticker from results.
        """
        existing = await self._repo.get_by_file_unique_id(incoming_file_unique_id)
        if not existing or not existing["visual_description"]:
            return None

        context = existing["visual_description"]
        if existing.get("emotion"):
            context += f" {existing['emotion']}"

        results = await self._sticker.search(
            context, limit=5, min_similarity=0.6
        )

        for r in results:
            if r.file_unique_id != incoming_file_unique_id:
                return r
        return None

    async def record_bot_use(self, file_unique_id: str) -> None:
        """Record that the bot sent this sticker."""
        await self._repo.increment_usage(file_unique_id, is_bot_use=True)

    async def get_file_unique_id(self, file_id: str) -> str | None:
        """Look up file_unique_id by file_id for bot usage tracking."""
        record = await self._repo.get_by_file_id(file_id)
        return record["file_unique_id"] if record else None

    @staticmethod
    def format_candidates_for_prompt(
        candidates: list[StickerSearchResult],
    ) -> str:
        """Format sticker candidates as a prompt section.

        Returns a text block ready for injection into the AI prompt.
        """
        if not candidates:
            return ""

        lines = ["[Доступные стикеры]"]
        for i, c in enumerate(candidates, 1):
            desc = c.visual_description[:100]
            parts = [f"STICKER:{c.file_id}", f'"{desc}"']
            if c.emotion:
                parts.append(f"эмоция: {c.emotion}")
            lines.append(f"{i}. {' — '.join(parts)}")

        return "\n".join(lines)

    @staticmethod
    def extract_sticker_from_response(text: str) -> tuple[str | None, str]:
        """Extract STICKER:<file_id> marker from AI response text.

        Returns:
            Tuple of (file_id, cleaned_text). file_id is None if no marker found.
            cleaned_text has the marker removed.
        """
        match = _STICKER_MARKER_RE.search(text)
        if not match:
            return None, text

        file_id = match.group(1)
        # Remove the marker from text
        cleaned = text[:match.start()] + text[match.end():]
        cleaned = cleaned.strip()

        return file_id, cleaned


def _has_sentiment_mismatch(
    candidate: StickerSearchResult,
    context: str,
) -> bool:
    """Check if sticker emoji sentiment contradicts message sentiment.

    Only penalises when context is clearly positive/friendly
    and the sticker emoji is negative.
    """
    if not candidate.emoji:
        return False
    return (
        candidate.emoji in _NEGATIVE_EMOJI
        and bool(_POSITIVE_CONTEXT_RE.search(context))
    )
