#!/usr/bin/env python
"""RAG eval harness (S3-2): replay eval cases through the REAL search path.

Calls ``RAGMemoryService.search()`` (``src/services/rag/memory.py``) --
the same entry point ``TextProcessingPipeline`` uses -- rather than
reimplementing the SQL. ``internal/analysis/q5_replay.py`` is the working
proto-harness this supersedes, and it rewrites the search SQL inline
(``q5_replay.py:100-124``): fine for one-off analysis, wrong for a tool
that will gate the S5 retrieval cutover, because S5's hybrid RRF switch
would then need a second reimplementation here, and the eval would start
measuring the reimplementation instead of prod. Side benefit of going
through the real service: S5's ``rag_backend`` flag flips retrieval here
exactly like it does in prod, with no extra harness work.

Query embeddings go through the real provider path (``AIRouter(settings)``,
decided [Q2]) -- not a raw HTTP call the way ``q5_replay.py:38-54`` does --
so the eval measures the same embedding call shape prod makes (model, 768
dims, no ``task_type``). No Dishka: this is a one-off script with no
request to scope to, same rationale as ``scripts/backfill_explicitness.py``.

Usage::

    python -m scripts.eval_rag <seed-dsn> [--cases PATH ...]
        [--min-similarity F] [--max-results N]

``<seed-dsn>`` is a REQUIRED positional argument with NO default (decided
[Q1]): the harness must never be able to default onto a live database.
The throwaway ``rag-analysis-seed`` container (2 918 ``chat_memory`` rows,
per the roadmap analysis) is expected at
``postgresql://r:r@127.0.0.1:55434/companion`` -- pass that DSN explicitly,
it is deliberately not hardcoded here.

This module produces per-case raw retrieval results (``CaseResult``);
recall@k / MRR / blind-rate / best-sim-percentile arithmetic lives in
``scripts/eval_metrics.py`` (S3-4), built on ``run_eval()``'s stable
``list[CaseResult]`` contract without another pass over the search path.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from scripts.eval_metrics import compute_metrics, format_metrics
from scripts.eval_schema import EvalCase, EvalCaseFileError, load_cases
from src.config import Settings
from src.database.connection import close_pool, create_pool
from src.database.repositories.memory import MemoryRepository
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter
from src.services.rag.memory import RAGMemoryService

logger = structlog.get_logger(__name__)

DEFAULT_CASES_PATH = Path("tests/fixtures/eval/cases.json")

# A small pool is plenty for a sequential, one-case-at-a-time script run
# against a throwaway seed container -- no reason to open prod-sized
# min_size/max_size (src/database/connection.py's defaults) against it.
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 5


@dataclass(frozen=True)
class CaseResult:
    """One case's real search-path retrieval result.

    ``hits`` is exactly what ``RAGMemoryService.search()`` returns
    (id, content, similarity, metadata, created_at, source_message_id) --
    unmodified, so S3-4's recall@k / MRR arithmetic operates on the same
    shape prod's pipeline sees, not a harness-specific reshaping.

    ``embedding_error``, if set, means the query embedding call itself
    failed (all providers exhausted) -- distinct from a healthy embedding
    that simply matched nothing, which S3-5's ``answer-absent`` stratum
    treats as *correct* behavior. Conflating the two would let a provider
    outage masquerade as a good negative-control result.
    """

    case: EvalCase
    hits: list[dict[str, Any]] = field(default_factory=list)
    embedding_error: str | None = None


async def run_eval(
    cases: list[EvalCase],
    *,
    service: RAGMemoryService,
    ai_router: AIRouter,
) -> list[CaseResult]:
    """Replay every case through the real embed -> search path, in order.

    Sequential on purpose (mirrors ``q5_replay.py``'s pacing intent): this
    hits a real embeddings provider per case, and the golden set (S3b) is
    expected to stay in the tens of cases, not thousands -- no need for
    concurrency here, and it keeps provider rate limits out of scope.
    """
    results: list[CaseResult] = []
    for case in cases:
        try:
            embedding_result = await ai_router.generate_embedding(
                case.question, chat_id=case.chat_id
            )
        except AIProviderError as exc:
            logger.warning(
                "eval_rag: query embedding failed, case counted as embedding_error",
                chat_id=case.chat_id,
                question=case.question[:80],
                error=str(exc),
            )
            results.append(CaseResult(case=case, embedding_error=str(exc)))
            continue

        hits = await service.search(
            case.chat_id,
            case.question,
            query_embedding=embedding_result.embedding,
            before=case.asked_at,
        )
        results.append(CaseResult(case=case, hits=hits))
    return results


def _load_all_cases(paths: list[Path]) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in paths:
        cases.extend(load_cases(path))
    return cases


def _print_results(results: list[CaseResult]) -> None:
    """Human-readable per-case line -- raw data only, no metrics (S3-4)."""
    for result in results:
        case = result.case
        if result.embedding_error is not None:
            status = f"EMBED-ERROR ({result.embedding_error})"
        else:
            best_sim = max((hit["similarity"] for hit in result.hits), default=0.0)
            status = f"{len(result.hits)} hit(s), best_sim={best_sim:.3f}"
        print(f"[{case.stratum}] chat={case.chat_id} {case.question[:70]!r} -> {status}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "dsn",
        help=(
            "Seed database DSN (REQUIRED, no default -- decided [Q1]: the "
            "harness must never silently point at a live database)."
        ),
    )
    parser.add_argument(
        "--cases",
        action="append",
        type=Path,
        default=None,
        help=(
            f"Eval case file, repeatable (default: {DEFAULT_CASES_PATH} -- the "
            "tracked synthetic template; pass internal/eval/cases.json for the "
            "real golden set once S3b fills it in)."
        ),
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=None,
        help="Override rag.min_similarity from config/default.yml for this run.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Override rag.max_results (the harness's k for recall@k) for this run.",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    """Bootstrap AIRouter/MemoryRepository/pool directly against the given
    seed DSN (no Dishka -- there is no request to scope to, decided [Q2])."""
    args = _parse_args(argv)

    case_paths = args.cases or [DEFAULT_CASES_PATH]
    try:
        cases = _load_all_cases(case_paths)
    except EvalCaseFileError as exc:
        print(f"INVALID case file: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print("No cases loaded -- nothing to evaluate.", file=sys.stderr)
        return 2

    settings = Settings()
    pool = await create_pool(args.dsn, min_size=_POOL_MIN_SIZE, max_size=_POOL_MAX_SIZE)
    try:
        repo = MemoryRepository(pool)
        # No response_log_repo: this is a read-only eval run against a
        # throwaway seed container, not a chat -- there is no per-chat/user
        # cost context worth logging, and AIRouter._log_usage() no-ops
        # cleanly on repo=None (mirrors scripts/backfill_explicitness.py).
        ai_router = AIRouter(settings)
        service = RAGMemoryService(
            memory_repo=repo,
            ai_router=ai_router,
            min_similarity=(
                args.min_similarity
                if args.min_similarity is not None
                else settings.rag.min_similarity
            ),
            max_results=(
                args.max_results if args.max_results is not None else settings.rag.max_results
            ),
        )

        results = await run_eval(cases, service=service, ai_router=ai_router)
        _print_results(results)
        metrics = compute_metrics(results, k=service.max_results)
        print()
        print(format_metrics(metrics))
    finally:
        await close_pool(pool)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
