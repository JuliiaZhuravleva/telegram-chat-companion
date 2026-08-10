"""Auto-strata harvest (S3-6): a generated `found`-stratum floor, no Julia.

``internal/analysis/q5_replay.py`` already found that a heuristic regex over
memory-seeking phrasing ("помнишь / напомни / что решили / …") pulls 11
candidate questions out of the n8n-era production corpus
(``internal/analysis/results/q5-replay.md``). That script is read-only
one-off analysis and reimplements the search SQL itself
(``q5_replay.py:100-124``) -- exactly the shortcut S3-2 exists to avoid for
anything that will gate a rollout. This module moves the *harvest* half
(the regex query against the n8n corpus) into the tracked harness, so it
produces ``EvalCase`` objects (S3-1) that S3-2's ``eval_rag.py`` replays
through the real ``RAGMemoryService.search()`` path -- no second SQL
reimplementation, no separate metrics code.

**Honest boundary (S3-6's own text): this is a floor, not a golden set.**
The regex identifies memory-*seeking* questions -- it has no way to know
which earlier message actually answers one, and nobody has reviewed these
11 cases by hand. So ``expected_message_id_ranges`` here is deliberately a
wide, unverified placeholder: "any message in this chat at or before the
question" (``[1, trigger_message_id]``), not a pinpoint answer location.
That makes ``recall@k`` for these cases collapse to "did the real search
path return *anything* at all" -- exactly ``blind_rate`` (S3-4), which is
the one number this harvest can honestly claim. Do not read a high
recall@k on this file as retrieval accuracy; read ``blind_rate`` instead
(today: 7/11 empty per ``results/q5-replay.md``, reproduced through the
real path once S3-2's harness replays this file). This does not replace
S3b's manually-curated golden set (roadmap §7: "<50 cases: trust only
large deltas").

Reads the **n8n corpus** (throwaway container, port 55435) -- a different
database and a different schema than S3-2's seed DSN (port 55434, the
``rag-analysis-seed`` snapshot that ``RAGMemoryService.search()`` queries).
Plain ``asyncpg.connect()`` is used here, not
``src.database.connection.create_pool()``: that helper registers the
pgvector extension on every connection (``register_vector``), and the n8n
corpus's ``chat_messages`` / ``bot_response_log`` tables have no vector
columns and no pgvector extension -- registering it there would just be an
extra failure mode with nothing to check.

Usage::

    python -m scripts.harvest_auto_strata <n8n-dsn> [--out PATH] [--limit N]

``<n8n-dsn>`` is a REQUIRED positional argument with NO default (mirrors
S3-2/[Q1]'s seed-DSN rule -- this must never be able to default onto a live
database). The throwaway ``rag-analysis-n8n`` container is expected at
``postgresql://r:r@127.0.0.1:55435/n8n``; pass that explicitly.

Output defaults to ``internal/eval/cases_auto_harvest.json`` -- alongside
S3b's real golden set (``internal/eval/cases.json``), gitignored for the
same reason (``internal/`` carries real chat content, ``.gitignore:114``).
Replay it with the existing harness, unchanged::

    python -m scripts.eval_rag <seed-dsn> --cases internal/eval/cases_auto_harvest.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg
import structlog

from scripts.eval_schema import EvalCase

logger = structlog.get_logger(__name__)

# Ported from internal/analysis/q5_replay.py:31-35 (moved into the tracked
# harness per this item's mandate -- internal/ is gitignored wholesale, so a
# tracked script cannot depend on importing from it).
MEMORY_SEEKING_REGEX = (
    r"(помнишь|напомни|вспомни|что решили|кто говорил|кто сказал|что там был"
    r"|когда (мы|был|шл|поедем|едем)|расскажи (про|о)|а что (мы|было)"
    r"|какой (был|мы)|что (обсуждали|говорили)|о ч[её]м (мы|был))"
)

# Same trigger-type / length bounds q5_replay.py used -- this is a like-for-like
# port of the harvest query, not a redesign.
_TRIGGER_TYPES = ("trigger", "reply_to_bot", "reply")
_MIN_QUESTION_LEN = 15
_MAX_QUESTION_LEN = 400

DEFAULT_LIMIT = 60  # matches q5_replay.py's LIMIT
DEFAULT_OUT = Path("internal/eval/cases_auto_harvest.json")

_HARVEST_QUERY = """
    SELECT l.chat_id, tm.message_id AS trigger_message_id, tm.created_at,
           tm.content AS question
    FROM bot_response_log l
    JOIN chat_messages tm
      ON tm.chat_id = l.chat_id AND tm.message_id = l.trigger_message_id
    JOIN chat_messages rm
      ON rm.chat_id = l.chat_id AND rm.message_id = l.response_message_id
    WHERE l.trigger_type = ANY($1::text[])
      AND tm.content ~* $2
      AND length(tm.content) BETWEEN $3 AND $4
    ORDER BY tm.created_at
    LIMIT $5
"""

_AUTO_HARVEST_NOTE = (
    "Auto-harvested (S3-6) via heuristic regex over the n8n-era corpus -- "
    "NOT a manually-verified golden-set case. expected_message_id_ranges is "
    "an unverified placeholder covering the whole chat history up to the "
    "question; only presence/absence of a hit (blind_rate, S3-4) is "
    "meaningful here, not recall@k pinpoint accuracy. See "
    "docs/plans/rag-s3-eval-harness.md S3-6 for the floor-vs-golden-set boundary."
)


def _case_from_row(row: asyncpg.Record) -> EvalCase:
    """Build one auto-harvested ``EvalCase`` from a harvest-query row.

    ``stratum="found"`` -- the regex selects memory-*seeking* phrasing, so
    the harvested cases are treated as "should have found something" for
    blind_rate purposes (S3-4's docstring already expects this file to feed
    that metric). ``expected_message_id_ranges`` is intentionally the widest
    honest bound (module docstring) rather than a fabricated pinpoint.
    """
    trigger_message_id: int = row["trigger_message_id"]
    return EvalCase(
        chat_id=row["chat_id"],
        question=row["question"],
        asked_at=row["created_at"],
        expected_message_id_ranges=[{"start": 1, "end": trigger_message_id}],
        stratum="found",
        note=_AUTO_HARVEST_NOTE,
    )


async def harvest_cases(
    conn: asyncpg.Connection,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[EvalCase]:
    """Run the memory-seeking harvest query and return validated ``EvalCase``s.

    ``conn`` is an already-connected asyncpg connection to the n8n corpus --
    takes a connection rather than a DSN so tests can pass a fake with a
    scripted ``.fetch()`` (mirrors S3-2's ``run_eval()`` taking a service
    rather than opening its own pool).
    """
    rows = await conn.fetch(
        _HARVEST_QUERY,
        list(_TRIGGER_TYPES),
        MEMORY_SEEKING_REGEX,
        _MIN_QUESTION_LEN,
        _MAX_QUESTION_LEN,
        limit,
    )
    return [_case_from_row(row) for row in rows]


def write_cases(cases: list[EvalCase], out: Path) -> None:
    """Serialize harvested cases as a JSON array ``load_cases()`` can read back."""
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = [json.loads(case.model_dump_json()) for case in cases]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "dsn",
        help=(
            "n8n corpus DSN (REQUIRED, no default -- mirrors S3-2/[Q1]: must "
            "never silently point at a live database)."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Where to write harvested cases (default: {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Max cases to harvest (default: {DEFAULT_LIMIT}, matches q5_replay.py).",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    conn = await asyncpg.connect(args.dsn)
    try:
        cases = await harvest_cases(conn, limit=args.limit)
    finally:
        await conn.close()

    if not cases:
        print("No memory-seeking candidates found -- nothing harvested.", file=sys.stderr)
        return 1

    write_cases(cases, args.out)
    logger.info("harvest_auto_strata: wrote cases", count=len(cases), out=str(args.out))
    print(f"Harvested {len(cases)} auto-strata case(s) -> {args.out}")
    print(
        "FLOOR, NOT GOLDEN SET (S3-6): expected_message_id_ranges are unverified "
        "placeholders -- only blind_rate from a real eval_rag.py run is meaningful "
        "here, not recall@k. Does not replace S3b."
    )
    print(f"Replay: python -m scripts.eval_rag <seed-dsn> --cases {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
