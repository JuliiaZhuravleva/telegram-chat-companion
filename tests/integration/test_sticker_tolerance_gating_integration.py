"""
Integration tests: sticker explicitness/tolerance gating (ADR-0008 Decision
2/3/6) against real Postgres+pgvector.

``tests/unit/test_sticker_repository.py``'s ``test_search_by_embedding_gates_on_tolerance``
only asserts the SQL *text* ("explicitness_score <=" appears, 0.42 is a bound
param) against a mocked pool -- it cannot catch a wrong operator, a flipped
comparison direction, or a predicate that silently doesn't filter anything
once real rows are involved. This file drives the real
``StickerRepository.search_by_embedding`` query end to end, per ADR-0008's
own Implementation notes for D-4:

- "NULL fail-closed ... integration-level, against a real Postgres row, not
  just the Python helper."
- "End-to-end gating: a sticker scored 0.6 is excluded from a
  tolerance_level = 0.5 chat's candidates and included in a
  tolerance_level = 1.0 chat's candidates, via the real search_by_embedding
  SQL path (Decision 6), not a mocked repository."
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.stickers import StickerRepository

_QUERY_EMBEDDING = [0.3] * 768


@pytest_asyncio.fixture
async def repo(db_conn: asyncpg.Connection) -> StickerRepository:
    return StickerRepository(db_conn)  # type: ignore[arg-type]


async def _seed_scored_sticker(
    repo: StickerRepository,
    *,
    file_unique_id: str,
    explicitness_score: float | None,
) -> None:
    """A fully analyzed, embedded candidate row -- everything
    search_by_embedding's other predicates already require (real
    description, not failed, real embedding) -- differing only in
    ``explicitness_score``, so a failure isolates to the tolerance predicate
    itself."""
    await repo.save_sticker(
        file_unique_id=file_unique_id,
        file_id=f"file-{file_unique_id}",
        visual_description="A candidate sticker",
        emotion="neutral",
        explicitness_score=explicitness_score,
    )
    await repo.update_embedding(file_unique_id, _QUERY_EMBEDDING)


class TestNullExplicitnessFailClosed:
    @pytest.mark.asyncio
    async def test_unscored_sticker_excluded_even_at_anarchy_tolerance(
        self, repo: StickerRepository
    ) -> None:
        """Decision 3: explicitness_score IS NULL must never surface as a
        candidate, including at tolerance_level = 1.0 -- a NULL is not "gate
        passed for free," it is "no data to gate on at all"."""
        await _seed_scored_sticker(repo, file_unique_id="tol-null-001", explicitness_score=None)

        results = await repo.search_by_embedding(
            _QUERY_EMBEDDING, limit=5, min_similarity=0.5, tolerance_level=1.0
        )

        assert "tol-null-001" not in {r["file_unique_id"] for r in results}


class TestEndToEndGatingAgainstRealSql:
    @pytest.mark.asyncio
    async def test_score_above_ceiling_excluded_below_or_equal_included(
        self, repo: StickerRepository
    ) -> None:
        """The plan's own worked example: a sticker scored 0.6 is excluded
        from a tolerance_level=0.5 chat and included in a tolerance_level=1.0
        chat -- through the real SQL predicate, not a mocked repository."""
        await _seed_scored_sticker(repo, file_unique_id="tol-060-002", explicitness_score=0.6)

        strict_results = await repo.search_by_embedding(
            _QUERY_EMBEDDING, limit=5, min_similarity=0.5, tolerance_level=0.5
        )
        assert "tol-060-002" not in {r["file_unique_id"] for r in strict_results}

        anarchy_results = await repo.search_by_embedding(
            _QUERY_EMBEDDING, limit=5, min_similarity=0.5, tolerance_level=1.0
        )
        assert "tol-060-002" in {r["file_unique_id"] for r in anarchy_results}

    @pytest.mark.asyncio
    async def test_score_equal_to_ceiling_is_included_boundary(
        self, repo: StickerRepository
    ) -> None:
        """Decision 2: the SQL predicate is a ceiling (<=), not a strict
        floor/ceiling (<) -- pins the same boundary
        test_sticker_tolerance.py's unit table already checks for the Python
        helper, but here against the real ``<=`` operator in the query."""
        await _seed_scored_sticker(repo, file_unique_id="tol-boundary-003", explicitness_score=0.5)

        results = await repo.search_by_embedding(
            _QUERY_EMBEDDING, limit=5, min_similarity=0.5, tolerance_level=0.5
        )

        assert "tol-boundary-003" in {r["file_unique_id"] for r in results}
