"""Tests for src.services.chat_config — merge logic and caching."""

from unittest.mock import AsyncMock

import pytest

from src.config import BotSettings
from src.models.chat_config import ChatConfig
from src.services.chat_config import ChatConfigService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    yaml_trigger_words=None,
    yaml_random_chance=0.05,
    yaml_min_interval=300,
    cache_ttl=60.0,
):
    """Create a ChatConfigService with mocked repos."""
    yaml_settings = BotSettings(
        trigger_words=yaml_trigger_words or ["bot", "бот"],
        random_response_chance=yaml_random_chance,
        random_response_min_interval=yaml_min_interval,
    )
    bot_config_repo = AsyncMock()
    chat_settings_repo = AsyncMock()

    service = ChatConfigService(
        yaml_settings=yaml_settings,
        bot_config_repo=bot_config_repo,
        chat_settings_repo=chat_settings_repo,
        cache_ttl=cache_ttl,
    )
    return service, bot_config_repo, chat_settings_repo


# ---------------------------------------------------------------------------
# _merge() tests — pure function, no I/O
# ---------------------------------------------------------------------------


class TestMerge:
    """Test ChatConfigService._merge() — three-layer merge logic."""

    def test_yaml_defaults_only(self):
        service, _, _ = _make_service(yaml_trigger_words=["hey"])
        config = service._merge(chat_id=1, global_overrides={}, chat_row=None)

        assert config.chat_id == 1
        assert config.trigger_words == ("hey",)
        assert config.random_response_chance == 0.05
        assert config.enabled is False  # dataclass default

    def test_global_overrides_yaml(self):
        service, _, _ = _make_service(yaml_trigger_words=["bot"])
        config = service._merge(
            chat_id=1,
            global_overrides={"trigger_words": ["hey", "yo"], "language": "en"},
            chat_row=None,
        )
        assert config.trigger_words == ("hey", "yo")
        assert config.language == "en"

    def test_per_chat_overrides_global(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={"random_response_chance": 0.1},
            chat_row={"random_response_chance": 0.5, "enabled": True},
        )
        assert config.random_response_chance == 0.5
        assert config.enabled is True

    def test_none_values_do_not_override(self):
        service, _, _ = _make_service(yaml_random_chance=0.05)
        config = service._merge(
            chat_id=1,
            global_overrides={"random_response_chance": 0.1},
            chat_row={"random_response_chance": None},
        )
        # None in chat_row should NOT override global
        assert config.random_response_chance == 0.1

    def test_enabled_from_chat_row(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={},
            chat_row={"enabled": True},
        )
        assert config.enabled is True

    def test_system_prompt_from_global(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={"system_prompt": "Be friendly"},
            chat_row=None,
        )
        assert config.system_prompt == "Be friendly"

    def test_per_chat_system_prompt_overrides_global(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={"system_prompt": "Be friendly"},
            chat_row={"system_prompt": "Be sarcastic"},
        )
        assert config.system_prompt == "Be sarcastic"

    def test_trigger_words_coerced_to_tuple(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={},
            chat_row={"trigger_words": ["a", "b"]},
        )
        assert config.trigger_words == ("a", "b")
        assert isinstance(config.trigger_words, tuple)

    def test_unknown_keys_in_chat_row_ignored(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={},
            chat_row={"unknown_field": "value", "enabled": True},
        )
        assert config.enabled is True

    def test_module_toggles(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={},
            chat_row={
                "rag_enabled": False,
                "abuse_filter_enabled": True,
                "sticker_response_chance": 0.3,
            },
        )
        assert config.rag_enabled is False
        assert config.abuse_filter_enabled is True
        assert config.sticker_response_chance == 0.3


# ---------------------------------------------------------------------------
# get_config() tests — caching behavior
# ---------------------------------------------------------------------------


class TestGetConfig:
    """Test ChatConfigService.get_config() — caching behavior."""

    @pytest.mark.asyncio
    async def test_returns_chat_config(self):
        service, bot_repo, chat_repo = _make_service()
        bot_repo.get_defaults.return_value = {}
        chat_repo.get.return_value = {"enabled": True}

        config = await service.get_config(123)
        assert isinstance(config, ChatConfig)
        assert config.chat_id == 123
        assert config.enabled is True

    @pytest.mark.asyncio
    async def test_cached_on_second_call(self):
        service, bot_repo, chat_repo = _make_service()
        bot_repo.get_defaults.return_value = {}
        chat_repo.get.return_value = None

        await service.get_config(123)
        await service.get_config(123)

        # chat_repo.get should only be called once
        chat_repo.get.assert_awaited_once_with(123)

    @pytest.mark.asyncio
    async def test_global_defaults_cached_across_chats(self):
        service, bot_repo, chat_repo = _make_service()
        bot_repo.get_defaults.return_value = {}
        chat_repo.get.return_value = None

        await service.get_config(100)
        await service.get_config(200)

        # Global defaults fetched only once
        bot_repo.get_defaults.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        service, bot_repo, chat_repo = _make_service(cache_ttl=0.01)
        bot_repo.get_defaults.return_value = {}
        chat_repo.get.return_value = None

        await service.get_config(123)

        # Wait for cache to expire
        import asyncio

        await asyncio.sleep(0.02)

        await service.get_config(123)
        assert chat_repo.get.await_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_clears_specific_chat(self):
        service, bot_repo, chat_repo = _make_service()
        bot_repo.get_defaults.return_value = {}
        chat_repo.get.return_value = None

        await service.get_config(123)
        service.invalidate(123)
        await service.get_config(123)

        assert chat_repo.get.await_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_all_clears_everything(self):
        service, bot_repo, chat_repo = _make_service()
        bot_repo.get_defaults.return_value = {}
        chat_repo.get.return_value = None

        await service.get_config(100)
        await service.get_config(200)
        service.invalidate_all()
        await service.get_config(100)

        # Global defaults re-fetched after invalidate_all
        assert bot_repo.get_defaults.await_count == 2
