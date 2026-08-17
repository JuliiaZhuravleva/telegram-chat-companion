"""Router-order regression: admin's own DM sticker (B-1) must be intercepted
by admin_sticker.handle_admin_sticker_check BEFORE media.py's silent
auto-learn (handle_sticker_message) ever sees it.

B-1's own last_update note flagged this as unverified: "QA follow-up: B-2
should add a live-checklist regression asserting media.py
handle_sticker_message is NOT invoked for this path (router-order proof)."
This file is that proof.

It drives the REAL `main_router` (src/bot/handlers/__init__.py) via
`Router.propagate_event()` -- not a call to either handler function directly
-- so a future re-ordering of `router.include_router(...)` calls, or a
filter change on either handler, is caught here instead of silently
reintroducing the double-learn/duplicate-notification bug B-1 fixed.

DI note: both handlers use dishka's `FromDishka[...]` for repo/service
params, but dishka only resolves those when `setup_dishka()` wraps the
dispatcher (real bot startup, src/main.py). Un-wrapped, aiogram's own
kwarg-injection matches purely by *parameter name* against the `data` dict
passed to `propagate_event()` -- so supplying mocks under the exact
parameter names (`sticker_repo=`, `sticker_service=`, ...) exercises the
real routers/filters without needing a live dishka container. `IsAdmin()`
is bypassed the same way real `AccessControlMiddleware` output would be
consumed, via its documented `is_admin` kwargs fast-path
(src/bot/filters/admin.py) -- so the filter object under test is the real
one, only its DB-backed slow path is skipped.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import router as main_router

_SAMPLE_STICKER_ROW = {
    "file_id": "CAACAgIAAxkB",
    "file_unique_id": "AgADvh4AAlkbCFI",
    "set_name": "test_set",
    "visual_description": "happy cat",
    "emotion": None,
    "character_or_meme": None,
    "suggested_contexts": None,
    "total_uses": 1,
    "bot_uses": 0,
    "emoji": "😺",
    "is_animated": False,
    "is_video": False,
    "analysis_failed": False,
}


def _make_router_message(
    *,
    is_admin: bool,
    chat_type: str = "private",
    sticker_known: bool = False,
) -> tuple[MagicMock, dict[str, object]]:
    """Build a MagicMock aiogram Message + the propagate_event() data dict.

    Sets every filter-relevant attribute the routers between admin_sticker
    and media.py read (sticker/text/caption/reply_to_message/voice/
    video_note/photo) so the event resolves through the REAL router/filter
    chain instead of accidentally matching an unrelated earlier handler
    (e.g. handle_voice_message's `F.voice | F.video_note`, which is truthy
    for an un-configured MagicMock attribute and would short-circuit the
    whole test before it reaches either sticker handler).
    """
    sticker = MagicMock(
        file_unique_id="AgADvh4AAlkbCFI",
        file_id="CAACAgIAAxkB",
        set_name="test_set",
        emoji="😺",
        is_animated=False,
        is_video=False,
    )

    msg = MagicMock()
    msg.sticker = sticker
    msg.text = None
    msg.caption = None
    msg.reply_to_message = None
    msg.voice = None
    msg.video_note = None
    msg.photo = None
    # Same trap as `voice`/`video_note` above, one router earlier:
    # chat_events' migration handler filters on
    # `F.migrate_to_chat_id | F.migrate_from_chat_id`, and an unset MagicMock
    # attribute is truthy — so an ordinary sticker would be consumed as a
    # group→supergroup migration. A real aiogram Message has both as None.
    msg.migrate_to_chat_id = None
    msg.migrate_from_chat_id = None
    msg.message_id = 1
    msg.chat = MagicMock()
    msg.chat.id = 555
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = 555
    msg.reply = AsyncMock()

    sticker_repo = MagicMock()
    sticker_repo.get_by_file_unique_id = AsyncMock(
        return_value=dict(_SAMPLE_STICKER_ROW) if sticker_known else None
    )
    sticker_repo.get_sticker_set = AsyncMock(return_value={"set_name": "test_set"})

    admin_repo = MagicMock()
    admin_repo.get_admin_language = AsyncMock(return_value="ru")

    bot_config_repo = MagicMock()
    bot_config_repo.get = AsyncMock(return_value="555")
    # A-1 added _resolve_default_tolerance_level(), which awaits get_defaults()
    # on this repo. An un-stubbed attribute is a plain MagicMock and raises
    # TypeError inside the handler under test, which reads as a routing failure
    # rather than a fixture gap. Empty dict == no admin-set default, so the
    # handler falls back to ChatConfig's 0.5 (ADR-0008).
    bot_config_repo.get_defaults = AsyncMock(return_value={})

    chat_config = MagicMock()
    chat_config.sticker_learning_enabled = True
    chat_config.sticker_reply_to_sticker_enabled = False
    chat_config.image_analysis_enabled = False

    sticker_service = MagicMock()
    sticker_service.learn = AsyncMock()

    sticker_responder = MagicMock()
    message_repo = MagicMock()
    message_repo.get_recent = AsyncMock(return_value=[])

    bot = MagicMock()

    data: dict[str, object] = {
        "is_admin": is_admin,
        "sticker_repo": sticker_repo,
        "admin_repo": admin_repo,
        "bot_config_repo": bot_config_repo,
        "chat_config": chat_config,
        "sticker_service": sticker_service,
        "sticker_responder": sticker_responder,
        "message_repo": message_repo,
        "bot": bot,
        "message_thread_id": None,
        "event_from_user": msg.from_user,
    }
    return msg, data


class TestAdminDmStickerRouterOrder:
    """Real-router proof that admin_sticker's DM check pre-empts media.py's
    silent auto-learn for the admin's own private-chat sticker (B-1), and
    that this only happens for the admin+private combination it's scoped to.
    """

    @pytest.mark.asyncio()
    async def test_admin_private_unknown_sticker_hits_admin_check_not_media_learn(
        self,
    ) -> None:
        """Positive, not-found branch: admin_sticker replies with the
        "not in catalog" + Analyze-button copy; media.py's sticker_service
        .learn() (the silent auto-learn call) never fires."""
        msg, data = _make_router_message(is_admin=True, chat_type="private", sticker_known=False)

        with patch(
            "src.bot.handlers.media.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await main_router.propagate_event("message", msg, **data)

        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "нет в базе" in reply_text  # admin_sticker.py's not-found copy
        sticker_service = data["sticker_service"]
        assert isinstance(sticker_service, MagicMock)
        sticker_service.learn.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_admin_private_known_sticker_hits_admin_check_not_media_learn(
        self,
    ) -> None:
        """Same proof for the found branch (sticker already in the catalog)."""
        msg, data = _make_router_message(is_admin=True, chat_type="private", sticker_known=True)

        with patch(
            "src.bot.handlers.media.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await main_router.propagate_event("message", msg, **data)

        msg.reply.assert_awaited_once()
        assert "happy cat" in msg.reply.call_args[0][0]
        sticker_service = data["sticker_service"]
        assert isinstance(sticker_service, MagicMock)
        sticker_service.learn.assert_not_awaited()

    # ── negative controls ────────────────────────────────────────────────
    # A green "learn() not awaited" above only means something if the same
    # scaffolding can produce an awaited call for a case that should NOT be
    # intercepted -- otherwise the assertion could be vacuously true (e.g.
    # sticker_service wired wrong, or media.py unreachable for any input).

    @pytest.mark.asyncio()
    async def test_non_admin_private_sticker_falls_through_to_media_learn(self) -> None:
        """Control: a non-admin's private-chat sticker is not the admin's
        own DM check -- IsAdmin() rejects it, so the event must fall
        through to media.py's ordinary auto-learn path."""
        msg, data = _make_router_message(is_admin=False, chat_type="private")

        with patch(
            "src.bot.handlers.media.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await main_router.propagate_event("message", msg, **data)

        msg.reply.assert_not_awaited()
        sticker_service = data["sticker_service"]
        assert isinstance(sticker_service, MagicMock)
        sticker_service.learn.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_admin_group_chat_sticker_falls_through_to_media_learn(self) -> None:
        """Control: the admin's sticker in a GROUP chat is not a DM check
        (admin_sticker's handler requires F.chat.type == 'private') -- must
        still fall through to media.py's ordinary group-chat learn."""
        msg, data = _make_router_message(is_admin=True, chat_type="group")

        with patch(
            "src.bot.handlers.media.download_telegram_file",
            new=AsyncMock(return_value=b"fake-bytes"),
        ):
            await main_router.propagate_event("message", msg, **data)

        msg.reply.assert_not_awaited()
        sticker_service = data["sticker_service"]
        assert isinstance(sticker_service, MagicMock)
        sticker_service.learn.assert_awaited_once()

    def test_admin_sticker_router_registered_before_media_router(self) -> None:
        """Structural guard mirroring the comment in handlers/__init__.py:
        even if a future filter edit breaks the behavioral tests above in a
        way that stops catching it, this pins admin_sticker's earlier
        registration slot directly."""
        from src.bot.handlers.admin_sticker import router as admin_sticker_router
        from src.bot.handlers.media import router as media_router

        sub_routers = main_router.sub_routers
        assert sub_routers.index(admin_sticker_router) < sub_routers.index(media_router)
