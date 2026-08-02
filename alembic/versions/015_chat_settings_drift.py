"""Add the six chat_settings columns that only ever existed in dev DBs.

Revision ID: 015
Revises: 014
Create Date: 2026-08-01

`ChatConfig` has carried these six per-chat toggles for a long time, and
`_CHAT_CONFIG_FIELDS` in src/services/chat_config.py reads them out of the
chat_settings row — but no migration ever created the columns.  They were
added by hand to the dev database, so `alembic upgrade head` on a fresh
database produced a schema *without* them.  Consequences on a fresh deploy:

* every per-chat override silently degrades to the global/YAML layer, and
* `ChatSettingsRepository.upsert(link_comments_enabled=...)` raises, because
  that name is listed in `_WRITABLE_COLUMNS`.

This blocks the n8n -> Python production migration, where 8 of 9 live chats
have `link_comments_enabled = true`.

Nullable with NO DEFAULT, matching the `kb_enabled` decision in 014: the
three-layer merge treats NULL as "not overridden", so a column carrying a
DEFAULT would materialize a value on `ensure_exists()` and silently shadow
its own global default (`bot_config.default_*`).  The DROP DEFAULT statements
repair dev databases where these columns were created with defaults; rows
that already materialized a value there are deliberately not backfilled to
NULL, same as 014.

Every statement is idempotent.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "015"
down_revision: str = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column, SQL type) — defaults deliberately omitted, see module docstring.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("sticker_reply_to_sticker_enabled", "BOOLEAN"),
    ("sticker_reply_to_sticker_chance", "FLOAT"),
    ("image_comment_sticker_enabled", "BOOLEAN"),
    ("image_comment_sticker_chance", "FLOAT"),
    ("link_comments_enabled", "BOOLEAN"),
    ("relevancy_gate_enabled", "BOOLEAN"),
)


def upgrade() -> None:
    for column, sql_type in _COLUMNS:
        # noqa: S608 — column/type come from the local _COLUMNS constant only.
        op.execute(  # noqa: S608
            f"ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS {column} {sql_type}"
        )
        # Repairs dev DBs that created the column with a DEFAULT; no-op otherwise.
        op.execute(f"ALTER TABLE chat_settings ALTER COLUMN {column} DROP DEFAULT")  # noqa: S608
        op.execute(f"ALTER TABLE chat_settings ALTER COLUMN {column} DROP NOT NULL")  # noqa: S608


def downgrade() -> None:
    for column, _sql_type in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE chat_settings DROP COLUMN IF EXISTS {column}")  # noqa: S608
