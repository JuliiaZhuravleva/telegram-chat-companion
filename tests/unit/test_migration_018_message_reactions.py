"""Tests for alembic/versions/018_message_reactions.py (R-1, ADR-0004).

Same approach as test_migration_014_chat_facts.py: load the migration module
via importlib (numeric-prefixed modules aren't importable via the normal
package path) and monkeypatch ``op.execute`` to capture rendered SQL instead
of touching a real database. Actually applying the migration against
Postgres+pgvector is QA-1's integration-test scope.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "018_message_reactions.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "migration_018_message_reactions", _MIGRATION_PATH
    )
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
    def test_revision_is_018(self, migration):
        assert migration.revision == "018"

    def test_down_revision_is_017(self, migration):
        assert migration.down_revision == "017"

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
        assert "-- Running upgrade 017 -> 018" in result.stdout


class TestUpgradeStatements:
    def test_creates_message_reactions_table(self, captured_sql):
        sql = "\n".join(captured_sql)
        assert "CREATE TABLE IF NOT EXISTS message_reactions" in sql

    def test_table_has_no_fk_to_chat_messages(self, captured_sql):
        """A reacted-to message can be older than retention or never saved --
        an FK would make such inserts fail (ADR-0004 Decision 1)."""
        sql = "\n".join(captured_sql)
        assert "REFERENCES chat_messages" not in sql
        assert "REFERENCES" not in sql

    def test_action_and_reaction_type_columns_not_null(self, captured_sql):
        create_stmt = next(s for s in captured_sql if "CREATE TABLE IF NOT EXISTS" in s)
        assert "action" in create_stmt and "VARCHAR(10) NOT NULL" in create_stmt
        assert "reaction_type" in create_stmt and "VARCHAR(20) NOT NULL" in create_stmt

    def test_user_id_and_actor_chat_id_are_nullable(self, captured_sql):
        """Neither carries NOT NULL -- exactly one is populated in practice,
        but no CHECK constraint enforces that (ADR-0004 Decision 1)."""
        create_stmt = next(s for s in captured_sql if "CREATE TABLE IF NOT EXISTS" in s)
        assert "user_id          BIGINT NOT NULL" not in create_stmt
        assert "actor_chat_id    BIGINT NOT NULL" not in create_stmt

    def test_creates_chat_message_index(self, captured_sql):
        sql = "\n".join(captured_sql)
        assert "CREATE INDEX IF NOT EXISTS idx_message_reactions_chat_message" in sql
        assert "ON message_reactions(chat_id, message_id)" in sql

    def test_creates_chat_created_index_for_retention_and_analytics(self, captured_sql):
        sql = "\n".join(captured_sql)
        assert "CREATE INDEX IF NOT EXISTS idx_message_reactions_chat_created" in sql
        assert "ON message_reactions(chat_id, created_at DESC)" in sql

    def test_adds_reactions_enabled_column_nullable_no_default(self, captured_sql):
        sql = " ".join(" ".join(captured_sql).split())
        assert "ADD COLUMN IF NOT EXISTS reactions_enabled BOOLEAN" in sql
        assert "reactions_enabled BOOLEAN NOT NULL" not in sql
        assert "reactions_enabled BOOLEAN DEFAULT" not in sql

    def test_adds_reactions_history_enabled_column_nullable_no_default(self, captured_sql):
        sql = " ".join(" ".join(captured_sql).split())
        assert "ADD COLUMN IF NOT EXISTS reactions_history_enabled BOOLEAN" in sql
        assert "reactions_history_enabled BOOLEAN NOT NULL" not in sql
        assert "reactions_history_enabled BOOLEAN DEFAULT" not in sql

    def test_all_statements_are_idempotent(self, captured_sql):
        for stmt in captured_sql:
            normalized = " ".join(stmt.split())
            if "CREATE TABLE" in normalized:
                assert "IF NOT EXISTS" in normalized, normalized
            if "CREATE INDEX" in normalized:
                assert "IF NOT EXISTS" in normalized, normalized
            if "ADD COLUMN" in normalized:
                assert "IF NOT EXISTS" in normalized, normalized

    def test_single_statement_per_execute_call(self, captured_sql):
        """Online migrations PREPARE each op.execute() string; more than one
        command in a string breaks `alembic upgrade head` on a fresh DB."""
        for stmt in captured_sql:
            # Strip `--` line comments first (a column comment can legitimately
            # contain a semicolon, e.g. "raw id, never resolved; see notes").
            code_only = "\n".join(line.split("--", 1)[0] for line in stmt.split("\n"))
            normalized = code_only.strip().rstrip(";")
            assert normalized.count(";") == 0, stmt


class TestDowngrade:
    @pytest.fixture
    def downgrade_sql(self, migration, monkeypatch):
        statements: list[str] = []
        fake_op = MagicMock()
        fake_op.execute.side_effect = lambda sql: statements.append(sql)
        monkeypatch.setattr(migration, "op", fake_op)
        migration.downgrade()
        return statements

    def test_drops_both_chat_settings_columns(self, downgrade_sql):
        sql = "\n".join(downgrade_sql)
        assert "DROP COLUMN IF EXISTS reactions_history_enabled" in sql
        assert "DROP COLUMN IF EXISTS reactions_enabled" in sql

    def test_drops_columns_before_dropping_table(self, downgrade_sql):
        col_idx = max(
            i
            for i, s in enumerate(downgrade_sql)
            if "DROP COLUMN IF EXISTS reactions_enabled" in s
            or "DROP COLUMN IF EXISTS reactions_history_enabled" in s
        )
        table_idx = next(
            i for i, s in enumerate(downgrade_sql) if "DROP TABLE IF EXISTS message_reactions" in s
        )
        assert col_idx < table_idx

    def test_drops_table_with_cascade(self, downgrade_sql):
        sql = "\n".join(downgrade_sql)
        assert "DROP TABLE IF EXISTS message_reactions CASCADE" in sql

    def test_all_drops_are_guarded(self, downgrade_sql):
        for stmt in downgrade_sql:
            normalized = " ".join(stmt.split())
            assert "IF EXISTS" in normalized, normalized
