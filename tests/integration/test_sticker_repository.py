"""
Integration tests: StickerRepository against real pgvector Postgres.

Covers the acceptance criteria in A-1 (TD-002):
  - save_sticker  → upsert semantics (insert then ON CONFLICT increment)
  - increment_usage → counters update correctly
  - clear_for_reanalysis → analysis fields are nulled out
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
    async def test_sets_analyzed_at_when_description_present(
        self, repo: StickerRepository
    ) -> None:
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
# clear_for_reanalysis
# ---------------------------------------------------------------------------


class TestClearForReanalysis:
    """clear_for_reanalysis NULLs out analysis fields to trigger re-analysis."""

    @pytest.mark.asyncio
    async def test_clears_visual_description(self, repo: StickerRepository) -> None:
        await repo.save_sticker(
            file_unique_id="clr-001",
            file_id="f-clr-001",
            visual_description="Old description",
        )
        await repo.clear_for_reanalysis("clr-001")

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
        await repo.clear_for_reanalysis("clr-002")

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
        await repo.clear_for_reanalysis("clr-003")

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
        await repo.clear_for_reanalysis("clr-004")

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
        await repo.clear_for_reanalysis("clr-005")

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
