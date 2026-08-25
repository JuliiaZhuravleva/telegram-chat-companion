# ADR-0010: Chat settings panel — grouped navigation schema (screen-per-group, back, "where am I") — ADR-0006 addendum

**Status:** accepted
**Date:** 2026-08-09
**Plan item:** B-1 (admin-ux-and-summary-2026-08-09)
**Author:** specialist-architect
**Relates to:** ADR-0006 (`docs/decisions/ADR-0006-chat-settings-panel-architecture.md` — Decisions 1–4,
extended below, none overridden); `src/bot/handlers/admin_chat_panel.py` (`render_chat_panel`,
`handle_chat_panel_toggle`, `adm_pnl_tol:` FSM); `src/bot/keyboards/admin_chat_panel.py`
(`chat_panel_keyboard`); `src/bot/settings_fields.py` (`FieldGroup`, `fields_by_group()`,
`group_label()`); downstream B-2 (backend-dev, implements this schema), B-3 (backend-dev, root-screen
copy/status text), D-1 (approve-flow deep link — unaffected, see Decision 6), Q-2 (manual smoke
checklist).

---

## Context

ADR-0006 (B-1 of the prior `chat-settings-panel-2026-08-06` plan) designed the panel's render/permission
split, its KB/Reactions link-out behavior (Decision 2), its generic bool-toggle mechanism (Decision 3),
and its entry points (Decision 4) — but did not design multi-screen navigation, because v1's field count
didn't yet demand it. It shipped as **one screen with every field inline**: `chat_panel_keyboard()`
(`admin_chat_panel.py` keyboards, `for group, fields in fields_by_group(): … for field in fields: rows.append(...)`)
appends a `noop` group-header row followed by every field's row, for every group, in one
`InlineKeyboardMarkup` — currently 4 header rows + 21 field rows (of 25 fields; KB's 1 and Reactions' 2
render as link rows instead, per Decision 2) + 1 back row = 26 rows in a single message.

This is exactly what the source plan's §3 complains about: *"Сейчас все настройки идут одним списком и
это совершенно неудобно"* — confirmed against the code (not just the PRD's claim): the group headers
that already exist (`FieldGroup` + `fields_by_group()`) are visual dividers only, not navigation. The plan
names the target shape explicitly: *"группа как отдельный экран (как уже сделано для KB/Reactions),
корневой экран — список разделов с кратким состоянием"* plus *"обратная навигация и «где я нахожусь»"*.
Julia confirmed this proposal verbatim (`human_feedback` ANSWER [B-1]: *"Да, всё так делаем"*) against
this item's own title — this ADR is that confirmed schema, formalized for B-2 to implement.

`FieldGroup` currently has 6 members; per-group field counts (`fields_by_group()`, verified by running it
against `CHAT_SETTINGS_FIELDS`):

> **Counts below are as of this ADR (25 fields, `MODULES` 8).** They are a snapshot of the decision, not
> a live index — S5b later added `chunks_enabled` to `MODULES` (26 fields, `MODULES` 9), and more will
> follow. The authoritative count is `len(CHAT_SETTINGS_FIELDS)`, pinned by
> `tests/unit/test_settings_fields.py`. The design conclusions here do not depend on the exact numbers.

| Group | Fields | Field types |
|---|---|---|
| `BEHAVIOR` | 5 | all non-BOOL (STR_LIST, FLOAT, INT, STR, STR) |
| `MODULES` | 8 | all BOOL |
| `STICKERS` | 7 | 3 BOOL + 4 FLOAT (incl. `tolerance_level`, its own FSM per ADR-0008 Decision 10) |
| `RULES` | 2 | 1 BOOL + 1 STR |
| `KB` | 1 | link-out only (ADR-0006 Decision 2) |
| `REACTIONS` | 2 | link-out only (ADR-0006 Decision 2) |

This ADR covers the 4 field-owning groups (`BEHAVIOR`, `MODULES`, `STICKERS`, `RULES`). `KB`/`REACTIONS`
keep ADR-0006 Decision 2's link-out behavior unchanged (Decision 3 below).

---

## Decision 1 — Two-tier screen model: root (section list) + one screen per field-owning group

The panel gains exactly one level of nesting, no more:

- **Root screen** (`adm_pnl_menu:{lang}:{chat_id}`, existing prefix, unchanged meaning): lists the 6
  sections — 4 tappable group buttons (`BEHAVIOR`/`MODULES`/`STICKERS`/`RULES`) plus the 2 existing
  KB/Reactions link rows (Decision 3) — instead of every field.
- **Group screen** (new, Decision 2): one per field-owning group, listing only that group's fields with
  their existing per-field controls (BOOL toggle / read-only / `tolerance_level`'s dedicated edit flow),
  exactly as they render today — moved, not redesigned.

No navigation-stack / breadcrumb-history abstraction is introduced. Every group screen has exactly one
parent (root) and every mutation re-render returns to exactly the screen it was invoked from (Decision 5).
This mirrors ADR-0006 Decision 1's own KISS posture ("no permission-strategy abstraction for a single
current caller") applied to navigation: two fixed levels have no need for a generic stack, and KB/Reactions
already prove the one-parent-per-screen shape works for this codebase (their own back buttons point to a
single hardcoded parent, `admin_kb.py:59`, `admin_reactions.py:53`).

## Decision 2 — New callback prefix: `adm_pnl_grp:{lang}:{chat_id}:{group}`

`{group}` is `FieldGroup.value` (`"behavior"` / `"modules"` / `"stickers"` / `"rules"`) — already a short,
lowercase, URL-safe token; no new short-code registry needed (unlike `FieldSpec.code`, which exists
because field *names* can be 30+ chars — group values are already ≤8 chars). Parse with
`FieldGroup(raw)` inside a `try/except ValueError`, mirroring the existing `field_by_code(code) is None`
invalid-data handling in `handle_chat_panel_toggle`.

**Callback-length check** (ADR-0006 already established this must be checked, not assumed):
`"adm_pnl_grp:"` (12) + `"ru:"` (3) + worst-case `chat_id` (`-1001234567890:`, 15) + `"stickers"` (8,
longest group value) = **38 bytes**, comfortably under Telegram's 64-byte limit and in the same range as
ADR-0006 Decision 3's own `adm_pnl_tgl:` budget (34 bytes). `KB`/`REACTIONS`'s existing link callbacks
(`adm_kb_menu:`, `adm_react_menu:`) are untouched — Decision 3.

## Decision 3 — Root screen: 4 group buttons + KB/Reactions link rows, unchanged from ADR-0006 Decision 2

Root screen rows, in `fields_by_group()`'s existing declaration order:

1. `💬 Поведение` → `adm_pnl_grp:{lang}:{chat_id}:behavior`
2. `🧩 Модули` → `adm_pnl_grp:{lang}:{chat_id}:modules`
3. `🎨 Стикеры` → `adm_pnl_grp:{lang}:{chat_id}:stickers`
4. `📏 Правила` → `adm_pnl_grp:{lang}:{chat_id}:rules`
5. `📚 База знаний: {✅|⚫}[· унаследовано]` → `adm_kb_menu:{lang}:{chat_id}` (**verbatim from ADR-0006
   Decision 2/B-2** — not re-derived, not turned into a 5th `adm_pnl_grp:` screen)
6. `😀 Реакции: {✅|⚫} / История: {✅|⚫}` → `adm_react_menu:{lang}:{chat_id}` (same)
7. `◀️ Назад` → `adm_pnl:{lang}:0` (picker — unchanged)

**Why KB/Reactions do not become a 5th/6th `adm_pnl_grp:` screen even though they're "groups" in the
registry:** ADR-0006 Decision 2's reasoning is untouched by this ADR — they link out because
`admin_kb.py`/`admin_reactions.py` already own the single write path for those 3 fields, and giving them
an intermediate group screen inside this panel would be a cosmetic detour (tap "📚" → group screen with
one row → tap again → real KB submenu) that adds a screen without adding a write path or a permission
boundary. They stay exactly one tap from root, as they are today.

**Row text — group label vs. reserved status slot:** button text is `group_label(group, lang)` alone
(e.g. `"💬 Поведение"`) as the structural baseline B-2 ships. Per the source plan's *"список разделов с
кратким состоянием"* and this item's own title ("предсказуемый возврат" is B-2's job; the *status* text
itself is B-3's, per this plan's own structure/copy split — see ADR-0009 Decision 7's identical split for
A-4's badge copy), B-3 appends a short trailing status **to the same row**, not a new row or a keyboard
shape change — analogous to how `_INHERITED_MARK` already appends to existing rows rather than adding
one. B-3 has everything it needs without new plumbing: `config` (effective `ChatConfig`) and `row` (raw
`chat_settings` columns) are already read by `render_chat_panel` for the root screen today and cost B-3
nothing new to consume. **This ADR does not pin the status formula** (e.g. "N/M on" reads naturally for
`MODULES`' 8 all-BOOL fields but not for `BEHAVIOR`'s 5 all-non-BOOL ones — a single formula across all 4
groups is not obviously right, and forcing one now would be guessing at B-3's job). Leave the exact
per-group status content to B-3, same discretion ADR-0009 left A-4 for badge wording.

## Decision 4 — Group screen: breadcrumb header + moved (not redesigned) field rows + one back row

Text:

```
{_PANEL_TITLE[lang]} › {group_label(group, lang)}

{chat_label} <code>{chat_id}</code>
```

e.g. `"⚙️ Настройки чата › 🎨 Стикеры\n\nMy Group <code>-1001234567890</code>"`. The `›` segment is the
literal "«где я»" the item title asks for — root screen keeps its existing text unchanged (a chat-level
screen has no ambiguity about which level it's at, so it needs no breadcrumb of its own).

Keyboard: exactly today's per-field row loop body (`chat_panel_keyboard`'s `for field in fields:` block —
BOOL → toggle button, `tolerance_level` → its dedicated `adm_pnl_tol:` prompt, everything else → read-only
`noop`, each with the existing `_is_inherited` marker), scoped to the one group instead of all four, plus
a single `◀️ Назад` row → `adm_pnl_menu:{lang}:{chat_id}` (root). This is a **move**, not a rewrite — the
row-rendering logic (value formatting, inherited marker, callback shapes for `adm_pnl_tgl:`/`adm_pnl_tol:`)
is unchanged; only its scope (which fields loop over) and its container (a new function vs. the root
keyboard) change. B-2 should lift the existing loop body directly rather than re-deriving it.

**Row-count check:** `MODULES` (largest group) is 8 field rows + 1 back row = 9 rows — trivially within
Telegram's limits (the *current* single-screen panel already ships ~26 rows without issue, so a 9-row
screen needs no new headroom analysis).

## Decision 5 — Mutation re-render target is the field's own group, derived, never a new parameter

`handle_chat_panel_toggle` (`adm_pnl_tgl:{lang}:{chat_id}:{code}`) and the `tolerance_level` FSM's save/
cancel handlers (`adm_pnl_tol:` prompt, its message-input handler, `adm_pnl_tolcancel:`) currently all
re-render the **root** panel unconditionally after a write
(`_render_and_show_panel` → `render_chat_panel`). Under the grouped schema they must instead re-render the
**group screen the field lives on** — otherwise a toggle inside "🧩 Модули" would kick the admin back to
root, breaking the "predictable return" the plan asks for (B-2's own title).

**No new callback_data field, FSM key, or "return-to" parameter is needed.** The group is always
recoverable from the field itself:

- Toggle: `field_by_code(code).group` (the field spec already carries its `FieldGroup`).
- Tolerance prompt/save/cancel: hardcode `FieldGroup.STICKERS` — `tolerance_level` cannot move groups
  without a `settings_fields.py` registry edit, at which point this hardcoded reference would be caught by
  the same change. (Equivalently, `field_by_key("tolerance_level").group` — either is fine; hardcoding is
  simpler and this is a single fixed field, not a loop over the registry.)

This is a deliberate two-way-door-favoring call: threading an explicit `group` param through every one of
these callbacks/FSM-data payloads would work too, but it's strictly more state to keep in sync for zero
behavioral gain — the group is already a pure function of the field, so deriving it costs nothing and
cannot drift out of sync the way a duplicated parameter could.

## Decision 6 — Function split: `render_chat_panel` (root, name/signature unchanged) + new `render_chat_panel_group`

```python
async def render_chat_panel(
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    lang: str,
    chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    ...  # unchanged signature (ADR-0006 Decision 1) — now renders the section list, not every field

async def render_chat_panel_group(
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    lang: str,
    chat_id: int,
    group: FieldGroup,
) -> tuple[str, InlineKeyboardMarkup]:
    ...  # new — Decision 4's screen, for one of the 4 field-owning groups only
```

Keeping `render_chat_panel`'s name and signature identical matters beyond tidiness: **D-1 (depends_on:
B-2) already plans to link straight to `adm_pnl_menu:{lang}:{chat_id}`** (ADR-0006 implementation note 4,
"the chat is already known at that point") — that callback still resolves to `render_chat_panel`, so D-1's
scope is unaffected by this ADR; its landing screen simply becomes the (better) section list instead of a
26-row flat scroll. No coordination needed between B-1/B-2 and D-1 beyond what ADR-0006 already specified.

Both functions stay pure `(text, keyboard)` returns with no `CallbackQuery` parameter, per ADR-0006
Decision 1 — that separation is orthogonal to this ADR and is not revisited.

## Decision 7 — Telegram-limits re-check and one already-solved edge case

- Callback-data bytes: Decision 2 above (38 bytes, new prefix); existing `adm_pnl_tgl:`/`adm_pnl_tol:`
  budgets (ADR-0006 Decision 3, ADR-0008 Decision 10) are untouched — the fields addressed by those
  prefixes don't change, only which screen re-renders afterward (Decision 5).
- Button/row counts: Decision 4 above (9 rows worst case).
- Message text length: breadcrumb adds one short line (`group_label` is ≤~20 chars incl. emoji); nowhere
  close to the 4096-char message limit.
- **Double-tap / re-render-identical-content is already handled**, not a new concern this ADR introduces:
  `safe_edit_text` (`src/bot/utils.py:101-121`) already swallows Telegram's "message is not modified"
  `TelegramBadRequest` — a fresh group screen render after a toggle always differs (the toggled row's
  status glyph flips), but even a no-op double-render (e.g. tapping the same root section button twice
  back-to-back through network lag) degrades gracefully through the existing helper. No new handling
  needed in B-2.

## Decision 8 — Known non-goal, explicitly not fixed here: KB/Reactions' own back button does not return to this panel

`kb_menu_keyboard()`'s back button (`admin_kb.py:94-96`) goes to `adm_kb:{lang}:0` — the **KB module's
own** picker — not to whichever screen linked into it (this panel's root, or KB's own top-level admin-menu
entry point). Same shape in `admin_reactions.py`. This is pre-existing behavior from before this plan
(ADR-0006 Decision 2 already accepted "one interaction step further" as a trade-off but did not call this
specific consequence out by name) — an admin who reaches `adm_kb_menu:` via this panel's root screen and
taps "◀️ Назад" lands on the KB module's picker, not back on this chat's panel.

**Not in scope to fix here.** Doing so would require either (a) threading a "return to" origin through
`adm_kb_menu:`'s own callback_data — a change to `admin_kb.py`/`admin_reactions.py`'s navigation model,
which ADR-0006 Decision 2 deliberately keeps outside this panel's module boundary — or (b) a generic
navigation-stack mechanism, rejected by Decision 1 above as more machinery than two fixed levels need.
Flagged here so it is not mistaken for a bug introduced by this ADR, and so Q-2's manual smoke checklist
describes it as expected (tap "📚 База знаний" from the chat panel → its own "◀️ Назад" returns to the KB
picker, not the chat panel — use `adm_pnl:` / the picker to get back to a specific chat's panel).

### Superseded 2026-08-09 — option (a) was implemented after all

The owner hit this within hours of B-2 landing and reported it as a bug, which is the answer to the
question this decision left open: once the panel became the per-chat hub, "Back lands in a different
section's chat list" stopped reading as an accepted trade-off and started reading as broken. Option (a)
above is what shipped — an origin token in `callback_data` (`src/bot/nav.py`), threaded through
every button in the KB/Reactions submenus that leads to a screen returning to them. Entering from this
panel now returns here; entering from a section's own picker still returns there; an absent or unknown
token reads as the old default, so keyboards rendered before the change degrade rather than break.

Two things this decision got right and are worth keeping in mind: it is indeed a change to
`admin_kb.py`/`admin_reactions.py`'s navigation model rather than something this panel can fix alone, and
`nav.py` hardcodes `adm_pnl_menu:` — a prefix owned by this module — so the three sub-panels are now
coupled through one file. A test asserts that literal still matches the live router prefix.

Q-2's checklist was updated in the same change: the step that would have recorded the old behavior as
expected now exercises both entry points instead.

---

## Consequences

### Positive

- Root screen drops from ~26 rows to 7 (4 groups + 2 links + back); the largest group screen is 9 rows —
  directly answers the plan's usability complaint without inventing new mechanism.
- Decision 5 means B-2 adds zero new state to the toggle/tolerance handlers — the group is a pure
  function of the field being edited, so "predictable return" (B-2's own title) falls out for free instead
  of needing a "remember where I came from" parameter.
- Decision 6 keeps `adm_pnl_menu:` — and therefore D-1's planned deep link — meaning-stable; D-1 needs no
  awareness of this ADR beyond "the screen got better."
- Decision 4 is a lift-not-rewrite of existing, already-tested row-rendering logic — the actual field
  behaviors (toggle, inherited marker, `tolerance_level`'s FSM) are unchanged, only their container.

### Negative / Trade-offs

- One more tap to reach any individual field (root → group → field) versus today's single scroll —
  accepted as the entire point of the request; the source plan explicitly asks for this shape.
- Decision 8's KB/Reactions back-button inconsistency remains unresolved — accepted pre-existing debt, not
  worsened by this ADR (the inconsistency exists identically today, this ADR just makes it reachable via a
  clearer path).
- Root screen's per-group status text (Decision 3) is left unspecified pending B-3 — B-2 ships a
  functionally complete root screen with plain group labels first; that's a positive for keeping B-2/B-3
  independently reviewable, not really a trade-off.

---

## Rejected alternatives

### A: Keep one flat screen, add anchors/pagination instead of real sub-screens

Rejected — a "page 2 of settings" pagination still shows an arbitrary slice of unrelated fields per page
(pagination boundaries don't align with `FieldGroup` boundaries unless hand-tuned per group, which is
just a group screen with extra steps), and doesn't give the "«где я»" signal the plan explicitly asks for.

### B: Full navigation-stack / breadcrumb-history mechanism (arbitrary depth, "back" pops a stack)

Rejected (Decision 1) — this panel has exactly 2 levels by construction (root, one group). A stack is the
right tool for arbitrary-depth navigation; building one for a fixed 2-level tree is the same category of
premature abstraction ADR-0006 Decision 1 already rejected for permission strategies ("speculative
abstraction for a single current caller/shape").

### C: Turn KB/Reactions into `adm_pnl_grp:` screens too, for a uniform 6-group root

Rejected (Decision 3) — would add a cosmetic detour screen without adding a write path or permission
boundary; actively works against ADR-0006 Decision 2's reasoning (one write path per field, reached in one
tap).

### D: Thread an explicit `group` (or "return_to") parameter through the toggle/tolerance callback_data and FSM data

Rejected (Decision 5) — strictly more state to keep in sync than deriving the group from the field spec,
for identical behavior; the field-to-group mapping already exists in the registry and cannot silently
drift out of sync with a derived read, only with a hand-duplicated parameter.

---

## Implementation notes for B-2 (backend-dev)

1. `settings_fields.py`: no changes needed — `FieldGroup`, `fields_by_group()`, `group_label()` already
   provide everything Decisions 2–4 need.
2. `keyboards/admin_chat_panel.py`: split `chat_panel_keyboard()` into `chat_panel_root_keyboard()`
   (Decision 3's 7 rows) and a new `chat_panel_group_keyboard(lang, *, chat_id, group, config, row)`
   (Decision 4 — lift the existing per-field loop body, scoped to `fields_by_group()`'s one matching
   entry).
3. `handlers/admin_chat_panel.py`:
   - `render_chat_panel` keeps its name/signature (Decision 6), now calls the root keyboard builder.
   - New `render_chat_panel_group(..., group: FieldGroup)` (Decision 6), new handler on
     `adm_pnl_grp:{lang}:{chat_id}:{group}` (Decision 2), same permission-check-then-render shape every
     other handler in this router already uses (`_is_private` + `check_admin_direct`, ADR-0006 Decision 1).
   - `handle_chat_panel_toggle`: after a successful write, re-render via
     `render_chat_panel_group(..., group=field.group)` instead of `_render_and_show_panel` (Decision 5).
   - Tolerance prompt/save/cancel handlers (`adm_pnl_tol:`, its message handler, `adm_pnl_tolcancel:`):
     re-render via `render_chat_panel_group(..., group=FieldGroup.STICKERS)` (Decision 5), not the root
     panel. Also update `handle_chat_panel_tolerance_prompt`'s implicit "return to" — since the prompt
     itself is sent as a *new* message (`callback.message.answer`, not an edit), no change needed there;
     only the post-save/cancel re-render target changes.
4. Root screen's group buttons carry no per-group status text yet (Decision 3) — plain `group_label(...)`
   is a complete, correct v1; do not block B-2 on inventing status copy that's B-3's job.
5. Existing tests (`tests/unit/test_admin_chat_panel_keyboards.py`,
   `tests/unit/test_admin_chat_panel_handler.py`) assert today's flat-list shape and will need rewriting
   to match the new root/group split — expected, not a regression signal; per this project's conventions
   backend-dev owns updating its own unit tests for code it touches.

## Implementation notes for B-3 (backend-dev, copy)

1. Add a short trailing status segment to each of the 4 group rows on the root screen (Decision 3),
   appended to the existing button text the same way `_INHERITED_MARK` appends to field rows — no keyboard
   shape change, no new callback, no new query (reuse `config`/`row` already passed into the root
   keyboard builder).
2. Exact status formula per group is this item's discretion (Decision 3 deliberately leaves it open) — a
   uniform "N/M on" reads naturally for `MODULES` (8 BOOL fields) but not for `BEHAVIOR` (0 BOOL fields);
   consider what's actually informative per group rather than forcing one formula across all 4 (e.g. an
   overridden-vs-inherited count works for every `not legacy` field in any group, via the already-existing
   `_is_inherited` helper, regardless of `FieldType`).
3. Out of this item's scope: breadcrumb text (Decision 4, already pinned by this ADR) and any group-screen
   field labels (already shipped, unchanged by this ADR).

## Implementation notes for Q-2 (qa, manual smoke checklist)

- Cover: root screen shows 4 group buttons + KB/Reactions link rows; tapping a group opens its screen with
  the `› {group}` breadcrumb; toggling a field inside a group re-renders that same group (not root);
  "◀️ Назад" from a group returns to root; "◀️ Назад" from root returns to the chat picker.
- Cover Decision 8 explicitly, worded as expected behavior, not a bug to file: KB/Reactions' own back
  button returns to their module's picker, not to this chat's panel.

---

## Out of scope (this ADR and B-2/B-3)

- Any change to which fields are BOOL-toggleable vs. read-only, or to F-1 (deferred generic non-BOOL FSM
  editing) — this ADR only changes navigation/screen structure, never field editability.
- Any change to KB/Reactions' own internal screens, write paths, or back-button target (Decision 8) —
  fixing that is a change to `admin_kb.py`/`admin_reactions.py`'s own module, not this panel.
- A defaults-screen (C-1) equivalent of this grouped navigation — C-1 was already scoped and ADR-0006-noted
  as a flat iteration over `new_fields()` (11 fields); if C-1 grows enough fields to need the same
  treatment, that's a future ADR, not implied by this one.
- Root screen per-group status wording/formula (Decision 3) — B-3's discretion.

---

*Document generated as part of B-1 (admin-ux-and-summary-2026-08-09 plan).*
*Architect: specialist-architect (universal baseline).*
