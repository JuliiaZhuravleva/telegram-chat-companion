"""Unit tests for RetentionCleaner + MaintenanceRepository (periodic_cleanup parity)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from src.config import MaintenanceSettings
from src.database.repositories.maintenance import RETENTION_TABLES, MaintenanceRepository
from src.services.maintenance.cleanup import RetentionCleaner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cleaner(**overrides: object) -> RetentionCleaner:
    config = MaintenanceSettings(**overrides)  # type: ignore[arg-type]
    return RetentionCleaner(pool=AsyncMock(), config=config)


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------


# Tables that are eligible for pruning but deliberately have no default window.
# Keeping this explicit is the point: an unscheduled table either grows forever
# (a bug) or holds state that must not age out (a decision). Say which.
_EXEMPT_BY_DESIGN = {
    # `has_rejected_attempt()` reads this with no time bound and the access
    # middleware short-circuits on it, so a rejected row IS the ban record.
    # See tests/integration/test_retention_preserves_bans.py.
    "unauthorized_attempts",
}


class TestWindows:
    def test_defaults_cover_every_retention_table_except_the_exempt_ones(self) -> None:
        """A table listed as prunable but never scheduled would grow forever —
        unless its exemption is deliberate, in which case it is listed above."""
        windows = _make_cleaner()._windows()
        assert set(windows) == set(RETENTION_TABLES) - _EXEMPT_BY_DESIGN

    def test_exempt_tables_are_still_prunable_when_asked(self) -> None:
        """The exemption is a default, not a prohibition — an operator who
        explicitly sets a window still gets one."""
        windows = _make_cleaner(unauthorized_attempts_days=30)._windows()
        assert windows["unauthorized_attempts"] == timedelta(days=30)

    def test_default_windows_match_config(self) -> None:
        windows = _make_cleaner()._windows()
        assert windows["user_activity"] == timedelta(hours=1)
        assert windows["chat_messages"] == timedelta(days=365)
        assert windows["response_log"] == timedelta(days=90)
        assert windows["abuse_blocked_log"] == timedelta(days=30)
        assert windows["message_reactions"] == timedelta(days=30)
        assert windows["decision_log"] == timedelta(days=90)
        assert windows["retrieval_log"] == timedelta(days=90)
        assert "unauthorized_attempts" not in windows

    def test_message_reactions_window_can_be_disabled(self) -> None:
        """message_reactions is a short, separate window (ADR-0004) -- must
        still be independently disableable like every other retention table."""
        windows = _make_cleaner(reactions_days=None)._windows()
        assert "message_reactions" not in windows
        assert "chat_messages" in windows

    def test_none_window_disables_that_table(self) -> None:
        """Operators must be able to opt out of deleting a specific table."""
        windows = _make_cleaner(chat_messages_days=None)._windows()
        assert "chat_messages" not in windows
        assert "user_activity" in windows

    def test_all_none_yields_no_work(self) -> None:
        windows = _make_cleaner(
            user_activity_hours=None,
            chat_messages_days=None,
            response_log_days=None,
            unauthorized_attempts_days=None,
            abuse_blocked_log_days=None,
            reactions_days=None,
            decision_log_days=None,
            retrieval_log_days=None,
        )._windows()
        assert windows == {}

    def test_chat_messages_default_preserves_migrated_history(self) -> None:
        """The n8n migration carries ~36k messages over; a 30-day window (the
        reference bot's) would delete almost all of them on the first pass."""
        assert MaintenanceSettings().chat_messages_days is not None
        assert MaintenanceSettings().chat_messages_days > 30  # type: ignore[operator]


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_prunes_every_configured_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = AsyncMock()
        repo.delete_older_than = AsyncMock(return_value=3)
        monkeypatch.setattr(
            "src.services.maintenance.cleanup.MaintenanceRepository", lambda _pool: repo
        )

        cleaner = _make_cleaner()
        deleted = await cleaner.run_once()

        scheduled = set(RETENTION_TABLES) - _EXEMPT_BY_DESIGN
        assert deleted == dict.fromkeys(scheduled, 3)
        assert repo.delete_older_than.await_count == len(scheduled)

    @pytest.mark.asyncio
    async def test_omits_tables_with_nothing_to_delete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = AsyncMock()
        repo.delete_older_than = AsyncMock(return_value=0)
        monkeypatch.setattr(
            "src.services.maintenance.cleanup.MaintenanceRepository", lambda _pool: repo
        )

        assert await _make_cleaner().run_once() == {}

    @pytest.mark.asyncio
    async def test_one_failing_table_does_not_stop_the_sweep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bad window on one table must not stall retention for the rest."""

        async def _delete(table: str, _window: timedelta) -> int:
            if table == "chat_messages":
                raise RuntimeError("boom")
            return 1

        repo = AsyncMock()
        repo.delete_older_than = AsyncMock(side_effect=_delete)
        monkeypatch.setattr(
            "src.services.maintenance.cleanup.MaintenanceRepository", lambda _pool: repo
        )

        deleted = await _make_cleaner().run_once()

        assert "chat_messages" not in deleted
        assert len(deleted) == len(set(RETENTION_TABLES) - _EXEMPT_BY_DESIGN) - 1

    @pytest.mark.asyncio
    async def test_passes_timedelta_not_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """asyncpg rejects string intervals for $1::interval (project gotcha)."""
        repo = AsyncMock()
        repo.delete_older_than = AsyncMock(return_value=0)
        monkeypatch.setattr(
            "src.services.maintenance.cleanup.MaintenanceRepository", lambda _pool: repo
        )

        await _make_cleaner().run_once()

        for call in repo.delete_older_than.await_args_list:
            assert isinstance(call.args[1], timedelta)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_disabled_config_starts_no_task(self) -> None:
        cleaner = _make_cleaner(enabled=False)
        await cleaner.start()
        assert cleaner._task is None
        await cleaner.stop()  # must stay safe with no task

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self) -> None:
        cleaner = _make_cleaner()
        await cleaner.start()
        assert cleaner._task is not None
        await cleaner.stop()
        assert cleaner._task.cancelled() or cleaner._task.done()


# ---------------------------------------------------------------------------
# Repository guard
# ---------------------------------------------------------------------------


class TestMaintenanceRepository:
    @pytest.mark.asyncio
    async def test_rejects_table_outside_allow_list(self) -> None:
        """Table names are interpolated into SQL, so the allow-list is the
        security boundary — never let an arbitrary name through."""
        repo = MaintenanceRepository(AsyncMock())
        with pytest.raises(ValueError, match="not eligible"):
            await repo.delete_older_than("bot_config", timedelta(days=1))

    @pytest.mark.asyncio
    async def test_returns_zero_when_query_yields_none(self) -> None:
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)
        repo = MaintenanceRepository(pool)
        assert await repo.delete_older_than("chat_messages", timedelta(days=1)) == 0
