"""chat_user_aliases -- the names a chat actually calls its people.

Revision ID: 033
Revises: 032
Create Date: 2026-08-25

The bot renders every participant as `username or first_name or user_id`
(`prompt_builder._format_message`), so it addresses a person by whatever their
Telegram account happens to say. In a real chat that is often not the name
anyone uses: the account reads "Капитан", the humans say "Костя". The bot
therefore both *addresses* people wrongly and *fails to understand* who a
question is about -- "what did <spoken name> do two months ago" searches an
archive filed under the account name, and the lexical leg of hybrid retrieval
misses by construction.

Until now the only place that mapping existed was a single hand-written
knowledge-base fact, captured through `/remember`. A fact reaches the prompt
only when similarity retrieval happens to pull it, which on a measured
six-turn production conversation it did **zero** times. Aliases have to be
present unconditionally, on every turn, which is why this is a table of its
own and not another `chat_facts` topic.

**One table, two different jobs, and they must not be confused.**

- `role = 'primary'` is what the bot *says*. Exactly one per person per chat,
  set by a human -- the person themselves, or a chat admin on their behalf.
  The cost of getting it wrong is the bot calling someone by a name it chose
  for them, in front of everyone.
- `role = 'alternate'` is what the bot *understands*. Any number of them. The
  cost of getting one wrong is a single retrieval that misses.

That asymmetry is the whole safety argument for the auto-collection planned as
a later slice: **automation may write `alternate` and must never write
`primary`.** The column is what makes that rule expressible, so it exists from
the first migration rather than being retrofitted around an autocollector.

**Uniqueness -- and what these indexes deliberately do NOT buy.**

Two partial uniques, both modelled on `idx_chat_facts_active_key`:
one active primary per `(chat_id, user_id)`, and one owner per
`(chat_id, alias_norm)` so a name cannot be claimed by two people in the same
chat -- which is also what stops someone assigning themselves a name another
member already answers to.

Both are scoped `WHERE status = 'active'`, so a retired row leaves the index
entirely -- which is what lets somebody take a name back after dropping it, and
is also why the index alone decides nothing. The repository resolves every
deterministic conflict in Python, under an advisory lock, before inserting; the
index is left as the backstop for the one case locking cannot cover, two
writers racing when no row exists yet.

**The rule it enforces in Python is that a `primary` outranks an `alternate`,
whoever owns it.** That single sentence closes two failures that the first
version of this table shipped with, both found by review and both reproduced:
promoting your own auto-seeded account name to primary raised a duplicate-key
error the retry loop then repeated for ever, and a second member could be
refused a name that only a machine-written recognition row was holding. A name
the bot *says* is a stronger claim than one it merely also understands, so the
alternate yields. An `alternate` yields to everything and displaces nothing,
which is what makes automation safe to point at it.

Said here because a test that exercises only the index will pass while both of
those bugs ship.

**Deviations from what a fresh reader might expect, each deliberate:**

- *No CHECK constraints* on `role`/`status`. The allowed values are trailing
  comments, as in `chat_facts` -- the only CHECK in this schema is on
  `health_log.status`. Migrations here are forward-only (merging to main is a
  production release and there is no rollback), so a too-tight CHECK is a
  one-way door.
- *No foreign keys.* There is no users table, and no chat-keyed table
  references `chat_settings`: an FK would break the integration-test idiom of
  inserting rows for arbitrary negative chat ids, and would fight
  `ChatMigrationRepository`, which rekeys `chat_settings.chat_id` first.
- *`alias_norm` is a plain TEXT column normalised in Python*, not `citext` and
  not `unaccent` -- neither extension exists in this database, and adding one
  is a separate decision with its own rehearsal risk. Note that the case-fold
  here is **not** the same no-op as the `translate($n, 'ёЁ', 'еЕ')` in
  `ChunkRepository.search`: that one mirrors an FTS expression where
  PostgreSQL's `russian` configuration already folds ё→е itself (measured,
  migration 029). A btree unique index does no folding of any kind, so the
  application genuinely has to do the work here.
- *Indexes on the full key* (`chat_id, user_id` and `chat_id, alias_norm`,
  both unpartitioned) exist for the pre-check above, which must see retired
  rows the partial uniques cannot.

**This table is self-gating.** An empty table means no roster section and no
name substitution, i.e. no behaviour change at all. There is deliberately no
fourth per-chat flag: the feature turns itself on for a chat the moment
somebody in it says what they want to be called.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "033"
down_revision: str = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One SQL statement per op.execute(): migrations run online through
    # SQLAlchemy's asyncpg dialect, which PREPAREs every statement, and
    # PostgreSQL rejects a prepared statement holding more than one command.
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_user_aliases (
            id                 BIGSERIAL PRIMARY KEY,
            chat_id            BIGINT NOT NULL,
            user_id            BIGINT NOT NULL,
            alias              TEXT NOT NULL,          -- as written, this is what renders
            alias_norm         TEXT NOT NULL,          -- normalised, this is what uniqueness sees
            role               TEXT NOT NULL,          -- primary|alternate
            status             TEXT NOT NULL DEFAULT 'active',  -- active|superseded|rejected|pending
            source             TEXT NOT NULL,          -- self|admin|remember|extracted
            source_message_id  BIGINT,
            source_user_id     BIGINT,                 -- who set it, may differ from user_id
            confidence         FLOAT,                  -- extractor confidence, NULL when manual
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # Serves the per-turn read (`WHERE chat_id = $1 AND status = 'active'`, on
    # the leading column) and the primary lookup keyed by both columns.
    #
    # There is deliberately NO second plain index on `(chat_id, alias_norm)`.
    # An earlier draft added one, justified by a pre-check that had to see
    # retired rows -- and then the pre-check was rewritten to be role-aware
    # instead, so no query looks up a retired row by name any more. All three
    # `alias_norm` queries in the repository are scoped `status = 'active'`,
    # which is exactly `uq_chat_user_aliases_name`'s predicate, so that partial
    # unique already serves them and is the smaller index. An index kept for a
    # reason that has stopped being true is write cost with a comment on it.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_user_aliases_chat_user
        ON chat_user_aliases(chat_id, user_id)
    """)

    # DB-level backstop for "exactly one active primary per person"
    # (ADR-0003's argument, applied to a second table).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_user_aliases_primary
        ON chat_user_aliases(chat_id, user_id)
        WHERE role = 'primary' AND status = 'active'
    """)
    # ...and for "one name belongs to one person in a chat".
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_user_aliases_name
        ON chat_user_aliases(chat_id, alias_norm)
        WHERE status = 'active'
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS chat_user_aliases_updated_at ON chat_user_aliases
    """)
    op.execute("""
        CREATE TRIGGER chat_user_aliases_updated_at
            BEFORE UPDATE ON chat_user_aliases
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)


def downgrade() -> None:
    # CASCADE takes the indexes and the trigger with it.
    op.execute("DROP TABLE IF EXISTS chat_user_aliases CASCADE")
