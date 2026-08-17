"""Mark a bot message as a voice transcription and link it to its source audio.

Revision ID: 028
Revises: 027
Create Date: 2026-08-17

`chat_messages.transcribed_message_id` — when non-NULL, THIS row is a message
the bot posted that carries the transcription of the voice/video note whose
`message_id` it holds. "Field is filled" is the whole test: there is no text
parsing and no heuristic.

Why a column and not a text marker. The bot needs to recognise its own
transcriptions, because a user replying to one is answering the person who
spoke, not the bot — without that, every such reply was read as
`TriggerType.REPLY` and answered unconditionally. The first implementation
recognised them by matching the rendered header ("🎙 Расшифровка от …"). That
is forgeable: a user can ask the bot to echo that exact text back, and the
bot's ordinary AI answer then becomes indistinguishable from a transcription —
replies to it stop counting as addressed to the bot, and the prompt is handed
an attacker-chosen author name for words that person never said. A column
cannot be forged: nothing but this code path ever writes it.

Notes on the row itself:

- `content` stays NULL. The transcript is already stored as the *voice*
  message's own content (`VoiceTranscriptionService.transcribe` UPSERTs it
  there), and writing it twice would put the same utterance in the prompt
  twice, once as the speaker and once as "Bot:".
- Because the row is content-free bookkeeping rather than a conversational
  turn, `message_type = 'transcription'` rows are excluded from the prompt
  history query and from `get_bot_message_stats`. Counting them would inflate
  the relevancy gate's `bot_ratio` / `consecutive_bot_at_end` and make the bot
  gradually mute itself in any chat with voice traffic.

No backfill is possible for messages that predate this: the bot's
transcription messages were never written to `chat_messages` at all (the old
save path UPSERTed the transcript onto the voice row under the voice message's
own id), so their Telegram message ids exist nowhere in our data and the Bot
API cannot read history back. `scripts/backfill_transcription_links.py` links
what is recoverable and reports what is not. Replies to transcriptions posted
before this deploy therefore keep the old behaviour; the gap closes on its own
as new voice messages arrive.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "028"
down_revision: str = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One statement per op.execute(): migrations run online through asyncpg,
    # which PREPAREs every statement, and PostgreSQL rejects a prepared
    # statement holding more than one command (CLAUDE.md).
    op.execute("""
        ALTER TABLE chat_messages
        ADD COLUMN IF NOT EXISTS transcribed_message_id BIGINT
    """)
    # Partial index: the backfill script and any "does a transcription already
    # exist for this audio" lookup scan by source id, and only a tiny fraction
    # of rows ever carry the column. The hot read path (is the replied-to
    # message a transcription?) is served by the (chat_id, message_id) primary
    # key and needs nothing here.
    #
    # Plain CREATE INDEX, not CONCURRENTLY — alembic runs migrations inside a
    # transaction and CONCURRENTLY cannot, the same trade-off 017 documents for
    # this table. It takes a SHARE lock that blocks writes for the build. The
    # WHERE clause keeps that build tiny (only rows with the column set, i.e.
    # none at all at migration time), so the lock is momentary today; revisit
    # together with 017 if chat_messages ever reaches millions of rows.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_transcribed_source
        ON chat_messages (chat_id, transcribed_message_id)
        WHERE transcribed_message_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_messages_transcribed_source")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS transcribed_message_id")
