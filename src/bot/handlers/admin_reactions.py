"""The reactions area's router, now empty.

The reactions switches (``reactions_enabled``, ``reactions_history_enabled``)
are ordinary toggles in a chat's «Реакции» section, written by
``admin_chat_panel.handle_chat_panel_toggle`` like every other bool setting;
that section also carries the live "is the bot an admin" line (ADR-0004
Decision 5). The old picker and submenu (``adm_react:``, ``adm_react_menu:``,
``adm_react_toggle:``) are retired: a button drawn by them falls to the stale
catch-all, which shows the panel as it is now.

The router stays only because ``handlers/__init__.py`` includes it; it can go
together with that include. The reaction *updates* from chats are handled in
``handlers/reactions.py``, which keeps its own ``chat_config.enabled`` guard
(``AccessControlMiddleware`` does not understand ``message_reaction``).
"""

from __future__ import annotations

from aiogram import Router

router = Router(name="admin_reactions")
