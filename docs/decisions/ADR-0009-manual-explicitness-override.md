# ADR-0009: Manual sticker explicitness override — priority over re-analysis, terminology ratification (ADR-0008 addendum)

**Status:** accepted
**Date:** 2026-08-09
**Plan item:** A-2 (admin-ux-and-summary-2026-08-09)
**Author:** specialist-architect
**Relates to:** ADR-0008 (`docs/decisions/ADR-0008-sticker-explicitness-tolerance.md` — Decisions 1, 3, 7, 8
extended below); `src/database/repositories/stickers.py` (`save_sticker()` upsert, `update_explicitness_score()`,
`_VISION_DERIVED_COLUMNS`, `clear_analysis()`); `src/services/modules/sticker/learning.py` (`learn()`
normal + duplicate-copy paths, `reanalyze()`, `StickerLearningResult`, `notify_admins()`);
`src/services/modules/sticker/tolerance.py` (`format_explicitness_line()`, shipped by A-1);
`src/bot/handlers/admin_sticker.py` (`_build_detail_text()`, `handle_sticker_detail`, `handle_run_analysis`,
`_resolve_default_tolerance_level()`); `src/bot/states/admin.py` (`AdminStates`);
`src/bot/handlers/admin_chat_panel.py` (`adm_pnl_tol:` FSM, ADR-0008 Decision 10 — the precedent this ADR
deliberately does *not* reuse, see Decision 7); downstream A-3 (backend-dev: migration + upsert + write
methods), A-4 (backend-dev: presets/manual-entry UI + reset + badge), Q-1 (qa: integration test).

---

## Context

Source plan (`docs/plans/admin-ux-and-summary-2026-08-09.md`, §1–2) asks for two things on top of
ADR-0008's tolerance-gating feature: show the score in every DM sticker card (A-1, done), and let an
admin set it by hand. The source plan names the blocking risk itself: `explicitness_score` is
overwritten unconditionally by `save_sticker()`'s upsert on every successful vision run (ADR-0008's own
docstring on that method says so explicitly — "this is the score of THIS analysis attempt … never a
value to preserve across re-analyzes"). Shipping a "set manually" button with no priority rule means the
next re-analysis (an admin action already exposed today, `adm_stk_reanalyze:`) silently erases it — the
plan calls this out verbatim: "Если ручную оценку затрёт следующий ре-анализ — фича бесполезна."

A-1 (done, commit `f5d376e`) already shipped the DM-card rendering and, along the way, made two decisions
under time/token pressure that this ADR must formally ratify or override: (1) the RU terminology pair
used in `format_explicitness_line()`, and (2) which chat's `tolerance_level` a catalog-wide DM view (no
specific chat in scope) compares against. A-1's own handoff note flags exactly this: *"Flagging for
architect: A-2's ADR should ratify or override this 'default vs real chat' split explicitly."*

This ADR fixes: the terminology (Decision 1, ratifying A-1), which chat ceiling catalog-wide views compare
against (Decision 2, ratifying A-1), the new persistence marker and its exact type/default (Decision 3),
the priority rule between a manual value and a fresh vision result — expressed as a single SQL-level
invariant, not a Python branch (Decision 4), what "reset to automatic" means operationally (Decision 5),
how the marker propagates through the existing duplicate-copy path (Decision 6), and the FSM state A-4
should build against (Decision 7). A-3 implements Decisions 3–6; A-4 implements Decision 7 plus the UI;
Q-1 tests Decisions 4–6.

---

## Decision 1 — Terminology: ratify Q1, already shipped by A-1, now the canonical record

Per-sticker value → **«оценка откровенности»**. Per-chat ceiling → **«уровень приличия»**. This is Julia's
own [Q1] answer ("Да, давай твою формулировку… зафиксировать в документации и в будущем следовать везде
ему") and is already live in `tolerance.py::format_explicitness_line()`, `_build_detail_text()`, and
`notify_admins()`. No code change follows from this decision — it exists so this pair has one canonical,
citable source instead of living only in a docstring and a PM handoff note. **Every future feature or
copy change touching either field must use these exact two terms** — not "уровень допустимости" (the
source plan's own informal working name before Q1 settled it), not a fresh synonym.

## Decision 2 — Ratify A-1's "default vs real chat" split for catalog-wide DM views

A-1's `_resolve_default_tolerance_level()` resolves `bot_config.default_tolerance_level` (if an admin set
one) else the `ChatConfig` dataclass fallback (`0.5`) for every DM view that isn't tied to one specific
chat — catalog browsing, DM sticker check, re-analyze result. Only `notify_admins()` (new-sticker
notification, which fires from inside `media.py`'s per-chat pipeline) threads the *real* originating
chat's `ChatConfig.tolerance_level`.

**Ratified, not overridden.** A catalog-wide view has no chat in scope by construction — there is no
"real" ceiling to show, and inventing one (e.g. the most-recently-viewed chat, or an arbitrary whitelist
entry) would be more misleading than a clearly-documented default, not less. Resolving the same two layers
`ChatConfigService` itself would resolve for a chat that never overrode anything is the least-surprising
choice available: it's the number every *new* chat would actually see today, and it changes in lockstep
if an admin later sets `bot_config.default_tolerance_level` from the defaults screen. No further action
for A-3/A-4 beyond keeping this split as-is.

## Decision 3 — New column: `sticker_knowledge.explicitness_is_manual BOOLEAN NOT NULL DEFAULT false`

Unlike `explicitness_score`/`tolerance_level` (ADR-0008 Decision 8: nullable, **no** SQL `DEFAULT`,
because those two participate in `ChatConfigService`'s three-layer merge, where a materialized default
permanently shadows a higher layer — migration 020's already-paid-for bug), this new column is a flat,
always-meaningful per-row fact with no merge semantics: every row either was or wasn't hand-set, and a
pre-migration row was, unambiguously, not. That is exactly the shape of `sticker_knowledge.analysis_failed
BOOLEAN NOT NULL DEFAULT false` (migration 001) and `quote_is_manual`'s *sibling reasoning* in migration
021 (though that one landed nullable because it's a rare-per-row optional fact, not a boolean that's
always-true-or-false for every row — ours is the latter, closer to `analysis_failed`). **`NOT NULL DEFAULT
false` is correct and safe here** — the migration-020 pitfall does not apply because `sticker_knowledge`
is not part of the `ChatConfig` three-layer merge; there is no higher layer for this column to shadow.

```sql
-- A-3, next migration after 025 → 026_sticker_explicitness_manual_flag.py
ALTER TABLE sticker_knowledge
  ADD COLUMN IF NOT EXISTS explicitness_is_manual BOOLEAN NOT NULL DEFAULT false;
```

One `op.execute()` statement (CLAUDE.md convention, already followed by every migration since 021).

## Decision 4 — Priority rule: a manual score is sticky across re-analysis, enforced in the upsert's SQL, not in Python

Change `save_sticker()`'s `ON CONFLICT … DO UPDATE` clause (`stickers.py:127-129`) from the current
unconditional overwrite:

```sql
explicitness_score = COALESCE(EXCLUDED.explicitness_score, sticker_knowledge.explicitness_score)
```

to a conditional that checks the existing row's own flag, not the incoming value:

```sql
explicitness_score = CASE
    WHEN sticker_knowledge.explicitness_is_manual THEN sticker_knowledge.explicitness_score
    ELSE COALESCE(EXCLUDED.explicitness_score, sticker_knowledge.explicitness_score)
END
```

`explicitness_is_manual` itself is **not named in the `SET` clause at all** — Postgres leaves an
unlisted column untouched on `UPDATE`, so every write that goes through `save_sticker()` (`learn()`'s
normal path, `reanalyze()`, the duplicate-copy path) leaves the flag exactly as it was. The flag can only
ever be set or cleared by the two dedicated methods in Decision 5 — a single source of truth for "who is
allowed to change manual status," matching how `update_explicitness_score()` is already the single
source of truth for "who is allowed to backfill a score outside the vision pipeline."

**Why SQL, not a Python `if is_manual: skip the write` branch in `learning.py`:** this is the same
reasoning ADR-0008 Decision 6 already used for the tolerance filter itself — the correctness property
must live at the one point every write path funnels through (`save_sticker()`'s single `INSERT … ON
CONFLICT`), not be re-derived at each of `learn()`'s two call sites plus `reanalyze()`. A Python branch
would also need its own read of the current row before deciding whether to pass a score at all, an extra
round-trip the `CASE` avoids for free.

**Interaction with the backfill script (ADR-0008 Decision 5) — no change needed.** The backfill script's
own target-set `WHERE … AND explicitness_score IS NULL` (`get_explicitness_backfill_candidates()`)
already structurally excludes any row with a non-`NULL` score, manual or automatic — a manually-scored
row can never appear in the backfill's target set in the first place, so `update_explicitness_score()`
(the backfill's writer) needs no `is_manual` awareness and this ADR does not touch it.

## Decision 5 — "Reset to automatic" means `NULL`, not "restore the last automatic value"

Two new repository methods, both narrow single-purpose `UPDATE`s in the same style as
`update_explicitness_score()`:

```python
async def set_manual_explicitness_score(self, file_unique_id: str, score: float) -> None:
    """Admin-set score (A-4). Sets explicitness_is_manual = true so save_sticker()'s
    upsert (Decision 4) protects it from the next re-analysis. Caller validates
    [0.0, 1.0] before calling — reject-not-clamp, same posture as ADR-0008 Decision 4
    — this method does not re-validate, matching update_explicitness_score()'s existing
    shape (repo methods trust the caller)."""
    ...  # UPDATE sticker_knowledge SET explicitness_score = $2, explicitness_is_manual = true,
        #                             updated_at = NOW() WHERE file_unique_id = $1

async def reset_explicitness_to_auto(self, file_unique_id: str) -> None:
    """Clears both the value and the flag (Decision 5) — NOT a revert to a
    remembered prior automatic value; there isn't one, a single column only
    ever holds the current value."""
    ...  # UPDATE sticker_knowledge SET explicitness_score = NULL, explicitness_is_manual = false,
        #                             updated_at = NOW() WHERE file_unique_id = $1
```

A single `explicitness_score` column has no room to remember "the automatic value before the admin
overrode it" — once overwritten there is nothing left to restore. Two shapes were available for "reset":

- **(a) Rejected — keep the number, just unlock it.** Flips `explicitness_is_manual` to `false` but
  leaves `explicitness_score` at the (still-manual) number until the *next* re-analysis happens to
  overwrite it. Rejected: a button labelled "reset to automatic" that visibly changes nothing until some
  future, unscheduled action is confusing, and leaves a technically-"automatic" score on screen that no
  vision run ever produced.
- **(b) Accepted — `NULL` both fields.** Matches ADR-0008 Decision 3's existing fail-closed NULL
  semantics exactly: the sticker becomes "не оценён" (excluded from every chat's candidates, shown as
  "not scored" in every DM card via the already-shipped `format_explicitness_line()`) until the admin
  taps the already-existing "🔄 Запустить заново" button to get a fresh vision score. No new UI state to
  design — reset reuses the same NULL rendering path A-1 already built, and reuses the existing re-analyze
  action for "I actually want a number now."

## Decision 6 — Duplicate-copy path (ADR-0008 Decision 7) must copy the flag together with the score, not the score alone

`_VISION_DERIVED_COLUMNS` (`stickers.py:13-26`) already lists `explicitness_score` as one of the columns a
duplicate sticker inherits verbatim from its canonical row at insert time (`learning.py:421`,
`copied = {col: canonical.get(col) for col in _VISION_DERIVED_COLUMNS}`). **Add
`explicitness_is_manual` to that same tuple.** This is a deliberate, non-obvious call: copying the score
value without also copying its manual status would silently reintroduce this ADR's exact bug one hop
away — a duplicate that copied a hand-vetted score would still get clobbered by *its own* first
re-analysis (`explicitness_is_manual` defaults `false` on a bare `INSERT`, so Decision 4's `CASE` would
not protect it). Copying both together is consistent with ADR-0008 Decision 7's own framing ("a
duplicate-matched sticker inherits the canonical row's `explicitness_score` … same as every other
vision-derived field" — this ADR treats manual-status as travelling with the value it describes, not as a
separate fact). The duplicate is fully mechanical: `_repo.get_by_file_unique_id(duplicate_of)` already
runs `SELECT *`, so no query changes are needed to make the new column visible to the `copied` dict;
`save_sticker()` gains one more keyword parameter (`explicitness_is_manual: bool = False`) threaded
through the same call site (`learning.py:448-466`) that already threads `explicitness_score`.

**Consequence worth stating so it isn't rediscovered as a surprise (mirrors ADR-0008 Decision 7's own
"accepted edge case" framing):** a freshly-learned duplicate can show the "(вручную)" badge (Decision 7 of
this ADR / A-4) on its very first admin notification, despite no admin having touched that specific file
yet — because it inherited both the score and the manual status from the sticker it's a copy of. This is
correct, not a bug: the admin's original manual judgment about *that image* still applies to a byte-for-
byte duplicate of it. If it's ever wrong for one particular duplicate, Decision 5's reset path already
covers "undo this and let the next re-analysis produce a fresh number."

**`StickerLearningResult` (`models.py`) needs the parallel field** — `explicitness_is_manual: bool =
False` — threaded through both `learn()` return paths (normal analysis: always `False`; duplicate-copy:
`copied["explicitness_is_manual"]`), because `notify_admins()` renders directly from this dataclass, not
from a fresh DB read, and per the paragraph above a duplicate's first notification can legitimately need
to show the badge. `ReanalyzeResult` needs no equivalent change: `handle_run_analysis()` already re-reads
the row from the repository after `reanalyze()` returns (`admin_sticker.py:643`), so
`updated.get("explicitness_is_manual")` is available there for free once the column exists.

## Decision 7 — A-4's FSM flow: use the unwired `awaiting_sticker_edit` scaffold state, not `awaiting_setting_value`

`AdminStates.awaiting_setting_value` (`src/bot/states/admin.py:13`) is **no longer the "grep-verified
unused" scaffold state** ADR-0008 Decision 10 found it to be — A-1's sibling plan already wired it to
`admin_chat_panel.py`'s `adm_pnl_tol:` per-**chat** tolerance FSM. Reusing it a second time for this
per-**sticker** score entry would put two unrelated flows behind one shared state, forcing the message
handler registered on that state to disambiguate by inspecting `state.get_data()` for a `chat_id` vs.
`file_unique_id` key — an avoidable coupling between two features that otherwise never interact.

**`AdminStates.awaiting_sticker_edit`** (`admin.py:16`, declared "Sticker management: waiting for new
description") is, by the same grep check ADR-0008 Decision 10 ran, currently **unused anywhere in `src/`
or `tests/`** — a second unwired scaffold, this time already sticker-scoped by name. This is A-4's
recommended state to build against: no new `State()` declaration, and no risk of colliding with the
description-merge reply flow (`handle_admin_sticker_reply`, gated `StateFilter(None)`, which never sets
this or any other state itself). **Not a hard mandate** — unlike ADR-0008 Decision 10, where reuse avoided
duplicating a soon-to-be-generic mechanism, there is no equivalent forcing reason here beyond tidiness; if
A-4 finds the existing name too easily confused with "edit the text description" (a real, different flow
on the same router), declaring a fresh `awaiting_sticker_score` state instead is an acceptable, purely
cosmetic alternative. Either way, mirror `admin_chat_panel.py`'s already-proven shape for this exact kind
of flow: validate `[0.0, 1.0]` before writing (reject-and-reprompt, not silent no-op — same posture as
ADR-0008 Decision 10's tolerance FSM), and a dedicated cancel callback (e.g. `adm_stk_expcancel:`,
mirroring `adm_pnl_tolcancel:`) that clears the state.

**Presets do not need the FSM at all.** A preset button (e.g. a small fixed set of values) is a direct
`callback_data` → range-free known-valid value → `set_manual_explicitness_score()` → re-render, with no
state transition — only the free-text "введите число" path needs `awaiting_sticker_edit` /
`awaiting_setting_value`-style validation. Do not route presets through the FSM; that would add a
round-trip with nothing to validate. Exact preset values, button copy, and badge wording (RU/EN) are
implementation/copy details left to A-4's discretion — same split this plan already uses elsewhere
(B-1/B-2 structure vs. B-3 copy) — not an architectural concern this ADR needs to pin down.

---

## Consequences

### Positive

- The priority rule (Decision 4) lives in exactly one place (`save_sticker()`'s upsert), the same
  precedent ADR-0008 Decision 6 already established for the tolerance filter — no risk of a second,
  drifting copy of the condition in `learning.py`.
- Decision 5's `NULL`-on-reset reuses A-1's already-shipped "не оценён" rendering path and the existing
  re-analyze action — zero new UI states for the reset outcome itself.
- Decision 6 closes a one-hop-removed reintroduction of the exact bug this ADR exists to prevent
  (a duplicate silently losing an inherited manual score on its own first re-analysis).
- Decision 7 avoids a needless coupling between the chat-level and sticker-level FSM flows that a naive
  "reuse `awaiting_setting_value` again" reading of ADR-0008 Decision 10 could have produced.
- `explicitness_is_manual`'s `NOT NULL DEFAULT false` (Decision 3) needs no backfill step: every
  pre-existing row is correctly `false` (no admin has ever set anything by hand yet) from the moment the
  column is added.

### Negative / Trade-offs

- One more boolean column and one more field on `StickerLearningResult` — accepted, mechanical, and
  narrowly scoped (mirrors ADR-0007's `duplicate_of` addition to the same dataclass).
- Decision 6 means a duplicate can carry a "(вручную)" badge for a file no admin has individually
  reviewed — accepted and documented so it isn't rediscovered as a surprise; Decision 5's reset path is
  the escape hatch if it's ever wrong for a specific duplicate.
- Decision 7 leaves two structurally-similar-but-separate single-field FSM flows in the codebase
  (`adm_pnl_tol:` for chat tolerance, whatever A-4 builds for sticker score) instead of one shared
  mechanism — accepted for the same reason ADR-0008 Decision 10 accepted its own FSM flow as a two-way
  door: cheap to delete in favor of a generic mechanism (F-1) if one ever lands, not worth generalizing
  now for two call sites.

---

## Rejected alternatives

### A: Keep the unconditional overwrite, add only a UI button, no priority rule

Rejected — this is the exact failure mode the source plan named verbatim ("фича будет бесполезной без
правила, которое её сохранит"): the very next re-analysis (already exposed today via
`adm_stk_reanalyze:`) would silently erase any manual value.

### B: Enforce the priority rule in Python (`learning.py`) instead of SQL

Rejected (Decision 4) — would require an extra read of the current row before every `save_sticker()` call
to decide whether to pass a score at all, and re-derives a correctness property `save_sticker()`'s single
upsert is better positioned to own, the same reasoning ADR-0008 Decision 6 already used for the tolerance
filter itself.

### C: "Reset to automatic" restores the number the last vision run produced

Rejected (Decision 5) — a single `explicitness_score` column has no second slot to remember a
pre-override automatic value; would require a new column purely to support undo, for a feature the source
plan only asked to "сбросить ручное значение обратно к автоматическому" (return control to the automatic
mechanism, not necessarily to a specific remembered number).

### D: Duplicate-copy inherits `explicitness_score` but not `explicitness_is_manual`

Rejected (Decision 6) — silently reintroduces this ADR's own bug one hop away: an inherited manual score
with the flag reset to `false` would be clobbered by the duplicate's own first re-analysis, exactly what
Decision 4 exists to prevent.

### E: Reuse `AdminStates.awaiting_setting_value` for A-4's FSM flow (mirroring ADR-0008 Decision 10 literally)

Rejected (Decision 7) — that state is no longer an unused scaffold; it now belongs to the per-chat
tolerance flow. A second, unrelated flow sharing it would force disambiguation by FSM-data shape between
two features (chat-level vs. sticker-level) that otherwise never need to know about each other.

---

## Implementation notes for A-3 (backend-dev)

1. Migration `026_sticker_explicitness_manual_flag.py` (`depends_on: 025`, sequential) per Decision 3.
2. `save_sticker()` (`stickers.py`): add `explicitness_is_manual: bool = False` parameter, thread into the
   `INSERT` values list; change the `ON CONFLICT … DO UPDATE`'s `explicitness_score` assignment to
   Decision 4's `CASE`; do **not** add `explicitness_is_manual` to the `SET` clause at all (Decision 4).
3. Add `explicitness_is_manual` to `_VISION_DERIVED_COLUMNS` (Decision 6) and thread
   `copied["explicitness_is_manual"]` through the duplicate-copy `save_sticker()` call
   (`learning.py:448-466`).
4. Two new repository methods per Decision 5: `set_manual_explicitness_score(file_unique_id, score)` and
   `reset_explicitness_to_auto(file_unique_id)`. Do not touch `update_explicitness_score()` (backfill
   script writer) — its existing target-set `WHERE` clause already structurally excludes manually-scored
   rows (Decision 4's closing paragraph).
5. Extend `StickerLearningResult` (`models.py`) with `explicitness_is_manual: bool = False`; set it on
   both `learn()` return paths per Decision 6's last paragraph.
6. `clear_analysis()` needs **no change** — it already leaves `explicitness_score` untouched (verified:
   its `SET` clause never names that column), which is already consistent with treating the score's
   lifecycle as independent of "was a full analysis attempted" (the same posture
   `update_explicitness_score()`'s own docstring states). No new interaction with
   `explicitness_is_manual` follows from this item.

## Implementation notes for A-4 (backend-dev)

1. Build against `AdminStates.awaiting_sticker_edit` (or a fresh, equally-scoped state) per Decision 7 —
   not `awaiting_setting_value`. Validate `[0.0, 1.0]` before calling
   `set_manual_explicitness_score()`, reject-and-reprompt on invalid input (mirrors
   `admin_chat_panel.py`'s `_TOLERANCE_INVALID` pattern), dedicated cancel callback that clears the state.
2. Preset buttons call `set_manual_explicitness_score()` directly on tap — no FSM involved (Decision 7,
   closing paragraph).
3. Reset button calls `reset_explicitness_to_auto()` (Decision 5) — re-render lands on the existing
   "не оценён" state, same as any other unscored sticker.
4. Extend `format_explicitness_line()` (`tolerance.py`, A-1) with an `is_manual: bool = False` keyword
   parameter and a visible marker (e.g. a parenthetical, RU+EN) when set — thread `sticker.get(
   "explicitness_is_manual")` / `updated.get("explicitness_is_manual")` through every call site A-1 already
   wired (`_build_detail_text()`, `handle_run_analysis()`'s post-reanalyze text) plus `notify_admins()`
   (Decision 6's duplicate-first-notification edge case). Exact badge copy is this item's own discretion,
   not pinned by this ADR.
5. New buttons attach to `sticker_detail_keyboard()` (`admin_sticker.py`'s keyboards module) alongside the
   existing "🔄 Запустить заново" / "🧹 Очистить анализ" rows; new callback prefixes should follow the
   established `adm_stk_{action}:{lang}:{params}` convention documented at the top of that file.

## Implementation notes for Q-1 (qa)

- Integration: a sticker with `explicitness_is_manual = true` survives `learn()`'s normal re-analysis path
  with its score unchanged (real Postgres row, real `save_sticker()` upsert — Decision 4's `CASE` is the
  one thing this test must pin, mirroring ADR-0008 D-4's own routing note about pinning the tolerance
  inequality direction against a real assertion point rather than a mocked repository).
- Integration: the duplicate-copy path inherits both `explicitness_score` and `explicitness_is_manual`
  together from a manually-scored canonical row (Decision 6), and that inherited-manual duplicate then
  *also* survives its own first re-analysis unchanged — the two-hop case the source plan's bug was really
  about.
- Unit or integration (either is fine): `reset_explicitness_to_auto()` clears both columns to
  `NULL`/`false` (Decision 5), and a subsequent `learn()` re-analysis on that now-unlocked row *does*
  overwrite the score with a fresh vision value — confirms reset genuinely re-opens the write path, not
  just flips a flag that nothing then reads correctly.
- Migration: `sticker_knowledge.explicitness_is_manual` is `NOT NULL DEFAULT false` (Decision 3) — assert
  schema directly, and assert every pre-migration row reads `false` (not `NULL`), the opposite polarity
  from ADR-0008 D-4's own migration assertions on the nullable-no-`DEFAULT` columns — worth a comment in
  the test noting the deliberate difference so a future reader doesn't "fix" it to match.

---

## Out of scope (this ADR and A-3/A-4/Q-1)

- Any change to the tolerance-gating comparison itself (ADR-0008 Decisions 1–2, 6) — this ADR only
  changes how `explicitness_score` gets written and by whom, never how it's read for candidate selection.
- A bulk "mark all as manual" or "reset all to auto" admin action — out of scope by the same ADR-0003
  boundary ADR-0008 Decision 5 already cites (no set-level bulk operations without first superseding that
  ADR); this item is single-sticker only, matching the source plan's own framing.
- Remembering more than one prior value per sticker (an audit history of manual overrides) — Decision 5
  rejects this as unneeded for what the source plan actually asked for ("сбросить… к автоматическому",
  not "see who set what and when").
- Exact preset values, button/badge copy, RU/EN wording — left to A-4's discretion (Decision 7), same
  structure/copy split this plan already uses for B-1/B-2 vs. B-3.

---

*Document generated as part of A-2 (admin-ux-and-summary-2026-08-09 plan).*
*Architect: specialist-architect (universal baseline).*
