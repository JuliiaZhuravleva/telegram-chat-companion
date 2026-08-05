"""Stop rules_mode / rules_enabled from shadowing their global defaults.

Revision ID: 020
Revises: 019
Create Date: 2026-08-03

The three-layer config merge reads NULL in `chat_settings` as "not overridden",
so a per-chat column carrying a DEFAULT materializes a value on every
`ensure_exists()` and permanently shadows its own `bot_config.default_*` global
layer (CLAUDE.md: "Per-chat columns: nullable, no DEFAULT").

Migration 015 repaired six columns that had this problem. `rules_mode` and
`rules_enabled` -- added WITH DEFAULT by migration 008 -- were not in its list
and were never fixed. Measured on the dev database before this migration: all 9
`chat_settings` rows had both columns materialized, against 1 of 9 for the
correctly-nullable `kb_enabled`. `bot_config.default_rules_mode` and
`default_rules_enabled` are therefore dead settings today: seeded by 008,
readable in the admin panel, and unable to affect any chat.

Two steps, matching 015's shape:

1. DROP DEFAULT (and DROP NOT NULL, harmless if already nullable) so newly
   created rows leave these NULL and defer to the global layer.

2. Clear the values that were materialized rather than chosen. Only rows whose
   value still equals the global default are cleared: for those, NULL is
   semantically identical to what they hold now, so nothing observable changes
   except that the global layer starts applying again. A row that differs is an
   admin's deliberate per-chat override and is left untouched -- this migration
   must not silently re-enable or disable a rules engine anywhere.

The defaults are read from `bot_config` rather than hardcoded, so a deployment
that already changed its global default repairs against its own value, not
against 008's seed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "020"
down_revision: str = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_settings ALTER COLUMN rules_mode DROP DEFAULT")
    op.execute("ALTER TABLE chat_settings ALTER COLUMN rules_mode DROP NOT NULL")
    op.execute("ALTER TABLE chat_settings ALTER COLUMN rules_enabled DROP DEFAULT")
    op.execute("ALTER TABLE chat_settings ALTER COLUMN rules_enabled DROP NOT NULL")

    # bot_config.value is JSONB: default_rules_mode is a JSON string ("all"),
    # default_rules_enabled a JSON boolean. #>>'{}' extracts the scalar as text
    # for both. COALESCE keeps 008's seeded values as the fallback if a
    # deployment removed the bot_config rows entirely.
    op.execute("""
        UPDATE chat_settings
        SET rules_mode = NULL
        WHERE rules_mode IS NOT NULL
          AND rules_mode = COALESCE(
              (SELECT value #>> '{}' FROM bot_config WHERE key = 'default_rules_mode'),
              'all'
          )
    """)
    op.execute("""
        UPDATE chat_settings
        SET rules_enabled = NULL
        WHERE rules_enabled IS NOT NULL
          AND rules_enabled = COALESCE(
              (SELECT value #>> '{}' FROM bot_config WHERE key = 'default_rules_enabled'),
              'false'
          )::BOOLEAN
    """)


def downgrade() -> None:
    # Restoring the DEFAULTs recreates the shadowing bug by design -- this is
    # what 008 established. Rows cleared above are NOT refilled: which of them
    # were materialized rather than chosen is no longer recoverable, and
    # guessing would invent per-chat overrides that never existed.
    op.execute("ALTER TABLE chat_settings ALTER COLUMN rules_mode SET DEFAULT 'all'")
    op.execute("ALTER TABLE chat_settings ALTER COLUMN rules_enabled SET DEFAULT false")
