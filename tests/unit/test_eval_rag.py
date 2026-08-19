"""Tests for scripts/eval_rag.py (S3-2: eval harness calls the real search path)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts.eval_rag import CaseResult, _load_all_cases, _parse_args, run_eval
from scripts.eval_schema import EvalCase, EvalCaseFileError
from src.services.ai.base import AIProviderError, EmbeddingResult
from src.services.ai.router import AIRouter
from src.services.rag.memory import RAGMemoryService

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "eval" / "cases.json"


def _make_case(**overrides: object) -> EvalCase:
    base: dict[str, object] = {
        "chat_id": -1009999990001,
        "question": "Where do we meet on Friday?",
        "asked_at": datetime(2026, 5, 10, 18, 0, 0, tzinfo=UTC),
        "expected_message_id_ranges": [{"start": 10, "end": 12}],
        "stratum": "found",
        "note": "The answer is in a single message.",
    }
    base.update(overrides)
    return EvalCase.model_validate(base)


def _make_embedding_result(vec: list[float] | None = None) -> EmbeddingResult:
    return EmbeddingResult(
        embedding=vec or [0.1] * 768,
        model="gemini-embedding-001",
        provider="gemini",
        dimensions=768,
    )


# The harness is handed trigger words because production strips them before
# embedding (R0/TD-092); a harness that skipped that step would measure a
# retrieval path the bot no longer runs.
TRIGGERS = ("бот", "bot")


class TestRunEval:
    """S3-2: ``run_eval()`` embeds via the real AIRouter path and searches
    via the real ``RAGMemoryService.search()`` -- no reimplemented SQL."""

    @pytest.mark.asyncio
    async def test_embeds_then_searches_with_before_bound(self) -> None:
        case = _make_case()
        ai_router = AsyncMock(spec=AIRouter)
        embedding_result = _make_embedding_result([0.2] * 768)
        ai_router.generate_embedding.return_value = embedding_result
        service = AsyncMock(spec=RAGMemoryService)
        service.search.return_value = [
            {
                "id": 1,
                "content": "we meet at 5pm",
                "similarity": 0.91,
                "metadata": None,
                "created_at": datetime(2026, 5, 9, tzinfo=UTC),
                "source_message_id": 11,
            }
        ]

        results = await run_eval(
            [case], service=service, ai_router=ai_router, trigger_words=TRIGGERS
        )

        assert len(results) == 1
        assert results[0].case is case
        assert results[0].hits == service.search.return_value
        assert results[0].embedding_error is None

        ai_router.generate_embedding.assert_awaited_once()
        assert ai_router.generate_embedding.call_args.kwargs["chat_id"] == case.chat_id

        service.search.assert_awaited_once()
        search_kwargs = service.search.call_args.kwargs
        # The harness must reuse the already-computed embedding (S2-4
        # pattern), not let search() embed the query a second time.
        assert search_kwargs["query_embedding"] == embedding_result.embedding
        # And it must pass the case's asked_at as the time bound (S3-3) --
        # omitting this is exactly the self-retrieval bug S3-3 exists to
        # prevent.
        assert search_kwargs["before"] == case.asked_at

    @pytest.mark.asyncio
    async def test_embedding_failure_is_reported_and_search_is_skipped(self) -> None:
        """All providers exhausted -> counted as embedding_error, not a
        silent empty-hit result -- conflating the two would let a provider
        outage read as a correct answer-absent case (S3-5)."""
        case = _make_case()
        ai_router = AsyncMock(spec=AIRouter)
        ai_router.generate_embedding.side_effect = AIProviderError(
            "all providers failed", provider="router"
        )
        service = AsyncMock(spec=RAGMemoryService)

        results = await run_eval(
            [case], service=service, ai_router=ai_router, trigger_words=TRIGGERS
        )

        assert len(results) == 1
        assert results[0].hits == []
        assert results[0].embedding_error is not None
        assert "all providers failed" in results[0].embedding_error
        service.search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multiple_cases_are_replayed_independently(self) -> None:
        case_a = _make_case(chat_id=-1)
        case_b = _make_case(chat_id=-2, stratum="answer-absent", expected_message_id_ranges=[])
        ai_router = AsyncMock(spec=AIRouter)
        ai_router.generate_embedding.return_value = _make_embedding_result()
        service = AsyncMock(spec=RAGMemoryService)
        service.search.return_value = []

        results = await run_eval(
            [case_a, case_b], service=service, ai_router=ai_router, trigger_words=TRIGGERS
        )

        assert [r.case.chat_id for r in results] == [-1, -2]
        assert service.search.await_count == 2


class TestCaseResultDefaults:
    def test_defaults_are_empty_hits_no_error(self) -> None:
        case = _make_case()
        result = CaseResult(case=case)

        assert result.hits == []
        assert result.embedding_error is None


class TestLoadAllCases:
    def test_loads_and_concatenates_multiple_files(self, tmp_path: Path) -> None:
        cases = _load_all_cases([TEMPLATE_PATH])
        assert len(cases) > 0
        assert all(isinstance(c, EvalCase) for c in cases)

    def test_missing_file_raises_eval_case_file_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.json"
        with pytest.raises(EvalCaseFileError):
            _load_all_cases([missing])


class TestParseArgs:
    def test_dsn_is_required(self) -> None:
        with pytest.raises(SystemExit):
            _parse_args([])

    def test_dsn_and_defaults(self) -> None:
        args = _parse_args(["postgresql://r:r@127.0.0.1:55434/companion"])
        assert args.dsn == "postgresql://r:r@127.0.0.1:55434/companion"
        assert args.cases is None
        assert args.min_similarity is None
        assert args.max_results is None

    def test_repeated_cases_flag_accumulates(self) -> None:
        args = _parse_args(
            [
                "postgresql://r:r@127.0.0.1:55434/companion",
                "--cases",
                "a.json",
                "--cases",
                "b.json",
            ]
        )
        assert args.cases == [Path("a.json"), Path("b.json")]

    def test_min_similarity_and_max_results_overrides(self) -> None:
        args = _parse_args(
            [
                "postgresql://r:r@127.0.0.1:55434/companion",
                "--min-similarity",
                "0.5",
                "--max-results",
                "3",
            ]
        )
        assert args.min_similarity == 0.5
        assert args.max_results == 3


class TestRunEvalQueryHygiene:
    """R0/TD-092 — the harness must embed what the pipeline embeds.

    Auto-harvested cases take their question from ``chat_messages.content``
    verbatim, so they carry the leading address the bot now strips. If this
    step were missing here, every recorded baseline would describe a path
    production stopped using, and nothing in the metrics would say so.
    """

    @pytest.mark.asyncio
    async def test_leading_address_is_stripped_before_embedding(self) -> None:
        case = _make_case(question="бот, где мы встречаемся?")
        ai_router = AsyncMock(spec=AIRouter)
        ai_router.generate_embedding.return_value = _make_embedding_result()
        service = AsyncMock(spec=RAGMemoryService)
        service.search.return_value = []

        await run_eval([case], service=service, ai_router=ai_router, trigger_words=TRIGGERS)

        assert ai_router.generate_embedding.call_args.args[0] == "где мы встречаемся?"
        # …and the same text reaches search(), so a future change that lets
        # search() re-embed cannot silently diverge from the embedding above.
        assert service.search.call_args.args[1] == "где мы встречаемся?"

    @pytest.mark.asyncio
    async def test_case_question_itself_is_not_mutated(self) -> None:
        """Reports quote the question as asked; only the query is cleaned."""
        case = _make_case(question="бот, где мы встречаемся?")
        ai_router = AsyncMock(spec=AIRouter)
        ai_router.generate_embedding.return_value = _make_embedding_result()
        service = AsyncMock(spec=RAGMemoryService)
        service.search.return_value = []

        results = await run_eval(
            [case], service=service, ai_router=ai_router, trigger_words=TRIGGERS
        )

        assert results[0].case.question == "бот, где мы встречаемся?"


class TestPrintResultsSurvivesUnscoredHits:
    """`_print_results` runs BEFORE `compute_metrics`, so a crash here discards
    the whole run -- after every provider call for the case set is paid for,
    and before a single metric prints.

    `--backend chunks` makes unscored hits routine: `ChunkRepository.search`
    returns `similarity = NULL` for a chunk the lexical leg found while its
    embedding was still pending, and for every row when the query itself could
    not be embedded. `max()` over a list holding one None raises, and
    `format(None, '.3f')` raises even for a single hit.

    The identical guard was added to `compute_metrics` in the same change and
    this sibling one function away was missed; two independent reviewers found
    it, and nothing in the suite touched `_print_results` at all.
    """

    def _result(self, *sims: float | None) -> CaseResult:
        return CaseResult(
            case=_make_case(),
            hits=[{"id": i, "content": "x", "similarity": s} for i, s in enumerate(sims)],
        )

    def test_a_single_unscored_hit_does_not_crash(self, capsys: pytest.CaptureFixture) -> None:
        from scripts.eval_rag import _print_results

        _print_results([self._result(None)])

        out = capsys.readouterr().out
        assert "1 hit(s)" in out
        assert "best_sim=n/a" in out
        assert "1 unscored" in out

    def test_a_mix_reports_the_best_scored_hit(self, capsys: pytest.CaptureFixture) -> None:
        from scripts.eval_rag import _print_results

        _print_results([self._result(None, 0.42, 0.61)])

        out = capsys.readouterr().out
        assert "best_sim=0.610" in out
        assert "1 unscored" in out

    def test_all_scored_reads_exactly_as_before(self, capsys: pytest.CaptureFixture) -> None:
        """The control: the common case must not have grown noise."""
        from scripts.eval_rag import _print_results

        _print_results([self._result(0.42, 0.61)])

        out = capsys.readouterr().out
        assert "best_sim=0.610" in out
        assert "unscored" not in out

    def test_a_blind_case_no_longer_reports_a_fake_zero_similarity(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Behaviour change, stated rather than buried: a case that retrieved
        nothing used to print `best_sim=0.000` because `max(..., default=0.0)`
        supplied a number where there was none. 0.0 is a real cosine value, so
        that line was indistinguishable from a genuine worst-possible match."""
        from scripts.eval_rag import _print_results

        _print_results([CaseResult(case=_make_case(), hits=[])])

        out = capsys.readouterr().out
        assert "0 hit(s)" in out
        assert "best_sim=n/a" in out
        assert "0.000" not in out
