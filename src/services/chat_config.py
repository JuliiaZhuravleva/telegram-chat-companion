"""
ChatConfigService — resolves effective per-chat configuration.

Three-layer merge (later wins):
1. YAML defaults (from config/default.yml via BotSettings)
2. Global DB overrides (from bot_config table, keys with default_ prefix)
3. Per-chat overrides (from chat_settings table row)

Results are cached with a simple TTL to avoid hitting the DB on every message.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any

import structlog

from src.config import BotSettings
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.models.chat_config import ChatConfig

logger = structlog.get_logger()


@dataclass
class _CacheEntry:
    config: ChatConfig
    expires_at: float


class ChatConfigService:
    """Resolves and caches effective per-chat configuration."""

    def __init__(
        self,
        yaml_settings: BotSettings,
        bot_config_repo: BotConfigRepository,
        chat_settings_repo: ChatSettingsRepository,
        cache_ttl: float = 60.0,
    ) -> None:
        self._yaml = yaml_settings
        self._bot_config_repo = bot_config_repo
        self._chat_settings_repo = chat_settings_repo
        self._cache_ttl = cache_ttl

        # Per-chat cache
        self._cache: dict[int, _CacheEntry] = {}

        # Global defaults cache (shared across all chats)
        self._global_cache: dict[str, Any] | None = None
        self._global_expires: float = 0.0

    async def get_config(self, chat_id: int) -> ChatConfig:
        """Get effective config for a chat (cached)."""
        now = monotonic()

        # Check per-chat cache
        entry = self._cache.get(chat_id)
        if entry is not None and entry.expires_at > now:
            return entry.config

        # Refresh global defaults if expired
        if self._global_cache is None or self._global_expires <= now:
            self._global_cache = await self._bot_config_repo.get_defaults()
            self._global_expires = now + self._cache_ttl

        # Fetch per-chat overrides
        chat_row = await self._chat_settings_repo.get(chat_id)

        # Merge three layers
        config = self._merge(chat_id, self._global_cache, chat_row)

        # Cache result
        self._cache[chat_id] = _CacheEntry(config=config, expires_at=now + self._cache_ttl)
        return config

    def is_cached(self, chat_id: int) -> bool:
        """Check if config for a chat is currently cached and valid."""
        entry = self._cache.get(chat_id)
        return entry is not None and entry.expires_at > monotonic()

    def invalidate(self, chat_id: int) -> None:
        """Invalidate cache for a specific chat."""
        self._cache.pop(chat_id, None)

    def invalidate_all(self) -> None:
        """Invalidate all caches (e.g. after global config change)."""
        self._cache.clear()
        self._global_cache = None
        self._global_expires = 0.0

    def _merge(
        self,
        chat_id: int,
        global_overrides: dict[str, Any],
        chat_row: dict[str, Any] | None,
    ) -> ChatConfig:
        """Merge three layers into a ChatConfig.

        This is a pure function (no I/O) — easy to unit test.
        Layer 1 (YAML) is read from self._yaml at call time.
        """
        # Layer 1: start with YAML defaults
        merged: dict[str, Any] = {
            "chat_id": chat_id,
            "trigger_words": tuple(self._yaml.trigger_words),
            "random_response_chance": self._yaml.random_response_chance,
            "random_response_min_interval": self._yaml.random_response_min_interval,
            "relevancy_gate_enabled": self._yaml.relevancy_gate_enabled,
        }

        # Layer 2: global DB overrides (only non-None values)
        for key, value in global_overrides.items():
            if value is not None and key in _CHAT_CONFIG_FIELDS:
                merged[key] = _coerce(key, value)

        # Layer 3: per-chat DB overrides
        if chat_row is not None:
            for key in _CHAT_CONFIG_FIELDS:
                if key in chat_row and chat_row[key] is not None:
                    merged[key] = _coerce(key, chat_row[key])

        return ChatConfig(**merged)


# Fields on ChatConfig that can come from DB layers
_CHAT_CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "enabled",
        "trigger_words",
        "random_response_chance",
        "random_response_min_interval",
        "system_prompt",
        "language",
        "rag_enabled",
        "transcribe_voice",
        "transcribe_video_notes",
        "abuse_filter_enabled",
        "sticker_learning_enabled",
        "sticker_response_chance",
        "sticker_reply_to_sticker_enabled",
        "sticker_reply_to_sticker_chance",
        "image_comment_sticker_enabled",
        "image_comment_sticker_chance",
        "image_analysis_enabled",
        "save_messages",
        "rules_enabled",
        "rules_mode",
        "link_comments_enabled",
        "relevancy_gate_enabled",
    }
)


def _coerce(key: str, value: Any) -> Any:
    """Normalize DB values to match ChatConfig field types."""
    if key == "trigger_words":
        # DB stores TEXT[] (list), ChatConfig expects tuple
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return (value,)
    return value
