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

    results = await repo.search_by_embedding([0.1] * 768, limit=3, min_similarity=0.7)

    assert len(results) == 1
    assert results[0]["similarity"] == 0.85
    sql = repo._pool.fetch.call_args.args[0]
    assert "description_embedding" in sql
    assert "analysis_failed = false" in sql


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
