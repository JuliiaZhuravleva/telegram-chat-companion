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
