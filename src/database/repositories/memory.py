"""Repository for chat_memory (RAG) table."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


class MemoryRepository:
    """Data access layer for RAG vector memory."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def store(
        self,
        chat_id: int,
        content: str,
        embedding: list[float],
        *,
        source_message_id: int | None = None,
        importance_score: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Store a memory with its embedding vector. Returns the new record ID."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO chat_memory
                (chat_id, content, embedding, source_message_id,
                 importance_score, metadata)
            VALUES ($1, $2, $3::vector, $4, $5, $6::jsonb)
            RETURNING id
            """,
            chat_id, content, str(embedding), source_message_id,
            importance_score,
            None if metadata is None else json.dumps(metadata),
        )
        return int(row["id"])

    async def search(
        self,
        chat_id: int,
        query_embedding: list[float],
        *,
        min_similarity: float = 0.65,
        max_results: int = 5,
    ) -> list[asyncpg.Record]:
        """Search memories by cosine similarity."""
        result: list[asyncpg.Record] = await self._pool.fetch(
            """
            SELECT id, content, metadata, importance_score,
                   1 - (embedding <=> $2::vector) AS similarity,
                   created_at
            FROM chat_memory
            WHERE chat_id = $1
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> $2::vector) >= $3
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY embedding <=> $2::vector ASC
            LIMIT $4
            """,
            chat_id, str(query_embedding), min_similarity, max_results,
        )
        return result

    async def delete(self, memory_id: int, *, chat_id: int) -> None:
        """Delete a specific memory entry (scoped to chat for access control)."""
        await self._pool.execute(
            "DELETE FROM chat_memory WHERE id = $1 AND chat_id = $2",
            memory_id, chat_id,
        )

    async def delete_expired(self) -> int:
        """Delete expired memories. Returns count of deleted rows."""
        result = await self._pool.execute(
            "DELETE FROM chat_memory WHERE expires_at IS NOT NULL AND expires_at < NOW()"
        )
        return int(result.split()[-1])
