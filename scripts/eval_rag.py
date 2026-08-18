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
from collections.abc import Sequence
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
from src.services.text.query_hygiene import strip_bot_address

logger = structlog.get_logger(__name__)

DEFAULT_CASES_PATH = Path("tests/fixtures/eval/cases.json")

# A small pool is plenty for a sequential, one-case-at-a-time script run
# against a throwaway seed container -- no reason to open prod-sized
# min_size/max_size (src/database/connection.py's defaults) against it.
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 5

# Pacing between cases. ``q5_replay.py:136`` sleeps 0.3s after every case,
# and that -- not sequentiality -- is what its "pacing" actually was. It
# matters more here than it did there: S2-1 removed the embeddings fallback
# chain (there was never a working one), so a RateLimitError is re-raised as
# AIProviderError immediately, with no second provider to absorb it. Without
# this delay a quota trip turns every remaining case into an embedding_error,
# and those cases drop out of every metric denominator -- i.e. a provider
# quota would quietly reshape the score. The 11-case auto-strata run never
# reached the limit; S3b's 30-50 curated cases are the size that would.
_INTER_CASE_DELAY_SECONDS = 0.3

# Exit codes. 0 means "measured something"; a caller that only checks the
# exit code must never read a run that measured NOTHING as a success -- this
# harness is meant to gate the S5 cutover, and "no evidence" is not a pass.
_EXIT_OK = 0
_EXIT_BAD_INPUT = 2
_EXIT_NOTHING_MEASURED = 3


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

    ``search_error`` is the same idea one step later: the retrieval call
    itself failed (connection drop, pool exhaustion, query timeout). It is
    kept separate from ``embedding_error`` so the summary can say which
    half broke, and both are excluded from every metric denominator --
    an errored case is not evidence of anything, in either direction.
    """

    case: EvalCase
    hits: list[dict[str, Any]] = field(default_factory=list)
    embedding_error: str | None = None
    search_error: str | None = None


async def run_eval(
    cases: list[EvalCase],
    *,
    service: RAGMemoryService,
    ai_router: AIRouter,
    trigger_words: Sequence[str],
) -> list[CaseResult]:
    """Replay every case through the real embed -> search path, in order.

    ``trigger_words`` exists so this harness embeds what production embeds.
    Auto-harvested questions come from ``chat_messages.content`` verbatim
    (``harvest_auto_strata.py``), so they carry the same leading ``бот`` the
    pipeline now strips before embedding (R0/TD-092). Measuring the raw
    question would score a retrieval path that no longer runs anywhere, and
    the resulting baseline would drift from production for a reason nothing
    in the numbers would reveal.

    It is a global list rather than each case's own chat config: the harness
    talks to a throwaway seed DB with no ``chat_settings`` to merge from, and
    every production chat currently configures the same two words. If that
    ever stops being true, this is the line that has to learn about the
    per-chat merge.

    Sequential and paced (``_INTER_CASE_DELAY_SECONDS``, mirroring
    ``q5_replay.py:136``): this hits a real embeddings provider per case,
    and the golden set (S3b) is expected to stay in the tens of cases, not
    thousands -- no need for concurrency here.

    Both external calls are guarded per case. Guarding only the embedding
    call (as the first version did) means a single transient DB error on
    the last case discards every result already computed, since nothing is
    printed until this function returns -- and the provider spend for those
    cases is already made.
    """
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        if index:
            await asyncio.sleep(_INTER_CASE_DELAY_SECONDS)
        query = strip_bot_address(case.question, trigger_words)
        try:
            embedding_result = await ai_router.generate_embedding(query, chat_id=case.chat_id)
        except AIProviderError as exc:
            logger.warning(
                "eval_rag: query embedding failed, case counted as embedding_error",
                chat_id=case.chat_id,
                question=case.question[:80],
                error=str(exc),
            )
            results.append(CaseResult(case=case, embedding_error=str(exc)))
            continue

        try:
            hits = await service.search(
                case.chat_id,
                query,
                query_embedding=embedding_result.embedding,
                before=case.asked_at,
            )
        except Exception as exc:  # noqa: BLE001 -- see below
            # Deliberately broad: the failure modes here are asyncpg's
            # (connection reset, pool timeout, a pgvector dimension
            # mismatch), and there is no shared base class to name. The
            # alternative is aborting the whole run, which is strictly
            # worse -- the case is recorded as a non-measurement and the
            # remaining cases still get their chance.
            logger.warning(
                "eval_rag: retrieval failed, case counted as search_error",
                chat_id=case.chat_id,
                question=case.question[:80],
                error=str(exc),
            )
            results.append(CaseResult(case=case, search_error=str(exc)))
            continue

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
        elif result.search_error is not None:
            status = f"SEARCH-ERROR ({result.search_error})"
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
        return _EXIT_BAD_INPUT
    if not cases:
        print("No cases loaded -- nothing to evaluate.", file=sys.stderr)
        return _EXIT_BAD_INPUT

    settings = Settings()
    # No response_log_repo: this is a read-only eval run against a
    # throwaway seed container, not a chat -- there is no per-chat/user
    # cost context worth logging, and AIRouter._log_usage() no-ops
    # cleanly on repo=None (mirrors scripts/backfill_explicitness.py).
    # Built before the pool so its own finally covers a create_pool failure:
    # the router lazily opens an httpx.AsyncClient per provider, and only
    # AIRouter.close() tears those down.
    ai_router = AIRouter(settings)
    try:
        pool = await create_pool(args.dsn, min_size=_POOL_MIN_SIZE, max_size=_POOL_MAX_SIZE)
        try:
            service = RAGMemoryService(
                memory_repo=MemoryRepository(pool),
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

            results = await run_eval(
                cases,
                service=service,
                ai_router=ai_router,
                trigger_words=settings.bot.trigger_words,
            )
            _print_results(results)
            metrics = compute_metrics(results, k=service.max_results)
            print()
            print(format_metrics(metrics))
        finally:
            await close_pool(pool)
    finally:
        await ai_router.close()

    # A run in which nothing could be measured is NOT a pass. Every case
    # erroring out prints "recall@5: 0.000 (n=0)", which is textually the
    # same shape as a genuinely terrible score -- so a wrapper diffing the
    # numbers, or one checking only the exit code, would read a total
    # provider outage as a clean result. Fail loudly instead.
    if metrics.n_recall_cases + metrics.n_negative_control == 0:
        print(
            f"MEASURED NOTHING: all {len(cases)} case(s) errored out "
            f"({metrics.n_embedding_errors} embedding, {metrics.n_search_errors} search). "
            "The metrics above are vacuous -- do not treat this run as a result.",
            file=sys.stderr,
        )
        return _EXIT_NOTHING_MEASURED

    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
