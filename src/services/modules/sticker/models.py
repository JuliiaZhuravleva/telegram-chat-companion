"""Data classes for sticker intelligence."""

from __future__ import annotations

from dataclasses import dataclass


class StickerRenderError(Exception):
    """Failed to render frames from animated/video sticker."""


@dataclass
class StickerLearningResult:
    """Result of learning a sticker."""

    is_new: bool
    file_unique_id: str
    visual_description: str | None = None
    emotion: str | None = None
    character_or_meme: str | None = None
    analysis_failed: bool = False
    collage_png: bytes | None = None


@dataclass
class StickerSearchResult:
    """Single result from sticker semantic search."""

    file_id: str
    file_unique_id: str
    visual_description: str
    emotion: str | None
    character_or_meme: str | None
    suggested_contexts: list[str]
    similarity: float
    total_uses: int
    bot_uses: int
