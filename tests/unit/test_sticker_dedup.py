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


class TestAdversarialFalsePositiveBoundary:
    """QA addition (A-3): the false-positive control above
    (``test_clearly_different_pictures_exceed_threshold``) uses two pictures
    that differ in both palette AND silhouette (solid red vs. blue
    checkerboard) — an easy case. ADR-0007's own Consequences section names
    the *sharpest* realistic risk explicitly: "two genuinely different
    animated stickers from the same pack that happen to share a similar
    idle/starting pose." This class exercises that harder, more realistic
    near-miss: two pictures sharing the same background/silhouette/eye
    layout, differing only in the mouth (a plausible same-pack "different
    expression" pair) — and a control demonstrating dHash is overwhelmingly
    a luminance-*gradient* hash with very little sensitivity to color (a
    same-shape pair that differs only in fill color lands well inside the
    dedup threshold, which is a real, ADR-undocumented blind spot worth
    flagging, not merely an assumption)."""

    @staticmethod
    def _face(mouth: str, eye_color: tuple[int, int, int, int] = (20, 20, 20, 255)) -> bytes:
        img = Image.new("RGBA", (64, 64), (250, 220, 170, 255))
        for x in range(8, 56):
            for y in range(8, 56):
                img.putpixel((x, y), (240, 200, 140, 255))
        for x in range(20, 26):
            for y in range(24, 30):
                img.putpixel((x, y), eye_color)
        for x in range(38, 44):
            for y in range(24, 30):
                img.putpixel((x, y), eye_color)
        if mouth == "smile":
            for x in range(22, 42):
                for y in range(40, 44):
                    img.putpixel((x, y), (150, 30, 30, 255))
        else:  # "shock": a taller, narrower open mouth — different pose
            for x in range(26, 38):
                for y in range(38, 50):
                    img.putpixel((x, y), (60, 20, 20, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_same_pack_different_expression_exceeds_threshold(self):
        """Same head/eyes/background, different mouth shape (stand-in for two
        distinct stickers from one pack sharing a similar idle pose) must
        NOT be treated as duplicates."""
        smile = self._face("smile")
        shock = self._face("shock")
        distance = hamming_distance(compute_image_hash(smile), compute_image_hash(shock))
        assert distance > DEDUP_HAMMING_THRESHOLD

    def test_same_silhouette_different_fill_color_stays_within_threshold(self):
        """Known blind spot, empirically confirmed (not assumed): dHash
        compares relative brightness between *neighboring* pixels only, so
        two images with the identical shape/silhouette but very different
        overall fill color hash almost identically after grayscale
        conversion — color carries very little weight (measured: 2 of 64
        bits differ here, well inside DEDUP_HAMMING_THRESHOLD=4, for two
        fills as different as saturated red vs. saturated blue).

        Flag: a same-silhouette, different-color sticker VARIANT (a real,
        plausible same-pack pattern — e.g. a character recolored per
        emotion/team) risks being wrongly merged by the current threshold.
        Not a regression to fix here (ADR-0007 Decision 1 chose dHash
        deliberately, and Decision 3's threshold is a considered trade-off),
        but this is a concrete, measured false-positive risk, not a
        theoretical one — worth carrying into any future threshold/algorithm
        revisit (ADR-0007 Decision 5's revisit trigger)."""
        red = _png_bytes(fill=(200, 30, 30, 255))
        blue = _png_bytes(fill=(20, 20, 220, 255))
        distance = hamming_distance(compute_image_hash(red), compute_image_hash(blue))
        assert distance <= DEDUP_HAMMING_THRESHOLD


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
