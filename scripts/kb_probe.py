#!/usr/bin/env python
"""KB coverage probe: ask the base a question and see what it would answer with.

`retrieval_log` records what the knowledge base *returned*. It cannot record
what it should have returned and did not — a question the base is blind to
leaves a row that looks exactly like a question the base was never meant to
answer. Once `knowledge_base.min_similarity` ships, a filtered-out turn becomes
indistinguishable from a turn where nothing was relevant, so the blind side of
the ledger has no observable trace at all.

This is the tool for that side. Give it real questions and it reports, per
question, what the base would hand the model and whether anything clears the
floor:

    WOULD ANSWER  0.793  какая у нас традиция про пятницу?
    BLIND         0.612  во сколько начинается созвон?
    BORDERLINE    0.706  что такое открытый проектор?

`BORDERLINE` is a hit whose margin over the floor is smaller than
``--borderline-margin``. It is the interesting category: a slightly different
phrasing of the same question would have been cut, so it marks facts worth
rewording (or a floor worth revisiting) *before* someone hits the bad phrasing
in a live chat.

**The questions must come from outside the base.** Questions written by reading
the facts are a mirror, not a measurement — they will match, and prove nothing
about coverage. Take them from what people actually ask: real chat history, a
support inbox, the organizer's own list.

Everything here is read-only, and enforced rather than promised: the search runs
inside an asyncpg ``readonly=True`` transaction, so a stray write would raise
``ReadOnlySQLTransactionError``. Embedding a question does cost an API call
(gemini-embedding-001, free tier) — one per question, no retries.

The retrieval path is the **production** one: `AIRouter.generate_embedding` and
`KnowledgeRepository.search_by_similarity`, not a reimplementation. A probe that
computes similarity its own way measures its own arithmetic.

Usage::

    python -m scripts.kb_probe <dsn> --chat-id -100123 \
        --question "во сколько созвон?" --question "какие правила?"
    python -m scripts.kb_probe <dsn> --chat-id -100123 --questions-file q.txt
    cat q.txt | python -m scripts.kb_probe <dsn> --chat-id -100123 --questions-file -

``<dsn>`` is a REQUIRED positional with NO default, same rule as
``scripts/kb_report.py`` and ``scripts/eval_rag.py``: a script that can be
pointed at a live database must never be able to *default* onto one.

Exit codes::

    0   probed at least one question successfully
    2   usage / input error
    3   measured nothing (every question errored) -- NOT a clean result
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

from src.config import Settings
from src.database.connection import close_pool, create_pool
from src.database.repositories.knowledge import KnowledgeRepository
from src.services.ai.base import AIProviderError
from src.services.ai.router import AIRouter

_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 5

_EXIT_OK = 0
_EXIT_BAD_INPUT = 2
_EXIT_NOTHING_MEASURED = 3

# How far above the floor a hit still counts as precarious. 0.05 is not a
# tuned constant: the production measurement that produced the 0.70 floor had
# its weakest genuine hit at 0.706, i.e. a margin of 0.006, and calling that
# "comfortably above" would have been the wrong reading.
DEFAULT_BORDERLINE_MARGIN = 0.05

_VERDICT_ANSWER = "WOULD ANSWER"
_VERDICT_BORDERLINE = "BORDERLINE"
_VERDICT_BLIND = "BLIND"
_VERDICT_ERROR = "ERROR"


@dataclass(frozen=True)
class ProbeResult:
    """One question's outcome. `top_sim` is None only when nothing came back."""

    question: str
    verdict: str
    top_sim: float | None
    facts: list[dict[str, Any]]
    error: str | None = None


def classify(top_sim: float | None, *, floor: float, borderline_margin: float) -> str:
    """Verdict for one question. Pure -- this is the tested seam.

    A floor of 0.0 means no filtering (see `_facts_above_floor` in the
    pipeline), so nothing can be blind: whatever came back reaches the model.
    Reporting BLIND there would describe a configuration that is not running.
    """
    if top_sim is None:
        return _VERDICT_BLIND
    if floor <= 0.0:
        return _VERDICT_ANSWER
    if top_sim < floor:
        return _VERDICT_BLIND
    if top_sim - floor < borderline_margin:
        return _VERDICT_BORDERLINE
    return _VERDICT_ANSWER


def summarize(results: list[ProbeResult]) -> dict[str, int]:
    """Counts per verdict, including errors, so nothing is silently dropped."""
    counts = {
        _VERDICT_ANSWER: 0,
        _VERDICT_BORDERLINE: 0,
        _VERDICT_BLIND: 0,
        _VERDICT_ERROR: 0,
    }
    for result in results:
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    return counts


def format_results(results: list[ProbeResult], *, floor: float, show_facts: bool) -> str:
    """Human-readable report. Blind questions first -- they are the finding."""
    order = {_VERDICT_BLIND: 0, _VERDICT_BORDERLINE: 1, _VERDICT_ANSWER: 2, _VERDICT_ERROR: 3}
    lines: list[str] = [f"floor = {floor}", ""]

    for result in sorted(results, key=lambda r: (order.get(r.verdict, 9), r.question)):
        sim = f"{result.top_sim:.3f}" if result.top_sim is not None else "  —  "
        lines.append(f"{result.verdict:<13} {sim}  {result.question}")
        if result.error:
            lines.append(f"                     ! {result.error}")
        if show_facts:
            for fact in result.facts:
                mark = "+" if (fact.get("similarity") or 0.0) >= floor else "-"
                head = " ".join(str(fact.get("fact_text") or "").split())[:70]
                lines.append(f"                {mark} {fact.get('similarity', 0.0):.3f}  {head}")

    counts = summarize(results)
    total = len(results)
    answered = counts[_VERDICT_ANSWER] + counts[_VERDICT_BORDERLINE]
    lines.extend(
        [
            "",
            f"answered      {answered}/{total}" + (f"  ({answered / total:.0%})" if total else ""),
            f"  comfortably {counts[_VERDICT_ANSWER]}",
            f"  borderline  {counts[_VERDICT_BORDERLINE]}   ← a worse phrasing would be cut",
            f"blind         {counts[_VERDICT_BLIND]}",
            f"errors        {counts[_VERDICT_ERROR]}",
        ]
    )
    return "\n".join(lines)


def load_questions(paths: list[str], inline: list[str]) -> list[str]:
    """Questions from --question flags and/or files ('-' reads stdin).

    Blank lines and `#` comments are skipped so a question file can carry notes
    about where the questions came from — which matters, because questions
    derived from the facts themselves measure nothing.
    """
    questions: list[str] = list(inline)
    for path in paths:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                questions.append(stripped)
    return questions


async def probe_one(
    question: str,
    *,
    ai_router: AIRouter,
    knowledge_repo: KnowledgeRepository,
    chat_id: int,
    limit: int,
    floor: float,
    borderline_margin: float,
) -> ProbeResult:
    """Embed one question and search, exactly as the pipeline does.

    An embedding or search failure is recorded as ERROR rather than counted as
    BLIND: "the base has nothing for this" and "we could not ask" are different
    findings, and collapsing them would let a provider outage read as a coverage
    gap and send someone off writing facts nobody needed.
    """
    try:
        embedding_result = await ai_router.generate_embedding(question, chat_id=chat_id)
    except (AIProviderError, OSError) as exc:
        return ProbeResult(question, _VERDICT_ERROR, None, [], f"embedding: {exc}")
    if embedding_result is None:
        return ProbeResult(question, _VERDICT_ERROR, None, [], "embedding: no result")

    try:
        facts = await knowledge_repo.search_by_similarity(
            chat_id, embedding_result.embedding, limit=limit
        )
    except (asyncpg.PostgresError, OSError) as exc:
        return ProbeResult(question, _VERDICT_ERROR, None, [], f"search: {exc}")

    top_sim = max(
        (float(f["similarity"]) for f in facts if f.get("similarity") is not None), default=None
    )
    verdict = classify(top_sim, floor=floor, borderline_margin=borderline_margin)
    return ProbeResult(question, verdict, top_sim, facts)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only probe: what would the knowledge base answer these questions with?"
    )
    parser.add_argument("dsn", help="PostgreSQL DSN. Required positional with no default.")
    parser.add_argument("--chat-id", type=int, required=True, help="Chat whose base to probe.")
    parser.add_argument("--question", action="append", default=[], help="A question (repeatable).")
    parser.add_argument(
        "--questions-file",
        action="append",
        default=[],
        help="File of questions, one per line ('-' for stdin). Repeatable.",
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=None,
        help="Similarity floor. Defaults to knowledge_base.min_similarity from config.",
    )
    parser.add_argument(
        "--borderline-margin",
        type=float,
        default=DEFAULT_BORDERLINE_MARGIN,
        help="A hit within this much of the floor is reported BORDERLINE.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Facts to retrieve per question.")
    parser.add_argument(
        "--show-facts", action="store_true", help="List the retrieved facts under each question."
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        questions = load_questions(args.questions_file, args.question)
    except OSError as exc:
        print(f"could not read questions: {exc}", file=sys.stderr)
        return _EXIT_BAD_INPUT
    if not questions:
        print(
            "No questions given. Pass --question and/or --questions-file.\n"
            "Take them from what people actually ask — questions written by "
            "reading the facts will match by construction and measure nothing.",
            file=sys.stderr,
        )
        return _EXIT_BAD_INPUT

    settings = Settings()
    floor = args.floor if args.floor is not None else settings.knowledge_base.min_similarity

    ai_router = AIRouter(settings)
    results: list[ProbeResult] = []
    try:
        pool = await create_pool(args.dsn, min_size=_POOL_MIN_SIZE, max_size=_POOL_MAX_SIZE)
        try:
            knowledge_repo = KnowledgeRepository(pool)
            for question in questions:
                results.append(
                    await probe_one(
                        question,
                        ai_router=ai_router,
                        knowledge_repo=knowledge_repo,
                        chat_id=args.chat_id,
                        limit=args.limit,
                        floor=floor,
                        borderline_margin=args.borderline_margin,
                    )
                )
        finally:
            await close_pool(pool)
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"database error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_BAD_INPUT
    finally:
        await ai_router.close()

    print(format_results(results, floor=floor, show_facts=args.show_facts))

    # Every question erroring prints "blind 0, answered 0" — textually the same
    # shape as a base with perfect coverage of an empty question list. Fail
    # loudly instead, same rule as kb_report.py's empty window.
    if all(r.verdict == _VERDICT_ERROR for r in results):
        print(
            f"\nMEASURED NOTHING: all {len(results)} question(s) errored. "
            "The counts above are vacuous — do not read them as coverage.",
            file=sys.stderr,
        )
        return _EXIT_NOTHING_MEASURED

    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
