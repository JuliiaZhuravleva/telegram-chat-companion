# ADR-0004: Reactions data model, module boundary, privacy toggles, tier-3 reaction piggyback, admin diagnostics

**Status:** accepted
**Date:** 2026-08-03
**Plan item:** ADR-0004 (reactions-2026-08-03) — Phase 1 (R-1, R-5, R-D1)
**Author:** specialist-architect
**Relates to:** `docs/plans/reactions-2026-08-03.md` §2–7; ADR-0003 (repository-location precedent, per-chat nullable-column convention); CLAUDE.md "alembic: one SQL statement per `op.execute()`", "Per-chat columns: nullable, no DEFAULT", "Handlers may call Telegram Bot API as fallback"

---

## Context

Julia asked for full reactions support: record who reacted/unreacted, occasionally comment
on notable reacts, have the bot set its own reactions, and learn the chat's reaction style.
Three specialists scoped this into a two-phase plan; Julia answered the open questions
(`docs/plans/reactions-2026-08-03.md` §6, recorded as `human_feedback` in the envelope).
This ADR is the durable record of the decisions those answers require, so `R-1`, `R-5`,
`R-D1` (Phase 1) and later `R-2`/`R-3`/`R-4` (Phase 2) don't re-derive them.

Five decisions are locked here:

1. **Data model** for the new `message_reactions` table (migration 018).
2. **Module boundary**: `src/services/modules/reactions/` vs. `src/database/repositories/reactions.py`.
3. **Privacy**: two independent per-chat toggles + a short, separate retention window +
   a hard rule that raw reaction rows never reach an AI prompt.
4. **Tier-3 reaction piggyback** (R-5): how `llm_judge` returns a suggested emoji at zero
   extra token cost, and how that's validated before `setMessageReaction`.
5. **Admin-rights diagnostics** (R-D1): why this is a live check, not a cached column.

---

## Decision 1 — `message_reactions` schema (migration 018)

### Denormalized row per (emoji, action) — R-1a

`MessageReactionUpdated` (verified: `aiogram.types.MessageReactionUpdated.model_fields` →
`chat, message_id, date, old_reaction, new_reaction, user, actor_chat`) carries the **full
current state** of a user's reactions on a message, not a queue of events. There is no
"added"/"removed" flag in the update — it has to be computed by diffing `old_reaction` against
`new_reaction`.

**Decision:** do the diff once, at write time, in the handler — not at read time. For a given
update, compute:

```
key(r) = (r.type, r.emoji if r.type == "emoji" else r.custom_emoji_id)   # r.type == "paid" → key = ("paid", None)

removed = {key(r) for r in old_reaction} - {key(r) for r in new_reaction}
added   = {key(r) for r in new_reaction} - {key(r) for r in old_reaction}
```

...and insert **one row per changed key** (action='removed' for `removed`, action='added' for
`added`). Reactions present in both old and new produce no row. This is Julia's R-1a choice
(denormalized row per emoji+action) over storing the two raw JSONB arrays and re-diffing on
every read — the diff logic is identical either way, but computing it once at write time is
what makes `R-9`'s later analytics ("top reactors", "chat mood over time") a plain `GROUP BY`
instead of a JSONB-unpacking query on every read.

### Type discriminator + raw custom-emoji id, unresolved — R-1b

Verified (`aiogram.types.ReactionTypeEmoji/CustomEmoji/Paid.model_fields` + live instantiation):
the Bot API's own discriminator values are exactly `"emoji"` / `"custom_emoji"` / `"paid"` —
reuse them verbatim as the `reaction_type` column rather than inventing a parallel vocabulary.

- `reaction_type = 'emoji'` → `emoji` column holds the literal emoji string (one of the ~76
  Telegram allows a bot to set — see Decision 4 for the bot's own outgoing side).
- `reaction_type = 'custom_emoji'` → `custom_emoji_id` column holds the **raw Telegram id**,
  unresolved. Resolving it to a human-readable sticker (`getCustomEmojiStickers`) is a separate
  API call per unique id and is **not** Phase-1 scope — R-1/R-9 only need to distinguish "this
  was a custom emoji" from "this was a standard one," not render it. If a later phase needs
  the rendered sticker, add a resolution cache then; don't pre-build it against unproven demand.
- `reaction_type = 'paid'` → both `emoji` and `custom_emoji_id` are `NULL`. Paid (Telegram
  Stars) reactions can't be *set* by bots (verified: `Bot.set_message_reaction` docstring —
  "Paid reactions can't be used by bots") but users **can** send them, and the read side must
  not crash or silently drop that row.

### Anonymous reactors, no FK to `chat_messages`

`user` is optional; when absent, `actor_chat` carries the anonymous-reactor chat (e.g. reacting
"as the channel"). Store both as nullable columns; exactly one of `user_id`/`actor_chat_id` is
populated in practice, but don't add a `CHECK` forcing that — a future Bot API edge case
(both absent, or Telegram adding a third actor kind) shouldn't hard-fail the insert.

No foreign key to `chat_messages(chat_id, message_id)`. Verified: **no table in this codebase
FKs onto `chat_messages`** (`grep -rn "REFERENCES chat_messages" alembic/versions/*.py` →
empty; `chat_facts.source_message_id` in ADR-0003 is a plain `BIGINT` for the same reason).
The source plan is explicit that a reacted-to message can be older than `chat_messages_days`
retention or never saved (`save_messages=false`) — an FK would make every such reaction insert
fail. `(chat_id, message_id)` is a soft join key, resolved best-effort at read time, exactly
like `chat_facts.source_message_id`.

### DDL

```sql
CREATE TABLE IF NOT EXISTS message_reactions (
    id               BIGSERIAL PRIMARY KEY,
    chat_id          BIGINT NOT NULL,
    message_id       BIGINT NOT NULL,
    user_id          BIGINT,              -- NULL when actor_chat_id is set (anonymous reactor)
    actor_chat_id    BIGINT,              -- NULL when user_id is set
    action           VARCHAR(10) NOT NULL,  -- 'added' | 'removed'
    reaction_type    VARCHAR(20) NOT NULL,  -- 'emoji' | 'custom_emoji' | 'paid' (Bot API's own vocabulary)
    emoji            VARCHAR(50),           -- set iff reaction_type = 'emoji'
    custom_emoji_id  VARCHAR(64),           -- set iff reaction_type = 'custom_emoji'; raw id, never resolved in Phase 1
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_message_reactions_chat_message
    ON message_reactions(chat_id, message_id);

-- Retention sweep (RetentionCleaner) and future analytics (R-9) both scan by recency.
CREATE INDEX IF NOT EXISTS idx_message_reactions_chat_created
    ON message_reactions(chat_id, created_at DESC);

ALTER TABLE chat_settings
  ADD COLUMN IF NOT EXISTS reactions_enabled BOOLEAN,          -- nullable, NO default (see Decision 3)
  ADD COLUMN IF NOT EXISTS reactions_history_enabled BOOLEAN;  -- nullable, NO default
```

One `op.execute()` per statement (CLAUDE.md: online migrations `PREPARE` each string; a
multi-statement string breaks `alembic upgrade head` on a fresh DB, invisible to the offline
`--sql` integration test — `test_alembic_online_upgrade.py` is the guard QA-1 already extends).
Both new `chat_settings` columns are nullable with no `DEFAULT`, per the established
three-layer-merge convention (ADR-0003, migration 015) — a `DEFAULT` would materialize on
`ensure_exists()` and permanently shadow the module's own global-layer toggle.

Next free migration number is **018** (`017_chat_messages_username_lookup.py` is the latest on
disk; `013` remains ADR-0002's soft reservation for `spend_limit_per_chat`, unaffected by this
table).

---

## Decision 2 — Module boundary: `modules/reactions/` vs. `database/repositories/`

The source plan's item title says "отдельный модуль `modules.reactions`" — correct at the
config-namespace level (a `modules: reactions: {enabled: ...}` block in `config/default.yml`,
parallel to `sticker_intelligence`). But **the repository does not live inside the module
package**, for the same reason ADR-0003 already corrected once: `StickerRepository` lives in
`src/database/repositories/stickers.py`, not `modules/sticker/`; `modules/sticker/*.py` holds
only dataclasses and pure logic. Repeating the modules-own-their-repository pattern here would
be a second, unjustified departure from an established split.

```
src/services/modules/reactions/
  models.py     — ReactionEvent dataclass, ALLOWED_REACTION_EMOJI tuple (Decision 4),
                   pure diff() helper (the added/removed computation above — unit-testable
                   with no I/O)
  selector.py   — ReactionSelector: validates a candidate emoji against ALLOWED_REACTION_EMOJI
                   and (when known) the chat's available_reactions; fail-closed (returns None,
                   never guesses a substitute). Single primitive shared by R-5 now and R-3/R-8
                   in Phase 2 — do not fork a second selector when those land.
  responder.py  — wraps bot.set_message_reaction(); catches TelegramBadRequest (restricted
                   available_reactions, non-reactable service messages) and logs+swallows,
                   never raises into the caller. Mirrors modules/sticker/responder.py's shape.

src/database/repositories/reactions.py
  ReactionRepository — insert diffed rows (bulk, one INSERT per changed key or a single
                        multi-row INSERT — implementer's call), read helpers for Phase 2
                        (R-4's chat-style profile, R-9's analytics).

src/bot/handlers/reactions.py
  @router.message_reaction() handler — diffs, calls ReactionRepository (only if
  reactions_history_enabled; see Decision 3).
```

Registering `@router.message_reaction()` is also what satisfies the source plan's
`resolve_used_update_types()` note (§2) — no separate `allowed_updates` wiring needed, but
QA-1 already carries the test asserting it.

---

## Decision 3 — Privacy: two independent toggles, not one

Julia's R-1c answer bundles two things that must **not** collapse into a single flag: "короткий
отдельный retention + отдельный privacy-тумблер на запись". Read literally and against what R-5
needs, these decompose into a **master module toggle** and a **separate, more granular
recording toggle**:

- **`reactions_enabled`** (`ChatConfig`, default `False`, module-off by default like
  `sticker_intelligence`/`kb_enabled`) — the module is on for this chat at all. Gates
  *everything*: the handler registering interest in this chat's reactions, and R-5's
  bot-initiated `setMessageReaction` call.
- **`reactions_history_enabled`** (`ChatConfig`, default `True` when the module is on) — gates
  *only* the `INSERT INTO message_reactions`. This is the privacy-sensitive half: recording
  *who* reacted to *what*, a behavioral trail strictly more sensitive than message text (per
  the source plan's own risk framing, §5.2). An owner can disable this while leaving
  `reactions_enabled` on — the bot still reacts to its own tier-3 silences (R-5, which needs no
  history at all: `llm_judge` decides live, per-call), it just stops logging other people's
  reaction behavior.

This split is why R-5 must **not** be gated on `reactions_history_enabled` — R-5's whole value
(reacting instead of a suppressed reply) is independent of whether history recording is on.
Gating R-5 on the history flag would silently break the cheapest, already-approved Phase-1 win
for any chat that opts out of behavioral logging, which is exactly the chat most likely to want
the *feature* without the *tracking*.

Both toggles: nullable `chat_settings` columns, no DEFAULT (Decision 1's DDL), added to
`_CHAT_CONFIG_FIELDS` / `_WRITABLE_COLUMNS` and `ChatConfig` by the R-1/R-D1 implementer,
following the exact pattern `kb_enabled` set in ADR-0003 / migration 014.

### Retention: short, separate, own config key

`message_reactions` gets its own retention window — **not** reused from
`chat_messages_days` (365, tuned for RAG/context continuity, an unrelated concern) or
`response_log_days` (90, cost analytics). Add to `RetentionCleaner`:

```python
# src/database/repositories/maintenance.py
RETENTION_TABLES["message_reactions"] = "created_at"

# src/config.py — MaintenanceSettings
reactions_days: int | None = 30  # short: this is a behavioral trail, not conversation content
```

30 days is a starting recommendation (same order as `abuse_blocked_log_days`, the other
"short, sensitive, recent-signal" table) — a **policy** number, not a technical constraint;
Julia can tune it after Phase 1 ships without a schema change (the column is generic
`created_at`-keyed, same as every other `RETENTION_TABLES` entry).

### Hard rule: raw reaction rows never reach an AI prompt

Julia's decision explicitly excludes this data from external AI. Concretely: `message_reactions`
rows (with `user_id`/`actor_chat_id`) must never be assembled into any `prompt_builder.py`
section or any `AIRouter.generate_text()` call, in Phase 1 or later. This is stricter than the
existing RAG/KB fencing (`sanitize_prompt_content()` + the "USER-GENERATED CONTENT" boundary
sentence, ADR-0003 Part 2) — those fence *content* a user wrote; this fences *who did what*,
which sanitization doesn't touch at all, because the sensitivity is in the row's existence and
attribution, not its text.

**Forward-looking scope note for Phase 2 (R-4, "learn the chat's reaction style"):** R-4's own
note already flags that the LLM-vs-statistics split needs detailed design "при планировании
фазы 2." This ADR adds one hard constraint that design must satisfy: if R-4 feeds a "chat
reaction style" signal to an LLM, it must be a **pre-aggregated, anonymized** frequency profile
(e.g. "🔥 is 3× more common than 👍 in this chat") with no `user_id`, no `message_id`, no raw
rows — never the `message_reactions` table itself, however filtered. That is R-4's design
problem, not this ADR's (Phase 2, not Phase 1 — architect scope for *that* decision arrives with
R-4's own planning), but it's flagged here now so the constraint isn't rediscovered as a review
finding after R-4's schema is already drafted.

---

## Decision 4 — Tier-3 reaction piggyback (R-5): zero extra tokens, fail-closed validation

Per Julia's explicit correction (`human_feedback`, R-5/уточнение Q4): the LLM-chooses-emoji
piggyback is valid **only** on the tier-3 path (`RelevancyGate` → `llm_judge`,
`src/services/relevancy/gate.py:86`), where the AI call is already paid for. No other path
(tier-1 fast_rules, tier-2 engagement, cooldown/blacklist, unfired `random_response_chance`)
gets a reaction in Phase 1 — there's no accumulated history yet for a statistical choice
(that arrives with R-4 in Phase 2), and adding a *dedicated* AI call anywhere just to pick an
emoji was explicitly rejected.

### Mechanism

Extend `llm_judge`'s existing single call (`src/services/relevancy/llm_judge.py`) to also ask
for a suggested reaction when it says NO — one prompt, one response, same call:

```python
_JUDGE_PROMPT = """\
...
Think briefly. On the second-to-last line answer YES or NO.
If NO, on the last line suggest ONE emoji reaction from this list that fits the message,
or NONE if nothing fits: {allowed_emoji}
"""
```

```python
@dataclass(frozen=True)
class JudgeResult:
    should_respond: bool
    suggested_emoji: str | None = None   # NEW — only meaningful when should_respond is False
    ...
```

`GateDecision` gains the same field, passed through unchanged by `RelevancyGate.evaluate()`.
The caller (message handler, on a tier-3 `should_respond=False`) passes
`suggested_emoji` through `ReactionSelector` **before** calling `responder.set_reaction()`.

### Fail-closed validation is mandatory, not optional

The LLM is asked to pick from a fixed list, but it is still free-text output — it can
hallucinate an emoji outside Telegram's allowed set, add whitespace/punctuation, or ignore the
instruction. `ReactionSelector` must:

1. Reject anything not in `ALLOWED_REACTION_EMOJI` (the ~76-emoji list from the source plan
   §3, hardcoded — Telegram's Bot API does not expose this list programmatically; it's static
   platform knowledge, same category as `EXPENSIVE_MODELS` being a hardcoded list in
   `capabilities.py`).
2. Treat parse failure or `NONE` as "no reaction" — **never substitute a default emoji**. A
   wrong-but-plausible guess (e.g. always falling back to 👍) would misrepresent what the LLM
   actually judged, which is worse than reacting to nothing.
3. Leave `available_reactions`-restriction handling to `responder.py` (Decision 2) — that's a
   per-chat, per-message runtime fact only Telegram can tell you at call time (a chat admin can
   restrict the set), not something `ReactionSelector` can pre-validate.

This keeps the "0 extra tokens" property honest: the piggyback only pays for itself if a bad
suggestion degrades gracefully to silence, not to an incorrect-but-confident reaction.

### One reaction per message, media-group note

`Bot.set_message_reaction()` already enforces "as non-premium users, bots can set up to one
reaction per message" and auto-redirects media-group reactions to the first non-deleted message
in the group (verified: `aiogram.client.bot.Bot.set_message_reaction` docstring) — `responder.py`
does not need to reimplement either constraint, just call it and handle the boolean/exception
result.

---

## Decision 5 — Admin-rights diagnostics (R-D1): live check, not a cached column

**Decision:** `bot.get_chat_member(chat_id, bot_id)` is called live, at two moments — (a) when
an admin toggles `reactions_enabled` on for a chat (immediate feedback: "⚠️ бот не
администратор — реакции работать не будут"), and (b) on-demand when the admin panel renders
the module's status line. **No persisted `bot_is_admin`-style column.**

This is a right-altitude call, not an oversight: a cached flag for "is the bot currently an
admin in this chat" is exactly the kind of fact that goes stale the moment an admin demotes the
bot outside of any bot-observable event — Telegram does not push a notification for that. A
stale `TRUE` would recreate the *exact* silent-failure mode this diagnostic exists to prevent
(§5.1 of the source plan: "без прав апдейты о реакциях не приходят молча, без ошибки"), just
one layer up. The live call is cheap (one Bot API round-trip, not in a hot path — only at
toggle-time and admin-panel render) and Telegram is definitionally the source of truth for its
own membership state.

This reuses, rather than introduces, a pattern: CLAUDE.md's existing ADR "Handlers may call
Telegram Bot API as fallback" already covers the admin handler calling `bot.get_chat()` for
missing chat titles — same class of "isolated live Bot API call from a handler, not worth a
service abstraction yet." `get_chat_member` on the bot's own id has no prior call site in this
codebase (verified: `grep -rn "get_chat_member" src/` → empty) — it's a new call, not a new
*pattern*; note this in the implementation so a reviewer doesn't mistake it for scope creep.

`bot_id` is already available as a `dp[]` process-lifetime singleton (CLAUDE.md: "Process-
lifetime singletons via `dp[]`, not Dishka") — reuse it rather than threading the bot's own id
through another path.

---

## Consequences

### Positive

- One migration (018) delivers R-1's full schema plus both privacy toggles; no follow-up
  `ALTER TABLE` anticipated for Phase 2 (R-4/R-9 read the same table, no new columns needed for
  read-only aggregation).
- `ReactionSelector`/`responder.py` are shared, tested-once primitives for R-5 now and R-3/R-8
  later — the plan's own dependency graph (`R-3`, `R-8` → `R-5`) is satisfied by this split.
- The two-toggle privacy model lets an owner keep the cheapest win (R-5) while opting a
  specific chat out of behavioral logging entirely — a real, likely-requested configuration
  this ADR makes possible without a third toggle.
- No FK to `chat_messages` avoids a whole class of insert failures the source plan already
  flagged as certain to occur (retention-expired or unsaved messages).

### Negative / Trade-offs

- `reaction_type='paid'` rows carry no emoji information at all (both `emoji` and
  `custom_emoji_id` NULL) — acceptable since bots can't originate them and Phase 1 has no
  consumer that needs to distinguish *which* paid tier was used, only that a reaction happened.
- `custom_emoji_id` is stored opaque/unresolved — any Phase-1 UI or log line showing "someone
  reacted with a custom emoji" can't render *which* one without an extra API call. Deferred
  deliberately (see Decision 1); revisit if R-9's analytics need it.
- The live `get_chat_member` diagnostic means the admin-panel status line does one extra Bot
  API round-trip on render — acceptable given it's an on-demand admin view, not a hot path, and
  the alternative (a cached flag) reintroduces the exact silent-staleness failure mode R-D1
  exists to close.

---

## Alternatives considered

### A: Store `old_reaction`/`new_reaction` as raw JSONB, diff at read time

Rejected: identical diff logic either way, but pushes the computation into every future reader
(R-4's chat-profile query, R-9's analytics, any ad-hoc admin query) instead of doing it once at
write time. Also loses "action" as a directly filterable/indexable column.

### B: Single `reactions_enabled` toggle covering both recording and bot-initiated reactions

Rejected: collapses two decisions Julia's answer treats as separate ("retention + privacy
toggle" is explicitly *in addition to* the feature working), and would force an owner who wants
R-5's zero-cost reactions but not behavioral logging to give up the entire module. See
Decision 3.

### C: Persist bot's admin status as a `chat_settings` column, refreshed periodically

Rejected: recreates the silent-staleness failure this diagnostic exists to prevent (a bot
demoted between refreshes reports a stale "OK"). A live call at the two moments that matter
(toggle-time, admin-panel render) is cheap enough that caching buys no meaningful latency win
for a real correctness cost. See Decision 5.

### D: Resolve `custom_emoji_id` to its sticker at write time (via `getCustomEmojiStickers`)

Rejected for Phase 1: one extra Bot API call per unique custom-emoji reaction, for a rendering
concern no Phase-1 consumer needs (R-1/R-D1/R-5 only need "was this a custom emoji," not what
it looks like). Store raw, resolve later if/when a consumer needs it.

---

## Implementation notes for backend-dev (R-1, R-5, R-D1) and qa (QA-1)

1. **R-1 / migration 018**: DDL verbatim from Decision 1, one `op.execute()` per statement.
   `ReactionRepository` in `src/database/repositories/reactions.py` (not `modules/reactions/`
   — see Decision 2). Diff logic (`diff()`) belongs in `modules/reactions/models.py` as a pure,
   I/O-free function — unit-test it directly against constructed `old_reaction`/`new_reaction`
   lists, no DB needed for that part.
2. **R-1 / privacy columns**: add `reactions_enabled`, `reactions_history_enabled` to
   `ChatConfig`, `_CHAT_CONFIG_FIELDS`, `_WRITABLE_COLUMNS` — pattern is identical to
   `kb_enabled` (ADR-0003 / migration 014); do not add a `DEFAULT` to the columns.
3. **R-1 / retention**: add `"message_reactions": "created_at"` to `RETENTION_TABLES`
   (`src/database/repositories/maintenance.py`) and `reactions_days: int | None = 30` to
   `MaintenanceSettings` (`src/config.py`).
4. **R-5**: extend `_JUDGE_PROMPT` and `JudgeResult` per Decision 4; add `ALLOWED_REACTION_EMOJI`
   (the ~76-emoji list, source plan §3) and `ReactionSelector` to `modules/reactions/`; add
   `responder.py` wrapping `bot.set_message_reaction()` with `TelegramBadRequest` handling. Gate
   the whole reaction-on-silence behavior on `config.reactions_enabled` only, **not**
   `reactions_history_enabled` (Decision 3).
5. **R-D1**: live `bot.get_chat_member(chat_id, bot_id)` at toggle-time and at admin-panel
   render; no new persisted column. Reuse the `bot_id` `dp[]` singleton. This is a new call
   site, not a new architectural pattern (CLAUDE.md's existing "Handlers may call Telegram Bot
   API as fallback" ADR already covers this class of call).
6. **QA-1**: the diff function (`modules/reactions/models.py::diff`) is a natural target for a
   pure unit test with no testcontainers needed — old/new reaction lists in, added/removed keys
   out. Integration tests (testcontainers) still needed for the actual `INSERT`, the
   `message_reaction` → `allowed_updates` auto-registration check, and the live admin-rights
   probe against a real chat where the bot is *not* an admin (verify it degrades to a warning,
   not a crash).

---

*Document generated as part of ADR-0004 (reactions-2026-08-03 plan, Phase 1).*
*Architect: specialist-architect (universal baseline).*
