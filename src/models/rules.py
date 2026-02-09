"""Data models for the custom rules engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RuleType(StrEnum):
    KEYWORD_TRIGGER = "keyword_trigger"
    USER_SPECIFIC = "user_specific"
    STICKER_FLOOD = "sticker_flood"
    SPAM_DETECT = "spam_detect"


class RulesMode(StrEnum):
    ALL = "all"
    HIGHEST_WEIGHT = "highest_weight"
    WEIGHTED_RANDOM = "weighted_random"


class RuleAction(StrEnum):
    NOTIFY_ADMIN = "notify_admin"
    WARN_USER = "warn_user"
    CUSTOM_RESPONSE = "custom_response"


_VALID_RULE_TYPES = frozenset(RuleType)
_VALID_RULE_ACTIONS = frozenset(RuleAction)


@dataclass(frozen=True)
class Rule:
    """A single custom rule loaded from DB."""

    id: int
    chat_id: int
    rule_type: RuleType
    config: dict[str, Any]
    weight: int = 1
    mandatory: bool = False
    enabled: bool = True
    status: str = "active"
    trigger_count: int = 0


@dataclass
class RuleMatch:
    """Result of evaluating a single rule against a message."""

    rule: Rule
    triggered: bool
    match_detail: str = ""
