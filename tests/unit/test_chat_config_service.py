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

    def test_is_forum_comes_from_chat_row(self):
        """TD-102: chat metadata rides the per-chat layer; NULL (not yet
        observed) must read as False, not leak through as None."""
        service, _, _ = _make_service()

        config = service._merge(chat_id=1, global_overrides={}, chat_row={"is_forum": True})
        assert config.is_forum is True

        config = service._merge(chat_id=1, global_overrides={}, chat_row={"is_forum": None})
        assert config.is_forum is False

        config = service._merge(chat_id=1, global_overrides={}, chat_row=None)
        assert config.is_forum is False

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

    def test_kb_enabled_defaults_false(self):
        """kb_enabled (A3, ADR-0003) is opt-in per chat -- defaults to False."""
        service, _, _ = _make_service()
        config = service._merge(chat_id=1, global_overrides={}, chat_row=None)
        assert config.kb_enabled is False

    def test_kb_enabled_from_chat_row(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={},
            chat_row={"kb_enabled": True},
        )
        assert config.kb_enabled is True

    def test_kb_enabled_from_global_overrides(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={"kb_enabled": True},
            chat_row=None,
        )
        assert config.kb_enabled is True

    def test_reactions_enabled_defaults_false(self):
        """reactions_enabled (R-1, ADR-0004) is the master module toggle,
        opt-in per chat -- defaults to False like kb_enabled."""
        service, _, _ = _make_service()
        config = service._merge(chat_id=1, global_overrides={}, chat_row=None)
        assert config.reactions_enabled is False
        assert config.reactions_history_enabled is True  # dataclass default

    def test_reactions_enabled_from_chat_row(self):
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={},
            chat_row={"reactions_enabled": True},
        )
        assert config.reactions_enabled is True

    def test_reactions_history_enabled_can_be_disabled_independently(self):
        """reactions_history_enabled gates only the INSERT -- must be settable
        without touching reactions_enabled (ADR-0004 Decision 3)."""
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={},
            chat_row={"reactions_enabled": True, "reactions_history_enabled": False},
        )
        assert config.reactions_enabled is True
        assert config.reactions_history_enabled is False

    def test_tolerance_level_defaults_to_point_five(self):
        """ADR-0008 Decision 1/8: no bot_config seed row and no SQL DEFAULT --
        the dataclass default (0.5) is the only layer-1 fallback."""
        service, _, _ = _make_service()
        config = service._merge(chat_id=1, global_overrides={}, chat_row=None)
        assert config.tolerance_level == 0.5

    def test_tolerance_level_from_global_default(self):
        """bot_config.default_tolerance_level set, no per-chat override."""
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={"tolerance_level": 1.0},
            chat_row=None,
        )
        assert config.tolerance_level == 1.0

    def test_tolerance_level_per_chat_overrides_global(self):
        """Per-chat value wins over the global default when both are set
        (ADR-0008 Decision 1's stated three-layer precedent)."""
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={"tolerance_level": 1.0},
            chat_row={"tolerance_level": 0.2},
        )
        assert config.tolerance_level == 0.2

    def test_tolerance_level_null_chat_row_value_falls_back_to_global(self):
        """A chat_settings row that exists but has tolerance_level=NULL must
        NOT shadow the global default with None -- only non-None per-chat
        values participate in layer 3 (mirrors ``test_none_values_do_not_override``
        for the pre-existing fields; this is the tolerance-specific instance
        of the exact bug class migration 020 fixed)."""
        service, _, _ = _make_service()
        config = service._merge(
            chat_id=1,
            global_overrides={"tolerance_level": 0.8},
            chat_row={"tolerance_level": None},
        )
        assert config.tolerance_level == 0.8


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

    @pytest.mark.asyncio
    async def test_brand_new_chat_tolerance_defaults_to_point_five(self):
        """ADR-0008 D-4: a brand-new chat (no chat_settings row, no global
        override) resolves tolerance_level to 0.5 through the *full*
        get_config() path -- repo mocks stand in for "row absent"/"no global
        override", not just the dataclass default read in isolation."""
        service, bot_repo, chat_repo = _make_service()
        bot_repo.get_defaults.return_value = {}
        chat_repo.get.return_value = None

        config = await service.get_config(999)
        assert config.tolerance_level == 0.5

    @pytest.mark.asyncio
    async def test_get_config_three_layer_tolerance_precedence(self):
        """Full get_config() path, three scenarios in one test to pin the
        precedence order ADR-0008's D-4 notes name explicitly: per-chat wins
        over global; per-chat NULL falls back to global; both absent falls
        back to the dataclass default."""
        service, bot_repo, chat_repo = _make_service()

        # Global set, no per-chat row -> global wins.
        bot_repo.get_defaults.return_value = {"tolerance_level": 0.9}
        chat_repo.get.return_value = None
        config = await service.get_config(1)
        assert config.tolerance_level == 0.9

        # Per-chat override present -> per-chat wins over global.
        service.invalidate_all()
        chat_repo.get.return_value = {"tolerance_level": 0.1}
        config = await service.get_config(2)
        assert config.tolerance_level == 0.1

        # Per-chat row exists but tolerance_level is NULL -> falls back to global.
        service.invalidate_all()
        chat_repo.get.return_value = {"tolerance_level": None}
        config = await service.get_config(3)
        assert config.tolerance_level == 0.9

        # Neither layer set -> dataclass default.
        service.invalidate_all()
        bot_repo.get_defaults.return_value = {}
        chat_repo.get.return_value = None
        config = await service.get_config(4)
        assert config.tolerance_level == 0.5
