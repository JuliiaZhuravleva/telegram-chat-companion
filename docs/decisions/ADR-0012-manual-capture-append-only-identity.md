# ADR-0012: Manual Knowledge Base capture is append-only, keyed by the capture

**Status:** accepted
**Date:** 2026-08-17
**Plan item:** KB-07 (S2 "Захват", `docs/plans/kb-revision-2026-08.md`)
**Relates to:** `docs/decisions/ADR-0003-chat-facts-data-model.md` (the supersession contract and the
"manual beats extracted at equal key" rule this ADR makes structurally unreachable),
`src/services/knowledge/capture.py` (`fact_predicate`),
`src/database/repositories/knowledge.py` (`append_fact` alongside `upsert_fact`),
`src/bot/handlers/commands.py` (`handle_remember`, `handle_kb_undo`),
`alembic/versions/014_chat_facts.py` (`idx_chat_facts_active_key`)

---

## Context

Phase 1's `/remember` passed a **constant** predicate — the literal string `"факт"` — for every
manually-captured fact. ADR-0003's designed identity is `(chat_id, subject, predicate)`, so a constant
predicate collapsed it to `(chat_id, subject)`: the second `/remember` about the same subject matched
`idx_chat_facts_active_key`, took the supersession branch, and **retired the first fact**.

That was not a decision, it was a consequence. The observable behaviour was that "tell the bot one
more thing about the venue" silently deleted what it already knew about the venue, with a
confirmation message saying the new fact had been saved — which was true, and which is exactly why
nobody noticed. The revision's D-3 records it as data loss.

Two ways out: keep one row per subject and make replacement explicit, or let a subject accumulate
facts. The owner's asks are about a chat's *accumulating* knowledge — house rules, an upcoming event,
who does what — so a subject is a topic of several facts, not a single-valued slot.

## Decision

**A manually-captured fact gets a predicate derived from the message that captured it**
(`fact_predicate(message_id)` → `"m<message_id>"`), and is written through a repository method that
cannot supersede anything (`append_fact`). A second `/remember` about the same subject is a second
row; both are live.

`upsert_fact` keeps the supersession semantics unchanged for the writers that genuinely replace a
value at a stable key: the Phase-2 reconciler, and S3's "rewrite this fact" action. The two paths are
separate methods rather than one method with a flag, because their invariants are opposite and a
boolean parameter that silently switches between "replace the value at this key" and "never touch
another row" gets passed wrongly in exactly one direction.

The predicate is derived from the **message id** rather than from a clock, a counter or a random
token, and that choice carries a property worth naming: the same capture can only ever own one row.
A redelivered Telegram update, or a user tapping send twice, collides on `idx_chat_facts_active_key`,
and `append_fact` answers "already saved, #id" instead of inserting a duplicate. A timestamp- or
uuid-based predicate would make that branch unreachable and every redelivery a duplicate fact —
carrying its own duplicate into the prompt and the token budget.

## Consequences

**ADR-0003's "manual beats extracted at equal key" rule is now structurally unreachable, and Phase 2
must not rely on it.** An extracted fact will never again collide by key with a manual one: manual
predicates are per-capture, extracted predicates are semantic. Authority must therefore be resolved
by *comparing* `authority_level` among the active facts sharing `(chat_id, subject)` — a reconciler
decision — not by a key collision the schema no longer produces. This is the one thing in this ADR
that a future implementer can get wrong by simply not reading it.

**`idx_chat_facts_active_key` changes meaning without changing shape.** It stays the DB-level backstop
for the create-create race, but for manual capture it now guards *redelivery of one capture* rather
than *one active fact per subject*. No migration: the index is unchanged, and the new predicate
scheme simply stops colliding. S2 adds no DDL at all — 027 was the revision's only migration, as
ADR-0003 intended.

**`predicate` stopped being human-meaningful, so it stopped being rendered.** `/kb`'s DM view printed
`{subject} — {predicate}: {value}`; with a generated predicate that line would have shown machine
identity to a reader. Every surface now renders `fact_text`, which is also the column the model is
shown — one fact reads the same in the group list, the DM list and the prompt.

**Contradiction became representable, and that changed two things outside this decision's own scope.**
Two live facts can now say opposite things about one subject, while retrieval ranks by similarity with
no recency term. So (a) the prompt block no longer calls its contents "authoritative, current" — it
names them as the organizers' curated facts, which is what they are; and (b) capture ships with a
removal path in the same slice (`handle_kb_undo`, the first caller of `reject_fact`). Append-only
removes the *accidental* correction that supersession provided, and shipping it without any way to
retire a fact would have left a chat accumulating permanent, retrievable, un-removable facts until
S3. The full management surface is still S3/KB-11; the undo button is the floor.

**Rows are still never deleted.** `append_fact` only inserts, undo sets `status='rejected'` with
`rejected_by`/`rejected_at` (migration 027), and `_LIVE_FACTS` hides the row from every read path.
ADR-0003's history guarantee is intact.

## Alternatives considered

**Keep a semantic predicate and make the supersession explicit** ("this replaces the venue"). Rejected
for this slice: it needs a fact-picking surface to point at *which* fact is being replaced, i.e. the
S3 management screen, and until that exists the only available behaviour is the silent overwrite this
ADR removes. S3/KB-12 provides it as "✏️ Переписать", built on `upsert_fact`, which is why that method
keeps its semantics.

**Drop or re-scope `idx_chat_facts_active_key`.** Rejected: it would be a second `chat_facts`
migration — precisely the schema cost ADR-0003 paid to avoid — and it would discard the redelivery
protection that the message-id predicate turns the index into.

**Content-hash predicate** (dedup identical text). Rejected: it makes two *deliberately* identical
facts in different contexts unrepresentable, and it moves the "is this the same fact?" question from
the transport layer (where redelivery actually happens) into semantics, where it needs a policy
nobody has asked for. A content-identical duplicate is a plausible future check, at capture time,
reported to the user — not an identity rule.
