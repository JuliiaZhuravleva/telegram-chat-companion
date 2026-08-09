# ADR-0009: KB sort order — separate retrieval ranking from budget-trim ordering

**Status:** accepted
**Date:** 2026-08-09
**Plan item:** S2-3a (rag-s2-hygiene)
**Author:** specialist-architect
**Supersedes:** ADR-0003 (`chat-facts-data-model`) Part 2, "Trimming algorithm" step 1 only — the
`ORDER BY salience DESC, embedding <=> $2 ASC` sort-order clause and its rationale ("retrieval order is
the repository's concern... salience ordering already encodes priority"). ADR-0003 Part 1 (schema) and
the rest of Part 2 (`KB_BUDGET_TOKENS` allocation, `MAX_FACT_CHARS` cap, fencing/sanitization) are
**unaffected** and remain in force.
**Relates to:** `src/database/repositories/knowledge.py:235-265`
(`KnowledgeRepository.search_by_similarity`), `src/services/text/prompt_builder.py:410-437`
(`trim_facts_to_budget`), `src/services/text/pipeline.py:59-62,468-474` (`_KB_SEARCH_LIMIT`,
`_timed_kb_facts`), `docs/plans/rag-revision-2026-08.md` §4.3 ("KB fix that must not wait: cosine becomes
the primary sort, salience a tiebreak"), `docs/plans/rag-s2-hygiene.md` §S2-3; downstream **S2-3b**
(backend-dev — implements this decision, rewrites the two tests this ADR obsoletes).

---

## Context

`KnowledgeRepository.search_by_similarity()` currently sorts
`ORDER BY salience DESC, embedding <=> $2 ASC` (`knowledge.py:258`), a choice ADR-0003 Part 2 made
deliberately and documented as feeding straight into `trim_facts_to_budget()` without re-sorting
(`prompt_builder.py`'s docstring: "Facts are assumed to already be ordered by salience DESC, then
...similarity DESC ... this function does not re-sort").

That single `ORDER BY` is asked to answer two different questions at once:

1. **Retrieval ranking ("выдача"):** of all a chat's active facts, which ones are relevant enough to
   this query to fetch at all? This is bounded by `_KB_SEARCH_LIMIT = 5`, whose own comment already
   states the intent: "a DB round-trip cost cap, not a budget" (`pipeline.py:60-62`) — i.e. the query is
   supposed to return the top-N *candidates*, with real budget enforcement happening downstream.
2. **Budget-trim priority ("обрезка под бюджет"):** of the facts actually retrieved, which ones survive
   if they don't all fit in `KB_BUDGET_TOKENS` (300 tokens, ADR-0003 Part 2)?

Sorting by `salience DESC` **first** makes the SQL clause answer question 2 (priority) while it is
actually gating question 1 (relevance) — the `LIMIT 5` round trip returns the chat's 5 highest-salience
facts regardless of the query, *before* `trim_facts_to_budget()` (or anything else) ever sees the rest.
Today this is inert: every fact's `salience` defaults to `0.5` (`chat_facts.salience FLOAT DEFAULT 0.5`,
`knowledge.py:110`), so ties fall through to the `embedding <=> $2 ASC` secondary key and retrieval is
*de facto* cosine-ordered. The moment PH2 (KB autocollection) starts differentiating salience, this stops
being true: a query about topic A can be answered with high-salience facts about topic B, and the
genuinely relevant, lower-salience fact about topic A is never fetched — not merely down-ranked, *absent*,
because it fell outside `LIMIT 5` before `trim_facts_to_budget()` runs. This is exactly the defect the
roadmap flags as a "KB fix that must not wait" (`rag-revision-2026-08.md` §4.3) and the plan brief
describes as "начнёт выдавать «самые важные» факты вместо тех, что относятся к вопросу."

The two existing tests pinning the current contract both assert the *conflated* order and must change:
`tests/unit/test_knowledge_repository.py::test_search_by_similarity_orders_salience_then_similarity`
(asserts the literal SQL string) and
`tests/integration/test_knowledge_repository.py::test_salience_wins_over_similarity` (asserts, against a
real pgvector index, that a dissimilar-but-high-salience fact outranks a near-identical-but-low-salience
one at the retrieval layer). The plan explicitly frames this as an architect-level decision, not a bare
`ORDER BY` edit, because reversing the clause naively also breaks `trim_facts_to_budget()`'s only
priority signal — hence this ADR.

---

## Decision

**Split the single sort key into two, one per concern, in two different layers.**

### 1. Retrieval (SQL, `KnowledgeRepository.search_by_similarity`) — similarity-primary

```sql
ORDER BY embedding <=> $2 ASC, salience DESC
```

Relevance to the current query decides which facts the round trip returns at all. `salience DESC`
survives only as a **tiebreak** for (near-)exact-distance ties — matching the roadmap's own framing
("cosine becomes the primary sort, salience a tiebreak", §4.3) and giving a deterministic order instead
of an arbitrary one when two facts are equidistant. `_KB_SEARCH_LIMIT` (`pipeline.py:60`) keeps its
existing meaning and value (5) — it was already documented as a round-trip cap, not a budget; this
decision makes the code match that comment for the first time.

### 2. Budget-trim priority (`trim_facts_to_budget`, `prompt_builder.py`) — salience-primary, stable

Before the existing char-cap + token-accumulate loop, stable-sort the retrieved facts by `salience DESC`:

```python
facts = sorted(facts, key=lambda f: f.get("salience", 0.5), reverse=True)
```

Python's `sorted()` is stable, so facts with equal salience keep the incoming (similarity) order — no
explicit secondary key needed, and none should be added: an explicit `(salience, similarity)` tuple key
risks getting the tie-break direction backwards (`similarity` here is "lower is better" pre-conversion in
some call sites — do not assume its sign without checking), where relying on stability cannot get it
wrong. This is the one-line change that preserves the *intent* ADR-0003 Part 2's test encoded — a
higher-salience fact should survive a budget cut ahead of a lower-salience one — while scoping it
correctly: salience decides what to drop from an **already query-relevant** candidate set, never what
counts as relevant in the first place. Today, with all facts at the default `salience = 0.5`, this sort
is a no-op and trim order stays exactly what it is now (retrieval/cosine order) — behavior is unchanged
until PH2 differentiates salience, exactly mirroring how the retrieval-side change is presently inert too.

### 3. `_KB_SEARCH_LIMIT` stays a round-trip cap, unchanged (5)

Not touched by this decision. Noted as a minor open consideration below, not a blocker.

---

## Why this is the right altitude (not a bigger redesign)

A full ranking overhaul (hybrid FTS+vector, RRF, a blended `salience*w1 + similarity*w2` score) is
explicitly S3+ scope per the roadmap (`rag-revision-2026-08.md` §4.2, "hybrid FTS + vector, RRF, in one
SQL" for `chat_chunks`, not `chat_facts`). This item only needs to stop the SQL clause from doing two
jobs at once; a two-line SQL edit plus a one-line stable sort is proportionate to that, and inventing a
blended-score column now would pre-empt a design question (weighting) that belongs to the later hybrid-
retrieval work, not to this correctness fix.

---

## Consequences

### Positive

- Retrieval relevance is restored for the case PH2 will create (differentiated salience) — a query about
  topic A can no longer be silently answered with an unrelated topic B fact merely because B has higher
  salience.
- Budget-trim priority is preserved, not lost — it just moves to the layer that actually owns "what do we
  do when not everything fits," which is where ADR-0003 Part 2's own docstring already pointed (`trim_facts_to_budget`) even though the sort itself lived one layer up.
- No schema/migration change; no new column, no new query parameter, no new configuration.
- Behavior is provably unchanged today (default salience, stable sort is a no-op) — this is a forward-
  looking correctness fix, not a live-traffic behavior change, consistent with the plan's "no P0" framing (none of S2's defects currently break prod).

### Negative / Trade-offs

- **Accepted, not a regression:** with `_KB_SEARCH_LIMIT = 5` unchanged, a highly-salient-but-marginally-
  relevant fact that falls outside the top-5-by-similarity is now never fetched, where previously it might
  have out-competed on salience. This is the fix working as intended (salience must never resurrect an
  irrelevant fact) — flagged here explicitly so a future reader doesn't mistake "a previously-surfaced
  high-salience fact stopped appearing for an unrelated query" for a bug.
- Two existing tests assert the superseded contract and must be rewritten (S2-3b, not this item — see
  Implementation notes). Until S2-3b lands, this ADR describes the target state, not the shipped one.
- `trim_facts_to_budget()`'s docstring claim "this function does not re-sort" becomes false and must be
  corrected (S2-3b) — leaving it as-is after the code changes would misdescribe the function to the next
  reader, the same class of drift ADR-0003 itself warns against.
- Minor, non-blocking: with `_KB_SEARCH_LIMIT = 5` and `KB_BUDGET_TOKENS` fitting "roughly 4-5 short
  facts" (ADR-0003 Part 2), there is little headroom for the trim-side salience sort to actually reorder
  anything in practice today — it mostly matters once PH2 grows both the per-chat fact count and
  `fact_text` lengths. Revisit `_KB_SEARCH_LIMIT` if/when that happens; not a reason to change it now.

---

## Rejected alternatives

### A: Only flip the SQL clause, do not touch `trim_facts_to_budget()`

Leaves the SQL as `embedding <=> $2 ASC, salience DESC` and drops the salience-priority signal from
budget trimming entirely (trim order = arrival order = cosine order). **Rejected**: this is the "naive
flip" the plan explicitly warns breaks the trim step — it silently discards the one piece of the original
design (curated facts should survive a budget cut ahead of merely-more-similar ones) that was actually
sound, for no benefit over the two-line version above.

### B: Drop salience from the SQL `ORDER BY` entirely (pure cosine, no tiebreak)

**Rejected**: cheap to add, and a deterministic tiebreak is strictly better than depending on Postgres's
unspecified tie order for equidistant rows. Keeping `salience DESC` as tiebreak costs nothing and matches
the roadmap's own phrasing ("a tiebreak").

### C: Compute a blended score in SQL (`salience * w1 + similarity * w2`) for retrieval

**Rejected** for this item (see "right altitude" above): introduces a weighting decision that belongs to
the S3+ hybrid-retrieval redesign, not to a correctness fix scoped at 45 minutes. Would also need a
distance-to-similarity conversion and a chosen scale for `salience`, both undecided and out of scope here.

### D: Raise `_KB_SEARCH_LIMIT` well above 5 to shrink the risk named in Consequences

**Rejected as part of this decision**: `_KB_SEARCH_LIMIT` is a DB round-trip cost cap (`pipeline.py:60`),
tuning it is an independent, separately-motivated decision (cost/latency vs. recall) that this ADR does
not need to make to fix the sort-order defect. Named as a future consideration instead of bundled in.

---

## Implementation notes for S2-3b (backend-dev)

1. **`knowledge.py:258`**: change `ORDER BY salience DESC, embedding <=> $2 ASC` to
   `ORDER BY embedding <=> $2 ASC, salience DESC`. Update the docstring at `:244-247` to cite this ADR
   instead of "ADR-0003 Part 2" for the ordering contract (ADR-0003 Part 2 remains correct for the budget
   constants and fencing — only the sort-order clause moved).
2. **`prompt_builder.py`'s `trim_facts_to_budget()`**: add the one-line stable sort by `salience DESC`
   before the char-cap/accumulate loop (Decision 2 above); correct the docstring — it must no longer claim
   "this function does not re-sort."
3. **Tests to rewrite** (both currently assert the superseded contract; do not just delete):
   - `tests/unit/test_knowledge_repository.py::test_search_by_similarity_orders_salience_then_similarity`
     → rename (e.g. `test_search_by_similarity_orders_similarity_then_salience`) and assert
     `"ORDER BY embedding <=> $2 ASC, salience DESC"` in the emitted SQL.
   - `tests/integration/test_knowledge_repository.py::test_salience_wins_over_similarity` → this exact
     scenario (dissimilar-high-salience vs. near-identical-low-salience) is now the **wrong** expectation
     at the retrieval layer and must be replaced there with the opposite assertion — similarity wins even
     against a much-higher-salience competitor — per the source plan's own control requirement ("Контроль
     обязан падать на текущем коде": the new test must fail against the pre-fix `ORDER BY`, i.e. actually
     exercise the changed clause). The **original scenario and its intent are not discarded** — port it,
     unchanged in spirit, to a new unit test on `trim_facts_to_budget()` in `tests/unit/test_prompt_builder.py`
     (two facts, one lower-similarity-higher-salience, assert it survives a tight budget ahead of the
     higher-similarity-lower-salience one). `test_orders_by_similarity_when_salience_tied`
     (unit + integration) needs **no change** — its assertion (similarity decides when salience is tied)
     holds under the new order too, since it now exercises the primary key directly instead of a
     tie-break fallback.
4. Do not change `_KB_SEARCH_LIMIT`, `KB_BUDGET_TOKENS`, or `MAX_FACT_CHARS` — out of scope (see Rejected
   D and ADR-0003 Part 2, both unaffected).

## Out of scope (this ADR and S2-3b)

- Any blended/weighted retrieval score (Rejected C) — S3+ hybrid-retrieval redesign territory.
- Changing `_KB_SEARCH_LIMIT` (Rejected D).
- `chat_chunks` / hybrid FTS+vector / RRF (`rag-revision-2026-08.md` §4.1–4.2) — different table, later
  slice.
- Anything in ADR-0003 Part 1 (schema) or the rest of Part 2 (budget allocation, fencing, sanitization) —
  unaffected by this decision.

---

*Document generated as part of S2-3a (rag-s2-hygiene plan).*
*Architect: specialist-architect (universal baseline).*
