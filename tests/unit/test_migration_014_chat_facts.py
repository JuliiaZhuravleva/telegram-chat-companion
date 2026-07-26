"""Tests for alembic/versions/014_chat_facts.py (A1).

Migration files are numeric-prefixed modules, not importable via the normal
package path, so they're loaded via importlib from their file path (same
approach alembic itself uses). ``op.execute`` is monkeypatched to capture the
rendered SQL instead of touching a real database -- integration coverage
(actually applying the migration against Postgres+pgvector) is A6's scope.

These tests guard the two properties that matter most for a migration that
runs against a live, already-populated database:
- chain integrity (revision/down_revision match the ADR-0003 reservation)
- idempotency (every DDL statement is IF NOT EXISTS / IF EXISTS safe, so a
  re-run or a crash-and-retry never errors on "already exists")
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "014_chat_facts.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_014_chat_facts", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration():
    return _load_migration_module()


@pytest.fixture
def captured_sql(migration, monkeypatch):
    """Run upgrade() with op.execute mocked; return the list of executed SQL strings."""
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = lambda sql: statements.append(sql)
    monkeypatch.setattr(migration, "op", fake_op)
    migration.upgrade()
    return statements


class TestRevisionChain:
    """Chain integrity per ADR-0003's migration-numbering reservation."""

    def test_revision_is_014(self, migration):
        assert migration.revision == "014"

    def test_down_revision_is_012(self, migration):
        # Chains onto 012 (newest on-disk migration), not 013 -- ADR-0002
        # reserves 013 for 013_spend_limit_per_chat.py, which hadn't landed.
        assert migration.down_revision == "012"

    def test_alembic_head_resolves_without_branch_conflict(self):
        """Full alembic --sql render succeeds end-to-end (no duplicate/branch heads)."""
        import os
        import subprocess

        env = dict(os.environ)
        env["DATABASE_URL"] = "postgresql://fake:fake@localhost/fake"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
            capture_output=True,
            text=True,
            env=env,
            cwd=Path(__file__).resolve().parents[2],
        )
        assert result.returncode == 0, result.stderr
        assert "-- Running upgrade 012 -> 014" in result.stdout


class TestUpgradeStatements:
    """upgrade() emits the expected DDL, all idempotent."""

    def test_creates_chat_facts_table(self, captured_sql):
        sql = "\n".join(captured_sql)
        assert "CREATE TABLE IF NOT EXISTS chat_facts" in sql

    def test_creates_status_index(self, captured_sql):
        sql = "\n".join(captured_sql)
        assert "CREATE INDEX IF NOT EXISTS idx_chat_facts_status" in sql
        assert "ON chat_facts(chat_id, status, valid_to)" in sql

    def test_creates_active_key_partial_index_unique(self, captured_sql):
        """UNIQUE — the DB-level backstop for 'one active row per key' (review fix)."""
        sql = "\n".join(captured_sql)
        assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_facts_active_key" in sql
        assert "WHERE valid_to IS NULL" in sql
        # Upgrades dev DBs that had the pre-review non-unique version.
        assert "DROP INDEX IF EXISTS idx_chat_facts_active_key" in sql

    def test_creates_ivfflat_embedding_index_lists_10(self, captured_sql):
        sql = "\n".join(captured_sql)
        assert "idx_chat_facts_embedding" in sql
        assert "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)" in sql

    def test_creates_updated_at_trigger_reusing_existing_function(self, captured_sql):
        sql = "\n".join(captured_sql)
        assert "DROP TRIGGER IF EXISTS chat_facts_updated_at ON chat_facts" in sql
        assert "CREATE TRIGGER chat_facts_updated_at" in sql
        assert "EXECUTE FUNCTION update_updated_at()" in sql
        # Reuses the existing function (migration 001) -- must not redefine it.
        assert not any("CREATE FUNCTION update_updated_at" in s for s in captured_sql)

    def test_adds_kb_organizer_ids_column(self, captured_sql):
        sql = "\n".join(captured_sql)
        assert "ADD COLUMN IF NOT EXISTS kb_organizer_ids JSONB NOT NULL DEFAULT '[]'" in sql

    def test_adds_kb_enabled_column_nullable_no_default(self, captured_sql):
        """No NOT NULL, no DEFAULT: NULL = defer to the global default layer
        (review fix — a DEFAULT would materialize on ensure_exists and shadow
        bot_config's default_kb_enabled forever)."""
        sql = " ".join(" ".join(captured_sql).split())
        assert "ADD COLUMN IF NOT EXISTS kb_enabled BOOLEAN" in sql
        assert "kb_enabled BOOLEAN NOT NULL" not in sql
        assert "kb_enabled BOOLEAN DEFAULT" not in sql
        assert "ALTER COLUMN kb_enabled DROP NOT NULL" in sql
        assert "ALTER COLUMN kb_enabled DROP DEFAULT" in sql

    def test_all_statements_are_idempotent(self, captured_sql):
        """Every CREATE/ADD/DROP statement guards against re-run failures."""
        for stmt in captured_sql:
            normalized = " ".join(stmt.split())
            if "CREATE TABLE" in normalized:
                assert "IF NOT EXISTS" in normalized, normalized
            if "CREATE INDEX" in normalized or "CREATE UNIQUE INDEX" in normalized:
                assert "IF NOT EXISTS" in normalized, normalized
            if "ADD COLUMN" in normalized:
                assert "IF NOT EXISTS" in normalized, normalized
            if normalized.startswith(("DROP TRIGGER", "DROP INDEX")):
                assert "IF EXISTS" in normalized, normalized


class TestDowngrade:
    """downgrade() reverses upgrade() in dependency-safe order, all IF EXISTS guarded."""

    @pytest.fixture
    def downgrade_sql(self, migration, monkeypatch):
        statements: list[str] = []
        fake_op = MagicMock()
        fake_op.execute.side_effect = lambda sql: statements.append(sql)
        monkeypatch.setattr(migration, "op", fake_op)
        migration.downgrade()
        return statements

    def test_drops_kb_columns(self, downgrade_sql):
        sql = "\n".join(downgrade_sql)
        assert "DROP COLUMN IF EXISTS kb_enabled" in sql
        assert "DROP COLUMN IF EXISTS kb_organizer_ids" in sql

    def test_drops_trigger_before_table(self, downgrade_sql):
        trigger_idx = next(i for i, s in enumerate(downgrade_sql) if "DROP TRIGGER" in s)
        table_idx = next(
            i for i, s in enumerate(downgrade_sql) if "DROP TABLE IF EXISTS chat_facts" in s
        )
        assert trigger_idx < table_idx

    def test_drops_table_with_cascade(self, downgrade_sql):
        sql = "\n".join(downgrade_sql)
        assert "DROP TABLE IF EXISTS chat_facts CASCADE" in sql

    def test_all_drops_are_guarded(self, downgrade_sql):
        for stmt in downgrade_sql:
            normalized = " ".join(stmt.split())
            assert "IF EXISTS" in normalized, normalized
