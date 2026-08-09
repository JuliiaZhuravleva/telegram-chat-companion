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
    # file_unique_id of the canonical sticker this description was copied
    # from via the pre-Vision image-hash dedup check (ADR-0007). None for
    # a sticker that was actually analyzed by Vision (or failed to be).
    duplicate_of: str | None = None
    # Vision-reported explicitness, 0.0 (safe) - 1.0 (maximally explicit)
    # (ADR-0008). None when unscored (analysis failed, or the model's
    # response didn't carry a valid value — reject-not-clamp, Decision 4).
    explicitness_score: float | None = None
    # Whether explicitness_score was hand-set by an admin rather than
    # produced by this analysis (ADR-0009 Decision 6). Always False on a
    # fresh Vision analysis; copied from the canonical row on a
    # duplicate-copy result, since manual status travels with the score it
    # describes. notify_admins() renders directly from this dataclass (not a
    # fresh DB read), so a duplicate's first notification can legitimately
    # show the "(вручную)" badge.
    explicitness_is_manual: bool = False


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
