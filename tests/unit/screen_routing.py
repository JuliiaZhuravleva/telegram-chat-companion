"""Where a callback payload lands in the REAL handler tree, without a bot or a database.

Shared by ``test_screen_rules.py`` (lint R7) and ``test_routes.py``. The walk
itself lives in `src.bot.screens.lint.first_handler` so ``scripts/dump_screens.py``
can use it too; this module only binds it to ``src.bot.handlers.router``, the
router ``main.py`` includes into the dispatcher.

What it proves and what it does not: it evaluates each handler's registered
filters in aiogram's order (own handlers, then sub-routers in include order),
so it answers "which handler would consume this payload". Async filters
(``IsAdmin``, ``StateFilter``) count as passing — they need a container or an
FSM context the walk does not have. It never calls a handler.
"""

from __future__ import annotations

from src.bot.handlers import router as main_router
from src.bot.screens.lint import first_handler


def first_match(data: str) -> str | None:
    """``"module.function"`` of the first callback handler ``data`` reaches, or None."""
    return first_handler(main_router, data)
