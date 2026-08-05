"""
Integration tests for internal/migrate-from-n8n.py against real Postgres+pgvector.

The script points at a production database, so the parts worth proving are the
ones that are expensive to get wrong: that pgvector embeddings survive the copy
bit-for-bit (they are portable only because both bots embed with
gemini-embedding-001 at 768 dimensions), that n8n's three-valued `chat_status`
lands correctly on this codebase's `enabled` + `unauthorized_attempts` split,
that n8n's separate rule columns fold into the `config` JSONB the rules engine
actually reads, that credentials are never copied into the target database, and
that re-running the script is a no-op rather than a duplication.

Two throwaway databases are created inside the shared test container: an
n8n-shaped source and an alembic-migrated target.  The script is exercised
through its real ``run()`` entry point.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from pgvector.asyncpg import register_vector

from tests.integration.conftest import _generate_migration_sql

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "internal" / "migrate-from-n8n.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT_PATH.exists(),
    reason="internal/ is gitignored; migration script not present in this checkout",
)

SOURCE_DB = "n8n_migration_src"
TARGET_DB = "n8n_migration_tgt"

# A recognisable 768-dim vector: every element distinct enough that a
# round-trip error would show up immediately.
_EMBEDDING = [round(i / 1000, 4) for i in range(768)]


def _load_script() -> ModuleType:
    """Import the migration script (its filename has dashes, so no plain import)."""
    spec = importlib.util.spec_from_file_location("migrate_from_n8n", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_from_n8n"] = module
    spec.loader.exec_module(module)
    return module


def _swap_db(url: str, dbname: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{dbname}"


# n8n-shaped source schema — column names and types as they exist in production.
_N8N_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chat_settings (
    chat_id BIGINT PRIMARY KEY,
    chat_title VARCHAR(255),
    chat_type VARCHAR(50),
    default_language VARCHAR(10),
    style_prompt TEXT,
    ai_model VARCHAR(100),
    random_response_chance DOUBLE PRECISION,
    random_response_min_interval INTEGER,
    transcribe_voice BOOLEAN,
    transcribe_video_notes BOOLEAN,
    save_messages BOOLEAN,
    rag_enabled BOOLEAN,
    bot_trigger_words TEXT[],
    rules_mode VARCHAR(20),
    abuse_filter_enabled BOOLEAN,
    abuse_sticker_enabled BOOLEAN,
    sticker_learning_enabled BOOLEAN,
    sticker_response_chance DOUBLE PRECISION,
    image_analysis_enabled BOOLEAN,
    sticker_reply_to_sticker_enabled BOOLEAN,
    sticker_reply_to_sticker_chance DOUBLE PRECISION,
    image_comment_sticker_enabled BOOLEAN,
    image_comment_sticker_chance DOUBLE PRECISION,
    link_comments_enabled BOOLEAN,
    chat_status VARCHAR(20),
    added_by_user_id BIGINT,
    last_bot_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE chat_memory (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB,
    embedding vector(768),
    source_message_id BIGINT,
    importance_score DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE TABLE chat_messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    user_id BIGINT,
    username VARCHAR(255),
    first_name VARCHAR(255),
    message_type VARCHAR(50) NOT NULL,
    content TEXT,
    raw_data JSONB,
    sticker_file_id VARCHAR(255),
    sticker_file_unique_id VARCHAR(255),
    sticker_set_name VARCHAR(255),
    sticker_emoji VARCHAR(50),
    reply_to_message_id BIGINT,
    is_bot_message BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    edited_at TIMESTAMPTZ,
    edit_count INTEGER DEFAULT 0,
    original_content TEXT,
    message_thread_id BIGINT,
    UNIQUE (chat_id, message_id)
);

CREATE TABLE sticker_knowledge (
    id SERIAL PRIMARY KEY,
    file_unique_id VARCHAR(255) UNIQUE NOT NULL,
    file_id VARCHAR(255) NOT NULL,
    set_name VARCHAR(255),
    emoji VARCHAR(50),
    is_animated BOOLEAN,
    is_video BOOLEAN,
    visual_description TEXT,
    emotion VARCHAR(100),
    suggested_contexts TEXT[],
    style_tags TEXT[],
    total_uses INTEGER,
    bot_uses INTEGER,
    last_used_at TIMESTAMPTZ,
    analyzed_at TIMESTAMPTZ,
    analysis_failed BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    character_or_meme VARCHAR(255),
    admin_notes TEXT,
    description_embedding vector(768),
    usage_contexts TEXT[],
    original_vision_description TEXT
);

CREATE TABLE sticker_sets (
    set_name VARCHAR(255) PRIMARY KEY,
    set_title VARCHAR(255),
    total_count INTEGER DEFAULT 0,
    thumbnail_file_id VARCHAR(255),
    is_animated BOOLEAN,
    is_video BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE custom_rules (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    rule_name VARCHAR(255),
    rule_type VARCHAR(50),
    config JSONB,
    weight INTEGER DEFAULT 1,
    action VARCHAR(50),
    action_config JSONB,
    enabled BOOLEAN DEFAULT true,
    trigger_count INTEGER DEFAULT 0,
    last_triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    mandatory BOOLEAN DEFAULT false,
    status VARCHAR(20) DEFAULT 'active',
    suggested_by BIGINT,
    suggestion_context TEXT
);

CREATE TABLE unauthorized_attempts (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    chat_title VARCHAR(255),
    chat_type VARCHAR(50),
    chat_username VARCHAR(255),
    user_id BIGINT,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    message_text TEXT,
    message_type VARCHAR(50),
    raw_data JSONB,
    notified BOOLEAN,
    admin_action VARCHAR(20),
    admin_action_at TIMESTAMPTZ,
    admin_action_by BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE bot_config (
    key VARCHAR(255) PRIMARY KEY,
    value JSONB,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


async def _seed_source(conn: asyncpg.Connection) -> None:
    # Three chats, one per chat_status value.
    await conn.execute(
        """
        INSERT INTO chat_settings (
            chat_id, chat_title, chat_type, default_language, style_prompt,
            bot_trigger_words, chat_status, link_comments_enabled,
            sticker_reply_to_sticker_chance, ai_model, abuse_sticker_enabled
        ) VALUES
            (-100, 'Live chat', 'supergroup', 'ru', 'be funny',
             ARRAY['бот'], 'whitelisted', true, 0.7, 'gemini-2.5-flash', true),
            (-200, 'Waiting room', 'group', 'ru', NULL,
             NULL, 'pending', false, NULL, NULL, NULL),
            (-300, 'Banned', 'group', 'en', NULL,
             NULL, 'blacklisted', false, NULL, NULL, NULL)
        """
    )
    await conn.execute(
        """
        INSERT INTO chat_memory (chat_id, content, metadata, embedding, importance_score)
        VALUES (-100, 'Julia likes pelmeni', '{"topic": "food"}'::jsonb, $1, 0.8)
        """,
        _EMBEDDING,
    )
    await conn.execute(
        """
        INSERT INTO chat_messages (chat_id, message_id, user_id, message_type, content)
        VALUES (-100, 1, 42, 'text', 'hello'), (-100, 2, 42, 'text', 'world')
        """
    )
    await conn.execute(
        """
        INSERT INTO sticker_knowledge (
            file_unique_id, file_id, set_name, visual_description,
            emotion, description_embedding, total_uses
        ) VALUES ('uniq-1', 'file-1', 'pack', 'anime girl with donut', 'joy', $1, 7)
        """,
        _EMBEDDING,
    )
    await conn.execute(
        "INSERT INTO sticker_sets (set_name, set_title, total_count) VALUES ('pack', 'Pack', 55)"
    )
    await conn.execute(
        """
        INSERT INTO custom_rules (chat_id, rule_name, rule_type, config, action, action_config, status)
        VALUES (
            -100, 'Борщ-детектор', 'keyword_trigger',
            '{"keywords": ["борщ"], "match_type": "contains"}'::jsonb,
            'custom_response',
            '{"response_template": "Опять борщ?"}'::jsonb,
            'draft'
        )
        """
    )
    # In production every admin_action is NULL — n8n recorded the decision by
    # moving chat_settings.chat_status instead. Cover both shapes.
    await conn.execute(
        """
        INSERT INTO unauthorized_attempts (chat_id, chat_title, chat_type, user_id, username,
                                           first_name, message_text, admin_action)
        VALUES
            (-999, 'Stranger', 'group', 7, 'stranger', 'Stran', 'let me in', 'rejected'),
            (-100, 'Live chat', 'supergroup', 8, 'member', 'Mem', 'hi', NULL),
            (-300, 'Banned', 'group', 9, 'banned', 'Ban', 'let me in', NULL),
            (-888, 'Unknown', 'group', 10, 'who', 'Who', 'hello?', NULL)
        """
    )
    await conn.execute(
        """
        INSERT INTO bot_config (key, value) VALUES
            ('default_chat_language', '"ru"'::jsonb),
            ('default_chat_random_response_chance', '0.07'::jsonb),
            ('admin_ids', '[111222333]'::jsonb),
            ('telegram_bot_token', '"secret-bot-token"'::jsonb),
            ('gemini_api_key', '"AIzaSecret"'::jsonb),
            ('hetzner_api_token', '"hetzner-secret"'::jsonb)
        """
    )


@pytest_asyncio.fixture
async def migration_env(pg_url: str) -> dict[str, Any]:  # type: ignore[misc]
    """Fresh source + target databases, torn down after the test."""
    admin = await asyncpg.connect(pg_url)
    try:
        for name in (SOURCE_DB, TARGET_DB):
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()

    source_url = _swap_db(pg_url, SOURCE_DB)
    target_url = _swap_db(pg_url, TARGET_DB)

    source = await asyncpg.connect(source_url)
    try:
        await source.execute(_N8N_SCHEMA)
        await register_vector(source)
        await _seed_source(source)
    finally:
        await source.close()

    target = await asyncpg.connect(target_url)
    try:
        await target.execute(_generate_migration_sql())
    finally:
        await target.close()

    yield {"source_url": source_url, "target_url": target_url, "script": _load_script()}

    admin = await asyncpg.connect(pg_url)
    try:
        for name in (SOURCE_DB, TARGET_DB):
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _apply(env: dict[str, Any]) -> int:
    return await env["script"].run(env["source_url"], env["target_url"], dry_run=False, only=None)


async def _apply_since(env: dict[str, Any], since: datetime) -> int:
    return await env["script"].run(
        env["source_url"], env["target_url"], dry_run=False, only=None, since=since
    )


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self, migration_env: dict[str, Any]) -> None:
        await migration_env["script"].run(
            migration_env["source_url"], migration_env["target_url"], dry_run=True, only=None
        )

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            for table in ("chat_settings", "chat_memory", "chat_messages", "custom_rules"):
                count = await target.fetchval(f"SELECT COUNT(*) FROM {table}")
                assert count == 0, f"{table} was written during a dry run"
        finally:
            await target.close()


# ---------------------------------------------------------------------------
# Vectors — the reason no re-embedding is needed
# ---------------------------------------------------------------------------


def _vector_values(value: Any) -> list[float]:
    """Decoded vector column → plain floats, across pgvector-python versions.

    pgvector < 0.4 decodes to an iterable (numpy array); 0.4+ returns a
    ``Vector`` object that is NOT iterable and exposes ``to_list()`` instead.
    The dependency is unpinned (``pgvector>=0.2.0``, TD-037), so the assertion
    must accept both or this suite breaks on a fresh install with no repo change
    (observed 2026-08-05: 0.5.0 → ``TypeError: 'Vector' object is not iterable``).
    """
    if hasattr(value, "to_list"):
        return [float(v) for v in value.to_list()]
    return [float(v) for v in value]


class TestEmbeddingsSurvive:
    @pytest.mark.asyncio
    async def test_chat_memory_embedding_round_trips_exactly(
        self, migration_env: dict[str, Any]
    ) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            await register_vector(target)
            row = await target.fetchrow("SELECT content, embedding FROM chat_memory")
            assert row is not None
            assert row["content"] == "Julia likes pelmeni"
            assert [round(v, 4) for v in _vector_values(row["embedding"])] == _EMBEDDING
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_sticker_embedding_round_trips_exactly(
        self, migration_env: dict[str, Any]
    ) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            await register_vector(target)
            row = await target.fetchrow(
                "SELECT description_embedding, total_uses FROM sticker_knowledge"
            )
            assert row is not None
            assert [round(v, 4) for v in _vector_values(row["description_embedding"])] == (
                _EMBEDDING
            )
            assert row["total_uses"] == 7
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_cosine_search_finds_the_migrated_memory(
        self, migration_env: dict[str, Any]
    ) -> None:
        """The end-user-visible proof: RAG retrieval works on migrated vectors."""
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            await register_vector(target)
            similarity = await target.fetchval(
                "SELECT 1 - (embedding <=> $1) FROM chat_memory LIMIT 1",
                _EMBEDDING,
            )
            assert similarity == pytest.approx(1.0, abs=1e-6)
        finally:
            await target.close()


# ---------------------------------------------------------------------------
# chat_status -> enabled + unauthorized_attempts
# ---------------------------------------------------------------------------


class TestChatStatusMapping:
    @pytest.mark.asyncio
    async def test_whitelisted_becomes_enabled(self, migration_env: dict[str, Any]) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            row = await target.fetchrow(
                "SELECT enabled, language, system_prompt, trigger_words, link_comments_enabled "
                "FROM chat_settings WHERE chat_id = -100"
            )
            assert row is not None
            assert row["enabled"] is True
            assert row["language"] == "ru"
            assert row["system_prompt"] == "be funny"  # style_prompt renamed
            assert row["trigger_words"] == ["бот"]  # bot_trigger_words renamed
            assert row["link_comments_enabled"] is True  # needs migration 015
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_pending_and_blacklisted_are_disabled(
        self, migration_env: dict[str, Any]
    ) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            rows = await target.fetch(
                "SELECT chat_id, enabled FROM chat_settings WHERE chat_id IN (-200, -300)"
            )
            assert {r["chat_id"]: r["enabled"] for r in rows} == {-200: False, -300: False}
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_blacklisted_chat_gets_a_rejected_attempt(
        self, migration_env: dict[str, Any]
    ) -> None:
        """AccessControlMiddleware.has_rejected_attempt() is how a blacklist is
        expressed here — without this row the ban silently disappears."""
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            status = await target.fetchval(
                "SELECT status FROM unauthorized_attempts WHERE chat_id = -300"
            )
            assert status == "rejected"
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_pending_chat_gets_a_pending_attempt(self, migration_env: dict[str, Any]) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            status = await target.fetchval(
                "SELECT status FROM unauthorized_attempts WHERE chat_id = -200"
            )
            assert status == "pending"
        finally:
            await target.close()


class TestAttemptStatusInference:
    """n8n leaves admin_action NULL and records the decision in chat_status, so
    the attempt's status has to be inferred from the chat — otherwise every
    already-handled request reappears in the admin panel's pending tab."""

    @pytest.mark.asyncio
    async def test_attempt_for_whitelisted_chat_becomes_approved(
        self, migration_env: dict[str, Any]
    ) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            status = await target.fetchval(
                "SELECT status FROM unauthorized_attempts WHERE chat_id = -100"
            )
            assert status == "approved"
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_attempt_for_blacklisted_chat_becomes_rejected(
        self, migration_env: dict[str, Any]
    ) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            statuses = {
                r["status"]
                for r in await target.fetch(
                    "SELECT status FROM unauthorized_attempts WHERE chat_id = -300"
                )
            }
            assert statuses == {"rejected"}
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_attempt_for_unknown_chat_stays_pending(
        self, migration_env: dict[str, Any]
    ) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            status = await target.fetchval(
                "SELECT status FROM unauthorized_attempts WHERE chat_id = -888"
            )
            assert status == "pending"
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_explicit_admin_action_wins_over_inference(
        self, migration_env: dict[str, Any]
    ) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            status = await target.fetchval(
                "SELECT status FROM unauthorized_attempts WHERE chat_id = -999"
            )
            assert status == "rejected"
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_admin_panel_pending_list_shows_only_the_unknown_chat(
        self, migration_env: dict[str, Any]
    ) -> None:
        """The user-visible outcome: after migrating, the pending tab lists the
        genuinely unhandled request and nothing else."""
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            rows = await target.fetch(
                """
                SELECT ua.chat_id FROM unauthorized_attempts ua
                WHERE ua.status = 'pending'
                  AND NOT EXISTS (
                      SELECT 1 FROM chat_settings cs
                      WHERE cs.chat_id = ua.chat_id AND cs.enabled = true
                  )
                """
            )
            assert sorted(r["chat_id"] for r in rows) == [-888, -200]
        finally:
            await target.close()


# ---------------------------------------------------------------------------
# custom_rules folding
# ---------------------------------------------------------------------------


class TestCustomRules:
    @pytest.mark.asyncio
    async def test_name_action_and_action_config_fold_into_config(
        self, migration_env: dict[str, Any]
    ) -> None:
        """RulesEngine._extract_actions() reads action / response_template out of
        config, so n8n's separate columns have to end up in there."""
        import json

        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            row = await target.fetchrow("SELECT rule_type, config, status FROM custom_rules")
            assert row is not None
            config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]

            assert row["rule_type"] == "keyword_trigger"
            assert config["keywords"] == ["борщ"]
            assert config["match_type"] == "contains"
            assert config["action"] == "custom_response"
            assert config["response_template"] == "Опять борщ?"
            assert config["name"] == "Борщ-детектор"
            assert row["status"] == "draft"  # inactive rules stay inactive
        finally:
            await target.close()


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


class TestSecrets:
    @pytest.mark.asyncio
    async def test_credentials_never_reach_the_target_database(
        self, migration_env: dict[str, Any]
    ) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            keys = {r["key"] for r in await target.fetch("SELECT key FROM bot_config")}
            assert "telegram_bot_token" not in keys
            assert "gemini_api_key" not in keys
            assert "hetzner_api_token" not in keys

            blob = str(await target.fetch("SELECT value FROM bot_config"))
            assert "secret-bot-token" not in blob
            assert "AIzaSecret" not in blob
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_mapped_defaults_are_migrated(self, migration_env: dict[str, Any]) -> None:
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            keys = {r["key"] for r in await target.fetch("SELECT key FROM bot_config")}
            assert "default_language" in keys
            assert "default_random_response_chance" in keys
            assert "admin_ids" in keys
        finally:
            await target.close()


# ---------------------------------------------------------------------------
# Idempotency + sequences
# ---------------------------------------------------------------------------


class TestRerun:
    @pytest.mark.asyncio
    async def test_running_twice_does_not_duplicate(self, migration_env: dict[str, Any]) -> None:
        """The cutover runs this a second time against fresh data; a re-run must
        converge, not double every table."""
        await _apply(migration_env)
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            assert await target.fetchval("SELECT COUNT(*) FROM chat_messages") == 2
            assert await target.fetchval("SELECT COUNT(*) FROM chat_memory") == 1
            assert await target.fetchval("SELECT COUNT(*) FROM chat_settings") == 3
            assert await target.fetchval("SELECT COUNT(*) FROM custom_rules") == 1
            assert await target.fetchval("SELECT COUNT(*) FROM sticker_knowledge") == 1
            # 4 copied from the source + 1 derived for the pending chat -200.
            # Chat -300 gets no derived row: the source already supplies a
            # rejected attempt for it, and _record_attempt is NOT EXISTS-guarded.
            assert await target.fetchval("SELECT COUNT(*) FROM unauthorized_attempts") == 5
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_sequences_advance_past_copied_ids(self, migration_env: dict[str, Any]) -> None:
        """Rows keep their original ids, so an un-synced SERIAL sequence would
        make the running bot's very next insert collide."""
        await _apply(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            new_id = await target.fetchval(
                """
                INSERT INTO chat_memory (chat_id, content) VALUES (-100, 'fresh')
                RETURNING id
                """
            )
            max_migrated = await target.fetchval(
                "SELECT MAX(id) FROM chat_memory WHERE content = 'Julia likes pelmeni'"
            )
            assert new_id > max_migrated
        finally:
            await target.close()


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


class TestMissingSourceTables:
    @pytest.mark.asyncio
    async def test_absent_source_tables_are_reported_not_fatal(
        self, migration_env: dict[str, Any]
    ) -> None:
        """The seeded source has no abuse_* / jailbreak_patterns tables; the run
        must finish and say so rather than crash halfway through."""
        exit_code = await _apply(migration_env)

        assert exit_code == 1  # warnings present
        target = await asyncpg.connect(migration_env["target_url"])
        try:
            assert await target.fetchval("SELECT COUNT(*) FROM chat_messages") == 2
        finally:
            await target.close()


# ---------------------------------------------------------------------------
# Catch-up run (--since) — the cutover step
# ---------------------------------------------------------------------------


class TestCatchUp:
    """The cutover does: full migration from dump #1, bot keeps running for days,
    then a --since run picks up everything that accumulated."""

    @staticmethod
    async def _watermark(env: dict[str, Any]) -> datetime:
        """The source database's own clock, as dump-n8n.sh records it."""
        source = await asyncpg.connect(env["source_url"])
        try:
            value: datetime = await source.fetchval("SELECT now()")
            return value
        finally:
            await source.close()

    @pytest.mark.asyncio
    async def test_new_rows_are_picked_up(self, migration_env: dict[str, Any]) -> None:
        await _apply(migration_env)
        watermark = await self._watermark(migration_env)

        source = await asyncpg.connect(migration_env["source_url"])
        try:
            await register_vector(source)
            await source.execute(
                """
                INSERT INTO chat_messages (chat_id, message_id, user_id, message_type, content)
                VALUES (-100, 3, 42, 'text', 'said after the dump')
                """
            )
            await source.execute(
                """
                INSERT INTO chat_memory (chat_id, content, embedding, importance_score)
                VALUES (-100, 'learned after the dump', $1, 0.9)
                """,
                _EMBEDDING,
            )
        finally:
            await source.close()

        await _apply_since(migration_env, watermark)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            assert await target.fetchval("SELECT COUNT(*) FROM chat_messages") == 3
            assert (
                await target.fetchval(
                    "SELECT content FROM chat_messages WHERE chat_id = -100 AND message_id = 3"
                )
                == "said after the dump"
            )
            assert await target.fetchval("SELECT COUNT(*) FROM chat_memory") == 2
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_edit_of_an_old_message_is_picked_up(self, migration_env: dict[str, Any]) -> None:
        """created_at is older than the watermark but edited_at is newer — the
        GREATEST() freshness expression is what catches this."""
        await _apply(migration_env)
        watermark = await self._watermark(migration_env)

        source = await asyncpg.connect(migration_env["source_url"])
        try:
            await source.execute(
                """
                UPDATE chat_messages
                SET content = 'hello (edited)', edited_at = now(), edit_count = 1,
                    original_content = 'hello'
                WHERE chat_id = -100 AND message_id = 1
                """
            )
        finally:
            await source.close()

        await _apply_since(migration_env, watermark)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            row = await target.fetchrow(
                "SELECT content, edit_count, original_content FROM chat_messages "
                "WHERE chat_id = -100 AND message_id = 1"
            )
            assert row is not None
            assert row["content"] == "hello (edited)"
            assert row["edit_count"] == 1
            assert row["original_content"] == "hello"
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_untouched_rows_are_not_recopied(self, migration_env: dict[str, Any]) -> None:
        """Proves the filter actually filters: a sentinel written directly into
        the target survives a catch-up run, because that row is older than the
        watermark and never changed in the source."""
        await _apply(migration_env)
        watermark = await self._watermark(migration_env)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            await target.execute(
                "UPDATE chat_messages SET content = 'SENTINEL' "
                "WHERE chat_id = -100 AND message_id = 2"
            )
        finally:
            await target.close()

        await _apply_since(migration_env, watermark)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            assert (
                await target.fetchval(
                    "SELECT content FROM chat_messages WHERE chat_id = -100 AND message_id = 2"
                )
                == "SENTINEL"
            )
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_mutable_tables_are_still_fully_resynced(
        self, migration_env: dict[str, Any]
    ) -> None:
        """sticker_knowledge has no freshness column on purpose: usage counters
        and admin notes change in place with no timestamp to filter on, so a
        catch-up must re-sync it whole."""
        await _apply(migration_env)
        watermark = await self._watermark(migration_env)

        source = await asyncpg.connect(migration_env["source_url"])
        try:
            await source.execute(
                "UPDATE sticker_knowledge SET total_uses = 99, admin_notes = 'winks' "
                "WHERE file_unique_id = 'uniq-1'"
            )
            await source.execute(
                "UPDATE chat_settings SET style_prompt = 'be terse' WHERE chat_id = -100"
            )
        finally:
            await source.close()

        await _apply_since(migration_env, watermark)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            row = await target.fetchrow(
                "SELECT total_uses, admin_notes FROM sticker_knowledge "
                "WHERE file_unique_id = 'uniq-1'"
            )
            assert row is not None
            assert row["total_uses"] == 99
            assert row["admin_notes"] == "winks"
            assert (
                await target.fetchval(
                    "SELECT system_prompt FROM chat_settings WHERE chat_id = -100"
                )
                == "be terse"
            )
        finally:
            await target.close()

    @pytest.mark.asyncio
    async def test_catch_up_with_no_changes_is_a_no_op(self, migration_env: dict[str, Any]) -> None:
        await _apply(migration_env)
        watermark = await self._watermark(migration_env)

        await _apply_since(migration_env, watermark)

        target = await asyncpg.connect(migration_env["target_url"])
        try:
            assert await target.fetchval("SELECT COUNT(*) FROM chat_messages") == 2
            assert await target.fetchval("SELECT COUNT(*) FROM chat_memory") == 1
            assert await target.fetchval("SELECT COUNT(*) FROM chat_settings") == 3
        finally:
            await target.close()
