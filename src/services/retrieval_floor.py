"""One definition of "does this row clear the retrieval floor".

There were two, and they were byte-identical after renaming variables: the
knowledge base filtered facts in ``TextProcessingPipeline._facts_above_floor``
(shipped in PR #45) and RAG filtered memories in
``RAGMemoryService.memories_above_floor`` (R1). Each was correct. The problem
was drift: both decide what reaches a prompt *and* what gets marked
``above_floor`` in ``retrieval_log``, so a fix applied to one and not the other
would make one source's log describe an injection that did not happen — and
nothing would fail while that was true.

Kept as a flat module rather than folded into either caller, because folding it
into one makes the other import across a boundary it has no business crossing:
the knowledge-base path would depend on ``services.rag`` for a rule that is
about neither.
"""

from __future__ import annotations

from typing import Any


def rows_above_floor(rows: list[dict[str, Any]], min_similarity: float) -> list[dict[str, Any]]:
    """The rows whose cosine similarity clears ``min_similarity``.

    ``min_similarity <= 0.0`` means *no floor* and returns the input unchanged.
    That is not the same as testing ``sim >= 0.0``: cosine similarity is
    defined on [-1, 1], so a ``>= 0.0`` test would still cut negatively-scored
    rows, and the documented "set it to 0.0 to retrieve everything" escape
    hatch would quietly not do that. It is also what the floor-sweep in
    ``scripts/kb_report.py`` assumes when it asks what a floor of 0.0 would
    have kept.

    A row with no usable ``similarity`` is treated as below any floor. It
    cannot be *shown* to clear one, and letting it through would make a
    malformed row more privileged than a genuine distant match. ``bool`` is
    excluded explicitly because it is a subclass of ``int`` and ``True`` would
    otherwise compare as 1.0 — clearing every floor there is.
    """
    if min_similarity <= 0.0:
        return list(rows)
    return [
        row
        for row in rows
        if isinstance(row.get("similarity"), int | float)
        and not isinstance(row.get("similarity"), bool)
        and float(row["similarity"]) >= min_similarity
    ]
