"""RAG memory service — vector search and storage."""

from __future__ import annotations

from typing import Any

import structlog

from src.database.repositories.memory import MemoryRepository
from src.services.ai.router import AIRouter

logger = structlog.get_logger(__name__)


class RAGMemoryService:
    """Retrieval-Augmented Generation memory using pgvector.

    Stores and retrieves contextual memories for each chat using
    embedding-based cosine similarity search.
    """

    def __init__(
        self,
        memory_repo: MemoryRepository,
        ai_router: AIRouter,
        *,
        min_similarity: float = 0.65,
        max_results: int = 5,
    ) -> None:
        self._repo = memory_repo
        self._ai_router = ai_router
        self._min_similarity = min_similarity
        self._max_results = max_results

    @property
    def min_similarity(self) -> float:
        """Effective similarity floor (read by retrieval_log params)."""
        return self._min_similarity

    @property
    def max_results(self) -> int:
        """Effective result cap (read by retrieval_log params)."""
        return self._max_results

    async def search(
        self,
        chat_id: int,
        query: str,
        *,
        min_similarity: float | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search memories relevant to a query.

        Returns list of dicts with keys: id, content, similarity, metadata,
        created_at.
        """
        try:
            embedding_result = await self._ai_router.generate_embedding(query)
        except Exception:
            logger.warning("Failed to generate query embedding for RAG search")
            return []

        rows = await self._repo.search(
            chat_id=chat_id,
            query_embedding=embedding_result.embedding,
            min_similarity=min_similarity or self._min_similarity,
            max_results=max_results or self._max_results,
        )

        return [
            {
                "id": row["id"],
                "content": row["content"],
                "similarity": float(row["similarity"]),
                "metadata": row["metadata"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def store(
        self,
        chat_id: int,
        content: str,
        *,
        source_message_id: int | None = None,
        importance_score: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        """Store a memory with its embedding. Returns memory ID or None on failure."""
        try:
            embedding_result = await self._ai_router.generate_embedding(content)
        except Exception:
            logger.warning("Failed to generate embedding for RAG store")
            return None

        return await self._repo.store(
            chat_id=chat_id,
            content=content,
            embedding=embedding_result.embedding,
            source_message_id=source_message_id,
            importance_score=importance_score,
            metadata=metadata,
        )

    async def delete(self, memory_id: int, *, chat_id: int) -> None:
        """Delete a specific memory (scoped to chat for access control)."""
        await self._repo.delete(memory_id, chat_id=chat_id)
