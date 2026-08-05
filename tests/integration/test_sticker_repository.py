"""
Integration tests: StickerRepository against real pgvector Postgres.

Covers the acceptance criteria in A-1 (TD-002):
  - save_sticker  → upsert semantics (insert then ON CONFLICT increment)
  - increment_usage → counters update correctly
  - clear_analysis → analysis fields are nulled out
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.stickers import StickerRepository

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def repo(db_conn: asyncpg.Connection) -> StickerRepository:
    """StickerRepository backed by the test connection (rolls back after test)."""
    return StickerRepository(db_conn)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# save_sticker — upsert
# ---------------------------------------------------------------------------


class TestSaveStickerUpsert:
    """save_sticker inserts on first call, updates total_uses on conflict."""

    @pytest.mark.asyncio
    async def test_insert_returns_integer_id(self, repo: StickerRepository) -> None:
        sticker_id = await repo.save_sticker(
            file_unique_id="unique-001",
            file_id="file-001",
            set_name="test_pack",
            emoji="😀",
            visual_description="A happy face sticker",
        )
        assert isinstance(sticker_id, int)
        assert sticker_id > 0

    @pytest.mark.asyncio
    async def test_insert_is_retrievable(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="unique-002",
            file_id="file-002",
            visual_description="A cat",
        )
        row = await repo.get_by_file_unique_id("unique-002")
        assert row is not None
        assert row["file_unique_id"] == "unique-002"
        assert row["visual_description"] == "A cat"
        assert row["total_uses"] == 1

    @pytest.mark.asyncio
    async def test_conflict_increments_total_uses(self, repo: StickerRepository) -> None:
        """Inserting the same file_unique_id twice increments total_uses."""
        await repo.save_sticker(file_unique_id="unique-003", file_id="file-003")
        await repo.save_sticker(file_unique_id="unique-003", file_id="file-003-v2")

        row = await repo.get_by_file_unique_id("unique-003")
        assert row is not None
        assert row["total_uses"] == 2
        # file_id should be updated to the newer value
        assert row["file_id"] == "file-003-v2"

    @pytest.mark.asyncio
    async def test_sets_analyzed_at_when_description_present(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="unique-004",
            file_id="file-004",
            visual_description="A sad dog",
        )
        row = await repo.get_by_file_unique_id("unique-004")
        assert row is not None
        assert row["analyzed_at"] is not None

    @pytest.mark.asyncio
    async def test_analyzed_at_null_when_no_description(self, repo: StickerRepository) -> None:
        await repo.save_sticker(file_unique_id="unique-005", file_id="file-005")
        row = await repo.get_by_file_unique_id("unique-005")
        assert row is not None
        assert row["analyzed_at"] is None

    @pytest.mark.asyncio
    async def test_analysis_failed_flag_stored(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="unique-006",
            file_id="file-006",
            analysis_failed=True,
        )
        row = await repo.get_by_file_unique_id("unique-006")
        assert row is not None
        assert row["analysis_failed"] is True


# ---------------------------------------------------------------------------
# increment_usage
# ---------------------------------------------------------------------------


class TestIncrementUsage:
    """increment_usage updates counters on an existing sticker row."""

    @pytest.mark.asyncio
    async def test_increments_total_uses(self, repo: StickerRepository) -> None:
        await repo.save_sticker(file_unique_id="inc-001", file_id="f-001")
        await repo.increment_usage("inc-001")

        row = await repo.get_by_file_unique_id("inc-001")
        assert row is not None
        # save_sticker starts at 1; increment adds 1 → 2
        assert row["total_uses"] == 2

    @pytest.mark.asyncio
    async def test_increments_bot_uses_when_flag_true(self, repo: StickerRepository) -> None:
        await repo.save_sticker(file_unique_id="inc-002", file_id="f-002")
        await repo.increment_usage("inc-002", is_bot_use=True)

        row = await repo.get_by_file_unique_id("inc-002")
        assert row is not None
        assert row["bot_uses"] == 1

    @pytest.mark.asyncio
    async def test_does_not_increment_bot_uses_when_flag_false(
        self, repo: StickerRepository
    ) -> None:
        await repo.save_sticker(file_unique_id="inc-003", file_id="f-003")
        await repo.increment_usage("inc-003", is_bot_use=False)

        row = await repo.get_by_file_unique_id("inc-003")
        assert row is not None
        assert row["bot_uses"] == 0

    @pytest.mark.asyncio
    async def test_multiple_increments_accumulate(self, repo: StickerRepository) -> None:
        await repo.save_sticker(file_unique_id="inc-004", file_id="f-004")
        for _ in range(4):
            await repo.increment_usage("inc-004")

        row = await repo.get_by_file_unique_id("inc-004")
        assert row is not None
        assert row["total_uses"] == 5  # 1 from insert + 4 increments


# ---------------------------------------------------------------------------
# clear_analysis
# ---------------------------------------------------------------------------


class TestClearAnalysis:
    """clear_analysis NULLs out analysis fields to trigger re-analysis."""

    @pytest.mark.asyncio
    async def test_clears_visual_description(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="clr-001",
            file_id="f-clr-001",
            visual_description="Old description",
        )
        await repo.clear_analysis("clr-001")

        row = await repo.get_by_file_unique_id("clr-001")
        assert row is not None
        assert row["visual_description"] is None

    @pytest.mark.asyncio
    async def test_clears_analyzed_at(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="clr-002",
            file_id="f-clr-002",
            visual_description="Will be cleared",
        )
        await repo.clear_analysis("clr-002")

        row = await repo.get_by_file_unique_id("clr-002")
        assert row is not None
        assert row["analyzed_at"] is None

    @pytest.mark.asyncio
    async def test_resets_analysis_failed(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="clr-003",
            file_id="f-clr-003",
            analysis_failed=True,
        )
        await repo.clear_analysis("clr-003")

        row = await repo.get_by_file_unique_id("clr-003")
        assert row is not None
        assert row["analysis_failed"] is False

    @pytest.mark.asyncio
    async def test_clears_embedding(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="clr-004",
            file_id="f-clr-004",
            visual_description="Has embedding",
        )
        await repo.update_embedding("clr-004", [0.1] * 768)
        await repo.clear_analysis("clr-004")

        row = await repo.get_by_file_unique_id("clr-004")
        assert row is not None
        assert row["description_embedding"] is None

    @pytest.mark.asyncio
    async def test_preserves_usage_counters(self, repo: StickerRepository) -> None:
        """Clearing analysis should NOT reset usage statistics."""
        await repo.save_sticker(
            file_unique_id="clr-005",
            file_id="f-clr-005",
            visual_description="Count me",
        )
        await repo.increment_usage("clr-005")
        await repo.clear_analysis("clr-005")

        row = await repo.get_by_file_unique_id("clr-005")
        assert row is not None
        assert row["total_uses"] == 2  # 1 insert + 1 increment


# ---------------------------------------------------------------------------
# Round-trip: get_by_file_id
# ---------------------------------------------------------------------------


class TestGetByFileId:
    @pytest.mark.asyncio
    async def test_lookup_by_file_id(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="fid-001",
            file_id="the-file-id",
            set_name="some_pack",
        )
        row = await repo.get_by_file_id("the-file-id")
        assert row is not None
        assert row["file_unique_id"] == "fid-001"

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_file_id(self, repo: StickerRepository) -> None:
        result = await repo.get_by_file_id("does-not-exist")
        assert result is None


# ---------------------------------------------------------------------------
# get_stickers_in_set — status derivation (powers the set-detail admin view)
# ---------------------------------------------------------------------------


class TestGetStickersInSet:
    """get_stickers_in_set() returns rows that power the set-detail admin view.

    Status is derived by the caller (``_status_badge``) from the returned columns:
      - ``visual_description IS NOT NULL``  →  analyzed (✅)
      - ``visual_description IS NULL`` and ``analysis_failed = False``  →  not analyzed (⏳)
      - ``analysis_failed = True``  →  failed (⚠️)

    These tests exercise all three status transitions as visible through the query.
    """

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_set(self, repo: StickerRepository) -> None:
        rows = await repo.get_stickers_in_set("nonexistent_pack_xyz")
        assert rows == []

    @pytest.mark.asyncio
    async def test_not_analyzed_sticker_appears_in_set(self, repo: StickerRepository) -> None:
        """Sticker saved without a description is retrievable and reflects not-analyzed state."""
        await repo.save_sticker(
            file_unique_id="gsis-001",
            file_id="f-gsis-001",
            set_name="gsis_set_a",
        )
        rows = await repo.get_stickers_in_set("gsis_set_a")
        assert len(rows) == 1
        assert rows[0]["file_unique_id"] == "gsis-001"
        # not-analyzed: description NULL, failed flag False
        assert rows[0]["visual_description"] is None
        assert rows[0]["analysis_failed"] is False

    @pytest.mark.asyncio
    async def test_analyzed_sticker_shows_description(self, repo: StickerRepository) -> None:
        """Sticker saved with a description reflects completed (✅) analysis state."""
        await repo.save_sticker(
            file_unique_id="gsis-002",
            file_id="f-gsis-002",
            set_name="gsis_set_b",
            visual_description="A cheerful cat waving",
        )
        rows = await repo.get_stickers_in_set("gsis_set_b")
        assert len(rows) == 1
        assert rows[0]["visual_description"] == "A cheerful cat waving"
        assert rows[0]["analysis_failed"] is False

    @pytest.mark.asyncio
    async def test_failed_sticker_shows_failed_flag(self, repo: StickerRepository) -> None:
        """Sticker with analysis_failed=True reflects failed (⚠️) analysis state."""
        await repo.save_sticker(
            file_unique_id="gsis-003",
            file_id="f-gsis-003",
            set_name="gsis_set_c",
            analysis_failed=True,
        )
        rows = await repo.get_stickers_in_set("gsis_set_c")
        assert len(rows) == 1
        assert rows[0]["analysis_failed"] is True
        assert rows[0]["visual_description"] is None

    @pytest.mark.asyncio
    async def test_only_returns_stickers_from_requested_set(self, repo: StickerRepository) -> None:
        """Stickers from a different set are not included in results."""
        await repo.save_sticker(
            file_unique_id="gsis-004",
            file_id="f-gsis-004",
            set_name="gsis_set_alpha",
        )
        await repo.save_sticker(
            file_unique_id="gsis-005",
            file_id="f-gsis-005",
            set_name="gsis_set_beta",
        )
        rows = await repo.get_stickers_in_set("gsis_set_alpha")
        assert len(rows) == 1
        assert rows[0]["file_unique_id"] == "gsis-004"

    @pytest.mark.asyncio
    async def test_pagination_limit_and_offset(self, repo: StickerRepository) -> None:
        """limit and offset correctly slice the result set."""
        for i in range(4):
            await repo.save_sticker(
                file_unique_id=f"gsis-pg-{i:03d}",
                file_id=f"f-gsis-pg-{i:03d}",
                set_name="gsis_paginated_set",
            )
        page1 = await repo.get_stickers_in_set("gsis_paginated_set", limit=2, offset=0)
        page2 = await repo.get_stickers_in_set("gsis_paginated_set", limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        ids_page1 = {r["file_unique_id"] for r in page1}
        ids_page2 = {r["file_unique_id"] for r in page2}
        assert ids_page1.isdisjoint(ids_page2)

    @pytest.mark.asyncio
    async def test_clear_then_get_shows_not_analyzed(self, repo: StickerRepository) -> None:
        """After clear_analysis(), get_stickers_in_set() surfaces the sticker as not-analyzed."""
        await repo.save_sticker(
            file_unique_id="gsis-006",
            file_id="f-gsis-006",
            set_name="gsis_set_d",
            visual_description="Will be cleared",
        )
        # Verify analyzed state before clear
        rows = await repo.get_stickers_in_set("gsis_set_d")
        assert rows[0]["visual_description"] == "Will be cleared"

        await repo.clear_analysis("gsis-006")

        rows = await repo.get_stickers_in_set("gsis_set_d")
        assert len(rows) == 1
        assert rows[0]["visual_description"] is None
        assert rows[0]["analysis_failed"] is False

    @pytest.mark.asyncio
    async def test_analyze_transition_updates_set_view(self, repo: StickerRepository) -> None:
        """save_sticker with a description transitions not-analyzed → analyzed as seen in set view."""
        # Insert without description (not-analyzed)
        await repo.save_sticker(
            file_unique_id="gsis-007",
            file_id="f-gsis-007",
            set_name="gsis_set_e",
        )
        rows = await repo.get_stickers_in_set("gsis_set_e")
        assert rows[0]["visual_description"] is None

        # Re-save with description (simulates successful analysis)
        await repo.save_sticker(
            file_unique_id="gsis-007",
            file_id="f-gsis-007",
            set_name="gsis_set_e",
            visual_description="A dog playing fetch",
            analysis_failed=False,
        )
        rows = await repo.get_stickers_in_set("gsis_set_e")
        assert rows[0]["visual_description"] == "A dog playing fetch"
        assert rows[0]["analysis_failed"] is False


# ---------------------------------------------------------------------------
# TestClearAnalysis — extension: verify via get_stickers_in_set()
# ---------------------------------------------------------------------------


class TestClearAnalysisSetListView:
    """Extension of clear_analysis coverage: transitions visible through get_stickers_in_set().

    The set-detail admin view uses get_stickers_in_set(), not get_by_file_unique_id().
    This class ensures the clear → not-analyzed transition is visible through that query.
    """

    @pytest.mark.asyncio
    async def test_clear_visible_in_set_list(self, repo: StickerRepository) -> None:
        """clear_analysis() → not-analyzed state is immediately reflected in set-list query."""
        await repo.save_sticker(
            file_unique_id="clrv-001",
            file_id="f-clrv-001",
            set_name="clrv_set",
            visual_description="Pre-clear description",
        )
        # Confirm analyzed state is visible
        rows = await repo.get_stickers_in_set("clrv_set")
        assert len(rows) == 1
        assert rows[0]["visual_description"] == "Pre-clear description"

        await repo.clear_analysis("clrv-001")

        rows = await repo.get_stickers_in_set("clrv_set")
        assert len(rows) == 1
        assert rows[0]["visual_description"] is None
        assert rows[0]["analysis_failed"] is False

    @pytest.mark.asyncio
    async def test_failed_sticker_clears_to_not_analyzed_in_set_list(
        self, repo: StickerRepository
    ) -> None:
        """clear_analysis() on a failed sticker resets to not-analyzed in the set-list view."""
        await repo.save_sticker(
            file_unique_id="clrv-002",
            file_id="f-clrv-002",
            set_name="clrv_set_fail",
            analysis_failed=True,
        )
        rows = await repo.get_stickers_in_set("clrv_set_fail")
        assert rows[0]["analysis_failed"] is True

        await repo.clear_analysis("clrv-002")

        rows = await repo.get_stickers_in_set("clrv_set_fail")
        assert rows[0]["analysis_failed"] is False
        assert rows[0]["visual_description"] is None


# ---------------------------------------------------------------------------
# Admin notifications (migration 016 regression)
# ---------------------------------------------------------------------------


class TestAdminNotifications:
    """save_notification / get_notification_by_reply / get_latest_sticker_msg.

    Regression for the 2026-08-02 prod incident: these methods hit
    admin_sticker_notifications, which existed only in hand-patched dev
    databases until migration 016 — on a fresh `alembic upgrade head` schema
    every call raised UndefinedTableError. Running them against the migrated
    testcontainer proves the migration creates everything the repository needs.
    """

    @pytest.mark.asyncio
    async def test_save_returns_integer_id(self, repo: StickerRepository) -> None:
        notif_id = await repo.save_notification(
            file_unique_id="notif-001",
            admin_id=111,
            message_id=1001,
            sticker_msg_id=1000,
            chat_id=111,
        )
        assert isinstance(notif_id, int)
        assert notif_id > 0

    @pytest.mark.asyncio
    async def test_reply_lookup_matches_sticker_msg_id(self, repo: StickerRepository) -> None:
        """Admin replies to the sticker message itself."""
        await repo.save_notification(
            file_unique_id="notif-002",
            admin_id=111,
            message_id=2002,
            sticker_msg_id=2001,
            chat_id=111,
        )
        row = await repo.get_notification_by_reply(chat_id=111, reply_to_message_id=2001)
        assert row is not None
        assert row["file_unique_id"] == "notif-002"

    @pytest.mark.asyncio
    async def test_reply_lookup_matches_message_id(self, repo: StickerRepository) -> None:
        """Admin replies to the description message instead of the sticker."""
        await repo.save_notification(
            file_unique_id="notif-003",
            admin_id=111,
            message_id=3002,
            sticker_msg_id=3001,
            chat_id=111,
        )
        row = await repo.get_notification_by_reply(chat_id=111, reply_to_message_id=3002)
        assert row is not None
        assert row["file_unique_id"] == "notif-003"

    @pytest.mark.asyncio
    async def test_reply_lookup_unknown_returns_none(self, repo: StickerRepository) -> None:
        row = await repo.get_notification_by_reply(chat_id=111, reply_to_message_id=99999)
        assert row is None

    @pytest.mark.asyncio
    async def test_latest_sticker_msg_returns_newest(
        self, repo: StickerRepository, db_conn: asyncpg.Connection
    ) -> None:
        old_id = await repo.save_notification(
            file_unique_id="notif-004",
            admin_id=222,
            message_id=4002,
            sticker_msg_id=4001,
            chat_id=222,
        )
        # NOW() is transaction-stable, so backdate the first row explicitly
        # to make the ORDER BY created_at DESC deterministic.
        await db_conn.execute(
            "UPDATE admin_sticker_notifications"
            " SET created_at = created_at - interval '1 minute' WHERE id = $1",
            old_id,
        )
        await repo.save_notification(
            file_unique_id="notif-005",
            admin_id=222,
            message_id=5002,
            sticker_msg_id=5001,
            chat_id=222,
        )
        assert await repo.get_latest_sticker_msg(admin_id=222, chat_id=222) == 5001

    @pytest.mark.asyncio
    async def test_latest_sticker_msg_ignores_zero_and_other_admins(
        self, repo: StickerRepository
    ) -> None:
        await repo.save_notification(
            file_unique_id="notif-006",
            admin_id=333,
            message_id=6002,
            sticker_msg_id=0,  # filtered out by sticker_msg_id > 0
            chat_id=333,
        )
        assert await repo.get_latest_sticker_msg(admin_id=333, chat_id=333) is None
        assert await repo.get_latest_sticker_msg(admin_id=444, chat_id=444) is None
