"""Animated/video sticker frame extraction and collage generation.

Renders .tgs (Lottie) and .webm (VP9 video) stickers into a 3x2 collage
of 6 evenly-spaced frames for Vision API analysis.

Dependencies:
- rlottie-python: renders .tgs frames via rlottie C binding
- Pillow: composites collage
- ffmpeg (system): extracts .webm frames
"""

from __future__ import annotations

import asyncio
import gzip
import io
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog
from PIL import Image, ImageDraw, ImageFont

from src.services.modules.sticker.models import StickerRenderError
from src.services.modules.sticker.motion import AnimationMotion, MotionAnalyzer

logger = structlog.get_logger(__name__)


@dataclass
class RenderedSticker:
    """Rendered sticker with timing and motion metadata.

    Attributes:
        collage_png: PNG bytes of the 3x2 frame collage
        duration: Total animation duration in seconds
        frame_times: Timestamp (in seconds) of each extracted frame
        motion: Motion analysis result (optional)
        hash_frame: PNG bytes of a single deterministic frame used for
            pre-Vision dedup hashing (ADR-0007 Decision 2) — never the
            Vision collage (its timestamp/motion-score labels would
            corrupt the hash) and, for video, never a motion-selected
            keyframe (those can land at different timestamps between two
            re-encoded copies of the same clip).
    """

    collage_png: bytes
    duration: float
    frame_times: list[float]
    motion: AnimationMotion | None = None
    hash_frame: bytes = b""


# Collage layout: 3 columns x 2 rows = 6 frames
_COLS = 3
_ROWS = 2
_FRAME_COUNT = _COLS * _ROWS  # 6
_FRAME_SIZE = 256  # px per frame in collage
_LABEL_HEIGHT = 20  # px for label at top of collage


def _pick_frame_indices(total_frames: int, count: int = _FRAME_COUNT) -> list[int]:
    """Pick evenly-spaced frame indices across the animation timeline."""
    if total_frames <= 0:
        return [0]
    if total_frames <= count:
        return list(range(total_frames))
    return [round((total_frames - 1) * i / (count - 1)) for i in range(count)]


def _create_motion_trail_frame(
    all_frames: list[Image.Image],
    center_idx: int,
    trail_length: int = 3,
) -> Image.Image:
    """Create motion trail effect by overlaying previous frames with fading opacity.

    Args:
        all_frames: Complete list of animation frames
        center_idx: Index of the main frame to show
        trail_length: Number of previous frames to overlay (default: 3)

    Returns:
        Composited image with motion trail (ghosting effect)
    """
    if center_idx >= len(all_frames) or center_idx < 0:
        return all_frames[0] if all_frames else Image.new("RGBA", (_FRAME_SIZE, _FRAME_SIZE))

    # Start with blank canvas
    base_frame = all_frames[center_idx].convert("RGBA")
    result = Image.new("RGBA", base_frame.size, (0, 0, 0, 0))

    # Overlay previous frames with decreasing opacity
    # trail_length=3 → opacities: [33%, 66%, 100%]
    for i in range(trail_length):
        frame_idx = center_idx - (trail_length - 1 - i)
        if frame_idx < 0 or frame_idx >= len(all_frames):
            continue

        frame = all_frames[frame_idx].convert("RGBA")
        alpha = int(255 * (i + 1) / trail_length)  # Fade in from past to present

        # Create alpha mask for this frame
        alpha_mask = Image.new("L", frame.size, alpha)
        result = Image.alpha_composite(
            result, Image.composite(frame, Image.new("RGBA", frame.size), alpha_mask)
        )

    return result


def _composite_collage(
    frames: list[Image.Image],
    label: str,
    motion: AnimationMotion | None = None,
) -> bytes:
    """Arrange frames in a 3x2 grid with labels, return PNG bytes.

    Args:
        frames: List of frames to arrange
        label: Title label for collage
        motion: Optional motion analysis data for enhanced labels

    Returns:
        PNG bytes of the composited collage
    """
    # Pad frames list to exactly _FRAME_COUNT
    while len(frames) < _FRAME_COUNT:
        frames.append(
            frames[-1].copy() if frames else Image.new("RGBA", (_FRAME_SIZE, _FRAME_SIZE))
        )

    collage_w = _COLS * _FRAME_SIZE
    collage_h = _LABEL_HEIGHT + _ROWS * _FRAME_SIZE
    collage = Image.new("RGBA", (collage_w, collage_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(collage)

    # Title label
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except OSError:
        font = ImageFont.load_default()  # type: ignore[assignment]

    # Enhanced title with motion info
    if motion:
        title = f"{label} — {motion.duration:.1f}s (motion: {motion.avg_motion:.2f})"
    else:
        title = label

    draw.text((8, 2), title, fill=(30, 100, 200), font=font)

    # Place frames
    # Generate labels based on motion data if available
    if motion and len(motion.keyframe_times) >= _FRAME_COUNT:
        frame_labels = []
        for i in range(_FRAME_COUNT):
            time = motion.keyframe_times[i]
            frame_idx = motion.keyframe_indices[i]

            # Get motion score at this frame
            if frame_idx < len(motion.motion_scores):
                motion_score = motion.motion_scores[frame_idx]
                frame_labels.append(f"t={time:.1f}s (m={motion_score:.2f})")
            else:
                frame_labels.append(f"t={time:.1f}s")
    else:
        # Fallback labels
        frame_labels = [
            "Frame 1 (start)",
            "Frame 2",
            "Frame 3",
            "Frame 4",
            "Frame 5",
            "Frame 6 (end)",
        ]

    try:
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except OSError:
        small_font = ImageFont.load_default()  # type: ignore[assignment]

    for idx, frame in enumerate(frames[:_FRAME_COUNT]):
        col = idx % _COLS
        row = idx // _COLS
        x = col * _FRAME_SIZE
        y = _LABEL_HEIGHT + row * _FRAME_SIZE

        resized = frame.resize((_FRAME_SIZE, _FRAME_SIZE), Image.Resampling.LANCZOS)
        collage.paste(resized, (x, y), resized if resized.mode == "RGBA" else None)

        # Frame label overlay
        if idx < len(frame_labels):
            draw.text((x + 4, y + 2), frame_labels[idx], fill=(50, 50, 50), font=small_font)

    buf = io.BytesIO()
    collage.save(buf, format="PNG")
    return buf.getvalue()


def _render_tgs_sync(tgs_data: bytes) -> RenderedSticker:
    """Synchronous TGS rendering with motion-aware keyframe selection.

    Runs in a thread due to rlottie blocking I/O.
    """
    from rlottie_python import LottieAnimation  # type: ignore[attr-defined]

    # Decompress TGS (gzip-compressed Lottie JSON)
    try:
        json_data = gzip.decompress(tgs_data).decode("utf-8")
    except Exception as exc:
        raise StickerRenderError(f"Failed to decompress TGS: {exc}") from exc

    try:
        anim = LottieAnimation.from_data(data=json_data)
    except Exception as exc:
        raise StickerRenderError(f"Failed to load Lottie animation: {exc}") from exc

    total_frames = anim.lottie_animation_get_totalframe()
    if total_frames <= 0:
        raise StickerRenderError("Animation has 0 frames")

    # Calculate duration (Lottie animations run at 60fps)
    fps = 60.0
    duration = total_frames / fps

    # Render ALL frames for motion analysis (sampled every 3rd for performance)
    # We'll sample every 3rd frame: 60fps → 20fps for motion analysis
    sampling = 3
    sampled_frames: list[Image.Image] = []
    w, h = anim.lottie_animation_get_size()
    default_size = (w or 512, h or 512)

    for frame_num in range(0, total_frames, sampling):
        try:
            im = anim.render_pillow_frame(frame_num=frame_num)
            sampled_frames.append(im)
        except Exception as exc:
            logger.warning(
                "Failed to render TGS frame for motion analysis",
                frame_num=frame_num,
                error=str(exc),
            )
            sampled_frames.append(Image.new("RGBA", default_size))

    # Analyze motion (synchronous, but fast with sampling)
    analyzer = MotionAnalyzer(target_keyframes=_FRAME_COUNT)
    # Create dummy motion analysis sync wrapper
    # (This runs in a thread, so we can't use async)
    from src.services.modules.sticker.motion import AnimationMotion

    try:
        # Use frame differencing for motion analysis
        motion_scores_sampled = analyzer._calculate_frame_differences(sampled_frames)
        sampled_indices = list(range(0, total_frames, sampling))
        motion_scores = analyzer._interpolate_motion_scores(
            motion_scores_sampled, sampled_indices, total_frames
        )
        keyframe_indices, keyframe_times = analyzer._select_keyframes(
            motion_scores, total_frames, duration
        )

        avg_motion = sum(motion_scores) / len(motion_scores) if motion_scores else 0.0
        peak_idx = motion_scores.index(max(motion_scores)) if motion_scores else 0
        peak_time = (peak_idx / total_frames) * duration if total_frames > 0 else 0.0

        motion = AnimationMotion(
            duration=duration,
            keyframe_indices=keyframe_indices,
            keyframe_times=keyframe_times,
            avg_motion=avg_motion,
            peak_motion_time=peak_time,
            motion_scores=motion_scores,
        )
    except Exception as exc:
        logger.warning("Motion analysis failed, using fallback", error=str(exc))
        motion = analyzer._create_fallback_motion(total_frames, duration)
        keyframe_indices = motion.keyframe_indices
        keyframe_times = motion.keyframe_times

    # Render keyframes at selected indices
    frames: list[Image.Image] = []
    for frame_num in keyframe_indices:
        try:
            im = anim.render_pillow_frame(frame_num=frame_num)
            frames.append(im)
        except Exception as exc:
            logger.warning("Failed to render TGS keyframe", frame_num=frame_num, error=str(exc))
            frames.append(Image.new("RGBA", default_size))

    collage_png = _composite_collage(frames, "ANIMATED STICKER", motion=motion)

    # Dedup hash frame (ADR-0007 Decision 2): reuse sampled_frames[0], which
    # is anim.render_pillow_frame(frame_num=0) since the sampling loop above
    # starts at frame_num=0 — zero extra render cost, bit-for-bit
    # deterministic for identical Lottie input.
    hash_buf = io.BytesIO()
    sampled_frames[0].convert("RGBA").save(hash_buf, format="PNG")

    return RenderedSticker(
        collage_png=collage_png,
        duration=duration,
        frame_times=keyframe_times,
        motion=motion,
        hash_frame=hash_buf.getvalue(),
    )


async def render_tgs(tgs_data: bytes) -> RenderedSticker:
    """Render .tgs (Lottie) sticker into a 6-frame PNG collage with timing metadata.

    Args:
        tgs_data: Raw .tgs file bytes (gzip-compressed Lottie JSON).

    Returns:
        RenderedSticker with collage PNG and frame timing information.

    Raises:
        StickerRenderError: If rendering fails.
    """
    try:
        return await asyncio.to_thread(_render_tgs_sync, tgs_data)
    except StickerRenderError:
        raise
    except Exception as exc:
        raise StickerRenderError(f"TGS rendering failed: {exc}") from exc


async def render_webm(webm_data: bytes) -> RenderedSticker:
    """Render .webm (VP9 video) sticker with motion-aware keyframe selection.

    Args:
        webm_data: Raw .webm file bytes.

    Returns:
        RenderedSticker with collage PNG, timing, and motion information.

    Raises:
        StickerRenderError: If rendering or ffmpeg fails.
    """
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise StickerRenderError("ffmpeg/ffprobe not found in PATH")

    tmp_dir = tempfile.mkdtemp(prefix="sticker_webm_")
    try:
        input_path = Path(tmp_dir) / "sticker.webm"
        input_path.write_bytes(webm_data)

        # Get video info via ffprobe
        total_frames, duration = await _probe_video(str(input_path))
        if total_frames <= 0 or duration <= 0:
            raise StickerRenderError(f"Invalid video: frames={total_frames}, duration={duration}")

        # Dedup hash frame (ADR-0007 Decision 2): a dedicated t=0 anchor
        # frame, extracted BEFORE motion analysis and never reused from the
        # motion-selected keyframes below — those land at motion-*peak*
        # timestamps, which can shift between two re-encoded copies of the
        # same video even though the underlying content is identical, and
        # comparing two different moments would defeat the dedup check.
        hash_frame = await _extract_hash_anchor_frame(input_path, tmp_dir)

        # Analyze motion via ffmpeg mestimate filter
        analyzer = MotionAnalyzer(target_keyframes=_FRAME_COUNT)
        try:
            motion = await analyzer.analyze_webm_via_ffmpeg(input_path, total_frames, duration)
            keyframe_indices = motion.keyframe_indices
            frame_times = motion.keyframe_times
        except Exception as exc:
            logger.warning("Motion analysis failed for WebM, using fallback", error=str(exc))
            motion = analyzer._create_fallback_motion(total_frames, duration)
            keyframe_indices = motion.keyframe_indices
            frame_times = motion.keyframe_times

        # Extract keyframes at motion peaks
        frames: list[Image.Image] = []

        for idx, timestamp in zip(keyframe_indices, frame_times, strict=True):
            frame_path = Path(tmp_dir) / f"frame_{idx}.png"
            await _ffmpeg_extract_frame(input_path, frame_path, timestamp)

            if frame_path.exists():
                frames.append(Image.open(frame_path).convert("RGBA"))
            else:
                frames.append(Image.new("RGBA", (_FRAME_SIZE, _FRAME_SIZE)))

        if not frames:
            raise StickerRenderError("No frames extracted from WebM")

        collage_png = _composite_collage(frames, "VIDEO STICKER", motion=motion)
        return RenderedSticker(
            collage_png=collage_png,
            duration=duration,
            frame_times=frame_times,
            motion=motion,
            hash_frame=hash_frame,
        )

    except StickerRenderError:
        raise
    except TimeoutError as exc:
        raise StickerRenderError("ffmpeg timed out") from exc
    except Exception as exc:
        raise StickerRenderError(f"WebM rendering failed: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _ffmpeg_extract_frame(input_path: Path, out_path: Path, timestamp: float) -> None:
    """Extract a single scaled+padded RGBA frame at ``timestamp`` via ffmpeg -ss.

    Shared by the motion-keyframe extraction loop and the dedup anchor-frame
    extraction (`_extract_hash_anchor_frame`) — same command, different
    timestamp source. Writes to ``out_path`` (PNG, by extension); silently
    leaves it missing on ffmpeg failure, same fail-open contract callers
    already handle for the keyframe loop.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={_FRAME_SIZE}:{_FRAME_SIZE}:force_original_aspect_ratio=decrease,"
        f"pad={_FRAME_SIZE}:{_FRAME_SIZE}:(ow-iw)/2:(oh-ih)/2:color=0x00000000",
        "-pix_fmt",
        "rgba",
        str(out_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc.wait(), timeout=15)


async def _extract_hash_anchor_frame(input_path: Path, tmp_dir: str) -> bytes:
    """Extract the t=0 dedup-hash anchor frame (ADR-0007 Decision 2).

    Deliberately NOT one of the motion-selected keyframes (see caller)
    Falls back to a blank RGBA frame on ffmpeg failure — mirrors the
    existing blank-frame fallback in the keyframe extraction loop, and
    keeps ``hash_frame`` a well-formed image `compute_image_hash()` can
    always parse (dedup itself fails open on a *degenerate* hash, not on
    an unparseable one).
    """
    anchor_path = Path(tmp_dir) / "hash_anchor.png"
    await _ffmpeg_extract_frame(input_path, anchor_path, 0.0)
    if anchor_path.exists():
        return anchor_path.read_bytes()

    logger.warning("Failed to extract dedup anchor frame, using blank fallback")
    buf = io.BytesIO()
    Image.new("RGBA", (_FRAME_SIZE, _FRAME_SIZE)).save(buf, format="PNG")
    return buf.getvalue()


async def _probe_video(path: str) -> tuple[int, float]:
    """Get total frame count and duration from a video file via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "quiet",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=nb_read_frames,duration",
        "-of",
        "csv=p=0",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    output = stdout.decode().strip()

    # Format: "duration,nb_read_frames" e.g. "2.933333,88"
    parts = output.split(",")
    if len(parts) < 2:
        # Fallback: try without frame counting (faster)
        return await _probe_video_fallback(path)

    try:
        duration = float(parts[0]) if parts[0] != "N/A" else 3.0
        total_frames = int(parts[1]) if parts[1] != "N/A" else int(duration * 30)
    except (ValueError, IndexError):
        return await _probe_video_fallback(path)

    return total_frames, duration


async def _probe_video_fallback(path: str) -> tuple[int, float]:
    """Fallback video probing without frame counting."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=duration,r_frame_rate",
        "-of",
        "csv=p=0",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    output = stdout.decode().strip()

    parts = output.split(",")
    try:
        duration = float(parts[0]) if parts[0] and parts[0] != "N/A" else 3.0
        if len(parts) > 1 and "/" in parts[1]:
            num, den = parts[1].split("/")
            fps = float(num) / float(den) if float(den) > 0 else 30.0
        else:
            fps = 30.0
        total_frames = max(1, int(duration * fps))
    except (ValueError, IndexError, ZeroDivisionError):
        # Absolute fallback: assume 3s at 30fps
        duration = 3.0
        total_frames = 90

    return total_frames, duration
