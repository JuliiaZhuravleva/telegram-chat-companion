# AI Cost Optimization Analysis

**Status:** draft  
**Authored:** 2026-06-05 (C-1 / source-fixes-2026-06-04)  
**Relates to:** Section B (B-1 TASK-6, B-2 TASK-7, B-3 TASK-8), C-2, C-3

---

## 1. Purpose

Map every AI call path, verify cost-logging completeness, identify
optimization opportunities, and produce a prioritized recommendation
backlog for the Section-B and C work items.

---

## 2. Current Cost Architecture

### 2.1 Pricing layer

`src/services/ai/pricing.py` — static `MODEL_PRICING` dict + `calculate_cost()`.
All costs are computed **in-process** from provider-returned token counts.
No provider offers a live pricing API; table must be maintained manually.

### 2.2 Schema

`response_log` table (migration 012 added the cost columns):

| Column | Type | Notes |
|--------|------|-------|
| `chat_id` | bigint | Per-chat attribution |
| `user_id` | bigint | Nullable (not all callers have user context) |
| `provider` | varchar | openai / gemini / deepseek / grok |
| `model` | varchar | Exact model string |
| `task_type` | varchar(50) | text / summary / embedding / vision / transcription / relevancy_check / sticker_merge / sticker_context |
| `tokens_input` | int | Nullable |
| `tokens_output` | int | Nullable |
| `cost_usd` | numeric(12,8) | Nullable; 0 for free models |
| `duration_seconds` | float | Audio only |
| `created_at` | timestamptz | Auto-set by DB default |

Partial index on `(created_at DESC) WHERE cost_usd IS NOT NULL` for aggregation queries.

### 2.3 Repository aggregations

`ResponseLogRepository` ships four read methods (all take a `timedelta` window):

- `get_total_cost(interval)` — scalar total
- `get_cost_by_model(interval)` — provider + model + task_type breakdown
- `get_cost_by_task_type(interval)` — task-level rollup
- `get_cost_by_provider(interval)` — provider-level rollup

Cross-check against OpenAI's Costs API is available via `OpenAIBillingClient`
(billing.py, admin panel "Verify" button).  Admin panel already shows cost data
(TASK-2, done).

---

## 3. AI Call Map

The table below lists every place AI is invoked and whether cost is currently
logged to `response_log`. ✅ = logged, ⚠️ = partial, ❌ = not logged.

| Call site | Operation | Model tier | task_type logged | chat_id attribution |
|-----------|-----------|------------|-----------------|---------------------|
| `TextProcessingPipeline._safe_log_response()` | text generation | cheap (default) | `"text"` ✅ | ✅ full chat_id |
| `RAGMemoryService.search()` | embedding | **free** | `"embedding"` ✅ (via router auto-log) | ❌ chat_id=0 |
| `RAGMemoryService.store()` | embedding | **free** | `"embedding"` ✅ (via router auto-log) | ❌ chat_id=0 |
| `StickerLearningService.learn()` → vision | vision analysis | moderate (grok-2-vision / gpt-5-nano) | `"vision"` ✅ (via router `analyze_image()` auto-log) | ❌ chat_id=0 |
| `StickerLearningService._search_pack_context_via_ai()` | text gen | cheap | `"sticker_context"` ✅ | ❌ chat_id=0 |
| `StickerLearningService.merge_admin_description()` | text gen | **expensive** (o4-mini, approved) | `"sticker_merge"` ✅ | ❌ chat_id=0 |
| `StickerLearningService._generate_and_store_embedding()` | embedding | **free** | `"embedding"` ✅ | ❌ chat_id=0 |
| `StickerResponderService` search | embedding | **free** | `"embedding"` ✅ | ❌ chat_id=0 |
| `SummaryService.generate()` | text gen | cheap | `"summary"` ✅ | ✅ chat_id via log_usage() |
| `RelevancyGate.evaluate()` tier-3 | text gen (LLM judge) | cheap (gpt-5-nano) | `"relevancy_check"` ✅ | ✅ chat_id passed explicitly |
| `AbilityFilter` (abuse embedding) | embedding | **free** | `"embedding"` ✅ | ❌ chat_id=0 |

**Key findings:**

1. All call paths log to `response_log`. No silent black holes. ✅
2. All **free** operations (embeddings via gemini-embedding-001) correctly produce
   `cost_usd = 0` — they add rows but no budget impact.
3. **chat_id attribution** is missing for all operations that happen outside the
   main text pipeline (stickers, RAG, embeddings, abuse filter). These land as
   `chat_id = 0` and are invisible in per-chat cost reports.
4. `asyncio.ensure_future()` is used for fire-and-forget logging in vision,
   transcription, and embedding paths. Under process shutdown or unhandled
   exceptions in the event loop, these futures can be silently dropped.

---

## 4. Token Budget Analysis

### 4.1 Input token hot spots

**Chat text generation (TextProcessingPipeline):**

The system prompt is assembled from up to 10 sections (see `prompt_builder.py`).
The user prompt includes up to 30 chat history messages (20 current topic + 10
other topics in forum mode). No hard cap on total input tokens is enforced.

Rough upper-bound estimate for an active chat:

| Section | Est. tokens |
|---------|-------------|
| Base personality | ~150 |
| Security boundary | ~50 |
| Language + formatting rules | ~60 |
| Fatigue / jailbreak (conditional) | 0–100 |
| Reply context (conditional) | 0–200 |
| RAG memories (up to 5 × ~80 tok) | 0–400 |
| Link context (conditional) | 0–300 |
| Sticker candidates (conditional) | 0–200 |
| Adaptive length | ~30 |
| Chat history (up to 30 msgs × ~40 tok) | ~1 200 |
| Current message | ~50 |
| **Total system + user input** | **~1 200 – 2 740** |

At gpt-5-nano pricing ($0.05/1M input), 2 500 tokens ≈ **$0.000125 per call**.
At 1 000 calls/day that is $0.125/day — acceptable. But with expensive fallback
models (gemini-3-pro, gpt-5.2) a single day at 1 000 calls/day could reach
**$5–$20** if the router falls back to premium models.

**Summary generation:**

`SummaryService.generate()` uses `max_tokens=8000` output. For 100 messages of
~30 words each the input is already ~3 000 tokens. A 8 000-token output budget
exceeds what a typical chat summary needs by a factor of 4–8×. This inflates
the *potential* cost ceiling even if the model rarely hits the limit.

At gemini-3-flash pricing ($3.00/1M output), 4 000 tokens used ≈ $0.012 per
summary — modest for single-user use. At scale with many `/summary` calls this
becomes the dominant task-type cost.

**Sticker vision analysis:**

Vision calls (new stickers only) carry a 6-frame collage PNG. grok-2-vision at
$2.00/$10.00 per 1M tokens, estimated ~500 input + 200 output tokens ≈ **$0.003
per sticker**. Once a sticker is seen, it's never re-analyzed (cache hit in DB).
Total corpus cost = number of unique stickers × $0.003.

### 4.2 Output token observations

`_BASE_MAX_TOKENS = 2000` for main pipeline. This is the hard ceiling per
generation call. Actual output is typically 50–300 tokens for chat replies.
The gap between ceiling and actual is fine — the model stops when done.

---

## 5. Identified Gaps and Bugs

### GAP-1 — No per-chat cost attribution for sticker and embedding operations (chat_id=0)

**Impact:** Admin cost view cannot break down sticker analysis, pack enrichment,
or admin merges by originating chat. All land in a single `chat_id=0` bucket.

**Root cause:** `AIRouter._log_usage()` defaults `chat_id=0`. The sticker and
RAG call sites do not pass a `chat_id` argument to the router methods, and the
router does not accept `chat_id` on `analyze_image()`, `generate_embedding()`.
Only `log_usage()` (the explicit caller path) accepts `chat_id`.

**Fix path:** Add an optional `chat_id` parameter to `AIRouter.analyze_image()`
and `AIRouter.generate_embedding()`. Thread it through from the call sites.
Low risk, small scope.

### GAP-2 — asyncio.ensure_future logging is not awaited (silent drop risk)

**Impact:** Under abnormal shutdown or unhandled exceptions, embedding and
vision usage rows may never be written. Current code at `router.py:299–306`
and `303–310`:

```python
asyncio.ensure_future(
    self._log_usage(task_type="embedding", …)
)
```

**Fix path:** Collect futures and `await asyncio.gather(*futures, return_exceptions=True)`
before returning, or accept the minor latency hit and `await self._log_usage(…)`
directly. The `_log_usage()` method already wraps itself in try/except and
never raises.

### GAP-3 — Pricing table is manually maintained, last verified 2026-02-10

**Impact:** Model price changes go undetected. Cost estimates in the admin
panel may drift from reality over time.

**Fix path:** Add a lightweight test or scheduled check that cross-verifies
`calculate_cost()` totals against OpenAI's Costs API (already implemented in
`billing.py` + admin panel) for the OpenAI slice. For Gemini/DeepSeek/Grok,
no machine-readable pricing API exists — note in a comment and set a calendar
reminder to review quarterly.

### GAP-4 — Summary `max_tokens=8000` is excessive

**Impact:** The model can generate up to 8 000 tokens of output per summary
call. Typical useful summaries are 400–800 tokens. The current ceiling wastes
budget on no-op padding if the model fills it (rare but possible).

**Fix path:** Reduce to 2 000 tokens (still 4× a reasonable summary) or make
it configurable via `AITaskConfig`. See REC-4 below.

### GAP-5 — No global or per-chat spend limit enforcement

**Impact:** A misconfigured fallback chain or sudden spike (jailbreak loop,
sticker pack import) can run up unbounded costs before an admin notices.

**Fix path:** B-3 / TASK-8 addresses this. See C-2 for layered design.

---

## 6. Optimization Recommendations

Ordered by effort-to-impact ratio (best first).

### REC-1 — Reduce summary `max_tokens` ceiling (trivial, immediate)

**Effort:** 15 minutes  
**Saving:** up to 75% of summary output costs  
**Action:** In `SummaryService.generate()`, change `max_tokens=8000` →
`max_tokens=2000`. Optionally expose as `AITaskConfig` param.  
**Risk:** Low. If a very large summary is legitimately needed, the user can
request `/summary 200` which naturally extends the conversation block.

### REC-2 — Clamp chat history to token budget (C-3 overlap)

**Effort:** 4–6h (investigation + implementation)  
**Saving:** 20–40% of text-generation input cost on long-history chats  
**Action:** In `prompt_builder.py`, implement a `trim_history_to_budget()`
function that counts approximate tokens (chars÷4 heuristic is sufficient)
for the history block and drops oldest messages when the budget is exceeded.
Suggested ceiling: 800 tokens for history (≈20 typical messages). The most
recent 5 messages should always be preserved.  
**Risk:** Low. History is already capped at 30 records by the DB query;
trimming is a further soft cap on token spend, not on logical completeness.  
**Note:** This is the same as C-3 (RAG context-size audit). Recommend folding
C-3 into this ticket.

### REC-3 — Add chat_id threading for sticker/vision operations (GAP-1)

**Effort:** 2–3h  
**Saving:** No direct cost saving; enables accurate per-chat cost attribution  
**Action:** Add `chat_id: int = 0` to `AIRouter.analyze_image()` and
`AIRouter.generate_embedding()`. Update call sites in
`StickerLearningService` and `RAGMemoryService`.  
**Risk:** Low — backward compatible (defaults to 0).

### REC-4 — Await logging futures instead of fire-and-forget (GAP-2)

**Effort:** 1h  
**Saving:** Prevents silent cost-row drops  
**Action:** In `AIRouter.generate_embedding()`, `analyze_image()`,
`transcribe_audio()`: await `self._log_usage(…)` directly. The method is
non-critical and already catches all exceptions internally.  
**Risk:** Adds <1ms latency to each call (DB round-trip is already happening
in the main flow). Acceptable.

### REC-5 — Add a provider-fallback cost guard (REC)

**Effort:** 3–4h  
**Saving:** Prevents accidental expensive-model usage under fallback  
**Action:** In `AIRouter._get_provider_chain()`, check whether any fallback
model is in `EXPENSIVE_MODELS` and emit a `logger.warning()` when the active
chain includes one. Optionally allow configuration of a `max_cost_per_call_usd`
threshold that refuses to route to expensive models above it.  
**Risk:** Low.

### REC-6 — Periodic pricing-table review reminder (GAP-3)

**Effort:** 5 minutes  
**Action:** Add a `# REVIEWED: <date>` comment to `pricing.py` and a checklist
item in `docs/setup.md` to update the table quarterly. Also add a test
assertion that all providers with active `is_available()` have at least one
entry in `MODEL_PRICING`.  
**Risk:** None.

---

## 7. Cost Model by Task Type (baseline estimates)

These assume a single active group chat.

| Task type | Frequency | Cost/call (est.) | Daily cost (est.) |
|-----------|-----------|-----------------|-------------------|
| text (chat response) | 100–500/day | $0.00013 | $0.013–$0.065 |
| relevancy_check (LLM tier) | 5–50/day | $0.00002 | $0.0001–$0.001 |
| summary | 1–5/day | $0.003–$0.01 | $0.003–$0.05 |
| vision (sticker learn, new only) | 0–20/day | $0.003 | $0–$0.06 |
| sticker_merge (admin, rare) | 0–2/day | $0.002 (o4-mini) | $0–$0.004 |
| embedding (free) | many | $0 | $0 |

**Takeaway:** For a single chat with normal usage, daily cost is well under
$0.15. The risk cases are:

1. **Fallback storm** — if the primary provider goes down and all requests fall
   through to a premium model (gpt-5.2 at $14/1M output), 500 calls/day →
   $7/day.
2. **Summary abuse** — a user hammering `/summary` with the current 8000-token
   ceiling on an expensive fallback model could generate $0.50+ per call.
3. **Sticker pack import** — importing a 120-sticker pack triggers 120 vision
   calls ≈ $0.36 one-time.

---

## 8. Dependency and Sequencing Notes

### Relation to B-1 (TASK-6)
B-1 ships the persistence of cost per provider call. The schema work is
complete (migration 012) and `ResponseLogRepository.log()` already accepts
`cost_usd`. B-1's remaining work is confirming that **all** call paths actually
write a row (see Section 3 above — they do). B-1 should also fix GAP-1 and
GAP-2 as part of its scope.

### Relation to B-2 (TASK-7)
B-2 exposes costs via `/costs` command. Data is already queryable via
`get_total_cost()` and `get_cost_by_model()`. B-2 is purely a handler/UX task.

### Relation to B-3 / C-2 (TASK-8 / layered spend limit)
B-3 adds a configurable daily limit. C-2 generalizes to per-chat + bot-global +
per-operation caps. The cheapest implementation is:

1. Add `daily_spend_limit_usd` to `chat_settings` (per-chat, nullable → falls
   back to global).
2. Add `bot_daily_spend_limit_usd` to `bot_config` (global).
3. In `TextProcessingPipeline.process()`, before Stage 4 (AI generation), call
   `response_log_repo.get_total_cost(timedelta(days=1))`. If over limit, return
   early with a user-visible warning.
4. The check adds 1 DB query per pipeline invocation. Cache the result with a
   5-minute TTL (acceptable: a burst could exceed the limit by at most 5
   minutes of traffic, which is bounded).

**Warning:** The pipeline check only enforces on text-generation calls. Sticker
vision analyses and admin merge calls are unguarded. A per-operation guard
would require threading the limit check through additional call sites.

### Relation to C-3 (RAG context-size audit)
C-3 should be merged with REC-2. The overlap is 100%: both ask "are we sending
too many tokens in RAG + history context?"

---

## 9. Suggested Backlog Additions

After this analysis, two new sub-tasks are worth adding to the plan:

| Proposed item | Effort | Priority | Rationale |
|---------------|--------|----------|-----------|
| Fix GAP-1: thread chat_id through analyze_image/generate_embedding | 2h | P3 | Cost attribution accuracy |
| Fix GAP-2: await log futures instead of ensure_future | 1h | P3 | Data reliability |
| Fold C-3 into REC-2 (history token budget) | 4h | P2 (C-3 already P2) | Avoids duplicate planning |

---

*Document generated as part of C-1 (source-fixes-2026-06-04 plan).*
*Architect: specialist-architect (universal baseline).*
