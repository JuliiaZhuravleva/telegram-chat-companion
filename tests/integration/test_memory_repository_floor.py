"""Integration test: the RAG similarity floor is no longer in SQL (R1).

Unit tests for this change assert on the query *string* and on mocked rows,
which can only confirm what the implementation already says. This file drives
the real ``MemoryRepository.search()`` against real pgvector rows, and pairs
every positive assertion with the pre-R1 SQL run over the same fixture — so
"the floor is gone" is measured against a query that demonstrably filters,
rather than against a fixture that happened to have nothing to filter.

Why it matters beyond tidiness: with the floor in ``WHERE``, a sub-floor row
was never returned, never logged, and therefore absent from ``retrieval_log``.
A turn that retrieved nothing recorded an empty result, so the data could not
distinguish a best match that missed by 0.001 from one that missed by 0.3 --
and ``docs/plans/rag-revision-2026-08.md`` §4.2 plans to re-calibrate the floor
from exactly those distributions.
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.memory import MemoryRepository
from src.services.rag.memory import RAGMemoryService

_EMBED_DIM = 768
CHAT = -930101

# The pre-R1 predicate, kept verbatim so the control runs the query that
# actually shipped rather than a paraphrase of it.
_PRE_R1_SQL = """
    SELECT id, content, 1 - (embedding <=> $2) AS similarity
    FROM chat_memory
    WHERE chat_id = $1
      AND embedding IS NOT NULL
      AND 1 - (embedding <=> $2) >= $3
      AND (expires_at IS NULL OR expires_at > NOW())
    ORDER BY embedding <=> $2 ASC
    LIMIT $4
"""


def _vector(*components: tuple[int, float]) -> list[float]:
    """A unit vector with the given (index, weight) components.

    Cosine similarity against ``_vector((0, 1.0))`` is then simply the weight
    on index 0 -- discrete, exact control over ranking without depending on
    any real embedding semantics.
    """
    vec = [0.0] * _EMBED_DIM
    for index, weight in components:
        vec[index] = weight
    norm = math.sqrt(sum(w * w for w in vec))
    return [w / norm for w in vec]


QUERY_VECTOR = _vector((0, 1.0))

# similarity 1.0, 0.6 and 0.0 against QUERY_VECTOR.
_FIXTURE = [
    ("exact match", _vector((0, 1.0)), 1.0),
    ("near miss", _vector((0, 0.6), (1, 0.8)), 0.6),
    ("unrelated", _vector((1, 1.0)), 0.0),
]


@pytest_asyncio.fixture
async def repo(db_pool: asyncpg.Pool) -> MemoryRepository:
    return MemoryRepository(db_pool)


@pytest_asyncio.fixture
async def spread_of_similarities(db_pool: asyncpg.Pool, repo: MemoryRepository) -> None:
    """Three memories straddling any floor worth testing.

    Asserted before use: a fixture whose rows do not actually span the
    threshold would let every test below pass vacuously.
    """
    await db_pool.execute("DELETE FROM chat_memory WHERE chat_id = $1", CHAT)
    for content, embedding, _ in _FIXTURE:
        await repo.store(CHAT, content, embedding)

    rows = await db_pool.fetch(
        "SELECT 1 - (embedding <=> $2) AS similarity FROM chat_memory WHERE chat_id = $1",
        CHAT,
        QUERY_VECTOR,
    )
    sims = sorted(float(row["similarity"]) for row in rows)
    assert len(sims) == 3, "fixture did not land"
    assert sims[0] < 0.7 < sims[-1], f"fixture does not straddle the floor: {sims}"


def _service(repo: MemoryRepository, floor: float) -> RAGMemoryService:
    return RAGMemoryService(repo, AsyncMock(), min_similarity=floor, max_results=10)


class TestRepositoryReturnsSubFloorRows:
    @pytest.mark.asyncio
    async def test_search_returns_the_whole_neighbourhood(
        self, repo: MemoryRepository, spread_of_similarities: None
    ) -> None:
        rows = await repo.search(CHAT, QUERY_VECTOR, max_results=10)

        by_content = {row["content"]: float(row["similarity"]) for row in rows}
        assert set(by_content) == {"exact match", "near miss", "unrelated"}
        assert by_content["near miss"] == pytest.approx(0.6, abs=1e-6)
        assert by_content["unrelated"] == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_the_pre_r1_query_would_have_hidden_them(
        self, db_pool: asyncpg.Pool, spread_of_similarities: None
    ) -> None:
        """The control. Without it the test above proves only that the
        fixture has three rows, not that a floor would have removed two."""
        rows = await db_pool.fetch(_PRE_R1_SQL, CHAT, QUERY_VECTOR, 0.7, 10)

        assert [row["content"] for row in rows] == ["exact match"]

    @pytest.mark.asyncio
    async def test_ordering_and_limit_still_hold(
        self, repo: MemoryRepository, spread_of_similarities: None
    ) -> None:
        """Dropping the predicate must not disturb what LIMIT selects.

        The migration argument for this slice is that the injected set is
        unchanged: both orders read from the same similarity-descending
        sequence, so the top-k is the top-k either way.
        """
        rows = await repo.search(CHAT, QUERY_VECTOR, max_results=2)

        assert [row["content"] for row in rows] == ["exact match", "near miss"]


class TestServiceStillFilters:
    """The floor did not disappear — it moved. Asserted through the real DB,
    because a mocked repository cannot show that the two layers agree about
    which rows the SQL actually produced."""

    @pytest.mark.asyncio
    async def test_search_applies_the_floor(
        self, repo: MemoryRepository, spread_of_similarities: None
    ) -> None:
        results: list[dict[str, Any]] = await _service(repo, 0.7).search(
            CHAT, "irrelevant", query_embedding=QUERY_VECTOR
        )

        assert [r["content"] for r in results] == ["exact match"]

    @pytest.mark.asyncio
    async def test_search_unfiltered_does_not(
        self, repo: MemoryRepository, spread_of_similarities: None
    ) -> None:
        results = await _service(repo, 0.7).search_unfiltered(
            CHAT, "irrelevant", query_embedding=QUERY_VECTOR
        )

        assert [r["content"] for r in results] == ["exact match", "near miss", "unrelated"]

    @pytest.mark.asyncio
    async def test_a_blind_turn_has_a_neighbourhood_to_report(
        self, repo: MemoryRepository, spread_of_similarities: None
    ) -> None:
        """The case that motivated the slice: nothing injected, everything
        still visible to whoever wants to re-tune the floor later.

        A question about something the chat never discussed — modelled as a
        query vector orthogonal to every stored memory, rather than by
        raising the floor above 1.0, which no cosine similarity can fail.
        """
        off_topic = _vector((5, 1.0))
        service = _service(repo, 0.7)

        assert await service.search(CHAT, "q", query_embedding=off_topic) == []

        considered = await service.search_unfiltered(CHAT, "q", query_embedding=off_topic)
        assert len(considered) == 3
        assert all(r["similarity"] == pytest.approx(0.0, abs=1e-6) for r in considered)
