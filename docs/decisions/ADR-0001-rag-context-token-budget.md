# ADR-0001: RAG + Chat History Context Token Budget

**Status:** accepted  
**Date:** 2026-06-05  
**Plan item:** C-3 (source-fixes-2026-06-04)  
**Author:** specialist-architect  
**Relates to:** C-1 REC-2, `src/services/text/prompt_builder.py`, `src/services/rag/memory.py`, `src/services/text/pipeline.py`

---

## Context

C-3 commissioned a full audit of the RAG retrieved-context and chat-history token budget to
determine whether the pipeline can send an unbounded volume of tokens to the AI model on
long-running chats, and to specify trimming bounds if so.

### Audit findings

#### 1. Chat history — unbounded per-message length

`get_recent_with_topic_context()` returns up to **30 rows** (20 current topic + 10 other
topics). The DB query caps row count but not row content length. A single long message
(pasted document, copied code snippet) can expand the history block dramatically.

`_format_message()` in `prompt_builder.py` emits each message verbatim with no length cap.

#### 2. RAG memories — row count capped, content length unbounded

`RAGMemoryService.search()` caps at **5 results** (`max_results=5`, `min_similarity=0.7`).
(The 0.65 originally recorded here was a dead constructor default; S2-2 removed it and
made the YAML the single source. R1 later moved the filtering itself out of SQL and into
the service, which is where the cap is applied today.)
Each memory is stored as a full Q&A pair:

```
Q: <user message>
A: <bot response>
```

If the bot's response was long (adaptive length kicked in for a verbose chat), a single
memory entry can exceed 300 tokens. At 5 memories that is 1 500 tokens of RAG context alone.

#### 3. Reply context — already capped

`_reply_section()` truncates `reply_text` to **500 chars** before injecting it. ✅

#### 4. Link context — no cap

`format_link_context_section()` emits link metadata with no length limit. A URL with a
large page title / description can add 200–500 tokens. ⚠️ (out of scope for this ADR —
the link extractor is a separate service; cap should live in `format_link_context_section`.)

#### 5. `prompt_builder.py` — no total input budget guard

`build_system_prompt()` and `build_user_prompt()` concatenate all sections without counting
aggregate token cost. There is no early exit or truncation based on a total budget.

#### 6. Worst-case estimate

| Section | Worst-case tokens |
|---------|------------------|
| System prompt (all conditional sections) | ~900 |
| RAG memories (5 × long Q&A) | ~1 500 |
| Chat history (30 × long message, ~150 chars each) | ~1 200 |
| Reply context (capped 500 chars) | ~125 |
| Link context (uncapped) | ~300 |
| Current message | ~100 |
| **Worst-case total input** | **~4 125** |

At gpt-5-nano this is $0.0002 per call — acceptable. The risk materialises when the router
falls back to a premium model (gpt-5.2 at $3.75/1M input): the same call costs $0.015.
A fallback storm (1 000 calls/day) × ($0.015 - $0.000125) ≈ **$15/day** additional input
cost from unguarded context alone.

The secondary risk is context *quality*: ancient messages from other forum topics dilute
relevance and reduce response quality.

---

## Decision

Adopt a **soft token budget** for the context sections of the assembled prompt. The budget
is enforced at prompt-assembly time, not at the AI call site, so it is provider-agnostic.

### Token estimation

Use the `chars ÷ 4` heuristic (sufficient for Russian + English mixed text; no tokenizer
dependency). This is already the industry-standard approximation for rough budgeting at
this scale. A ±20% error is acceptable — we are setting soft ceilings, not hard limits.

### Budget constants (configurable at module level in `prompt_builder.py`)

| Constant | Value | Rationale |
|----------|-------|-----------|
| `CONTEXT_BUDGET_TOKENS` | 1 200 | History + RAG combined ceiling |
| `HISTORY_BUDGET_TOKENS` | 800 | History block alone |
| `RAG_BUDGET_TOKENS` | 400 | RAG block alone (5 × ~80 tokens nominal) |
| `MAX_MSG_CHARS` | 400 | Per-message content cap (≈ 100 tokens) |
| `MAX_MEMORY_CHARS` | 600 | Per-memory content cap (≈ 150 tokens) |
| `HISTORY_MIN_RECENCY` | 5 | Most-recent N messages always preserved |

`CONTEXT_BUDGET_TOKENS = HISTORY_BUDGET_TOKENS + RAG_BUDGET_TOKENS`. The split is
**not additive at call time** — RAG and history are budgeted independently so that a
long history does not crowd out RAG (and vice versa).

### Trimming algorithm — chat history

Called in `build_user_prompt()` before assembling the `<chat_history>` block:

```python
def trim_history_to_budget(
    messages: list[dict], 
    budget_tokens: int = HISTORY_BUDGET_TOKENS,
    min_recency: int = HISTORY_MIN_RECENCY,
) -> list[dict]:
    """Drop oldest messages that would exceed budget.

    Always preserves the most-recent `min_recency` messages,
    regardless of token cost.

    Messages are assumed to arrive oldest-first (pipeline reverses
    the DESC DB order before passing to PromptContext).
    """
```

1. Per-message content is **pre-capped** to `MAX_MSG_CHARS` (truncate with `"…"` suffix).
2. Budget is consumed from oldest to newest.
3. The most-recent `HISTORY_MIN_RECENCY` messages are always included, even if they alone
   exceed the budget. (A full-context override is better than a confusing empty history.)
4. Messages that overflow the budget are silently dropped (not replaced with a placeholder —
   the prompt already says "last messages" which implies possible omissions).

### Trimming algorithm — RAG memories

Called in `_rag_section()`:

```python
def trim_memories_to_budget(
    memories: list[dict],
    budget_tokens: int = RAG_BUDGET_TOKENS,
) -> list[dict]:
    """Drop lowest-similarity memories that would exceed budget."""
```

1. Per-memory content is **pre-capped** to `MAX_MEMORY_CHARS`.
2. Memories are already ordered by descending similarity (highest first from the DB query).
3. Iterate from highest to lowest similarity, accumulating token cost; stop when budget is
   exhausted. Drop the tail.
4. Because similarity ordering is best-first, this naturally prefers quality over quantity.

---

## Consequences

### Positive

- **Cost ceiling tightened**: worst-case context tokens drop from ~4 125 to ~1 800
  (900 system + 800 history + 400 RAG + 100 current message + 300 link/reply).
- **Quality preserved**: the most-recent and most-relevant content is always kept.
- **Provider-agnostic**: no tokenizer dependency; works across all providers.
- **No behavioral change on short chats**: the trim functions are no-ops when content
  is already within budget (the common case).

### Negative / Trade-offs

- **Information loss on very long messages**: a 2 000-char message gets cut to 400 chars.
  Accept this — a message that long is almost certainly a pasted document or wall-of-text,
  and the model benefits more from the fresh parts than from the tail.
- **Heuristic token count**: `chars ÷ 4` under-counts Russian text by ~15% (Cyrillic
  tokenises at ~1.15 chars/token vs ~4 chars/token for English). The effect is that the
  Russian budget is slightly tighter than intended, which is conservative (safe direction).
- **HISTORY_MIN_RECENCY override**: if the last 5 messages collectively exceed 800 history
  tokens, the budget guard is bypassed. This is intentional — coherence of the immediate
  context trumps cost optimisation.

---

## Implementation hooks

Backend-dev implements the following (no ADR change needed; this section is the design spec):

### 1. `src/services/text/prompt_builder.py`

- Add module-level constants: `CONTEXT_BUDGET_TOKENS`, `HISTORY_BUDGET_TOKENS`,
  `RAG_BUDGET_TOKENS`, `MAX_MSG_CHARS`, `MAX_MEMORY_CHARS`, `HISTORY_MIN_RECENCY`.
- Add helper `_est_tokens(text: str) -> int` → `max(1, len(text) // 4)`.
- Add `trim_history_to_budget(messages, budget_tokens, min_recency)` — pure function,
  returns a trimmed list.
- Add `trim_memories_to_budget(memories, budget_tokens)` — pure function.
- In `build_user_prompt()`: call `trim_history_to_budget()` on `ctx.recent_messages`
  **after** the existing per-message `_format_message()` pass, or integrate cap into
  `_format_message()` (cap content before formatting, then budget-trim the list).
- In `_rag_section()`: call `trim_memories_to_budget()` before rendering.

### 2. Logging

- Add a `structlog.debug()` call when trimming actually fires, recording
  `messages_dropped`, `memories_dropped`, and `tokens_estimated`. This lets us
  observe whether the trim fires in real traffic without cluttering production logs.

### 3. Tests (`tests/unit/test_prompt_builder.py`)

Cover at minimum:
- `trim_history_to_budget`: empty input, all-fit input, overflow-drop-oldest,
  min-recency override (last 5 always kept even if over budget).
- `trim_memories_to_budget`: empty input, all-fit, overflow-drops-lowest-similarity.
- `build_user_prompt()` end-to-end with 30 messages of 500 chars each → assert output
  token estimate ≤ `HISTORY_BUDGET_TOKENS * 1.2` (20% slack for heuristic).

### 4. No DB or migration changes required.

---

## Alternatives considered

### A: Hard limit at DB query level

Lower `current_topic_limit` from 20 to 10 in `get_recent_with_topic_context()`.

**Rejected**: row-count reduction is blunt; a chat with 10 short messages loses nothing,
while a chat with 10 long messages still overflows. Token budgeting in the prompt layer
is more precise.

### B: Tokenizer-based exact counting

Use `tiktoken` (OpenAI) or provider SDKs to count exact tokens per message.

**Rejected**: `tiktoken` is OpenAI-specific; Gemini and DeepSeek tokenise differently.
Adding a tokenizer per provider adds a dependency and complexity that is not justified
for the 20–30% accuracy gain over `chars ÷ 4` in this context.

### C: Summarise old history before dropping

When history overflows, summarise the oldest N messages with a cheap AI call before
dropping them.

**Rejected**: adds latency and a nested AI call to every pipeline invocation where
history is long. The value is marginal — old history in a busy chat is already low
relevance. Use RAG for long-term memory instead.

---

## Migration note

Existing inline ADRs in `CLAUDE.md` ("## Architectural Decisions") should be migrated to
`docs/decisions/` as separate files per the `sessions.md` convention. This is ADR-0001
and bootstraps the `docs/decisions/` directory. Existing inline ADRs should be
ADR-0002 through ADR-0010+ as they are extracted (out of scope for this item).
