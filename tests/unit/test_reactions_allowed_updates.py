"""`message_reaction` auto-registration into `allowed_updates` (QA-1, ADR-0004).

The source plan (docs/plans/reactions-2026-08-03.md §2) flags this as a "verify
by test, not by faith" risk: `dp.start_polling(bot)` in `src/main.py` is called
without an explicit `allowed_updates`, so aiogram derives the list from
registered observers via `Dispatcher.resolve_used_update_types()`. Registering
`@router.message_reaction()` (src/bot/handlers/reactions.py) is claimed to be
sufficient on its own -- no separate wiring needed. This test proves it against
the real `main_router` tree instead of trusting the comment in reactions.py.
"""

from __future__ import annotations

from aiogram import Dispatcher

from src.bot.handlers import router as main_router


class TestMessageReactionAutoRegistration:
    def test_message_reaction_is_in_resolved_update_types(self) -> None:
        dp = Dispatcher()
        dp.include_router(main_router)

        assert "message_reaction" in dp.resolve_used_update_types()

    def test_a_dispatcher_with_no_routers_does_not_claim_message_reaction(self) -> None:
        """Control: an empty dispatcher must NOT report message_reaction --
        otherwise this suite could pass for a reason unrelated to the
        reactions router (e.g. a library-level default)."""
        dp = Dispatcher()

        assert "message_reaction" not in dp.resolve_used_update_types()
