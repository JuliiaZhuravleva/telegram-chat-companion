"""``AdminRepository.get_health_by_hour`` against real Postgres: the status board's day strip."""

from __future__ import annotations

from datetime import datetime

import asyncpg
import pytest

from src.database.repositories.admin import AdminRepository


@pytest.mark.asyncio
async def test_worst_status_per_hour_inside_the_window(db_conn: asyncpg.Connection) -> None:
    await db_conn.execute("DELETE FROM health_log")  # rolled back with the test
    for hours_back, minute, status in (
        (2, 5, "healthy"),
        (2, 20, "critical"),  # one bad check colours its whole hour
        (2, 40, "warning"),
        (1, 10, "skipped"),
        (1, 50, "skipped"),
        (0, 0, "warning"),  # the current hour
        (26, 0, "critical"),  # outside the 24 h window
    ):
        await db_conn.execute(
            "INSERT INTO health_log (checked_at, status) VALUES ("
            "date_trunc('hour', NOW()) - make_interval(hours => $1) + make_interval(mins => $2),"
            " $3)",
            hours_back,
            minute,
            status,
        )

    rows = await AdminRepository(db_conn).get_health_by_hour(24)  # type: ignore[arg-type]

    this_hour = await db_conn.fetchval("SELECT date_trunc('hour', NOW() AT TIME ZONE 'UTC')")
    assert all(isinstance(row["hour"], datetime) and row["hour"].tzinfo is None for row in rows)
    by_hours_back = {
        int((this_hour - row["hour"]).total_seconds() // 3600): row["worst"] for row in rows
    }
    assert by_hours_back == {2: 3, 1: 0, 0: 2}
    assert [row["hour"] for row in rows] == sorted(row["hour"] for row in rows)


@pytest.mark.asyncio
async def test_no_checks_no_rows(db_conn: asyncpg.Connection) -> None:
    await db_conn.execute("DELETE FROM health_log")
    assert await AdminRepository(db_conn).get_health_by_hour(24) == []  # type: ignore[arg-type]
