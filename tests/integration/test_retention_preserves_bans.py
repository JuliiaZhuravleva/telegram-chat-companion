"""Retention must not age out the rows that encode a ban.

`unauthorized_attempts` looks like a log, and the reference n8n bot pruned it
like one (30 days). Here it is state: `AdminRepository.has_rejected_attempt()`
asks "was this chat ever rejected?" with no time bound, and
`AccessControlMiddleware` short-circuits on the answer. So a `rejected` row *is*
the ban record.

This was caught against real production data during the n8n migration: 3 of the
14 rejected rows were older than 30 days, and 2 chats would have lost their only
rejected row within an hour of the bot starting — silently un-banning them.

The test drives the real cleaner over a real table, then asks the real
repository, because the failure is a *coupling* between two components that each
look correct alone.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest
import pytest_asyncio

from src.config import MaintenanceSettings
from src.database.repositories.admin import AdminRepository
from src.database.repositories.maintenance import MaintenanceRepository
from src.services.maintenance.cleanup import RetentionCleaner

CHAT_ID = -100_555_000_111
ANCIENT = timedelta(days=400)


@pytest_asyncio.fixture
async def ancient_rejection(db_pool: asyncpg.Pool) -> int:
    """A chat rejected long ago — older than any window we might configure."""
    await db_pool.execute("DELETE FROM unauthorized_attempts WHERE chat_id = $1", CHAT_ID)
    return int(
        await db_pool.fetchval(
            """
            INSERT INTO unauthorized_attempts (chat_id, chat_title, status, created_at)
            VALUES ($1, 'long-banned chat', 'rejected', NOW() - $2::interval)
            RETURNING id
            """,
            CHAT_ID,
            ANCIENT,
        )
    )


async def test_default_config_never_prunes_unauthorized_attempts() -> None:
    """The window is None on purpose. Setting a number here re-opens the hole."""
    assert MaintenanceSettings().unauthorized_attempts_days is None, (
        "unauthorized_attempts must not have a retention window: "
        "has_rejected_attempt() reads it as durable state, so pruning un-bans chats"
    )


async def test_cleaner_leaves_an_ancient_ban_intact(
    db_pool: asyncpg.Pool, ancient_rejection: int
) -> None:
    """Run the real cleaner with the real defaults; the ban must survive."""
    admin_repo = AdminRepository(db_pool)
    assert await admin_repo.has_rejected_attempt(CHAT_ID) is True

    cleaner = RetentionCleaner(pool=db_pool, config=MaintenanceSettings())
    await cleaner.run_once()

    assert await admin_repo.has_rejected_attempt(CHAT_ID) is True, (
        "the cleaner deleted the row the access middleware relies on"
    )
    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM unauthorized_attempts WHERE id = $1", ancient_rejection
        )
        == 1
    )


async def test_repository_still_refuses_an_ineligible_table(db_pool: asyncpg.Pool) -> None:
    """The allowlist is the guard against a caller-supplied table name."""
    repo = MaintenanceRepository(db_pool)
    with pytest.raises(ValueError, match="not eligible"):
        await repo.delete_older_than("chat_settings", timedelta(days=1))
