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
from unittest.mock import patch

import pytest
from PIL import Image

from src.services.modules.sticker.dedup import compute_image_hash, hamming_distance
from src.services.modules.sticker.motion import MotionAnalyzer
from src.services.modules.sticker.renderer import (
    _FRAME_SIZE,
    _create_motion_trail_frame,
    _extract_hash_anchor_frame,
    _ffmpeg_extract_frame,
    render_tgs,
    render_webm,
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


def _lottie_bytes_with_color_keyframes(color_keyframes: list[dict]) -> bytes:
    """A Lottie animation whose (canvas-covering) shape fill color is driven
    by explicit, hold-interpolated (``"h": 1``) keyframes — real rlottie
    rendering, no mocking. The shape covers almost the entire 64x64 canvas
    so a color swap produces a near-maximal per-pixel diff (small moving
    shapes were tried first and produced diffs normalized well under
    `min_delta=0.15` against the full-canvas `max_diff` denominator in
    `_calculate_frame_differences` — verified empirically, not assumed).
    Used by ``TestRealOscillationEndToEnd`` (C-2) to drive
    `_detect_oscillation` from genuinely differing rendered pixels instead
    of a forced mock return value, closing the gap C-1's own call-site tests
    (which mock `_detect_oscillation` directly) leave open."""
    import gzip
    import json

    lottie = {
        "v": "5.5.2",
        "fr": 30,
        "ip": 0,
        "op": 30,
        "w": 64,
        "h": 64,
        "nm": "test",
        "ddd": 0,
        "assets": [],
        "layers": [
            {
                "ddd": 0,
                "ind": 1,
                "ty": 4,
                "nm": "shape",
                "sr": 1,
                "ks": {
                    "o": {"a": 0, "k": 100},
                    "r": {"a": 0, "k": 0},
                    "p": {"a": 0, "k": [32, 32, 0]},
                    "a": {"a": 0, "k": [0, 0, 0]},
                    "s": {"a": 0, "k": [100, 100, 100]},
                },
                "ao": 0,
                "shapes": [
                    {
                        "ty": "rc",
                        "p": {"a": 0, "k": [0, 0]},
                        "s": {"a": 0, "k": [60, 60]},
                        "r": {"a": 0, "k": 0},
                    },
                    {"ty": "fl", "c": {"a": 1, "k": color_keyframes}, "o": {"a": 0, "k": 100}},
                ],
                "ip": 0,
                "op": 30,
                "st": 0,
                "bm": 0,
            }
        ],
    }
    return gzip.compress(json.dumps(lottie).encode("utf-8"))


_RED = [1, 0, 0, 1]
_BLUE = [0, 0, 1, 1]

# Fill color snaps back and forth between red/blue every 3 frames (matches
# the analyzer's default sampling stride) — a real analogue of "cat rapidly
# turns its head side to side" (the plan's own AgAD7DoAAppnmEg example):
# repeated large, fast changes rather than one smooth move. Empirically
# verified (not just asserted) to make the real `_calculate_frame_differences`
# -> interpolate -> `_detect_oscillation` chain land on True.
_OSCILLATING_COLOR_KEYFRAMES = [
    {"t": 0, "s": _RED, "h": 1},
    {"t": 3, "s": _RED, "h": 1},
    {"t": 6, "s": _BLUE, "h": 1},
    {"t": 9, "s": _BLUE, "h": 1},
    {"t": 12, "s": _RED, "h": 1},
    {"t": 15, "s": _RED, "h": 1},
    {"t": 18, "s": _BLUE, "h": 1},
    {"t": 21, "s": _BLUE, "h": 1},
    {"t": 24, "s": _RED, "h": 1},
    {"t": 27, "s": _RED, "h": 1},
    {"t": 29, "s": _RED},
]

# Exactly one color change, held before and after — a single deliberate
# transition (no back-and-forth), real analogue of C-1's "current route
# stays unaffected" claim for non-shaky animations.
_SMOOTH_SWEEP_COLOR_KEYFRAMES = [
    {"t": 0, "s": _RED, "h": 1},
    {"t": 3, "s": _RED, "h": 1},
    {"t": 6, "s": _RED, "h": 1},
    {"t": 9, "s": _RED, "h": 1},
    {"t": 12, "s": _RED, "h": 1},
    {"t": 15, "s": _BLUE, "h": 1},
    {"t": 18, "s": _BLUE, "h": 1},
    {"t": 21, "s": _BLUE, "h": 1},
    {"t": 24, "s": _BLUE, "h": 1},
    {"t": 27, "s": _BLUE, "h": 1},
    {"t": 29, "s": _BLUE},
]


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


class TestCreateMotionTrailFrame:
    """Pure-function tests for the ghosting composite (C-1's ready-made helper,
    now wired up). Real PIL compositing, no mocking — small and deterministic."""

    @staticmethod
    def _moving_square_frames() -> list[Image.Image]:
        """3 frames on a transparent 30x10 canvas, each with an opaque 10x10
        colored square at a DIFFERENT x-position — simulates a character
        moving across frames, which is exactly where the ghosting effect is
        visible (transparent regions of the top/most-recent frame let older,
        partial-alpha frames show through)."""
        size = (30, 10)

        def _square(x_start: int, color: tuple[int, int, int]) -> Image.Image:
            frame = Image.new("RGBA", size, (0, 0, 0, 0))
            for x in range(x_start, x_start + 10):
                for y in range(10):
                    frame.putpixel((x, y), (*color, 255))
            return frame

        red = _square(0, (255, 0, 0))
        green = _square(10, (0, 255, 0))
        blue = _square(20, (0, 0, 255))
        return [red, green, blue]

    def test_ghosting_visible_through_transparent_regions(self):
        """At the oldest frame's own position (fully transparent in the two
        later frames), the ghost trail must still show a faded red — proving
        the earlier frames actually contribute, not just the top layer."""
        frames = self._moving_square_frames()
        result = _create_motion_trail_frame(frames, center_idx=2, trail_length=3)

        assert result.mode == "RGBA"
        assert result.size == (30, 10)

        # Oldest frame's square (fully transparent in frames 1 and 2) —
        # ghosted red should be visible but not full-alpha/full-intensity.
        r, g, b, a = result.getpixel((5, 5))
        assert r > 0 and g == 0 and b == 0
        assert 0 < a < 255

        # Middle frame's square — stronger ghost than the oldest (later
        # frame = higher alpha weight = (i+1)/trail_length).
        r2, g2, b2, a2 = result.getpixel((15, 5))
        assert g2 > 0
        assert a2 > a

        # Most recent (center) frame's own square — fully opaque, its own color.
        assert result.getpixel((25, 5)) == (0, 0, 255, 255)

        # Region with no content in ANY source frame stays fully transparent.
        assert result.getpixel((0, 9)) is not None  # sanity: pixel access works
        empty = Image.new("RGBA", (30, 10), (0, 0, 0, 0))
        empty_frames = [empty, empty, empty]
        empty_result = _create_motion_trail_frame(empty_frames, center_idx=2, trail_length=3)
        assert empty_result.getpixel((5, 5))[3] == 0

    def test_returns_first_frame_unchanged_when_center_idx_out_of_bounds(self):
        frame = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
        result = _create_motion_trail_frame([frame], center_idx=5, trail_length=3)
        assert result is frame

        result_negative = _create_motion_trail_frame([frame], center_idx=-1, trail_length=3)
        assert result_negative is frame

    def test_returns_blank_frame_when_no_frames_available(self):
        result = _create_motion_trail_frame([], center_idx=0, trail_length=3)
        assert result.mode == "RGBA"
        assert result.size == (_FRAME_SIZE, _FRAME_SIZE)
        assert result.getpixel((0, 0)) == (0, 0, 0, 0)

    def test_single_available_frame_composites_only_itself(self):
        """center_idx=0 with only 1 frame in the list: trail positions that
        would reach into negative indices are skipped, leaving just the
        center frame's own (fully opaque) content."""
        only_frame = Image.new("RGBA", (4, 4), (100, 150, 200, 255))
        result = _create_motion_trail_frame([only_frame], center_idx=0, trail_length=3)
        assert result.getpixel((0, 0)) == (100, 150, 200, 255)


class TestTgsMotionTrailSubstitution:
    """Call-site test (C-1): proves the oscillation branch in _render_tgs_sync
    actually invokes `_create_motion_trail_frame` when triggered, and does NOT
    when it isn't — asserting the wiring, not just the helper's own correctness
    (a correct-but-uncalled helper would pass every test above and still be dead
    code, as it was before this item)."""

    @pytest.mark.asyncio
    async def test_trail_frame_substituted_when_oscillating(self):
        with (
            patch.object(MotionAnalyzer, "_detect_oscillation", return_value=True),
            patch(
                "src.services.modules.sticker.renderer._create_motion_trail_frame",
                wraps=_create_motion_trail_frame,
            ) as mock_trail,
        ):
            result = await render_tgs(_minimal_tgs_bytes())

        assert result.motion is not None
        assert result.motion.is_oscillating is True
        mock_trail.assert_called_once()
        # First positional arg is the sampled-frames list used for motion
        # analysis (reused, not re-rendered) — never empty for a real animation.
        call_args = mock_trail.call_args
        assert len(call_args[0][0]) > 0

    @pytest.mark.asyncio
    async def test_trail_frame_not_substituted_when_not_oscillating(self):
        with (
            patch.object(MotionAnalyzer, "_detect_oscillation", return_value=False),
            patch(
                "src.services.modules.sticker.renderer._create_motion_trail_frame",
                wraps=_create_motion_trail_frame,
            ) as mock_trail,
        ):
            result = await render_tgs(_minimal_tgs_bytes())

        assert result.motion is not None
        assert result.motion.is_oscillating is False
        mock_trail.assert_not_called()


class TestRealOscillationEndToEnd:
    """C-2 regression: TestTgsMotionTrailSubstitution above proves the
    substitution *wiring* is correct, but it forces the verdict via
    ``patch.object(MotionAnalyzer, "_detect_oscillation", return_value=...)``
    — a mirror of the implementation, not an independent check (the real
    detection logic is never exercised). These two drive a genuinely
    oscillating / genuinely single-direction Lottie animation through the
    REAL rlottie render -> real frame differencing -> real
    `_detect_oscillation` -> real trail substitution chain, with nothing
    mocked except an observability wrapper around
    `_create_motion_trail_frame` (to assert call/no-call without changing
    its behavior)."""

    @pytest.mark.asyncio
    async def test_real_oscillating_animation_triggers_trail_and_hint(self):
        with patch(
            "src.services.modules.sticker.renderer._create_motion_trail_frame",
            wraps=_create_motion_trail_frame,
        ) as mock_trail:
            result = await render_tgs(
                _lottie_bytes_with_color_keyframes(_OSCILLATING_COLOR_KEYFRAMES)
            )

        assert result.motion is not None
        # Real detection, not a mocked return value.
        assert result.motion.is_oscillating is True
        mock_trail.assert_called_once()

    @pytest.mark.asyncio
    async def test_real_single_direction_animation_stays_on_current_route(self):
        """A real, genuinely non-oscillating animation must reach the same
        'no substitution' outcome C-1's mocked test asserts — proving current
        route preservation isn't an artifact of the mock always returning
        False."""
        with patch(
            "src.services.modules.sticker.renderer._create_motion_trail_frame",
            wraps=_create_motion_trail_frame,
        ) as mock_trail:
            result = await render_tgs(
                _lottie_bytes_with_color_keyframes(_SMOOTH_SWEEP_COLOR_KEYFRAMES)
            )

        assert result.motion is not None
        assert result.motion.is_oscillating is False
        mock_trail.assert_not_called()


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not available in PATH")
class TestWebmMotionTrailSubstitution:
    """WebM counterpart of TestTgsMotionTrailSubstitution — same call-site proof,
    against the real ffmpeg pipeline (renderer.py has no cheap pre-rendered frame
    list for WebM, so this also exercises the on-demand `_extract_trail_frames`
    helper end to end)."""

    @staticmethod
    def _build_solid_webm(tmp_path: Path) -> Path:
        """A trivial 1s solid-color .webm — motion content doesn't matter here,
        only that oscillation detection is forced via mocking."""
        img = Image.new("RGBA", (64, 64), (200, 50, 50, 255))
        img_path = tmp_path / "solid.png"
        img.save(img_path)
        webm_path = tmp_path / "solid.webm"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-t",
                "1",
                "-i",
                str(img_path),
                "-vf",
                "fps=10,format=yuv420p",
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
    async def test_trail_frame_substituted_when_oscillating(self, tmp_path):
        webm_path = self._build_solid_webm(tmp_path)

        with (
            patch.object(MotionAnalyzer, "_detect_oscillation", return_value=True),
            patch(
                "src.services.modules.sticker.renderer._create_motion_trail_frame",
                wraps=_create_motion_trail_frame,
            ) as mock_trail,
        ):
            result = await render_webm(webm_path.read_bytes())

        assert result.motion is not None
        assert result.motion.is_oscillating is True
        mock_trail.assert_called_once()

    @pytest.mark.asyncio
    async def test_trail_frame_not_substituted_when_not_oscillating(self, tmp_path):
        webm_path = self._build_solid_webm(tmp_path)

        with (
            patch.object(MotionAnalyzer, "_detect_oscillation", return_value=False),
            patch(
                "src.services.modules.sticker.renderer._create_motion_trail_frame",
                wraps=_create_motion_trail_frame,
            ) as mock_trail,
        ):
            result = await render_webm(webm_path.read_bytes())

        assert result.motion is not None
        assert result.motion.is_oscillating is False
        mock_trail.assert_not_called()
