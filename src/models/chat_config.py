"""
Effective per-chat configuration after three-layer merge.

Merge order (later wins):
1. YAML defaults (config/default.yml via Settings)
2. Global DB overrides (bot_config table)
3. Per-chat overrides (chat_settings table)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatConfig:
    """Resolved configuration for a specific chat."""

    chat_id: int

    # Whitelist
    enabled: bool = False

    # Behavior
    trigger_words: tuple[str, ...] = ("bot", "бот")
    random_response_chance: float = 0.05
    random_response_min_interval: int = 300
    system_prompt: str = ""
    language: str = "ru"

    # Module toggles
    rag_enabled: bool = True
    transcribe_voice: bool = True
    transcribe_video_notes: bool = True
    abuse_filter_enabled: bool = False
    sticker_learning_enabled: bool = False
    sticker_response_chance: float = 0.15
    image_analysis_enabled: bool = True
    save_messages: bool = True

    # Rules engine
    rules_enabled: bool = False
    rules_mode: str = "all"

    # Link comments
    link_comments_enabled: bool = False
