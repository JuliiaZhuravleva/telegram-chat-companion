"""advice_watch -- what the blog classifier read and what it concluded (slice 3b).

Revision ID: 037
Revises: 036
Create Date: 2026-10-29

The blog's community rules ask members not to give unsolicited advice and to
support the author by naming her feelings. A background worker reads every
member's comment with its post and thread, asks a model factual questions about
it, and code turns the answers into a verdict (`src/services/advice/`). For the
first two weeks nothing is said in the chat: the owner gets a daily digest in
her DM.

- `advice_checked` -- one row per message the worker has finished with,
  classified or given up on. This, not the verdict table, is what "already
  read" means: a series of three messages is one verdict and three checks, and
  a message whose call failed for good must still stop being retried.
- `advice_verdicts` -- one row per classified series, every verdict including
  `none`: the monthly table counts advice per person, and a month of `none`
  rows is what makes a count of three meaningful. `facts` keeps the model's raw
  answers so a wrong verdict can be traced to the question it got wrong.
  `UNIQUE (chat_id, first_message_id)` makes a re-run after a crash a no-op.
- `advice_post_facts` -- the answers about a post, asked once per post rather
  than once per comment under it.

None of the three is cleaned by retention: they are small (a few dozen rows a
day in one chat) and the monthly table reads a month back.

Like every chat-keyed table here, a group->supergroup upgrade leaves these rows
on the old id (`ChatMigrationRepository`); the blog is already a supergroup.

`chat_settings.advice_watch_enabled` follows the settings rule: nullable, no
DEFAULT (a DEFAULT would be materialized by `ensure_exists()` and shadow the
global layer -- CLAUDE.md, migrations 014/015). Off in YAML; nothing runs until
it is switched on for a chat.
"""

from __future__ import annotations

from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS advice_checked (
            chat_id     BIGINT      NOT NULL,
            message_id  BIGINT      NOT NULL,
            checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (chat_id, message_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS advice_verdicts (
            id               BIGSERIAL   PRIMARY KEY,
            chat_id          BIGINT      NOT NULL,
            user_id          BIGINT,
            first_message_id BIGINT      NOT NULL,
            last_message_id  BIGINT      NOT NULL,
            message_ids      BIGINT[]    NOT NULL,
            post_message_id  BIGINT,
            facts            JSONB       NOT NULL,
            advice           TEXT        NOT NULL
                CHECK (advice IN ('none', 'unsolicited', 'requested', 'permission_asked')),
            level            TEXT
                CHECK (level IN ('hard', 'soft')),
            support          SMALLINT    NOT NULL CHECK (support BETWEEN 0 AND 2),
            constructive     BOOLEAN     NOT NULL,
            model            TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            notified_at      TIMESTAMPTZ,
            UNIQUE (chat_id, first_message_id)
        )
        """
    )
    # The digest's query: what has not been reported yet, per chat.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_advice_verdicts_unnotified
            ON advice_verdicts (chat_id, created_at)
            WHERE notified_at IS NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS advice_post_facts (
            chat_id         BIGINT      NOT NULL,
            post_message_id BIGINT      NOT NULL,
            facts           JSONB       NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (chat_id, post_message_id)
        )
        """
    )
    op.execute("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS advice_watch_enabled BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_settings DROP COLUMN IF EXISTS advice_watch_enabled")
    op.execute("DROP TABLE IF EXISTS advice_post_facts")
    op.execute("DROP TABLE IF EXISTS advice_verdicts")
    op.execute("DROP TABLE IF EXISTS advice_checked")
