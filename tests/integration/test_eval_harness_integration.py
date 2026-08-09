"""Integration tests for the RAG eval harness (S3-8).

``docs/plans/rag-s3-eval-harness.md`` S3-8 is explicit that the tool which
will gate the S5 retrieval cutover must itself be checked, with two
requirements a pure-unit-test suite cannot cover (those live in
``tests/unit/test_eval_schema.py`` / ``test_eval_metrics.py`` /
``test_eval_rag.py``, which mock the search path entirely):

1. **S3-3's time bound is a real ``WHERE`` predicate, not a postfilter** --
   ``TestBeforeFilterAppliedInWhereNotPostFilter`` below. The item's acceptance
   text calls out the exact failure mode a naive control would miss: "кейс,
   где будущая самоссылка ранжируется #1 ДО фильтра (иначе тест проходит и на
   баге постфильтрации)" -- a case where the future self-reference (the
   memory of the question itself) ranks #1 *before* the time filter is
   applied. With ``max_results=1``, a postfilter implementation (apply the
   time cutoff in Python after ``LIMIT``) would already have thrown away the
   real answer by the time it discards the self-reference, and returns
   nothing -- turning a "should find the answer" case into a "found nothing"
   case for a reason that has nothing to do with retrieval quality (S3-3's
   own worry, ``docs/plans/rag-s3-eval-harness.md`` lines 65-68). Mirrors the
   positive/negative-control pattern of
   ``test_memory_repository_chat_scoping.py`` (S2-7a): the negative control
   duplicates the *naive* postfilter shape with real fixture data and asserts
   it DOES reproduce the miss -- proving the fixture is discriminating, not
   vacuous, before trusting the positive test next to it.

2. **The harness's metric must actually fall when retrieval is broken** --
   ``TestDegradationControl`` below. Runs the real
   ``scripts.eval_rag.run_eval()`` -> ``scripts.eval_metrics.compute_metrics()``
   pipeline end to end against a real pgvector-backed
   ``MemoryRepository``/``RAGMemoryService``, once with a healthy query
   vector and once with search deliberately degraded (an unrelated query
   vector, and separately an empty index) -- exactly the S3-8 acceptance
   text's "искусственно ухудшенное поиска ... заведомо неверный вектор
   запроса или пустой индекс". A harness that prints a decent number no
   matter the input "looks exactly like a healthy system" (S3-8's own
   framing) and would silently fail to gate S5.

Uses the pgvector testcontainer from ``tests/integration/conftest.py`` (real
migrated schema, real ``embedding <=> query`` cosine search) -- per the item's
explicit instruction to reuse it rather than mock the database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio

from scripts.eval_metrics import compute_metrics
from scripts.eval_rag import run_eval
from scripts.eval_schema import EvalCase
from src.database.repositories.memory import MemoryRepository
from src.services.ai.base import EmbeddingResult
from src.services.ai.router import AIRouter
from src.services.rag.memory import RAGMemoryService

_EMBED_DIM = 768


def _one_hot(index: int, *, dim: int = _EMBED_DIM) -> list[float]:
    """Deterministic unit vector, single 1.0 at ``index``. See
    ``test_memory_repository_chat_scoping.py`` for the same helper -- gives
    fully deterministic pgvector ranking without depending on real embedding
    semantics."""
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


def _mostly_aligned(index: int, *, dim: int = _EMBED_DIM) -> list[float]:
    """A vector strongly, but not perfectly, aligned with ``_one_hot(index)``.

    Cosine similarity against ``_one_hot(index)`` works out to ~0.970 --
    high enough to clear any realistic threshold, but strictly LESS than the
    self-referencing row's exact 1.0 match. This is what lets the self-
    reference legitimately outrank the real answer in an unfiltered
    similarity ranking, which is the whole point of the case: the ordering
    bug can only be observed when the wrong row wins the popularity contest
    before time-filtering is supposed to remove it.
    """
    vec = [0.0] * dim
    vec[index] = 0.8
    vec[(index + 1) % dim] = 0.2
    return vec


def _make_embedding_result(vec: list[float]) -> EmbeddingResult:
    return EmbeddingResult(embedding=vec, model="test-embed", provider="test", dimensions=len(vec))


@pytest_asyncio.fixture
async def repo(db_pool: asyncpg.Pool) -> MemoryRepository:
    return MemoryRepository(db_pool)


class TestBeforeFilterAppliedInWhereNotPostFilter:
    """S3-3 regression guard: the ``before`` bound must exclude rows in SQL
    ``WHERE`` (ahead of ``LIMIT``), not in a Python postfilter after it."""

    CHAT_ID = -930301
    ASKED_AT = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    QUERY_VEC = _one_hot(0)
    # Exact match -> cosine similarity 1.0 -- ranks #1 in an unfiltered query.
    SELF_REF_VEC = _one_hot(0)
    # Strong but imperfect match -> cosine similarity ~0.970 -- ranks #2
    # unless the future row is excluded from consideration entirely.
    ANSWER_VEC = _mostly_aligned(0)

    @pytest_asyncio.fixture
    async def fixture_rows(self, db_pool: asyncpg.Pool) -> dict[str, int]:
        """One real (past) answer and one future self-reference memory of
        the question itself -- the shape ``q5_replay.py`` and S3-3 describe:
        without a time bound, "X asked: ..." out-ranks the real answer."""
        await db_pool.execute("DELETE FROM chat_memory WHERE chat_id = $1", self.CHAT_ID)
        answer_id = await db_pool.fetchval(
            """
            INSERT INTO chat_memory (chat_id, content, embedding, source_message_id, created_at)
            VALUES ($1, 'The pizza place is Dobraya Pizza', $2, 141, $3::timestamptz)
            RETURNING id
            """,
            self.CHAT_ID,
            self.ANSWER_VEC,
            self.ASKED_AT - timedelta(days=1),
        )
        self_ref_id = await db_pool.fetchval(
            """
            INSERT INTO chat_memory (chat_id, content, embedding, source_message_id, created_at)
            VALUES ($1, 'User asked: which pizza place did we pick?', $2, 999, $3::timestamptz)
            RETURNING id
            """,
            self.CHAT_ID,
            self.SELF_REF_VEC,
            self.ASKED_AT + timedelta(minutes=1),
        )
        return {"answer": answer_id, "self_ref": self_ref_id}

    @pytest.mark.asyncio
    async def test_real_search_excludes_future_self_reference_and_finds_the_answer(
        self, repo: MemoryRepository, fixture_rows: dict[str, int]
    ) -> None:
        """Positive assertion: with k=1, the real ``MemoryRepository.search()``
        (WHERE-based ``before``) must return the past answer, not the
        higher-similarity future self-reference."""
        results = await repo.search(
            self.CHAT_ID,
            self.QUERY_VEC,
            min_similarity=0.5,
            max_results=1,
            before=self.ASKED_AT,
        )

        assert len(results) == 1
        assert results[0]["id"] == fixture_rows["answer"]
        assert results[0]["source_message_id"] == 141

    @pytest.mark.asyncio
    async def test_naive_postfilter_on_the_same_fixture_would_miss_the_answer(
        self, db_pool: asyncpg.Pool, fixture_rows: dict[str, int]
    ) -> None:
        """Mandatory negative control (mirrors
        ``test_memory_repository_chat_scoping.py``'s S2-7a pattern): the
        positive test above only proves something if THIS fixture data would
        actually break under the bug S3-3 fixed. Deliberately reimplements
        the pre-S3-3 shape -- rank by similarity, ``LIMIT`` first, THEN drop
        rows past ``asked_at`` in Python -- against the same rows and asserts
        it reproduces the miss. If this control ever stopped failing, the
        positive test's case would no longer be discriminating (e.g. if
        ``ANSWER_VEC``/``SELF_REF_VEC`` stopped colliding under real
        similarity math), and that should be caught here, not assumed.
        """
        del fixture_rows  # only needed to ensure the rows exist
        unfiltered_top_k = await db_pool.fetch(
            """
            SELECT id, source_message_id, created_at,
                   1 - (embedding <=> $2) AS similarity
            FROM chat_memory
            WHERE chat_id = $1
            ORDER BY embedding <=> $2 ASC
            LIMIT 1
            """,
            self.CHAT_ID,
            self.QUERY_VEC,
        )

        # LIMIT already picked the self-reference (perfect similarity) --
        # the real answer never made it into the top-k in the first place.
        assert [row["source_message_id"] for row in unfiltered_top_k] == [999]

        postfiltered = [row for row in unfiltered_top_k if row["created_at"] < self.ASKED_AT]

        assert postfiltered == [], (
            "negative control did not reproduce the postfilter miss -- the "
            "fixture's self-reference/answer similarity no longer collides, "
            "so the positive test above would be vacuous"
        )


class TestDegradationControl:
    """S3-8 acceptance text: 'при искусственно ухудшенном поиске ... метрика
    обязана упасть' -- runs the REAL ``run_eval()`` -> ``compute_metrics()``
    pipeline, not a mocked one, so the arithmetic is exercised against a real
    search response shape.
    """

    CHAT_ID = -930302
    EMPTY_CHAT_ID = -930303
    ASKED_AT = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    ANSWER_VEC = _one_hot(1)
    UNRELATED_QUERY_VEC = _one_hot(500)  # orthogonal to ANSWER_VEC -- similarity 0.0

    @pytest_asyncio.fixture
    async def stored_answer(self, db_pool: asyncpg.Pool) -> int:
        await db_pool.execute("DELETE FROM chat_memory WHERE chat_id = $1", self.CHAT_ID)
        new_id = await db_pool.fetchval(
            """
            INSERT INTO chat_memory (chat_id, content, embedding, source_message_id, created_at)
            VALUES ($1, 'We meet at the station', $2, 305, $3::timestamptz)
            RETURNING id
            """,
            self.CHAT_ID,
            self.ANSWER_VEC,
            self.ASKED_AT - timedelta(days=1),
        )
        return int(new_id)

    def _eval_case(self, *, chat_id: int) -> EvalCase:
        return EvalCase.model_validate(
            {
                "chat_id": chat_id,
                "question": "Where do we meet?",
                "asked_at": self.ASKED_AT.isoformat(),
                "expected_message_id_ranges": [{"start": 305, "end": 305}],
                "stratum": "found",
                "note": "integration fixture",
            }
        )

    async def _run_recall(
        self, repo: MemoryRepository, *, chat_id: int, query_vec: list[float]
    ) -> float:
        ai_router = AsyncMock(spec=AIRouter)
        ai_router.generate_embedding.return_value = _make_embedding_result(query_vec)
        service = RAGMemoryService(
            memory_repo=repo, ai_router=ai_router, min_similarity=0.5, max_results=5
        )

        results = await run_eval(
            [self._eval_case(chat_id=chat_id)], service=service, ai_router=ai_router
        )
        metrics = compute_metrics(results, k=service.max_results)
        return metrics.recall_at_k

    @pytest.mark.asyncio
    async def test_healthy_search_yields_full_recall(
        self, repo: MemoryRepository, stored_answer: int
    ) -> None:
        """Baseline: with a query vector that actually matches the stored
        answer, recall@k must be 1.0 -- the reference point the degraded
        runs below are compared against."""
        del stored_answer
        recall = await self._run_recall(repo, chat_id=self.CHAT_ID, query_vec=self.ANSWER_VEC)
        assert recall == 1.0

    @pytest.mark.asyncio
    async def test_wrong_query_vector_drops_recall_to_zero(
        self, repo: MemoryRepository, stored_answer: int
    ) -> None:
        """Degradation control #1: same stored data, but the harness embeds
        a query that has nothing to do with it (orthogonal vector -- as if
        the embedding provider returned garbage). recall@k MUST fall -- a
        harness that still reports a healthy number here would be
        indistinguishable from a correctly working one."""
        del stored_answer
        recall = await self._run_recall(
            repo, chat_id=self.CHAT_ID, query_vec=self.UNRELATED_QUERY_VEC
        )
        assert recall == 0.0

    @pytest.mark.asyncio
    async def test_empty_index_drops_recall_to_zero(self, repo: MemoryRepository) -> None:
        """Degradation control #2: a chat with NO memories at all (empty
        index) -- the other half of the item's acceptance text ('пустой
        индекс'). Uses ``EMPTY_CHAT_ID``, deliberately never seeded by any
        fixture in this file, so nothing exists for the query to match."""
        recall = await self._run_recall(repo, chat_id=self.EMPTY_CHAT_ID, query_vec=self.ANSWER_VEC)
        assert recall == 0.0
