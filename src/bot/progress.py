"""A placeholder message that ticks while a long operation runs.

Transcribing a six-minute voice note takes ~15 seconds; a 500-message summary
takes longer. For that whole window the chat saw either nothing at all (voice)
or a frozen "⏳ Генерирую саммари..." (summary), which is indistinguishable
from the bot having ignored the request or died -- and when the delivery then
failed, that is exactly what had happened.

So the placeholder is not decoration: it is the only signal that work is in
progress, and it is what turns a silent failure into a visible one. It appears
immediately, updates while the work runs, and is finally *replaced by the
result* rather than deleted, so the answer lands where the reader was already
looking.

Nothing in here may raise into the caller. A cosmetic ticker that can kill the
operation it is reporting on would be strictly worse than no ticker: the
project has no flood-control middleware, and `editMessageText` answers a burst
with `TelegramRetryAfter`, a sibling of `TelegramBadRequest` that nothing in
`src/` currently catches.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import TracebackType

import structlog
from aiogram.types import Message

from src.bot.utils import safe_edit_text

logger = structlog.get_logger(__name__)

# How often the placeholder is refreshed. Deliberately slow: the point is to
# prove liveness, not to animate. At 5s a minute-long summary costs twelve
# edits, comfortably inside Telegram's per-chat budget, and every tick carries
# a different elapsed count so it can never be rejected as "not modified".
TICK_INTERVAL = 5.0

# Below this, a placeholder is noise -- a short voice note is transcribed and
# answered in about a second, and a "⏳" that appears and vanishes in that time
# reads as a glitch.
MIN_SECONDS_TO_ANNOUNCE = 20


class ProgressNotice:
    """Announce a long operation, keep the announcement alive, then replace it.

    Usage::

        async with ProgressNotice(message, text="🎙 Расшифровываю…") as notice:
            result = await slow_thing()
        await notice.finish(parts, language=lang)

    When ``enabled`` is False every method is a no-op and no message is ever
    sent, so a caller can decide per-request (audio duration, message count)
    without branching around the block.
    """

    def __init__(
        self,
        message: Message,
        *,
        text: str,
        enabled: bool = True,
        reply_to_message_id: int | None = None,
        interval: float = TICK_INTERVAL,
    ) -> None:
        self._message = message
        self._text = text
        self._enabled = enabled
        self._reply_to = reply_to_message_id
        self._interval = interval
        self._placeholder: Message | None = None
        self._task: asyncio.Task[None] | None = None
        self._started = 0.0

    @property
    def placeholder(self) -> Message | None:
        """The live placeholder, or None if it was never sent."""
        return self._placeholder

    async def __aenter__(self) -> ProgressNotice:
        if not self._enabled:
            return self
        self._started = time.monotonic()
        try:
            self._placeholder = await self._message.answer(
                self._text, reply_to_message_id=self._reply_to
            )
        except Exception as exc:
            # Losing the placeholder must not lose the operation it announces.
            logger.warning(
                "Could not post the progress placeholder",
                chat_id=self._message.chat.id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return self
        self._task = asyncio.create_task(self._tick())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._stop_ticking()
        # An exception must take the placeholder with it. `finish` and `fail`
        # are both called AFTER the block, so a raise inside it skips them
        # entirely and leaves "🎙 Расшифровываю…" standing in the chat for ever
        # -- a durable version of exactly the silence this class exists to
        # remove. The transcription path raises on more than AIProviderError:
        # the OpenAI provider wraps only httpx timeouts and HTTP errors, so a
        # 200 with a non-JSON body, or a 429 whose Retry-After is an HTTP-date,
        # comes straight through.
        #
        # CancelledError is excluded: on a deploy restart the discard would
        # itself be cancelled, and swallowing the cancellation to attempt it
        # would delay shutdown for a cosmetic cleanup.
        if exc_type is not None and not issubclass(exc_type, asyncio.CancelledError):
            await self._discard()

    async def _stop_ticking(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        # BaseException is deliberate: a ticker that dies of anything at all
        # must not take the operation it was reporting on with it.
        with contextlib.suppress(BaseException):
            await task

    async def _tick(self) -> None:
        """Refresh the placeholder until cancelled.

        Stops at the first failure rather than retrying. A chat that is
        rejecting our edits (flood control, a deleted placeholder) will keep
        rejecting them, and a loop that keeps trying turns one throttled edit
        into a stream of them.
        """
        while True:
            await asyncio.sleep(self._interval)
            elapsed = int(time.monotonic() - self._started)
            placeholder = self._placeholder
            if placeholder is None:
                return
            try:
                await safe_edit_text(
                    placeholder,
                    f"{self._text} · {elapsed} s",
                    parse_mode=None,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info(
                    "Progress placeholder stopped updating",
                    chat_id=self._message.chat.id,
                    error_type=type(exc).__name__,
                )
                return

    async def finish(self, parts: list[str], *, language: str) -> list[Message]:
        """Replace the placeholder with the result; send any further parts.

        Returns every message the result occupies, in order. Falls back to a
        plain send when there is no placeholder to edit (it was never posted,
        or someone deleted it), so the result is delivered either way.
        """
        from src.bot.reply_flow import send_html_parts

        await self._stop_ticking()
        if not parts:
            # `split_html` returns no parts when the body has no visible text.
            # Leaving the placeholder up would promise a result that is never
            # coming, so drop it rather than freeze it.
            await self._discard()
            return []

        placeholder = self._placeholder
        if placeholder is not None:
            try:
                await placeholder.edit_text(parts[0], parse_mode="HTML")
            except Exception as exc:
                logger.info(
                    "Could not edit the placeholder into the result; sending fresh",
                    chat_id=self._message.chat.id,
                    error_type=type(exc).__name__,
                )
            else:
                rest = await send_html_parts(
                    message=self._message,
                    parts=parts[1:],
                    reply_to_message_id=None,
                    language=language,
                )
                self._placeholder = None
                return [placeholder, *rest]
            # The edit failed: drop the stale "⏳" so it cannot sit in the chat
            # looking like work still in flight, then send the result outright.
            await self._discard()

        return await send_html_parts(
            message=self._message,
            parts=parts,
            reply_to_message_id=self._reply_to,
            language=language,
        )

    async def fail(self, text: str) -> None:
        """Turn the placeholder into a failure line, or send one.

        Silent when the notice was disabled: a caller that only announces long
        operations must not start reporting on short ones, which would be a
        new class of message rather than a fix to an existing one.
        """
        await self._stop_ticking()
        if not self._enabled:
            return
        placeholder = self._placeholder
        self._placeholder = None
        try:
            if placeholder is not None:
                await safe_edit_text(placeholder, text, parse_mode=None)
            else:
                await self._message.answer(text)
        except Exception as exc:
            logger.warning(
                "Could not report the failure to the chat",
                chat_id=self._message.chat.id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _discard(self) -> None:
        placeholder, self._placeholder = self._placeholder, None
        if placeholder is None:
            return
        try:
            await placeholder.delete()
        except Exception:
            # A placeholder we cannot delete is a cosmetic problem only.
            logger.debug("Could not delete the progress placeholder")
