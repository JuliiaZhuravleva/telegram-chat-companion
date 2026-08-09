"""
Integration test: MemoryRepository chat-scoping privacy invariant (S2-7a).

``docs/plans/rag-s2-hygiene.md`` §S2-7 names this the "cross-chat retrieval —
never" rule from the roadmap's §6 — the one rule marked "never" — and notes
it is held up by a single line of SQL (``WHERE chat_id = $1`` in
``MemoryRepository.search()``, ``src/database/repositories/memory.py``) that
no test was watching.

The item's own acceptance text is explicit that a positive assertion alone
does not satisfy it:

    "Тест chat-scoping должен быть проверен на падение при снятом
    chat_id-фильтре — иначе он ничего не доказывает."
    (the test must be checked to fail with the chat_id filter removed, or it
    proves nothing)

``TestChatScoping.test_search_never_returns_another_chats_memory`` is the
positive assertion. ``TestChatScoping.test_query_without_chat_filter_leaks_across_chats``
is the mandatory negative control: it runs the *same* SQL as
``MemoryRepository.search()`` against the *same* fixture rows with only the
``chat_id = $1`` predicate removed, and asserts that this DOES return rows
from both chats. That proves two things a passing positive test alone
cannot: (1) the fixture's two memories actually collide under the
similarity math used (same one-hot embedding, so cosine similarity is
exactly 1.0 for both), so a missing filter would be caught, not silently
tolerated by chance; and (2) `chat_memory` actually has rows to leak in the
first place. Whoever edits ``search()`` next and drops the predicate will
see this file fail loudly, on the query, without needing to hand-edit
`src/` to rediscover that the control is load-bearing.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.memory import MemoryRepository

_EMBED_DIM = 768

CHAT_A = -930001
CHAT_B = -930002


def _one_hot(index: int, *, dim: int = _EMBED_DIM) -> list[float]:
    """A deterministic unit vector with a single 1.0 at `index`.

    Cosine similarity between two one-hot vectors is 1.0 if same index, 0.0
    if different — gives fully deterministic, discrete control over pgvector
    ranking without depending on real embedding semantics. Mirrors the
    helper in ``tests/integration/test_knowledge_repository.py``.
    """
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


@pytest_asyncio.fixture
async def repo(db_pool: asyncpg.Pool) -> MemoryRepository:
    return MemoryRepository(db_pool)


@pytest_asyncio.fixture
async def two_chat_memories(db_pool: asyncpg.Pool, repo: MemoryRepository) -> tuple[int, int]:
    """One memory in each of two chats, with the SAME embedding.

    This is the case most likely to leak: if the two chats' memories were
    given different embeddings that just happened not to collide, a passing
    "no leak" assertion would be indistinguishable from a broken filter that
    was never exercised. An identical one-hot vector in both chats forces
    cosine similarity to 1.0 either way, so chat B's row is guaranteed to
    clear any similarity threshold for a chat A query -- the only thing that
    can still keep it out is the ``chat_id`` predicate itself.
    """
    await db_pool.execute(
        "DELETE FROM chat_memory WHERE chat_id = ANY($1::bigint[])", [CHAT_A, CHAT_B]
    )
    id_a = await repo.store(CHAT_A, "secret plan for chat A", _one_hot(0))
    id_b = await repo.store(CHAT_B, "secret plan for chat B", _one_hot(0))
    return id_a, id_b


class TestChatScoping:
    @pytest.mark.asyncio
    async def test_search_never_returns_another_chats_memory(
        self, repo: MemoryRepository, two_chat_memories: tuple[int, int]
    ) -> None:
        """Two memories, same embedding, two different chats; querying from
        chat A must return chat A's memory only -- chat B's must never
        appear, no matter how high its similarity score is."""
        results = await repo.search(
            CHAT_A,
            _one_hot(0),
            min_similarity=0.99,
            max_results=10,
        )

        contents = [row["content"] for row in results]
        assert contents == ["secret plan for chat A"]
        assert "secret plan for chat B" not in contents

    @pytest.mark.asyncio
    async def test_query_without_chat_filter_leaks_across_chats(
        self, db_pool: asyncpg.Pool, two_chat_memories: tuple[int, int]
    ) -> None:
        """Mandatory negative control (S2-7a acceptance text in
        docs/plans/rag-s2-hygiene.md): the test above only proves something
        if the same fixture data WOULD leak once the ``chat_id`` predicate
        is removed.

        Deliberately duplicates ``MemoryRepository.search()``'s SQL here
        (not by importing/patching the repository -- there is nothing to
        monkeypatch, the predicate is inline in the query string) with only
        ``AND chat_id = $1`` deleted. If this ever stopped returning both
        chats, the positive test above would no longer be trustworthy and
        this file — not a future privacy incident — is where that should
        surface.
        """
        rows = await db_pool.fetch(
            """
            SELECT chat_id, content
            FROM chat_memory
            WHERE embedding IS NOT NULL
              AND 1 - (embedding <=> $1) >= $2
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY embedding <=> $1 ASC
            """,
            _one_hot(0),
            0.99,
        )

        leaked_chat_ids = {row["chat_id"] for row in rows}
        assert {CHAT_A, CHAT_B} <= leaked_chat_ids, (
            "negative control did not reproduce a cross-chat leak with the "
            "chat_id predicate removed -- the positive test above would be "
            "vacuous (fixture data doesn't actually collide)"
        )
