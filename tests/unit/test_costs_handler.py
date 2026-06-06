"""Tests for admin panel costs handlers and /costs command."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.admin import handle_costs, handle_costs_command, handle_costs_verify

# ---------------------------------------------------------------------------
# Helpers (same pattern as test_admin_handler.py)
# ---------------------------------------------------------------------------


def _make_callback(
    data: str = "adm_costs:ru:24h",
    user_id: int = 12345,
) -> MagicMock:
    """Mock aiogram CallbackQuery."""
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.answer = AsyncMock()

    from aiogram.types import Message

    inner_msg = MagicMock(spec=Message)
    inner_msg.edit_text = AsyncMock()
    inner_msg.edit_reply_markup = AsyncMock()
    inner_msg.delete = AsyncMock()
    inner_msg.chat = MagicMock()
    inner_msg.chat.type = "private"
    callback.message = inner_msg

    return callback


def _make_response_log_repo(
    total_cost: Decimal = Decimal("0.0150"),
    by_task: list | None = None,
    by_model: list | None = None,
    by_provider: list | None = None,
) -> AsyncMock:
    repo = AsyncMock()
    repo.get_total_cost = AsyncMock(return_value=total_cost)
    repo.get_cost_by_task_type = AsyncMock(
        return_value=by_task
        or [
            {"task_type": "text", "call_count": 10, "total_cost": Decimal("0.0100")},
            {"task_type": "embedding", "call_count": 5, "total_cost": Decimal("0.0050")},
        ]
    )
    repo.get_cost_by_model = AsyncMock(
        return_value=by_model
        or [
            {
                "provider": "openai",
                "model": "gpt-5-nano",
                "task_type": "text",
                "call_count": 10,
                "total_cost": Decimal("0.0100"),
                "total_tokens_in": 5000,
                "total_tokens_out": 2000,
            }
        ]
    )
    repo.get_cost_by_provider = AsyncMock(
        return_value=by_provider
        or [
            {"provider": "openai", "call_count": 15, "total_cost": Decimal("0.0150")},
        ]
    )
    return repo


def _make_settings(openai_api_key: str = "sk-test") -> MagicMock:
    s = MagicMock()
    s.openai_api_key = openai_api_key
    return s


# ---------------------------------------------------------------------------
# handle_costs
# ---------------------------------------------------------------------------


class TestHandleCosts:
    @pytest.mark.asyncio
    async def test_shows_costs_default_24h(self):
        cb = _make_callback("adm_costs:ru:24h")
        repo = _make_response_log_repo()

        await handle_costs(cb, repo, is_admin=True)

        repo.get_total_cost.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "$0.0150" in text
        assert "gpt-5-nano" in text

    @pytest.mark.asyncio
    async def test_russian_labels(self):
        cb = _make_callback("adm_costs:ru:24h")
        repo = _make_response_log_repo()

        await handle_costs(cb, repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "Расходы" in text
        assert "Итого" in text

    @pytest.mark.asyncio
    async def test_english_labels(self):
        cb = _make_callback("adm_costs:en:24h")
        repo = _make_response_log_repo()

        await handle_costs(cb, repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "AI Costs" in text
        assert "Total" in text

    @pytest.mark.asyncio
    async def test_all_periods(self):
        for period in ("1h", "24h", "7d"):
            cb = _make_callback(f"adm_costs:en:{period}")
            repo = _make_response_log_repo()

            await handle_costs(cb, repo, is_admin=True)

            cb.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_costs:ru:24h")
        repo = _make_response_log_repo()

        await handle_costs(cb, repo, is_admin=False)

        repo.get_total_cost.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_match_verify_prefix(self):
        """adm_costs_verify should NOT be handled by handle_costs."""
        cb = _make_callback("adm_costs_verify:ru:24h")
        repo = _make_response_log_repo()

        await handle_costs(cb, repo, is_admin=True)

        # Should return early without querying
        repo.get_total_cost.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_costs_verify
# ---------------------------------------------------------------------------


class TestHandleCostsVerify:
    @pytest.mark.asyncio
    async def test_shows_comparison(self):
        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings("sk-test-admin")

        mock_report = OpenAICostReport(
            total_usd=Decimal("0.0200"),
            buckets=[],
        )

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(return_value=mock_report)
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "$0.0200" in text  # OpenAI reported
        assert "$0.0150" in text  # Our calculation

    @pytest.mark.asyncio
    async def test_no_api_key_shows_error(self):
        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings("")

        await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "not configured" in text.lower() or "не настроен" in text.lower()

    @pytest.mark.asyncio
    async def test_api_error_shows_message(self):
        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:ru:24h")
        repo = _make_response_log_repo()
        settings = _make_settings("sk-test")

        mock_report = OpenAICostReport(
            total_usd=Decimal("0"),
            buckets=[],
            error="API key lacks billing access (admin key required)",
        )

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(return_value=mock_report)
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "billing access" in text.lower()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        cb = _make_callback("adm_costs_verify:ru:24h")
        repo = _make_response_log_repo()
        settings = _make_settings()

        await handle_costs_verify(cb, repo, settings, is_admin=False)

        repo.get_cost_by_provider.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_costs_command (/costs DM command)
# ---------------------------------------------------------------------------


def _make_message_for_costs(
    chat_type: str = "private",
    user_id: int = 12345,
) -> MagicMock:
    """Mock aiogram Message for /costs command tests."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _make_admin_repo_for_costs(language: str = "ru") -> AsyncMock:
    """Mock AdminRepository for /costs tests."""
    repo = AsyncMock()
    repo.get_admin_language = AsyncMock(return_value=language)
    return repo


class TestHandleCostsCommand:
    @pytest.mark.asyncio
    async def test_shows_total_and_per_model_ru(self):
        """Russian admin sees total cost and per-model breakdown."""
        msg = _make_message_for_costs()
        repo = _make_response_log_repo()
        admin_repo = _make_admin_repo_for_costs("ru")
        bot_config_repo = AsyncMock()

        await handle_costs_command(msg, repo, admin_repo, bot_config_repo)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args.args[0]
        assert "Расходы" in text
        assert "Итого" in text
        assert "$0.0150" in text
        assert "gpt-5-nano" in text

    @pytest.mark.asyncio
    async def test_shows_total_and_per_model_en(self):
        """English admin sees total cost and per-model breakdown."""
        msg = _make_message_for_costs()
        repo = _make_response_log_repo()
        admin_repo = _make_admin_repo_for_costs("en")
        bot_config_repo = AsyncMock()

        await handle_costs_command(msg, repo, admin_repo, bot_config_repo)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args.args[0]
        assert "AI Costs" in text
        assert "Total" in text
        assert "$0.0150" in text

    @pytest.mark.asyncio
    async def test_includes_reply_markup(self):
        """Response carries inline keyboard for period navigation."""
        msg = _make_message_for_costs()
        repo = _make_response_log_repo()
        admin_repo = _make_admin_repo_for_costs("en")
        bot_config_repo = AsyncMock()

        await handle_costs_command(msg, repo, admin_repo, bot_config_repo)

        call_kwargs = msg.answer.call_args.kwargs
        assert call_kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_ignores_group_chat(self):
        """Handler silently returns if invoked outside a DM."""
        msg = _make_message_for_costs(chat_type="group")
        repo = _make_response_log_repo()
        admin_repo = _make_admin_repo_for_costs()
        bot_config_repo = AsyncMock()

        await handle_costs_command(msg, repo, admin_repo, bot_config_repo)

        msg.answer.assert_not_awaited()
        repo.get_total_cost.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_model_list_shows_only_total(self):
        """No rows in response_log → only total line, no by-model section."""
        msg = _make_message_for_costs()
        repo = _make_response_log_repo(total_cost=Decimal("0"))
        # Override directly — `[] or [default]` is truthy, so can't use helper arg
        repo.get_cost_by_model = AsyncMock(return_value=[])
        admin_repo = _make_admin_repo_for_costs("en")
        bot_config_repo = AsyncMock()

        await handle_costs_command(msg, repo, admin_repo, bot_config_repo)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args.args[0]
        assert "By model" not in text
        assert "$0.0000" in text

    @pytest.mark.asyncio
    async def test_uses_24h_interval(self):
        """Handler always queries with the 24h interval."""
        from datetime import timedelta

        msg = _make_message_for_costs()
        repo = _make_response_log_repo()
        admin_repo = _make_admin_repo_for_costs()
        bot_config_repo = AsyncMock()

        await handle_costs_command(msg, repo, admin_repo, bot_config_repo)

        call_args = repo.get_total_cost.call_args.args
        assert call_args[0] == timedelta(hours=24)
