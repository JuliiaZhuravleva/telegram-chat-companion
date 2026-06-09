# ADR-0003: Sticker Analysis State Is Transient UI, Not a Persisted Enum Column

**Status:** accepted  
**Date:** 2026-06-09  
**Plan item:** C-1 (sticker-status-ux-2026-06-09)  
**Author:** specialist-architect  
**Relates to:** `src/bot/handlers/admin_sticker.py`, `src/bot/keyboards/admin_sticker.py`,
`src/services/modules/sticker/learning.py`, `src/database/repositories/stickers.py`,
`alembic/versions/005_sticker_knowledge.py`

---

## Context

The sticker admin panel's `🔄 Запустить заново` (re-analyze) action ran synchronously but gave
the admin no visible progress signal: the re-analysis blocked the callback handler for several
seconds, and the result was delivered as a separate toast + new message rather than an in-place
edit. Status labels were inconsistent between the set-detail keyboard (`[FAILED]` /
`⏳ ожидает анализа`) and the sticker-detail view (`⏳ Визуальный анализ не выполнен` /
`⚠️ Анализ провалился`) — two different vocabularies for the same three underlying states.

During the planning Q&A for `sticker-status-ux-2026-06-09`, the PM raised the question of
whether the "pending" or "in-progress" UX state required introducing a persistent
`analysis_status` enum column to survive process restarts or display across views in a
single query, or whether the current three-column derivation (`visual_description`,
`analysis_failed`, `analyzed_at`) was sufficient.

### Existing persistence model (migration 005)

The `sticker_knowledge` table already records the three persistent analysis facts:

| Column | Type | Semantics |
|--------|------|-----------|
| `visual_description` | `TEXT` (nullable) | `NULL` = not yet analyzed |
| `analysis_failed` | `BOOLEAN DEFAULT false` | Set `true` after a failed analysis attempt |
| `analyzed_at` | `TIMESTAMPTZ` (nullable) | Timestamp of last successful analysis |

These columns are written by `StickerLearningService.learn()` and cleared by
`StickerRepository.clear_analysis()`. No other column tracks analysis lifecycle.

---

## Decision

**Sticker analysis state remains fully derived from the three existing columns at read time.
No `analysis_status` enum column is added. No background task or job queue is introduced.
Re-analysis stays synchronous.**

The three persistent states derivable from these columns are:

| Derived state | Condition | Icon |
|---------------|-----------|------|
| **Not analyzed** | `visual_description IS NULL AND analysis_failed = false` | ⏳ |
| **Analyzed** | `visual_description IS NOT NULL` | ✅ |
| **Failed** | `analysis_failed = true` | ⚠️ |

The **in-progress** state that the admin observes (`⏳ Анализирую…`) is a transient UI edit
to the Telegram message. It is not persisted to the database; it exists only for the duration
of the synchronous vision call (typically 2–8 s). The handler:

1. Edits the message to `⏳ Анализирую…` (hides action buttons) **before** calling the
   blocking `sticker_service.reanalyze()`.
2. After the call returns, edits the same message to `✅ Анализ обновлён` (new description,
   buttons restored) on success, or `⚠️ Ошибка анализа: <reason>` (+ Retry button) on failure.

The `_status_badge(sticker, lang, *, short: bool) -> str` helper (introduced in A-1) reads
the three derivation columns and returns a consistent icon + label. It is the single
authoritative point for turning persisted column state into display strings. It is used by
both the set-detail keyboard (`short=True`) and the sticker-detail view (`short=False`).

### Rationale

Re-analysis is triggered by an explicit admin tap in a DM session. The admin sees the
in-progress state in the same message they tapped; they are waiting for it. There is no
scenario where the pending state needs to survive a process restart or be visible to a
second admin session — the bot has a single operator and a single admin DM surface.

Persisting an `analysis_status` enum would require a new DB column and a migration, and
would duplicate information already derivable from the three existing columns, introducing
a synchronization risk (e.g. `analysis_status = 'analyzing'` stuck after a crash).

---

## Consequences

### Positive

- **No migration required.** No changes to the `sticker_knowledge` table or any existing
  query.
- **No synchronization risk.** There is no `analysis_status` column that can get out of
  sync with the underlying `visual_description` / `analysis_failed` / `analyzed_at` facts.
- **Crash safety.** If the bot restarts mid-analysis, the sticker retains its pre-analysis
  state (clear + `analysis_failed = false`). The admin sees ⏳ (not analyzed) when they
  re-open the detail view — correct, because the analysis did not complete.
- **Single derivation point.** `_status_badge()` is the only place that maps column state to
  display strings. Adding a new language or changing a label requires touching one function.

### Negative / Trade-offs

- **In-progress state is not visible from a second session.** If two admins simultaneously
  view the same sticker while one triggers re-analysis, the second will not see the
  `⏳ Анализирую…` edit. This is accepted: the bot has one operator and single-DM admin UX.
- **Transient in-progress edit can be lost.** If the Telegram `edit_text` call for the
  `⏳` state fails (network error), the admin sees no in-progress indicator. Per ADR (and
  the `resolved decisions` in the plan), the implementation **logs and continues** — the
  analysis proceeds and the `✅`/`⚠️` result edit is still attempted. The admin receives
  the outcome even if they missed the intermediate state.
- **Long-running analysis blocks the callback.** The synchronous model means the callback
  handler is occupied for the duration of the vision call. This is unchanged behavior and
  is acceptable for a low-frequency admin action with a single operator.

---

## Rejected alternatives

### A: Persist `analysis_status` enum column

Add `analysis_status VARCHAR(20) NOT NULL DEFAULT 'not_analyzed'` (values:
`not_analyzed` / `analyzing` / `analyzed` / `failed`) to `sticker_knowledge`. Derive display
strings from this column instead of the three existing columns.

**Rejected.** Introduces redundancy with the existing three-column model and a
synchronization risk: if the bot crashes while `analysis_status = 'analyzing'`, the row
is stuck in a bad state that the bot cannot automatically recover from without an explicit
stuck-job sweep. Also requires a migration (013 or later) that adds no new query capability —
all three states are already derivable.

### B: Background task with a persisted job queue

Move re-analysis to a background `asyncio.Task` and store a pending job in a `sticker_jobs`
table. The admin gets immediate feedback ("queued"), and the result arrives as a push
notification when the background worker completes.

**Rejected.** Adds a background task runtime, a new table, and a push-notification path —
significant scope beyond the UX defect being fixed. The `sticker-status-ux-2026-06-09`
brief explicitly places this in **OUT / future backlog**: "Background/async re-analysis with
a persisted `analysis_status` enum column + migration." The in-place edit model achieves
the admin's goal (see pending state + result in the same message) without the complexity.

### C: Dual-write: keep derivation but also write `analysis_status` for display

Keep the three derivation columns, but also write an `analysis_status` column so that
status queries are a single column read.

**Rejected.** Queries on `sticker_knowledge` for the admin surface are not
performance-critical (set-detail views are on-demand, not high-frequency). Replacing the
three-column derivation in a WHERE/ORDER clause would save negligible query cost while
adding the same synchronization risk as alternative A.

---

## Scope boundary this decision enforces

The following are explicitly **out of scope** for the current branch and any future item
that references this ADR as a prerequisite without first superseding it:

- Adding any `analysis_status`, `analysis_state`, or equivalent enum/string column to
  `sticker_knowledge`.
- Background or async re-analysis jobs.
- Set-level bulk re-analyze with progress tracking (X/N indicator).
- Push notifications to the admin on completion (vs. in-place edit).

Items in the future backlog that require any of the above **must** first supersede this ADR
with a new ADR that documents the migration path and the synchronization risk mitigation.

---

*Document generated as part of C-1 (sticker-status-ux-2026-06-09 plan).*  
*Architect: specialist-architect (universal baseline).*
