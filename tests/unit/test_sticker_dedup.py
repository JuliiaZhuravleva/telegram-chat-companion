"""Tests for src.services.modules.sticker.dedup (ADR-0007).

compute_image_hash()/hamming_distance() are exercised against real Pillow
images (not string fixtures) so a broken resize/threshold constant would
actually show up here, not just in a hand-rolled hex string.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image, UnidentifiedImageError

from src.services.modules.sticker.dedup import (
    DEDUP_HAMMING_THRESHOLD,
    compute_image_hash,
    find_duplicate,
    hamming_distance,
)


def _png_bytes(
    fill: tuple[int, int, int, int] = (200, 40, 40, 255),
    size: tuple[int, int] = (64, 64),
    fmt: str = "PNG",
) -> bytes:
    """A simple synthetic sticker image: a filled square with a corner mark
    (pure solid color would resize into a degenerate all-equal dHash where
    every comparison is a tie, which is not representative of real art)."""
    img = Image.new("RGBA", size, fill)
    # Distinct corner so the dHash isn't a trivial all-zero/all-one pattern.
    for x in range(size[0] // 3):
        for y in range(size[1] // 3):
            img.putpixel((x, y), (255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class TestComputeImageHash:
    def test_identical_bytes_produce_identical_hash(self):
        data = _png_bytes()
        assert compute_image_hash(data) == compute_image_hash(data)

    def test_same_picture_recompressed_is_within_threshold(self):
        """Re-saving through Pillow (stand-in for Telegram's WEBP re-encode)
        must not push the hash past the dedup threshold."""
        original = _png_bytes()
        img = Image.open(io.BytesIO(original))
        recompressed_buf = io.BytesIO()
        img.save(recompressed_buf, format="PNG", optimize=True)

        distance = hamming_distance(
            compute_image_hash(original), compute_image_hash(recompressed_buf.getvalue())
        )
        assert distance <= DEDUP_HAMMING_THRESHOLD

    def test_transparent_padding_color_difference_hashes_identically(self):
        """Two exports of the same artwork whose *transparent* pixels carry
        different RGB padding must flatten to the same hash (ADR-0007
        Decision 1) — this is exactly what alpha_composite-onto-white
        guards against."""
        size = (64, 64)
        img_a = Image.new("RGBA", size, (0, 0, 0, 0))
        img_b = Image.new("RGBA", size, (123, 45, 67, 0))  # different RGB, same alpha=0
        for img in (img_a, img_b):
            for x in range(20, 44):
                for y in range(20, 44):
                    img.putpixel((x, y), (200, 40, 40, 255))

        buf_a, buf_b = io.BytesIO(), io.BytesIO()
        img_a.save(buf_a, format="PNG")
        img_b.save(buf_b, format="PNG")

        assert compute_image_hash(buf_a.getvalue()) == compute_image_hash(buf_b.getvalue())

    def test_clearly_different_pictures_exceed_threshold(self):
        """Adversarial negative control: two genuinely different pictures,
        not two copies of one file (ADR-0007 Consequences)."""
        red = _png_bytes(fill=(220, 20, 20, 255))
        blue_checkerboard = Image.new("RGBA", (64, 64), (20, 20, 220, 255))
        for x in range(0, 64, 8):
            for y in range(0, 64, 8):
                if (x // 8 + y // 8) % 2 == 0:
                    for dx in range(8):
                        for dy in range(8):
                            blue_checkerboard.putpixel((x + dx, y + dy), (240, 240, 20, 255))
        buf = io.BytesIO()
        blue_checkerboard.save(buf, format="PNG")

        distance = hamming_distance(compute_image_hash(red), compute_image_hash(buf.getvalue()))
        assert distance > DEDUP_HAMMING_THRESHOLD

    def test_unparseable_bytes_raise(self):
        """Callers (learning.py) must catch this and treat it as fail-open —
        compute_image_hash() itself does not swallow the error."""
        with pytest.raises(UnidentifiedImageError):
            compute_image_hash(b"not an image")


class TestHammingDistance:
    def test_identical_hashes_zero_distance(self):
        assert hamming_distance("0000000000000000", "0000000000000000") == 0

    def test_all_bits_different(self):
        assert hamming_distance("0000000000000000", "ffffffffffffffff") == 64

    def test_single_bit_difference(self):
        assert hamming_distance("0000000000000000", "0000000000000001") == 1


class TestFindDuplicate:
    _T0 = datetime(2026, 1, 1, tzinfo=UTC)
    _T1 = _T0 + timedelta(days=1)

    def test_no_candidates_returns_none(self):
        assert find_duplicate("0000000000000000", []) is None

    def test_within_threshold_matches(self):
        # 2 bits set -> distance 2, within DEDUP_HAMMING_THRESHOLD (4).
        candidates = [("uid-1", "0000000000000003", self._T0, None)]
        assert find_duplicate("0000000000000000", candidates) == "uid-1"

    def test_beyond_threshold_does_not_match(self):
        # 8 bits set -> distance 8, beyond threshold.
        candidates = [("uid-1", "00000000000000ff", self._T0, None)]
        assert find_duplicate("0000000000000000", candidates) is None

    def test_exactly_at_threshold_matches(self):
        # 4 bits set -> distance 4 == DEDUP_HAMMING_THRESHOLD, inclusive.
        candidates = [("uid-1", "000000000000000f", self._T0, None)]
        assert find_duplicate("0000000000000000", candidates) == "uid-1"

    def test_smallest_distance_wins(self):
        candidates = [
            ("far", "0000000000000003", self._T0, None),  # distance 2
            ("close", "0000000000000001", self._T1, None),  # distance 1
        ]
        assert find_duplicate("0000000000000000", candidates) == "close"

    def test_tie_broken_by_oldest_created_at(self):
        candidates = [
            ("newer", "0000000000000001", self._T1, None),
            ("older", "0000000000000001", self._T0, None),
        ]
        assert find_duplicate("0000000000000000", candidates) == "older"

    def test_chain_flattens_to_root(self):
        """A matched candidate that is itself already a detected duplicate
        resolves to *its* target, never the intermediate row (ADR-0007
        Decision 6 — chains always flatten to a single root)."""
        candidates = [("mid-duplicate", "0000000000000001", self._T0, "root-uid")]
        assert find_duplicate("0000000000000000", candidates) == "root-uid"

    def test_chain_flatten_applies_even_when_not_the_closest(self):
        """The winning candidate (by distance/tie-break) determines the
        result even if a closer non-duplicate candidate also exists further
        away in iteration order — flatten happens on whichever row wins."""
        candidates = [
            ("root-candidate", "0000000000000003", self._T0, None),  # distance 2
            ("mid-duplicate", "0000000000000001", self._T1, "actual-root"),  # distance 1, wins
        ]
        assert find_duplicate("0000000000000000", candidates) == "actual-root"
