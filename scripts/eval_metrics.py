"""RAG eval metrics (S3-4): recall@k, MRR, blind-rate, best-sim percentiles.

Built on top of ``scripts.eval_rag.run_eval()``'s stable per-case contract
(``CaseResult`` -- S3-2 deliberately stops at raw hits so this module can do
the arithmetic without a second pass over the search path).

Four numbers, per docs/plans/rag-s3-eval-harness.md S3-4:

* ``recall_at_k`` -- PRIMARY. Over cases that have an expected answer
  (``found`` / ``knowledge-update``), the share where at least one returned
  hit lands in an acceptable ``expected_message_id_ranges`` entry.
* ``mrr`` -- SECONDARY. Same eligible cases, mean reciprocal rank of the
  first acceptable hit (0.0 if none).
* ``blind_rate`` -- share of those same eligible cases that came back with
  *zero* hits at all -- the direct analog of ``q5_replay.py``'s "bot answers
  blind" number (today 7/11 on the auto-harvest corpus, S3-6). Reported
  separately from ``negative_control_rate`` below: S3-5 requires the two
  never be conflated, since an empty result is the *wrong* outcome for a
  ``found``/``knowledge-update`` case but the *right* one for
  ``answer-absent``.
* ``negative_control_rate`` -- share of ``answer-absent`` cases that
  correctly came back empty (S3-5's negative control -- what stops a
  lowered similarity floor from looking like a pure recall win).
* ``best_sim_percentiles`` -- collected for free from the same run (no
  extra retrieval calls): percentiles of each case's best (top) hit
  similarity, over every case that returned at least one hit, regardless of
  stratum. S6 calibrates the threshold on this distribution.

Cases whose query embedding itself failed (``CaseResult.embedding_error``)
are excluded from every rate above and reported separately
(``n_embedding_errors``) -- an infra outage must not read as either a hit
or a correct empty result (same distinction ``eval_rag.py``'s module
docstring already draws for ``answer-absent``).

Knowledge-update freshness (S3-5): ``EvalCase.expected_message_id_ranges``
has no dedicated "which range is current" field (S3-1). This module treats
the LAST range in a ``knowledge-update`` case's list as the authoritative
(freshest) one and the rest as superseded -- a hit landing only in an
earlier range counts as a MISS, matching
``tests/fixtures/eval/cases.json``'s knowledge-update case and its note
("правильный ответ — свежий; попадание только в устаревшую версию —
ошибка"). This is a convention, not yet a schema-enforced contract; flagging
for the architect to consider formalizing once S3b's real golden set has
more than one knowledge-update case to validate it against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from scripts.eval_schema import EvalCase, MessageIdRange

if TYPE_CHECKING:
    from scripts.eval_rag import CaseResult

DEFAULT_PERCENTILES: tuple[int, ...] = (10, 25, 50, 75, 90)


@dataclass(frozen=True)
class Metrics:
    """Aggregate metrics over one eval run. See module docstring for defs."""

    k: int
    recall_at_k: float
    mrr: float
    n_recall_cases: int
    blind_rate: float
    n_blind_eligible: int
    negative_control_rate: float
    n_negative_control: int
    best_sim_percentiles: dict[int, float] = field(default_factory=dict)
    n_best_sim: int = 0
    n_embedding_errors: int = 0


def _acceptable_ranges(case: EvalCase) -> list[MessageIdRange]:
    """Ranges a retrieved hit must land in to count as a correct answer.

    ``answer-absent`` has none by construction (nothing should be found).
    ``knowledge-update`` accepts only the last-listed (freshest) range --
    see module docstring. ``found`` accepts any of its ranges (S3-1 uses
    multiple ranges on a single ``found`` case to mean "the answer may be
    split across these spots", not "these are chronological versions").
    """
    if case.stratum == "answer-absent":
        return []
    if case.stratum == "knowledge-update":
        return [case.expected_message_id_ranges[-1]]
    return list(case.expected_message_id_ranges)


def _hit_rank(result: CaseResult, *, k: int) -> int | None:
    """1-based rank of the first acceptable hit within the top ``k``, else None."""
    ranges = _acceptable_ranges(result.case)
    if not ranges:
        return None
    for rank, hit in enumerate(result.hits[:k], start=1):
        message_id = hit.get("source_message_id")
        if message_id is None:
            continue
        if any(r.start <= message_id <= r.end for r in ranges):
            return rank
    return None


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile, ``p`` in ``[0, 100]`` (numpy's default method).

    ``sorted_values`` must be non-empty and already sorted ascending.
    """
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (p / 100)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[int(rank)]
    lower_weight = sorted_values[lower] * (upper - rank)
    upper_weight = sorted_values[upper] * (rank - lower)
    return lower_weight + upper_weight


def compute_metrics(
    results: list[CaseResult],
    *,
    k: int,
    percentiles: tuple[int, ...] = DEFAULT_PERCENTILES,
) -> Metrics:
    """Compute every S3-4 metric from a completed ``run_eval()`` result set.

    ``k`` is the harness's configured top-k for this run (``max_results``) --
    hits beyond it are ignored defensively even though ``run_eval()``'s
    search call already caps results there.
    """
    scored = [r for r in results if r.embedding_error is None]
    n_embedding_errors = len(results) - len(scored)

    recall_eligible = [r for r in scored if r.case.stratum != "answer-absent"]
    ranks = [_hit_rank(r, k=k) for r in recall_eligible]
    n_recall_cases = len(recall_eligible)
    recall_at_k = (
        sum(1 for rank in ranks if rank is not None) / n_recall_cases if n_recall_cases else 0.0
    )
    mrr = (
        sum(1.0 / rank for rank in ranks if rank is not None) / n_recall_cases
        if n_recall_cases
        else 0.0
    )
    blind_rate = (
        sum(1 for r in recall_eligible if not r.hits[:k]) / n_recall_cases
        if n_recall_cases
        else 0.0
    )

    negative_control = [r for r in scored if r.case.stratum == "answer-absent"]
    n_negative_control = len(negative_control)
    negative_control_rate = (
        sum(1 for r in negative_control if not r.hits[:k]) / n_negative_control
        if n_negative_control
        else 0.0
    )

    best_sims = sorted(max(hit["similarity"] for hit in r.hits[:k]) for r in scored if r.hits[:k])
    best_sim_percentiles = {p: _percentile(best_sims, p) for p in percentiles} if best_sims else {}

    return Metrics(
        k=k,
        recall_at_k=recall_at_k,
        mrr=mrr,
        n_recall_cases=n_recall_cases,
        blind_rate=blind_rate,
        n_blind_eligible=n_recall_cases,
        negative_control_rate=negative_control_rate,
        n_negative_control=n_negative_control,
        best_sim_percentiles=best_sim_percentiles,
        n_best_sim=len(best_sims),
        n_embedding_errors=n_embedding_errors,
    )


def format_metrics(metrics: Metrics) -> str:
    """Human-readable summary block for the CLI (``scripts/eval_rag.py``)."""
    lines = [
        f"recall@{metrics.k}: {metrics.recall_at_k:.3f} (n={metrics.n_recall_cases})",
        f"MRR: {metrics.mrr:.3f} (n={metrics.n_recall_cases})",
        f"blind rate (found/knowledge-update, empty result): "
        f"{metrics.blind_rate:.3f} (n={metrics.n_blind_eligible})",
        f"negative-control rate (answer-absent, correctly empty): "
        f"{metrics.negative_control_rate:.3f} (n={metrics.n_negative_control})",
    ]
    if metrics.best_sim_percentiles:
        pct_str = ", ".join(
            f"p{p}={v:.3f}" for p, v in sorted(metrics.best_sim_percentiles.items())
        )
        lines.append(f"best-sim percentiles: {pct_str} (n={metrics.n_best_sim})")
    else:
        lines.append("best-sim percentiles: n/a (no case returned a hit)")
    if metrics.n_embedding_errors:
        lines.append(
            f"excluded from all metrics above: {metrics.n_embedding_errors} "
            "case(s) with a query embedding_error"
        )
    return "\n".join(lines)
