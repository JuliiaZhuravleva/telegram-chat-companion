"""Sticker explicitness/tolerance comparison (ADR-0008 Decision 2).

Single named function for the one comparison this feature is built around,
so the inequality direction is never re-derived (and never accidentally
flipped) at each call site. The hot path (``StickerRepository.search_by_embedding``,
ADR-0008 Decision 6) applies the *equivalent* predicate in SQL for
performance/`LIMIT` correctness; this Python function is the production-adjacent
assertion point tests pin the inequality direction against.
"""

from __future__ import annotations


def is_within_tolerance(explicitness_score: float | None, tolerance_level: float) -> bool:
    """A sticker is an eligible response candidate iff it has been scored
    AND its explicitness does not exceed the chat's ceiling. See ADR-0008.

    ``tolerance_level`` is a ceiling on acceptable explicitness (raising it
    admits a strict superset of what a lower value admits) — not a floor,
    not a minimum-required-spice knob. ``explicitness_score is None``
    (unscored) always excludes the sticker, even at ``tolerance_level = 1.0``
    (Decision 3, fail-closed) — never treat NULL as 0.0 (would ship
    unscored/pre-migration content) or as 1.0 (collapses "unknown" and
    "verified maximally explicit" onto the same value).
    """
    return explicitness_score is not None and explicitness_score <= tolerance_level


def format_explicitness_line(
    explicitness_score: float | None,
    tolerance_level: float,
    lang: str = "ru",
    *,
    is_manual: bool = False,
) -> str:
    """One-line summary for every admin-facing DM sticker card (A-1).

    Format: ``<оценка откровенности>: <score> · уровень приличия чата
    <tolerance_level> · пройдёт/не пройдёт`` — built on top of
    ``is_within_tolerance()`` so the pass/fail verdict can never disagree
    with the actual gating decision (ADR-0008 Decision 2). Terminology
    fixed by the owner's [Q1] answer: the sticker-side value is "оценка
    откровенности", the chat-side ceiling is "уровень приличия" (A-2).

    ``explicitness_score is None`` (unscored — not yet analyzed, a vision
    response that didn't carry a valid value, or a legacy catalog row
    pending the ADR-0008 backfill script) always renders as "not scored"
    and never a fabricated pass/fail — mirrors ``is_within_tolerance()``'s
    fail-closed contract instead of re-deriving it here.

    ``is_manual`` (ADR-0009/A-4): appends a "(вручную)"/"(manual)" marker
    when the score was hand-set by an admin (``explicitness_is_manual``)
    rather than produced by the vision pipeline. Only rendered alongside an
    actual score — a reset always NULLs both fields together (Decision 5),
    so ``is_manual=True`` with ``explicitness_score=None`` shouldn't occur,
    but the badge is intentionally omitted in the unscored branch either
    way to avoid a nonsensical "не оценён (вручную)".
    """
    if lang == "ru":
        label = "Оценка откровенности"
        if explicitness_score is None:
            return f"<b>{label}:</b> не оценён (бот не отправит)"
        verdict = (
            "✅ пройдёт"
            if is_within_tolerance(explicitness_score, tolerance_level)
            else "❌ не пройдёт"
        )
        badge = " (вручную)" if is_manual else ""
        return (
            f"<b>{label}:</b> {explicitness_score:.2f} · "
            f"уровень приличия чата {tolerance_level:.2f} · {verdict}{badge}"
        )

    label = "Explicitness score"
    if explicitness_score is None:
        return f"<b>{label}:</b> not scored (bot won't send it)"
    verdict = (
        "✅ passes" if is_within_tolerance(explicitness_score, tolerance_level) else "❌ blocked"
    )
    badge = " (manual)" if is_manual else ""
    return (
        f"<b>{label}:</b> {explicitness_score:.2f} · chat ceiling {tolerance_level:.2f} · "
        f"{verdict}{badge}"
    )
