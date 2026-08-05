"""Unit tests for ObservabilityRepository (decision_log / retrieval_log, migration 022)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.database.repositories.observability import ObservabilityRepository


def _make_repo() -> tuple[ObservabilityRepository, AsyncMock]:
    pool = AsyncMock()
    pool.execute = AsyncMock()
    return ObservabilityRepository(pool), pool


class TestLogDecision:
    @pytest.mark.asyncio
    async def test_inserts_into_decision_log_with_all_fields(self) -> None:
        repo, pool = _make_repo()
        await repo.log_decision(
            -100500,
            stage="relevancy_gate",
            decision="silent",
            tier="fast_rules",
            reason="too_short",
            message_id=42,
            user_id=7,
        )
        sql = pool.execute.await_args.args[0]
        assert "INSERT INTO decision_log" in sql
        assert pool.execute.await_args.args[1:] == (
            -100500,
            42,
            7,
            "relevancy_gate",
            "silent",
            "fast_rules",
            "too_short",
        )

    @pytest.mark.asyncio
    async def test_optional_fields_default_to_null(self) -> None:
        repo, pool = _make_repo()
        await repo.log_decision(-1, stage="pipeline", decision="silent")
        args = pool.execute.await_args.args
        assert args[2] is None  # message_id
        assert args[3] is None  # user_id
        assert args[6] is None  # tier
        assert args[7] is None  # reason


class TestLogRetrieval:
    @pytest.mark.asyncio
    async def test_inserts_into_retrieval_log_with_json_payloads(self) -> None:
        repo, pool = _make_repo()
        items = [{"id": 1, "sim": 0.81, "injected": True, "head": "Q: hi"}]
        await repo.log_retrieval(
            -100500,
            source="rag_memory",
            query_text="а что было в феврале?",
            params={"min_similarity": 0.7, "max_results": 5},
            results=items,
            n_results=1,
            n_injected=1,
            duration_ms=123,
            message_id=42,
        )
        sql = pool.execute.await_args.args[0]
        args = pool.execute.await_args.args
        assert "INSERT INTO retrieval_log" in sql
        assert args[1:5] == (-100500, 42, "rag_memory", "а что было в феврале?")
        # JSONB payloads must arrive as JSON strings, not Python reprs
        assert json.loads(args[5]) == {"min_similarity": 0.7, "max_results": 5}
        assert json.loads(args[6]) == items
        assert args[7:] == (1, 1, 123, None)

    @pytest.mark.asyncio
    async def test_none_payloads_stay_null(self) -> None:
        repo, pool = _make_repo()
        await repo.log_retrieval(-1, source="kb")
        args = pool.execute.await_args.args
        assert args[5] is None  # params
        assert args[6] is None  # results
        assert args[7:] == (0, 0, None, None)

    @pytest.mark.asyncio
    async def test_error_is_persisted(self) -> None:
        """A failed retrieval pass must be distinguishable from an empty one."""
        repo, pool = _make_repo()
        await repo.log_retrieval(-1, source="rag_memory", error="TimeoutError: pool exhausted")
        args = pool.execute.await_args.args
        assert args[10] == "TimeoutError: pool exhausted"
