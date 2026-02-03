"""Tests for src.config — pure functions and Settings behavior."""

from src.config import ModuleConfig, deep_merge, load_yaml_config
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
