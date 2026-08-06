"""Unit tests for motion analysis and scdet output parsing."""

from __future__ import annotations

import pytest
from PIL import Image

from src.services.modules.sticker.motion import MotionAnalyzer


@pytest.fixture
def analyzer() -> MotionAnalyzer:
    return MotionAnalyzer(target_keyframes=6)


# --- _parse_scdet_output ---


REAL_SCDET_STDERR = """\
[Parsed_metadata_1 @ 0xffff740044c0] frame:0    pts:0       pts_time:0
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.mafd=0.000
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.score=0.000
[Parsed_metadata_1 @ 0xffff740044c0] frame:1    pts:33      pts_time:0.033
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.mafd=4.320
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.score=4.320
[Parsed_metadata_1 @ 0xffff740044c0] frame:2    pts:67      pts_time:0.067
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.mafd=0.018
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.score=0.018
[Parsed_metadata_1 @ 0xffff740044c0] frame:3    pts:100     pts_time:0.1
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.mafd=2.999
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.score=2.981
[Parsed_metadata_1 @ 0xffff740044c0] frame:4    pts:133     pts_time:0.133
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.mafd=4.247
[Parsed_metadata_1 @ 0xffff740044c0] lavfi.scd.score=1.248
"""


def test_parse_scdet_real_output(analyzer: MotionAnalyzer) -> None:
    """Parse real ffmpeg scdet output — expect normalized 0-1 scores."""
    scores = analyzer._parse_scdet_output(REAL_SCDET_STDERR)
    assert len(scores) == 5
    # Max score (4.320) normalizes to 1.0
    assert scores[1] == pytest.approx(1.0)
    # First frame (0.000) stays at 0.0
    assert scores[0] == pytest.approx(0.0)
    # All scores in [0, 1]
    for s in scores:
        assert 0.0 <= s <= 1.0


def test_parse_scdet_empty_stderr(analyzer: MotionAnalyzer) -> None:
    """Empty stderr produces empty list."""
    assert analyzer._parse_scdet_output("") == []


def test_parse_scdet_no_mafd_lines(analyzer: MotionAnalyzer) -> None:
    """Stderr with ffmpeg errors but no mafd lines."""
    stderr = """\
ffmpeg version 7.1.3 Copyright (c) 2000-2025 the FFmpeg developers
Input #0, matroska,webm, from '/tmp/sticker.webm':
  Duration: 00:00:02.90, start: 0.000000
Stream mapping:
  Stream #0:0 -> #0:0 (vp9 -> wrapped_avframe)
"""
    assert analyzer._parse_scdet_output(stderr) == []


def test_parse_scdet_single_frame(analyzer: MotionAnalyzer) -> None:
    """Single frame with mafd=0.000 — should return list of length 1."""
    stderr = "[Parsed_metadata_1 @ 0x0] lavfi.scd.mafd=0.000\n"
    scores = analyzer._parse_scdet_output(stderr)
    # Single score 0.0 / max(0.0) — max_score is 0 so no division
    assert len(scores) == 1
    assert scores[0] == pytest.approx(0.0)


# --- _select_keyframes ---


def test_select_keyframes_short_animation(analyzer: MotionAnalyzer) -> None:
    """Animation with fewer frames than target returns all frames."""
    scores = [0.0, 0.5, 1.0, 0.3]
    indices, times = analyzer._select_keyframes(scores, total_frames=4, duration=0.13)
    assert indices == [0, 1, 2, 3]


def test_select_keyframes_picks_peaks(analyzer: MotionAnalyzer) -> None:
    """Keyframes should include first/last frame and peak motion frames."""
    # 30 frames, clear peak at frame 15
    scores = [0.1] * 30
    scores[15] = 1.0
    scores[25] = 0.8

    indices, times = analyzer._select_keyframes(scores, total_frames=30, duration=1.0)
    assert indices[0] == 0  # first frame
    assert indices[-1] == 29  # last frame
    assert 15 in indices  # peak should be selected


def test_select_keyframes_returns_target_count(analyzer: MotionAnalyzer) -> None:
    """Should return exactly target_keyframes indices."""
    scores = [float(i % 5) / 5 for i in range(90)]
    indices, times = analyzer._select_keyframes(scores, total_frames=90, duration=3.0)
    assert len(indices) == 6
    assert len(times) == 6


# --- _create_fallback_motion ---


def test_fallback_motion_evenly_spaced(analyzer: MotionAnalyzer) -> None:
    """Fallback should produce evenly-spaced keyframes with zero motion."""
    motion = analyzer._create_fallback_motion(total_frames=90, duration=3.0)
    assert motion.avg_motion == 0.0
    assert len(motion.keyframe_indices) == 6
    assert motion.keyframe_indices[0] == 0
    assert motion.keyframe_indices[-1] == 89
    assert all(s == 0.0 for s in motion.motion_scores)


# --- _calculate_frame_differences ---


def _solid_frame(color: tuple[int, int, int], size: int = 8) -> Image.Image:
    """Create a small solid-color RGB image for testing."""
    img = Image.new("RGB", (size, size), color)
    return img


def test_calculate_frame_differences_identical_frames(analyzer: MotionAnalyzer) -> None:
    """Identical frames should produce zero motion scores."""
    frame = _solid_frame((128, 64, 32))
    scores = analyzer._calculate_frame_differences([frame, frame, frame])
    # length = n_frames (first element always 0.0)
    assert len(scores) == 3
    assert scores[0] == pytest.approx(0.0)  # first-frame sentinel
    assert scores[1] == pytest.approx(0.0)  # identical pair
    assert scores[2] == pytest.approx(0.0)  # identical pair


def test_calculate_frame_differences_max_contrast(analyzer: MotionAnalyzer) -> None:
    """Black→white transition should produce a score close to 1.0."""
    black = _solid_frame((0, 0, 0))
    white = _solid_frame((255, 255, 255))
    scores = analyzer._calculate_frame_differences([black, white])
    assert len(scores) == 2
    assert scores[0] == pytest.approx(0.0)
    assert scores[1] == pytest.approx(1.0)


def test_calculate_frame_differences_partial_change(analyzer: MotionAnalyzer) -> None:
    """Partial color change should produce a score between 0 and 1."""
    frame_a = _solid_frame((0, 0, 0))
    frame_b = _solid_frame((128, 0, 0))  # only red channel, half-max
    scores = analyzer._calculate_frame_differences([frame_a, frame_b])
    assert len(scores) == 2
    assert 0.0 < scores[1] < 1.0


def test_calculate_frame_differences_single_frame(analyzer: MotionAnalyzer) -> None:
    """Single frame input returns [0.0] (nothing to diff against)."""
    scores = analyzer._calculate_frame_differences([_solid_frame((100, 100, 100))])
    assert scores == [0.0]


def test_calculate_frame_differences_accepts_rgba(analyzer: MotionAnalyzer) -> None:
    """RGBA input is converted to RGB without error."""
    rgba_frame = Image.new("RGBA", (8, 8), (255, 0, 0, 128))
    black = _solid_frame((0, 0, 0))
    scores = analyzer._calculate_frame_differences([rgba_frame, black])
    assert len(scores) == 2
    assert 0.0 <= scores[1] <= 1.0


# --- _interpolate_motion_scores ---


def test_interpolate_exact_match(analyzer: MotionAnalyzer) -> None:
    """When frame_idx exactly matches a sample index, use that sample's score."""
    sampled_scores = [0.0, 0.5, 1.0]
    sampled_indices = [0, 5, 10]
    result = analyzer._interpolate_motion_scores(sampled_scores, sampled_indices, total_frames=11)
    assert result[0] == pytest.approx(0.0)
    assert result[5] == pytest.approx(0.5)
    assert result[10] == pytest.approx(1.0)


def test_interpolate_midpoint(analyzer: MotionAnalyzer) -> None:
    """Frame halfway between two samples gets the average of their scores."""
    sampled_scores = [0.0, 1.0]
    sampled_indices = [0, 10]
    result = analyzer._interpolate_motion_scores(sampled_scores, sampled_indices, total_frames=11)
    assert result[5] == pytest.approx(0.5, abs=1e-9)


def test_interpolate_output_length(analyzer: MotionAnalyzer) -> None:
    """Output list length equals total_frames regardless of sample count."""
    sampled_scores = [0.0, 0.3, 0.7, 1.0]
    sampled_indices = [0, 3, 6, 9]
    result = analyzer._interpolate_motion_scores(sampled_scores, sampled_indices, total_frames=30)
    assert len(result) == 30


def test_interpolate_beyond_last_sample(analyzer: MotionAnalyzer) -> None:
    """Frames beyond the last sample extrapolate with the last sample score."""
    sampled_scores = [0.2, 0.8]
    sampled_indices = [0, 6]
    result = analyzer._interpolate_motion_scores(sampled_scores, sampled_indices, total_frames=10)
    # Frames 7, 8, 9 are all beyond the last sample (index 6)
    for frame_idx in (7, 8, 9):
        assert result[frame_idx] == pytest.approx(0.8)


def test_interpolate_empty_inputs(analyzer: MotionAnalyzer) -> None:
    """Empty sampled inputs return all-zeros of the requested length."""
    result = analyzer._interpolate_motion_scores([], [], total_frames=5)
    assert result == [0.0] * 5


# --- _detect_oscillation (C-1) ---


def test_detect_oscillation_shaking_motion(analyzer: MotionAnalyzer) -> None:
    """Rapid back-and-forth (e.g. a head shaking side to side) is oscillating."""
    scores = [0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1]
    assert analyzer._detect_oscillation(scores) is True


def test_detect_oscillation_single_smooth_gesture(analyzer: MotionAnalyzer) -> None:
    """A single deliberate gesture (rise to one peak, then fall) is NOT oscillating —
    at most one direction reversal."""
    scores = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 0.8, 0.6, 0.4, 0.2, 0.0]
    assert analyzer._detect_oscillation(scores) is False


def test_detect_oscillation_monotonic_rise(analyzer: MotionAnalyzer) -> None:
    """A monotonic rise (no reversals at all) is NOT oscillating."""
    scores = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert analyzer._detect_oscillation(scores) is False


def test_detect_oscillation_flat_near_zero(analyzer: MotionAnalyzer) -> None:
    """A near-static sticker (no significant motion at all) is NOT oscillating."""
    scores = [0.0, 0.01, 0.0, 0.02, 0.01, 0.0]
    assert analyzer._detect_oscillation(scores) is False


def test_detect_oscillation_noise_below_min_delta_ignored(analyzer: MotionAnalyzer) -> None:
    """Small fluctuations below min_delta must NOT count as reversals — proves the
    noise floor actually gates classification, not just presence of any wiggle."""
    # Tiny up/down jitter on top of a flat baseline, all deltas < default min_delta=0.15
    scores = [0.5, 0.55, 0.5, 0.53, 0.48, 0.52, 0.49]
    assert analyzer._detect_oscillation(scores) is False


def test_detect_oscillation_respects_min_reversals_param(analyzer: MotionAnalyzer) -> None:
    """A sequence with exactly 2 significant reversals is below the default
    min_reversals=3 threshold, but crosses a lowered threshold."""
    scores = [0.1, 0.9, 0.1, 0.9]  # rise, fall, rise = 2 reversals
    assert analyzer._detect_oscillation(scores) is False
    assert analyzer._detect_oscillation(scores, min_reversals=2) is True


def test_detect_oscillation_too_short_sequence(analyzer: MotionAnalyzer) -> None:
    """Fewer than 3 samples can't establish a reversal — always False."""
    assert analyzer._detect_oscillation([0.0, 1.0]) is False
    assert analyzer._detect_oscillation([]) is False
