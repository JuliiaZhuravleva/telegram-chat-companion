"""Tests for the ADR-0007 (Decision 2) dedup hash-frame extraction in renderer.py.

Unlike test_sticker_learning.py (which mocks `render_tgs`/`render_webm` entirely),
these drive the REAL rlottie/ffmpeg pipelines end to end. Decision 2's two concrete
claims — "TGS hash_frame is bit-for-bit deterministic" and "WebM hash_frame is
pinned to t=0, never a motion-selected keyframe" — can only be falsified against
the real renderer; a mock of `render_tgs`/`render_webm` would trivially "pass"
regardless of whether the underlying extraction logic is correct.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from src.services.modules.sticker.dedup import compute_image_hash, hamming_distance
from src.services.modules.sticker.renderer import (
    _FRAME_SIZE,
    _extract_hash_anchor_frame,
    _ffmpeg_extract_frame,
    render_tgs,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None

# A minimal, valid, single-shape Lottie animation (30 frames @ 60fps = 0.5s).
# Content is a static red square — sufficient to check hash_frame's shape/
# determinism; per-timestamp content distinction is covered by the WebM tests
# below instead (constructing a Lottie animation with genuinely time-varying
# geometry is unnecessary complexity for what Decision 2 actually claims about
# TGS: "frame 0 is derived purely from the Lottie JSON, so it is bit-for-bit
# deterministic for identical input" — determinism, not content-at-t0).
_MINIMAL_LOTTIE_JSON = """
{
  "v": "5.5.2", "fr": 60, "ip": 0, "op": 30, "w": 64, "h": 64,
  "nm": "test", "ddd": 0, "assets": [],
  "layers": [
    {
      "ddd": 0, "ind": 1, "ty": 4, "nm": "shape", "sr": 1,
      "ks": {
        "o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0},
        "p": {"a": 0, "k": [32, 32, 0]}, "a": {"a": 0, "k": [0, 0, 0]},
        "s": {"a": 0, "k": [100, 100, 100]}
      },
      "ao": 0,
      "shapes": [
        {"ty": "rc", "p": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [40, 40]}, "r": {"a": 0, "k": 0}},
        {"ty": "fl", "c": {"a": 0, "k": [1, 0, 0, 1]}, "o": {"a": 0, "k": 100}}
      ],
      "ip": 0, "op": 30, "st": 0, "bm": 0
    }
  ]
}
"""


def _minimal_tgs_bytes() -> bytes:
    import gzip

    return gzip.compress(_MINIMAL_LOTTIE_JSON.encode("utf-8"))


class TestTgsHashFrame:
    """RenderedSticker.hash_frame for .tgs stickers (ADR-0007 Decision 2)."""

    @pytest.mark.asyncio
    async def test_hash_frame_is_a_valid_parseable_png(self):
        result = await render_tgs(_minimal_tgs_bytes())

        assert result.hash_frame
        img = Image.open(io.BytesIO(result.hash_frame))
        assert img.mode == "RGBA"
        # Native Lottie canvas size (64x64 per the fixture's "w"/"h") — NOT
        # resized to the collage's _FRAME_SIZE, since hash_frame reuses the
        # raw rendered frame directly (no collage compositing step).
        assert img.size == (64, 64)

    @pytest.mark.asyncio
    async def test_hash_frame_is_deterministic_across_independent_renders(self):
        """Decision 2: 'bit-for-bit deterministic for identical input' — not
        just 'hashes to the same dHash', the actual PNG bytes must match."""
        tgs_data = _minimal_tgs_bytes()

        first = await render_tgs(tgs_data)
        second = await render_tgs(tgs_data)

        assert first.hash_frame == second.hash_frame


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not available in PATH")
class TestWebmHashAnchorFrame:
    """_extract_hash_anchor_frame for .webm stickers (ADR-0007 Decision 2)."""

    @staticmethod
    def _build_two_color_webm(tmp_path: Path) -> Path:
        """A 2s clip: solid red (with a white corner mark, to avoid dHash's
        degenerate all-equal-pixel case on a flat color) for second 0, solid
        blue (with a yellow corner mark) for second 1. The hard color cut at
        t=1.0 makes "which half did this frame come from" unambiguous — the
        real-world equivalent of the motion-peak-vs-t=0 divergence Decision 2
        is written against.
        """
        img_a = Image.new("RGBA", (64, 64), (220, 20, 20, 255))
        for x in range(20):
            for y in range(20):
                img_a.putpixel((x, y), (255, 255, 255, 255))
        img_b = Image.new("RGBA", (64, 64), (20, 20, 220, 255))
        for x in range(44, 64):
            for y in range(44, 64):
                img_b.putpixel((x, y), (255, 255, 0, 255))

        path_a, path_b = tmp_path / "a.png", tmp_path / "b.png"
        img_a.save(path_a)
        img_b.save(path_b)

        webm_path = tmp_path / "two_color.webm"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-t",
                "1",
                "-i",
                str(path_a),
                "-loop",
                "1",
                "-t",
                "1",
                "-i",
                str(path_b),
                "-filter_complex",
                "[0:v][1:v]concat=n=2:v=1:a=0,fps=10,format=yuv420p",
                "-c:v",
                "libvpx-vp9",
                "-deadline",
                "realtime",
                "-cpu-used",
                "8",
                str(webm_path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return webm_path

    @pytest.mark.asyncio
    async def test_anchor_frame_reflects_t0_content_not_a_later_frame(self, tmp_path):
        """The anchor must come from the RED (t=0) half, not the BLUE (t=1+)
        half — proving it is genuinely pinned to t=0.0, not reusing whatever
        frame a motion-peak selection elsewhere in the pipeline would pick
        (Decision 2's entire reason for not reusing motion-selected
        keyframes)."""
        webm_path = self._build_two_color_webm(tmp_path)

        anchor = await _extract_hash_anchor_frame(webm_path, str(tmp_path))
        later_path = tmp_path / "later.png"
        await _ffmpeg_extract_frame(webm_path, later_path, 1.5)
        later = later_path.read_bytes()

        distance = hamming_distance(compute_image_hash(anchor), compute_image_hash(later))
        assert distance > 4  # ADR-0007 Decision 3's DEDUP_HAMMING_THRESHOLD

        anchor_img = Image.open(io.BytesIO(anchor)).convert("RGB")
        # Sample a background pixel, well away from either corner mark.
        r, g, b = anchor_img.getpixel((128, 128))
        assert r > b  # red-dominant -> the t=0 half, not the t=1+ (blue) half

    @pytest.mark.asyncio
    async def test_anchor_frame_deterministic_across_independent_calls(self, tmp_path):
        webm_path = self._build_two_color_webm(tmp_path)

        first = await _extract_hash_anchor_frame(webm_path, str(tmp_path))
        second = await _extract_hash_anchor_frame(webm_path, str(tmp_path))

        assert compute_image_hash(first) == compute_image_hash(second)

    @pytest.mark.asyncio
    async def test_falls_back_to_well_formed_blank_frame_on_ffmpeg_failure(self, tmp_path):
        """A nonexistent input makes ffmpeg fail to produce output — must fall
        back to a blank, still-hashable frame (fail-open), never raise."""
        missing_input = tmp_path / "does-not-exist.webm"

        anchor = await _extract_hash_anchor_frame(missing_input, str(tmp_path))

        img = Image.open(io.BytesIO(anchor))
        assert img.size == (_FRAME_SIZE, _FRAME_SIZE)
        # Must still be hashable — dedup's fail-open contract is "skip the
        # match", not "crash ingestion".
        compute_image_hash(anchor)
