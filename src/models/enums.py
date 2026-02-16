"""Enums for trigger types and response dispositions."""

from __future__ import annotations

from enum import StrEnum


class TriggerType(StrEnum):
    """Why the bot decided to respond."""

    TRIGGER = "trigger"
    REPLY = "reply"
    RANDOM = "random"
    NONE = "none"


class ResponseType(StrEnum):
    """Disposition returned by anti-abuse check."""

    NORMAL = "normal"
    JAILBREAK = "jailbreak"
    JAILBREAK_PENDING = "jailbreak_pending"
    BLACKLISTED = "blacklisted"
    BLACKLIST_NOTIFY = "blacklist_notify"
    COOLDOWN = "cooldown"
