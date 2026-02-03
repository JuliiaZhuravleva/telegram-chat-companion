# Telegram Chat Companion

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)

An AI-powered Telegram bot that acts as a **chat participant**, not just a command responder.

## What Makes This Bot Unique

Unlike traditional command-based bots, Telegram Chat Companion:

- **Participates in conversations** — responds to triggers and mentions, not just `/commands`
- **Remembers context** — RAG-based memory for each chat using pgvector
- **Multi-provider AI** — Gemini, OpenAI, Grok, DeepSeek, or bring your own
- **Modular design** — enable only the features you need
- **Easy to extend** — add custom commands in minutes

## Supported AI Providers

| Provider | Text | Embeddings | Vision | Transcription |
|----------|:----:|:----------:|:------:|:-------------:|
| OpenAI   | ✅   | ✅         | ✅     | ✅            |
| Gemini   | ✅   | ✅         | ✅     | ❌            |
| Grok     | ✅   | ❌         | ✅     | ❌            |
| DeepSeek | ✅   | ✅         | ❌     | ❌            |

More providers coming soon (Anthropic, HuggingFace custom models).

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ with [pgvector](https://github.com/pgvector/pgvector) extension
- Telegram Bot Token from [@BotFather](https://t.me/botfather)
- At least one AI provider API key

### Installation

```bash
# Clone the repository
git clone https://github.com/JuliiaZhuravleva/telegram-chat-companion.git
cd telegram-chat-companion

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Configure
cp config/.env.example .env
# Edit .env with your tokens and database URL

# Run
python -m src.main
```

### With Docker

```bash
cp config/.env.example .env
# Edit .env with your configuration

docker compose up -d
```

## Configuration

### Minimal Bot (text responses only)

```yaml
# config/local.yml
modules:
  rag_memory: true
  voice_transcription: false
  sticker_intelligence: false
  image_analysis: false
  abuse_filter: false
```

### Full-Featured Bot

```yaml
modules:
  rag_memory: true
  voice_transcription: true
  sticker_intelligence: true
  image_analysis: true
  abuse_filter: true
```

### AI Provider Configuration

```yaml
ai:
  default_provider: "gemini"

  tasks:
    text_generation:
      provider: "gemini"
      model: "gemini-2.5-flash"
      fallback: ["openai", "deepseek"]

    embeddings:
      provider: "gemini"
      model: "text-embedding-004"
      fallback: ["openai"]

    vision:
      provider: "openai"
      model: "gpt-4o-mini"
      fallback: ["gemini"]
```

## Features

### Core
- **Text responses** with RAG context
- **Trigger-based activation** — responds to mentions and replies
- **Random responses** — occasionally joins conversations naturally

### Optional Modules
- **Voice transcription** — transcribe voice messages and video notes (Whisper)
- **Image analysis** — understand and comment on images
- **Sticker intelligence** — learn and use stickers contextually
- **Abuse filter** — 3-layer detection (regex → embeddings → AI)
- **Link comments** — extract and comment on YouTube/TikTok/Instagram links

## Documentation

- [Configuration Guide](docs/configuration.md)
- [AI Providers Setup](docs/ai-providers.md)
- [Adding Custom Commands](docs/adding-commands.md)
- [Deployment Guide](docs/deployment.md)
- [Architecture Overview](docs/architecture.md)

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linter
ruff check src tests

# Run type checker
mypy src
```

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with [aiogram](https://github.com/aiogram/aiogram)
- Vector search powered by [pgvector](https://github.com/pgvector/pgvector)
