"""A rate limit must not park healthy chunks (found while backfilling, 2026-08-19).

`ChatChunkIndexer` parks a chunk after three failed embedding attempts, so one
permanently-unacceptable row cannot sit at the head of a FIFO queue for ever.
It told "bad row" from "provider down" by counting: charge a failure unless
*every* row in the batch failed.

A per-minute quota does not fail every row. It trips partway through the batch
-- the first N succeed, the rest are refused -- so `len(failed) < batch`, the
outage guard stays silent, and every healthy row behind the limit is charged.
Three passes later they are parked. Parking is in-process state and
`chat_chunks` is exempt from retention, so the rows stay `embedding IS NULL`
until someone restarts the bot; nothing raises, and the pass summary is gated
on `chunks or embedded` so a stalled indexer logs nothing at all.

Measured on a 2841-chunk corpus before the fix: **58 healthy chunks parked**
across two backfill runs, every one behind a rate limit.

The fix asks the provider instead of the arithmetic -- `RateLimitError` sets
`retriable`, and the router now carries it through its fallback rather than
raising a fresh error that defaults it to False.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import ChunkIndexerSettings
from src.services.ai.base import AIProviderError, EmbeddingResult, RateLimitError
from src.services.ai.router import _is_retriable
from src.services.rag.indexer import ChatChunkIndexer

_MAX_ATTEMPTS_GUESS = 3


def _indexer() -> ChatChunkIndexer:
    return ChatChunkIndexer(
        pool=MagicMock(),
        ai_router=AsyncMock(),
        chat_config=MagicMock(),
        config=ChunkIndexerSettings(),
    )


class TestRouterKeepsTheRetriableFlag:
    def test_a_rate_limited_last_error_makes_the_router_error_retriable(self) -> None:
        assert _is_retriable(RateLimitError("slow down", "gemini", retry_after=65.0)) is True

    def test_a_plain_provider_error_stays_non_retriable(self) -> None:
        assert _is_retriable(AIProviderError("bad input", "gemini")) is False

    def test_no_last_error_is_not_retriable(self) -> None:
        """No provider was even tried -- retrying changes nothing."""
        assert _is_retriable(None) is False

    async def test_the_router_propagates_it_through_the_fallback(self) -> None:
        """The end-to-end path that actually mattered: the router raised a
        *fresh* AIProviderError here, letting `retriable` default to False and
        throwing away the one fact a caller could act on."""
        from src.services.ai.router import AIRouter

        settings = MagicMock()
        settings.openai_api_key = None
        settings.gemini_api_key = "test-key"
        settings.grok_api_key = None
        settings.deepseek_api_key = None
        settings.ai.default_provider = "gemini"
        settings.ai.tasks = {}
        router = AIRouter(settings)

        provider = AsyncMock()
        provider.generate_embedding.side_effect = RateLimitError("429", "gemini", 65.0)
        router._get_provider_chain = lambda _task: ["gemini"]  # type: ignore[method-assign]
        router._get_provider = AsyncMock(return_value=provider)  # type: ignore[method-assign]

        with pytest.raises(AIProviderError) as exc_info:
            await router.generate_embedding("текст", chat_id=1)

        assert exc_info.value.retriable is True, (
            "a rate limit reached the caller as a permanent failure"
        )
        assert isinstance(exc_info.value.__cause__, RateLimitError), (
            "the original error must stay chained, or the traceback loses the cause"
        )


class TestParkingIgnoresRetriableFailures:
    async def test_the_accounting_is_never_handed_a_retriable_id(self) -> None:
        """Assert on what `_embed_pending` *hands* the accounting, not on what
        the accounting then does with it -- the defect lived in the handing.

        The first version of this test called `_account_for_failures([], batch=5)`
        with an empty list. That returns at the function's first line in every
        implementation, so it passed identically against the pre-fix code while
        its docstring claimed to reproduce "the exact production shape". The
        mutation run did not expose it either: the two neighbouring tests
        caught every mutation, and a vacuous third hid behind them. Reviewers
        found it. "The mutation was caught" says nothing about whether *each*
        test is load-bearing.
        """
        indexer = _indexer()
        rows = [{"id": i, "chat_id": -1, "content": f"чанк {i}"} for i in range(1, 6)]
        calls = {"n": 0}
        charged: list[list[int]] = []

        async def embed(_content: str, **_kw: object) -> EmbeddingResult:
            calls["n"] += 1
            if calls["n"] <= 2:
                return EmbeddingResult(
                    embedding=[0.1] * 768, model="m", provider="gemini", dimensions=768
                )
            raise RateLimitError("quota", "gemini", retry_after=65.0)

        indexer._ai_router.generate_embedding = embed  # type: ignore[method-assign]
        original_account = indexer._account_for_failures
        indexer._account_for_failures = (  # type: ignore[method-assign]
            lambda failed, *, batch: (
                charged.append(list(failed)),
                original_account(failed, batch=batch),
            )[1]
        )

        repo = AsyncMock()
        repo.get_pending_embeddings.return_value = rows
        import src.services.rag.indexer as indexer_module

        original = indexer_module.ChunkRepository
        indexer_module.ChunkRepository = lambda _pool: repo  # type: ignore[assignment,misc]
        try:
            await indexer._embed_pending()
        finally:
            indexer_module.ChunkRepository = original  # type: ignore[assignment]

        assert calls["n"] == 5, "the whole batch should have been attempted"
        assert charged == [[]], f"rate-limited ids were handed to the accounting: {charged}"

    async def test_a_retriable_failure_never_reaches_the_accounting(self) -> None:
        """Driven through the real `_embed_pending`, because the bug was in
        which ids get *handed* to the accounting, not in the accounting."""
        indexer = _indexer()
        rows = [{"id": i, "chat_id": -1, "content": f"чанк {i}"} for i in range(1, 6)]

        calls = {"n": 0}

        async def embed(_content: str, **_kw: object) -> EmbeddingResult:
            calls["n"] += 1
            if calls["n"] <= 2:
                return EmbeddingResult(
                    embedding=[0.1] * 768, model="m", provider="gemini", dimensions=768
                )
            raise RateLimitError("quota", "gemini", retry_after=65.0)

        indexer._ai_router.generate_embedding = embed  # type: ignore[method-assign]

        repo = AsyncMock()
        repo.get_pending_embeddings.return_value = rows
        import src.services.rag.indexer as indexer_module

        original = indexer_module.ChunkRepository
        indexer_module.ChunkRepository = lambda _pool: repo  # type: ignore[assignment,misc]
        try:
            # More passes than `_MAX_ATTEMPTS`: the pre-fix code parked on the
            # third, so a single pass would have proved nothing.
            total = sum([await indexer._embed_pending() for _ in range(_MAX_ATTEMPTS_GUESS + 2)])
        finally:
            indexer_module.ChunkRepository = original  # type: ignore[assignment]

        assert total == 2, "the two rows the quota let through should have been written"
        assert indexer._parked == set(), (
            f"rate-limited chunks were parked: {sorted(indexer._parked)}"
        )
        assert indexer._failures == {}

    async def test_a_whole_batch_failing_without_the_flag_is_still_an_outage(self) -> None:
        """The second net, and the only test that touches it.

        `retriable` is set by the providers we know about. A provider that goes
        down and reports it as a plain `AIProviderError` -- a new backend, an
        HTTP shape nobody has classified yet -- arrives here looking exactly
        like a bad row. Failing the *entire* batch is the one unambiguous
        signal left, so it is kept. The mutation run found this path uncovered:
        deleting the check broke no test until this one existed."""
        indexer = _indexer()
        rows = [{"id": i, "chat_id": -1, "content": f"чанк {i}"} for i in range(1, 6)]

        async def embed(_content: str, **_kw: object) -> EmbeddingResult:
            raise AIProviderError("backend exploded, unclassified", "newprovider")

        indexer._ai_router.generate_embedding = embed  # type: ignore[method-assign]

        repo = AsyncMock()
        repo.get_pending_embeddings.return_value = rows
        import src.services.rag.indexer as indexer_module

        original = indexer_module.ChunkRepository
        indexer_module.ChunkRepository = lambda _pool: repo  # type: ignore[assignment,misc]
        try:
            for _ in range(_MAX_ATTEMPTS_GUESS + 2):
                await indexer._embed_pending()
        finally:
            indexer_module.ChunkRepository = original  # type: ignore[assignment]

        assert indexer._parked == set(), (
            f"an unclassified whole-provider outage parked the backlog: {sorted(indexer._parked)}"
        )

    async def test_a_permanently_refused_row_is_still_parked(self) -> None:
        """The control the fix must not break: a non-retriable failure is what
        parking exists for, and removing it would let one bad row starve the
        FIFO queue behind it for ever."""
        indexer = _indexer()
        rows = [{"id": 7, "chat_id": -1, "content": "плохая строка"}]

        async def embed(_content: str, **_kw: object) -> EmbeddingResult:
            raise AIProviderError("input permanently refused", "gemini")

        indexer._ai_router.generate_embedding = embed  # type: ignore[method-assign]

        repo = AsyncMock()
        repo.get_pending_embeddings.return_value = rows
        import src.services.rag.indexer as indexer_module

        original = indexer_module.ChunkRepository
        indexer_module.ChunkRepository = lambda _pool: repo  # type: ignore[assignment,misc]
        try:
            for _ in range(_MAX_ATTEMPTS_GUESS):
                await indexer._embed_pending()
        finally:
            indexer_module.ChunkRepository = original  # type: ignore[assignment]

        assert 7 in indexer._parked, "a permanently-refused row must still park"

    async def test_a_wrong_width_vector_is_still_parked(self) -> None:
        """The other non-retriable kind: the column is `vector(768)` and a
        wrong-width result would raise on every retry."""
        indexer = _indexer()
        rows = [{"id": 9, "chat_id": -1, "content": "текст"}]

        async def embed(_content: str, **_kw: object) -> EmbeddingResult:
            return EmbeddingResult(
                embedding=[0.1] * 1536, model="m", provider="openai", dimensions=1536
            )

        indexer._ai_router.generate_embedding = embed  # type: ignore[method-assign]

        repo = AsyncMock()
        repo.get_pending_embeddings.return_value = rows
        import src.services.rag.indexer as indexer_module

        original = indexer_module.ChunkRepository
        indexer_module.ChunkRepository = lambda _pool: repo  # type: ignore[assignment,misc]
        try:
            for _ in range(_MAX_ATTEMPTS_GUESS):
                await indexer._embed_pending()
        finally:
            indexer_module.ChunkRepository = original  # type: ignore[assignment]

        assert 9 in indexer._parked


class TestTheBackfillWorkerHasTheSameFix:
    """`EmbeddingBackfillWorker` is the same mechanism over `chat_memory` and
    `chat_facts` -- the stores the bot reads *today* -- and it had the defect in
    a worse form: it charged every failure unconditionally and had no
    whole-batch outage guard at all. A reviewer found it; the first fix had
    been applied only to the chunk indexer, which nothing reads yet.
    """

    def _worker(self) -> object:
        from src.config import EmbeddingBackfillSettings
        from src.services.rag.backfill import EmbeddingBackfillWorker

        return EmbeddingBackfillWorker(
            pool=MagicMock(),
            ai_router=AsyncMock(),
            config=EmbeddingBackfillSettings(),
        )

    async def _run(self, worker: object, error: Exception, passes: int) -> None:
        from src.services.rag.backfill import _Source

        async def embed(_text: str, **_kw: object) -> EmbeddingResult:
            raise error

        worker._ai_router.generate_embedding = embed  # type: ignore[attr-defined]
        source = _Source(
            name="chat_memory",
            text_field="content",
            fetch=AsyncMock(return_value=[]),
            update=AsyncMock(),
        )
        rows = [{"id": 1, "chat_id": -1, "content": "текст"}]
        for _ in range(passes):
            await worker._process(source, rows)  # type: ignore[attr-defined]

    async def test_a_rate_limit_never_parks_a_memory_row(self) -> None:
        worker = self._worker()
        await self._run(worker, RateLimitError("quota", "gemini", 65.0), passes=5)
        assert worker._parked == set(), (  # type: ignore[attr-defined]
            f"a rate limit parked a healthy row of the store the bot reads: {worker._parked}"  # type: ignore[attr-defined]
        )

    async def test_a_permanent_provider_refusal_still_parks(self) -> None:
        """The control: the starvation guard must survive the fix."""
        worker = self._worker()
        await self._run(worker, AIProviderError("refused for ever", "gemini"), passes=3)
        assert worker._parked, "a permanently-refused row must still park"  # type: ignore[attr-defined]

    async def test_a_non_provider_exception_still_parks(self) -> None:
        """Anything that is not an `AIProviderError` is this row's problem
        until proven otherwise -- narrowing the catch must not have widened
        the exemption."""
        worker = self._worker()
        await self._run(worker, ValueError("malformed response body"), passes=3)
        assert worker._parked  # type: ignore[attr-defined]


class TestAStalledPassIsVisible:
    """A pass that had rows and embedded none used to log nothing at all: the
    summary is gated on progress, and with the fix nothing gets parked either
    (correctly -- the rows are healthy). Sustained quota exhaustion therefore
    looked exactly like a caught-up, idle indexer."""

    async def _stalled_pass(self) -> ChatChunkIndexer:
        indexer = _indexer()

        async def embed(_content: str, **_kw: object) -> EmbeddingResult:
            raise RateLimitError("quota", "gemini", retry_after=65.0)

        indexer._ai_router.generate_embedding = embed  # type: ignore[method-assign]
        repo = AsyncMock()
        repo.get_pending_embeddings.return_value = [{"id": 1, "chat_id": -1, "content": "чанк"}]
        import src.services.rag.indexer as indexer_module

        original = indexer_module.ChunkRepository
        indexer_module.ChunkRepository = lambda _pool: repo  # type: ignore[assignment,misc]
        try:
            await indexer._embed_pending()
        finally:
            indexer_module.ChunkRepository = original  # type: ignore[assignment]
        return indexer

    async def test_a_stalled_pass_is_counted(self) -> None:
        indexer = await self._stalled_pass()
        assert indexer._stalled_passes == 1

    async def test_progress_resets_the_counter(self) -> None:
        """Otherwise the number stops meaning "consecutive" and a single old
        blip makes every later pass look stuck."""
        indexer = await self._stalled_pass()
        assert indexer._stalled_passes == 1

        async def embed(_content: str, **_kw: object) -> EmbeddingResult:
            return EmbeddingResult(
                embedding=[0.1] * 768, model="m", provider="gemini", dimensions=768
            )

        indexer._ai_router.generate_embedding = embed  # type: ignore[method-assign]
        repo = AsyncMock()
        repo.get_pending_embeddings.return_value = [{"id": 2, "chat_id": -1, "content": "чанк"}]
        import src.services.rag.indexer as indexer_module

        original = indexer_module.ChunkRepository
        indexer_module.ChunkRepository = lambda _pool: repo  # type: ignore[assignment,misc]
        try:
            await indexer._embed_pending()
        finally:
            indexer_module.ChunkRepository = original  # type: ignore[assignment]

        assert indexer._stalled_passes == 0

    async def test_an_empty_queue_is_not_a_stall(self) -> None:
        """Nothing pending is the healthy steady state, not a problem."""
        indexer = _indexer()
        repo = AsyncMock()
        repo.get_pending_embeddings.return_value = []
        import src.services.rag.indexer as indexer_module

        original = indexer_module.ChunkRepository
        indexer_module.ChunkRepository = lambda _pool: repo  # type: ignore[assignment,misc]
        try:
            await indexer._embed_pending()
        finally:
            indexer_module.ChunkRepository = original  # type: ignore[assignment]

        assert indexer._stalled_passes == 0
