"""Create admin_sticker_notifications — the table only dev DBs ever had.

Revision ID: 016
Revises: 015
Create Date: 2026-08-02

Same drift class as 015, one level up: a whole table instead of columns.
`StickerRepository.save_notification()` / `get_notification_by_reply()` have
referenced admin_sticker_notifications since the sticker admin-notification
flow landed, but no migration ever created it — the table was added by hand
to the dev database, so `alembic upgrade head` on a fresh database produced
a schema without it.

Consequences on a fresh deploy (both observed in prod on 2026-08-02):

* every learned sticker logs "Failed to notify admin about sticker" — the
  notification messages are sent, but recording them raises
  UndefinedTableError inside save_notification(), and
* an admin replying to a sticker notification gets silence: the reply
  handler crashes in get_notification_by_reply() before it can respond.

Schema mirrors the hand-created dev table exactly (column types, nullability,
index names), so applying this to a dev database is a no-op and fresh
databases converge on the same shape. Every statement is idempotent.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "016"
down_revision: str = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS admin_sticker_notifications (
            id              SERIAL PRIMARY KEY,
            file_unique_id  VARCHAR(255) NOT NULL,
            admin_id        BIGINT NOT NULL,
            message_id      BIGINT,          -- the text/description message in the admin's DM
            sticker_msg_id  BIGINT,          -- the sticker message itself (reply target)
            chat_id         BIGINT,          -- admin's DM chat
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # get_notification_by_reply() looks up by (chat_id, message_id) OR
    # (chat_id, sticker_msg_id) — one index per arm of the OR.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_admin_sticker_notif_chat_msg
        ON admin_sticker_notifications(chat_id, message_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_admin_sticker_notif_chat_stk
        ON admin_sticker_notifications(chat_id, sticker_msg_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_sticker_notifications;")
