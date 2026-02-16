"""Shared utilities."""

from __future__ import annotations

from typing import Any


def parse_admin_ids(raw: Any) -> list[int]:
    """Parse admin IDs from bot_config value (str or list).

    BotConfigRepository.get() returns json.loads() result, which may be
    a comma-separated string ("123,456") or a JSON array ([123, 456]).
    This function handles both formats consistently.
    """
    if not raw:
        return []
    try:
        if isinstance(raw, list):
            return [int(x) for x in raw]
        if isinstance(raw, str):
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        return []
    except (ValueError, TypeError):
        return []
