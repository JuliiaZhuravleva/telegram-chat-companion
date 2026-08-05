# Contributing

Contributions are welcome! This guide will help you get started.

## Development Setup

```bash
git clone https://github.com/JuliiaZhuravleva/telegram-chat-companion.git
cd telegram-chat-companion

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
pre-commit install
```

## Code Style

- **Formatter & linter:** ruff (configured in `pyproject.toml`)
- **Type checker:** mypy (strict mode)
- **Line length:** 100 characters
- **Python:** 3.11+, use modern syntax (PEP 604 unions, etc.)

Run checks before committing:

```bash
ruff check src/ tests/
ruff format src/ tests/
mypy src/
pytest tests/ -v
```

## Project Structure

### Adding a New AI Provider

1. Create `src/services/ai/providers/your_provider.py`
2. Implement `AIProvider` (from `src/services/ai/base.py`)
3. Add to `PROVIDER_CAPABILITIES` in `src/services/ai/capabilities.py`
4. Add API key to `Settings` in `src/config.py`
5. Add lazy import in `AIRouter._get_provider()`
6. Write tests in `tests/unit/test_your_provider.py`

### Adding a New Module

1. Create service in `src/services/modules/your_module.py`
2. Add handler in `src/bot/handlers/` if it needs message processing
3. Register in `src/di.py` if it has dependencies
4. Add toggle to `ChatConfig` if it's per-chat configurable
5. Write tests

### Adding a New Repository

1. Create `src/database/repositories/your_repo.py`
2. Accept `asyncpg.Pool` in `__init__`
3. Register in `src/di.py` `RepositoryProvider`
4. Add migration in `alembic/versions/` if new tables are needed

## Testing

- **Unit tests** (`tests/unit/`): Mock all I/O, no database needed
- **Integration tests** (`tests/integration/`): Use testcontainers with real PostgreSQL

```bash
# Unit tests only (fast)
pytest tests/unit/ -v

# All tests with coverage
pytest tests/ -v --cov=src --cov-report=term-missing
```

## Pull Request Guidelines

1. Create a feature branch from `main`
2. Keep changes focused — one feature/fix per PR
3. Add tests for new functionality
4. Ensure all checks pass (`ruff`, `mypy`, `pytest`)
5. Write a clear PR description

> **Merging to `main` deploys to production**, unattended, within minutes — see
> [docs/deployment.md](docs/deployment.md). Green checks and a clean migration rehearsal are the
> only guard between a merged PR and the running bot, so the merge *is* the release decision.
> Two things that matter when changing infrastructure: the deployer waits on the CI jobs **by
> name** (a renamed job stalls deploys rather than failing them), and migrations are forward-only
> — there is no automatic rollback.

## Architecture Decisions

Major architectural decisions live as ADR notes inside the codebase (see the "Architectural Decisions" section near the top of each relevant module). When proposing architectural changes, please open an issue first.
