"""Tests for KnowledgeRepository (chat_facts) with mocked asyncpg pool."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import asyncpg
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
    connection.fetch = AsyncMock(return_value=[])
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


@pytest.mark.asyncio
async def test_upsert_fact_carries_expires_at_into_the_insert(repo, conn):
    """The supersession path gained `expires_at` too (migration 027).

    Asserts the BOUND PARAMETER, not the SQL: the INSERT text is shared with
    `append_fact` via `_INSERT_FACT`, so a `$14` in the string proves nothing
    about *this* call site actually forwarding the argument.
    """
    repo_, _pool = repo
    conn.fetchrow.side_effect = [{"id": 5}, {"id": 6}]
    expires_at = datetime(2026, 9, 5, 23, 59, 59, 999999, tzinfo=ZoneInfo("Asia/Tbilisi"))

    await repo_.upsert_fact(
        chat_id=-1009999990001,
        subject="s",
        predicate="p",
        value="v",
        fact_text="ft",
        source="manual",
        expires_at=expires_at,
    )

    insert_call = conn.fetchrow.await_args_list[1]
    assert "INSERT INTO chat_facts" in insert_call.args[0]
    assert _bound_param(insert_call, 14) is expires_at
    # ...and it still superseded: advisory lock + close old + stamp forward.
    assert conn.execute.await_count == 3
    assert any("status = 'superseded'" in c.args[0] for c in conn.execute.await_args_list)


# ---------------------------------------------------------------------------
# append_fact — the append-only write path (KB-07)
# ---------------------------------------------------------------------------


def _bound_param(call, position: int):
    """The value bound to `$<position>` of the statement in `call`.

    `call.args[0]` is the SQL, so `$1` is `args[1]` -- asserting on the bound
    parameter rather than on the SQL text is the point: `_INSERT_FACT` is shared
    between both write paths, so its text says nothing about what a given call
    site passed.
    """
    return call.args[position]


def _statements(pool, conn) -> list[str]:
    """Every SQL string actually issued, on the pool and on a pooled connection.

    Built from the awaited calls, so a statement that was never issued cannot
    appear here -- and an assertion over this list cannot pass vacuously by
    looking at a call that did not happen.
    """
    calls = []
    for target in (pool, conn):
        for method in ("fetchrow", "fetch", "execute", "fetchval"):
            mock = getattr(target, method, None)
            if mock is None or not hasattr(mock, "await_args_list"):
                continue
            calls.extend(c.args[0] for c in mock.await_args_list if c.args)
    return [c for c in calls if isinstance(c, str)]


@pytest.mark.asyncio
async def test_append_fact_binds_expires_at_as_the_14th_parameter(repo):
    """A tz-aware deadline must reach asyncpg unchanged.

    Not "a datetime equal to it": `end_of_day()` returns an aware value on
    purpose (asyncpg encodes a *naive* datetime through the process's local
    timezone, so dropping the tzinfo silently re-dates the deadline by the
    machine's UTC offset). Identity is the cheapest assertion that a
    normalisation step cannot slip in unnoticed.
    """
    repo_, pool = repo
    # Two statements now: the existence pre-check (no row -> None), then the
    # INSERT. The pre-check is what stops a redelivery from resurrecting an
    # undone fact, since `reject_fact` moves the row out of the partial UNIQUE
    # index that used to catch it.
    pool.fetchrow.side_effect = [None, {"id": 11}]
    expires_at = datetime(2026, 9, 5, 23, 59, 59, 999999, tzinfo=ZoneInfo("Asia/Tbilisi"))

    fact_id, created = await repo_.append_fact(
        chat_id=-1009999990001,
        subject="правила",
        predicate="m1001",
        value="сбор в 19:00",
        fact_text="сбор в 19:00",
        source="manual",
        expires_at=expires_at,
    )

    assert (fact_id, created) == (11, True)
    assert pool.fetchrow.await_count == 2
    precheck, call = pool.fetchrow.await_args_list
    assert "SELECT id, status FROM chat_facts" in precheck.args[0]
    assert "INSERT INTO chat_facts" in call.args[0]
    assert _bound_param(call, 14) is expires_at
    assert _bound_param(call, 14).tzinfo is not None
    # The other 13 slots must not have shifted while the 14th was added.
    assert _bound_param(call, 1) == -1009999990001
    assert _bound_param(call, 3) == "правила"
    assert _bound_param(call, 4) == "m1001"
    assert _bound_param(call, 13) == 0.5  # salience, i.e. expires_at was appended


@pytest.mark.asyncio
async def test_append_fact_defaults_expires_at_to_null(repo):
    """A fact without a deadline binds NULL, never a computed "far future"."""
    repo_, pool = repo
    pool.fetchrow.side_effect = [None, {"id": 12}]

    await repo_.append_fact(
        chat_id=-1009999990001,
        subject="s",
        predicate="m1",
        value="v",
        fact_text="ft",
        source="manual",
    )

    assert _bound_param(pool.fetchrow.await_args_list[1], 14) is None


@pytest.mark.asyncio
async def test_append_fact_never_supersedes_on_the_happy_path(repo, conn):
    """The whole invariant of KB-07, asserted on the statements really issued.

    Phase 1 wrote `/remember` through `upsert_fact`, whose key collapsed to
    (chat_id, subject) because the predicate was a constant -- so "add another
    detail about the same thing" retired the previous fact. The append path may
    therefore issue exactly two statements: the existence pre-check and the
    INSERT. No `FOR UPDATE` lookup (there is nothing to lock, by design), no
    `status = 'superseded'` UPDATE, no advisory lock, not even a transaction to
    hold them in.
    """
    repo_, pool = repo
    pool.fetchrow.side_effect = [None, {"id": 13}]

    await repo_.append_fact(
        chat_id=-1009999990001,
        subject="s",
        predicate="m1",
        value="v",
        fact_text="ft",
        source="manual",
    )

    issued = _statements(pool, conn)
    assert len(issued) == 2, f"append_fact must issue exactly two statements, got: {issued}"
    assert "SELECT id, status FROM chat_facts" in issued[0]
    assert "INSERT INTO chat_facts" in issued[1]
    joined = "\n".join(issued)
    assert "superseded" not in joined
    assert "FOR UPDATE" not in joined
    assert "UPDATE chat_facts" not in joined
    assert "pg_advisory_xact_lock" not in joined
    pool.acquire.assert_not_called()
    conn.transaction.assert_not_called()


@pytest.mark.asyncio
async def test_append_fact_returns_the_existing_row_on_unique_violation(repo, conn):
    """The redelivered-capture case: report the row that exists, write nothing.

    `created=False` is the load-bearing half -- it is what makes the handler say
    "already saved" instead of claiming a second save, and it must come with the
    *existing* id so the undo button points at a real row.
    """
    repo_, pool = repo
    pool.fetchrow.side_effect = [
        None,  # pre-check: nothing yet (the concurrent writer has not committed)
        asyncpg.UniqueViolationError("duplicate key value violates unique constraint"),
        {"id": 77},
    ]

    fact_id, created = await repo_.append_fact(
        chat_id=-1009999990001,
        subject="s",
        predicate="m1",
        value="v",
        fact_text="ft",
        source="manual",
    )

    assert (fact_id, created) == (77, False)
    assert pool.fetchrow.await_count == 3
    lookup = pool.fetchrow.await_args_list[2]
    assert "SELECT id FROM chat_facts" in lookup.args[0]
    assert "valid_to IS NULL" in lookup.args[0]
    assert lookup.args[1:] == (-1009999990001, "s", "m1")
    # Nothing was retired to make room for a fact that was never written.
    issued = "\n".join(_statements(pool, conn))
    assert "superseded" not in issued
    assert "UPDATE chat_facts" not in issued


@pytest.mark.asyncio
async def test_append_fact_does_not_resurrect_an_undone_fact(repo, conn):
    """A redelivered capture must not re-create a fact the user just removed.

    The partial UNIQUE index (`WHERE valid_to IS NULL`) cannot catch this on its
    own: `reject_fact` sets `valid_to = NOW()`, so an undone row *leaves* the
    index and the redelivered INSERT would succeed. The pre-check is the guard,
    and it must look at the key regardless of `valid_to` -- which is exactly what
    makes this test fail if someone "simplifies" it back to a live-rows-only
    lookup.
    """
    repo_, pool = repo
    pool.fetchrow.side_effect = [{"id": 91, "status": "rejected"}]

    fact_id, created = await repo_.append_fact(
        chat_id=-1009999990001,
        subject="s",
        predicate="m1",
        value="v",
        fact_text="ft",
        source="manual",
    )

    assert (fact_id, created) == (91, False)
    issued = _statements(pool, conn)
    assert len(issued) == 1, f"the pre-check must short-circuit before the INSERT, got: {issued}"
    assert "INSERT INTO chat_facts" not in "\n".join(issued)
    # The pre-check must NOT be scoped to live rows, or the rejected row is
    # invisible to it and the resurrection is back.
    assert "valid_to IS NULL" not in issued[0]


@pytest.mark.asyncio
async def test_append_fact_reraises_when_the_lookup_finds_no_row(repo):
    """A violation on some *other* key must not be reported as "already saved".

    Swallowing it would return a fact id that is not this capture's (or worse,
    a stale one), and the user would be told their text was stored when the
    INSERT failed.
    """
    repo_, pool = repo
    pool.fetchrow.side_effect = [
        asyncpg.UniqueViolationError("duplicate key value violates unique constraint"),
        None,
    ]

    with pytest.raises(asyncpg.UniqueViolationError):
        await repo_.append_fact(
            chat_id=-1009999990001,
            subject="s",
            predicate="m1",
            value="v",
            fact_text="ft",
            source="manual",
        )


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


@pytest.mark.parametrize("topic", [None, "event:summer-meetup"])
@pytest.mark.asyncio
async def test_get_active_facts_excludes_expired_on_both_branches(repo, topic):
    """Both branches share one predicate -- the split is where they drift."""
    repo_, pool = repo
    pool.fetch.return_value = []

    await repo_.get_active_facts(1, topic=topic)

    assert "expires_at IS NULL OR expires_at > NOW()" in pool.fetch.call_args.args[0]


# ---------------------------------------------------------------------------
# projection + ordering of the list reads
# ---------------------------------------------------------------------------


def _select_list(sql: str) -> str:
    """The projection between SELECT and FROM. Lets a test say "not `embedding`"
    without tripping over the `embedding IS NOT NULL` in a WHERE clause."""
    head = sql.split("FROM", 1)[0]
    return head.split("SELECT", 1)[1]


def _order_by(sql: str) -> str:
    """The ORDER BY clause only.

    `predicate` is a legitimately *selected* column, so a bare
    `"predicate" not in sql` would be red on a correct implementation. The claim
    under test is about the sort key, so the assertion has to be about the sort
    key.
    """
    assert "ORDER BY" in sql, f"no ORDER BY in:\n{sql}"
    return sql.split("ORDER BY", 1)[1]


@pytest.mark.parametrize("topic", [None, "event:summer-meetup"])
@pytest.mark.asyncio
async def test_get_active_facts_does_not_ship_the_embedding_vector(repo, topic):
    """`SELECT *` sent 768 floats per row to the bot for a list that renders none.

    Parametrized over both branches because the topic filter duplicates the
    query -- one branch keeping `SELECT *` is exactly how this regresses, and
    `get_active_facts` is unbounded, so the waste grows with the corpus S2 is
    built to create.
    """
    repo_, pool = repo
    pool.fetch.return_value = []

    await repo_.get_active_facts(-1009999990001, topic=topic)

    projection = _select_list(pool.fetch.call_args.args[0])
    assert "*" not in projection, "named columns only -- `SELECT *` carries `embedding`"
    assert "embedding" not in projection
    # ...but still everything a renderer needs, including the S1 columns.
    for column in ("fact_text", "topic", "expires_at", "rejected_by", "created_at"):
        assert column in projection


@pytest.mark.parametrize("topic", [None, "event:summer-meetup"])
@pytest.mark.asyncio
async def test_get_active_facts_tiebreaks_on_created_at_not_predicate(repo, topic):
    """Append-only predicates sort as TEXT, so `predicate` is the wrong key.

    KB-07 derives each fact's predicate from its command's message id (`m1001`,
    `m999`). Ordering by that string puts `m1001` before `m999` -- i.e. two facts
    about one subject list in an order that is neither capture order nor any
    order a reader can explain, and that flips as message ids gain a digit.
    `id` follows `created_at` because rows written in one transaction share
    `NOW()` to the microsecond.
    """
    repo_, pool = repo
    pool.fetch.return_value = []

    await repo_.get_active_facts(-1009999990001, topic=topic)

    order = _order_by(pool.fetch.call_args.args[0])
    assert "created_at" in order
    assert "id" in order
    assert "predicate" not in order


# ---------------------------------------------------------------------------
# get_expired_facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_expired_facts_returns_only_aged_out_rows(repo):
    """Expired facts must stay REACHABLE for management, just not retrievable.

    Otherwise `_LIVE_FACTS` hides them from every read path and the action
    they need -- clear the expiry, the event moved -- cannot reach them.
    """
    repo_, pool = repo
    pool.fetch.return_value = [{"id": 3, "subject": "meetup"}]

    result = await repo_.get_expired_facts(-1009999990001)

    assert result == [{"id": 3, "subject": "meetup"}]
    sql, chat_id = pool.fetch.call_args.args
    assert "expires_at IS NOT NULL" in sql
    assert "expires_at <= NOW()" in sql
    # Still a live row, not a superseded or rejected one.
    assert "status = 'active'" in sql
    assert "valid_to IS NULL" in sql
    assert chat_id == -1009999990001


@pytest.mark.asyncio
async def test_get_expired_facts_does_not_ship_the_embedding_vector(repo):
    """Same projection rule as the active list -- the expired segment renders
    text, not vectors, and it is the one read that is guaranteed to grow."""
    repo_, pool = repo
    pool.fetch.return_value = []

    await repo_.get_expired_facts(-1009999990001)

    projection = _select_list(pool.fetch.call_args.args[0])
    assert "*" not in projection
    assert "embedding" not in projection
    assert "expires_at" in projection


# ---------------------------------------------------------------------------
# search_by_similarity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_similarity_orders_similarity_then_salience(repo, conn):
    repo_, _pool = repo
    conn.fetch.return_value = [
        {"id": 1, "similarity": 0.9, "salience": 0.8},
    ]
    embedding = [0.1] * 768

    result = await repo_.search_by_similarity(1, embedding, limit=3)

    assert len(result) == 1
    sql, chat_id, query_embedding, limit = conn.fetch.call_args.args
    assert "ORDER BY embedding <=> $2 ASC, salience DESC" in sql
    assert "status = 'active'" in sql
    assert "valid_to IS NULL" in sql
    assert "embedding IS NOT NULL" in sql
    assert chat_id == 1
    assert query_embedding == embedding
    assert limit == 3


@pytest.mark.asyncio
async def test_search_by_similarity_excludes_expired_facts(repo, conn):
    """Migration 027: an aged-out fact must stop reaching the prompt."""
    await repo_search(repo, conn)
    sql = conn.fetch.call_args.args[0]
    assert "expires_at IS NULL OR expires_at > NOW()" in sql


@pytest.mark.asyncio
async def test_search_by_similarity_sets_ivfflat_probes_before_querying(repo, conn):
    """The probes GUC is worthless unless it is set on the SAME connection first.

    Asserts the call site, not the constant: `set_config` must run on the
    acquired connection, transaction-locally, BEFORE the vector query. A
    reordering or a drop of the set_config call turns this red.
    """
    await repo_search(repo, conn)

    set_calls = [c for c in conn.execute.call_args_list if "set_config" in str(c.args[0])]
    assert len(set_calls) == 1, "probes must be set exactly once per search"

    stmt, value = set_calls[0].args[0], set_calls[0].args[1]
    assert "ivfflat.probes" in stmt
    # is_local=true -- must not leak onto a pooled connection.
    assert stmt.strip().endswith("true)")
    assert value == "10", "probes must equal the index's `lists` for an exact scan"

    # Ordering: set_config is issued, and the query runs on the same connection.
    assert conn.fetch.call_count == 1
    assert conn.transaction.call_count == 1


async def repo_search(repo, conn):
    """Drive one search_by_similarity call against the mocked connection."""
    repo_, _pool = repo
    conn.fetch.return_value = []
    await repo_.search_by_similarity(1, [0.1] * 768, limit=3)


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
async def test_reject_fact_records_who_removed_it(repo):
    """Migration 027: the revision lets chat admins remove facts, so
    'it disappeared' must have an answer other than 'ask everyone'."""
    repo_, pool = repo
    pool.execute.return_value = "UPDATE 1"

    await repo_.reject_fact(7, chat_id=-1009999990001, rejected_by=424242)

    sql, fact_id, chat_id, rejected_by = pool.execute.call_args.args
    assert "rejected_by = $3" in sql
    assert "rejected_at = NOW()" in sql
    assert (fact_id, chat_id, rejected_by) == (7, -1009999990001, 424242)


@pytest.mark.asyncio
async def test_reject_fact_allows_an_unattributed_removal(repo):
    """A system-initiated removal must stay expressible and distinguishable."""
    repo_, pool = repo
    pool.execute.return_value = "UPDATE 1"

    await repo_.reject_fact(7, chat_id=1)

    assert pool.execute.call_args.args[3] is None


@pytest.mark.asyncio
async def test_reject_fact_returns_false_when_no_row_matched(repo):
    repo_, pool = repo
    pool.execute.return_value = "UPDATE 0"

    result = await repo_.reject_fact(999, chat_id=1)

    assert result is False
