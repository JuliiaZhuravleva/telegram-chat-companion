"""Data classes for sticker intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    # Set when analysis_failed=True; None on success.
    failure_reason: Literal["vision", "content_filter", "empty"] | None = None


@dataclass
class ReanalyzeResult:
    """Result of re-running vision analysis on a sticker (admin action).

    ``ok=True``  → analysis succeeded; ``visual_description`` holds the new text.
    ``ok=False`` → analysis failed; ``reason`` identifies the failure mode.
    """

    ok: bool
    reason: Literal["download", "vision", "content_filter", "empty"] | None = None
    visual_description: str | None = None


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
