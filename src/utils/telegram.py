"""Telegram helpers: file download, chat URL formatting, MIME detection,
chat-action ("bot is typing") indicator.

Shared by voice, image, sticker, and admin panel handlers.
"""

from __future__ import annotations

import asyncio
import mimetypes
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramUnauthorizedError,
)
from aiogram.utils.chat_action import ChatActionSender

logger = structlog.get_logger(__name__)

# Re-send the chat action slightly under Telegram's ~5s client-side expiry
# so the indicator never visibly flickers off between re-sends.
_TYPING_INDICATOR_INTERVAL = 4.0

# Seconds for one download attempt, passed explicitly rather than inherited —
# see `download_telegram_file`'s docstring for why the library's own default
# cannot be relied on. Same value aiogram uses today, so this pins current
# behaviour rather than changing it.
DOWNLOAD_TIMEOUT_SECONDS = 30

# How many times one download may be attempted in total. Three matches
# `FloodControlMiddleware.DEFAULT_MAX_ATTEMPTS`, deliberately: a bot with two
# different retry budgets is a bot whose worst-case latency nobody can state.
# Measured cause for this existing: 9 voice notes and video notes lost over
# three days in production, 8 of them at 30-34s wall clock — the CDN fetch
# stalling out — while the very same files downloaded in under a second when
# retried days later.
DOWNLOAD_ATTEMPTS = 3

# Linear backoff: ~1s, then ~2s. The observed failure is a stalled connection,
# not a busy server, so the point of waiting at all is to get a fresh socket
# rather than to let a queue drain.
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_MAX_JITTER_SECONDS = 0.5

# Answers that will not change no matter how many times we ask: the file is
# gone, too big for the Bot API's 20 MB download ceiling, or not ours to fetch.
# Retrying these turns one permanent failure into three and delays the caller's
# own degradation by the whole backoff budget.
_PERMANENT_DOWNLOAD_ERRORS = (
    TelegramBadRequest,
    TelegramNotFound,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramUnauthorizedError,
)


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


@dataclass(frozen=True)
class ChatReference:
    """A chat identifier parsed out of admin-supplied text (D-1 shortcut).

    Exactly one field is set:

    - ``chat_id`` — resolved directly from the text, no network call needed
      (a ``t.me/c/<internal_id>`` link — the reverse of what ``build_chat_url``
      produces for a supergroup/channel — or a bare chat id the admin pasted,
      e.g. copied from a panel header's ``<code>{chat_id}</code>``).
    - ``username`` — a ``t.me/<username>`` link or ``@username``; the caller
      still needs one ``bot.get_chat()`` call to learn which chat this is.
    """

    chat_id: int | None = None
    username: str | None = None


# t.me/c/<internal_id>[/<message_id>] — build_chat_url's private-link form for
# supergroups/channels. See its docstring for the -100 prefix/internal-id split.
_TME_C_RE = re.compile(r"(?:https?://)?t\.me/c/(\d+)(?:/\d+)?/?$", re.IGNORECASE)
# t.me/<username>[/<message_id>] — the public-link form. Telegram usernames are
# 5-32 chars, start with a letter, then letters/digits/underscores.
_TME_USERNAME_RE = re.compile(
    r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?/?$", re.IGNORECASE
)
_AT_USERNAME_RE = re.compile(r"^@([A-Za-z][A-Za-z0-9_]{4,31})$")
# A bare chat id an admin might paste (e.g. copied from a panel header, or from
# a forwarded message's raw id). Supergroup/channel ids are always negative, so
# requiring the leading "-" keeps a plain numeric *title* query (rare, but
# possible) from being misread as an id.
_RAW_CHAT_ID_RE = re.compile(r"^-\d+$")

# t.me path segments that are not usernames (private invite links, story/IV
# viewers, sticker packs, ...) — matching one as a username would silently
# "resolve" to nonsense instead of falling through to a title search.
_TME_RESERVED_PATHS = frozenset({"c", "s", "iv", "joinchat", "proxy", "addstickers", "share"})


def parse_chat_reference(text: str) -> ChatReference | None:
    """Parse admin-supplied text into a chat reference (D-1 shortcut).

    Returns ``None`` when ``text`` doesn't look like a chat link/id at all —
    callers should fall back to a title search in that case. Recognizes:

    - ``t.me/c/<internal_id>`` → ``chat_id`` (pure, no API call).
    - a bare negative chat id (``-1001234567890``) → ``chat_id`` directly.
    - ``t.me/<username>`` or ``@username`` → ``username`` (caller resolves
      via ``bot.get_chat()``).

    Private invite links (``t.me/+...``, ``t.me/joinchat/...``) are not
    resolvable without joining and deliberately fall through to ``None``.
    """
    candidate = text.strip()
    if not candidate:
        return None

    m = _TME_C_RE.search(candidate)
    if m:
        internal_id = m.group(1)
        return ChatReference(chat_id=-int(f"100{internal_id}"))

    if _RAW_CHAT_ID_RE.match(candidate):
        return ChatReference(chat_id=int(candidate))

    m = _AT_USERNAME_RE.match(candidate)
    if m:
        return ChatReference(username=m.group(1))

    m = _TME_USERNAME_RE.search(candidate)
    if m and m.group(1).lower() not in _TME_RESERVED_PATHS:
        return ChatReference(username=m.group(1))

    return None


class TelegramFileError(Exception):
    """Failed to download a file from Telegram servers."""


async def _attempt_download(bot: Bot, file_id: str, *, timeout: int) -> bytes:
    """One get_file + CDN fetch. Raises the underlying error unwrapped."""
    file = await bot.get_file(file_id, request_timeout=timeout)
    if not file.file_path:
        raise TelegramFileError(f"No file_path returned for file_id={file_id}")

    # A FRESH buffer per attempt, and the single most important line in this
    # module. `download_file` writes chunks straight into `destination` and
    # only rewinds it once the stream completes, so a timeout mid-download
    # leaves a partial file sitting at the write head. Hoisting this out of the
    # retry loop -- the obvious refactor -- would append attempt two onto
    # attempt one's truncated bytes, sail past the emptiness check below, and
    # hand a corrupt audio file to Whisper, billed and transcribed into
    # nonsense. Every existing test mocks `download_file` to write once and
    # would stay green.
    buf = BytesIO()
    await bot.download_file(file.file_path, destination=buf, timeout=timeout)
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


async def download_telegram_file(
    bot: Bot,
    file_id: str,
    *,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
    attempts: int = DOWNLOAD_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    jitter: Callable[[], float] | None = None,
) -> bytes:
    """Download a file from Telegram servers by file_id.

    Steps, up to ``attempts`` times:

    1. ``bot.get_file(file_id)`` for the ``file_path``.
    2. ``bot.download_file(file_path)`` — a plain GET against Telegram's file
       CDN.

    ## Why this retries, when FloodControlMiddleware refuses to

    That middleware's docstring argues a retry is only safe when the first
    attempt provably did nothing, and narrows itself to ``TelegramRetryAfter``
    for exactly that reason: re-sending an ambiguous *send* can post a message
    to a group twice. None of that applies here. This is a GET; it has no
    chat-visible side effect, changes nothing on Telegram's side, and the caller
    already throws away whatever it returns on failure. Retrying a download is
    the same argument reaching the opposite conclusion, not an exception to it.

    Note also that the middleware could not cover step 2 even if it wanted to:
    it wraps ``make_request``, and ``download_file`` streams through
    ``session.stream_content()``, bypassing the request middleware chain
    entirely. A stalled CDN fetch has never had any retry anywhere.

    ## Why the timeout is passed explicitly

    aiogram's ``download_file`` carries its own ``timeout: int = 30`` default,
    and ``pyproject.toml`` pins nothing tighter than ``aiogram>=3.4.0`` while
    the Dockerfile and both CI jobs install from it — so prod, CI and a
    developer's venv can each resolve a different aiogram and a different
    number. Passing it makes the value a fact of this project rather than of
    whatever version resolved. It is deliberately the same 30s the library
    already used: the failures this fixes were stalls that never completed, not
    transfers that were merely slow (a 9.6 MB video note normally lands in
    0.8s), so more attempts help and a longer clock would not.

    Raises:
        TelegramFileError: every failure, after the attempts are spent. The
            concrete exception type is named in the message — four callers see
            only this type, and "the CDN stalled" versus "the file is gone" are
            different situations that read identically once the type is lost.
    """
    _sleep = sleep or asyncio.sleep
    _jitter = jitter or (lambda: random.uniform(0.0, _RETRY_MAX_JITTER_SECONDS))

    for attempt in range(1, attempts + 1):
        try:
            return await _attempt_download(bot, file_id, timeout=timeout)
        except TelegramFileError:
            # Ours, from the two checks above: no file_path, or zero bytes.
            # Neither is a transient network condition and neither improves by
            # being asked again -- and retrying them would turn one permanent
            # failure into three, which is how a fix becomes a slower bug.
            raise
        except _PERMANENT_DOWNLOAD_ERRORS as exc:
            # The file is gone, too big, or we are not allowed to have it.
            raise TelegramFileError(
                f"Failed to download file {file_id}: {type(exc).__name__}: {exc}"
            ) from exc
        except Exception as exc:
            if attempt == attempts:
                raise TelegramFileError(
                    f"Failed to download file {file_id} after {attempts} attempts: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            delay = _RETRY_BASE_DELAY_SECONDS * attempt + _jitter()
            logger.info(
                "Telegram file download failed, retrying",
                file_id=file_id,
                attempt=attempt,
                attempts=attempts,
                retry_in=round(delay, 2),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await _sleep(delay)

    raise AssertionError("unreachable: the loop either returns or raises")


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


@asynccontextmanager
async def typing_indicator(
    bot: Bot,
    chat_id: int,
    message_thread_id: int | None,
    action: str = "typing",
    *,
    enabled: bool = True,
) -> AsyncIterator[None]:
    """Show a chat action (default "typing") for the duration of the block.

    Thin wrapper around aiogram's ``ChatActionSender``: it keep-alives the
    action by re-sending every ~4s (Telegram expires it client-side after
    ~5s) and guarantees it stops on exit — including when the wrapped
    operation raises. Use this instead of a one-shot ``bot.send_chat_action``
    call or a hand-rolled ``asyncio.create_task``; a single send goes stale
    and silently drops on any operation over ~5s (this is what I-1 fixed).

    Args:
        bot: Bot instance.
        chat_id: Target chat id.
        message_thread_id: Forum topic id, or ``None`` for non-forum chats.
            **Required** — unlike ``message.answer()``, ``bot.send_chat_action``
            does NOT inherit the topic from the triggering message. Omitting
            it silently routes the indicator to the forum's General topic.
        action: Chat action type — ``"typing"`` by default, ``"choose_sticker"``
            / ``"upload_photo"`` where the bot is committed to a sticker/image
            reply. See ``ChatActionSender`` classmethods for the full set of
            valid Bot API chat actions.
        enabled: Single point through which a future per-chat toggle is meant
            to flow (read from ``ChatConfig`` once `chat_settings` gains a
            column for it — none exists yet). Defaults to ``True``; callers
            should rely on the default rather than hardcoding ``enabled=True``.
    """
    if not enabled:
        yield
        return

    async with ChatActionSender(
        bot=bot,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        action=action,
        interval=_TYPING_INDICATOR_INTERVAL,
    ):
        yield
