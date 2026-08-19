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
- `/summary [n]` — AI-generated chat summary; the optional count covers the last `n`
  messages (default 100, from 20 up to 1000)
- `/summary500` — the same summary over 500 messages, as one word
- `/remember [#тема] <текст> [до <дата>]` — save a fact into the chat's knowledge base. Used as a
  reply with no text of its own, it saves the replied-to message — or just the part of it you
  highlighted. `#тема` files the fact under a topic, and `до 5 сентября` gives it a deadline after
  which it stops influencing answers. Group chats only, organizers and bot admins only. A second
  `/remember` about the same subject **adds** a fact rather than replacing the first one
  ([ADR-0012](docs/decisions/ADR-0012-manual-capture-append-only-identity.md)); the confirmation
  carries an undo button. A pasted multi-line list (house rules, say) is stored as **one**
  complete fact rather than split per line — split facts would let the bot answer "what are our
  rules?" with three of twelve in the confident tone of a curated base, whereas one fact is either
  complete or visibly truncated
- `/kb` — browse the facts remembered for this chat

### Optional Modules
- **Knowledge base** — per-chat facts the bot remembers and reuses as context; captured by chat
  organizers with `/remember` in the chat itself, with the module toggled and organizers appointed
  from the admin panel. Facts only reach an answer when they actually match what was asked
  (`knowledge_base.min_similarity`, default 0.70): a question the base has nothing to say about is
  answered without it, rather than with the nearest few facts presented as relevant
- **Voice transcription** — transcribe voice messages and video notes (gpt-4o-mini-transcribe)
- **Image analysis** — understand and comment on images
- **Sticker intelligence** — learn and use stickers contextually. Each sticker carries an
  explicitness score and each chat a ceiling, so a sticker is only sent where it fits; the
  admin card shows both and the resulting verdict, and an admin can set a sticker's score by
  hand — a manual score is kept, not overwritten by the next automatic analysis
- **Reactions** — records who added or removed which reaction, and can answer with a reaction instead of words when it decides not to speak. Opt-in per chat, with a separate switch for the history; **requires the bot to be a chat administrator**, as Telegram sends no reaction updates otherwise — the admin panel shows that status live
- **Link comments** — extract and comment on YouTube/TikTok links
- **Custom rules** — keyword triggers, spam detection, regex matching

### Admin panel

`/admin` in a direct message opens the bot's control panel (bot admins only):

- **Chat settings** — one grouped panel per whitelisted chat covering every per-chat
  option: behaviour, modules, stickers, rules, plus links into the knowledge-base and
  reactions sub-panels. Options a chat has not overridden are marked *inherited*, so it
  is always clear whether a value is the chat's own or comes from the global layer.
  Approving a new chat offers a direct link into its settings. The chat picker lists the
  most active chats first (messages in the last 24h, with the count in the caption), and
  `/panel <link or title>` jumps straight to one chat's panel — ambiguous titles offer a
  list to pick from, and only whitelisted chats are reachable either way.
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

python -m scripts.verify_commands   # Check the bot's registered slash commands
python -m scripts.eval_rag <dsn>    # Measure RAG retrieval quality
python -m scripts.kb_report <dsn>   # Measure Knowledge Base retrieval, sweep the similarity floor
python -m scripts.backfill_chunks <dsn>   # Build the conversation-chunk index now, instead of waiting
```

The bot pushes its command menus to Telegram on every start and verifies what
Telegram actually holds; `scripts/verify_commands.py` runs the same check on
demand (`--fix` re-pushes, `--json` for machines). Commands are declared once in
[src/bot/command_registry.py](src/bot/command_registry.py) — adding a handler
without a spec there fails CI.

`scripts/eval_rag.py` replays a set of eval cases through the **real** search
path the bot uses, reporting recall@k, MRR, blind rate and the similarity
distribution. It exists so retrieval changes can be judged against a recorded
number rather than an impression — see
[the baseline](docs/rag-eval-baseline.md). "The real search path" now includes
query hygiene: a message that opens by addressing the bot ("бот, ...") has that
address removed before the retrieval embedding is computed, in the harness
exactly as in the bot, because the trigger word is semantically loud and was
measured to steer retrieval toward the bot's own memories. The database DSN is a required
argument with no default: the harness must never be able to point at a live
database. Cases are validated by `scripts/eval_schema.py`; the tracked template
in `tests/fixtures/eval/` is synthetic, and real cases stay out of this
repository. Because they do, a baseline is only as reproducible as the corpora
behind it: the case set is regenerated deterministically from a frozen corpus
rather than kept as a file, and both corpora are kept as verified `pg_dump`
archives outside the repository. The exact procedure lives with the numbers, in
[the baseline](docs/rag-eval-baseline.md#reproducing).

`scripts/kb_report.py` is the Knowledge Base counterpart, and needs no cases at
all: the bot has been recording per-fact similarity for every KB lookup since
migration 022, so the report reads real traffic and sweeps candidate similarity
floors over it. Read-only is enforced by the database rather than by
convention — the query runs inside a `readonly=True` transaction — and an empty
window exits non-zero, because a silent zero reads exactly like "no problems
found". See [the KB baseline](docs/kb-eval-baseline.md).

`scripts/backfill_chunks.py` belongs to a different part of the same story. Long-term
memory has so far been Q&A pairs written only on turns where the bot replied, which on a
live chat is 4–8% of the history — and not a random 4–8%, since it is exactly the part
where people addressed the bot. `chat_chunks` (migration 029) indexes the conversation
itself instead: sessions bounded by a three-hour pause, rendered as verbatim
`Имя (ЧЧ:ММ): текст` lines under a dateline, embedded as documents rather than as
queries. A background worker fills it every fifteen minutes; this script is the same
worker driven in a loop, for when waiting is not worth it. **Nothing reads the table
yet** — retrieval moves onto it in a later slice, behind a shadow period, which is what
makes the index safe to build in production while the bot serves traffic.

Both of those read what retrieval *returned*. `scripts/kb_probe.py` covers the
side neither can see: given real questions, it reports whether the knowledge
base would answer them at all — `WOULD ANSWER` / `BORDERLINE` / `BLIND`. A
question the base is blind to leaves no trace in the logs, and the similarity
floor makes that worse, since a filtered-out turn looks exactly like a turn
where nothing was relevant. `BORDERLINE` marks a hit whose margin over the floor
is thin enough that a slightly worse phrasing would get nothing. Take the
questions from what people actually ask — questions written by reading the facts
match by construction and measure nothing.

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Setup Guide](docs/setup.md)
- [Configuration Reference](docs/configuration.md)
- [Deployment](docs/deployment.md) — **merging to `main` deploys to production**: the gates, what this repo must keep true, and the failure modes
- [Database Backups](docs/backups.md) — nightly encrypted dumps, off-host upload, restore and rehearsal
- [Functionality Overview](docs/FUNCTIONALITY.md) — full feature catalogue with live-QA observations and improvement recommendations
- [RAG Eval Baseline](docs/rag-eval-baseline.md) — recorded retrieval measurements: how a baseline is produced, and what the current numbers are
- [KB Eval Baseline](docs/kb-eval-baseline.md) — the same for the Knowledge Base, plus why its similarity floor cannot be calibrated yet
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
