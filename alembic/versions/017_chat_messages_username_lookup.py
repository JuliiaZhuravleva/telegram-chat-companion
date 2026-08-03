"""Index chat_messages for case-insensitive @username lookup.

Revision ID: 017
Revises: 016
Create Date: 2026-08-03

The KB organizer flow resolves a plain `@username` against this chat's message
history, because the Bot API offers no username-to-user lookup
(`MessageRepository.find_by_username` / `username_seen_elsewhere`). Both match
with `LOWER(username) = LOWER($2)`, which a plain btree index on `username`
cannot serve — so before this migration every such lookup was a sequential scan.

`find_by_username` at least narrows by `chat_id` first. `username_seen_elsewhere`
does not: it deliberately searches *every other* chat (`chat_id != $1`) to tell
"never seen this username" apart from "seen it, just not in this chat", so its
cost grows with total retained history rather than with one chat's traffic.

Two functional indexes, one per access pattern:

* `(chat_id, LOWER(username))` — the chat-scoped lookup.
* `(LOWER(username))` — the cross-chat existence probe.

Partial (`WHERE username IS NOT NULL`) because both queries already require it
and most rows in a busy chat have no username. Cheap to build at current
volumes (tens of thousands of rows); plain CREATE INDEX rather than
CONCURRENTLY, since alembic runs migrations inside a transaction and
CONCURRENTLY cannot. Revisit if chat_messages grows to millions of rows.

Idempotent, so applying to an already-patched database is a no-op.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "017"
down_revision: str = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # MessageRepository.find_by_username(chat_id, username)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_username_lower
        ON chat_messages (chat_id, LOWER(username))
        WHERE username IS NOT NULL
    """)

    # MessageRepository.username_seen_elsewhere(chat_id, username) — scans
    # across chats, so it cannot use the chat-scoped index above.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_username_lower
        ON chat_messages (LOWER(username))
        WHERE username IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_messages_username_lower;")
    op.execute("DROP INDEX IF EXISTS idx_chat_messages_chat_username_lower;")
