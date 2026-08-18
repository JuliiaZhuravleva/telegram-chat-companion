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
      model: "whisper-1"
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

Three things about it that are behaviour, not tuning:

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

`python -m scripts.backfill_chunks <dsn>` runs the same worker in a loop when waiting
for the schedule is not worth it — after a restore, or before a measurement.

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
