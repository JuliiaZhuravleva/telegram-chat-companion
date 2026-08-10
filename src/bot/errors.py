"""Global error handler — a crashed handler must not leave the UI hanging.

Nearly every callback handler in this project does its database work first and
calls ``callback.answer()`` last. That ordering is fine until an ``await``
before it raises: the callback query then goes unanswered, Telegram spins the
button until it times out, and the admin sees a control that "does nothing".
The traceback, meanwhile, goes only to aiogram's own logger.

So the failure is invisible in the one place it matters (the UI says nothing
happened) and inconspicuous in the other (a log nobody is tailing). This
handler closes both halves: it answers the callback so the spinner stops and
the person is told to retry, and it logs with enough structure to find later.

It is registered on the **dispatcher**, not on a feature router, because
aiogram propagates errors up the router tree — the dispatcher is the only
place that catches every handler, including ones added later that never
thought about this.

Scope note: this is a safety net, not a substitute for local handling. A
handler that can predictably fail should still say something specific; all
this guarantees is that nothing fails *silently*.
"""

from __future__ import annotations

import structlog
from aiogram.types import ErrorEvent, Update

logger = structlog.get_logger(__name__)

# No chat_config here on purpose: resolving it needs the container and the
# database, and this runs precisely when something like that has just failed.
# Guessing a language from a possibly-broken context to render an apology is
# not worth a second exception, so this uses the project's default language.
_ERROR_ALERT = "Ошибка, попробуйте ещё раз"


def _chat_id_of(update: Update) -> int | None:
    """Best-effort chat id for the log line.

    Written out rather than chained with ``or``/``and``: this runs on a path
    where things are already broken, and a clever expression that silently
    yields ``False`` or a stray attribute would corrupt the one record we get
    of what went wrong. Every step is allowed to be absent — a callback query
    older than 48 hours arrives with ``message`` unset.
    """
    callback = update.callback_query
    if callback is not None and callback.message is not None:
        return int(callback.message.chat.id)

    if update.message is not None:
        return int(update.message.chat.id)

    return None


async def handle_unexpected_error(event: ErrorEvent) -> bool:
    """Log any unhandled handler exception; un-hang the UI if it was a button.

    Returns ``True`` so aiogram treats the error as handled and does not log
    it a second time — this function is the report.
    """
    update = event.update
    callback = update.callback_query

    # exc_info is passed explicitly rather than relying on logger.exception()
    # reading sys.exc_info(): that couples the record to being called inside
    # the original except block, which is not a property worth depending on.
    logger.error(
        "unhandled_handler_error",
        error_type=type(event.exception).__name__,
        error=str(event.exception),
        update_id=update.update_id,
        callback_data=callback.data if callback is not None else None,
        chat_id=_chat_id_of(update),
        exc_info=event.exception,
    )

    if callback is not None:
        try:
            await callback.answer(_ERROR_ALERT, show_alert=True)
        except Exception as exc:
            # Already answered, expired (Telegram drops queries after ~1 min),
            # or the network is down. Nothing further to do — but do not let
            # the error handler itself raise, or aiogram logs *this* instead
            # of the original, which is strictly worse than where we started.
            logger.warning(
                "error_alert_delivery_failed",
                error_type=type(exc).__name__,
            )

    return True
