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


def _make_settings(
    openai_api_key: str = "sk-test",
    openai_admin_api_key: str = "sk-admin-test",
    openai_project_id: str = "proj_test",
) -> MagicMock:
    """Mock Settings.

    Every field is set explicitly: a bare MagicMock returns a truthy attribute
    for anything, so an unset key would silently pass the presence checks the
    handler is supposed to enforce.
    """
    s = MagicMock()
    s.openai_api_key = openai_api_key
    s.openai_admin_api_key = openai_admin_api_key
    s.openai_project_id = openai_project_id
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
    async def test_no_admin_key_names_the_missing_setting(self):
        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings(openai_admin_api_key="")

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            await handle_costs_verify(cb, repo, settings, is_admin=True)

        MockClient.assert_not_called()
        text = cb.message.edit_text.call_args.args[0]
        assert "OPENAI_ADMIN_API_KEY" in text
        assert "OPENAI_PROJECT_ID" not in text  # that one IS set

    @pytest.mark.asyncio
    async def test_no_project_id_names_the_missing_setting(self):
        """The project id is required even when the admin key is present."""
        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings(openai_project_id="")

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            await handle_costs_verify(cb, repo, settings, is_admin=True)

        MockClient.assert_not_called()
        text = cb.message.edit_text.call_args.args[0]
        assert "OPENAI_PROJECT_ID" in text
        assert "OPENAI_ADMIN_API_KEY" not in text  # that one IS set

    @pytest.mark.asyncio
    async def test_both_missing_lists_both(self):
        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings(openai_admin_api_key="", openai_project_id="")

        await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "OPENAI_ADMIN_API_KEY" in text
        assert "OPENAI_PROJECT_ID" in text

    @pytest.mark.asyncio
    async def test_missing_setting_message_is_localised(self):
        cb = _make_callback("adm_costs_verify:ru:24h")
        repo = _make_response_log_repo()
        settings = _make_settings(openai_admin_api_key="")

        await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "не хватает" in text.lower()

    @pytest.mark.asyncio
    async def test_regular_key_alone_is_not_enough(self):
        """A project key does not stand in for the admin key.

        Regression guard: the handler used to send `openai_api_key` to the
        billing endpoint, which always 403s.
        """
        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings(openai_api_key="sk-proj-test", openai_admin_api_key="")

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            await handle_costs_verify(cb, repo, settings, is_admin=True)

        MockClient.assert_not_called()
        text = cb.message.edit_text.call_args.args[0]
        assert "OPENAI_ADMIN_API_KEY" in text

    @pytest.mark.asyncio
    async def test_uses_admin_key_and_passes_project_id(self):
        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings(
            openai_api_key="sk-proj-regular",
            openai_admin_api_key="sk-admin-billing",
            openai_project_id="proj_bot",
        )

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(
                return_value=OpenAICostReport(total_usd=Decimal("0.02"), buckets=[])
            )
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        MockClient.assert_called_once_with("sk-admin-billing")
        assert instance.get_costs.call_args.kwargs["project_id"] == "proj_bot"

    @pytest.mark.asyncio
    async def test_confirmed_scoping_names_the_project(self):
        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings(openai_project_id="proj_bot")

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(
                return_value=OpenAICostReport(
                    total_usd=Decimal("0.02"),
                    buckets=[],
                    project_ids_seen={"proj_bot"},
                )
            )
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "Scoped to project" in text
        assert "proj_bot" in text

    @pytest.mark.asyncio
    async def test_unconfirmed_scoping_says_so(self):
        """No per-project breakdown → the figure must not claim to be scoped."""
        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:en:24h")
        repo = _make_response_log_repo()
        settings = _make_settings(openai_project_id="proj_bot")

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(
                return_value=OpenAICostReport(
                    total_usd=Decimal("0.02"),
                    buckets=[],
                    project_ids_seen=set(),
                )
            )
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "could not be confirmed" in text
        assert "Scoped to project" not in text

    @pytest.mark.asyncio
    async def test_project_filter_ignored_is_localised(self):
        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:ru:24h")
        repo = _make_response_log_repo()
        settings = _make_settings()

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(
                return_value=OpenAICostReport(
                    total_usd=Decimal("0"),
                    buckets=[],
                    error="OpenAI ignored the project filter — figures would be org-wide",
                    error_code="project_filter_ignored",
                )
            )
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "фильтр не применился" in text
        assert "OPENAI_PROJECT_ID" in text

    @pytest.mark.asyncio
    async def test_api_error_shows_message(self):
        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:ru:24h")
        repo = _make_response_log_repo()
        settings = _make_settings()

        mock_report = OpenAICostReport(
            total_usd=Decimal("0"),
            buckets=[],
            error="API key lacks billing access (admin key required)",
            error_code="no_billing_access",
        )

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(return_value=mock_report)
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "403" in text
        assert "admin" in text.lower()

    @pytest.mark.asyncio
    async def test_unmapped_error_falls_back_to_english(self):
        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:ru:24h")
        repo = _make_response_log_repo()
        settings = _make_settings()

        mock_report = OpenAICostReport(
            total_usd=Decimal("0"),
            buckets=[],
            error="OpenAI API error: HTTP 500",
            error_code="http_error",
        )

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(return_value=mock_report)
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "HTTP 500" in text

    @pytest.mark.asyncio
    async def test_compares_over_window_openai_actually_returned(self):
        """Our own figure must span the buckets OpenAI sent, not the button.

        The smallest OpenAI bucket is a full day, so "1h" comes back covering
        far more; querying our own cost over 1h would subtract two different
        windows and show the gap as a costing error.
        """
        import time as _time
        from datetime import timedelta

        from src.services.ai.billing import OpenAICostBucket, OpenAICostReport

        cb = _make_callback("adm_costs_verify:en:1h")
        repo = _make_response_log_repo()
        settings = _make_settings()

        bucket_start = int(_time.time()) - 36 * 3600
        mock_report = OpenAICostReport(
            total_usd=Decimal("0.02"),
            buckets=[
                OpenAICostBucket(
                    start_time=bucket_start,
                    end_time=bucket_start + 86400,
                    amount_usd=Decimal("0.02"),
                )
            ],
            covered_from=bucket_start,
        )

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(return_value=mock_report)
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        used_interval = repo.get_cost_by_provider.call_args.args[0]
        assert used_interval > timedelta(hours=35)
        assert used_interval != timedelta(hours=1)

        text = cb.message.edit_text.call_args.args[0]
        assert "36h" in text

    @pytest.mark.asyncio
    async def test_falls_back_to_period_when_no_buckets(self):
        from datetime import timedelta

        from src.services.ai.billing import OpenAICostReport

        cb = _make_callback("adm_costs_verify:en:7d")
        repo = _make_response_log_repo()
        settings = _make_settings()

        with patch("src.services.ai.billing.OpenAIBillingClient") as MockClient:
            instance = AsyncMock()
            instance.get_costs = AsyncMock(
                return_value=OpenAICostReport(total_usd=Decimal("0"), buckets=[])
            )
            instance.close = AsyncMock()
            MockClient.return_value = instance

            await handle_costs_verify(cb, repo, settings, is_admin=True)

        assert repo.get_cost_by_provider.call_args.args[0] == timedelta(days=7)

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
