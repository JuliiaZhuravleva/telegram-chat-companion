"""Tests for KnowledgeRepository (chat_facts) with mocked asyncpg pool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database.repositories.knowledge import KnowledgeRepository


class _AsyncCM:
    """Minimal async context manager wrapper (pattern: test_link_extractor.py)."""

    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *exc_info):
        return False


@pytest.fixture
def conn():
    """A mocked asyncpg connection acquired from the pool."""
    connection = MagicMock()
    connection.fetchrow = AsyncMock()
    connection.execute = AsyncMock()
    connection.transaction = MagicMock(return_value=_AsyncCM(None))
    return connection


@pytest.fixture
def repo(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return KnowledgeRepository(pool), pool


# ---------------------------------------------------------------------------
# upsert_fact — supersession-in-transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_fact_no_existing_row_is_plain_insert(repo, conn):
    repo_, pool = repo
    conn.fetchrow.side_effect = [
        None,  # no existing active row at this (chat_id, subject, predicate)
        {"id": 1},  # new row insert RETURNING id
    ]

    new_id = await repo_.upsert_fact(
        chat_id=1,
        subject="мероприятие",
        predicate="дата",
        value="2026-08-01",
        fact_text="Мероприятие состоится 2026-08-01",
        source="manual",
    )

    assert new_id == 1
    pool.acquire.assert_called_once()
    conn.transaction.assert_called_once()
    assert conn.fetchrow.await_count == 2
    # Advisory lock is the only execute() — no supersession UPDATEs.
    conn.execute.assert_awaited_once()
    lock_sql = conn.execute.await_args_list[0].args[0]
    assert "pg_advisory_xact_lock" in lock_sql

    select_sql = conn.fetchrow.await_args_list[0].args[0]
    assert "FOR UPDATE" in select_sql
    assert "valid_to IS NULL" in select_sql

    insert_sql = conn.fetchrow.await_args_list[1].args[0]
    assert "INSERT INTO chat_facts" in insert_sql
    assert "'active'" in insert_sql


@pytest.mark.asyncio
async def test_upsert_fact_supersedes_existing_active_row(repo, conn):
    repo_, _pool = repo
    conn.fetchrow.side_effect = [
        {"id": 5},  # existing active row at this key
        {"id": 6},  # new row insert RETURNING id
    ]

    new_id = await repo_.upsert_fact(
        chat_id=1,
        subject="мероприятие",
        predicate="дата",
        value="2026-08-02",
        fact_text="Дата перенесена на 2026-08-02",
        source="manual",
    )

    assert new_id == 6
    # execute order: advisory lock -> close old row -> stamp superseded_by.
    # The old row must be closed BEFORE the new INSERT (unique partial index
    # forbids two active rows for the key even transiently).
    assert conn.execute.await_count == 3
    lock_sql = conn.execute.await_args_list[0].args[0]
    assert "pg_advisory_xact_lock" in lock_sql

    close_sql, close_old_id = conn.execute.await_args_list[1].args
    assert "UPDATE chat_facts" in close_sql
    assert "status = 'superseded'" in close_sql
    assert close_old_id == 5

    stamp_sql, stamp_old_id, superseded_by = conn.execute.await_args_list[2].args
    assert "superseded_by" in stamp_sql
    assert stamp_old_id == 5
    assert superseded_by == 6


@pytest.mark.asyncio
async def test_upsert_fact_retries_once_on_unique_violation(repo, conn):
    """Create-create race backstop: unique violation -> one retry that supersedes."""
    import asyncpg

    repo_, _pool = repo
    conn.fetchrow.side_effect = [
        None,  # attempt 1: no existing row
        asyncpg.UniqueViolationError("duplicate key value violates unique constraint"),
        {"id": 7},  # attempt 2: the racing winner's row is now visible
        {"id": 8},  # attempt 2: new row insert RETURNING id
    ]

    new_id = await repo_.upsert_fact(
        chat_id=1,
        subject="s",
        predicate="p",
        value="v",
        fact_text="ft",
        source="manual",
    )

    assert new_id == 8
    assert conn.fetchrow.await_count == 4


@pytest.mark.asyncio
async def test_upsert_fact_uses_one_transaction(repo, conn):
    """Both the existing-row check and the writes happen inside conn.transaction()."""
    repo_, _pool = repo
    conn.fetchrow.side_effect = [{"id": 5}, {"id": 6}]

    await repo_.upsert_fact(
        chat_id=1,
        subject="s",
        predicate="p",
        value="v",
        fact_text="ft",
        source="manual",
    )

    conn.transaction.assert_called_once()


# ---------------------------------------------------------------------------
# get_by_id / get_active_facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_id_found(repo):
    repo_, pool = repo
    pool.fetchrow.return_value = {"id": 1, "chat_id": 1, "subject": "s"}

    result = await repo_.get_by_id(1, chat_id=1)

    assert result == {"id": 1, "chat_id": 1, "subject": "s"}
    pool.fetchrow.assert_awaited_once()
    assert pool.fetchrow.call_args.args[1:] == (1, 1)


@pytest.mark.asyncio
async def test_get_by_id_not_found(repo):
    repo_, pool = repo
    pool.fetchrow.return_value = None

    result = await repo_.get_by_id(999, chat_id=1)

    assert result is None


@pytest.mark.asyncio
async def test_get_active_facts_without_topic(repo):
    repo_, pool = repo
    pool.fetch.return_value = [{"id": 1, "subject": "s"}]

    result = await repo_.get_active_facts(1)

    assert result == [{"id": 1, "subject": "s"}]
    sql = pool.fetch.call_args.args[0]
    assert "status = 'active'" in sql
    assert "valid_to IS NULL" in sql
    assert "topic = $2" not in sql


@pytest.mark.asyncio
async def test_get_active_facts_with_topic_filter(repo):
    repo_, pool = repo
    pool.fetch.return_value = []

    await repo_.get_active_facts(1, topic="event:summer-meetup")

    sql, chat_id, topic = pool.fetch.call_args.args
    assert "topic = $2" in sql
    assert chat_id == 1
    assert topic == "event:summer-meetup"


# ---------------------------------------------------------------------------
# search_by_similarity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_similarity_orders_salience_then_similarity(repo):
    repo_, pool = repo
    pool.fetch.return_value = [
        {"id": 1, "similarity": 0.9, "salience": 0.8},
    ]
    embedding = [0.1] * 768

    result = await repo_.search_by_similarity(1, embedding, limit=3)

    assert len(result) == 1
    sql, chat_id, query_embedding, limit = pool.fetch.call_args.args
    assert "ORDER BY salience DESC, embedding <=> $2 ASC" in sql
    assert "status = 'active'" in sql
    assert "valid_to IS NULL" in sql
    assert "embedding IS NOT NULL" in sql
    assert chat_id == 1
    assert query_embedding == embedding
    assert limit == 3


# ---------------------------------------------------------------------------
# reject_fact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_fact_returns_true_when_updated(repo):
    repo_, pool = repo
    pool.execute.return_value = "UPDATE 1"

    result = await repo_.reject_fact(1, chat_id=1)

    assert result is True
    sql = pool.execute.call_args.args[0]
    assert "status = 'rejected'" in sql
    assert "DELETE" not in sql


@pytest.mark.asyncio
async def test_reject_fact_returns_false_when_no_row_matched(repo):
    repo_, pool = repo
    pool.execute.return_value = "UPDATE 0"

    result = await repo_.reject_fact(999, chat_id=1)

    assert result is False
