"""The global error handler (TD-062).

Two properties matter, and they fail differently:

* the **UI** must stop spinning — otherwise an admin sees a button that "does
  nothing" and has no way to tell a crash from a slow query;
* the failure must be **recorded** — otherwise the only trace is aiogram's own
  logger, and the incident is discovered by someone noticing an absence.

The wiring test at the bottom is the one that would actually have caught the
original defect: the handler being correct is not the same as the handler
being registered, and nothing in this codebase asserted the latter.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from src.bot.errors import handle_unexpected_error


def _error_event(*, callback=None, message=None, exception=None, update_id=7):
    """An ErrorEvent stand-in.

    A MagicMock rather than a real ErrorEvent: the pydantic model requires a
    fully-valid Update, which would make these tests about constructing
    Telegram payloads instead of about error handling.
    """
    update = MagicMock()
    update.update_id = update_id
    update.callback_query = callback
    update.message = message

    event = MagicMock()
    event.update = update
    event.exception = exception or RuntimeError("pool exhausted")
    return event


def _callback(*, data="adm_pnl_open:123", chat_id=-1001234567890):
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.chat.id = chat_id
    return callback


@pytest.mark.asyncio
async def test_failing_callback_is_answered_so_the_button_stops_spinning() -> None:
    callback = _callback()

    handled = await handle_unexpected_error(_error_event(callback=callback))

    callback.answer.assert_awaited_once()
    kwargs = callback.answer.await_args.kwargs
    assert kwargs.get("show_alert") is True, "a silent toast is too easy to miss on a failure"
    assert callback.answer.await_args.args[0], "the alert must carry text, not be empty"
    assert handled is True, "returning True stops aiogram logging the same error a second time"


@pytest.mark.asyncio
async def test_error_is_logged_with_the_context_needed_to_find_it() -> None:
    callback = _callback(data="adm_defs_tgl_kb_enabled:1")

    with capture_logs() as logs:
        await handle_unexpected_error(
            _error_event(callback=callback, exception=ValueError("bad row"))
        )

    entries = [e for e in logs if e["event"] == "unhandled_handler_error"]
    assert len(entries) == 1

    entry = entries[0]
    assert entry["error_type"] == "ValueError"
    assert entry["callback_data"] == "adm_defs_tgl_kb_enabled:1"
    assert entry["chat_id"] == -1001234567890
    assert entry["update_id"] == 7


@pytest.mark.asyncio
async def test_a_failing_answer_does_not_re_raise() -> None:
    """The safety net must not itself become the failure.

    A callback query expires after about a minute and answering it then raises.
    If that propagated, aiogram would log *this* exception instead of the
    original one — strictly worse than having no handler at all.
    """
    callback = _callback()
    callback.answer = AsyncMock(side_effect=RuntimeError("query is too old"))

    with capture_logs() as logs:
        handled = await handle_unexpected_error(_error_event(callback=callback))

    assert handled is True
    assert any(e["event"] == "unhandled_handler_error" for e in logs), (
        "the original error must still be recorded even when the alert fails"
    )
    assert any(e["event"] == "error_alert_delivery_failed" for e in logs)


@pytest.mark.asyncio
async def test_non_callback_update_is_logged_without_crashing() -> None:
    """Message updates have no query to answer; the log must still happen.

    Guards the attribute walking in ``_chat_id_of``: a message-shaped update
    has no ``callback_query``, and an over-clever expression would raise here
    — on the path whose entire job is to survive.
    """
    message = MagicMock()
    message.chat.id = 42

    with capture_logs() as logs:
        handled = await handle_unexpected_error(_error_event(callback=None, message=message))

    entry = next(e for e in logs if e["event"] == "unhandled_handler_error")
    assert entry["chat_id"] == 42
    assert entry["callback_data"] is None
    assert handled is True


def test_main_registers_the_error_handler_on_the_dispatcher() -> None:
    """The wiring, not the function — the assertion that actually bites.

    A correct handler nobody registered behaves exactly like no handler, and
    every other test in this file passes happily in that state.

    This reads ``src/main.py`` with ``ast`` instead of running it, because
    ``main()`` is one ~180-line coroutine that opens a database pool and a Bot
    before it ever touches the dispatcher — there is no seam to call. A source
    assertion is weaker than executing the wiring (it would not notice the
    call being made unreachable), but it does catch the realistic regression:
    someone deleting or renaming the registration. Recorded as TD-069.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/main.py").read_text())

    registrations = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "errors"
    ]
    assert registrations, (
        "src/main.py registers no error handler on dp.errors — the handler is "
        "then dead code and every behaviour test above proves nothing"
    )
    assert any(
        isinstance(arg, ast.Name) and arg.id == "handle_unexpected_error"
        for call in registrations
        for arg in call.args
    ), "dp.errors.register(...) exists but is not passed handle_unexpected_error"
