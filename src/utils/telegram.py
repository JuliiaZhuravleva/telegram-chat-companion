"""Telegram helpers: file download, chat URL formatting, MIME detection.

Shared by voice, image, sticker, and admin panel handlers.
"""

from __future__ import annotations

import mimetypes
from io import BytesIO

import structlog
from aiogram import Bot

logger = structlog.get_logger(__name__)


def build_chat_url(
    chat_id: int,
    chat_type: str | None = None,
    chat_username: str | None = None,
) -> str | None:
    """Return a clickable Telegram URL for a chat, or None if not linkable.

    Resolution order:

    - If ``chat_username`` is set → ``https://t.me/{username}`` (public link,
      works for anyone).
    - If ``chat_type`` is ``supergroup`` or ``channel`` AND ``chat_id`` is below
      ``-1_000_000_000_000`` → ``https://t.me/c/{internal_id}``. Telegram
      encodes supergroup/channel IDs as ``-100`` + internal ID, so we strip
      the sign and the ``"100"`` prefix to recover the portion used in
      ``t.me/c/`` links (e.g. ``-1001234567890`` → ``1234567890``). This link
      form only opens for existing members.
    - If ``chat_type == "private"`` and ``chat_id`` is a positive user ID →
      ``tg://user?id={chat_id}``.
    - Otherwise (legacy groups without username, unknown types) → ``None``;
      callers should fall back to plain text.
    """
    if chat_username:
        return f"https://t.me/{chat_username}"
    if chat_type in ("supergroup", "channel") and chat_id < -1_000_000_000_000:
        return f"https://t.me/c/{str(abs(chat_id))[3:]}"
    if chat_type == "private" and chat_id > 0:
        return f"tg://user?id={chat_id}"
    return None


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
