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

    # Sticker responses
    sticker_reply_to_sticker_enabled: bool = True
    sticker_reply_to_sticker_chance: float = 0.5
    image_comment_sticker_enabled: bool = True
    image_comment_sticker_chance: float = 0.3

    # Sticker explicitness gating (ADR-0008). Ceiling on acceptable
    # explicitness_score (0.0 = strictest, 1.0 = anarchy/no restriction).
    # Layer-1 fallback used whenever neither bot_config.default_tolerance_level
    # nor a per-chat chat_settings.tolerance_level override is set.
    tolerance_level: float = 0.5

    # Link comments
    link_comments_enabled: bool = False

    # Relevancy gate (filters random responses for natural participation)
    relevancy_gate_enabled: bool = True

    # Chunk retrieval (S5b): whether the conversation-chunk index is SEARCHED
    # for this chat. Independent of the two gates around it, so all four
    # combinations are expressible: `save_messages` decides whether there is
    # anything to chunk, `rag_enabled` decides whether the Q&A store is
    # searched, and this decides whether the chunk store is. Off by default —
    # the slice that added the read path must not also switch it on, since
    # merging to main is a production release.
    chunks_enabled: bool = False

    # Knowledge Base (opt-in per chat, ADR-0003)
    kb_enabled: bool = False

    # Reactions (opt-in per chat, ADR-0004). reactions_enabled is the master
    # module toggle for the bot's AUTONOMOUS reactions (R-5's LLM-driven path
    # and its successors); reactions_history_enabled gates only the
    # message_reactions INSERT, so an owner can keep the feature without
    # behavioral logging. Exception: a custom rule with action=set_reaction
    # is an explicit admin instruction and is gated by rules_enabled instead
    # (see RuleActionExecutor._set_reaction).
    reactions_enabled: bool = False
    reactions_history_enabled: bool = True
