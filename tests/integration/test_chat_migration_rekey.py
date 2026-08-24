"""A supergroup upgrade must carry the curated moderation rules with it (TD-112).

Why this cannot be a unit test. The defect was not that `migrate()` failed —
it succeeded, reported `migrated`, and moved everything it knew about. The
whole bug lives in the gap between what the transaction touches and what the
*readers* look up, and both readers are SQL: `RulesRepository.get_active_rules`
filters `enabled = true AND (status = 'active' OR status IS NULL)`, and the
admin menu enumerates chats from `chat_settings`. A mock of `conn.execute`
proves the statement was issued; only a real table proves the rule comes back
out under the new id.

The rules are seeded through the REAL `RulesRepository.create()` for the same
reason the S4 test drives the real `migrate()`: a hand-written INSERT can
easily produce a row the reader filters away, and then `get_active_rules(OLD)
== []` passes for entirely the wrong reason. Each test asserts the rule is
retrievable under the OLD id BEFORE the migration, so a fixture that produced
an invisible rule fails as a precondition instead of masquerading as a result.

Ids are deliberately fake — this repository is public and gitleaks cannot see a
bare integer (CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from src.database.repositories.chat_migration import ChatMigrationRepository
from src.database.repositories.rules import RulesRepository

OLD_ID = -1009999991001
NEW_ID = -1009999991002


@pytest_asyncio.fixture
async def clean_ids(db_pool: asyncpg.Pool) -> AsyncIterator[None]:
    """`db_pool` has no rollback (only `db_conn` does), so clean both ways.

    Before, so a re-run after a crash starts from a known state; after, so the
    session database does not accumulate an `enabled = true` settings row that
    some later unqualified aggregate would inherit.
    """

    async def _purge() -> None:
        for table in ("custom_rules", "chat_facts", "chat_settings"):
            await db_pool.execute(
                f"DELETE FROM {table} WHERE chat_id = ANY($1::bigint[])",  # noqa: S608
                [OLD_ID, NEW_ID],
            )

    await _purge()
    yield
    await _purge()


async def _seed_chat_with_a_rule(db_pool: asyncpg.Pool, chat_id: int) -> int:
    await db_pool.execute(
        "INSERT INTO chat_settings (chat_id, chat_title, chat_type, enabled) "
        "VALUES ($1, 'rekey probe', 'group', true)",
        chat_id,
    )
    return await RulesRepository(db_pool).create(
        chat_id=chat_id,
        rule_type="keyword",
        config={"keywords": ["инадзума"], "action": "set_reaction", "emoji": "💊"},
    )


class TestCuratedRulesFollowTheChat:
    async def test_rules_are_retrievable_under_the_new_id(
        self, db_pool: asyncpg.Pool, clean_ids: None
    ) -> None:
        rule_id = await _seed_chat_with_a_rule(db_pool, OLD_ID)
        rules = RulesRepository(db_pool)

        before = await rules.get_active_rules(OLD_ID)
        assert [r["id"] for r in before] == [rule_id], (
            "fixture precondition: the seeded rule must be visible to the real reader "
            "BEFORE the migration, or an empty result afterwards proves nothing"
        )

        outcome = await ChatMigrationRepository(db_pool).migrate(OLD_ID, NEW_ID)

        assert outcome.status == "migrated"
        assert outcome.rules_moved == 1
        assert [r["id"] for r in await rules.get_active_rules(NEW_ID)] == [rule_id]
        assert await rules.get_active_rules(OLD_ID) == [], (
            "a rule left on the old id keeps firing for a chat that no longer exists"
        )

    async def test_a_refused_migration_leaves_the_rules_where_they_work(
        self, db_pool: asyncpg.Pool, clean_ids: None
    ) -> None:
        """`target_occupied` must destroy nothing — including the third table.

        Reachable in production: `ChatConfigMiddleware.ensure_exists()` creates
        a settings row for every chat it sees, and the new supergroup's own
        service message can arrive first.
        """
        rule_id = await _seed_chat_with_a_rule(db_pool, OLD_ID)
        await db_pool.execute(
            "INSERT INTO chat_settings (chat_id, chat_title, chat_type) "
            "VALUES ($1, 'already here', 'supergroup')",
            NEW_ID,
        )
        rules = RulesRepository(db_pool)

        outcome = await ChatMigrationRepository(db_pool).migrate(OLD_ID, NEW_ID)

        assert outcome.status == "target_occupied"
        assert outcome.rules_moved == 0
        assert [r["id"] for r in await rules.get_active_rules(OLD_ID)] == [rule_id], (
            "refusing must not strand the rules halfway"
        )
        assert await rules.get_active_rules(NEW_ID) == []


class TestTheSchemaAllowsTheReKey:
    async def test_custom_rules_cannot_collide_on_a_re_key(self, db_pool: asyncpg.Pool) -> None:
        """Documents WHY the `chat_settings`-only guard is enough for rules.

        `chat_facts` carries a partial UNIQUE on (chat_id, subject, predicate),
        so a target id holding facts can abort the whole transaction. If
        `custom_rules` ever grows a chat-scoped unique index, the same becomes
        true of rules and the guard must widen with it — this test is the
        tripwire for that day.
        """
        colliding = await db_pool.fetch(
            """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE tablename = 'custom_rules' AND indexdef LIKE '%UNIQUE%'
              AND indexdef LIKE '%chat_id%'
            """
        )
        assert colliding == [], (
            f"custom_rules grew a chat-scoped UNIQUE index: {[dict(r) for r in colliding]} — "
            "migrate()'s target probe only checks chat_settings and will now abort mid-transaction"
        )
