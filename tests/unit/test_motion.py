"""Unit tests for motion analysis and scdet output parsing."""

from __future__ import annotations

import pytest

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
