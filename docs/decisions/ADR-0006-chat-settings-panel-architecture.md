# ADR-0006: Chat settings panel — render/permission separation, KB/Reactions embedding, entry points

**Status:** accepted
**Date:** 2026-08-06
**Plan item:** A-2 (chat-settings-panel-2026-08-06)
**Author:** specialist-architect
**Relates to:** `docs/plans/chat-settings-panel-2026-08-06.md` (Цель 2, R1–R3); A-1
(`src/bot/settings_fields.py`, done); ADR-0003 (`kb_organizer_ids`/KB module boundary);
ADR-0004 (Reactions module boundary, Decision 5 live admin-rights check); CLAUDE.md
"extract after 3 repetitions", "Per-chat columns: nullable, no DEFAULT"

---

## Context

The PRD's Цель 2 requires the new per-chat settings panel to be a **self-contained tool**:
rendering must be parameterized by `chat_id`, and *who is allowed to open it* must be a
separate concern from *what gets rendered* — today's global-admin-from-DM check is v1's only
entry point, but a later iteration adds a second one (a chat's own admins, opening the panel
from inside the chat, checked against Telegram chat-membership rights, not `bot_config.admin_ids`).
The architecture must not force a rewrite when that lands.

Two more things need a decision before B-1 can start:

1. How the panel's KB (`kb_enabled`) and Reactions (`reactions_enabled`,
   `reactions_history_enabled`) rows relate to the **existing** dedicated KB/Reactions
   sub-panels, which already have their own toggle handlers, chat-pickers, and (Reactions)
   a live Bot-API admin-rights diagnostic (ADR-0004 Decision 5).
2. Where the panel's entry points live, given the codebase already has an established
   "one dedicated chat-picker per module" convention (`adm_kb:` → `kb_chat_picker_keyboard`,
   `src/bot/keyboards/admin_kb.py:22-45`; `adm_react:` → `reactions_chat_picker_keyboard`,
   mirrored in `admin_reactions.py`) rather than repurposing the whitelist-management screen
   (`adm_wl_chats:`, `src/bot/handlers/admin.py:1017-1105`, `chats_list_keyboard`,
   `src/bot/keyboards/admin.py:335-409`) as a launcher for other modules.

A-1 (done, commit `4147315980450f51cbff6664895ee4336321573e`) already shipped the shared field
registry (`src/bot/settings_fields.py`) and its docstring states two facts as settled — this
ADR is what makes them formally true rather than pre-empted:

- `kb_organizer_ids` stays KB-panel-only ("per the A-2 ADR", `settings_fields.py:18-21`).
- `CHAT_SETTINGS_FIELDS` includes `kb_enabled` / `reactions_enabled` /
  `reactions_history_enabled` as ordinary `FieldType.BOOL` entries (`settings_fields.py:294-317`),
  each with a short `code` ("kb", "rx", "rh") — the registry is generic across *consumers*
  (chat panel, defaults screen); it does not itself decide how the chat panel *uses* the KB/
  Reactions entries. Decision 2 below is what resolves that.

---

## Decision 1 — Render is a pure `(text, keyboard)` function; permission check stays a call-site guard

**Signature (B-1 to implement):**

```python
async def render_chat_panel(
    chat_settings_repo: ChatSettingsRepository,
    bot_config_repo: BotConfigRepository,
    chat_config_service: ChatConfigService,
    lang: str,
    chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    ...
```

No `CallbackQuery`/`Message` parameter, no permission check inside. The callback handler that
calls it does exactly two things in order: (a) check whether the caller is allowed to see
*this* `chat_id`'s panel, using whichever predicate applies to *that* entry point; (b) call
`render_chat_panel(...)` and hand the `(text, keyboard)` to `safe_edit_text` (or, for a future
non-callback entry point, `Message.answer`).

This is a **deliberate tightening**, not a bug fix, relative to the existing KB/Reactions
precedent: `_render_kb_menu` (`admin_kb.py:187-201`) and `_render_menu`
(`admin_reactions.py:155-183`) already keep the permission check *out* of the render helper
(the check happens once, in the calling `@router.callback_query` handler, before `_render_*`
is invoked) — that half of the separation already exists and B-1 should keep following it
verbatim. What's new here is that those two helpers still take `callback: CallbackQuery` and
edit `callback.message` directly, coupling "what to show" to "editing this specific DM callback
message." `render_chat_panel` must not take that dependency, because Цель 2's stated future
caller (an admin invoking the panel from inside the chat) has no DM callback message to edit —
it would send a fresh message in the chat instead. Returning plain `(text, keyboard)` costs
nothing extra for v1 (the DM callback handler still just calls `safe_edit_text` with the
result) and avoids a rewrite later.

**No permission-strategy abstraction (interface/Protocol/plugin) is introduced.** v1 has one
caller (the DM callback handler), which does the checked-then-render sequence directly. The
future in-chat entry point will be a second, independent handler doing its own
checked-then-render sequence with a different predicate. Building an abstraction for a single
current implementation would be premature — the two-way door here is "keep `render_chat_panel`
free of permission logic," which is already achieved without inventing an interface.

For v1, the permission predicate is `check_admin_direct(bot_config_repo, user_id)`
(`src/bot/utils.py:32-52`) + `_is_private(callback)`, exactly the pattern KB and Reactions use
(`admin_kb.py:317-326`, `admin_reactions.py`). **Do not** reach for the `_guard_admin` /
`_check_admin` family from `admin.py:240-248` (the middleware-injected-`is_admin` variant used
by the whitelist/approve flow) — `check_admin_direct`'s own docstring
(`src/bot/utils.py:44-47`) already states these two families are intentionally not merged;
picking one per new module (as KB/Reactions did) rather than mixing both in one file is the
established convention.

---

## Decision 2 — KB and Reactions groups embed by **link**, not a duplicated toggle

The panel's KB and Reactions sections render as a **status line + "open" button** deep-linking
to the existing per-chat submenus, reusing their callback_data verbatim:

- `📚 База знаний: {✅|⚫} [inherited-marker if B-2 applies]` → button →
  `adm_kb_menu:{lang}:{chat_id}` (existing prefix, `admin_kb.py:281`).
- `😀 Реакции: {✅|⚫} / История: {✅|⚫}` → button → `adm_react_menu:{lang}:{chat_id}`
  (existing prefix, `admin_reactions.py`).

They are **not** rendered as inline toggle rows using the panel's own generic
bool-field-toggle mechanism (Decision 3), even though `kb_enabled` / `reactions_enabled` /
`reactions_history_enabled` are ordinary `BOOL` `FieldSpec`s in the A-1 registry.

**Why this is the right call, not just a style choice:** `admin_kb.py:310-338`
(`handle_kb_toggle`) and `admin_reactions.py:251-` (`handle_reactions_toggle`) are *already* the
single existing implementation of "flip `kb_enabled`/`reactions_enabled`/
`reactions_history_enabled`'s effective value and write it." If the new panel *also* toggled
these three fields directly through its own generic write path, there would be **two**
independent code paths performing the same write for the same three columns — a duplication
that actively regresses correctness, not just tidiness: E-1 (separately scoped, `depends_on: []`)
is retrofitting cache invalidation into exactly `admin_kb.py`/`admin_reactions.py`'s existing
toggle handlers; a second, parallel toggle path in the new panel would need to independently
stay in sync with whatever E-1 lands, or the two paths silently diverge on invalidation
semantics. Linking out sidesteps that entirely — there is exactly one write path per field,
for these three, exactly as there is today.

This reasoning does **not** apply to the other ~20 bool fields (`rag_enabled`,
`transcribe_voice`, `sticker_learning_enabled`, `rules_enabled`, `link_comments_enabled`, …) —
those have **zero** existing UI (per the PRD's own gap analysis), so the panel's generic
per-field toggle (Decision 3) is their *only* implementation. No duplication risk there; build
it directly on the panel.

`kb_organizer_ids` was never a toggle candidate (it's not a `ChatConfig`/registry field at
all, A-1 excluded it — `settings_fields.py:14-21`) and stays reachable only through
`adm_kb_orgs:` inside the KB submenu, as A-1 already assumed.

**Consequence for B-2** (inheritance marker): the status line shown next to the KB/Reactions
link buttons still needs the "inherited from default" marker when applicable (all three fields
are `legacy=False`, i.e. in `new_fields()`) — B-2 computes and shows it the same way it does
for every other `new_fields()` row; it just attaches to a link row instead of a toggle row.
The marker computation itself (raw-column-is-NULL vs. effective value) is unaffected by
Decision 2.

**Consequence for C-1** (defaults screen): Decision 2 is specific to the **per-chat panel**.
`bot_config.default_kb_enabled` / `default_reactions_enabled` /
`default_reactions_history_enabled` have no equivalent "existing dedicated sub-panel" at the
global-defaults layer — C-1 toggles these three exactly like its other 8 fields, through
`BotConfigRepository.set(f"default_{field.key}", value)` (`src/database/repositories/bot_config.py:27-35`).
**C-1 must call `chat_config_service.invalidate_all()`** after any default write, not
`invalidate(chat_id)` — a default change affects the shared `_global_cache`
(`src/services/chat_config.py:52-54, 66-68`), which per-chat `invalidate()` never touches. This
is easy to get wrong by analogy with B-1's per-chat writes and is called out here so it isn't
rediscovered as a review finding.

---

## Decision 3 — One generic toggle callback for the ~20 fields with no existing UI

A single callback prefix, e.g. `adm_pnl_tgl:{lang}:{chat_id}:{code}` (`code` = the A-1
registry's short `FieldSpec.code`), backs every `FieldType.BOOL` field that is *not* one of
the three link-only KB/Reactions fields (Decision 2). Handler shape mirrors
`admin_kb.py:310-338` generalized over the registry:

```python
field = field_by_code(code)  # settings_fields.field_by_code
config = await chat_config_service.get_config(chat_id)          # effective value (all 24 fields, cached)
new_value = not getattr(config, field.key)
await chat_settings_repo.set_field(chat_id, field.key, new_value)  # existing method, chat_settings.py:102-107
chat_config_service.invalidate(chat_id)                          # B-1's own write self-invalidates (PRD, not E-1's job)
```

Reusing `ChatConfigService.get_config()` for the effective value (rather than hand-rolling a
per-field `_effective_x()` helper the way `admin_kb.py:194,336` currently does for the single
`kb_enabled` case) avoids writing ~20 near-identical helpers; the service already does the
three-layer merge and is already available via Dishka (`chat_events.py:54`).

**Callback-length check** (the PRD flags Telegram's 64-byte `callback_data` limit as a live
constraint, not a theoretical one): `"adm_pnl_tgl:"` (12) + `"ru:"` (3) + a worst-case
`chat_id` (`-1001234567890`, 14 chars + `:` = 15) + a 4-char code = **34 bytes**, comfortably
under 64. The registry's short-code design (A-1) does solve the constraint it was built for.

---

## Decision 4 — Entry points mirror the existing per-module chat-picker convention

The panel gets its **own** dedicated chat-picker, following the `adm_kb:`/`adm_react:`
precedent (`kb_chat_picker_keyboard`, `admin_kb.py:22-45`; the analogous Reactions keyboard),
**not** a button bolted onto `adm_wl_chats:` (`chats_list_keyboard`,
`src/bot/keyboards/admin.py:335-409`):

- New main-menu button "⚙️ Настройки чата" (alongside the existing `_t("kb", lang)` /
  `_t("reactions", lang)` row, `src/bot/keyboards/admin.py:96-104`) → `adm_pnl:{lang}:0`
  (paginated picker, mirroring `kb_chat_picker_keyboard`).
- Picker row tap → `adm_pnl_menu:{lang}:{chat_id}` (direct per-chat render, mirroring
  `adm_kb_menu:`).
- D-1's "⚙️ Настройки чата" button (after `adm_approve:`/`adm_wl_apr:`) links straight to
  `adm_pnl_menu:{lang}:{chat_id}` — the chat is already known at that point, same as D-1 would
  do for any per-chat screen.

**Why not repurpose `adm_wl_chats:`:** that screen's one existing action button is `❌` /
`adm_wl_rm_ask:` — a **remove-from-whitelist** action (`chats_list_keyboard`,
`admin.py:368-373`). Adding a second, unrelated action (open settings) to the same row turns a
single-purpose whitelist-management screen into a mixed launcher, and — concretely — requires
touching `chats_list_keyboard` / `_render_wl_chats`, code B-1 otherwise never needs to modify.
The codebase's own answer to "a module needs to address one whitelisted chat" is already "give
the module its own picker" (KB and Reactions each did this independently rather than share
`adm_wl_chats:`); the panel is the third instance of the same shape, not a reason to invent a
different shape. This also satisfies R1's literal requirement ("вход из списка чатов
whitelist") — the panel's own picker *is* a list of whitelisted chats, addressed via its own
prefix, exactly as KB's and Reactions' already are.

---

## Consequences

### Positive

- `render_chat_panel` has exactly one shape to test and one call site pattern to replicate for
  the future in-chat entry point — no rewrite anticipated when that lands, only a second
  handler with a different guard.
- Decision 2 keeps "flip `kb_enabled`/`reactions_enabled`/`reactions_history_enabled`" a
  single-source-of-truth operation, so E-1's cache-invalidation retrofit (separately scoped)
  only has one call site per field to fix, not two.
- Decision 3's reliance on `ChatConfigService.get_config()` means the ~20 net-new toggles share
  one effective-value read path instead of ~20 hand-rolled ones.
- Decision 4 costs one new picker (small, same shape as two that already exist) in exchange for
  not touching the whitelist-removal screen at all.

### Negative / Trade-offs

- The KB/Reactions rows on the unified panel are visually one interaction step further than
  every other bool row (tap "open" → then toggle, vs. toggle inline) — accepted as the price of
  not duplicating the write path; Julia can revisit if this reads as inconsistent in practice
  (a two-way door: promoting them to inline toggles later means routing them through Decision
  3's generic mechanism and deleting `admin_kb.py`/`admin_reactions.py`'s dedicated toggle
  handlers in the same change, not adding a second path).
- Four near-identical paginated chat-pickers now exist (`adm_wl_chats:`, `adm_kb:`,
  `adm_react:`, `adm_pnl:`) with no shared implementation — pre-existing debt (KB/Reactions
  already duplicated this pattern twice before this plan), not introduced by this ADR. Not
  worth a shared-picker extraction inside this plan's budget; flag as tech debt if a fifth
  module ever needs one (CLAUDE.md "extract after 3 repetitions" — the threshold was already
  passed by KB+Reactions; this ADR knowingly adds a fourth rather than stopping to extract,
  because the extraction is orthogonal to Цель 2/R1-R3 and would expand A-2/B-1's scope past
  what was estimated and approved).

---

## Alternatives considered

### A: Panel toggles `kb_enabled`/`reactions_enabled`/`reactions_history_enabled` inline, via the same generic mechanism as every other bool field

Rejected (Decision 2): creates a second write path for fields that already have one, working
against E-1's invalidation retrofit instead of alongside it.

### B: Bolt a "⚙️" button onto `adm_wl_chats:`'s existing row instead of a dedicated picker

Rejected (Decision 4): mixes whitelist-removal and settings-launch on one screen and requires
modifying `chats_list_keyboard`/`_render_wl_chats`, which otherwise need no change; also
inconsistent with the established one-picker-per-module precedent (KB, Reactions).

### C: A permission-checker `Protocol`/strategy object passed into `render_chat_panel`

Rejected (Decision 1): speculative abstraction for a single current caller. The "doesn't block
adding chat-admin access later" requirement is satisfied by keeping the render function free of
permission logic at all — a second caller with a second guard needs no interface, just a
second handler.

---

## Implementation notes for backend-dev (B-1, C-1) and downstream items (B-2, D-1, E-1)

1. **B-1**: `render_chat_panel(...)` per Decision 1, in a new
   `src/bot/handlers/admin_chat_panel.py` + `src/bot/keyboards/admin_chat_panel.py` pair
   (mirrors the `admin_kb.py`/`keyboards/admin_kb.py` split), registered in
   `src/bot/handlers/__init__.py` alongside `admin_kb_router`/`admin_reactions_router`
   (`__init__.py:8-9,31-32`). Callback prefixes: `adm_pnl:` (picker), `adm_pnl_menu:` (render),
   `adm_pnl_tgl:` (generic bool toggle, Decision 3). KB/Reactions groups render per Decision 2
   (link out, no local toggle). Self-invalidate (`chat_config_service.invalidate(chat_id)`)
   after every `adm_pnl_tgl:` write — this is B-1's own job per the PRD, distinct from E-1's
   retrofit of the *existing* KB/Reactions toggle handlers.
2. **B-2** (depends on B-1): the inherited-marker computation needs the **raw**
   `chat_settings_repo.get(chat_id)` row (to see which `new_fields()` columns are `NULL`) in
   addition to the effective `ChatConfigService.get_config()` values already used for rendering
   — effective value alone can't distinguish "explicitly set, happens to match the default"
   from "inherited." Only `new_fields()` rows (registry helper, `settings_fields.py:342-346`)
   get the marker, including the KB/Reactions link rows per Decision 2's consequence above.
3. **C-1** (depends on A-1 only): reuse `render`-style helpers analogous to B-1's but writing
   through `BotConfigRepository.set(f"default_{key}", value)` and calling
   `chat_config_service.invalidate_all()`, not `invalidate(chat_id)` — see Decision 2's C-1
   consequence. Iterate `new_fields()` only (11), never the 13 legacy ones (C-2 is deferred
   tech debt).
4. **D-1** (depends on B-1): link directly to `adm_pnl_menu:{lang}:{chat_id}` (Decision 4) —
   no picker step needed since `chat_id` is already known at approve time.
5. **E-1** (independent, `depends_on: []`): retrofits invalidation into `admin_kb.py`'s
   `handle_kb_toggle` (`admin_kb.py:310-338`) and `admin_reactions.py`'s
   `handle_reactions_toggle` (`admin_reactions.py:251-`) — the exact two call sites Decision 2
   keeps as the single source of truth for those three fields. No new call sites are created by
   B-1 for these three fields, so E-1's scope is unaffected by this ADR.

---

*Document generated as part of ADR-0006 (chat-settings-panel-2026-08-06 plan, item A-2).*
*Architect: specialist-architect (universal baseline).*
