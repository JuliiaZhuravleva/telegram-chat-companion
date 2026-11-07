"""The organizer writers of a ``ChatSettingsRepository`` double.

The handlers write organizers only through ``add_kb_organizer``,
``remove_kb_organizer`` and ``restore_kb_organizer``. This fake gives a
``MagicMock`` repository those three, kept honest against the row its ``get``
returns: a write changes what the next ``get`` reads, and a chat with no row
(``get`` answering None) makes every writer answer None, as the single
``UPDATE … RETURNING`` does. The SQL itself is tested against PostgreSQL in
``tests/integration/test_kb_organizer_writers.py``.

The row is read off ``repo.get.return_value`` at each call, so a test that
replaces ``repo.get`` afterwards still writes over the row it set. ``set_field``
is deliberately left out: awaiting a bare ``MagicMock`` attribute raises, so a
handler that went back to rewriting the whole list fails loudly.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.utils import parse_user_id_list

WRITERS = ("add_kb_organizer", "remove_kb_organizer", "restore_kb_organizer")


def wire_organizer_writers(repo: MagicMock) -> MagicMock:
    """Give ``repo`` the three organizer writers, over the row ``repo.get`` returns."""

    def stored() -> list[int] | None:
        row = repo.get.return_value
        if not isinstance(row, dict) or not row:
            return None
        return parse_user_id_list(row.get("kb_organizer_ids"))

    def keep(ids: list[int]) -> list[int]:
        repo.get.return_value = {**repo.get.return_value, "kb_organizer_ids": json.dumps(ids)}
        return list(ids)

    # chat_id keeps the real signature (handlers may pass it by name); which
    # chat was written is checked by `written`, off the await args.
    async def add(chat_id: int, user_id: int) -> list[int] | None:  # noqa: ARG001
        ids = stored()
        if ids is None:
            return None
        return keep(ids if user_id in ids else [*ids, user_id])

    async def remove(chat_id: int, user_id: int) -> list[int] | None:  # noqa: ARG001
        ids = stored()
        if ids is None:
            return None
        return keep([uid for uid in ids if uid != user_id])

    async def restore(
        chat_id: int,  # noqa: ARG001
        user_id: int,
        position: int | None,
    ) -> list[int] | None:
        ids = stored()
        if ids is None:
            return None
        if user_id in ids:
            return keep(ids)
        at = len(ids) if position is None else min(max(position, 0), len(ids))
        return keep([*ids[:at], user_id, *ids[at:]])

    repo.add_kb_organizer = AsyncMock(side_effect=add)
    repo.remove_kb_organizer = AsyncMock(side_effect=remove)
    repo.restore_kb_organizer = AsyncMock(side_effect=restore)
    return repo


def written(repo: Any, chat_id: int) -> list[int] | None:
    """The organizers the one write left stored for ``chat_id``; None when nothing wrote.

    Fails on more than one write, or a write to another chat.
    """
    writers = [getattr(repo, name) for name in WRITERS]
    assert all(isinstance(w, AsyncMock) for w in writers), "wire_organizer_writers(repo) first"
    calls = [call for writer in writers for call in writer.await_args_list]
    if not calls:
        return None
    assert len(calls) == 1, f"expected one organizer write, got {calls}"
    assert calls[0].args[0] == chat_id, calls[0]
    row = repo.get.return_value
    return parse_user_id_list(row.get("kb_organizer_ids")) if isinstance(row, dict) else None
