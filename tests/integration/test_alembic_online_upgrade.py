"""
`alembic upgrade head` must work ONLINE against a real, empty database.

This is the exact command the Dockerfile CMD runs at container start, and the
one the Mac mini's deploy harness runs in a one-off container before the bot
starts.  Neither path had any test coverage, because ``conftest.run_migrations``
applies migrations through alembic's *offline* ``--sql`` mode and pipes the SQL
through asyncpg's simple query protocol — which happily accepts multi-statement
strings that the online path cannot execute.

That gap hid a real defect: migration 008 put ``DROP TRIGGER ...; CREATE TRIGGER
...`` inside a single ``op.execute()``.  Online, SQLAlchemy's asyncpg dialect
PREPAREs each statement and PostgreSQL rejects a prepared statement holding more
than one command, so ``alembic upgrade head`` aborted at 008 on every fresh
database while the whole test suite stayed green.

These tests are the control for that: run against the pre-fix 008 they fail with
``cannot insert multiple commands into a prepared statement``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

PROJECT_ROOT = Path(__file__).parent.parent.parent
ONLINE_DB = "alembic_online_probe"


def _swap_db(url: str, dbname: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{dbname}"


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke alembic the way the container entrypoint does: online, via subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest_asyncio.fixture
async def empty_database(pg_url: str) -> str:  # type: ignore[misc]
    """A throwaway database with nothing in it — not even the vector extension."""
    admin = await asyncpg.connect(pg_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{ONLINE_DB}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{ONLINE_DB}"')
    finally:
        await admin.close()

    yield _swap_db(pg_url, ONLINE_DB)

    admin = await asyncpg.connect(pg_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{ONLINE_DB}" WITH (FORCE)')
    finally:
        await admin.close()


class TestOnlineUpgrade:
    @pytest.mark.asyncio
    async def test_upgrade_head_succeeds_on_a_fresh_database(self, empty_database: str) -> None:
        result = _run_alembic(empty_database, "upgrade", "head")

        assert result.returncode == 0, (
            "alembic upgrade head failed against a real database — this is what "
            f"prod does on first boot.\nstderr:\n{result.stderr}"
        )

    @pytest.mark.asyncio
    async def test_schema_reaches_head_and_the_bot_can_start(self, empty_database: str) -> None:
        """main.py's _verify_schema() checks these tables exist before serving."""
        _run_alembic(empty_database, "upgrade", "head")

        conn = await asyncpg.connect(empty_database)
        try:
            heads = _run_alembic(empty_database, "heads")
            applied = await conn.fetchval("SELECT version_num FROM alembic_version")
            assert applied in heads.stdout, f"applied {applied!r} is not the head revision"

            for table in ("bot_config", "chat_settings", "custom_rules", "health_log"):
                exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
                assert exists, f"_verify_schema() would reject a DB without {table}"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_upgrade_is_idempotent(self, empty_database: str) -> None:
        """claw-deploy runs the migration step and the Dockerfile CMD runs it again;
        the second pass has to be a clean no-op, not an error."""
        _run_alembic(empty_database, "upgrade", "head")
        second = _run_alembic(empty_database, "upgrade", "head")

        assert second.returncode == 0, f"second upgrade failed:\n{second.stderr}"

    @pytest.mark.asyncio
    async def test_no_migration_bundles_multiple_commands_in_one_execute(
        self, empty_database: str
    ) -> None:
        """Belt-and-braces companion to the run above: walk the chain one revision
        at a time so a failure names the offending revision instead of just
        'head'."""
        revisions = [
            "001", "002", "003", "004", "005", "006", "007",
            "008", "009", "010", "011", "012", "014", "015",
            "016", "017", "018",
        ]  # fmt: skip
        for revision in revisions:
            result = _run_alembic(empty_database, "upgrade", revision)
            assert result.returncode == 0, (
                f"migration {revision} cannot be applied online.\nstderr:\n{result.stderr}"
            )
