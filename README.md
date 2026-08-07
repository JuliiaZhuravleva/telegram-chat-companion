# Telegram Chat Companion

[![CI](https://github.com/JuliiaZhuravleva/telegram-chat-companion/actions/workflows/ci.yml/badge.svg)](https://github.com/JuliiaZhuravleva/telegram-chat-companion/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)

An AI-powered Telegram bot that acts as a **chat participant**, not just a command responder.

## What Makes This Bot Unique

Unlike traditional command-based bots, Telegram Chat Companion:

- **Participates in conversations** — responds to triggers and mentions, not just `/commands`
- **Remembers context** — RAG-based memory for each chat using pgvector
- **Multi-provider AI** — Gemini and OpenAI today, with automatic fallback; Grok, DeepSeek and Anthropic planned
- **Adaptive responses** — matches conversation tone and message length
- **Modular design** — enable only the features you need
- **Easy to extend** — add new AI providers or modules in minutes

## Supported AI Providers

| Provider  | Text | Embeddings | Vision | Transcription | Status      |
|-----------|:----:|:----------:|:------:|:-------------:|-------------|
| Gemini    | ✅   | ✅ (free)  | ✅     | ❌            | implemented |
| OpenAI    | ✅   | ✅         | ✅     | ✅            | implemented |
| Grok      | ✅   | ❌         | ✅     | ❌            | planned     |
| DeepSeek  | ✅   | ✅         | ❌     | ❌            | planned     |
| Anthropic | ✅   | ❌         | ✅     | ❌            | planned     |

Only Gemini and OpenAI are wired up in [src/services/ai/providers/](src/services/ai/providers/) today — the rest are placeholders in the capabilities matrix.

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 16+ with [pgvector](https://github.com/pgvector/pgvector) extension
- Telegram Bot Token from [@BotFather](https://t.me/botfather)
- At least one AI provider API key

### Installation

```bash
git clone https://github.com/JuliiaZhuravleva/telegram-chat-companion.git
cd telegram-chat-companion

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

cp config/.env.example .env
# Edit .env with your tokens and database URL

# Apply database migrations
alembic upgrade head

# Run
python -m src.main
```

### With Docker

```bash
cp config/.env.example .env
# Edit .env with your configuration

docker compose up -d
```

### Backups

A `backup` service ships with both compose files: a nightly `pg_dump`, encrypted
to an [age](https://age-encryption.org/) public key before it leaves the host and
uploaded with [rclone](https://rclone.org/) (Google Drive by default). It stays
off until `BACKUP_ENABLED=true` is set, and it is opt-in on the dev compose
(`--profile backup`).

`scripts/restore-db.sh` is the other half — it verifies the archive, refuses to
overwrite a database that already has tables, and checks the restored row counts
against the backup's manifest.

See [docs/backups.md](docs/backups.md) for the one-time key and rclone setup.

## Architecture

```
Telegram Update
    → ChatConfigMiddleware (inject per-chat config)
    → AccessControlMiddleware (whitelist check)
    → Handler (message/command/media)
        → TextProcessingPipeline → AI Response
```

- **Dependency injection** via [Dishka](https://github.com/reagento/dishka)
- **Database migrations** via [Alembic](https://alembic.sqlalchemy.org/)
- **Three-layer config** — YAML defaults → global DB → per-chat DB
- **Automatic AI fallback** — Gemini → OpenAI (DeepSeek placeholder pending implementation)

See [docs/architecture.md](docs/architecture.md) for detailed diagrams.

## Features

### Core
- **Text responses** with RAG context and adaptive length
- **Trigger-based activation** — responds to mentions, replies, and trigger words
- **Random responses** — occasionally joins conversations naturally
- **Anti-abuse system** — regex patterns + embedding similarity + SQL-based rate limiting

### Commands
- `/help` — dynamic feature list based on enabled modules
- `/summary` — AI-generated chat summary
- `/remember` — save a fact from a replied-to message into the chat's knowledge base
- `/kb` — browse the facts remembered for this chat

### Optional Modules
- **Knowledge base** — per-chat facts the bot remembers and reuses as context; curated by chat organizers from the admin panel
- **Voice transcription** — transcribe voice messages and video notes (Whisper)
- **Image analysis** — understand and comment on images
- **Sticker intelligence** — learn and use stickers contextually
- **Reactions** — records who added or removed which reaction, and can answer with a reaction instead of words when it decides not to speak. Opt-in per chat, with a separate switch for the history; **requires the bot to be a chat administrator**, as Telegram sends no reaction updates otherwise — the admin panel shows that status live
- **Link comments** — extract and comment on YouTube/TikTok links
- **Custom rules** — keyword triggers, spam detection, regex matching

### Admin panel

`/admin` in a direct message opens the bot's control panel (bot admins only):

- **Chat settings** — one grouped panel per whitelisted chat covering every per-chat
  option: behaviour, modules, stickers, rules, plus links into the knowledge-base and
  reactions sub-panels. Options a chat has not overridden are marked *inherited*, so it
  is always clear whether a value is the chat's own or comes from the global layer.
  Approving a new chat offers a direct link into its settings.
- **Global settings** — the values applied to every chat that has no value of its own,
  existing chats included; a chat's own setting always wins.
- Whitelist, custom rules, sticker management, usage statistics, spend, health and
  notification controls.

## Configuration

```yaml
# config/local.yml — minimal setup
bot:
  trigger_words: ["bot", "бот"]
  random_response_chance: 0.05

ai:
  default_provider: "gemini"
  tasks:
    text_generation:
      provider: "gemini"
      model: "gemini-3-flash-preview"
      fallback: ["openai", "deepseek"]
    embeddings:
      provider: "gemini"
      model: "gemini-embedding-001"
```

See [docs/configuration.md](docs/configuration.md) for the full reference.

## Development

```bash
pip install -e ".[dev]"

pytest tests/ -v             # Run tests
ruff check src/ tests/       # Lint
mypy src/                    # Type check
pre-commit install           # Set up git hooks
```

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Setup Guide](docs/setup.md)
- [Configuration Reference](docs/configuration.md)
- [Deployment](docs/deployment.md) — **merging to `main` deploys to production**: the gates, what this repo must keep true, and the failure modes
- [Database Backups](docs/backups.md) — nightly encrypted dumps, off-host upload, restore and rehearsal
- [Functionality Overview](docs/FUNCTIONALITY.md) — full feature catalogue with live-QA observations and improvement recommendations
- [Admin DM Guide](docs/admin-dm-guide.md) — every command and panel screen available to a bot admin in a direct message, and the rules behind each action
- [Admin DM Internals](docs/admin-dm-internals.md) — routers, callback grammar, permission checks, storage and the traps behind that surface

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with [aiogram 3](https://github.com/aiogram/aiogram)
- Dependency injection by [Dishka](https://github.com/reagento/dishka)
- Vector search powered by [pgvector](https://github.com/pgvector/pgvector)
