"""Perceptual-hash duplicate detection for stickers (ADR-0007).

Cheap, Pillow-only image-similarity check that runs *before* the Vision API
call in ``StickerLearningService.learn()``. Deliberately conservative
(ADR-0007 Decision 3): missing a real duplicate only costs one avoidable
Vision call, while a false match would silently give a sticker someone
else's description — so the threshold is biased toward false negatives.

Kept DB-free on purpose so it can be unit-tested without Postgres.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import cast

from PIL import Image

# Two images are treated as "the same picture" only below this Hamming
# distance (of 64 bits). See ADR-0007 Decision 3 for the empirical
# validation behind this value (same-picture-recompressed → 0,
# transparent-padding-color-difference → 0, random-noise control → 26).
DEDUP_HAMMING_THRESHOLD = 4


def compute_image_hash(image_bytes: bytes) -> str:
    """64-bit difference hash (dHash), hex-encoded, Pillow-only.

    Robust to Telegram's WEBP re-encoding / minor recompression artifacts.
    NOT robust to crops, rotations, or mirrored art — by design (ADR-0007
    Decision 1): those fall through to a normal, slightly wasteful, but
    still-correct Vision call.

    Raises:
        Whatever Pillow raises on unparseable image bytes (e.g.
        ``PIL.UnidentifiedImageError``). Callers must treat this as
        fail-open — skip the dedup check, proceed to Vision as usual —
        never as a hard failure of sticker ingestion (ADR-0007 Decision 4).
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    # Flatten transparency onto a fixed background so two exports of the same
    # picture with different transparent-pixel RGB padding still hash
    # identically — same alpha_composite-onto-canvas idiom already used for
    # collage frames (renderer.py's _create_motion_trail_frame).
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(bg, img).convert("L")
    small = flat.resize((9, 8), Image.Resampling.LANCZOS)
    px = small.load()  # PixelAccess, not .getdata() — avoids the Pillow 14 deprecation
    assert px is not None  # populated by .load() on a real, opened image
    # `flat` is single-channel ("L" mode), so each access is always a scalar,
    # never the multi-band tuple PixelAccess.__getitem__ is typed to allow.
    bits = "".join(
        "1" if cast(int, px[col, row]) > cast(int, px[col + 1, row]) else "0"
        for row in range(8)
        for col in range(8)
    )
    return f"{int(bits, 2):016x}"


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Bit-difference count between two hex-encoded 64-bit hashes."""
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")


def find_duplicate(
    target_hash: str,
    candidates: list[tuple[str, str, datetime, str | None]],
) -> str | None:
    """Find the canonical sticker to reuse for ``target_hash``.

    Args:
        target_hash: dHash (hex) of the incoming sticker's image.
        candidates: ``(file_unique_id, image_hash, created_at,
            duplicate_of_file_unique_id)`` for every existing sticker
            eligible for matching (ADR-0007 Decision 5's candidate query —
            includes rows that are themselves already-detected duplicates,
            since their own ``image_hash`` is still a legitimate match
            target for a third copy).

    Returns:
        The canonical ``file_unique_id`` to copy vision fields from, or
        ``None`` if no candidate is within ``DEDUP_HAMMING_THRESHOLD``.

    Selection (ADR-0007 Decision 6): smallest Hamming distance wins; ties
    broken by oldest ``created_at`` (first-seen wins — deterministic and
    auditable, unlike a usage-counter tie-break). If the winning candidate
    is itself already a detected duplicate (``duplicate_of_file_unique_id``
    is set), resolve to *its* target instead — duplicate chains always
    flatten to a single root, so "how many duplicates does canonical X
    have" stays a single-hop query.
    """
    best: tuple[int, datetime, str, str | None] | None = None
    for file_unique_id, image_hash, created_at, duplicate_of in candidates:
        distance = hamming_distance(target_hash, image_hash)
        if distance > DEDUP_HAMMING_THRESHOLD:
            continue
        candidate = (distance, created_at, file_unique_id, duplicate_of)
        if best is None or candidate[:2] < best[:2]:
            best = candidate

    if best is None:
        return None

    _distance, _created_at, file_unique_id, duplicate_of = best
    return duplicate_of or file_unique_id
