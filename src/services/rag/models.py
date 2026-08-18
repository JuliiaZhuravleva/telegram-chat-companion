"""Pure data shapes of the chunk pipeline (S4).

Separate from `chunker.py` because both sides of the boundary need them: the
chunker produces `Chunk`s, `ChunkRepository` writes them, and the repository
must not have to import the chunking algorithm to know the shape of a row.
Same arrangement as `services/modules/reactions/models.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceMessage:
    """One message as the chunker needs it -- already known to have text."""

    message_id: int
    created_at: datetime
    text: str
    user_id: int | None = None
    name: str | None = None
    is_bot: bool = False


@dataclass(frozen=True)
class Chunk:
    """One indexable slice of conversation.

    `part` is the chunk's position inside its session, so the natural key
    `(chat_id, thread_id, msg_from, msg_to, part)` stays stable across re-runs
    of the same input -- that is what makes `ON CONFLICT DO NOTHING` a real
    idempotency guarantee rather than a hope.
    """

    chat_id: int
    thread_id: int | None
    msg_from: int
    msg_to: int
    part: int
    content: str
    senders: tuple[int, ...]
    msg_count: int
    started_at: datetime
    ended_at: datetime
