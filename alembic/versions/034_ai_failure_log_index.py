"""ai_failure_log -- index the two columns every health query filters on.

Revision ID: 034
Revises: 033
Create Date: 2026-09-17

Migration 031 created this table with a primary key and nothing else, on the
stated premise that it would hold "a handful of rows". The outage of
2026-09-16/17 voided that premise: a provider whose quota is exhausted
produces one row per attempt, and the retry loop attempted 131-155 times an
hour for a day. Measured on production during that window: 3222 rows total, of
which 3093 from a single day.

It stays that way by design even with the new backoff window -- a skipped call
still records a terminal failure, because the health check counts those rows
and silence there would read as health. So the row rate is a feature and the
scan cost is the thing to fix.

Both health queries filter `created_at > now() - interval` and group or
partition by `task_type`, and they run every five minutes for the life of the
process. Measured on a 112k-row copy: two sequential scans, ~2768 buffer hits,
16-19 ms per cycle. The composite index turns both into range scans.

`(task_type, created_at)` in that order, not the reverse: the streak query
needs the rows of ONE task in time order (`LAG(...) PARTITION BY task_type
ORDER BY created_at`), which this index can satisfy directly.

`CREATE INDEX` (not `CONCURRENTLY`): alembic runs inside a transaction here,
and the production deploy rehearses every migration on a copy of the live
database first. At this table's size the lock is milliseconds.
"""

from __future__ import annotations

from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One statement per `op.execute`: migrations run online through the
    # asyncpg dialect, which PREPAREs every statement, and PostgreSQL refuses
    # a prepared statement carrying more than one command.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_failure_log_task_time
            ON ai_failure_log (task_type, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ai_failure_log_task_time")
