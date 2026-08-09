"""Tests for sticker repository SQL construction."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database.repositories.stickers import StickerRepository


@pytest.fixture
def repo():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"id": 1})
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return StickerRepository(pool)


@pytest.mark.asyncio
async def test_get_by_file_unique_id(repo):
    repo._pool.fetchrow = AsyncMock(return_value={"file_unique_id": "abc"})

    result = await repo.get_by_file_unique_id("abc")

    assert result == {"file_unique_id": "abc"}
    repo._pool.fetchrow.assert_awaited_once()
    call_args = repo._pool.fetchrow.call_args
    assert "abc" in call_args.args


@pytest.mark.asyncio
async def test_get_by_file_unique_id_not_found(repo):
    repo._pool.fetchrow = AsyncMock(return_value=None)

    result = await repo.get_by_file_unique_id("unknown")

    assert result is None


@pytest.mark.asyncio
async def test_save_sticker_returns_id(repo):
    repo._pool.fetchrow = AsyncMock(return_value={"id": 42})

    sticker_id = await repo.save_sticker(
        file_unique_id="unique-1",
        file_id="file-1",
        set_name="test_set",
        emoji="😀",
        visual_description="A happy face",
    )

    assert sticker_id == 42
    repo._pool.fetchrow.assert_awaited_once()
    sql = repo._pool.fetchrow.call_args.args[0]
    assert "INSERT INTO sticker_knowledge" in sql
    assert "ON CONFLICT" in sql


@pytest.mark.asyncio
async def test_save_sticker_passes_dedup_columns(repo):
    """ADR-0007: image_hash / duplicate_of_file_unique_id are wired through
    to the INSERT (both the SQL text and the bound positional params)."""
    repo._pool.fetchrow = AsyncMock(return_value={"id": 7})

    await repo.save_sticker(
        file_unique_id="unique-1",
        file_id="file-1",
        image_hash="0123456789abcdef",
        duplicate_of_file_unique_id="canonical-uid",
    )

    sql = repo._pool.fetchrow.call_args.args[0]
    assert "image_hash" in sql
    assert "duplicate_of_file_unique_id" in sql
    bound_args = repo._pool.fetchrow.call_args.args[1:]
    assert "0123456789abcdef" in bound_args
    assert "canonical-uid" in bound_args


@pytest.mark.asyncio
async def test_get_dedup_candidates_query_shape(repo):
    repo._pool.fetch = AsyncMock(
        return_value=[
            {
                "file_unique_id": "uid-1",
                "image_hash": "0000000000000000",
                "created_at": "2026-01-01",
                "duplicate_of_file_unique_id": None,
            }
        ]
    )

    results = await repo.get_dedup_candidates()

    assert len(results) == 1
    sql = repo._pool.fetch.call_args.args[0]
    assert "image_hash IS NOT NULL" in sql
    assert "visual_description IS NOT NULL" in sql
    assert "analysis_failed = false" in sql


@pytest.mark.asyncio
async def test_update_embedding(repo):
    embedding = [0.1] * 768

    await repo.update_embedding("unique-1", embedding)

    repo._pool.execute.assert_awaited_once()
    sql = repo._pool.execute.call_args.args[0]
    assert "description_embedding" in sql
    assert repo._pool.execute.call_args.args[1] == "unique-1"


@pytest.mark.asyncio
async def test_increment_usage(repo):
    await repo.increment_usage("unique-1", is_bot_use=True)

    repo._pool.execute.assert_awaited_once()
    sql = repo._pool.execute.call_args.args[0]
    assert "total_uses" in sql
    assert "bot_uses" in sql


@pytest.mark.asyncio
async def test_search_by_embedding(repo):
    repo._pool.fetch = AsyncMock(
        return_value=[
            {
                "file_id": "file-1",
                "file_unique_id": "unique-1",
                "visual_description": "Happy cat",
                "similarity": 0.85,
            }
        ]
    )

    results = await repo.search_by_embedding(
        [0.1] * 768, limit=3, min_similarity=0.7, tolerance_level=0.5
    )

    assert len(results) == 1
    assert results[0]["similarity"] == 0.85
    sql = repo._pool.fetch.call_args.args[0]
    assert "description_embedding" in sql
    assert "analysis_failed = false" in sql


@pytest.mark.asyncio
async def test_search_by_embedding_gates_on_tolerance(repo):
    """ADR-0008 Decision 6: the tolerance predicate + value are wired into
    the SQL text and bound params, not silently dropped."""
    repo._pool.fetch = AsyncMock(return_value=[])

    await repo.search_by_embedding([0.1] * 768, limit=3, min_similarity=0.7, tolerance_level=0.42)

    sql = repo._pool.fetch.call_args.args[0]
    assert "explicitness_score IS NOT NULL" in sql
    assert "explicitness_score <=" in sql
    bound_args = repo._pool.fetch.call_args.args[1:]
    assert 0.42 in bound_args


@pytest.mark.asyncio
async def test_get_pack_context_with_exclude(repo):
    repo._pool.fetch = AsyncMock(return_value=[{"visual_description": "Sad cat"}])

    results = await repo.get_pack_context("funny_cats", exclude_file_unique_id="unique-1")

    assert len(results) == 1
    sql = repo._pool.fetch.call_args.args[0]
    assert "set_name" in sql
    assert "file_unique_id !=" in sql


@pytest.mark.asyncio
async def test_get_pack_context_without_exclude(repo):
    repo._pool.fetch = AsyncMock(return_value=[])

    await repo.get_pack_context("funny_cats")

    sql = repo._pool.fetch.call_args.args[0]
    assert "set_name" in sql
    assert "file_unique_id !=" not in sql


@pytest.mark.asyncio
async def test_accumulate_context(repo):
    await repo.accumulate_context("unique-1", "hello world")

    repo._pool.execute.assert_awaited_once()
    sql = repo._pool.execute.call_args.args[0]
    assert "usage_contexts" in sql
    assert repo._pool.execute.call_args.args[1] == "unique-1"
    assert repo._pool.execute.call_args.args[2] == "hello world"


@pytest.mark.asyncio
async def test_upsert_sticker_set(repo):
    await repo.upsert_sticker_set(
        set_name="funny_cats",
        set_title="Funny Cats",
        total_count=20,
    )

    repo._pool.execute.assert_awaited_once()
    sql = repo._pool.execute.call_args.args[0]
    assert "INSERT INTO sticker_sets" in sql
    assert "ON CONFLICT" in sql


@pytest.mark.asyncio
async def test_get_sticker_set(repo):
    repo._pool.fetchrow = AsyncMock(
        return_value={"set_name": "funny_cats", "set_title": "Funny Cats"}
    )

    result = await repo.get_sticker_set("funny_cats")

    assert result["set_name"] == "funny_cats"
    repo._pool.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_sticker_set_not_found(repo):
    repo._pool.fetchrow = AsyncMock(return_value=None)

    result = await repo.get_sticker_set("unknown")

    assert result is None


@pytest.mark.asyncio
async def test_save_sticker_passes_explicitness_score(repo):
    """ADR-0008: explicitness_score is wired through to the INSERT (both the
    SQL text and the bound positional params), and reaches the ON CONFLICT
    UPDATE clause too (unconditional overwrite, same as emotion/character)."""
    repo._pool.fetchrow = AsyncMock(return_value={"id": 9})

    await repo.save_sticker(
        file_unique_id="unique-1",
        file_id="file-1",
        explicitness_score=0.7,
    )

    sql = repo._pool.fetchrow.call_args.args[0]
    assert "explicitness_score" in sql
    bound_args = repo._pool.fetchrow.call_args.args[1:]
    assert 0.7 in bound_args


@pytest.mark.asyncio
async def test_save_sticker_explicitness_score_defaults_to_none(repo):
    repo._pool.fetchrow = AsyncMock(return_value={"id": 9})

    await repo.save_sticker(file_unique_id="unique-1", file_id="file-1")

    bound_args = repo._pool.fetchrow.call_args.args[1:]
    # explicitness_score, explicitness_is_manual are the last two bound params.
    assert bound_args[-2] is None
    assert bound_args[-1] is False


@pytest.mark.asyncio
async def test_save_sticker_passes_explicitness_is_manual(repo):
    """ADR-0009 Decision 4: explicitness_is_manual is wired through to the
    INSERT (both the SQL text and the bound positional params)."""
    repo._pool.fetchrow = AsyncMock(return_value={"id": 10})

    await repo.save_sticker(
        file_unique_id="unique-1",
        file_id="file-1",
        explicitness_score=0.9,
        explicitness_is_manual=True,
    )

    sql = repo._pool.fetchrow.call_args.args[0]
    assert "explicitness_is_manual" in sql
    bound_args = repo._pool.fetchrow.call_args.args[1:]
    assert bound_args[-1] is True


@pytest.mark.asyncio
async def test_save_sticker_explicitness_is_manual_absent_from_set_clause(repo):
    """ADR-0009 Decision 4: explicitness_is_manual must NOT appear in the
    ON CONFLICT ... DO UPDATE SET clause — Postgres leaves an unlisted
    column untouched on UPDATE, so only the two dedicated methods
    (set_manual_explicitness_score / reset_explicitness_to_auto) can ever
    change it. Every write through save_sticker() must leave it as-is."""
    repo._pool.fetchrow = AsyncMock(return_value={"id": 11})

    await repo.save_sticker(file_unique_id="unique-1", file_id="file-1")

    sql = repo._pool.fetchrow.call_args.args[0]
    set_clause = sql[sql.index("DO UPDATE") :]
    assert "explicitness_is_manual =" not in set_clause


@pytest.mark.asyncio
async def test_save_sticker_explicitness_score_case_protects_manual(repo):
    """ADR-0009 Decision 4: the ON CONFLICT SET clause must check the
    EXISTING row's own explicitness_is_manual flag (not the incoming value)
    before allowing an overwrite."""
    repo._pool.fetchrow = AsyncMock(return_value={"id": 12})

    await repo.save_sticker(file_unique_id="unique-1", file_id="file-1")

    sql = repo._pool.fetchrow.call_args.args[0]
    assert "WHEN sticker_knowledge.explicitness_is_manual" in sql
    assert "THEN sticker_knowledge.explicitness_score" in sql


@pytest.mark.asyncio
async def test_get_explicitness_backfill_candidates_query_shape(repo):
    repo._pool.fetch = AsyncMock(
        return_value=[
            {
                "file_unique_id": "uid-1",
                "file_id": "file-1",
                "is_animated": False,
                "is_video": False,
            }
        ]
    )

    results = await repo.get_explicitness_backfill_candidates()

    assert len(results) == 1
    sql = repo._pool.fetch.call_args.args[0]
    assert "visual_description IS NOT NULL" in sql
    assert "analysis_failed = false" in sql
    assert "explicitness_score IS NULL" in sql


@pytest.mark.asyncio
async def test_update_explicitness_score(repo):
    await repo.update_explicitness_score("unique-1", 0.4)

    repo._pool.execute.assert_awaited_once()
    sql = repo._pool.execute.call_args.args[0]
    assert "explicitness_score" in sql
    assert "UPDATE sticker_knowledge" in sql
    assert repo._pool.execute.call_args.args[1] == "unique-1"
    assert repo._pool.execute.call_args.args[2] == 0.4


@pytest.mark.asyncio
async def test_set_manual_explicitness_score(repo):
    """ADR-0009 Decision 5: sets both the score and the manual flag."""
    await repo.set_manual_explicitness_score("unique-1", 0.8)

    repo._pool.execute.assert_awaited_once()
    sql = repo._pool.execute.call_args.args[0]
    assert "UPDATE sticker_knowledge" in sql
    assert "explicitness_score = $2" in sql
    assert "explicitness_is_manual = true" in sql
    assert repo._pool.execute.call_args.args[1] == "unique-1"
    assert repo._pool.execute.call_args.args[2] == 0.8


@pytest.mark.asyncio
async def test_reset_explicitness_to_auto(repo):
    """ADR-0009 Decision 5: clears both fields to NULL/false, no remembered
    prior automatic value."""
    await repo.reset_explicitness_to_auto("unique-1")

    repo._pool.execute.assert_awaited_once()
    sql = repo._pool.execute.call_args.args[0]
    assert "UPDATE sticker_knowledge" in sql
    assert "explicitness_score = NULL" in sql
    assert "explicitness_is_manual = false" in sql
    assert repo._pool.execute.call_args.args[1] == "unique-1"
