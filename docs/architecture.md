# Architecture Overview

## Module Structure

```
src/
├── main.py                    # Entry point, Dishka container setup
├── config.py                  # Settings (YAML + env + pydantic-settings)
├── di.py                      # Dishka DI providers
├── models/
│   └── chat_config.py         # Frozen dataclass: per-chat config
├── bot/
│   ├── handlers/              # aiogram Router handlers
│   │   ├── message.py         # Text message processing
│   │   ├── commands.py        # /help, /summary, /start
│   │   ├── callbacks.py       # Inline keyboard callbacks
│   │   ├── media.py           # Voice, photo, sticker handlers
│   │   └── admin.py           # Admin panel (FSM)
│   ├── middleware/
│   │   ├── chat_config.py     # Inject ChatConfig per message
│   │   ├── access_control.py  # Whitelist + admin detection
│   │   ├── activity_tracker.py
│   │   └── message_saver.py
│   ├── keyboards/             # Inline keyboard builders
│   └── filters/               # Custom aiogram filters
├── services/
│   ├── chat_config.py         # Three-layer config merge + cache
│   ├── ai/
│   │   ├── base.py            # AIProvider ABC + result dataclasses
│   │   ├── capabilities.py    # Provider capability matrix
│   │   ├── router.py          # AIRouter: fallback chain
│   │   └── providers/         # Concrete AI providers
│   ├── text/
│   │   ├── pipeline.py        # TextProcessingPipeline orchestrator
│   │   ├── prompt_builder.py  # System prompt assembly
│   │   ├── adaptive_length.py # Median-based response length
│   │   └── formatter.py       # Markdown → Telegram HTML
│   ├── rag/
│   │   └── memory.py          # Vector search + store
│   ├── abuse/
│   │   ├── checker.py         # SQL function wrapper
│   │   ├── filter.py          # Pattern + embedding filter
│   │   └── notifications.py   # Admin alerts
│   └── modules/               # Optional feature modules
└── database/
    ├── connection.py           # asyncpg pool + pgvector
    └── repositories/           # Data access layer
```

## Dependency Injection (Dishka)

```
Scope.APP (bot lifetime)
├── Settings           ← from_context (passed at startup)
├── asyncpg.Pool       ← create_pool / close_pool
└── AIRouter           ← AIRouter(settings)

Scope.REQUEST (per Telegram update)
├── BotConfigRepository    ← pool
├── ChatSettingsRepository ← pool
└── ChatConfigService      ← yaml_settings + repos
```

Handlers receive dependencies via `FromDishka[Type]` type hints.
Middleware accesses the container via `data["dishka_container"]`.

## Data Flow

```
Telegram Update
    → [Dishka ContainerMiddleware]  — creates REQUEST scope
    → ChatConfigMiddleware          — injects chat_config
    → AccessControlMiddleware       — whitelist + admin check
    → ActivityTrackerMiddleware     — track user activity
    → MessageSaverMiddleware        — save to chat_messages
    → Handler
        → TextProcessingPipeline
            1. Anti-abuse SQL check
            2. Pattern + embedding filter
            3. Gather context (recent msgs + RAG)
            4. Build system prompt (personality + adaptive length)
            5. AIRouter.generate_text() with fallback
            6. Markdown → HTML formatting
            7. Send response
            8. Post-response: log, update cooldown, RAG store
```

## Configuration Layers

```
config/default.yml          ← YAML defaults
    └─▶ bot_config table    ← Global DB overrides (default_* keys)
         └─▶ chat_settings  ← Per-chat DB overrides
              └─▶ ChatConfig (frozen dataclass)
```

`ChatConfigService` merges these three layers with 60s TTL cache.

## Database

- **PostgreSQL 16** with **pgvector** extension
- **asyncpg** for async access (no ORM)
- **Repository pattern** for data access
- **Alembic** for schema migrations
- **768-dimensional vectors** (gemini-embedding-001) with IVFFlat index

Background tasks (process-lifetime, owned by `main()`, not Dishka): `HealthChecker`,
`StickerSetSyncScheduler`, `RetentionCleaner`, `EmbeddingBackfillWorker`, and — since
S4 — `ChatChunkIndexer`, which turns saved messages into the `chat_chunks` index.
