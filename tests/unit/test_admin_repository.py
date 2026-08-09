"""Tests for AdminRepository with mocked asyncpg pool."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from src.database.repositories.admin import AdminRepository


@pytest.fixture
def repo():
    """AdminRepository with mocked pool."""
    pool = AsyncMock()
    return AdminRepository(pool), pool


class TestLogUnauthorized:
    @pytest.mark.asyncio
    async def test_inserts_and_returns_id(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"id": 42}

        result = await repo_.log_unauthorized(
            chat_id=-100123,
            chat_title="Test Chat",
            user_id=456,
            user_first_name="Alice",
        )

        assert result == 42
        pool.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_truncates_long_message(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"id": 1}

        long_text = "x" * 500
        await repo_.log_unauthorized(chat_id=1, message_text=long_text)

        call_args = pool.fetchrow.call_args[0]
        # 7th positional arg is message_text
        assert len(call_args[7]) <= 200


class TestGetPendingAttempts:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = [
            {"id": 1, "chat_id": -100, "status": "pending"},
            {"id": 2, "chat_id": -200, "status": "pending"},
        ]

        result = await repo_.get_pending_attempts(limit=10)

        assert len(result) == 2
        assert result[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_empty_result(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = []

        result = await repo_.get_pending_attempts()
        assert result == []


class TestUpdateAttemptStatus:
    @pytest.mark.asyncio
    async def test_updates_and_returns_row(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"id": 1, "status": "approved"}

        result = await repo_.update_attempt_status(1, "approved")

        assert result is not None
        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = None

        result = await repo_.update_attempt_status(999, "rejected")
        assert result is None


class TestStatistics:
    @pytest.mark.asyncio
    async def test_message_count(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 42

        result = await repo_.get_message_count(timedelta(hours=1))
        assert result == 42

    @pytest.mark.asyncio
    async def test_message_count_returns_zero_for_null(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = None

        result = await repo_.get_message_count(timedelta(hours=24))
        assert result == 0

    @pytest.mark.asyncio
    async def test_response_count(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 10

        result = await repo_.get_response_count(timedelta(days=7))
        assert result == 10

    @pytest.mark.asyncio
    async def test_unauth_count(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 3

        result = await repo_.get_unauth_count(timedelta(hours=24))
        assert result == 3

    @pytest.mark.asyncio
    async def test_active_chats_count(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 5

        result = await repo_.get_active_chats_count(timedelta(days=7))
        assert result == 5

    @pytest.mark.asyncio
    async def test_enabled_chats_count(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 2

        result = await repo_.get_enabled_chats_count()
        assert result == 2


class TestStickerSession:
    @pytest.mark.asyncio
    async def test_create_session(self, repo):
        repo_, pool = repo
        await repo_.create_sticker_session(12345)
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_session(self, repo):
        repo_, pool = repo
        await repo_.delete_sticker_session(12345)
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_has_session_true(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 1

        result = await repo_.has_sticker_session(12345)
        assert result is True

    @pytest.mark.asyncio
    async def test_has_session_false(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = None

        result = await repo_.has_sticker_session(12345)
        assert result is False


class TestGetEnabledChatsPage:
    @pytest.mark.asyncio
    async def test_returns_chats_and_total(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 3
        pool.fetch.return_value = [
            {"chat_id": -100, "chat_title": "Alpha", "chat_type": "group"},
            {"chat_id": -200, "chat_title": "Beta", "chat_type": "supergroup"},
        ]

        chats, total = await repo_.get_enabled_chats_page(0, per_page=5)

        assert total == 3
        assert len(chats) == 2
        assert chats[0]["chat_title"] == "Alpha"

    @pytest.mark.asyncio
    async def test_empty_result(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 0
        pool.fetch.return_value = []

        chats, total = await repo_.get_enabled_chats_page(0)
        assert total == 0
        assert chats == []


class TestGetEnabledChatsPageByActivity:
    """C-1: activity-sorted picker method (dedicated -- not shared with
    the title-sorted KB/Reactions/whitelist pickers)."""

    @pytest.mark.asyncio
    async def test_returns_chats_with_counts_and_total(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 2
        pool.fetch.return_value = [
            {"chat_id": -100, "chat_title": "Busy", "chat_type": "group", "message_count_24h": 42},
            {"chat_id": -200, "chat_title": "Quiet", "chat_type": "group", "message_count_24h": 0},
        ]

        chats, total = await repo_.get_enabled_chats_page_by_activity(0, per_page=5)

        assert total == 2
        assert len(chats) == 2
        assert chats[0]["message_count_24h"] == 42
        assert chats[1]["message_count_24h"] == 0

    @pytest.mark.asyncio
    async def test_empty_result(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 0
        pool.fetch.return_value = []

        chats, total = await repo_.get_enabled_chats_page_by_activity(0)
        assert total == 0
        assert chats == []

    @pytest.mark.asyncio
    async def test_single_query_no_n_plus_1(self, repo):
        """One fetch() call regardless of how many chats come back -- the
        aggregate must be a single LEFT JOIN, not a per-chat lookup."""
        repo_, pool = repo
        pool.fetchval.return_value = 50
        pool.fetch.return_value = [
            {"chat_id": -i, "chat_title": f"Chat {i}", "chat_type": "group", "message_count_24h": i}
            for i in range(10)
        ]

        await repo_.get_enabled_chats_page_by_activity(0, per_page=10)

        pool.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_default_24h_window_and_pagination_params(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 0
        pool.fetch.return_value = []

        await repo_.get_enabled_chats_page_by_activity(page=2, per_page=10)

        call_args = pool.fetch.call_args[0]
        # (query, window, per_page, offset)
        assert call_args[1] == timedelta(hours=24)
        assert call_args[2] == 10
        assert call_args[3] == 20  # page 2 * per_page 10

    @pytest.mark.asyncio
    async def test_accepts_custom_window(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 0
        pool.fetch.return_value = []

        await repo_.get_enabled_chats_page_by_activity(0, window=timedelta(hours=1))

        call_args = pool.fetch.call_args[0]
        assert call_args[1] == timedelta(hours=1)


class TestFindEnabledChatsByTitle:
    """D-1 shortcut's title-search method."""

    @pytest.mark.asyncio
    async def test_returns_matches(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = [
            {"chat_id": -100, "chat_title": "Foo Bar", "chat_type": "group"},
        ]

        result = await repo_.find_enabled_chats_by_title("foo")

        assert result == [{"chat_id": -100, "chat_title": "Foo Bar", "chat_type": "group"}]

    @pytest.mark.asyncio
    async def test_escapes_ilike_wildcards(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = []

        await repo_.find_enabled_chats_by_title("50%_off\\")

        escaped_query = pool.fetch.call_args[0][1]
        assert escaped_query == "50\\%\\_off\\\\"

    @pytest.mark.asyncio
    async def test_passes_limit(self, repo):
        repo_, pool = repo
        pool.fetch.return_value = []

        await repo_.find_enabled_chats_by_title("x", limit=3)

        assert pool.fetch.call_args[0][2] == 3


class TestGetPendingAttemptsPage:
    @pytest.mark.asyncio
    async def test_returns_attempts_and_total(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 2
        pool.fetch.return_value = [
            {"id": 1, "chat_id": -100, "chat_title": "Test", "status": "pending"},
        ]

        attempts, total = await repo_.get_pending_attempts_page(0, per_page=5)

        assert total == 2
        assert len(attempts) == 1
        assert attempts[0]["id"] == 1


class TestGetAttempt:
    @pytest.mark.asyncio
    async def test_returns_attempt(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = {"id": 42, "chat_id": -100, "status": "pending"}

        result = await repo_.get_attempt(42)

        assert result is not None
        assert result["id"] == 42

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, repo):
        repo_, pool = repo
        pool.fetchrow.return_value = None

        result = await repo_.get_attempt(999)
        assert result is None


class TestNotificationSettings:
    @pytest.mark.asyncio
    async def test_get_defaults_when_no_settings(self, repo):
        repo_, _ = repo
        bot_config_repo = AsyncMock()
        bot_config_repo.get.return_value = None

        result = await repo_.get_notification_settings(bot_config_repo)
        assert result["sticker"] == "on"
        assert result["unauthorized"] is True
        assert result["jailbreak"] is True
        assert result["blacklist"] is True
        assert result["ai_fallback"] is True

    @pytest.mark.asyncio
    async def test_get_from_existing_settings(self, repo):
        repo_, _ = repo
        bot_config_repo = AsyncMock()
        bot_config_repo.get.return_value = (
            '{"lang": "en", "notifications": {"sticker": "detailed", "jailbreak": false}}'
        )

        result = await repo_.get_notification_settings(bot_config_repo)
        assert result["sticker"] == "detailed"
        assert result["jailbreak"] is False
        # Defaults for missing keys
        assert result["unauthorized"] is True
        assert result["blacklist"] is True

    @pytest.mark.asyncio
    async def test_set_preserves_existing_data(self, repo):
        import json

        repo_, _ = repo
        bot_config_repo = AsyncMock()
        bot_config_repo.get.return_value = '{"lang": "ru", "notifications": {"sticker": "on"}}'

        await repo_.set_notification_setting(bot_config_repo, "jailbreak", False)

        call_args = bot_config_repo.set.call_args[0]
        saved = json.loads(call_args[1])
        assert saved["lang"] == "ru"
        assert saved["notifications"]["sticker"] == "on"
        assert saved["notifications"]["jailbreak"] is False

    @pytest.mark.asyncio
    async def test_set_creates_notifications_key_if_missing(self, repo):
        import json

        repo_, _ = repo
        bot_config_repo = AsyncMock()
        bot_config_repo.get.return_value = '{"lang": "en"}'

        await repo_.set_notification_setting(bot_config_repo, "sticker", "off")

        call_args = bot_config_repo.set.call_args[0]
        saved = json.loads(call_args[1])
        assert saved["lang"] == "en"
        assert saved["notifications"]["sticker"] == "off"


class TestAdminLanguage:
    @pytest.mark.asyncio
    async def test_get_language_default(self, repo):
        repo_, _ = repo
        bot_config_repo = AsyncMock()
        bot_config_repo.get.return_value = None

        result = await repo_.get_admin_language(bot_config_repo)
        assert result == "ru"

    @pytest.mark.asyncio
    async def test_get_language_from_settings(self, repo):
        repo_, _ = repo
        bot_config_repo = AsyncMock()
        bot_config_repo.get.return_value = '{"lang": "en"}'

        result = await repo_.get_admin_language(bot_config_repo)
        assert result == "en"

    @pytest.mark.asyncio
    async def test_set_language(self, repo):
        repo_, _ = repo
        bot_config_repo = AsyncMock()
        bot_config_repo.get.return_value = None

        await repo_.set_admin_language(bot_config_repo, "en")

        bot_config_repo.set.assert_awaited_once()
        call_args = bot_config_repo.set.call_args[0]
        assert call_args[0] == "admin_settings"
        assert '"lang": "en"' in call_args[1]

    @pytest.mark.asyncio
    async def test_set_language_preserves_existing_settings(self, repo):
        repo_, _ = repo
        bot_config_repo = AsyncMock()
        bot_config_repo.get.return_value = '{"other_key": "value"}'

        await repo_.set_admin_language(bot_config_repo, "ru")

        call_args = bot_config_repo.set.call_args[0]
        saved_json = call_args[1]
        assert '"lang": "ru"' in saved_json
        assert '"other_key": "value"' in saved_json


class TestGetRejectedAttemptsPage:
    @pytest.mark.asyncio
    async def test_returns_rejected_only(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 1
        pool.fetch.return_value = [
            {
                "id": 5,
                "chat_id": -100,
                "chat_title": "Rejected Chat",
                "chat_type": "supergroup",
                "user_id": 42,
                "user_first_name": "Mallory",
                "user_username": "mal",
                "message_text": "spam",
                "created_at": None,
            },
        ]

        attempts, total = await repo_.get_rejected_attempts_page(page=0)

        assert total == 1
        assert len(attempts) == 1
        assert attempts[0]["chat_title"] == "Rejected Chat"
        # Query must filter on status='rejected'
        query = pool.fetch.call_args[0][0]
        assert "'rejected'" in query
        # Excludes chats that are now enabled
        assert "enabled = true" in query

    @pytest.mark.asyncio
    async def test_empty_when_no_rejected(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 0
        pool.fetch.return_value = []

        attempts, total = await repo_.get_rejected_attempts_page(page=0)

        assert attempts == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_pagination_offset(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = 20
        pool.fetch.return_value = []

        await repo_.get_rejected_attempts_page(page=2, per_page=5)

        # Positional args: (per_page=5, offset=page*per_page=10)
        args = pool.fetch.call_args[0]
        assert args[1] == 5  # per_page
        assert args[2] == 10  # offset


class TestHasRejectedAttempt:
    @pytest.mark.asyncio
    async def test_true_when_exists(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = True

        assert await repo_.has_rejected_attempt(-100) is True
        query = pool.fetchval.call_args[0][0]
        assert "'rejected'" in query

    @pytest.mark.asyncio
    async def test_false_when_missing(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = False

        assert await repo_.has_rejected_attempt(-100) is False

    @pytest.mark.asyncio
    async def test_coerces_none_to_false(self, repo):
        repo_, pool = repo
        pool.fetchval.return_value = None

        assert await repo_.has_rejected_attempt(-100) is False


class TestDeleteAttempt:
    @pytest.mark.asyncio
    async def test_returns_true_when_deleted(self, repo):
        repo_, pool = repo
        pool.execute.return_value = "DELETE 1"

        assert await repo_.delete_attempt(42) is True
        query = pool.execute.call_args[0][0]
        assert "DELETE FROM unauthorized_attempts" in query

    @pytest.mark.asyncio
    async def test_returns_false_when_nothing_deleted(self, repo):
        repo_, pool = repo
        pool.execute.return_value = "DELETE 0"

        assert await repo_.delete_attempt(999) is False

    @pytest.mark.asyncio
    async def test_returns_false_on_empty_result(self, repo):
        repo_, pool = repo
        pool.execute.return_value = ""

        assert await repo_.delete_attempt(1) is False
