"""Data models for the custom rules engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, TypeGuard


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
    SET_REACTION = "set_reaction"


_VALID_RULE_TYPES = frozenset(RuleType)
_VALID_RULE_ACTIONS = frozenset(RuleAction)

MAX_WEIGHT: Final = 2**31 - 1
"""``custom_rules.weight`` is a PostgreSQL ``INT``: asyncpg refuses anything larger."""


def is_weight(value: object) -> TypeGuard[int]:
    """A weight a rule may be created with: a whole number from 1 to `MAX_WEIGHT`.

    ``True`` is an ``int`` to Python and not a number to the admin who typed it.
    """
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_WEIGHT


def stored_weight(value: object) -> int:
    """A stored rule's weight as the engine and its screen read it: 1 unless `is_weight`.

    The column is nullable with no CHECK, and the JSON prompt once stored 0, a
    negative number or null as typed. In a weight mode ``random.choices`` refuses
    weights that sum to 0 and ``max`` cannot compare None with a number: either
    raised out of `RuleEngine.evaluate` and silenced every rule of the message,
    the mandatory ones included.
    """
    return value if is_weight(value) else 1


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
