"""The chunk index against a real PostgreSQL (S4, migration 029).

Everything here is invisible to the unit suite by construction: the natural
key's `NULLS NOT DISTINCT`, the generated tsvector, `IS NOT DISTINCT FROM` on
a NULL thread, and the watermark arithmetic are all properties of the SQL, and
a mocked repository would agree with any of them.

The two that would be silently wrong without a real database:

* a natural key **without** `NULLS NOT DISTINCT` accepts unlimited duplicates
  for every non-forum chat, because `NULL <> NULL`. The index would grow on
  every pass and retrieval would return the same conversation five times.
* `message_thread_id = $1` with `$1 = NULL` matches nothing, so the indexer
  would quietly index zero messages for exactly the chats that have the most.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.config import ChunkIndexerSettings
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_migration import ChatMigrationRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.chunks import ChunkRepository
from src.database.repositories.messages import MessageRepository
from src.services.ai.base import AIProviderError
from src.services.chat_config import ChatConfigService
from src.services.rag.chunker import build_chunks, source_messages
from src.services.rag.indexer import INDEX_TASK_TYPE, ChatChunkIndexer
from src.services.rag.models import Chunk

CHAT_ID = -100999000222
OTHER_CHAT = -100999000333
NOW = datetime.now(UTC)


@dataclass
class _Embedding:
    embedding: list[float]
    model: str = "gemini-embedding-001"
    provider: str = "gemini"
    dimensions: int = 768
    tokens_input: int = 0


class _FakeRouter:
    """Stands in for AIRouter with a real signature, not a mock.

    A mock would accept `task_type=` whether or not the caller passes it, and
    the whole point of S4's embedding call is that it does.
    """

    def __init__(
        self,
        *,
        dimensions: int = 768,
        fail: bool = False,
        fail_marker: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._dimensions = dimensions
        self._fail = fail
        self._fail_marker = fail_marker

    async def generate_embedding(
        self, text: str, *, chat_id: int = 0, **kwargs: str | int
    ) -> _Embedding:
        task_type = kwargs.get("task_type")
        self.calls.append((text, str(task_type) if task_type is not None else None))
        if self._fail or (self._fail_marker is not None and self._fail_marker in text):
            raise AIProviderError("provider down", provider="gemini")
        return _Embedding(embedding=[0.01] * self._dimensions)


@pytest.fixture
def chunks(db_pool: asyncpg.Pool) -> ChunkRepository:
    return ChunkRepository(db_pool)


@pytest.fixture
def messages(db_pool: asyncpg.Pool) -> MessageRepository:
    return MessageRepository(db_pool)


@pytest.fixture(autouse=True)
async def _clean(db_pool: asyncpg.Pool):
    async def wipe() -> None:
        for chat in (CHAT_ID, OTHER_CHAT):
            await db_pool.execute("DELETE FROM chat_chunks WHERE chat_id = $1", chat)
            await db_pool.execute("DELETE FROM chat_messages WHERE chat_id = $1", chat)
            await db_pool.execute("DELETE FROM chat_settings WHERE chat_id = $1", chat)
        # `db_pool` is not the rolled-back `db_conn` fixture -- writes here are
        # real and outlive the test. Migration 001 seeds this global default as
        # true and the gate test below flips it, which without this line
        # followed the rest of the session and turned indexing off for every
        # later test in whatever order pytest picked.
        await db_pool.execute(
            "UPDATE bot_config SET value = 'true'::jsonb WHERE key = 'default_save_messages'"
        )

    await wipe()
    yield
    await wipe()


def _chunk(**overrides: object) -> Chunk:
    base = {
        "chat_id": CHAT_ID,
        "thread_id": None,
        "msg_from": 10,
        "msg_to": 20,
        "part": 0,
        "content": "Чат «Тест», 18 августа 2026\nАня (12:00): ёлка на площади",
        "senders": (501,),
        "msg_count": 3,
        "started_at": NOW - timedelta(days=1),
        "ended_at": NOW - timedelta(days=1) + timedelta(minutes=5),
    }
    base.update(overrides)
    return Chunk(**base)  # type: ignore[arg-type]


class TestNaturalKey:
    async def test_a_null_thread_still_deduplicates(self, chunks: ChunkRepository) -> None:
        """`NULL <> NULL`, so a plain UNIQUE would let this through -- and
        every non-forum chat has a NULL thread."""
        assert await chunks.insert_many([_chunk()]) == 1

        assert await chunks.insert_many([_chunk(content="переписанный текст")]) == 0

    async def test_a_different_range_is_a_different_chunk(self, chunks: ChunkRepository) -> None:
        await chunks.insert_many([_chunk()])

        assert await chunks.insert_many([_chunk(msg_from=20, msg_to=31)]) == 1

    async def test_the_same_range_in_another_thread_is_a_different_chunk(
        self, chunks: ChunkRepository
    ) -> None:
        """Nothing writes a non-NULL `thread_id` today -- chunks are
        chat-wide. The key still has to distinguish them, because that column
        is what forum-aware chunking would use, and a natural key that only
        works for the current writer is not a key."""
        await chunks.insert_many([_chunk()])

        assert await chunks.insert_many([_chunk(thread_id=77)]) == 1

    async def test_parts_of_one_range_coexist(self, chunks: ChunkRepository) -> None:
        await chunks.insert_many([_chunk()])

        assert await chunks.insert_many([_chunk(part=1)]) == 1


class TestGeneratedTsvector:
    async def test_yo_is_normalised_at_index_time(
        self, chunks: ChunkRepository, db_pool: asyncpg.Pool
    ) -> None:
        await chunks.insert_many([_chunk()])

        found = await db_pool.fetchval(
            """
            SELECT count(*) FROM chat_chunks
            WHERE chat_id = $1 AND tsv @@ to_tsquery('russian', translate($2, 'ёЁ', 'еЕ'))
            """,
            CHAT_ID,
            "елка",
        )

        assert found == 1

    async def test_the_tsvector_follows_the_content(
        self, chunks: ChunkRepository, db_pool: asyncpg.Pool
    ) -> None:
        # Generated, not application-maintained: the FTS leg cannot drift from
        # the text it indexes even if a future writer forgets it exists.
        await chunks.insert_many([_chunk(content="Аня (12:00): собираемся у почты")])
        await db_pool.execute(
            "UPDATE chat_chunks SET content = 'Аня (12:00): встречаемся на вокзале' "
            "WHERE chat_id = $1",
            CHAT_ID,
        )

        vector = await db_pool.fetchval(
            "SELECT tsv::text FROM chat_chunks WHERE chat_id = $1", CHAT_ID
        )

        assert "вокзал" in vector
        assert "почт" not in vector


class TestWatermark:
    async def test_the_newest_indexed_message(self, chunks: ChunkRepository) -> None:
        await chunks.insert_many(
            [
                _chunk(msg_from=10, msg_to=20),
                _chunk(msg_from=100, msg_to=300),
                _chunk(msg_from=40, msg_to=60),
            ]
        )

        assert await chunks.watermark(CHAT_ID) == 300

    async def test_an_empty_index_starts_at_zero(self, chunks: ChunkRepository) -> None:
        assert await chunks.watermark(CHAT_ID) == 0

    async def test_another_chat_does_not_move_it(self, chunks: ChunkRepository) -> None:
        await chunks.insert_many([_chunk(chat_id=OTHER_CHAT, msg_from=900, msg_to=999)])

        assert await chunks.watermark(CHAT_ID) == 0


class TestPendingEmbeddings:
    async def test_pending_then_filled(
        self, chunks: ChunkRepository, db_pool: asyncpg.Pool
    ) -> None:
        await chunks.insert_many([_chunk()])
        pending = await chunks.get_pending_embeddings(10)
        assert len(pending) == 1

        await chunks.update_embedding(
            int(pending[0]["id"]),
            [0.02] * 768,
            model="gemini-embedding-001",
            task_type=INDEX_TASK_TYPE,
        )

        assert await chunks.get_pending_embeddings(10) == []
        row = await db_pool.fetchrow(
            "SELECT emb_model, emb_task_type FROM chat_chunks WHERE chat_id = $1", CHAT_ID
        )
        assert row["emb_model"] == "gemini-embedding-001"
        assert row["emb_task_type"] == INDEX_TASK_TYPE

    async def test_parked_ids_are_excluded(self, chunks: ChunkRepository) -> None:
        await chunks.insert_many([_chunk()])
        pending = await chunks.get_pending_embeddings(10)

        assert await chunks.get_pending_embeddings(10, exclude_ids=[int(pending[0]["id"])]) == []

    async def test_counts(self, chunks: ChunkRepository) -> None:
        await chunks.insert_many([_chunk(), _chunk(msg_from=30, msg_to=40)])

        assert await chunks.counts(CHAT_ID) == {"total": 2, "pending": 2}


class TestSourceQuery:
    async def _seed(self, messages: MessageRepository) -> None:
        await messages.save(
            CHAT_ID, 101, "text", user_id=501, first_name="Аня", content="первое сообщение"
        )
        await messages.save(
            CHAT_ID, 102, "text", user_id=502, first_name="Борис", content="второе сообщение"
        )
        await messages.save(CHAT_ID, 103, "text", content="ответ бота", is_bot_message=True)
        await messages.save(
            CHAT_ID, 104, "transcription", user_id=None, content=None, is_bot_message=True
        )

    async def test_returns_the_whole_chat(self, messages: MessageRepository) -> None:
        await self._seed(messages)

        rows = await messages.get_for_chunking(CHAT_ID, after_message_id=0, limit=100)

        assert [row["message_id"] for row in rows] == [101, 102, 103]

    async def test_no_row_python_calls_empty_survives_the_sql_filter(
        self, messages: MessageRepository
    ) -> None:
        """SQL and `source_messages` must agree on "empty", character for character.

        The cases come from Python's own definition -- every code point whose
        `.strip()` is empty -- rather than from a list matching the predicate,
        so the test cannot be a mirror of the implementation. This is the
        starvation `get_for_chunking`'s docstring promises to prevent: `LIMIT`
        counts rows, not usable rows, so a batch of rows the chunker will drop
        yields no chunk, leaves the watermark unmoved and is re-read for ever.
        `btrim(content)` trimmed only U+0020 and let every one of these through.
        """
        blanks = [chr(code) for code in range(0x3001) if chr(code).strip() == ""]
        assert "\u00a0" in blanks and "\n" in blanks  # the fixture reaches the gap

        for offset, blank in enumerate(blanks):
            await messages.save(CHAT_ID, 200 + offset, "text", user_id=501, content=blank)
        await messages.save(CHAT_ID, 900, "text", user_id=501, content="настоящее сообщение")

        rows = await messages.get_for_chunking(CHAT_ID, after_message_id=0, limit=1000)

        assert [row["message_id"] for row in rows] == [900]

    async def test_the_watermark_excludes_what_is_already_indexed(
        self, messages: MessageRepository
    ) -> None:
        await self._seed(messages)

        rows = await messages.get_for_chunking(CHAT_ID, after_message_id=102, limit=100)

        assert [row["message_id"] for row in rows] == [103]

    async def test_a_reply_chain_does_not_split_the_conversation(
        self, messages: MessageRepository, db_pool: asyncpg.Pool
    ) -> None:
        """`message_thread_id` marks reply chains, not forum topics (measured
        on production 2026-08-19: 2.0-2.7 messages per value, ~70% NULL). A
        reply carries the id and the message it answers does not, so filtering
        by it would put the two halves of one exchange in different chunks."""
        await self._seed(messages)
        await db_pool.execute(
            "UPDATE chat_messages SET message_thread_id = 101 "
            "WHERE chat_id = $1 AND message_id = $2",
            CHAT_ID,
            102,
        )

        rows = await messages.get_for_chunking(CHAT_ID, after_message_id=0, limit=100)

        assert [row["message_id"] for row in rows] == [101, 102, 103]

    async def test_rows_the_chunker_cannot_use_are_dropped(
        self, messages: MessageRepository, db_pool: asyncpg.Pool
    ) -> None:
        await self._seed(messages)
        await db_pool.execute(
            "UPDATE chat_messages SET created_at = NULL WHERE chat_id = $1 AND message_id = $2",
            CHAT_ID,
            101,
        )

        rows = await messages.get_for_chunking(CHAT_ID, after_message_id=0, limit=100)

        assert [row["message_id"] for row in rows] == [102, 103]

    async def test_contentless_rows_never_occupy_the_batch(
        self, messages: MessageRepository, db_pool: asyncpg.Pool
    ) -> None:
        """`LIMIT` counts rows, not usable ones. A run of stickers longer than
        one batch would otherwise fill the window, produce no chunk, leave the
        watermark unmoved, and hide every real message behind it -- for ever,
        on every pass."""
        for message_id in (301, 302, 303):
            await messages.save(
                CHAT_ID, message_id, "sticker", user_id=501, first_name="Аня", content=None
            )
        await messages.save(CHAT_ID, 304, "text", user_id=501, first_name="Аня", content="   ")
        await messages.save(
            CHAT_ID, 305, "text", user_id=501, first_name="Аня", content="настоящая реплика"
        )

        rows = await messages.get_for_chunking(CHAT_ID, after_message_id=0, limit=2)

        assert [row["message_id"] for row in rows] == [305]

    async def test_the_rows_feed_the_chunker_unchanged(self, messages: MessageRepository) -> None:
        """The column list the query selects and the keys `source_messages`
        reads are two halves of one contract, and nothing else checks them
        against each other."""
        await self._seed(messages)
        rows = await messages.get_for_chunking(CHAT_ID, after_message_id=0, limit=100)

        built = build_chunks(
            source_messages(rows), chat_id=CHAT_ID, thread_id=None, chat_title="Тест"
        )

        assert len(built) == 1
        assert "Аня" in built[0].content
        assert "Bot" in built[0].content
        assert built[0].senders == (501, 502)


class TestIndexerEndToEnd:
    """The whole pass, against real SQL and a real ChatConfigService.

    The config service is the real one on purpose: `save_messages` is resolved
    through the three-layer merge, and a stub would assert that the indexer
    calls *something*, not that the gate the owner actually toggles works.
    """

    def _indexer(
        self, db_pool: asyncpg.Pool, router: _FakeRouter, **overrides: int | bool
    ) -> ChatChunkIndexer:
        from src.config import BotSettings

        settings = ChunkIndexerSettings(**overrides)  # type: ignore[arg-type]
        return ChatChunkIndexer(
            pool=db_pool,
            ai_router=router,  # type: ignore[arg-type]
            chat_config=ChatConfigService(
                BotSettings(),
                BotConfigRepository(db_pool),
                ChatSettingsRepository(db_pool),
            ),
            config=settings,
        )

    async def _seed_closed_conversation(
        self, messages: MessageRepository, db_pool: asyncpg.Pool, *, chat_id: int = CHAT_ID
    ) -> None:
        await db_pool.execute(
            "INSERT INTO chat_settings (chat_id, chat_title, enabled) VALUES ($1, $2, true)",
            chat_id,
            "Тестовая беседа",
        )
        started = NOW - timedelta(days=2)
        for index in range(6):
            await messages.save(
                chat_id,
                200 + index,
                "text",
                user_id=501 + (index % 2),
                first_name="Аня" if index % 2 == 0 else "Борис",
                content=f"реплика номер {index} про субботний поход на озеро",
            )
        await db_pool.execute(
            "UPDATE chat_messages SET created_at = "
            "$2::timestamptz + (message_id - 200) * interval '1 minute' "
            "WHERE chat_id = $1",
            chat_id,
            started,
        )

    async def test_a_closed_conversation_is_chunked_and_embedded(
        self, db_pool: asyncpg.Pool, messages: MessageRepository, chunks: ChunkRepository
    ) -> None:
        await self._seed_closed_conversation(messages, db_pool)
        router = _FakeRouter()

        result = await self._indexer(db_pool, router).run_once()

        assert result["chunks"] == 1
        assert result["embedded"] == 1
        assert await chunks.counts(CHAT_ID) == {"total": 1, "pending": 0}
        assert router.calls[0][1] == INDEX_TASK_TYPE, "chunks must be indexed as documents"

    async def test_a_second_pass_writes_nothing(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ) -> None:
        await self._seed_closed_conversation(messages, db_pool)
        indexer = self._indexer(db_pool, _FakeRouter())
        await indexer.run_once()

        assert await indexer.run_once() == {"chats": 1, "chunks": 0, "embedded": 0}

    async def test_an_open_conversation_waits(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ) -> None:
        """The debounce: a session that can still grow must not be chunked,
        or its `(msg_from, msg_to)` changes under the natural key."""
        await self._seed_closed_conversation(messages, db_pool)
        await db_pool.execute(
            "UPDATE chat_messages SET created_at = $2 WHERE chat_id = $1",
            CHAT_ID,
            NOW - timedelta(minutes=2),
        )

        assert (await self._indexer(db_pool, _FakeRouter()).run_once())["chunks"] == 0

    async def test_save_messages_off_means_no_indexing(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ) -> None:
        await self._seed_closed_conversation(messages, db_pool)
        await db_pool.execute(
            "UPDATE chat_settings SET save_messages = false WHERE chat_id = $1", CHAT_ID
        )

        result = await self._indexer(db_pool, _FakeRouter()).run_once()

        assert result == {"chats": 0, "chunks": 0, "embedded": 0}

    async def test_a_migrated_chats_tail_is_still_indexed(
        self, db_pool: asyncpg.Pool, messages: MessageRepository, chunks: ChunkRepository
    ) -> None:
        """TD-104: a group becoming a supergroup must not orphan its history.

        Driven through the real `ChatMigrationRepository.migrate()` rather
        than a hand-written UPDATE. The defect was two correct pieces
        disagreeing about where a chat lives, and a fixture that re-keys the
        row its own way can easily move it somewhere production never does --
        proving something about the fixture instead of about the migration.
        """
        await self._seed_closed_conversation(messages, db_pool)
        outcome = await ChatMigrationRepository(db_pool).migrate(CHAT_ID, OTHER_CHAT)

        assert outcome.status == "migrated", "fixture precondition: the settings row moved"
        assert (
            await db_pool.fetchval("SELECT count(*) FROM chat_messages WHERE chat_id = $1", CHAT_ID)
            == 6
        ), "the migration deliberately leaves messages behind -- that is the whole premise"

        result = await self._indexer(db_pool, _FakeRouter()).run_once()

        assert result["chunks"] == 1
        assert await chunks.counts(CHAT_ID) == {"total": 1, "pending": 0}

    async def test_a_chat_with_no_settings_row_still_obeys_the_global_gate(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ) -> None:
        """Enumerating from the message table must not become a way around
        `save_messages`.

        A chat with no row of its own is answered by the global layer, and
        that answer has to be honoured -- otherwise the fix for TD-104 would
        have quietly made the orphaned chats the only ones the owner cannot
        switch off.
        """
        await self._seed_closed_conversation(messages, db_pool)
        await db_pool.execute("DELETE FROM chat_settings WHERE chat_id = $1", CHAT_ID)
        await BotConfigRepository(db_pool).set("default_save_messages", False)

        assert await self._indexer(db_pool, _FakeRouter()).run_once() == {
            "chats": 0,
            "chunks": 0,
            "embedded": 0,
        }

    async def test_a_chat_the_settings_table_never_heard_of_is_indexed(
        self, db_pool: asyncpg.Pool, messages: MessageRepository, chunks: ChunkRepository
    ) -> None:
        """The positive half of the pair above, and it also exercises the
        title fallback: with no settings row there is no `chat_title`, and the
        header has to render without one rather than raise."""
        await self._seed_closed_conversation(messages, db_pool)
        await db_pool.execute("DELETE FROM chat_settings WHERE chat_id = $1", CHAT_ID)

        assert (await self._indexer(db_pool, _FakeRouter()).run_once())["chunks"] == 1
        assert await chunks.counts(CHAT_ID) == {"total": 1, "pending": 0}
        content = await db_pool.fetchval(
            "SELECT content FROM chat_chunks WHERE chat_id = $1", CHAT_ID
        )
        assert "реплика номер 0" in content
        # Assert the HEADER, not merely that nothing raised. `_render_header`
        # takes the no-title branch here, and a version that interpolated the
        # raw value would write `Чат «None», …` into every chunk of every
        # settings-less chat -- embedded and FTS-indexed -- while a body-only
        # assertion passed just the same.
        header = content.splitlines()[0]
        assert header.startswith("Чат, "), header
        assert "None" not in header, header
        assert "«" not in header, header

    async def test_an_embedding_outage_leaves_the_chunk_pending(
        self, db_pool: asyncpg.Pool, messages: MessageRepository, chunks: ChunkRepository
    ) -> None:
        """The row must survive the outage: content first, vector later. A
        chunk dropped because the provider blinked is unrecoverable -- the
        watermark has already moved past its messages."""
        await self._seed_closed_conversation(messages, db_pool)

        result = await self._indexer(db_pool, _FakeRouter(fail=True)).run_once()

        assert result["chunks"] == 1
        assert result["embedded"] == 0
        assert await chunks.counts(CHAT_ID) == {"total": 1, "pending": 1}

    async def test_a_wrong_width_vector_is_never_stored(
        self, db_pool: asyncpg.Pool, messages: MessageRepository, chunks: ChunkRepository
    ) -> None:
        await self._seed_closed_conversation(messages, db_pool)

        result = await self._indexer(db_pool, _FakeRouter(dimensions=1536)).run_once()

        assert result["embedded"] == 0
        assert await chunks.counts(CHAT_ID) == {"total": 1, "pending": 1}

    async def test_a_permanently_failing_chunk_is_parked(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ) -> None:
        await self._seed_closed_conversation(messages, db_pool)
        indexer = self._indexer(db_pool, _FakeRouter(dimensions=1536))

        for _ in range(3):
            await indexer.run_once()
        router = _FakeRouter(dimensions=1536)
        indexer._ai_router = router  # type: ignore[assignment]
        await indexer.run_once()

        assert router.calls == [], "a parked chunk must stop being retried"

    async def test_each_chat_is_indexed_under_its_own_id(
        self, db_pool: asyncpg.Pool, messages: MessageRepository, chunks: ChunkRepository
    ) -> None:
        """The privacy invariant, at write time: a chunk belongs to exactly
        one chat and nothing may merge two chats' messages into one."""
        await self._seed_closed_conversation(messages, db_pool)
        await self._seed_closed_conversation(messages, db_pool, chat_id=OTHER_CHAT)

        await self._indexer(db_pool, _FakeRouter()).run_once()

        assert await chunks.counts(CHAT_ID) == {"total": 1, "pending": 0}
        assert await chunks.counts(OTHER_CHAT) == {"total": 1, "pending": 0}
        rows = await db_pool.fetch(
            "SELECT chat_id, content FROM chat_chunks WHERE chat_id = ANY($1::bigint[])",
            [CHAT_ID, OTHER_CHAT],
        )
        assert len(rows) == 2


class TestNothingIsSkippedAcrossPasses:
    """The regression that measurement found, and only measurement could.

    The watermark is a `message_id`. While the fetch was ordered by
    `created_at`, a batch spanned a *wider* id range than it contained, and
    every id inside that range but outside the batch was excluded for ever by
    `message_id > watermark`. On production this was 792 messages in the
    largest chat and 352 in the second -- a fourteenth of the conversation,
    missing from the index with nothing anywhere reporting it.

    Unit tests cannot see it: it lives in the interaction between the query's
    ORDER BY, the LIMIT, and a watermark derived from what was written.
    """

    def _indexer(self, db_pool: asyncpg.Pool, batch: int) -> ChatChunkIndexer:
        from src.config import BotSettings

        return ChatChunkIndexer(
            pool=db_pool,
            ai_router=_FakeRouter(),  # type: ignore[arg-type]
            chat_config=ChatConfigService(
                BotSettings(),
                BotConfigRepository(db_pool),
                ChatSettingsRepository(db_pool),
            ),
            config=ChunkIndexerSettings(messages_per_pass=batch, embed_per_pass=100),
        )

    async def test_shuffled_timestamps_lose_nothing(
        self, db_pool: asyncpg.Pool, messages: MessageRepository
    ) -> None:
        await db_pool.execute(
            "INSERT INTO chat_settings (chat_id, chat_title, enabled) VALUES ($1, $2, true)",
            CHAT_ID,
            "Тестовая беседа",
        )
        started = NOW - timedelta(days=30)
        for index in range(40):
            await messages.save(
                CHAT_ID,
                400 + index,
                "text",
                user_id=501,
                first_name="Аня",
                content=f"метка-{index} обычная реплика про поход",
            )
        # Timestamps that do NOT follow message_id: ids 400..419 are stamped
        # after ids 420..439, exactly the shape the n8n import left behind.
        await db_pool.execute(
            """
            UPDATE chat_messages
            SET created_at = $2::timestamptz
                + CASE WHEN message_id < 420 THEN 20 ELSE 0 END * interval '1 minute'
                + (message_id - 400) * interval '1 second'
            WHERE chat_id = $1
            """,
            CHAT_ID,
            started,
        )

        indexer = self._indexer(db_pool, batch=10)
        for _ in range(8):
            await indexer.run_once()

        rendered = "\n".join(
            row["content"]
            for row in await db_pool.fetch(
                "SELECT content FROM chat_chunks WHERE chat_id = $1", CHAT_ID
            )
        )
        missing = [index for index in range(40) if f"метка-{index} " not in rendered]

        assert not missing, f"messages missing from the index: {missing}"


class TestParking:
    """Parking must separate "this row is bad" from "the provider is down".

    Counting an outage toward the per-row limit parks the whole backlog after
    three passes, and parked rows are only retried on a process restart -- so
    a five-minute provider blip would stop the index filling until someone
    noticed, which is exactly the kind of failure nobody notices.
    """

    def _indexer(self, db_pool: asyncpg.Pool, router: _FakeRouter) -> ChatChunkIndexer:
        from src.config import BotSettings

        return ChatChunkIndexer(
            pool=db_pool,
            ai_router=router,  # type: ignore[arg-type]
            chat_config=ChatConfigService(
                BotSettings(),
                BotConfigRepository(db_pool),
                ChatSettingsRepository(db_pool),
            ),
            config=ChunkIndexerSettings(),
        )

    async def _seed(self, chunks: ChunkRepository, count: int, *, bad_index: int | None) -> None:
        rows = []
        for index in range(count):
            marker = "НЕПЕРЕВАРИВАЕМОЕ " if index == bad_index else ""
            rows.append(
                _chunk(
                    msg_from=100 + index * 10,
                    msg_to=105 + index * 10,
                    content=f"Чат «Тест», 18 августа 2026\nАня (12:0{index}): {marker}реплика",
                )
            )
        await chunks.insert_many(rows)

    async def test_an_outage_does_not_park_the_backlog(
        self, db_pool: asyncpg.Pool, chunks: ChunkRepository
    ) -> None:
        await self._seed(chunks, 3, bad_index=None)
        down = self._indexer(db_pool, _FakeRouter(fail=True))
        for _ in range(4):
            await down.run_once()

        recovered = _FakeRouter()
        down._ai_router = recovered  # type: ignore[assignment]
        result = await down.run_once()

        assert result["embedded"] == 3
        assert await chunks.counts(CHAT_ID) == {"total": 3, "pending": 0}

    async def test_one_bad_row_among_many_is_parked(
        self, db_pool: asyncpg.Pool, chunks: ChunkRepository
    ) -> None:
        await self._seed(chunks, 3, bad_index=1)
        indexer = self._indexer(db_pool, _FakeRouter(fail_marker="НЕПЕРЕВАРИВАЕМОЕ"))

        for _ in range(3):
            await indexer.run_once()
        after_parking = _FakeRouter(fail_marker="НЕПЕРЕВАРИВАЕМОЕ")
        indexer._ai_router = after_parking  # type: ignore[assignment]
        await indexer.run_once()

        assert after_parking.calls == [], "the queue must move on past a row that never embeds"
        assert await chunks.counts(CHAT_ID) == {"total": 3, "pending": 1}
