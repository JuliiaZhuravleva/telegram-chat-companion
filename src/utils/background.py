"""Fire-and-forget task spawning that survives garbage collection."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

# asyncio holds only weak references to tasks: the result of a bare
# ensure_future() can be garbage-collected mid-flight, silently dropping the
# work — the worst failure mode for audit-log writers whose whole point is
# that the row is the durable record. This set is the strong reference.
_TASKS: set[asyncio.Task[Any]] = set()


def fire_and_forget(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule ``coro`` without awaiting it; hold a strong ref until done.

    Exception handling stays the caller's job — wrap the coroutine body in
    its own try/except (the ``_safe_*`` convention). This helper only
    prevents the disappearing-task failure mode.
    """
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
