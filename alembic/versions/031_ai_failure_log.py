"""ai_failure_log -- make a failed AI call visible to automation.

Revision ID: 031
Revises: 030
Create Date: 2026-08-24

Video-note transcription was dead in every chat for five days (2026-08-19 to
2026-08-24, fixed in ba8ce2c) and nothing noticed. The reason is structural,
not an oversight in that one feature: `AIRouter._log_usage` writes to
`response_log` only on the SUCCESS path, so a failed AI call leaves no row
anywhere. `HealthChecker`'s only AI signal is `get_fallback_count()`, which
reads `response_log` -- it is therefore blind to failure by construction, and
can see a fallback only once one has already succeeded.

A separate table rather than a flag on `response_log`: that table feeds
`SpendLimitService` and the /costs analytics, so adding rows that are not
real calls would quietly change the meaning of every existing aggregate,
including the one that caps spending. Failures are a different fact with a
different lifetime.

Deliberately no index. Terminal failures (every provider in the chain
exhausted) are rare by construction, so this table is expected to hold a
handful of rows; an index on it would be dead weight, and this project has
already been bitten by trusting a plan derived from dev-sized data (TD-063).
Add one if the row count ever justifies it.

`created_at` is NOT NULL with a DEFAULT on purpose: nullable timestamps are a
known trap here -- a bare `created_at <` comparison silently skips NULL rows,
which would make the retention DELETE quietly leave them behind for ever.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "031"
down_revision: str = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One statement per op.execute(): migrations run online through asyncpg,
    # which PREPAREs every statement, and PostgreSQL rejects a prepared
    # statement holding more than one command (CLAUDE.md).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_failure_log (
            id BIGSERIAL PRIMARY KEY,
            task_type TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            error_type TEXT,
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_failure_log")
