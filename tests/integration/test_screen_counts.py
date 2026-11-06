"""The panel's entry-button counts agree with the lists behind them.

A button that says "Заявки · 2" and opens a list of one is the failure this
guards: the count and the page are two queries, so the only proof they share a
predicate is running both on the same rows. Ids are obviously fake.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.admin import AdminRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.rules import RulesRepository


@pytest_asyncio.fixture
async def admin_repo(db_conn: asyncpg.Connection) -> AdminRepository:
    return AdminRepository(db_conn)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def settings_repo(db_conn: asyncpg.Connection) -> ChatSettingsRepository:
    return ChatSettingsRepository(db_conn)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def rules_repo(db_conn: asyncpg.Connection) -> RulesRepository:
    return RulesRepository(db_conn)  # type: ignore[arg-type]


async def _attempt(repo: AdminRepository, chat_id: int, status: str = "pending") -> int:
    attempt_id = await repo.log_unauthorized(chat_id=chat_id, chat_title="Тестовый чат", user_id=1)
    if status != "pending":
        await repo.update_attempt_status(attempt_id, status)
    return attempt_id


class TestCountAttempts:
    @pytest.mark.asyncio
    async def test_counts_by_status(self, admin_repo: AdminRepository) -> None:
        base_pending = await admin_repo.count_attempts("pending")
        base_rejected = await admin_repo.count_attempts("rejected")
        await _attempt(admin_repo, -400001)
        await _attempt(admin_repo, -400002)
        await _attempt(admin_repo, -400003, "rejected")

        assert await admin_repo.count_attempts("pending") == base_pending + 2
        assert await admin_repo.count_attempts("rejected") == base_rejected + 1

    @pytest.mark.asyncio
    async def test_enabled_chat_is_not_counted(
        self, admin_repo: AdminRepository, settings_repo: ChatSettingsRepository
    ) -> None:
        """The control: the same row counts until its chat is enabled."""
        await _attempt(admin_repo, -400011)
        before = await admin_repo.count_attempts("pending")
        await settings_repo.upsert(-400011, enabled=True)
        assert await admin_repo.count_attempts("pending") == before - 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["pending", "rejected"])
    async def test_count_equals_the_list_total(
        self,
        admin_repo: AdminRepository,
        settings_repo: ChatSettingsRepository,
        status: str,
    ) -> None:
        await _attempt(admin_repo, -400021, status)
        await _attempt(admin_repo, -400022, status)
        await _attempt(admin_repo, -400023, status)
        await settings_repo.upsert(-400023, enabled=True)

        page = (
            admin_repo.get_pending_attempts_page
            if status == "pending"
            else admin_repo.get_rejected_attempts_page
        )
        _, total = await page(0, 100)
        assert await admin_repo.count_attempts(status) == total
        assert total >= 2


class TestCountForChat:
    @pytest.mark.asyncio
    async def test_counts_every_rule_of_the_chat(self, rules_repo: RulesRepository) -> None:
        chat_id, other = -400101, -400102
        assert await rules_repo.count_for_chat(chat_id) == 0
        config = {"words": ["тест"]}
        first = await rules_repo.create(chat_id=chat_id, rule_type="keyword_trigger", config=config)
        await rules_repo.create(chat_id=chat_id, rule_type="keyword_trigger", config=config)
        await rules_repo.create(chat_id=other, rule_type="keyword_trigger", config=config)
        await rules_repo.toggle(first, enabled=False)

        assert await rules_repo.count_for_chat(chat_id) == 2, "a disabled rule still counts"
        _, total = await rules_repo.get_rules_page(chat_id, 0, 100)
        assert await rules_repo.count_for_chat(chat_id) == total
