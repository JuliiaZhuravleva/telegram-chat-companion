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
from pathlib import Path

import structlog
from PIL import Image, ImageDraw, ImageFont

from src.services.modules.sticker.models import StickerRenderError

logger = structlog.get_logger(__name__)

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


def _composite_collage(
    frames: list[Image.Image],
    label: str,
) -> bytes:
    """Arrange frames in a 3x2 grid with labels, return PNG bytes."""
    # Pad frames list to exactly _FRAME_COUNT
    while len(frames) < _FRAME_COUNT:
        frames.append(frames[-1].copy() if frames else Image.new("RGBA", (_FRAME_SIZE, _FRAME_SIZE)))

    collage_w = _COLS * _FRAME_SIZE
    collage_h = _LABEL_HEIGHT + _ROWS * _FRAME_SIZE
    collage = Image.new("RGBA", (collage_w, collage_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(collage)

    # Title label
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    draw.text((8, 2), label, fill=(30, 100, 200), font=font)

    # Place frames
    frame_labels = [
        "Frame 1 (start)", "Frame 2", "Frame 3",
        "Frame 4", "Frame 5", "Frame 6 (end)",
    ]

    try:
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except OSError:
        small_font = ImageFont.load_default()

    for idx, frame in enumerate(frames[:_FRAME_COUNT]):
        col = idx % _COLS
        row = idx // _COLS
        x = col * _FRAME_SIZE
        y = _LABEL_HEIGHT + row * _FRAME_SIZE

        resized = frame.resize((_FRAME_SIZE, _FRAME_SIZE), Image.LANCZOS)
        collage.paste(resized, (x, y), resized if resized.mode == "RGBA" else None)

        # Frame label overlay
        if idx < len(frame_labels):
            draw.text((x + 4, y + 2), frame_labels[idx], fill=(50, 50, 50), font=small_font)

    buf = io.BytesIO()
    collage.save(buf, format="PNG")
    return buf.getvalue()


def _render_tgs_sync(tgs_data: bytes) -> bytes:
    """Synchronous TGS rendering — runs in a thread."""
    from rlottie_python import LottieAnimation

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

    indices = _pick_frame_indices(total_frames)

    frames: list[Image.Image] = []
    for frame_num in indices:
        try:
            im = anim.render_pillow_frame(frame_num=frame_num)
            frames.append(im)
        except Exception as exc:
            logger.warning("Failed to render TGS frame", frame_num=frame_num, error=str(exc))
            # Use a blank frame
            w, h = anim.lottie_animation_get_size()
            frames.append(Image.new("RGBA", (w or 512, h or 512)))

    return _composite_collage(frames, "ANIMATED STICKER")


async def render_tgs(tgs_data: bytes) -> bytes:
    """Render .tgs (Lottie) sticker into a 6-frame PNG collage.

    Args:
        tgs_data: Raw .tgs file bytes (gzip-compressed Lottie JSON).

    Returns:
        PNG bytes of the 3x2 frame collage.

    Raises:
        StickerRenderError: If rendering fails.
    """
    try:
        return await asyncio.to_thread(_render_tgs_sync, tgs_data)
    except StickerRenderError:
        raise
    except Exception as exc:
        raise StickerRenderError(f"TGS rendering failed: {exc}") from exc


async def render_webm(webm_data: bytes) -> bytes:
    """Render .webm (VP9 video) sticker into a 6-frame PNG collage.

    Args:
        webm_data: Raw .webm file bytes.

    Returns:
        PNG bytes of the 3x2 frame collage.

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

        # Extract 6 evenly-spaced frames
        indices = _pick_frame_indices(total_frames)
        frames: list[Image.Image] = []

        for idx in indices:
            timestamp = (idx / total_frames) * duration if total_frames > 0 else 0
            frame_path = Path(tmp_dir) / f"frame_{idx}.png"

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-ss", f"{timestamp:.3f}",
                "-i", str(input_path),
                "-frames:v", "1",
                "-vf", f"scale={_FRAME_SIZE}:{_FRAME_SIZE}:force_original_aspect_ratio=decrease,"
                       f"pad={_FRAME_SIZE}:{_FRAME_SIZE}:(ow-iw)/2:(oh-ih)/2:color=0x00000000",
                "-pix_fmt", "rgba",
                str(frame_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=15)

            if frame_path.exists():
                frames.append(Image.open(frame_path).convert("RGBA"))
            else:
                frames.append(Image.new("RGBA", (_FRAME_SIZE, _FRAME_SIZE)))

        if not frames:
            raise StickerRenderError("No frames extracted from WebM")

        return _composite_collage(frames, "VIDEO STICKER")

    except StickerRenderError:
        raise
    except TimeoutError as exc:
        raise StickerRenderError("ffmpeg timed out") from exc
    except Exception as exc:
        raise StickerRenderError(f"WebM rendering failed: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _probe_video(path: str) -> tuple[int, float]:
    """Get total frame count and duration from a video file via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "quiet",
        "-count_frames",
        "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_frames,duration",
        "-of", "csv=p=0",
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
        "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration,r_frame_rate",
        "-of", "csv=p=0",
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
