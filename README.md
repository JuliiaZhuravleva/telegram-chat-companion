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
- **Multi-provider AI** — Gemini, OpenAI, Grok, DeepSeek with automatic fallback
- **Adaptive responses** — matches conversation tone and message length
- **Modular design** — enable only the features you need
- **Easy to extend** — add new AI providers or modules in minutes

## Supported AI Providers

| Provider | Text | Embeddings | Vision | Transcription |
|----------|:----:|:----------:|:------:|:-------------:|
| Gemini   | ✅   | ✅ (free)  | ✅     | ❌            |
| OpenAI   | ✅   | ✅         | ✅     | ✅            |
| Grok     | ✅   | ❌         | ✅     | ❌            |
| DeepSeek | ✅   | ✅         | ❌     | ❌            |

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

# Initialize database
psql $DATABASE_URL -f sql/schema.sql

# Run
python -m src.main
```

### With Docker

```bash
cp config/.env.example .env
# Edit .env with your configuration

docker compose up -d
```

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
- **Automatic AI fallback** — Gemini → OpenAI → DeepSeek

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

### Optional Modules
- **Voice transcription** — transcribe voice messages and video notes (Whisper)
- **Image analysis** — understand and comment on images
- **Sticker intelligence** — learn and use stickers contextually
- **Link comments** — extract and comment on YouTube/TikTok links
- **Custom rules** — keyword triggers, spam detection, regex matching

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
- [Functionality Overview](docs/FUNCTIONALITY.md) — full feature catalogue with live-QA observations and improvement recommendations

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with [aiogram 3](https://github.com/aiogram/aiogram)
- Dependency injection by [Dishka](https://github.com/reagento/dishka)
- Vector search powered by [pgvector](https://github.com/pgvector/pgvector)
