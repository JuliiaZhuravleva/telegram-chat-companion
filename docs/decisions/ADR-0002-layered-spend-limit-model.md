# ADR-0002: Layered Spend-Limit Model (Per-Chat / Bot-Global / Per-Operation)

**Status:** accepted  
**Date:** 2026-06-05  
**Plan item:** C-2 (source-fixes-2026-06-04) — depends on B-3  
**Author:** specialist-architect  
**Supersedes / extends:** B-3 (TASK-8) — `SpendLimitService` v1 (bot-global only)  
**Relates to:** C-1 (cost-optimization-analysis.md §8), `src/services/costs/spend_limit.py`,
`src/models/chat_config.py`, `src/database/repositories/response_log.py`

---

## Context

B-3 (TASK-8) shipped `SpendLimitService` with a single bot-global daily cap stored in
`bot_config.daily_spend_limit_usd`.  The enforcement point is the message handler
(`src/bot/handlers/message.py`): **after** each AI response is sent, a warning is
appended if the 24-hour total exceeds the configured limit.

This is sufficient as a single-chat personal bot, but breaks down in three scenarios
identified during C-1 analysis:

1. **Multi-chat deployments** — one heavy chat can consume the entire bot budget,
   silencing all other chats with no granular control.
2. **Per-chat budget allocation** — a group admin may want to give their chat its own
   daily allowance independent of the global cap.
3. **Per-operation runaway** — a `/summary` abuse loop or sticker-pack import can
   saturate a specific task-type budget without hitting the global cap.

C-2 designs a three-layer cap model that is backward-compatible with B-3 and is
**implementable in a single backend-dev task** with no architectural rework.

---

## Decision

Adopt a **three-layer spend-limit hierarchy**:

```
Layer 3 — Per-operation daily cap  (bot-global, task_type granularity)
    └─ Layer 2 — Per-chat daily cap  (per-chat, overrides/supplements layer 1)
         └─ Layer 1 — Bot-global daily cap  (B-3 / TASK-8, already shipped)
```

Each layer is **independent**: exceeding any one layer triggers its own action.
Layers do not subtract from one another — they are checked in parallel.

### Layer 1 — Bot-global daily cap (already implemented, no change)

- **Storage:** `bot_config.daily_spend_limit_usd` (numeric, nullable).
- **Query:** `ResponseLogRepository.get_total_cost(timedelta(days=1))` — all chats, all
  task types.
- **Enforcement:** warn-only, appended to the response message.
- **When checked:** after AI call, in the message handler.
- **No change required from this ADR.**

### Layer 2 — Per-chat daily cap (new)

- **Storage:** `chat_settings.daily_spend_limit_usd` column (numeric, nullable).
  `NULL` means "no per-chat limit; fall through to global only".
- **ChatConfig field:** `daily_spend_limit_usd: Decimal | None = None` — added to the
  frozen dataclass.  Injected via the existing three-layer config merge.
- **Query:** new `ResponseLogRepository.get_total_cost_for_chat(chat_id, interval)`:
  ```sql
  SELECT COALESCE(SUM(cost_usd), 0)
  FROM response_log
  WHERE chat_id = $1
    AND created_at > NOW() - $2::interval
  ```
- **Enforcement mode:** configurable per-chat via `chat_settings.spend_limit_mode`
  (varchar, default `'warn'`, enum `'warn' | 'block'`).
  - `'warn'`: append a localised warning (same style as Layer 1).
  - `'block'`: skip AI generation and return a hard-limit message.  The pipeline
    must check this **before** the AI call (Stage 4 of `TextProcessingPipeline`).
- **Priority:** per-chat limit is evaluated independently of the global limit.
  Both can fire on the same message if both are exceeded.

#### Enforcement point for `'block'` mode

In `TextProcessingPipeline.process()`, insert a guard **before Stage 4**
(AI generation):

```python
if chat_config.daily_spend_limit_usd is not None:
    chat_total = await spend_limit_svc.get_today_total_for_chat(chat_id)
    if chat_total > chat_config.daily_spend_limit_usd:
        if chat_config.spend_limit_mode == "block":
            return SpendBlockedResult(...)   # new result variant
        # warn path falls through to post-send logic
```

The `'warn'` path for per-chat is checked **after** the AI call (same as Layer 1),
so a single DB query covers both layers at the same enforcement point.

#### Result caching

The per-chat cost query adds 1 DB round-trip per pipeline invocation.  Cache the
result for **60 seconds** (same TTL as `ChatConfigMiddleware`) using a simple
`dict[int, (Decimal, float)]` in `SpendLimitService` keyed by `chat_id`.

A 60-second window means a burst can exceed the per-chat limit by at most 60 seconds
of traffic, which is bounded and acceptable.

**Do not** cache the global query (it covers all chats; invalidation on per-chat
writes is complex).  The global query runs ~1–5 ms on the indexed `response_log` table
and is already fire-and-forget post-send.

### Layer 3 — Per-operation daily cap (new, global only)

- **Storage:** `bot_config.per_task_type_daily_limit_usd` — a JSON object,
  e.g. `{"summary": 0.10, "vision": 0.20}`.  `NULL` means disabled.
- **Query:** `ResponseLogRepository.get_cost_by_task_type(timedelta(days=1))` — already
  implemented; no new query needed.  Filter by task_type client-side in the service.
- **Enforcement:** warn-only (no block mode for Layer 3 — operation-level blocking
  requires threading the check through every call site, which is out of scope).
- **When checked:** post-send, same enforcement point as Layer 1.
- **Caller:** `SpendLimitService.get_warnings_if_exceeded(chat_id, lang, task_type)`.

---

## `SpendLimitService` API revision

The revised service encapsulates all three layers.  The handler call site stays unchanged:
`await spend_limit_svc.get_warnings_if_exceeded(...)`.

```python
@dataclass
class SpendWarning:
    layer: Literal["global", "per_chat", "per_operation"]
    message: str

class SpendLimitService:

    async def get_today_total_for_chat(self, chat_id: int) -> Decimal:
        """Per-chat cost total (last 24 h).  Result cached 60 s."""

    async def check_global(self) -> tuple[Decimal | None, Decimal, bool]:
        """(limit, today_total, is_exceeded) for the global cap (Layer 1)."""

    async def check_per_chat(
        self, chat_id: int, limit: Decimal
    ) -> tuple[Decimal, bool]:
        """(today_total_for_chat, is_exceeded) for Layer 2."""

    async def check_per_operation(
        self, task_type: str
    ) -> tuple[Decimal | None, Decimal, bool]:
        """(limit, today_total_for_task_type, is_exceeded) for Layer 3."""

    async def get_warnings_if_exceeded(
        self,
        chat_id: int,
        lang: str = "ru",
        task_type: str = "text",
        per_chat_limit: Decimal | None = None,
    ) -> list[SpendWarning]:
        """
        Return a (possibly empty) list of warnings for all exceeded layers.
        Never raises — safe to call after every AI response.
        """

    async def is_blocked(
        self,
        chat_id: int,
        per_chat_limit: Decimal | None,
        spend_limit_mode: str,
    ) -> bool:
        """
        True if per-chat limit is exceeded AND mode == 'block'.
        Called BEFORE AI generation in the pipeline.
        """
```

The handler becomes:

```python
# Before AI call (block check)
if await spend_limit_svc.is_blocked(
    chat_id=message.chat.id,
    per_chat_limit=chat_config.daily_spend_limit_usd,
    spend_limit_mode=chat_config.spend_limit_mode,
):
    await message.answer(_BLOCK_TEXT[chat_config.language])
    return

# ... AI generation ...

# After AI call (warn check, all layers)
warnings = await spend_limit_svc.get_warnings_if_exceeded(
    chat_id=message.chat.id,
    lang=chat_config.language,
    task_type=result.task_type,
    per_chat_limit=chat_config.daily_spend_limit_usd,
)
for w in warnings:
    await message.answer(w.message)
```

---

## Database changes required

### Migration 013 — `chat_settings` columns

```sql
ALTER TABLE chat_settings
  ADD COLUMN IF NOT EXISTS daily_spend_limit_usd NUMERIC(12,8),
  ADD COLUMN IF NOT EXISTS spend_limit_mode VARCHAR(10) NOT NULL DEFAULT 'warn';
```

`spend_limit_mode` is constrained to `{'warn', 'block'}` by application code (not a DB
`CHECK` constraint — avoids a migration for future modes).

### No changes to `response_log` or `bot_config` schema.

### `ResponseLogRepository` additions

```python
async def get_total_cost_for_chat(
    self, chat_id: int, interval: timedelta
) -> Decimal:
    """Total cost for a specific chat within the given interval."""
```

The `get_cost_by_task_type()` method (already implemented) is reused for Layer 3
without modification.

---

## `ChatConfig` additions

```python
# In src/models/chat_config.py
daily_spend_limit_usd: Decimal | None = None   # Layer 2 cap; None = no per-chat limit
spend_limit_mode: str = "warn"                  # 'warn' | 'block'
```

`ChatConfigService` reads these from `chat_settings` via the existing merge.

---

## Consequences

### Positive

- **Backward-compatible:** Layer 1 (B-3) behavior unchanged. Chats with no
  `chat_settings` entry experience no behavior change.
- **Operator control:** per-chat limits can be set via the admin panel without
  redeploying. The config merge means per-chat overrides are live within 60 s.
- **Hard-block option:** production operators can set `spend_limit_mode = 'block'`
  for shared/untrusted chats; personal chats stay on `'warn'`.
- **Layer 3 (per-operation) is low-cost to add:** existing `get_cost_by_task_type()`
  already returns the data; only the service logic is new.

### Negative / Trade-offs

- **One extra DB query per message** for the per-chat cost check (Layer 2).
  Mitigated by 60-second in-memory cache.  The cache is per-process (no Redis); on
  multi-process deploys each process caches independently — acceptable for a warn/block
  that can lag by ≤60 s.
- **Per-chat blocking adds a `SpendBlockedResult` variant** to the pipeline return type.
  Backend-dev must thread this through without breaking the existing result handling.
- **Layer 3 warn fires globally**: if `summary` exceeds its task-type cap, ALL chats
  receive the warning on their next `summary` call.  This may be confusing in
  multi-chat deployments.  Accept for v1 — per-chat per-operation limits are Phase 2.
- **`chat_id=0` rows** (sticker/embedding operations — GAP-1 from C-1 analysis) are
  included in the global total but excluded from per-chat totals.  This means per-chat
  totals are conservative (lower than actual bot-wide cost attributable to that chat).
  Fix GAP-1 (thread `chat_id` through `analyze_image`/`generate_embedding`) to close
  this gap; tracked separately in C-1 §9.

---

## Phased implementation plan

| Phase | Content | Dependencies |
|-------|---------|--------------|
| **Phase 1** (this ADR) | Migration 013, `ChatConfig` fields, `ResponseLogRepository.get_total_cost_for_chat()`, `SpendLimitService` v2, handler wiring | B-3 done ✅ |
| **Phase 2** (future) | Per-chat per-operation limits (extend Layer 3 to `chat_settings`); per-chat `/costs` breakdown | Phase 1 done |
| **Phase 3** (future) | Thread `chat_id` through sticker/embedding call sites (GAP-1) so per-chat totals are accurate | Phase 1 done |

**Phase 1 is the implementation scope of C-2.**

---

## Alternatives considered

### A: Per-chat limits as `bot_config` keys with chat_id suffix

Store as `chat_123456_daily_limit_usd` in `bot_config`.

**Rejected:** does not scale to many chats; not queryable without a table scan;
breaks the existing three-layer config merge pattern.

### B: Single global cap only, no per-chat layer

Keep B-3 as-is; add per-operation caps only.

**Rejected:** the primary multi-chat scenario is unaddressed.  Per-chat control is
the most operationally useful layer and is the lowest implementation risk.

### C: Block globally at the pipeline level using the global cap

Hard-block all chats when the global cap is exceeded, not just warn.

**Rejected:** too blunt.  The global cap is an emergency safeguard; the per-chat cap
is the right granularity for blocking.  A global block during a sticker import (which
hammers the global total) would silence unrelated chats.

### D: Use Redis / shared cache for per-chat cost totals

Replace in-process dict with Redis so multi-process deploys share the cache.

**Rejected:** the project has no Redis dependency; adding one for a 60-second cache
is disproportionate.  Accept per-process cache staleness of ≤60 s.

---

## Implementation notes for backend-dev

1. Create `alembic/versions/013_spend_limit_per_chat.py` with the migration above.
2. Add `daily_spend_limit_usd` and `spend_limit_mode` to `ChatConfig` (import `Decimal`
   from `decimal`).
3. Update `ChatConfigService._merge()` to read the new columns from the `chat_settings`
   row and populate the `ChatConfig` fields.
4. Add `ResponseLogRepository.get_total_cost_for_chat(chat_id, interval)`.
5. Refactor `SpendLimitService` per the API above.  Keep `get_warning_if_exceeded()`
   as a deprecated alias for one release to avoid breaking tests.
6. Wire the pre-call `is_blocked()` check and post-call `get_warnings_if_exceeded()`
   into `src/bot/handlers/message.py` (or the pipeline directly if cleaner).
7. Tests:
   - Unit: `SpendLimitService.is_blocked()`, `get_warnings_if_exceeded()` with all three
     layers (each exceeded, none exceeded, combinations).
   - Unit: `ChatConfigService` merge with `daily_spend_limit_usd` and `spend_limit_mode`.
   - Integration: `ResponseLogRepository.get_total_cost_for_chat()` round-trip.

---

*Document generated as part of C-2 (source-fixes-2026-06-04 plan).*  
*Architect: specialist-architect (universal baseline).*
