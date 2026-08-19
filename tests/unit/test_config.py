"""Tests for src.config — pure functions and Settings behavior."""

from src.config import (
    EmbeddingBackfillSettings,
    ModuleConfig,
    Settings,
    deep_merge,
    load_yaml_config,
)
from src.services.ai.capabilities import EXPENSIVE_MODELS


class TestDeepMerge:
    """Tests for deep_merge() pure function."""

    def test_empty_dicts(self):
        assert deep_merge({}, {}) == {}

    def test_override_simple_values(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_does_not_mutate_base(self):
        base = {"a": 1}
        override = {"a": 2}
        deep_merge(base, override)
        assert base == {"a": 1}

    def test_nested_dict_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 1}
        override = {"a": {"y": 3, "z": 4}}
        result = deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}, "b": 1}

    def test_override_dict_with_scalar(self):
        base = {"a": {"x": 1}}
        override = {"a": "replaced"}
        result = deep_merge(base, override)
        assert result == {"a": "replaced"}

    def test_override_scalar_with_dict(self):
        base = {"a": 1}
        override = {"a": {"x": 2}}
        result = deep_merge(base, override)
        assert result == {"a": {"x": 2}}

    def test_deeply_nested(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result = deep_merge(base, override)
        assert result == {"a": {"b": {"c": 99, "d": 2}}}


class TestLoadYamlConfig:
    """Tests for load_yaml_config()."""

    def test_loads_default_yml(self):
        config = load_yaml_config()
        assert "bot" in config
        assert "ai" in config

    def test_bot_trigger_words_from_yaml(self):
        config = load_yaml_config()
        assert config["bot"]["trigger_words"] == ["bot", "бот"]

    def test_knowledge_base_module_disabled_by_default(self):
        """kb_enabled (A3, ADR-0003) is opt-in -- the module YAML default is off."""
        config = load_yaml_config()
        kb_module = config["modules"]["knowledge_base"]
        assert kb_module["enabled"] is False
        assert "embeddings" in kb_module["requires"]

    def test_transcription_model_sources_agree(self):
        """The default transcription model lives in three places: the YAML
        task config (what the router actually uses), DEFAULT_MODELS (the
        cost-policy registry tests audit), and the provider's hardcoded
        fallback for when task config is absent. Nothing at runtime forces
        them to match — this test does (deep-review 2026-08-19)."""
        import inspect

        from src.services.ai.capabilities import DEFAULT_MODELS
        from src.services.ai.providers.openai import OpenAIProvider

        config = load_yaml_config()
        yaml_model = config["ai"]["tasks"]["transcription"]["model"]
        assert yaml_model == DEFAULT_MODELS["openai"]["transcription"]

        # The provider fallback is a literal in transcribe_audio; assert on
        # the source rather than invoking the network path.
        source = inspect.getsource(OpenAIProvider.transcribe_audio)
        assert f'model or "{yaml_model}"' in source

    def test_default_config_uses_no_expensive_models(self):
        """Cost policy: default.yml must only use cheap models."""
        config = load_yaml_config()
        tasks = config.get("ai", {}).get("tasks", {})
        for task_name, task_config in tasks.items():
            model = task_config.get("model")
            if model:
                assert model not in EXPENSIVE_MODELS, (
                    f"Task '{task_name}' uses expensive model '{model}' in default.yml"
                )
            for fb_model in task_config.get("fallback_models", {}).values():
                assert fb_model not in EXPENSIVE_MODELS, (
                    f"Task '{task_name}' fallback uses expensive model '{fb_model}'"
                )

    def test_embeddings_has_no_fallback(self):
        """S2-1: embeddings has no fallback provider, declared honestly.

        A prior ``fallback: [openai]`` here was never a working reserve --
        ``fallback_models`` is parsed (``AITaskConfig``) but never read by
        ``AIRouter``, so a fallback call reused Gemini's model name
        (``gemini-embedding-001``) against OpenAI's API and always 404'd.
        Regression guard: this must stay empty until a second 768-dim-native
        embedding provider exists (see the comment in config/default.yml).
        """
        config = load_yaml_config()
        embeddings_task = config["ai"]["tasks"]["embeddings"]
        assert embeddings_task.get("fallback", []) == []
        assert embeddings_task.get("fallback_models", {}) == {}

    def test_relevancy_check_task_removed_as_dead_config(self):
        """S2-9/TD-058: ``relevancy_check`` was never read by ``AIRouter.generate_text()``

        (no per-task routing parameter -- provider/max_tokens/temperature were all
        ignored, see ``config/default.yml``'s comment). Removed rather than left as a
        knob that silently does nothing. Regression guard: don't let it creep back in
        without also wiring ``generate_text()`` to route by task (TD-058).
        """
        config = load_yaml_config()
        tasks = config.get("ai", {}).get("tasks", {})
        assert "relevancy_check" not in tasks

    def test_embedding_backfill_section_present_and_enabled(self):
        """S2-10: the background worker is on by default in default.yml."""
        config = load_yaml_config()
        backfill = config["embedding_backfill"]
        assert backfill["enabled"] is True
        assert backfill["interval_seconds"] == 3600
        assert backfill["batch_limit"] == 20


class TestEmbeddingBackfillSettings:
    """S2-10: defaults for the standalone settings class, and that Settings
    wires it in under settings.embedding_backfill (mirrors MaintenanceSettings)."""

    def test_defaults(self):
        config = EmbeddingBackfillSettings()
        assert config.enabled is True
        assert config.interval_seconds == 3600
        assert config.batch_limit == 20

    def test_settings_exposes_embedding_backfill(self, make_settings):
        settings: Settings = make_settings()
        assert isinstance(settings.embedding_backfill, EmbeddingBackfillSettings)
        assert settings.embedding_backfill.enabled is True


class TestSettings:
    """Tests for the Settings class."""

    def test_creates_with_required_fields(self, make_settings):
        s = make_settings()
        assert s.telegram_bot_token == "test-token"
        assert s.database_url == "postgresql://test:test@localhost/test"

    def test_optional_api_keys_default_to_none(self, make_settings):
        s = make_settings()
        assert s.openai_api_key is None
        assert s.gemini_api_key is None
        assert s.grok_api_key is None
        assert s.deepseek_api_key is None

    def test_custom_api_keys(self, make_settings):
        s = make_settings(openai_api_key="sk-test-123")
        assert s.openai_api_key == "sk-test-123"

    def test_default_bot_settings(self, make_settings):
        s = make_settings()
        assert s.bot.random_response_chance == 0.05


class TestIsModuleEnabled:
    """Tests for Settings.is_module_enabled()."""

    def test_returns_false_for_unknown_module(self, make_settings):
        s = make_settings()
        assert s.is_module_enabled("nonexistent") is False

    def test_returns_true_for_enabled_module(self, make_settings):
        s = make_settings(modules={"voice": ModuleConfig(enabled=True)})
        assert s.is_module_enabled("voice") is True

    def test_returns_false_for_disabled_module(self, make_settings):
        s = make_settings(modules={"voice": ModuleConfig(enabled=False)})
        assert s.is_module_enabled("voice") is False

    def test_checks_capability_requirements(self, make_settings):
        s = make_settings(
            modules={"sticker": ModuleConfig(enabled=True, requires=["vision", "embeddings"])}
        )
        # _has_capability is a stub that returns True
        assert s.is_module_enabled("sticker") is True
