# ADR-0008: Sticker explicitness/tolerance gating — score source, NULL policy, comparison direction, backfill

**Status:** accepted
**Date:** 2026-08-06
**Plan item:** D-1 (sticker-management-2026-08-06)
**Author:** specialist-architect
**Relates to:** `src/services/modules/sticker/learning.py` (vision prompt/parser), `src/database/repositories/stickers.py`
(candidate query, dedup-copy columns), `src/services/modules/sticker/responder.py` (candidate call sites),
`src/services/text/pipeline.py:198`, `src/bot/handlers/media.py:236,382`, `src/services/chat_config.py`
(three-layer merge), `src/models/chat_config.py`, `src/bot/settings_fields.py`, `src/bot/states/admin.py`;
ADR-0003 (`sticker_knowledge` derived-state philosophy, no-bulk-reanalyze scope boundary); ADR-0006
(chat-settings-panel registry/render architecture, `FieldType.FLOAT` read-only-until-F-1 convention);
ADR-0007 (`_VISION_DERIVED_COLUMNS` duplicate-copy list, which already named this field as "the next
candidate", `stickers.py:9-12`); `alembic/versions/020_rules_columns_drop_default.py` (precedent for the
nullable/no-SQL-DEFAULT pitfall this ADR must avoid repeating); downstream D-2 (backend-dev, migration +
prompt/parser + backfill script), D-3 (backend-dev, per-chat field + candidate filter + admin FSM), D-4
(qa, tests + live checklist)

---

## Context

Source plan (`docs/plans/sticker-management-2026-08-06.md`, §4) asks for a per-chat "decency floor": new
chats should not receive explicit stickers by default (`0.5`), while some chats ("своих", anarchy-style)
should receive everything (`1.0`). Julia's answer to the PM's [D-1] question is explicit about the
backfill mechanism: **(в) a one-off maintenance script**, not a UI button — the source brief separately
states the button path is forbidden ("Массовая переоценка старого каталога через интерфейс запрещена
прежним решением"), which is ADR-0003's own scope boundary (`analysis_status`/bulk-reanalyze-with-progress
is explicitly out of scope for anything that doesn't first supersede that ADR). The brief also states
the tolerance mechanism is deliberately **not** the abuse/antispam module ("не связано с антиспамом").

This item (D-1) fixes: the two new fields and their scale, the comparison direction between them, the
NULL policy (source plan: "до его прогона «оценки нет» = «скрыто»"), where the score comes from for new
vs. pre-existing stickers, the shape of the one-off backfill script, and — because D-3's own title asks
for "FSM админ-установки" while investigating the sibling `chat-settings-panel-2026-08-06` plan surfaced
a real blocker — how a per-chat float gets set by an admin at all, given that plan's generic FSM editing
of non-bool fields (F-1) is **deferred to a separate iteration**, not part of v1. D-2/D-3 implement
against these decisions; D-4 tests against them.

---

## Decision 1 — Two floats, same 0.0–1.0 scale, opposite roles, different tables

- `sticker_knowledge.explicitness_score FLOAT` — **per-sticker**, Vision-derived (Decision 4). `0.0` =
  completely normie/safe, `1.0` = maximally explicit. Nullable, **no SQL `DEFAULT`** (Decision 8).
- `chat_settings.tolerance_level FLOAT` — **per-chat**, admin-settable (Decision 10), merged through
  `ChatConfigService`'s existing three-layer merge (`src/services/chat_config.py:95-126`). `0.0` = admits
  nothing with any explicitness at all, `1.0` = anarchy, admits everything (Julia's "срачейка" example).
  Nullable, no SQL `DEFAULT` (Decision 8); `ChatConfig.tolerance_level: float = 0.5` is the layer-1
  fallback (Decision 8 explains why this is sufficient with no migration seed row).

Both fields share one scale on purpose — no unit conversion, no separate "how explicit is too explicit"
mapping table. This is the entire design: one number describes the sticker, one number describes the
chat's ceiling, and Decision 2 defines the single comparison between them.

## Decision 2 — Comparison direction: `tolerance_level` is a ceiling, named in exactly one place

A sticker is an eligible response candidate in a given chat **iff**:

```python
explicitness_score is not None and explicitness_score <= tolerance_level
```

`tolerance_level` is a **ceiling** on acceptable explicitness (raising it strictly admits a superset of
what a lower value admits), not a floor and not a minimum-required-spice knob. This direction is the one
fact D-4's own routing note names explicitly ("направление неравенства") as a thing to test — it is also
the single easiest line in this whole feature to accidentally flip during a refactor, so **do not inline
`<=` at each call site**. Give it one named function:

```python
# new module, e.g. src/services/modules/sticker/tolerance.py
def is_within_tolerance(explicitness_score: float | None, tolerance_level: float) -> bool:
    """A sticker is an eligible response candidate iff it has been scored
    AND its explicitness does not exceed the chat's ceiling. See ADR-0008."""
    return explicitness_score is not None and explicitness_score <= tolerance_level
```

D-3 wires the *equivalent* SQL predicate (Decision 6) for the hot path, but this Python function is what
D-4 pins the inequality direction against with a table of `(score, tolerance, expected)` cases — one
production-adjacent assertion point instead of re-deriving the direction from a raw SQL string in tests.

## Decision 3 — NULL policy: fail-closed is an explicit `IS NOT NULL` guard, not an emergent sentinel

`explicitness_score IS NULL` (unscored — either not-yet-analyzed or pre-migration catalog row, Decision 5)
excludes a sticker from every chat's candidate pool, **including a chat with `tolerance_level = 1.0`**.
This must be written as the explicit guard in Decision 2's comparison — never as "default unscored rows to
`0.0`" (would silently ship pre-migration content that happens to be explicit into brand-new strict
chats — exactly what Julia's requirement rules out) and never as "default to `1.0`" (coincidentally
correct for gating but semantically backwards — `1.0` is supposed to mean "verified fine even at the
loosest chat," not "unknown"). Two different failure states (unscored vs. verified-safe) must not collapse
onto the same sentinel value; `IS NULL` is the only representation that keeps them distinguishable and
matches the source plan's literal requirement verbatim.

`tolerance_level` cannot be `NULL` at read time: `ChatConfig.tolerance_level`'s dataclass default (`0.5`,
Decision 1) always fills the three-layer merge (`chat_config.py:107-126`) even when neither the
`bot_config` nor the `chat_settings` layer has a row — there is no code path that reads `tolerance_level`
before it is resolved to a concrete float. Only `explicitness_score` has a genuine, long-lived NULL state.

## Decision 4 — Source of the score for new analyses: one more field in the existing Vision JSON, zero extra calls

`_build_vision_prompt` (`learning.py:839-1069`) already asks for a `{"visual", "emotion", "contexts",
"tags", "character"}` JSON object in one Vision call per sticker. Add `"explicit": <0.0-1.0>` to that same
schema (prompt text near `learning.py:1060-1067`) and extend `_parse_vision_response`
(`learning.py:1156-1267`, both the direct-JSON path and the regex-fallback path used for truncated
responses, `learning.py:1192-1225`) with matching extraction. This costs nothing extra for new ingests or
admin-triggered re-analysis (`ReanalyzeResult`, `learning.py:515-522`) — same request, one more field in
the response.

**Validation policy — reject, don't clamp.** A non-numeric value, a missing key, or a value outside
`[0.0, 1.0]` must all resolve to `explicitness_score = None` (logged at `warning`, mirroring the existing
`logger.warning("Failed to parse vision JSON, trying regex fallback", ...)` pattern at `learning.py:1194`),
never silently clamped into range. Clamping a wildly-wrong value (e.g. the model returns `"7"` or a
percentage `"70"`) into `[0, 1]` manufactures a false sense of a real score; leaving it `NULL` keeps
Decision 3's fail-closed guarantee intact for exactly the case where the model's output can't be trusted.

## Decision 5 — Backfill for the existing catalog: a narrow, explicitness-only one-off script — not full re-analysis, not a UI button

Two independent constraints, both already decided outside this ADR, converge on the same shape:

1. **Not a UI button.** ADR-0003's scope boundary already forbids "set-level bulk re-analyze with progress
   tracking" without first superseding that ADR; Julia's own [D-1] answer picked the one-off-script option
   explicitly. This backfill does not supersede ADR-0003 because it is not a bulk re-analysis — it never
   re-derives `visual_description`/`emotion`/`style_tags`/`character_or_meme`/`description_embedding`, and
   it is not reachable from the bot at all (an out-of-band ops script, run manually).
2. **Not a full re-analysis, even out-of-band.** A full Vision re-run over every already-catalogued sticker
   (a) roughly doubles the Vision spend for the whole catalog for a need that is only "score one new axis"
   and (b) risks *perturbing* already-good, already-embedded descriptions — Vision is not perfectly
   reproducible run-to-run, so a full re-analysis is a correctness regression risk for a maintenance script
   whose only job is populating one column. This is the same "don't touch what already works" instinct
   ADR-0003 established for this table, applied to a script instead of a handler.

**Target set** (the rows that would otherwise become permanently invisible the moment Decision 3's filter
ships): `visual_description IS NOT NULL AND analysis_failed = false AND explicitness_score IS NULL` — this
is exactly the WHERE clause `search_by_embedding` already applies (`stickers.py:216-218`) minus the new
predicate, i.e. every row that is *today* an eligible response candidate and would silently stop being one
without a score. Rows with `analysis_failed = true` or `visual_description IS NULL` are already excluded
from candidate selection regardless of this feature — no reason to spend a Vision call scoring them.

**Mechanics:** for each target row, fetch the image via `bot.get_file` (the only way — raw bytes are not
persisted, per ADR-0007 Decision 8's own note on this) and run a *narrow*, single-field Vision prompt
("rate explicitness 0.0-1.0, respond with just the number" or an equally small JSON), not the full
`_build_vision_prompt`. For animated (`.tgs`) / video (`.webm`) stickers, reuse the **existing** render
path (`renderer.py`'s `sampled_frames[0]` / webm `t=0` anchor frame, ADR-0007 Decision 2) for the frame to
send — do not invent a second "which frame represents this sticker" answer. On success: `UPDATE
sticker_knowledge SET explicitness_score = $1 WHERE file_unique_id = $2` — **do not** touch
`analyzed_at`/`analysis_failed`/any other column; those retain their ADR-0003 meaning ("was a full
analysis attempted") and a score-only backfill is not that. On a per-row failure: log at `warning` and
continue (mirrors the log-and-continue pattern ADR-0003 already established for this table) — one bad
file must never abort the whole backfill run.

**No existing Python one-off-script convention exists in this repo** (`scripts/` currently holds only
shell scripts for DB backup: `backup-db.sh` etc.; the only `__main__` under `src/` is the bot entrypoint,
`src/main.py`). This is the first one. Give it its own file, `scripts/backfill_explicitness.py`, run
manually (`python -m scripts.backfill_explicitness` or equivalent), wiring `Bot`/`AIRouter`/
`StickerRepository`/the pool directly rather than through the bot's Dishka request scope (there is no
request to scope to). Log a final summary (rows scored / rows skipped / rows failed) — this script has no
admin-facing progress UI by design (Decision constraint 1 above), so the log is the only feedback channel,
matching ADR-0007 Decision 3's "the log is the only feedback loop" precedent for this same module.

## Decision 6 — Gating point: one SQL predicate on the existing candidate query, threaded through 3 call sites

Every bot-initiated sticker send already funnels through one repository method,
`StickerRepository.search_by_embedding` (`stickers.py:196-227`), reached via
`StickerLearningService.search()` (`learning.py:526-568`), reached from exactly two `StickerResponderService`
methods:

- `get_sticker_candidates()` (`responder.py:34-42`) ← called from `pipeline.py:198`
  (`_safe_get_sticker_candidates`, gated by `config.sticker_learning_enabled`) and from
  `media.py:236` (image-comment sticker, gated by `chat_config.image_comment_sticker_enabled`).
- `find_sticker_for_sticker_reply()` (`responder.py:44-66`) ← called from `media.py:382`
  (sticker-to-sticker reply, gated by `chat_config.sticker_reply_to_sticker_enabled`). Its own app-side
  loop (`responder.py:63-65`) only excludes the incoming sticker's own `file_unique_id` from an
  already-filtered result set — it is not a second tolerance check to add.

**All three call sites already hold the merged `ChatConfig`** (`config`/`chat_config` locals) at the point
they call into the responder — threading `tolerance_level: float` through is one new argument at each of
4 signatures (`search_by_embedding` → `search` → the two responder methods), not a new DB read anywhere.

**Filter in SQL, not after fetch.** Add `AND explicitness_score IS NOT NULL AND explicitness_score <= $N`
to the existing WHERE clause (`stickers.py:216-219`), keeping `ORDER BY ... LIMIT $3` semantics intact. An
app-side post-filter would return fewer than `limit` results (or thin the ranked list non-uniformly across
chats with different `tolerance_level`) — the SQL predicate is what keeps "top `limit` candidates" true
for every tolerance value, not just `1.0`.

## Decision 7 — Duplicate-copy consequence: a one-time copy, not a live join (cross-references ADR-0007 Decision 7)

`explicitness_score` joins `_VISION_DERIVED_COLUMNS` (`stickers.py:13-20`) — already flagged in the
comment right above it as "the next candidate" (`stickers.py:9-12`) — so a duplicate-matched sticker
(A-2's dedup path) inherits the
canonical row's `explicitness_score` at insert time, same as every other vision-derived field.

**Accepted, documented edge case:** if the canonical row predates this migration (`explicitness_score IS
NULL`), the new duplicate copies that `NULL` and is fail-closed-hidden too — consistent, not a bug (garbage
in, garbage out). But because the copy happens **once, at insert time**, backfilling the canonical row
later (Decision 5) does **not** retroactively update duplicates that already copied its `NULL` — they stay
unscored until an admin re-analyzes them individually or the backfill script is re-run and happens to
reach them directly (their own row also satisfies Decision 5's target-set WHERE clause once they have a
`visual_description`, which the copy path already gives them). Not a correctness bug — `duplicate_of_file_unique_id`
still records the relationship for anyone auditing why a row is stuck at `NULL` — but worth stating so it
isn't rediscovered as a surprise later.

## Decision 8 — Schema: nullable, **no SQL `DEFAULT`**, on both new columns — this exact mistake has already happened once

`alembic/versions/020_rules_columns_drop_default.py` documents a real, already-paid-for bug: `rules_mode`/
`rules_enabled` were added *with* a SQL `DEFAULT` on the `chat_settings` column, which meant every
per-chat row materialized a concrete value on first contact (`ensure_exists()`) and **permanently shadowed
`bot_config.default_*` for that field** — measured before the fix: 9/9 rows had it, against 1/9 for the
correctly-nullable `kb_enabled`. `default_rules_mode`/`default_rules_enabled` were dead settings for the
lifetime between migrations 008 and 020.

Both new columns must therefore be nullable with **no** `DEFAULT` clause:

```sql
-- D-2, next migration after 023 → 024_sticker_explicitness_score.py
ALTER TABLE sticker_knowledge
  ADD COLUMN IF NOT EXISTS explicitness_score FLOAT;   -- NULL = unscored, Decision 3

-- D-3, depends on D-2 → 025_chat_tolerance_level.py
ALTER TABLE chat_settings
  ADD COLUMN IF NOT EXISTS tolerance_level FLOAT;      -- NULL = inherit, Decision 1/8
```

No SQL `CHECK` constraint is required (Decision 4's reject-not-clamp app-level validation is the actual
gate for `explicitness_score`; `tolerance_level` is admin-set through the FSM in Decision 10, which can
validate range before writing) — a DB-level `CHECK (... BETWEEN 0 AND 1)` would be reasonable
defense-in-depth but is not required by anything in this plan; leave it to D-2/D-3's discretion rather than
mandating a schema convention with no other precedent in this project's migrations.

**No `bot_config.default_tolerance_level` seed row.** 9 of the 11 `legacy=False` fields in
`settings_fields.py` (e.g. `sticker_reply_to_sticker_chance: float = 0.5`, `models/chat_config.py:47`) rely
purely on the `ChatConfig` dataclass default with no seeded `bot_config` row — `get_defaults()`
(`bot_config.py:47-53`) simply returns nothing for that key until an admin uses the defaults screen (C-1,
done) to set one, and the dataclass default (`0.5`, matching Julia's literal ask) applies until then. This
is the simpler, already-proven pattern (vs. the two-legacy-field precedent of seeding a row at migration
time, which cost the migration-020 repair above) — `tolerance_level` should follow the majority pattern,
not the two-field exception.

**Pitfall D-3 must not miss:** adding the column is not enough — `tolerance_level` must also be added to
`_CHAT_CONFIG_FIELDS` (`chat_config.py:130-158`), the frozenset `_merge()` actually reads from. Forgetting
this makes both the `bot_config` and `chat_settings` layers silently inert for this field — every chat
reads the layer-1 dataclass default (`0.5`) forever, indistinguishable from "working as intended for a
chat that never overrode it," which is exactly the kind of silent-no-op this project's own migration 020
already had to repair once. `_coerce()` (`chat_config.py:161-168`) needs no new branch — `tolerance_level`
is a plain float, unlike `trigger_words`' array coercion.

## Decision 9 — Not routed through the abuse module

`src/services/abuse/*` (`filter.py`, `checker.py`) is a pattern/embedding filter over **incoming user
messages**, producing a `ResponseType` (`BLACKLISTED`/`COOLDOWN`/...) that decides whether the bot responds
to a message at all (`pipeline.py`'s early-return block, ~lines 150-172). It answers "is this *incoming*
message abusive/spammy." Tolerance gating answers a different question on a different axis: "is this
sticker the bot is about to *send* too explicit for this chat's own stated ceiling." One gates input, the
other gates a specific category of output; conflating them would make `abuse_filter_enabled` (a toggle a
chat can disable independently) accidentally control sticker content appropriateness too, which neither
the source brief nor any existing user-facing copy claims it does. This matches the source plan's own
explicit instruction ("не связано с антиспамом/антиабьюзом") and needs no new architecture to keep true —
Decision 6 already gates sticker selection at the repository/candidate layer, nowhere near `abuse/*`.

## Decision 10 — Admin sets `tolerance_level` via a minimal, dedicated FSM flow — does not wait on F-1

D-3's own title asks for "FSM админ-установки" (an admin flow to set the value per chat). The PM's D-3
note already flags a registry/FieldSpec dependency on the sibling `chat-settings-panel-2026-08-06` plan
("проверить, что соответствующий FieldSpec/registry приземлён") — investigating that plan's envelope
surfaces a more specific finding than "check it landed":

- **The registry/rendering half is already unblocked, today.** `src/bot/settings_fields.py` (A-1, done)
  already supports `FieldType.FLOAT`; two existing fields (`sticker_response_chance`,
  `sticker_reply_to_sticker_chance`) already render read-only on both the chat panel (B-1, done) and the
  defaults screen (C-1, done, `legacy=False` fields only) with no known gap. Adding
  `FieldSpec("tolerance_level", FieldGroup.STICKERS, ..., FieldType.FLOAT, legacy=False)` to
  `CHAT_SETTINGS_FIELDS` (`settings_fields.py:119-317`) needs **no** further work from the sibling plan to
  become visible (read-only) on both screens.
- **The one genuinely blocked piece is *editing*.** `settings_fields.py`'s own docstring
  (`settings_fields.py:36-39`) states FSM editing of non-BOOL fields is item **F-1**, and the sibling
  envelope shows F-1 `status: blocked`, deferred to "a separate iteration" by Julia's own decision in that
  plan, with `depends_on: [B-1]` but no landing estimate. Waiting on F-1 would leave D block's whole stated
  purpose — different `tolerance_level` per chat — undelivered for an unscoped amount of time.

**Decision: D-3 builds its own small, single-field FSM flow, independent of F-1.** A dedicated callback
(e.g. `adm_pnl_tol:{lang}:{chat_id}`, next to the existing `adm_pnl_tgl:` bool-toggle prefix from
ADR-0006 Decision 3) prompts for a numeric value and writes it through `chat_settings_repo.set_field()` +
`chat_config_service.invalidate(chat_id)`, exactly like every other per-chat write in that module. **Reuse
`AdminStates.awaiting_setting_value`** (`src/bot/states/admin.py:10`, labeled "Default settings: waiting
for text/array input") — grep-verified **unused anywhere in `src/` or `tests/`**, i.e. a scaffold state
declared (presumably in anticipation of F-1) and never wired to a handler. Repurposing it for this one flow
costs no new `State()` and does not expand this item's scope to "build F-1." Validate the input range
(`0.0`–`1.0`) before writing — same reject-not-clamp posture as Decision 4, and reject non-numeric input
with a re-prompt rather than a silent no-op.

**This is a two-way door.** If/when F-1 lands generically, this one flow can be deleted in favor of the
generic mechanism in the same change that lands F-1 — nothing here is designed to survive F-1 forever, it
exists only to not block D block on an unscoped, separately-decided deferral in a different plan.

---

## Consequences

### Positive

- Zero extra Vision cost for new/re-analyzed stickers (Decision 4) — one more JSON field on the call this
  feature was already going to make.
- The backfill script (Decision 5) is bounded in scope (one column, one small prompt) and cost (catalog is
  "hundreds, not tens of thousands" per ADR-0007's own sizing note) — it does not re-pay for the whole
  catalog's existing, working analysis.
- Decision 8's nullable/no-`DEFAULT` schema, chosen *because* this project already paid for the opposite
  mistake once (migration 020), avoids repeating a bug class with a known fix cost.
- Decision 10 unblocks D-3 without waiting on an unscoped sibling-plan deferral, while staying a genuinely
  two-way door if the generic mechanism lands later.
- Decision 6's SQL-level filter means `LIMIT`/ranking stays correct for every `tolerance_level`, not just
  the extremes.

### Negative / Trade-offs

- Legacy catalog rows stay invisible as response candidates (not "sent with a permissive default") until
  either the backfill script reaches them or an admin re-analyzes the sticker individually — accepted,
  this is the literal fail-closed behavior the source plan asked for, not a bug.
- A duplicate-matched sticker whose canonical row is later backfilled does not retroactively gain a score
  itself (Decision 7) — accepted as a documented, self-healing-on-next-full-backfill-pass edge case, not a
  correctness bug.
- Decision 10's dedicated FSM flow is a second small FSM mechanism alongside whatever F-1 eventually builds
  generically — accepted as bounded, single-field scope now, with an explicit "delete when F-1 lands"
  framing rather than an open-ended duplication.
- The backfill script (Decision 5) still needs live `Bot`/Telegram file access to run — same operational
  gap A-3's live checklist already named for this plan ("requires live bot + real Telegram file access QA
  doesn't have in this session"); it is a script Julia runs out of band, not something D-4 can fully
  exercise in CI.

---

## Rejected alternatives

### A: Route tolerance gating through `src/services/abuse/*`

Rejected (Decision 9): different axis (incoming message abuse vs. outgoing sticker appropriateness),
explicitly ruled out by the source brief, and would make an unrelated toggle (`abuse_filter_enabled`)
silently control sticker content.

### B: Treat `explicitness_score IS NULL` as `0.0` (safe) or `1.0` (always-hide) instead of an explicit guard

Rejected (Decision 3): `0.0` ships potentially-explicit legacy content into brand-new strict chats before
backfill runs — the exact opposite of the source plan's ask. `1.0` happens to gate correctly but collapses
"unknown" and "verified maximally explicit" onto the same value, which is misleading the moment anyone
reads the column and loses the fail-closed reasoning behind it.

### C: Full catalog re-analysis (not narrow, explicitness-only) for the backfill

Rejected (Decision 5): roughly doubles Vision spend for the whole catalog and risks perturbing
already-good, already-embedded descriptions for a script whose only job is one new column.

### D: Admin-facing bulk-reanalyze / "rescan for explicitness" button

Rejected (Decision 5, per ADR-0003's existing scope boundary and Julia's own [D-1] answer): would require
first superseding ADR-0003's explicit "no set-level bulk re-analyze with progress tracking" boundary for a
need the source plan already answered a different way.

### E: App-side post-filter on `search()`'s results instead of a SQL predicate

Rejected (Decision 6): breaks `LIMIT`/ranking semantics — a chat with a stricter `tolerance_level` would
silently get fewer, non-uniformly-thinned candidates instead of the same top-`limit` search re-ranked
within its own ceiling.

### F: Build F-1 (generic FSM editing of all non-BOOL fields) now, inside this item, to unblock D-3 cleanly

Rejected (Decision 10): F-1 is a separately-scoped, larger effort (5h, covers `system_prompt`,
`trigger_words`, all chances/intervals, `language`, `rules_mode` with range/format validation per field)
that Julia already explicitly deferred to a separate iteration in the sibling plan — building it here would
roughly double D-3's estimated scope for a decision that was already made, elsewhere, not to build it yet.

---

## Implementation notes for D-2 (backend-dev)

1. Migration `024_sticker_explicitness_score.py` per Decision 8 (nullable, no `DEFAULT`, no backfill in
   the migration itself — the script is separate, Decision 5).
2. `_build_vision_prompt` (`learning.py:1060-1067`) + `_parse_vision_response` (`learning.py:1156-1267`,
   both the direct-JSON and regex-fallback branches): add `"explicit"` per Decision 4's reject-not-clamp
   validation. Extend `StickerLearningResult` (`models.py`) with `explicitness_score: float | None = None`,
   same shape as ADR-0007's `duplicate_of` addition.
3. Add `explicitness_score` to `_VISION_DERIVED_COLUMNS` (`stickers.py:13-20`) — Decision 7's copy
   consequence is then automatic, no new code path needed in the dedup-copy function.
4. New `scripts/backfill_explicitness.py` per Decision 5's target-set WHERE clause and mechanics
   (narrow prompt, existing render-path frame selection, log-and-continue per row, final summary log).

## Implementation notes for D-3 (backend-dev)

1. Migration `025_chat_tolerance_level.py` (`depends_on: D-2`, sequential number) per Decision 8.
2. Add `tolerance_level` to **both** `_CHAT_CONFIG_FIELDS` (`chat_config.py:130-158`, Decision 8's named
   pitfall) and `ChatConfig` (`models/chat_config.py`, dataclass default `0.5`).
3. Add `FieldSpec("tolerance_level", FieldGroup.STICKERS, ..., FieldType.FLOAT, legacy=False)` to
   `CHAT_SETTINGS_FIELDS` (`settings_fields.py`) — already renders read-only on both existing screens with
   no further sibling-plan work (Decision 10).
4. Thread `tolerance_level: float` through `search_by_embedding` → `search()` →
   `get_sticker_candidates()` / `find_sticker_for_sticker_reply()` (Decision 6), reading it from the
   `ChatConfig` already in scope at all 3 call sites (`pipeline.py:198`, `media.py:236`, `media.py:382`) —
   no new DB read.
5. Dedicated FSM flow reusing `AdminStates.awaiting_setting_value` (Decision 10) — validate `[0.0, 1.0]`
   before writing, reject-and-reprompt on invalid input, `invalidate(chat_id)` after write (matches every
   other panel write path per ADR-0006 Decision 3).

## Implementation notes for D-4 (qa)

- Unit: `is_within_tolerance()` (Decision 2) against an explicit `(score, tolerance, expected)` table
  including the boundary (`score == tolerance` → included) and the direction-inversion negative control
  (a naive `>=` would pass a "same file twice" style test suite but fail this table).
- NULL fail-closed: a sticker with `explicitness_score IS NULL` must be excluded even at
  `tolerance_level = 1.0` — integration-level, against a real Postgres row, not just the Python helper.
- Migration: `chat_settings.tolerance_level` and `sticker_knowledge.explicitness_score` both nullable, no
  `DEFAULT` — assert schema directly (mirrors migration 020's own verification shape) so this specific,
  previously-real bug class can't silently recur.
- Default seed: a brand-new chat (`chat_settings` row absent or `tolerance_level` NULL) resolves to `0.5`
  through the full `get_config()` path, not just the dataclass default in isolation.
- Three-layer merge: `bot_config.default_tolerance_level` set → per-chat override still wins when present;
  per-chat NULL → global default applies; both absent → `0.5`.
- End-to-end gating: a sticker scored `0.6` is excluded from a `tolerance_level = 0.5` chat's candidates
  and included in a `tolerance_level = 1.0` chat's candidates, via the real `search_by_embedding` SQL path
  (Decision 6), not a mocked repository.
- Live checklist (deferred to Julia, same convention as A-3/B-2/C-2): running `scripts/backfill_explicitness.py`
  against a handful of real catalog stickers and confirming scores land in a sane range for known
  safe/explicit examples — this needs live bot/Telegram file access QA does not have in this session
  (Decision 5's own named gap).

---

## Out of scope (this ADR and D-2/D-3/D-4)

- Any admin UI action that triggers bulk re-analysis of the existing catalog (Decision 5/D — ADR-0003's
  boundary, unless separately superseded).
- Routing tolerance gating through `src/services/abuse/*` (Decision 9/A).
- Generic FSM editing of every non-BOOL settings field (F-1's scope, sibling plan, deferred by Julia) —
  Decision 10 builds only the one field-specific flow this item needs.
- A DB-level `CHECK` constraint on either float column (Decision 8 — left to D-2/D-3's discretion, not
  mandated).
- Retroactively re-scoring duplicate rows whose canonical sticker gets backfilled after the copy already
  happened (Decision 7's accepted edge case) — a new, separately-scoped follow-up if it turns out to
  matter in practice.

---

*Document generated as part of D-1 (sticker-management-2026-08-06 plan).*
*Architect: specialist-architect (universal baseline).*
