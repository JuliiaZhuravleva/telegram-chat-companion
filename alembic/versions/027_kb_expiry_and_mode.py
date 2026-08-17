"""KB Phase 2 groundwork: fact expiry, removal attribution, per-chat KB mode.

Revision ID: 027
Revises: 026
Create Date: 2026-08-17

Plan: docs/plans/kb-revision-2026-08.md (S1 / KB-03). This is the **only**
migration in that plan -- ADR-0003 deliberately over-sized `chat_facts` so
Phases 1-3 would need no further DDL, and that held: everything else the
revision needs already exists as an inert column.

Purely additive, all nullable, no backfill, no data rewrite, no NOT NULL, no
index-predicate change. That shape is what lets the production rehearsal (a
run against a copy of the live database) actually prove something: there is
no statement here whose effect depends on existing row contents.

Columns
-------
- `chat_facts.expires_at TIMESTAMPTZ` -- "valid until". NOT expressible with
  the existing `valid_to`: that column is written only as `NOW()` and read
  only as `valid_to IS NULL`, so a future value there would hide the fact
  immediately AND be indistinguishable from a supersession. `expires_at` is
  a separate axis: the fact is current until the date, then stops being
  retrievable **without** becoming a superseded revision of anything.

- `chat_facts.rejected_by BIGINT` / `rejected_at TIMESTAMPTZ` -- who retired
  a fact and when. `reject_fact()` records neither today, and the revision
  widens the writer set to every Telegram chat administrator (plan §4.4), so
  "a fact vanished and nobody can say who removed it" becomes reachable by
  people the bot operator did not personally appoint. Added now because this
  migration is open now; a second `chat_facts` migration is exactly what
  ADR-0003 paid schema cost to avoid.

- `chat_settings.kb_mode TEXT` -- **nullable, no DEFAULT**, per the
  three-layer merge rule (CLAUDE.md; migration 020's own bug). A SQL DEFAULT
  would be materialized into every row by `ensure_exists()` and would then
  permanently shadow the `bot_config.default_kb_mode` global layer. Left
  unread by application code in this slice: the setting's UI and its
  `ChatConfig` wiring land together in S3 (KB-20), because a setting nobody
  can toggle is the exact failure this whole revision exists to correct.

No index is added for `expires_at`. The live-fact predicate's selective part
(`chat_id, status, valid_to`) is already covered by `idx_chat_facts_status`,
and `NOW()` is not immutable so it cannot appear in a partial index predicate
anyway. Revisit alongside the `lists = 10` -> `100` note in migration 014 if
a chat's fact count ever approaches the thousands.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "027"
down_revision: str = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One statement per op.execute(): migrations run online through
    # SQLAlchemy's asyncpg dialect, which PREPAREs every statement, and
    # PostgreSQL rejects a prepared statement holding more than one command.
    op.execute("""
        ALTER TABLE chat_facts
        ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ
    """)
    op.execute("""
        ALTER TABLE chat_facts
        ADD COLUMN IF NOT EXISTS rejected_by BIGINT
    """)
    op.execute("""
        ALTER TABLE chat_facts
        ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ
    """)
    op.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS kb_mode TEXT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS kb_mode")
    op.execute("ALTER TABLE chat_facts DROP COLUMN IF EXISTS rejected_at")
    op.execute("ALTER TABLE chat_facts DROP COLUMN IF EXISTS rejected_by")
    op.execute("ALTER TABLE chat_facts DROP COLUMN IF EXISTS expires_at")
