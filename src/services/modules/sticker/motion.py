"""Motion analysis and keyframe selection for animated stickers.

Analyzes movement intensity in .webm and .tgs animations to select
6 keyframes at motion peaks (not evenly spaced across timeline).

Uses:
- ffmpeg scdet filter for .webm frame differences (mean absolute frame difference)
- Frame differencing for .tgs (sampled every 3rd frame for performance)
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import structlog
from PIL import Image, ImageChops, ImageStat

logger = structlog.get_logger(__name__)


@dataclass
class AnimationMotion:
    """Motion analysis result for an animated sticker.

    Attributes:
        duration: Total animation duration in seconds
        keyframe_indices: List of 6 frame indices at motion peaks
        keyframe_times: Corresponding timestamps in seconds
        avg_motion: Average motion score (0.0 to 1.0)
        peak_motion_time: Timestamp of highest motion intensity
        motion_scores: Per-frame motion scores (normalized 0-1)
        is_oscillating: True when the motion-score sequence reverses
            direction repeatedly (e.g. a head shaking side to side) rather
            than following a single smooth rise/fall to one peak. See
            ``MotionAnalyzer._detect_oscillation``. Renderer/prompt code
            uses this to (C-1): substitute a motion-trail ghost frame for
            the keyframe closest to the peak, and add a vision-prompt hint
            — both no-ops when False, so non-oscillating stickers keep the
            exact prior collage/prompt (current route preserved).
    """

    duration: float
    keyframe_indices: list[int]
    keyframe_times: list[float]
    avg_motion: float
    peak_motion_time: float
    motion_scores: list[float]
    is_oscillating: bool = False


class MotionAnalyzer:
    """Analyzes motion in animated stickers and selects optimal keyframes."""

    def __init__(self, target_keyframes: int = 6) -> None:
        """Initialize motion analyzer.

        Args:
            target_keyframes: Number of keyframes to extract (default: 6 for 3x2 collage)
        """
        self.target_keyframes = target_keyframes

    async def analyze_webm_via_ffmpeg(
        self,
        webm_path: Path,
        total_frames: int,
        duration: float,
    ) -> AnimationMotion:
        """Analyze motion in .webm video using ffmpeg mestimate filter.

        Args:
            webm_path: Path to .webm file
            total_frames: Total frame count (from ffprobe)
            duration: Video duration in seconds

        Returns:
            AnimationMotion with keyframe indices and motion metadata
        """
        logger.debug("Analyzing WebM motion", path=str(webm_path), frames=total_frames)

        # Use ffmpeg scdet filter to extract frame differences (motion metric)
        # Output format: lavfi.scd.mafd (mean absolute frame difference)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i",
            str(webm_path),
            "-vf",
            "scdet=t=100,metadata=mode=print",
            "-f",
            "null",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        logger.info(
            "ffmpeg scdet completed",
            returncode=proc.returncode,
            stderr_len=len(stderr),
        )

        # Parse motion scores from stderr (metadata output goes to stderr)
        motion_scores = self._parse_scdet_output(stderr.decode())
        logger.info("Parsed motion scores", count=len(motion_scores))

        # Fallback if parsing failed
        if not motion_scores or len(motion_scores) < 2:
            logger.warning("Motion analysis failed, using uniform fallback")
            return self._create_fallback_motion(total_frames, duration)

        # Select keyframes at motion peaks
        keyframe_indices, keyframe_times = self._select_keyframes(
            motion_scores, total_frames, duration
        )

        avg_motion = sum(motion_scores) / len(motion_scores) if motion_scores else 0.0
        peak_idx = motion_scores.index(max(motion_scores))
        peak_time = (peak_idx / total_frames) * duration if total_frames > 0 else 0.0

        return AnimationMotion(
            duration=duration,
            keyframe_indices=keyframe_indices,
            keyframe_times=keyframe_times,
            avg_motion=avg_motion,
            peak_motion_time=peak_time,
            motion_scores=motion_scores,
            is_oscillating=self._detect_oscillation(motion_scores),
        )

    async def analyze_tgs_frames(
        self,
        frames: list[Image.Image],
        total_frames: int,
        duration: float,
        sampling: int = 3,
    ) -> AnimationMotion:
        """Analyze motion in .tgs animation via frame differencing.

        Samples every Nth frame to reduce computation cost, computes
        pixel differences between consecutive frames.

        Args:
            frames: List of ALL rendered frames from Lottie animation
            total_frames: Total frame count
            duration: Animation duration in seconds
            sampling: Analyze every Nth frame (default: 3 for 60fps → 20fps)

        Returns:
            AnimationMotion with keyframe indices and motion metadata
        """
        logger.debug("Analyzing TGS motion", total_frames=total_frames, sampling=sampling)

        if len(frames) < 2:
            return self._create_fallback_motion(total_frames, duration)

        # Sample frames for motion analysis (every 3rd frame for performance)
        sampled_indices = list(range(0, len(frames), sampling))
        sampled_frames = [frames[i] for i in sampled_indices if i < len(frames)]

        if len(sampled_frames) < 2:
            return self._create_fallback_motion(total_frames, duration)

        # Calculate frame differences
        motion_scores_sampled = self._calculate_frame_differences(sampled_frames)

        # Interpolate back to full frame count
        motion_scores = self._interpolate_motion_scores(
            motion_scores_sampled, sampled_indices, total_frames
        )

        # Select keyframes at motion peaks
        keyframe_indices, keyframe_times = self._select_keyframes(
            motion_scores, total_frames, duration
        )

        avg_motion = sum(motion_scores) / len(motion_scores) if motion_scores else 0.0
        peak_idx = motion_scores.index(max(motion_scores)) if motion_scores else 0
        peak_time = (peak_idx / total_frames) * duration if total_frames > 0 else 0.0

        return AnimationMotion(
            duration=duration,
            keyframe_indices=keyframe_indices,
            keyframe_times=keyframe_times,
            avg_motion=avg_motion,
            peak_motion_time=peak_time,
            motion_scores=motion_scores,
            is_oscillating=self._detect_oscillation(motion_scores),
        )

    def _detect_oscillation(
        self,
        motion_scores: list[float],
        *,
        min_reversals: int = 3,
        min_delta: float = 0.15,
    ) -> bool:
        """Detect rapid back-and-forth motion (shaking/wobbling) via direction reversals.

        A single deliberate gesture (e.g. a punch) produces motion scores that
        rise smoothly to one peak and fall back — at most one reversal. A
        shake/wobble (e.g. a cat rapidly turning its head side to side)
        produces several significant rises and falls in quick succession.
        Counts direction reversals in ``motion_scores``, ignoring changes
        smaller than ``min_delta`` so sensor/encoding noise on an otherwise
        flat or smoothly-ramping signal doesn't get miscounted as motion —
        that noise floor is what keeps a single-peak gesture (and a static
        near-zero signal) classified as non-oscillating.

        Args:
            motion_scores: Per-frame motion scores (normalized 0-1).
            min_reversals: Minimum number of significant direction reversals
                required to classify the sequence as oscillating.
            min_delta: Minimum score change (0-1) to count as a genuine
                directional move rather than noise.

        Returns:
            True if the sequence reverses direction at least ``min_reversals``
            times with each reversal exceeding ``min_delta``.
        """
        if len(motion_scores) < 3:
            return False

        reversals = 0
        direction = 0  # -1 falling, 0 unknown (no significant move yet), +1 rising
        last_extreme = motion_scores[0]

        for score in motion_scores[1:]:
            delta = score - last_extreme
            if abs(delta) < min_delta:
                continue
            new_direction = 1 if delta > 0 else -1
            if direction != 0 and new_direction != direction:
                reversals += 1
            direction = new_direction
            last_extreme = score

        return reversals >= min_reversals

    def _parse_scdet_output(self, stderr: str) -> list[float]:
        """Parse motion scores from ffmpeg scdet filter output.

        Looks for lines like: "lavfi.scd.mafd=0.433" (mean absolute frame difference)
        """
        scores: list[float] = []
        pattern = r"lavfi\.scd\.mafd=(\d+(?:\.\d+)?)"

        for line in stderr.splitlines():
            match = re.search(pattern, line)
            if match:
                try:
                    score = float(match.group(1))
                    scores.append(score)
                except ValueError:
                    continue

        if not scores:
            # Log stderr snippet for diagnostics (truncated to avoid flooding)
            snippet = stderr[:300].replace("\n", " ") if stderr else "(empty)"
            logger.warning(
                "No motion scores found in ffmpeg output",
                stderr_snippet=snippet,
            )
            return []

        # Normalize scores to 0-1 range
        max_score = max(scores) if scores else 1.0
        if max_score > 0:
            scores = [s / max_score for s in scores]

        return scores

    def _calculate_frame_differences(self, frames: list[Image.Image]) -> list[float]:
        """Calculate normalized pixel differences between consecutive frames.

        Uses PIL ImageChops.difference + ImageStat for vectorized computation
        (avoids a slow Python pixel-by-pixel loop: ~100-1000x faster on typical
        sticker frames at 256-512 px).

        Returns:
            List of motion scores (0-1), length = len(frames). First element is
            always 0.0 (no prior frame to compare against).
        """
        scores: list[float] = []

        for i in range(len(frames) - 1):
            frame1 = frames[i].convert("RGB")
            frame2 = frames[i + 1].convert("RGB")

            # Vectorised absolute per-pixel difference via PIL (no Python loops)
            diff_img = ImageChops.difference(frame1, frame2)
            stat = ImageStat.Stat(diff_img)

            # stat.sum gives [R_total, G_total, B_total] — sum all channels
            total_diff = sum(stat.sum)

            # Normalize by image size and max possible difference (255*3 per pixel)
            width, height = frame1.size
            max_diff = width * height * 255 * 3
            normalized_score = total_diff / max_diff if max_diff > 0 else 0.0
            scores.append(normalized_score)

        # Prepend zero: first frame has no preceding frame to diff against
        return [0.0] + scores

    def _interpolate_motion_scores(
        self,
        sampled_scores: list[float],
        sampled_indices: list[int],
        total_frames: int,
    ) -> list[float]:
        """Interpolate motion scores from sampled frames to all frames.

        Linear interpolation between sampled points.
        """
        if not sampled_scores or not sampled_indices:
            return [0.0] * total_frames

        full_scores: list[float] = []

        for frame_idx in range(total_frames):
            # Find surrounding sampled points
            before_idx = 0
            after_idx = 0

            for i, sample_idx in enumerate(sampled_indices):
                if sample_idx <= frame_idx:
                    before_idx = i
                if sample_idx >= frame_idx:
                    after_idx = i
                    break

            # Linear interpolation
            if before_idx == after_idx:
                # Exact match or out of bounds
                score = sampled_scores[before_idx] if before_idx < len(sampled_scores) else 0.0
            else:
                # Interpolate
                idx1 = sampled_indices[before_idx]
                idx2 = sampled_indices[after_idx]
                score1 = sampled_scores[before_idx]
                score2 = sampled_scores[after_idx]

                if idx2 > idx1:
                    t = (frame_idx - idx1) / (idx2 - idx1)
                    score = score1 + t * (score2 - score1)
                else:
                    score = score1

            full_scores.append(score)

        return full_scores

    def _select_keyframes(
        self,
        motion_scores: list[float],
        total_frames: int,
        duration: float,
    ) -> tuple[list[int], list[float]]:
        """Select keyframes at motion peaks with temporal coverage.

        Strategy:
        1. Always include first and last frame
        2. Divide timeline into segments
        3. Pick frame with highest motion in each segment
        4. Ensure minimum temporal spacing

        Returns:
            (keyframe_indices, keyframe_times)
        """
        if total_frames <= self.target_keyframes:
            indices = list(range(total_frames))
            times = [(i / total_frames) * duration if total_frames > 0 else 0.0 for i in indices]
            return indices, times

        keyframe_indices: list[int] = []

        # Always include first frame
        keyframe_indices.append(0)

        # Divide remaining frames into segments
        segments = self.target_keyframes - 2  # -2 for first and last
        segment_size = total_frames // (segments + 1)

        for seg in range(1, segments + 1):
            start = seg * segment_size
            end = min(start + segment_size, total_frames)

            # Find frame with highest motion in this segment
            best_idx = start
            best_score = motion_scores[start] if start < len(motion_scores) else 0.0

            for idx in range(start, end):
                if idx < len(motion_scores) and motion_scores[idx] > best_score:
                    best_score = motion_scores[idx]
                    best_idx = idx

            keyframe_indices.append(best_idx)

        # Always include last frame
        keyframe_indices.append(total_frames - 1)

        # Sort and deduplicate
        keyframe_indices = sorted(set(keyframe_indices))

        # Pad if needed
        while len(keyframe_indices) < self.target_keyframes:
            keyframe_indices.append(keyframe_indices[-1])

        # Truncate if too many
        keyframe_indices = keyframe_indices[: self.target_keyframes]

        # Calculate timestamps
        keyframe_times = [
            (idx / total_frames) * duration if total_frames > 0 else 0.0 for idx in keyframe_indices
        ]

        return keyframe_indices, keyframe_times

    def _create_fallback_motion(self, total_frames: int, duration: float) -> AnimationMotion:
        """Create fallback motion data with evenly-spaced frames.

        Used when motion analysis fails.
        """
        logger.warning("Using fallback motion analysis (evenly-spaced frames)")

        # Evenly-spaced fallback
        if total_frames <= self.target_keyframes:
            indices = list(range(total_frames))
        else:
            indices = [
                round((total_frames - 1) * i / (self.target_keyframes - 1))
                for i in range(self.target_keyframes)
            ]

        times = [(idx / total_frames) * duration if total_frames > 0 else 0.0 for idx in indices]

        return AnimationMotion(
            duration=duration,
            keyframe_indices=indices,
            keyframe_times=times,
            avg_motion=0.0,
            peak_motion_time=0.0,
            motion_scores=[0.0] * total_frames,
        )
