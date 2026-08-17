"""Shared utilities."""

from __future__ import annotations

import json
from typing import Any


def parse_user_id_list(raw: Any) -> list[int]:
    """Parse a per-chat JSON list of user ids (`chat_settings.kb_organizer_ids`).

    Defensive because asyncpg hands a `jsonb` column back either already decoded
    or as a raw string depending on how it was written, and a malformed value
    must degrade to "nobody" rather than raise inside an authority check.

    Separate from `parse_admin_ids` on purpose: that one also accepts the legacy
    comma-separated `bot_config` spelling, which has never been a valid shape
    here. Shared by `handlers/commands.py` (the `/remember` authority gate) and
    `handlers/admin_kb.py` (the organizer management screen) — two readers of one
    column that had two independent copies of this parse, so a change to how
    organizers are stored had to be made twice with nothing enforcing it.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    try:
        return [int(v) for v in raw]
    except (ValueError, TypeError):
        return []


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
