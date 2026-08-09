# ADR-0011: Memory rows must not be irrecoverably deleted without a prior summary

**Status:** accepted
**Date:** 2026-08-09
**Plan item:** S2-11 (rag-s2-hygiene)
**Author:** specialist-architect
**Supersedes:** the age-based-retention resolution S2-6 carried in Revision 1 of
`docs/plans/rag-s2-hygiene.execution.md` (add `chat_memory` to `RetentionCleaner._windows()` and
`RETENTION_TABLES`) — superseded by Julia's `[S2-6b]` answer before it shipped; no code implemented that
resolution, so nothing needs to be rolled back.
**Relates to:** `src/services/maintenance/cleanup.py` (`RetentionCleaner._windows()`),
`src/database/repositories/maintenance.py` (`RETENTION_TABLES`), `src/database/repositories/memory.py`
(`chat_memory`; `delete_expired()` already removed, commit `0905a53`, S2-5a), `src/services/rag/memory.py`
(`RAGMemoryService.delete()`), `docs/plans/rag-revision-2026-08.md` §5 (roadmap rows S5 "Q&A writes stop",
S6 "`chat_memory` drop migration scheduled") and §6 ("Digest/summary tier" listed as a v1 non-goal —
addressed explicitly below, this ADR does not reopen it); downstream **S2-6** (backend-dev — adds an
exclusion comment citing this ADR, no functional change).

---

## Context

S2 originally treated two dead/underused mechanisms as one decision: `MemoryRepository.delete_expired()`
(zero call sites) and whether `chat_memory` should join the other eight tables `RetentionCleaner` prunes by
age (`user_activity`, `chat_messages`, `response_log`, `unauthorized_attempts`, `abuse_blocked_log`,
`message_reactions`, `decision_log`, `retrieval_log`). Revision 1 of this plan resolved that in favor of
age-based retention (Julia: "по возрасту я согласна"), with `delete_expired()` to be removed either way
since its own DELETE-by-`expires_at` shape wasn't reusable for a summary-gated future.

Before that landed, Julia added a condition in the same answer that the initial resolution did not carry
forward: *"я бы перед удалением делала какую-то историческую память по удаляемому периоду, чтобы хотя бы
верхнеуровнево хранилась память"* — and, pressed on scope in `[S2-6b]`, generalized it past `chat_memory`
specifically: *"мне не важно какие таблицы что делают, мне важно чтобы мы не удаляли данные, которые потом
никак не восстановить и ничего из них не запомнили."* That is a data-preservation invariant, not a
table-specific retention policy, and it overturns the Revision 1 resolution: an unconditional age-based
sweep is exactly the mechanism that would violate it. This item is the PM's placeholder for turning that
answer into a durable decision (S2-6, S2-5a and the S6 roadmap line all depend on it either directly or in
spirit).

`chat_memory` is not a one-off case. The roadmap (`rag-revision-2026-08.md`) already schedules two future
events that are the same class of action:

- **S5** ("Retrieval cutover"): "Q&A writes stop" — `chat_memory` stops accepting new rows once
  `chat_chunks` (built in S4) takes over live retrieval.
- **S6** ("Calibration"): "`chat_memory` drop migration scheduled" — the table itself goes away.

`chat_chunks` does not exist yet (S4 migration, not yet written), so this decision is necessarily
prospective for it. But its own lifecycle will eventually raise the identical question — GC of stale
session windows during indexing (§4.1: "GC of stale windows") and, further out, its own eventual
retirement — so scoping this ADR to `chat_memory` alone would just relocate the same unresolved question to
S4/S5 instead of answering it once.

**A scope boundary worth naming explicitly, because it is easy to conflate:** `rag-revision-2026-08.md` §6
lists *"Digest/summary tier — top-k structurally cannot answer «что было за месяц»; it's a separate feature
whose prerequisite (chunks) v1 builds"* as a **non-goal**. That is a decision about a *retrieval feature* —
whether the bot can answer questions about its own history on demand. The summary this ADR requires is a
*preservation artifact* — it does not need to be searchable, injected into a prompt, or even human-legible
beyond a debugging log; its only job is to exist somewhere durable before the source rows it describes stop
existing. A future reader must not read §6 as having already settled this question in the negative — it
answers a different question. This ADR does not reopen §6.

---

## Decision

**Invariant:** no row in a bot long-term-memory store may be irrecoverably deleted by an automatic or
bulk process unless a high-level summary of the data being removed has first been durably persisted
somewhere the row's loss does not also erase. This currently means `chat_memory`; from S4 onward it also
means `chat_chunks`, with no separate ADR required unless that table's lifecycle turns out to differ from
this one in a way that matters (if so, amend this document rather than silently diverging).

This invariant **gates**, specifically:

1. Any future age- or size-based retention sweep over `chat_memory` (the mechanism Revision 1 of S2-6
   would have added to `RetentionCleaner`) — not permitted until a summary step exists.
2. The S6 roadmap line "`chat_memory` drop migration scheduled" — that line must be read as *implicitly*
   including "produce and durably store a summary of what's being dropped," not as a bare `DROP TABLE`. The
   roadmap's own current wording predates this decision; whoever plans S6 should cite this ADR when
   expanding that line, not just the table name.
3. Any equivalent future mechanism for `chat_chunks` (its own retention/GC, or its eventual retirement).

**Carve-outs — the invariant does *not* apply to:**

- **Rows that were never durably stored.** The S2-1 wrong-dimensionality guard in
  `RAGMemoryService.store()` refuses to persist an embedding whose vector length doesn't match, and returns
  without writing. There is nothing to summarize because nothing was ever at rest — this is a write-time
  rejection, not a deletion, and stays exactly as S2-10 left it (`src/services/rag/memory.py`, "still
  refuses to store, deliberate S2-1 behavior").
- **Explicit, user-directed single-record deletion.** `RAGMemoryService.delete()` /
  `MemoryRepository.delete()` (`chat_id`-scoped, currently unreferenced — dead code, not touched by this
  ADR or by S2-5a/S2-5b) exist for a future "forget this" / erasure-style action. If and when such an
  action is wired up, requiring a summary first would be actively wrong: the user's intent *is* for that
  specific memory to be gone, and persisting a summary of content someone explicitly asked to have erased
  reintroduces the exact privacy problem the action exists to resolve. This invariant is about data lost to
  *nobody's* decision (an anonymous background sweep) — not data removed by the *subject's own* decision.
  Named here so a future implementer of erasure doesn't misapply this ADR to gate it.
- **Non-production databases** (tests, local dev) — not the concern this invariant is protecting.
- **`chat_facts` / the knowledge-base store.** Different table, different lifecycle (curated facts, not
  append-only conversation memory), and outside this item's named scope (`chat_memory` and `chat_chunks`
  only, per the plan item title). Not addressed here either way.

**What this ADR does *not* do:** design or build the summarization mechanism. Per the plan's own framing,
that work belongs at the point where memory actually gets decommissioned (S5/S6), against real knowledge of
what "high-level" needs to mean at that scale — building it speculatively now, with no consumer, would be
exactly the one-off work the plan explicitly chose to avoid. A future ADR should cover: where the summary
lives, its granularity (per-chat? per-time-window?), and how "durably persisted" is verified before the
DELETE it gates is allowed to run.

---

## Why this is the right altitude

The alternative Julia's answer rules out — resolve this per-table, as S2-6 originally scoped it — would
leave the identical question open again the moment `chat_chunks` needs its own retention story (S4/S5), and
again whenever the next long-lived store is added after that. A one-line "policy, not implementation"
decision, recorded once, is proportionate to a 30-minute item and avoids that repeat cost; actually building
the summarizer now would be disproportionate in the other direction — there is no design input yet (no
data on `chat_memory`'s real size/growth rate, no decision on what "decommission" technically looks like)
to build it correctly, only to guess.

---

## Consequences

### Positive

- Closes the loop the plan already half-committed to: S2-5a's removal of `delete_expired()` (commit
  `0905a53`) is now backed by a recorded rationale instead of an inline commit-message justification, and
  S2-6 has a citable reason for excluding `chat_memory` instead of a comment that asserts it without
  grounding.
- One invariant, stated once, that both `chat_memory` today and `chat_chunks` from S4 onward can point to —
  the next table doesn't reopen the question.
- Makes explicit which future roadmap step (S6) is now blocked on more than table maintenance — a
  DROP migration written against the current roadmap wording alone would violate this decision.

### Negative / Trade-offs

- **Accepted, not a regression:** `chat_memory` keeps growing unboundedly until S5/S6 — this ADR
  deliberately does not size that risk (no current row-count/growth data, and the plan's own framing for
  this table is "stopgap, cheap and provisional" pending its S5/S6 retirement). If growth becomes an
  operational problem before then, that is a new, separate decision — not evidence against this one.
- `chat_memory.expires_at` (referenced in `MemoryRepository.search()`'s `WHERE` clause) is now confirmed
  fully vestigial — no code path writes it (S2-5a's finding), and this ADR gives no path back to using it
  as-is, since a bare `expires_at`-driven delete is exactly the unconditional-sweep shape the invariant
  forbids. Not fixed here (schema change, outside this item's scope) — flagged so a future cleanup doesn't
  read the column as evidence a TTL mechanism was intended to return.
- `rag-revision-2026-08.md`'s S6 row does not yet textually say "gated on a summary step" — anyone planning
  S6 from the roadmap table alone, without also finding this ADR, could miss the precondition. Updating
  that roadmap row is a natural follow-up but is out of scope for this item (S2-11 is the ADR; editing the
  roadmap doc is not named in its title) and is left for whoever scopes S5/S6.

---

## Rejected alternatives

### A: Keep Revision 1's resolution — age-based retention on `chat_memory` now, no summary step

**Rejected.** This is precisely the shape Julia's `[S2-6b]` answer overturned: an unconditional sweep would
delete rows that were never summarized, i.e. lose them with nothing remembered from them.

### B: Design and build the summarization mechanism in this slice

**Rejected**, per the plan's own scope note: no current consumer, and the correct granularity depends on
knowledge (real data volume, what "decommission" means technically) that doesn't exist yet at S2. Building
it now risks a redesign later anyway — one-off work the plan explicitly declined.

### C: Apply the invariant to every `DELETE` in the codebase, including per-record and user-directed ones

**Rejected** — over-broad. It would misapply a rule against *unaccountable* data loss (a background sweep
nobody asked for) to *accountable* data loss (a user asking for their own record to be forgotten), where
the two have opposite intents; see Carve-outs above.

### D: Leave S2-6/S2-5a undocumented, resolve informally via commit messages only

**Rejected** — this project's own conventions (this plan's "Расширения области" notes, the `_EXEMPT_BY_DESIGN`
precedent referenced for S2-6) exist because an unexplained exclusion in a whitelist/window list is exactly
the kind of thing a future reader silently "fixes" by re-adding the excluded table, without knowing why it
was left out. `chat_memory`'s absence from `RETENTION_TABLES` needs the same durable citation the other
seven exclusions would get.

---

## Implementation notes for S2-6 (backend-dev, `depends_on: [S2-11]`)

1. **No functional change to `RETENTION_TABLES`** (`src/database/repositories/maintenance.py`).
   `chat_memory` was never in that dict — there is nothing to remove, and nothing should be added purely to
   mark an exclusion (the dict's contract is "tables this repository is allowed to prune"; listing an
   excluded table there would invert that meaning). This corrects the "two layers, both need edits" framing
   in this plan's PM notes (`## Заметки PM` → `Расширения области`) — that framing described Revision 1's
   now-superseded resolution (age-based retention *inclusion*), where a second-layer edit really would have
   been needed. Under this decision (exclusion, not inclusion), `_windows()` is the only file that changes.
2. **`_windows()`** (`src/services/maintenance/cleanup.py:77-120`): add a comment — not a dict entry —
   noting the deliberate omission, e.g.:

   ```python
   # chat_memory (RAG long-term memory) is deliberately NOT listed here, and is
   # not in RETENTION_TABLES either. ADR-0011 establishes a data-preservation
   # invariant: memory rows must not be irrecoverably deleted without first
   # persisting a high-level summary, which this slice does not build.
   # Unbounded growth is an accepted, temporary trade-off until the S5/S6
   # decommission work (docs/plans/rag-revision-2026-08.md).
   ```

3. **Test:** a regression asserting `"chat_memory" not in RETENTION_TABLES` and `"chat_memory" not in
   RetentionCleaner(...)._windows()` output is enough to catch an accidental future re-addition; no new
   behavior exists to test beyond that (this item ships no runtime change).
4. `delete_expired()` is already gone (S2-5a, commit `0905a53`) — nothing to reconcile there.

## Out of scope (this ADR)

- Building the summarization mechanism itself — deferred to a future ADR written at S5/S6 planning time.
- Any code change to `RETENTION_TABLES` or `_windows()` — S2-6's job, notes above are guidance for it, not
  performed here (this item, per its own title, is ADR-only).
- Removing the vestigial `chat_memory.expires_at` column — separate, schema-touching tech debt, not raised
  by this item.
- `chat_facts` / knowledge-base lifecycle — different table, not named in this item's scope.
- Designing the eventual erasure/"forget this" user action — carved out above, not designed here.
- Updating `rag-revision-2026-08.md`'s S6 row text to cite this ADR — natural follow-up, left to whoever
  scopes S5/S6 (not part of S2-11).

---

*Document generated as part of S2-11 (rag-s2-hygiene plan).*
*Architect: specialist-architect (universal baseline).*
