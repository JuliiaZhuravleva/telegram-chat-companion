"""chat_member_state -- who is in a chat, who left, who came back.

Revision ID: 036
Revises: 035
Create Date: 2026-10-27

The Bot API cannot list a chat's members, so "welcome back" needs the bot's own
memory of who was here. One row per (chat, person), moved by atomic transitions
in `ChatMemberStateRepository` -- a join and a leave each arrive twice (the
`chat_member` update and the service message) in any order, and concurrently.

`status`:
- `member` -- joined while the bot watched;
- `left` / `kicked` -- gone;
- `seen` -- known from an outside archive (seeded by an import), current
  presence unknown. A join on a `seen` row is a return. Someone this chat's own
  `chat_messages` already knows needs no row: `record_join` reads the archive.

`pending_greeting` (`new | back`) is set by a join and cleared by whoever
greets -- the join itself, or the person's first message. Clearing is the
atomic claim, so one join produces one greeting however many paths race.

The per-chat columns follow the settings rule: nullable, no DEFAULT (a DEFAULT
would be materialized by `ensure_exists()` and shadow the global layer --
CLAUDE.md, migrations 014/015). `community_templates` is NOT merged: hand-written
texts per chat, like `kb_organizer_ids`.
"""

from __future__ import annotations

from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None

_BOOL_COLUMNS = (
    "community_enabled",
    "greet_on_first_message",
    "welcome_back_enabled",
    "farewell_public",
    "farewell_dm",
    "returner_joke_enabled",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_member_state (
            chat_id          BIGINT      NOT NULL,
            user_id          BIGINT      NOT NULL,
            status           TEXT        NOT NULL
                CHECK (status IN ('member', 'left', 'kicked', 'seen')),
            first_seen_at    TIMESTAMPTZ,
            last_joined_at   TIMESTAMPTZ,
            last_left_at     TIMESTAMPTZ,
            join_count       INTEGER     NOT NULL DEFAULT 0,
            leave_count      INTEGER     NOT NULL DEFAULT 0,
            pending_greeting TEXT
                CHECK (pending_greeting IN ('new', 'back')),
            greeted_at       TIMESTAMPTZ,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    for column in _BOOL_COLUMNS:
        op.execute(f"ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS {column} BOOLEAN")
    op.execute("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS community_templates JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS community_templates")
    for column in reversed(_BOOL_COLUMNS):
        op.execute(f"ALTER TABLE chat_settings DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TABLE IF EXISTS chat_member_state")
