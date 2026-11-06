"""kb_organizer_names -- the name a knowledge-base organizer had when added.

Revision ID: 038
Revises: 037
Create Date: 2026-11-07

`chat_settings.kb_organizer_ids` is a JSON list of user ids, and the `/remember`
authority gate parses exactly that format (`parse_user_id_list`), so it stays as
it is. The organizer list used to name each id with a live `getChatMember`. Telegram's
person picker offers anyone (contacts, recent chats, global search), so an
organizer is often someone who is not in the group: the lookup then fails with
"member not found" or PARTICIPANT_ID_INVALID, and the list showed «участник
<id>», although the picker had handed over that person's name a moment before.

One row per (chat, person): the parts of the name the add path was given (the
picker's shared user, a forward's sender, a typed @username with the first
name the chat history knows, a participant's latest message). Every part is
nullable: Telegram gives some of them and not others. A newer add replaces the
row, because it is what Telegram said most recently.

This is not a setting. It is kept out of the ChatConfig merge, so the
"nullable, no DEFAULT" rule for `chat_settings` columns does not apply, and
`updated_at` has a DEFAULT.

Removing an organizer leaves the row: `↩️ Вернуть` must bring the name back. It also
means a row can outlive the organizer. The rows are a few per chat and hold names
the bot was given when they were added. A group->supergroup upgrade leaves them on
the old id, like every chat-keyed table here (`ChatMigrationRepository`).
"""

from __future__ import annotations

from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_organizer_names (
            chat_id     BIGINT      NOT NULL,
            user_id     BIGINT      NOT NULL,
            first_name  TEXT,
            last_name   TEXT,
            username    TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kb_organizer_names")
