#!/usr/bin/env python
"""KB retrieval report (S0 / KB-01): choose the similarity floor from real traffic.

``TextProcessingPipeline`` has been writing one ``retrieval_log`` row per KB
lookup since migration 022, and each row's ``results`` JSONB carries the
per-fact cosine ``sim`` (``pipeline.py:582-590``). Retention is 90 days
(``config/default.yml`` ``retrieval_log_days``). So the question "what
similarity floor should KB-15 use?" is already answered by data on disk --
this report reads it instead of guessing, which is why S0 runs before S4.

Nothing here writes. The query runs inside an asyncpg ``readonly=True``
transaction, so the guarantee is enforced by PostgreSQL rather than by
convention: a stray INSERT would raise ``ReadOnlySQLTransactionError``, not
succeed quietly.

Usage::

    python -m scripts.kb_report <dsn> [--since-days N] [--chat-id ID]
                                      [--floors 0.4,0.45,...] [--markdown]

``<dsn>`` is a REQUIRED positional with NO default, same rule as
``scripts/eval_rag.py``: a report that can be run against a live database
must never be able to *default* onto one.

Exit codes::

    0   measured at least one KB retrieval
    1   measured nothing -- an empty window is NOT a clean result
    2   usage / connection error

Exit 1 on an empty window is deliberate. A silent zero looks exactly like a
healthy "no problems found", and this script exists to produce a number
somebody will paste into a plan; it must fail loudly rather than print a
tidy table of zeroes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

# Sweep range from the plan (docs/plans/kb-revision-2026-08.md, KB-01).
DEFAULT_FLOORS: tuple[float, ...] = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85)
DEFAULT_SINCE_DAYS = 90

_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 4


# ---------------------------------------------------------------- data shapes


@dataclass(frozen=True)
class FloorRow:
    """What one candidate floor would have done to the recorded traffic."""

    floor: float
    facts_kept: int
    facts_cut: int
    # Turns that returned facts today but would return none at this floor.
    # Kept separate from `turns_blind_today` on purpose: lumping them together
    # would blame the floor for turns that already had nothing to show.
    turns_newly_blind: int

    @property
    def facts_total(self) -> int:
        return self.facts_kept + self.facts_cut

    @property
    def facts_cut_pct(self) -> float:
        return 100.0 * self.facts_cut / self.facts_total if self.facts_total else 0.0


@dataclass(frozen=True)
class Report:
    turns_total: int
    # Turns whose KB lookup returned nothing at all (empty base, no embedding,
    # or an errored search). No floor can make these worse.
    turns_blind_today: int
    facts_total: int
    best_sim_percentiles: dict[int, float]
    all_sim_percentiles: dict[int, float]
    floors: tuple[FloorRow, ...]

    @property
    def turns_with_facts(self) -> int:
        return self.turns_total - self.turns_blind_today


# ------------------------------------------------------------- pure summarise


def _percentile(sorted_values: Sequence[float], pct: int) -> float:
    """Nearest-rank percentile. No numpy dependency for one arithmetic call."""
    if not sorted_values:
        return 0.0
    rank = max(1, min(len(sorted_values), round(pct / 100.0 * len(sorted_values))))
    return sorted_values[rank - 1]


def summarize(
    turns: Sequence[Sequence[float]],
    floors: Sequence[float] = DEFAULT_FLOORS,
) -> Report:
    """Summarise per-turn similarity lists. Pure -- this is the tested seam.

    ``turns`` is one list of fact similarities per recorded KB lookup, in any
    order; an empty inner list is a lookup that returned no facts.
    """
    all_sims: list[float] = []
    best_sims: list[float] = []
    blind_today = 0

    for sims in turns:
        if not sims:
            blind_today += 1
            continue
        all_sims.extend(sims)
        best_sims.append(max(sims))

    all_sorted = sorted(all_sims)
    best_sorted = sorted(best_sims)
    pcts = (10, 25, 50, 75, 90, 95)

    floor_rows: list[FloorRow] = []
    for floor in floors:
        kept = sum(1 for s in all_sims if s >= floor)
        newly_blind = sum(1 for b in best_sims if b < floor)
        floor_rows.append(
            FloorRow(
                floor=floor,
                facts_kept=kept,
                facts_cut=len(all_sims) - kept,
                turns_newly_blind=newly_blind,
            )
        )

    return Report(
        turns_total=len(turns),
        turns_blind_today=blind_today,
        facts_total=len(all_sims),
        best_sim_percentiles={p: _percentile(best_sorted, p) for p in pcts},
        all_sim_percentiles={p: _percentile(all_sorted, p) for p in pcts},
        floors=tuple(floor_rows),
    )


# --------------------------------------------------------------- row decoding


def extract_sims(results: Any) -> list[float]:
    """Pull the per-fact ``sim`` values out of one ``retrieval_log.results``.

    asyncpg hands back JSONB as ``str`` unless a codec is registered, so both
    forms are accepted. A row whose payload is malformed contributes an empty
    list -- i.e. it counts as a blind turn rather than vanishing from the
    denominator, which would quietly flatter every floor.
    """
    if isinstance(results, str):
        try:
            results = json.loads(results)
        except (ValueError, TypeError):
            return []
    if not isinstance(results, list):
        return []
    sims: list[float] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        raw = item.get("sim")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            sims.append(float(raw))
    return sims


# ----------------------------------------------------------------------- I/O

_QUERY = """
    SELECT results
    FROM retrieval_log
    WHERE source = 'kb'
      AND created_at >= NOW() - ($1::int * INTERVAL '1 day')
      AND ($2::bigint IS NULL OR chat_id = $2)
    ORDER BY created_at
"""


async def fetch_turns(
    pool: asyncpg.Pool, *, since_days: int, chat_id: int | None
) -> list[list[float]]:
    """Read recorded KB lookups. Read-only, enforced by the transaction."""
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        rows = await conn.fetch(_QUERY, since_days, chat_id)
    return [extract_sims(row["results"]) for row in rows]


# -------------------------------------------------------------------- render


def format_report(report: Report, *, since_days: int, markdown: bool = False) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"KB retrieval report — last {since_days} days")
    add("")
    add(f"  KB lookups recorded : {report.turns_total}")
    add(
        f"  ...returned nothing : {report.turns_blind_today} "
        f"({100.0 * report.turns_blind_today / report.turns_total:.1f}%) "
        "— already blind today, no floor can worsen these"
    )
    add(f"  ...returned facts   : {report.turns_with_facts}")
    add(f"  facts returned      : {report.facts_total}")
    add("")

    add("Similarity percentiles")
    add("  p    best-per-turn   all facts")
    for p in sorted(report.best_sim_percentiles):
        add(
            f"  {p:>3}  {report.best_sim_percentiles[p]:>13.3f}"
            f"   {report.all_sim_percentiles[p]:>9.3f}"
        )
    add("")

    add("Floor sweep — what each candidate floor would have done")
    if markdown:
        add("")
        add("| floor | facts kept | facts cut | turns newly blind |")
        add("|---|---|---|---|")
    else:
        add("  floor   facts kept    facts cut   turns newly blind")

    for row in report.floors:
        newly_pct = (
            100.0 * row.turns_newly_blind / report.turns_with_facts
            if report.turns_with_facts
            else 0.0
        )
        if markdown:
            add(
                f"| {row.floor:.2f} | {row.facts_kept} | "
                f"{row.facts_cut} ({row.facts_cut_pct:.1f}%) | "
                f"{row.turns_newly_blind} ({newly_pct:.1f}%) |"
            )
        else:
            add(
                f"  {row.floor:.2f}   {row.facts_kept:>10}   "
                f"{row.facts_cut:>5} ({row.facts_cut_pct:>5.1f}%)   "
                f"{row.turns_newly_blind:>5} ({newly_pct:>5.1f}%)"
            )

    add("")
    add(
        "'turns newly blind' = turns that show KB facts today but would show none\n"
        "at this floor. That is the cost side; the benefit is the facts cut, which\n"
        "are the ones the prompt currently calls authoritative without earning it."
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------- CLI


def _parse_floors(raw: str) -> tuple[float, ...]:
    try:
        floors = tuple(sorted(float(part) for part in raw.split(",") if part.strip()))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"floors must be comma-separated numbers: {exc}") from exc
    if not floors:
        raise argparse.ArgumentTypeError("floors must not be empty")
    if any(f < 0.0 or f > 1.0 for f in floors):
        raise argparse.ArgumentTypeError("floors must lie in [0.0, 1.0] (cosine similarity)")
    return floors


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only report over retrieval_log to choose the KB similarity floor."
    )
    parser.add_argument(
        "dsn",
        help="PostgreSQL DSN. Required positional with no default, on purpose.",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=DEFAULT_SINCE_DAYS,
        help=f"Window to read (default {DEFAULT_SINCE_DAYS}, matching retrieval_log retention).",
    )
    parser.add_argument("--chat-id", type=int, default=None, help="Restrict to one chat.")
    parser.add_argument(
        "--floors",
        type=_parse_floors,
        default=DEFAULT_FLOORS,
        help="Comma-separated candidate floors to sweep.",
    )
    parser.add_argument(
        "--markdown", action="store_true", help="Emit the sweep as a Markdown table."
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.since_days < 1:
        print("--since-days must be >= 1", file=sys.stderr)
        return 2

    pool = None
    try:
        pool = await asyncpg.create_pool(args.dsn, min_size=_POOL_MIN_SIZE, max_size=_POOL_MAX_SIZE)
        if pool is None:
            print("could not create a connection pool", file=sys.stderr)
            return 2
        turns = await fetch_turns(pool, since_days=args.since_days, chat_id=args.chat_id)
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"database error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if pool is not None:
            await pool.close()

    if not turns:
        print(
            f"no KB retrievals recorded in the last {args.since_days} days"
            + (f" for chat {args.chat_id}" if args.chat_id is not None else "")
            + "\nNothing was measured. This is a failure, not a clean result —"
            " widen the window, check the chat id, or confirm the KB is enabled"
            " somewhere.",
            file=sys.stderr,
        )
        return 1

    report = summarize(turns, args.floors)
    print(format_report(report, since_days=args.since_days, markdown=args.markdown))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
