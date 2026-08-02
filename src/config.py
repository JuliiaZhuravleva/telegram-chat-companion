"""
Configuration management using pydantic-settings.

Loads settings from:
1. config/default.yml (defaults)
2. config/local.yml (local overrides, gitignored)
3. Environment variables (highest priority)
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def load_yaml_config() -> dict[str, Any]:
    """Load configuration from YAML files."""
    config_dir = Path(__file__).parent.parent / "config"
    config: dict[str, Any] = {}

    # Load default config
    default_path = config_dir / "default.yml"
    if default_path.exists():
        with open(default_path) as f:
            config = yaml.safe_load(f) or {}

    # Load local overrides (gitignored)
    local_path = config_dir / "local.yml"
    if local_path.exists():
        with open(local_path) as f:
            local_config = yaml.safe_load(f) or {}
            config = deep_merge(config, local_config)

    return config


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class LoggingSettings(BaseSettings):
    """Logging configuration."""

    level: str = "INFO"
    format: str = "json"


class BotSettings(BaseSettings):
    """Bot behavior settings."""

    trigger_words: list[str] = Field(default_factory=lambda: ["bot", "бот"])
    random_response_chance: float = 0.05
    random_response_min_interval: int = 300
    relevancy_gate_enabled: bool = True


class RAGSettings(BaseSettings):
    """RAG memory settings."""

    min_similarity: float = 0.7
    max_results: int = 5
    default_importance: float = 0.5


class AITaskConfig(BaseSettings):
    """Configuration for a specific AI task."""

    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    fallback: list[str] = Field(default_factory=list)
    fallback_models: dict[str, str] = Field(default_factory=dict)
    max_tokens: int = 500
    temperature: float = 0.9
    dimensions: int | None = None  # For embeddings


class AISettings(BaseSettings):
    """AI provider settings."""

    default_provider: str = "gemini"
    tasks: dict[str, AITaskConfig] = Field(default_factory=dict)


class ModuleConfig(BaseSettings):
    """Configuration for a module."""

    model_config = SettingsConfigDict(extra="allow")

    enabled: bool = True
    requires: list[str] = Field(default_factory=list)


class MaintenanceSettings(BaseSettings):
    """Data retention windows for the periodic cleanup task.

    Mirrors the reference bot's ``periodic_cleanup()`` (see
    internal/n8n-reference/data/data-lifecycle.md).  Any window set to ``None``
    disables cleanup for that table, so an operator can always opt out of
    deleting a specific kind of data.
    """

    enabled: bool = True
    interval_seconds: int = 3600  # hourly; user_activity has a 1-hour window

    # Ephemeral: rebuilt continuously, only used for spam/velocity windows.
    user_activity_hours: int | None = 1

    # Deliberately longer than the reference bot's 30 days: the n8n migration
    # carries ~36k historical messages over and they must survive the first
    # cleanup pass.  Raise further before migrating if older history matters.
    chat_messages_days: int | None = 365

    # Backs /costs analytics (a Python-only feature the reference bot lacked),
    # so kept far longer than n8n's 7-day debug-log window.
    response_log_days: int | None = 90

    # NOT pruned, deliberately — unlike the reference bot, this table is not a
    # log here, it is state.  `AdminRepository.has_rejected_attempt()` asks it
    # "was this chat ever rejected?" with no time bound, and
    # `AccessControlMiddleware` short-circuits on the answer.  A rejected row IS
    # the ban record, so ageing it out silently un-bans the chat: the bot starts
    # notifying the admin about it again, having lost the evidence that someone
    # already decided.  n8n could prune at 30 days because it recorded the
    # decision in chat_settings.chat_status instead.  Volume is a non-issue —
    # the live migration carried 147 rows for ~6 months of production.
    unauthorized_attempts_days: int | None = None

    abuse_blocked_log_days: int | None = 30


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required settings (from environment)
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    database_url: str = Field(alias="DATABASE_URL")

    # Optional API keys (from environment)
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    grok_api_key: str | None = Field(default=None, alias="GROK_API_KEY")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    youtube_api_key: str | None = Field(default=None, alias="YOUTUBE_API_KEY")

    # Nested settings (from YAML)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    bot: BotSettings = Field(default_factory=BotSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    ai: AISettings = Field(default_factory=AISettings)
    maintenance: MaintenanceSettings = Field(default_factory=MaintenanceSettings)
    modules: dict[str, ModuleConfig] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        # Load YAML config first
        yaml_config = load_yaml_config()

        # Merge YAML config into data (env vars take precedence)
        for key, value in yaml_config.items():
            if key not in data:
                data[key] = value

        super().__init__(**data)

    def is_module_enabled(self, module_name: str) -> bool:
        """Check if a module is enabled and has required capabilities."""
        module = self.modules.get(module_name)
        if module is None:
            return False
        if not module.enabled:
            return False

        # Check if required AI capabilities are available
        return all(self._has_capability(cap) for cap in module.requires)

    def _has_capability(self, capability: str) -> bool:  # noqa: ARG002
        """Check if any configured provider supports the capability."""
        # Stub — will be wired to AI provider system
        return True


# Global settings instance
settings = Settings()
