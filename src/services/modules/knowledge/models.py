"""Data models for the per-chat Knowledge Base (`chat_facts`, ADR-0003).

Phase 1 (manual MVP) scope: `ChatFact` mirrors the `chat_facts` table
(migration 014). Extraction/reconciliation/scheduling models land here too in
later phases (Phase 2+, see `docs/plans/knowledge-base-research-2026-07-23.md`
§3.6) -- this module, not `database/repositories/`, per ADR-0003's repository-
location correction (repository = `src/database/repositories/knowledge.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class FactStatus(StrEnum):
    """Lifecycle status of a `chat_facts` row (MemStrata bi-temporal pattern).

    Phase 1 only ever writes ACTIVE (manual entry) or REJECTED (organizer
    removal) -- PENDING and SUPERSEDED are exercised starting Phase 2
    (autocollection confirmation queue / reconciler), but the column exists
    now so migration 014 needs no Phase-2 follow-up ALTER TABLE.
    """

    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class FactSource(StrEnum):
    """Provenance of a `chat_facts` row."""

    MANUAL = "manual"
    EXTRACTED = "extracted"


@dataclass
class ChatFact:
    """A single fact in a chat's Knowledge Base.

    Mirrors `chat_facts` (migration 014, ADR-0003). `KnowledgeRepository`'s
    read methods return plain `dict`/`asyncpg.Record` (matching the existing
    repository convention -- see `StickerRepository`/`RulesRepository` --
    and the shape `trim_facts_to_budget()` in `prompt_builder.py` expects,
    per ADR-0003 Part 2); this dataclass is the typed domain model for
    callers that want one (e.g. constructing a fact before a write, or a
    typed view in A4/A5) via `from_record()`.
    """

    id: int
    chat_id: int
    subject: str
    predicate: str
    value: str
    fact_text: str
    source: FactSource
    topic: str | None = None
    embedding: list[float] | None = None
    status: FactStatus = FactStatus.ACTIVE
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    superseded_by: int | None = None
    source_message_id: int | None = None
    source_user_id: int | None = None
    authority_level: int = 0
    confidence: float | None = None
    salience: float = 0.5
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_record(cls, record: Any) -> ChatFact:
        """Build a `ChatFact` from an `asyncpg.Record` (or any `Mapping`)."""
        data = dict(record)
        return cls(
            id=data["id"],
            chat_id=data["chat_id"],
            subject=data["subject"],
            predicate=data["predicate"],
            value=data["value"],
            fact_text=data["fact_text"],
            source=FactSource(data["source"]),
            topic=data.get("topic"),
            embedding=data.get("embedding"),
            status=FactStatus(data["status"]),
            valid_from=data.get("valid_from"),
            valid_to=data.get("valid_to"),
            superseded_by=data.get("superseded_by"),
            source_message_id=data.get("source_message_id"),
            source_user_id=data.get("source_user_id"),
            authority_level=data.get("authority_level", 0),
            confidence=data.get("confidence"),
            salience=data.get("salience", 0.5),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )
