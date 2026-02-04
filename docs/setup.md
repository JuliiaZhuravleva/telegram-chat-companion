# Setup Guide

## Prerequisites

- Python 3.11+
- PostgreSQL 16+ with pgvector extension
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- At least one AI API key (Gemini recommended — embeddings are free)

## Local Development

### 1. Clone and install

```bash
git clone https://github.com/JuliiaZhuravleva/telegram-chat-companion.git
cd telegram-chat-companion

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
pre-commit install
```

### 2. Configure environment

```bash
cp config/.env.example .env
```

Edit `.env` with your values:

```env
TELEGRAM_BOT_TOKEN=your-bot-token
DATABASE_URL=postgresql://user:pass@localhost:5432/telegram_bot
GEMINI_API_KEY=your-gemini-key
# Optional:
# OPENAI_API_KEY=your-openai-key
# GROK_API_KEY=your-grok-key
# DEEPSEEK_API_KEY=your-deepseek-key
```

### 3. Set up PostgreSQL

```bash
# Create database
createdb telegram_bot

# Enable pgvector extension
psql telegram_bot -c "CREATE EXTENSION IF NOT EXISTS vector"

# Apply schema
psql $DATABASE_URL -f sql/schema.sql
```

### 4. Run

```bash
python -m src.main
```

## Docker Setup

```bash
cp config/.env.example .env
# Edit .env

docker compose up -d
```

## Running Tests

```bash
# Unit tests (no database needed)
pytest tests/unit/ -v

# All tests with coverage
pytest tests/ -v --cov=src

# Lint + type check
ruff check src/ tests/
mypy src/
```

## Configuration

See [configuration.md](configuration.md) for the full reference.
