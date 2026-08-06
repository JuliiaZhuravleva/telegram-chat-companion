# ADR-0007: Sticker duplicate detection — perceptual hash before Vision, canonical-copy schema

**Status:** accepted
**Date:** 2026-08-06
**Plan item:** A-1 (sticker-management-2026-08-06)
**Author:** specialist-architect
**Relates to:** `src/services/modules/sticker/learning.py`, `src/services/modules/sticker/renderer.py`,
`src/services/modules/sticker/motion.py`, `src/database/repositories/stickers.py`,
`alembic/versions/005_sticker_knowledge.py`; ADR-0003 (`sticker_knowledge` derived-state
philosophy, admin re-analyze override); downstream A-2 (backend-dev, migration + ingest),
A-3 (qa, tests + live checklist)

---

## Context

Source plan (`docs/plans/sticker-management-2026-08-06.md`, §1) names three stickers already
duplicated in the catalog (`AgAD6xIAAv3NMUs`, `AgAD_xEAAsJVUEo`, `AgADzioAAuSTIEs`) — the same
picture re-uploaded (typically via a different sticker pack) ends up as a distinct
`file_unique_id` row, paying for a fresh Vision analysis + embedding call and producing a
second, possibly inconsistent, description for artwork the bot already knows.

Julia's answer to the PM's [A-1] question (`human_feedback`, ts `13:21:50Z`) is explicit about
scope: the **primary** deliverable is a cheap image-hash check *before* the Vision call; a
"compare by meaning, after analysis" semantic layer is **future work, not this plan** (also
stated directly in the source brief, "Не входит в этот план"). This ADR covers only the
pre-Vision hash path.

This ADR fixes: the hash algorithm and its dependency footprint, the frame each sticker type
hashes, the similarity threshold, the DB schema for the canonical copy, and the matching
strategy. A-2 implements against these decisions; A-3 tests against them.

---

## Decision 1 — Hash algorithm: 64-bit dHash, Pillow only, no new dependency

Use a **difference hash (dHash)**: grayscale, flatten transparency onto white, resize to 9×8,
compare each pixel to its right-hand neighbor row-wise → 64 bits → hex-encoded 16-char string.

```python
# src/services/modules/sticker/dedup.py (new module)
from PIL import Image
import io

def compute_image_hash(image_bytes: bytes) -> str:
    """64-bit difference hash (dHash), hex-encoded, Pillow-only.

    Robust to Telegram's WEBP re-encoding / minor recompression artifacts.
    NOT robust to crops, rotations, or mirrored art — by design, see Decision 3
    (the threshold is deliberately tight; those cases fall through to a normal,
    slightly wasteful, but still-correct Vision call).
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    # Flatten transparency onto a fixed background so two exports of the same
    # picture with different transparent-pixel RGB padding still hash identically
    # — same alpha_composite-onto-canvas idiom already used for collage frames
    # (renderer.py's _create_motion_trail_frame / _composite_collage).
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(bg, img).convert("L")
    small = flat.resize((9, 8), Image.Resampling.LANCZOS)
    px = small.load()  # PixelAccess, not .getdata() — avoids the Pillow 14 deprecation
    bits = "".join(
        "1" if px[col, row] > px[col + 1, row] else "0"
        for row in range(8)
        for col in range(8)
    )
    return f"{int(bits, 2):016x}"


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Bit-difference count between two hex-encoded 64-bit hashes."""
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
```

**Why dHash, and why this satisfies "no external dependency" (the goal the source brief states
for item C's motion library, which the PM's [A-1] phrasing extends to this item's hash
choice):**

- `Pillow>=10.0.0` is already a project dependency (`pyproject.toml`); dHash needs nothing
  beyond `Image.open/convert/resize/getdata`, all present in Pillow's core API. No `numpy`,
  no `imagehash`/`ImageHash` package.
- Rejected **pHash (DCT-based)**: mathematically stronger against rotation/scaling, but a
  real DCT implementation needs `numpy`/`scipy` (or a dependency like `imagehash`, which itself
  depends on `numpy`) — the exact "new library" tradeoff the source brief asks to avoid unless
  necessary. Telegram stickers aren't rotated/scaled between re-uploads; dHash's weaker
  invariance is not a real gap here.
- Rejected **average hash (aHash)**: same dependency footprint as dHash (Pillow-only), but
  more sensitive to uniform brightness/contrast shifts from re-encoding; dHash's relative
  comparison is the better default at equal cost.
- Rejected **cryptographic hash (SHA-256) of the raw bytes**: only catches byte-identical
  files. Telegram re-encodes WEBP on upload in ways that can change bytes without changing the
  picture, and the whole point of "similarity threshold" (explicitly named in the item title)
  is graceful tolerance of that — a crypto hash has no notion of "close enough," only equal/not
  equal.

## Decision 2 — Hash source frame per sticker type

The hash must be computed from a single, deterministic frame — never from the Vision collage
(`renderer.py`'s `_composite_collage`), which draws timestamp/motion-score text labels over the
image and would corrupt the hash with label pixels that have nothing to do with the artwork.

- **Static**: hash the raw `image_data` (webp bytes) directly. No render step involved — this
  is the cheapest case and requires no changes to `renderer.py`.
- **Animated (.tgs)**: reuse `sampled_frames[0]` from `_render_tgs_sync`
  (`renderer.py:227-237`) — it is already `anim.render_pillow_frame(frame_num=0)` because the
  sampling loop starts at `frame_num=0` (`range(0, total_frames, sampling)`). **Zero extra
  render cost.** Frame 0 is derived purely from the Lottie JSON, so it is bit-for-bit
  deterministic for identical input — no dependency on the (separately noisy) motion-peak
  selection.
- **Video (.webm)**: **do not** reuse `frames[0]` from `render_webm`'s motion-selected
  keyframes. Those are chosen at `keyframe_times[i]`, which are motion-*peak* timestamps, not
  `t=0` — and a re-encoded/re-uploaded copy of the same video can shift the motion-score curve
  slightly, causing `keyframe_times[0]` to land at a different moment in the two copies even
  though the underlying video is the same. That would compare two genuinely different frames
  and defeat the whole check. Instead, extract one dedicated anchor frame at `timestamp=0.0`
  via the existing `ffmpeg -ss` single-frame extraction pattern (`renderer.py:353-374`), before
  motion analysis runs. Cost: one extra `ffmpeg` subprocess call (~15-50 ms) — negligible next
  to the Vision API round-trip this check exists to avoid.

**A-2 action:** add a `hash_frame: bytes` (PNG) field to `RenderedSticker`
(`renderer.py:31-45`), populated per the rules above, so `learning.py` never needs to know
which sticker type produced it.

## Decision 3 — Similarity threshold: Hamming distance ≤ 4 of 64 bits, biased toward false negatives

`DEDUP_HAMMING_THRESHOLD = 4` (named constant in `dedup.py`). Two images are treated as "the
same picture" only below this distance.

**Why conservative, and why the asymmetry is deliberate:** the two failure directions have very
different costs.

- **False negative** (a real duplicate is missed): costs one avoidable Vision + embedding call.
  Wasteful, not wrong — the sticker still gets a correct, independent description.
- **False positive** (two different stickers are merged): the new sticker silently gets
  *someone else's* description. This is a user-visible correctness bug, and — per ADR-0003 —
  the only recovery path is the admin noticing and tapping "🔄 Запустить заново" on that
  specific sticker. There is no bulk detection sweep for wrongly-merged rows.

Given that asymmetry, bias toward missing dedups rather than risking wrong merges: 4/64 bits
(~94% block agreement at 8×8 resolution) is a widely-used "near-identical, different
compression" threshold in dHash literature; it does not attempt to catch crops, palette swaps,
or genuinely similar-but-different art from the same pack (Decision 8 below covers exactly this
risk for animated packs with a shared idle pose).

Log every match at `info` level with both `file_unique_id`s and the measured distance — this
plan has no telemetry/tuning UI, so the log is the only feedback loop if the threshold needs
revisiting later.

## Decision 4 — Schema: `image_hash` + `duplicate_of_file_unique_id`

New migration (next number after `022_observability_log.py` → **`023_sticker_dedup_hash.py`**):

```sql
ALTER TABLE sticker_knowledge
  ADD COLUMN image_hash CHAR(16),                       -- hex dHash; NULL = not computed
  ADD COLUMN duplicate_of_file_unique_id VARCHAR(255)
    REFERENCES sticker_knowledge(file_unique_id) ON DELETE SET NULL;

CREATE INDEX idx_sticker_knowledge_duplicate_of
  ON sticker_knowledge(duplicate_of_file_unique_id)
  WHERE duplicate_of_file_unique_id IS NOT NULL;
```

- `image_hash CHAR(16)`, nullable. `NULL` for every row created before this migration (no
  backfill — see Decision 8) and for any row where hashing failed (fail-open, Decision 5).
  No index on `image_hash` itself: matching is an app-side Hamming scan (Decision 5), not an
  equality lookup, so a btree index on this column would not be used by the matching query.
- `duplicate_of_file_unique_id`, nullable self-FK, `ON DELETE SET NULL` (if the canonical row
  is ever deleted, duplicates keep their already-copied description; they don't need the
  pointer to remain functional — see Decision 7). Indexed because "how many duplicates point at
  X" is a natural future admin/debug query and the column is free to index now.

## Decision 5 — Matching strategy: app-side O(N) Hamming scan, no new index

On every non-exact-match ingest: `SELECT file_unique_id, image_hash, created_at FROM
sticker_knowledge WHERE image_hash IS NOT NULL AND visual_description IS NOT NULL AND
analysis_failed = false`, then compute `hamming_distance()` against each row in Python
(`dedup.py`, DB-agnostic, unit-testable without Postgres).

Rejected a bit-level Postgres query (`bit(64)` XOR + `bit_count()`, PG14+) or a
`pg_trgm`/extension-based approach: migration 005's own sizing comment ("small initial
dataset," `ivfflat lists=10`) describes a catalog of hundreds, not tens of thousands, of rows.
A full Python scan over a few hundred 64-bit ints is microsecond-scale and adds no new Postgres
extension or index type — consistent with ADR-0003's precedent of not building infrastructure
for a scale the project isn't at. **Revisit trigger:** if the catalog passes roughly 5,000 rows
*and* this scan is visibly on `learn()`'s critical path (no instrumentation required now; a
manual check is enough if `learn()` starts feeling slow).

## Decision 6 — Canonical selection: closest hash wins, oldest breaks ties, chain flattens to root

Among candidates within the threshold, pick the one with the **smallest Hamming distance**;
tie-break by **oldest `created_at`** (first-seen wins). Oldest-first is deterministic and
auditable; a "most `total_uses`" tie-break was considered and rejected because usage counters
change continuously, which would make canonical selection non-reproducible between two
otherwise-identical dedup runs.

If the matched candidate itself already has `duplicate_of_file_unique_id` set (it is itself a
previously-detected duplicate), resolve to *its* target instead — always point at the root,
never build a multi-level chain. This keeps "how many duplicates does canonical X have" a
single-hop query.

## Decision 7 — What gets copied, what doesn't, and two concrete pitfalls for A-2

Copy only the **vision-derived** columns from the canonical row onto the new row. Keep a single
named list next to the copy function so it stays visible when new vision-derived columns are
added later (per the item's "будущие vision-поля обобщённо" requirement — this is a
low-ceremony way to satisfy it without building a registry/decorator abstraction for a single
call site, mirroring ADR-0006 Decision 1's "no abstraction for one current caller"):

```python
# src/database/repositories/stickers.py, near save_sticker()
_VISION_DERIVED_COLUMNS = (
    "visual_description",
    "original_vision_description",
    "emotion",
    "suggested_contexts",
    "style_tags",
    "character_or_meme",
    "description_embedding",
)
# NOTE: extend this tuple whenever a new Vision-derived column lands
# (D-2's explicitness_score, when it ships, is the next candidate).
```

**Not copied** — stay specific to the new Telegram object: `file_id`, `set_name`, `emoji`,
`is_animated`, `is_video`, `usage_contexts` (this row's own usage log starts empty),
`admin_notes` (per-row).

Two pitfalls A-2 must handle explicitly, not by blindly copying the tuple above verbatim:

1. **Format auto-tag.** `learning.py:206-209` appends `"animated"`/`"video"` to `style_tags`
   based on *this* sticker's own type. If a duplicate match happens to be cross-type (e.g. a
   static re-post of the same picture matches an animated canonical, or vice versa — the dHash
   only looks at one still frame, so this is possible), copying `style_tags` verbatim would
   carry over the wrong format tag. Re-apply the auto-tag step to the copied `style_tags` using
   the *new* sticker's own `sticker_type`, exactly as the existing Vision path already does.
2. **`analyzed_at` semantics.** `StickerRepository.save_sticker()`'s existing SQL
   (`stickers.py:60-84`) sets `analyzed_at = NOW()` whenever a non-null `visual_description` is
   written — it cannot distinguish "Vision just ran" from "we copied a description." This ADR
   does **not** ask for a new parameter/method to special-case that distinction: it costs a new
   code path for a cosmetic difference, and `duplicate_of_file_unique_id IS NOT NULL` is already
   the authoritative signal for "how did this row get its description" (ADR-0003's derived-state
   philosophy: don't add a column that duplicates what's already derivable). Accept `analyzed_at
   = NOW()` on copy as a documented consequence, not a bug.

Extend `StickerLearningResult` (`models.py:13-25`) with `duplicate_of: str | None = None`, set
on the copy path. This gives A-3 a clean typed assertion point (no DB introspection needed in
unit tests) and costs nothing — `notify_admins()` already fires unconditionally for any
`is_new and not analysis_failed` result (`media.py:356`), so duplicates get the existing
"new sticker" admin notification for free with no changes to the notification path.

## Decision 8 — No backfill of the existing catalog; the three named example stickers are a hash-correctness check, not a merge target

The three stickers named in the source brief (`AgAD6xIAAv3NMUs`, `AgAD_xEAAsJVUEo`,
`AgADzioAAuSTIEs`) are **already** persisted with separate rows and separate descriptions. This
ADR does not add a one-off backfill script (unlike D-1's explicit backfill decision for
`explicitness_score`) — the source plan and the PM's [A-1] answer both frame this item purely
as "before Vision, going forward" (economics of *future* ingests), with no backfill requirement
stated. Adding one would expand A-2 past its estimated 5-6h for a need nobody asked for.

**Consequence, stated explicitly so A-3's live checklist isn't misread:** re-sending those three
specific stickers to the bot today will **not** retroactively merge them — their rows predate
`image_hash` and stay `NULL` forever unless someone re-triggers analysis on them individually.
A-3's live checklist against these three should verify the **hash algorithm's real-world
correctness** (download each pair via `bot.get_file`, run `compute_image_hash` on both offline,
confirm the Hamming distance is at/under the threshold — proving the mechanism would have caught
them had it existed at ingest time), not that the bot merges them now. If retroactive merging of
the existing catalog is wanted later, that is a new, separately-scoped backfill item — flag as
backlog, don't build it here.

---

## Consequences

### Positive

- Zero new dependencies; `Pillow` (already required) is sufficient.
- Zero added Postgres infrastructure (no new index type, no extension).
- Both Vision *and* embedding-generation costs are avoided for a detected duplicate — the item
  title asks for reusing the embedding too, and the full-column copy (Decision 7) covers it in
  one write.
- `duplicate_of_file_unique_id` gives the future semantic-comparison work (explicitly deferred)
  a ready-made "these were already known to be the same picture" seed set to build on rather
  than starting cold.
- The false-positive-averse threshold (Decision 3) means a wrong bug here degrades to "one
  sticker briefly has someone else's description until an admin notices and re-analyzes" —
  never a data-loss or safety issue.

### Negative / Trade-offs

- Video (.webm) dedup pays one extra `ffmpeg` call per non-static ingest that isn't already an
  exact `file_unique_id` match (Decision 2) — accepted, still far cheaper than a Vision call.
- The catalog predating this migration (including the three named example stickers) is not
  retroactively deduplicated (Decision 8) — accepted, explicitly out of scope, revisit as a
  separate backlog item if wanted.
- A cross-type false match (static picture vs. an animated/video sticker sharing one similar
  frame) is possible in principle (Decision 7, pitfall 1) — mitigated by the tight threshold,
  not eliminated by design. Two genuinely different animated stickers from the same pack that
  happen to share a similar idle/starting pose are the sharpest realistic version of this risk;
  A-3's test plan should include this case as an adversarial negative control (two *different*
  stickers, not two copies of one file), not only a positive "same file twice" control.
- App-side O(N) scan (Decision 5) doesn't scale indefinitely — acceptable now, flagged with an
  explicit revisit trigger rather than pre-built for a catalog size that doesn't exist yet.

---

## Rejected alternatives

### A: Semantic/description-based comparison (compare Vision output text/embeddings post-analysis)

Rejected for this item **by the PM's own decision**, not an architectural call: explicitly
named "future extension, not in this plan" in the source plan and Julia's [A-1] answer. Would
also not save the Vision call this item exists to avoid — comparing *after* analysis has already
paid for it once.

### B: Add `imagehash` (or similar perceptual-hash library) as a new dependency

Rejected: Decision 1's Pillow-only dHash achieves the same near-duplicate tolerance at zero new
supply-chain surface, satisfying the brief's "без внешних зависимостей" framing.

### C: Postgres-side Hamming distance via `bit(64)` + `bit_count()` (PG14+)

Rejected for now (Decision 5): no measured performance need at current catalog size; adds
query complexity (casting hex to `bit(64)`, a functional index) for a scale the project isn't
at. Two-way door — revisit if the catalog grows.

### D: Backfill existing catalog with hashes + retroactive merge

Rejected (Decision 8): not requested by the plan or the PM's answer; would expand A-2's scope
for a need that can be a separately-scoped follow-up if it turns out to matter in practice.

---

## Implementation notes for A-2 (backend-dev)

1. New module `src/services/modules/sticker/dedup.py`: `compute_image_hash()`,
   `hamming_distance()`, `DEDUP_HAMMING_THRESHOLD = 4`, and a pure orchestration helper (e.g.
   `find_duplicate(target_hash, candidates: list[tuple[str, str, datetime]]) -> str | None`)
   implementing Decision 6's selection/tie-break/chain-flatten logic — keep it DB-free so A-3
   can unit test it without Postgres.
2. Migration `023_sticker_dedup_hash.py` per Decision 4.
3. `RenderedSticker` (`renderer.py`) gains `hash_frame: bytes` per Decision 2 (static needs no
   renderer change — `learning.py` hashes `image_data` directly for that branch).
4. `StickerRepository`: add the candidate-fetch query (Decision 5's WHERE clause) and a copy
   path using `_VISION_DERIVED_COLUMNS` (Decision 7) — either a new `save_duplicate_sticker()`
   method or a parameterization of `save_sticker()`; either is fine, but do not let the two
   copy-vs-analyze code paths diverge in what they write to `total_uses`/`last_used_at`
   (INSERT semantics should stay identical to the existing new-sticker path).
5. `learning.py:learn()` insertion point: after the existing exact-`file_unique_id` shortcut
   (unchanged) and after render (for non-static types — reuses `hash_frame`, no reordering of
   the render step itself), before building the Vision prompt. On a hit: skip straight to the
   copy-and-save path, populate `StickerLearningResult.duplicate_of` (Decision 7), return
   without calling `self._ai.analyze_image` or `_generate_and_store_embedding`.
6. `StickerLearningResult` (`models.py`): add `duplicate_of: str | None = None`.

## Implementation notes for A-3 (qa)

- Unit-test `dedup.py` in isolation (no DB, no Telegram): known-identical images → distance 0;
  known-different images → distance well above threshold; the two adversarial cases from
  Consequences (transparent-padding color difference; two different stickers sharing a similar
  pose) as explicit negative/positive controls, not just "same file twice."
- Integration: confirm `_ai.analyze_image` / `_generate_and_store_embedding` are **not** called
  on a detected duplicate (mock/spy assertion, per the item title's "Vision не вызывается на
  дубле").
- Live checklist against the three named stickers: verify per Decision 8 — hash-correctness
  proof via offline download + `compute_image_hash`, not an in-product merge assertion.

---

## Out of scope (this ADR and A-2/A-3)

- Semantic/description-based duplicate comparison after Vision analysis (Decision A / source
  plan "Не входит в этот план").
- Any new external dependency for hashing (Decision 1/B).
- Backfilling `image_hash` for pre-existing catalog rows, or retroactively merging the three
  named example stickers (Decision 8/D).
- Admin-facing UI/notification changes distinguishing "new" from "duplicate-detected" stickers
  beyond the existing generic new-sticker notification (Decision 7's `duplicate_of` field is
  available for a future notification variant, but wiring one is not required here).

---

*Document generated as part of A-1 (sticker-management-2026-08-06 plan).*
*Architect: specialist-architect (universal baseline).*
