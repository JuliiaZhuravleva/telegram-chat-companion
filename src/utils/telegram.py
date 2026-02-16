"""Telegram file download utilities.

Shared by voice, image, and sticker handlers.
"""

from __future__ import annotations

import mimetypes
from io import BytesIO

import structlog
from aiogram import Bot

logger = structlog.get_logger(__name__)


class TelegramFileError(Exception):
    """Failed to download a file from Telegram servers."""


async def download_telegram_file(bot: Bot, file_id: str) -> bytes:
    """Download a file from Telegram servers by file_id.

    Steps:
    1. Call bot.get_file(file_id) to get File object with file_path.
    2. Download from Telegram CDN via bot.download_file(file_path).

    Returns:
        Raw bytes of the downloaded file.

    Raises:
        TelegramFileError: If download fails at any step.
    """
    try:
        file = await bot.get_file(file_id)
        if not file.file_path:
            raise TelegramFileError(f"No file_path returned for file_id={file_id}")

        buf = BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        data = buf.getvalue()

        if not data:
            raise TelegramFileError(f"Empty file downloaded for file_id={file_id}")

        logger.debug(
            "Downloaded telegram file",
            file_id=file_id,
            size=len(data),
            file_path=file.file_path,
        )
        return data

    except TelegramFileError:
        raise
    except Exception as exc:
        raise TelegramFileError(f"Failed to download file {file_id}: {exc}") from exc


def detect_mime_type(file_path: str) -> str:
    """Detect MIME type from a Telegram file path.

    Falls back to application/octet-stream if unknown.
    """
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        return mime

    # Manual fallbacks for common Telegram file extensions
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    ext_map = {
        "ogg": "audio/ogg",
        "oga": "audio/ogg",
        "opus": "audio/opus",
        "webp": "image/webp",
        "tgs": "application/gzip",
        "webm": "video/webm",
    }
    return ext_map.get(ext, "application/octet-stream")
