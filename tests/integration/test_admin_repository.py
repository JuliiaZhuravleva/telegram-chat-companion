"""
Integration tests: AdminRepository against real Postgres.

Covers the A-1 acceptance criteria for admin repo:
  - log_unauthorized / get_pending_attempts / update_attempt_status
  - delete_attempt / has_rejected_attempt
  - admin_sticker_session CRUD
  - get_enabled_chats_page  (pagination sanity)
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.admin import AdminRepository
from src.database.repositories.chat_settings import ChatSettingsRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def admin_repo(db_conn: asyncpg.Connection) -> AdminRepository:
    return AdminRepository(db_conn)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def settings_repo(db_conn: asyncpg.Connection) -> ChatSettingsRepository:
    return ChatSettingsRepository(db_conn)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# log_unauthorized
# ---------------------------------------------------------------------------


class TestLogUnauthorized:
    @pytest.mark.asyncio
    async def test_returns_integer_id(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(
            chat_id=-200001,
            chat_title="Blocked Group",
            user_id=12345,
            user_first_name="Alice",
        )
        assert isinstance(attempt_id, int)
        assert attempt_id > 0

    @pytest.mark.asyncio
    async def test_row_is_retrievable(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(
            chat_id=-200002,
            chat_title="Spy Chat",
            user_id=9999,
        )
        row = await admin_repo.get_attempt(attempt_id)
        assert row is not None
        assert row["chat_id"] == -200002
        assert row["status"] == "pending"

    @pytest.mark.asyncio
    async def test_truncates_long_message_text(self, admin_repo: AdminRepository) -> None:
        long_msg = "x" * 500
        attempt_id = await admin_repo.log_unauthorized(
            chat_id=-200003,
            message_text=long_msg,
        )
        row = await admin_repo.get_attempt(attempt_id)
        assert row is not None
        assert len(row["message_text"]) <= 200


# ---------------------------------------------------------------------------
# get_pending_attempts
# ---------------------------------------------------------------------------


class TestGetPendingAttempts:
    @pytest.mark.asyncio
    async def test_returns_pending_rows(self, admin_repo: AdminRepository) -> None:
        await admin_repo.log_unauthorized(chat_id=-201001)
        await admin_repo.log_unauthorized(chat_id=-201002)

        attempts = await admin_repo.get_pending_attempts(limit=50)
        chat_ids = {a["chat_id"] for a in attempts}
        assert -201001 in chat_ids
        assert -201002 in chat_ids

    @pytest.mark.asyncio
    async def test_does_not_return_approved(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(chat_id=-201003)
        await admin_repo.update_attempt_status(attempt_id, "approved")

        attempts = await admin_repo.get_pending_attempts(limit=50)
        ids = [a["id"] for a in attempts]
        assert attempt_id not in ids


# ---------------------------------------------------------------------------
# update_attempt_status
# ---------------------------------------------------------------------------


class TestUpdateAttemptStatus:
    @pytest.mark.asyncio
    async def test_approve_changes_status(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(chat_id=-202001)
        result = await admin_repo.update_attempt_status(attempt_id, "approved")
        assert result is not None
        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_changes_status(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(chat_id=-202002)
        result = await admin_repo.update_attempt_status(attempt_id, "rejected")
        assert result is not None
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_invalid_status_raises(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(chat_id=-202003)
        with pytest.raises(ValueError, match="Invalid attempt status"):
            await admin_repo.update_attempt_status(attempt_id, "invalid_value")

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_id(self, admin_repo: AdminRepository) -> None:
        result = await admin_repo.update_attempt_status(999_999_999, "approved")
        assert result is None


# ---------------------------------------------------------------------------
# has_rejected_attempt
# ---------------------------------------------------------------------------


class TestHasRejectedAttempt:
    @pytest.mark.asyncio
    async def test_true_after_rejection(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(chat_id=-203001)
        await admin_repo.update_attempt_status(attempt_id, "rejected")

        assert await admin_repo.has_rejected_attempt(-203001) is True

    @pytest.mark.asyncio
    async def test_false_when_only_pending(self, admin_repo: AdminRepository) -> None:
        await admin_repo.log_unauthorized(chat_id=-203002)
        assert await admin_repo.has_rejected_attempt(-203002) is False

    @pytest.mark.asyncio
    async def test_false_for_unknown_chat(self, admin_repo: AdminRepository) -> None:
        assert await admin_repo.has_rejected_attempt(-999888777) is False


# ---------------------------------------------------------------------------
# delete_attempt
# ---------------------------------------------------------------------------


class TestDeleteAttempt:
    @pytest.mark.asyncio
    async def test_delete_returns_true(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(chat_id=-204001)
        deleted = await admin_repo.delete_attempt(attempt_id)
        assert deleted is True

    @pytest.mark.asyncio
    async def test_deleted_attempt_not_retrievable(self, admin_repo: AdminRepository) -> None:
        attempt_id = await admin_repo.log_unauthorized(chat_id=-204002)
        await admin_repo.delete_attempt(attempt_id)
        row = await admin_repo.get_attempt(attempt_id)
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_unknown_id_returns_false(self, admin_repo: AdminRepository) -> None:
        deleted = await admin_repo.delete_attempt(999_999_998)
        assert deleted is False


# ---------------------------------------------------------------------------
# Admin sticker session CRUD
# ---------------------------------------------------------------------------


class TestAdminStickerSession:
    @pytest.mark.asyncio
    async def test_create_and_check_session(self, admin_repo: AdminRepository) -> None:
        await admin_repo.create_sticker_session(admin_user_id=7001)
        assert await admin_repo.has_sticker_session(7001) is True

    @pytest.mark.asyncio
    async def test_no_session_by_default(self, admin_repo: AdminRepository) -> None:
        assert await admin_repo.has_sticker_session(7999) is False

    @pytest.mark.asyncio
    async def test_delete_removes_session(self, admin_repo: AdminRepository) -> None:
        await admin_repo.create_sticker_session(admin_user_id=7002)
        await admin_repo.delete_sticker_session(7002)
        assert await admin_repo.has_sticker_session(7002) is False

    @pytest.mark.asyncio
    async def test_create_is_idempotent(self, admin_repo: AdminRepository) -> None:
        """Calling create_sticker_session twice should not raise."""
        await admin_repo.create_sticker_session(admin_user_id=7003)
        await admin_repo.create_sticker_session(admin_user_id=7003)
        assert await admin_repo.has_sticker_session(7003) is True


# ---------------------------------------------------------------------------
# Whitelist pagination
# ---------------------------------------------------------------------------


class TestGetEnabledChatsPage:
    @pytest.mark.asyncio
    async def test_pagination_returns_subset(
        self,
        admin_repo: AdminRepository,
        settings_repo: ChatSettingsRepository,
    ) -> None:
        """With 3 enabled chats and per_page=2, page 0 returns 2 rows."""
        for chat_id in (-300001, -300002, -300003):
            await settings_repo.upsert(chat_id, enabled=True, chat_title=f"Chat {chat_id}")

        chats, total = await admin_repo.get_enabled_chats_page(page=0, per_page=2)
        assert total >= 3
        assert len(chats) == 2

    @pytest.mark.asyncio
    async def test_total_count_is_accurate(
        self,
        admin_repo: AdminRepository,
        settings_repo: ChatSettingsRepository,
    ) -> None:
        await settings_repo.upsert(-300011, enabled=True)
        await settings_repo.upsert(-300012, enabled=True)

        _, total = await admin_repo.get_enabled_chats_page(page=0, per_page=100)
        assert total >= 2
