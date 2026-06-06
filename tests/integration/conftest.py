"""
Integration test configuration.

Spins up a real pgvector/pgvector:pg16 container via testcontainers,
runs Alembic migrations once for the session, then exposes:

  - pg_url         (session, sync)   — plain postgresql:// URL
  - db_pool        (session, async)  — asyncpg Pool
  - db_conn        (function, async) — isolated connection; rolls back after each test

Migration strategy:
  Alembic's env.py uses SQLAlchemy's asyncpg dialect, which wraps every statement
  in asyncpg's PREPARE (extended query protocol).  PostgreSQL rejects multi-statement
  PREPARE strings, which breaks migrations that put multiple SQL statements inside a
  single op.execute() call.

  Instead we use alembic's --sql (offline) mode to generate plain SQL, then apply it
  via asyncpg's conn.execute() (simple query protocol) which supports multi-statement
  strings natively.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from pgvector.asyncpg import register_vector
from testcontainers.postgres import PostgresContainer

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Must match docker-compose.yml for pgvector support
PGVECTOR_IMAGE = "pgvector/pgvector:pg16"


# ---------------------------------------------------------------------------
# Container  (session-scoped, synchronous)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container():
    """Start pgvector container once for the whole test session."""
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="test",
        password="test",
        dbname="test_db",
        driver=None,  # return plain postgresql:// URL, not sqlalchemy-driver URL
    ) as container:
        yield container


@pytest.fixture(scope="session")
def pg_url(postgres_container: PostgresContainer) -> str:
    """Plain postgresql:// connection URL (compatible with asyncpg)."""
    return postgres_container.get_connection_url(driver=None)


# ---------------------------------------------------------------------------
# Migrations  (session-scoped, synchronous — must finish before async pool opens)
# ---------------------------------------------------------------------------


def _generate_migration_sql() -> str:
    """
    Use ``alembic upgrade head --sql`` (offline mode) to emit the full migration SQL.

    Offline mode does NOT connect to Postgres; it just renders the SQL.
    A fake DATABASE_URL is required only to satisfy alembic's env.py.
    """
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://fake:fake@localhost/fake",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic --sql generation failed:\n{result.stderr}")
    return result.stdout


async def _apply_migration_sql(pg_url: str, sql: str) -> None:
    """
    Apply rendered migration SQL via asyncpg's simple query protocol.

    asyncpg.Connection.execute() without parameters uses PostgreSQL's simple
    query protocol, which supports arbitrary multi-statement strings and never
    tries to PREPARE the statement — unlike the extended protocol used by
    SQLAlchemy's asyncpg dialect.
    """
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def run_migrations(pg_url: str) -> None:
    """Render and apply all Alembic migrations against the test container."""
    sql = _generate_migration_sql()
    asyncio.run(_apply_migration_sql(pg_url, sql))


# ---------------------------------------------------------------------------
# asyncpg pool + per-test isolation  (function-scoped to share the test's event loop)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_pool(pg_url: str, run_migrations: None) -> asyncpg.Pool:  # type: ignore[misc]  # noqa: ARG001
    """
    Per-test asyncpg connection pool.

    Function-scoped so the pool lives on the same event loop as the test
    function — avoids "Future attached to a different loop" errors that arise
    when a session-scoped pool (created on Loop A) is used by a test running
    on Loop B.

    Pool creation against a local Docker container is fast (< 50 ms); the
    overhead is acceptable for the isolation guarantee.
    """

    async def _init_conn(conn: asyncpg.Connection) -> None:
        """Register pgvector codec — mirrors src/database/connection.py."""
        await register_vector(conn)

    pool: asyncpg.Pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=3, init=_init_conn)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def db_conn(db_pool: asyncpg.Pool) -> asyncpg.Connection:  # type: ignore[misc]
    """
    Per-test database connection wrapped in a transaction.

    The transaction is rolled back after each test, leaving the DB clean
    for the next test without any TRUNCATE overhead.

    Repositories accept either a Pool or Connection — both expose the same
    fetchrow / fetch / execute / fetchval interface.
    """
    async with db_pool.acquire() as conn:  # type: ignore[union-attr]
        tx = conn.transaction()
        await tx.start()
        yield conn
        await tx.rollback()
