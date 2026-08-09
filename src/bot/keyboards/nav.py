"""Back-navigation origin for the per-chat sub-panels (KB, Reactions).

Both submenus are reachable from two places: their own chat picker in the
admin menu (``adm_kb:`` / ``adm_react:``), and the chat settings panel's
link rows (ADR-0006 Decision 2). Their "Back" button was hardcoded to their
own picker, so entering from the panel and pressing Back dropped the admin
into a *different* section's chat list. Reported 2026-08-09; older than the
grouped panel (B-2), which only made it easy to hit by turning the panel
into the per-chat hub.

The entry point therefore rides along in ``callback_data`` as a trailing
token: ``p`` for the chat settings panel, absent for the section's own
picker. One character on purpose — ``adm_react_toggle:`` already spent 60 of
the 64 bytes Telegram allows on a callback payload, so there was no room for
a descriptive word (that toggle now uses the field registry's two-letter
codes for the same reason).
"""

from __future__ import annotations

PANEL_ORIGIN = "p"


def parse_origin(parts: list[str], index: int) -> str:
    """Read the optional origin token off an already-split ``callback_data``.

    Anything that isn't the panel token — a missing segment, a stale payload
    from before this existed, a forged one — reads as the default origin, so
    the worst case is the old behavior rather than a crash.
    """
    return PANEL_ORIGIN if len(parts) > index and parts[index] == PANEL_ORIGIN else ""


def origin_suffix(origin: str) -> str:
    """Render the origin back into ``callback_data`` (empty for the default)."""
    return f":{PANEL_ORIGIN}" if origin == PANEL_ORIGIN else ""


def back_callback(origin: str, *, lang: str, chat_id: int, default: str) -> str:
    """Back target for a sub-panel's root screen.

    ``default`` is the section's own picker — where Back went before, and
    still goes when the admin arrived from there.
    """
    if origin == PANEL_ORIGIN:
        return f"adm_pnl_menu:{lang}:{chat_id}"
    return default
