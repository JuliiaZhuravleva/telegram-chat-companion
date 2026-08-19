#!/usr/bin/env python
"""Side-by-side retrieval from both stores, for the S5 cutover decision.

`scripts/eval_rag.py` scores one store against the eval cases. This script
asks the question that one cannot: *is the chunk index better than the Q&A
memory it replaces* -- and it exists because the obvious way to answer that is
wrong in a way that flatters the answer.

**Why recall@k cannot decide this.** `eval_metrics` credits a hit when it lands
in a case's `expected_message_id_ranges`. The auto-harvested cases
(`harvest_auto_strata.py`) set that range to the widest honest bound --
"anywhere at or before the question" -- because nobody has verified by hand
which message actually answers any of them. For `chat_memory`, whose rows point
at one message each, that collapses recall to "did anything clear the floor",
which is why the recorded baselines say to read `blind_rate` and ignore
`recall@k`. For `chat_chunks`, whose rows span a range, *every* chunk from the
chat's past overlaps that bound, so a floor-free run scores recall 1.000 and
blind rate 0.000 -- a number produced entirely by the shape of the cases and
the absence of a floor, not by retrieval getting better. Reporting it as a win
over 0.364 would be the single most misleading thing this slice could do.

**What it reports instead.**

1. *Coverage*, which needs no ground truth and is the revision's central claim:
   how much of the conversation each store can reach at all. `chat_memory` only
   holds turns where the bot replied; chunks hold everything.
2. *Side-by-side top-k* per case, floor-free, from both stores, with the
   scores and enough of each row's text to judge. This is the artifact a human
   (or a judge model) grades to produce the pinpointed golden set S3b needs --
   it turns "we lack ground truth" from a blocker into a review task.

**The output contains real chat content.** It defaults under `internal/`
(gitignored) and must never be committed or pasted into `docs/`; the aggregate
numbers in section 1 are public-safe, the transcripts in section 2 are not.
Same rule as `docs/rag-eval-baseline.md`'s header states for its own file.

Usage::

    python -m scripts.eval_compare <dsn> --cases internal/eval/cases_auto_harvest.json

`<dsn>` is a REQUIRED positional with no default: read-only or not, a tool
that can be pointed at a live database must never be able to default onto one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import asyncpg
import structlog

from scripts.eval_schema import EvalCase, EvalCaseFileError, load_cases
from src.config import Settings
from src.database.connection import close_pool, create_pool
from src.database.repositories.chunks import ChunkRepository
from src.database.repositories.memory import MemoryRepository
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter
from src.services.rag.chunk_retrieval import ChunkRetrievalService
from src.services.rag.memory import RAGMemoryService
from src.services.text.query_hygiene import strip_bot_address

logger = structlog.get_logger(__name__)

_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 2
_INTER_CASE_DELAY_SECONDS = 1.0
_HEAD_CHARS = 400

_EXIT_OK = 0
_EXIT_BAD_INPUT = 2
_EXIT_NOTHING_MEASURED = 3

DEFAULT_OUT = Path("internal/eval/backend-comparison.md")


def exit_code(measured: int, store_failures: dict[str, int]) -> int:
    """The run's verdict, as a pure function so it can be tested.

    Three distinct outcomes, and the point is that only the first is a result:

    * nothing embedded at all -> `_EXIT_NOTHING_MEASURED`;
    * every measured case failed to *search* one of the stores -> also
      `_EXIT_NOTHING_MEASURED`. `measured` counts cases whose query embedding
      succeeded, which says nothing about whether either store answered: a
      schema mismatch or a bug in the hybrid SQL fails inside the per-store
      loop, is written into the report as "search failed", and used to leave
      both the count and the exit code reading like a clean run. A comparison
      where one side answered nothing is not a comparison;
    * otherwise 0, with any partial failures reported separately by the caller.

    Extracted from `main()` because an exit code is what a wrapper reads, and
    a contract nothing can call is a contract nothing can check.
    """
    if measured == 0:
        return _EXIT_NOTHING_MEASURED
    if any(count >= measured for count in store_failures.values()):
        return _EXIT_NOTHING_MEASURED
    return _EXIT_OK


async def coverage(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Per chat: how much of the history each store can reach.

    `covered_messages` for chunks is the count of messages whose id falls
    inside some chunk's `[msg_from, msg_to]`, computed against the messages
    themselves rather than summed from `msg_count` -- the chunker overlaps
    consecutive chunks by up to 2 messages on purpose, so summing would report
    more coverage than exists (measured on production: `sum(msg_count)` exceeds
    the chunkable message count by ~20%, which is the seam, not double
    indexing).

    `chat_memory` is counted by distinct `source_message_id`, which is the only
    link it has back to the conversation.
    """
    rows = await pool.fetch(
        """
        WITH msgs AS (
            SELECT chat_id, count(*)::int AS total
            FROM chat_messages
            GROUP BY chat_id
        ),
        chunked AS (
            SELECT m.chat_id, count(DISTINCT m.message_id)::int AS covered
            FROM chat_messages m
            JOIN chat_chunks c
              ON c.chat_id = m.chat_id
             AND m.message_id BETWEEN c.msg_from AND c.msg_to
            GROUP BY m.chat_id
        ),
        mem AS (
            SELECT chat_id,
                   count(*)::int AS rows_stored,
                   count(DISTINCT source_message_id)::int AS covered
            FROM chat_memory
            GROUP BY chat_id
        ),
        chunk_rows AS (
            SELECT chat_id, count(*)::int AS rows_stored
            FROM chat_chunks
            GROUP BY chat_id
        )
        SELECT msgs.chat_id,
               msgs.total,
               coalesce(mem.rows_stored, 0)   AS memory_rows,
               coalesce(mem.covered, 0)       AS memory_covered,
               coalesce(chunk_rows.rows_stored, 0) AS chunk_rows,
               coalesce(chunked.covered, 0)   AS chunk_covered
        FROM msgs
        LEFT JOIN mem        ON mem.chat_id = msgs.chat_id
        LEFT JOIN chunked    ON chunked.chat_id = msgs.chat_id
        LEFT JOIN chunk_rows ON chunk_rows.chat_id = msgs.chat_id
        ORDER BY msgs.total DESC
        """
    )
    return [dict(row) for row in rows]


def _format_coverage(rows: list[dict[str, Any]]) -> str:
    """Section 1 -- aggregates only, no message text. Public-safe."""
    lines = [
        "## 1. Coverage — how much of the conversation each store can reach",
        "",
        "| chat | messages | memory rows | reachable | chunk rows | reachable |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    totals = {
        "total": 0,
        "memory_rows": 0,
        "memory_covered": 0,
        "chunk_rows": 0,
        "chunk_covered": 0,
    }
    for index, row in enumerate(rows, start=1):
        for key in totals:
            totals[key] += row[key]
        mem_pct = 100 * row["memory_covered"] / row["total"] if row["total"] else 0.0
        chunk_pct = 100 * row["chunk_covered"] / row["total"] if row["total"] else 0.0
        # Chats are numbered, not named: a chat_id is a real Telegram
        # identifier and this file is written where one may not go.
        lines.append(
            f"| #{index} | {row['total']} | {row['memory_rows']} | {mem_pct:.1f}% | "
            f"{row['chunk_rows']} | {chunk_pct:.1f}% |"
        )
    mem_pct = 100 * totals["memory_covered"] / totals["total"] if totals["total"] else 0.0
    chunk_pct = 100 * totals["chunk_covered"] / totals["total"] if totals["total"] else 0.0
    lines.append(
        f"| **all** | **{totals['total']}** | **{totals['memory_rows']}** | **{mem_pct:.1f}%** | "
        f"**{totals['chunk_rows']}** | **{chunk_pct:.1f}%** |"
    )
    lines.append("")
    lines.append(
        "`reachable` = share of this chat's messages that the store can return at all: "
        "for `chat_memory`, messages named by a `source_message_id`; for `chat_chunks`, "
        "messages whose id falls inside some chunk's span. Counted against the messages "
        "themselves rather than summed from `msg_count`, because consecutive chunks overlap "
        "by design and summing overstates coverage."
    )
    return "\n".join(lines)


async def compare_cases(
    cases: list[EvalCase],
    *,
    memory: RAGMemoryService,
    chunks: ChunkRetrievalService,
    ai_router: AIRouter,
    trigger_words: list[str],
    top_k: int,
) -> tuple[list[str], int]:
    """Section 2 -- the graded artifact.

    Returns `(lines, n_measured, store_failures)`. The third element exists
    because `n_measured` counts cases whose *query embedding* succeeded, which
    says nothing about whether either store answered: a broken query, a schema
    mismatch or a bad hybrid SQL fails inside the per-store loop, is written
    into the report as "search failed", and used to leave the count and the
    exit code reading like a clean run. A comparison where one side answered
    nothing is not a comparison.
    """
    lines = [
        "## 2. Side-by-side retrieval, floor-free",
        "",
        "Both stores answer the same question with the same query embedding, with **no**",
        "similarity floor on either side, so what is shown is what each store *ranks*",
        "rather than what its current threshold happens to admit. Grade each row: does it",
        "contain what the question asks for? That judgement is the pinpointed ground truth",
        "the auto-harvested cases lack, and it is what turns these into a real golden set.",
        "",
    ]
    measured = 0
    store_failures: dict[str, int] = {}
    for index, case in enumerate(cases, start=1):
        if index > 1:
            await asyncio.sleep(_INTER_CASE_DELAY_SECONDS)
        query = strip_bot_address(case.question, trigger_words)
        lines.append(
            f"### Case {index} — stratum `{case.stratum}`, asked {case.asked_at:%Y-%m-%d %H:%M}"
        )
        lines.append("")
        lines.append(f"> {case.question}")
        lines.append("")
        try:
            embedding = (await ai_router.generate_embedding(query, chat_id=case.chat_id)).embedding
        except AIProviderError as exc:
            lines.append(f"_embedding failed: {exc} — case not measured_\n")
            continue

        for label, service in (("chat_memory (Q&A pairs)", memory), ("chat_chunks (S5)", chunks)):
            try:
                hits = await service.search(
                    case.chat_id,
                    query,
                    query_embedding=embedding,
                    min_similarity=0.0,
                    max_results=top_k,
                    before=case.asked_at,
                )
            except Exception as exc:  # noqa: BLE001 - asyncpg has no shared base worth naming
                lines.append(f"**{label}** — search failed: {exc}\n")
                store_failures[label] = store_failures.get(label, 0) + 1
                continue
            lines.append(f"**{label}** — {len(hits)} hit(s)")
            lines.append("")
            if not hits:
                lines.append("_nothing returned_")
                lines.append("")
                continue
            for rank, hit in enumerate(hits, start=1):
                sim = hit.get("similarity")
                sim_str = f"{sim:.3f}" if isinstance(sim, int | float) else "n/a"
                extra = ""
                if hit.get("rrf_score") is not None:
                    extra = (
                        f", rrf={hit['rrf_score']:.5f}"
                        f", vec_rank={hit.get('vec_rank')}, fts_rank={hit.get('fts_rank')}"
                    )
                head = " ".join((hit.get("content") or "").split())[:_HEAD_CHARS]
                lines.append(f"{rank}. `sim={sim_str}{extra}` — {head}")
            lines.append("")
        measured += 1
    return lines, measured, store_failures


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("dsn", help="Database holding BOTH stores (required, no default)")
    parser.add_argument("--cases", type=Path, required=True, help="Eval case file")
    parser.add_argument("--top-k", type=int, default=5, help="Rows per store per case")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Where to write the report (default: {DEFAULT_OUT}, gitignored)",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cases = load_cases(args.cases)
    except EvalCaseFileError as exc:
        print(f"INVALID case file: {exc}", file=sys.stderr)
        return _EXIT_BAD_INPUT
    if not cases:
        print("No cases loaded -- nothing to compare.", file=sys.stderr)
        return _EXIT_BAD_INPUT

    settings = Settings()
    ai_router = AIRouter(settings)
    try:
        pool = await create_pool(args.dsn, min_size=_POOL_MIN_SIZE, max_size=_POOL_MAX_SIZE)
        try:
            cov = await coverage(pool)
            lines, measured, store_failures = await compare_cases(
                cases,
                memory=RAGMemoryService(
                    memory_repo=MemoryRepository(pool),
                    ai_router=ai_router,
                    min_similarity=settings.rag.min_similarity,
                    max_results=args.top_k,
                ),
                chunks=ChunkRetrievalService(
                    ChunkRepository(pool), ai_router, max_results=args.top_k
                ),
                ai_router=ai_router,
                trigger_words=list(settings.bot.trigger_words),
                top_k=args.top_k,
            )
        finally:
            await close_pool(pool)
    finally:
        await ai_router.close()

    report = "\n".join(
        [
            "# Backend comparison — chat_memory vs chat_chunks",
            "",
            "> Contains real chat content. Gitignored on purpose; never copy section 2",
            "> into `docs/`. Section 1's aggregates are public-safe.",
            "",
            _format_coverage(cov),
            "",
            *lines,
        ]
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(_format_coverage(cov))
    print()
    print(f"Wrote {measured}/{len(cases)} measured case(s) to {args.out}")

    # Same rule as eval_rag: a run that measured nothing must not exit 0, or a
    # wrapper reading only the status treats a total provider outage as a result.
    if measured == 0:
        print("MEASURED NOTHING: every case failed to embed.", file=sys.stderr)
        return exit_code(measured, store_failures)
    for label, count in sorted(store_failures.items()):
        if count >= measured:
            print(
                f"MEASURED NOTHING FOR {label}: its search failed on all "
                f"{count} measured case(s). The report has no rows to compare "
                "against -- do not read this run as a comparison.",
                file=sys.stderr,
            )
            return exit_code(measured, store_failures)
    if store_failures:
        print(
            "warning: some searches failed — "
            + ", ".join(f"{label}: {n}/{measured}" for label, n in sorted(store_failures.items())),
            file=sys.stderr,
        )
    return exit_code(measured, store_failures)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
