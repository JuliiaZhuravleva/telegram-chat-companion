# Configuration Reference

Configuration is loaded from three sources (later wins):

1. `config/default.yml` — YAML defaults
2. `.env` / environment variables — secrets and overrides
3. Per-chat settings in `chat_settings` database table

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `GEMINI_API_KEY` | No | Google Gemini API key |
| `OPENAI_API_KEY` | No | OpenAI API key |
| `OPENAI_ADMIN_API_KEY` | No | OpenAI **organization Admin** key, used only by the admin panel's cost-verification button. Not interchangeable with `OPENAI_API_KEY`: the `/v1/organization/*` billing endpoints reject a project key, and an admin key is rejected on `/v1/chat/completions`. Create at [admin keys](https://platform.openai.com/settings/organization/admin-keys) (organization owners only). Requires `OPENAI_PROJECT_ID` |
| `OPENAI_PROJECT_ID` | No | Project (`proj_…`) the cost verification reports on. Required together with `OPENAI_ADMIN_API_KEY` — without it the costs endpoint answers organization-wide. Find it under [projects](https://platform.openai.com/settings/organization/projects) |
| `GROK_API_KEY` | No | xAI Grok API key |
| `DEEPSEEK_API_KEY` | No | DeepSeek API key |

## YAML Configuration

### Bot Settings

```yaml
bot:
  trigger_words: ["bot", "бот"]
  random_response_chance: 0.05        # 0.0–1.0
  random_response_min_interval: 300   # seconds
```

### AI Settings

```yaml
ai:
  default_provider: "gemini"

  tasks:
    text_generation:
      provider: "gemini"
      model: "gemini-3-flash-preview"
      fallback: ["openai", "deepseek"]
      max_tokens: 500
      temperature: 0.9

    embeddings:
      provider: "gemini"
      model: "gemini-embedding-001"   # free tier
      fallback: ["openai"]

    vision:
      provider: "gemini"
      model: "gemini-3-flash-preview"
      fallback: ["openai"]

    transcription:
      provider: "openai"
      model: "gpt-4o-mini-transcribe"
```

### RAG Settings

```yaml
rag:
  min_similarity: 0.7    # Cosine similarity threshold (0.0–1.0)
  max_results: 5          # Maximum RAG results per query
  default_importance: 0.5
```

The floor is applied by `RAGMemoryService`, not in SQL (R1). The repository returns the
nearest `max_results` memories whatever they score, the service drops those below the
threshold, and `retrieval_log` records the whole candidate set with `above_floor` per row
— so a turn that injected nothing still says how far off the best match was. While the
floor lived in the `WHERE` clause, that turn logged an empty result and missing by 0.001
looked exactly like missing by 0.3.

Two consequences worth knowing before reading that table:

- **It now retains short heads of memories the model never saw.** Rejected rows are the
  point of the record, and they are chat content, kept for `maintenance.retrieval_log_days`
  (90). Measured on production: ~60 RAG lookups per 30 days, three quarters of them blind,
  so roughly 27 kB a month. The knowledge base has done the same since its floor shipped.
- **Rows written before R1 mean something different**, and nothing marks the boundary
  explicitly — but they are still distinguishable: a pre-R1 row's `results` entries carry
  no `above_floor` key at all, and its `n_results` counts only what cleared the floor
  rather than what was considered. Split on the presence of that key before pooling a
  window that straddles the deploy. `scripts/kb_report.py` prints a warning when its own
  window does.

### Chunk Index Settings (S4)

```yaml
chunk_indexer:
  enabled: true
  interval_seconds: 900
  messages_per_pass: 2000   # per chat, per pass
  embed_per_pass: 100       # embedding calls per pass, across all chats
```

The background worker that turns saved messages into `chat_chunks` — conversation
sessions over the *whole* chat, as opposed to `chat_memory`'s Q&A pairs, which only
exist for turns where the bot replied (4–8% of a live chat, measured on production
2026-08-18).

Four things about it that are behaviour, not tuning:

- **The gate is `save_messages`, not `rag_enabled`.** Indexing asks "is there anything
  to index"; retrieval asks "should we search". A chat that turns saving off keeps the
  chunks it already has and stops gaining new ones — its memory freezes rather than
  disappears. Nothing reads chunks until S5, so today the setting has no user-visible
  effect at all.
- **Only closed sessions are indexed** — a conversation whose last message is younger
  than the 3-hour session pause is left alone until it settles. Chunking a live session
  would produce a row whose message range changes as people keep talking, and the
  natural key would then hold both the stale and the extended version.
- **`chat_chunks` is outside retention**, like `chat_memory` (ADR-0011). `chat_messages`
  has a 365-day window; if chunks aged with it, the bot's memory would develop a hole
  exactly one year deep with no observable trace.
- **Chats are enumerated from `chat_messages`, not from `chat_settings`.** The two can
  come apart: Telegram gives a group a new `chat_id` when it becomes a supergroup, the
  settings row is re-keyed onto the new id and the message history deliberately is not.
  While the worker read the settings table, that history left the index for ever and
  retention would eventually delete it — so the enumeration follows the data. Such a
  chat is logged on every pass (`Indexing a chat with no settings row`), because two
  things about it are not fixed here.
  First, its gate is the *global* `default_save_messages`, not the owner's own toggle:
  that travelled to the new id with the row. A chat that had turned saving off before
  upgrading keeps gaining chunks where the bullet above says its memory should freeze,
  and no admin screen shows it. Nothing stored is destroyed and no new messages are
  saved.
  Second, the recovered chunks carry the *old* `chat_id`, so retrieval asking as the
  new supergroup will not find them until they are re-keyed. Preserved, not yet
  reachable. Both want the same missing piece — a recorded `old → new` mapping.

`python -m scripts.backfill_chunks <dsn>` runs the same worker in a loop when waiting
for the schedule is not worth it — after a restore, or before a measurement.

### Chunk Retrieval Settings (S5b)

Whether a chat is searched at all is **not** here — it is `chunks_enabled` in the
three-layer merge (`chat_settings.chunks_enabled` → `bot_config.default_chunks_enabled`
→ `false`), toggleable per chat from the settings panel. These are the ranking knobs.

```yaml
chunk_retrieval:
  max_results: 5
  min_similarity: 0.0    # 0.0 = no floor, on purpose — see below
  rrf_k: 60
  vector_weight: 1.0
  fts_weight: 1.0
  depth_multiplier: 2    # how deep each leg goes before fusion; must be >= 2
```

Every one of these is recorded verbatim in `retrieval_log.params` on every turn, so a
distribution read back months later says which numbers produced it. That is the point:
S6 sweeps them, and a log saying only `"chunks"` could not tell a weight change from a
floor change after both had happened.

- **`min_similarity: 0.0` means no floor, and it is a decision rather than a placeholder.**
  The 0.7 that RAG and KB use was calibrated on `chat_memory`, whose documents are built
  from the raw exchange and therefore begin with the same bot address the query does;
  measured 2026-08-19, that shared boilerplate inflates cosine on hits *and* misses.
  Chunks are ordinary conversation on a differently-offset scale, so importing 0.7 would
  carry a number across exactly the discontinuity
  [rag-eval-baseline.md](rag-eval-baseline.md) warns about. What bounds injection
  meanwhile is the prompt budget plus framing that tells the model to ignore off-topic
  fragments — not a threshold nobody has measured.
- **`depth_multiplier` below 2 quietly disables RRF.** Fusing two top-`k` lists can only
  return rows some leg already had in its top `k`, which throws away the one thing the
  fusion is for: a row ranked 7th by both legs beating a row ranked 1st by one and 400th
  by the other.
- **The prompt budget is not configurable** — `CHUNKS_BUDGET_TOKENS` (900) and
  `MAX_CHUNK_CHARS` (1300) live in `prompt_builder.py`, because the two are an arithmetic
  pair: the cap is what makes "about two fragments" true, and moving one without the
  other silently makes it one.

Rolling out is two DB writes and no restart: `chat_settings.chunks_enabled = true` for
one chat first, then `bot_config.default_chunks_enabled` for the rest. Rolling back is
the same writes inverted — the indexer keeps writing either way, so reading and writing
never have to be reverted together.

### Knowledge Base Settings

Retrieval tuning only — whether the module runs at all is `modules.knowledge_base.enabled`
plus the per-chat `kb_enabled` toggle.

```yaml
knowledge_base:
  min_similarity: 0.7    # Cosine similarity threshold (0.0–1.0)
```

A fact below the floor is not shown to the model, and a turn where nothing clears it
gets no knowledge-base block in the prompt at all — the bot answers as it would with no
base. Sub-floor facts are still written to `retrieval_log` (with `above_floor: false`),
so the floor stays measurable after it ships; see
[kb-eval-baseline.md](kb-eval-baseline.md) for the production measurement this value
comes from.

Set to `0.0` to disable filtering entirely and restore the previous behaviour. Note that
`config/default.yml` is COPY'd into the image at build time, so changing it means a
rebuild (or an in-container edit plus restart).

### Logging

```yaml
logging:
  level: "INFO"          # DEBUG, INFO, WARNING, ERROR
  format: "json"         # "json" or "console"
```

## Per-Chat Settings (Database)

These settings can be overridden per chat via the admin panel or directly in the `chat_settings` table:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `enabled` | bool | false | Whether the bot responds in this chat |
| `trigger_words` | text[] | ["bot", "бот"] | Words that trigger a response |
| `random_response_chance` | float | 0.05 | Random response probability |
| `system_prompt` | text | "" | Custom AI personality |
| `language` | varchar | "ru" | Response language |
| `rag_enabled` | bool | true | Enable RAG memory |
| `transcribe_voice` | bool | true | Transcribe voice messages |
| `abuse_filter_enabled` | bool | false | Enable anti-abuse system |
| `sticker_learning_enabled` | bool | false | Learn sticker meanings |
| `image_analysis_enabled` | bool | true | Analyze images |
| `save_messages` | bool | true | Save message history |
