"""A chat must become visible to the admin the moment it wants in (TD-025).

Adding the bot to a group queued nothing and notified nobody: the chat sat in
`chat_settings` with `enabled = false`, absent from the «Ожидают» tab (which
reads `unauthorized_attempts`), until some human happened to post there. The
log line was the worst part — it asserted "awaiting whitelist approval" while
nothing had been enqueued for approval.

Two families of test here, and the second is the one that matters most. Making
a new event file access requests is easy; making it file them for the RIGHT
events is where this goes wrong, because Telegram delivers `my_chat_member`
for things that are not joins at all.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from src.bot.access_requests import (
    NOTIFY_COOLDOWN_SECONDS,
    AccessRequest,
    AccessRequestService,
    NotifyCooldown,
    SubmitResult,
)
from src.bot.handlers.chat_events import handle_my_chat_member
from src.services.abuse.notifications import AbuseNotificationService

GROUP_ID = -1009999992001
ADMIN_ID = 500042


def _make_member_update(
    *,
    status: str = "member",
    old_status: str = "left",
    chat_type: str = "supergroup",
    chat_id: int = GROUP_ID,
) -> MagicMock:
    event = MagicMock()
    event.chat = MagicMock()
    event.chat.id = chat_id
    event.chat.title = "A group"
    event.chat.full_name = "A group"
    event.chat.type = chat_type
    event.chat.username = None
    event.new_chat_member = MagicMock()
    event.new_chat_member.status = status
    event.old_chat_member = MagicMock()
    event.old_chat_member.status = old_status
    event.from_user = MagicMock()
    event.from_user.id = 500001
    event.from_user.first_name = "Adder"
    event.from_user.last_name = None
    event.from_user.username = "adder"
    return event


def _make_repos(*, enabled: bool = False) -> tuple[AsyncMock, MagicMock]:
    repo = AsyncMock()
    repo.ensure_exists = AsyncMock(return_value={"chat_id": GROUP_ID, "enabled": enabled})
    repo.set_field = AsyncMock()
    return repo, MagicMock()


def _make_service() -> AsyncMock:
    service = AsyncMock()
    service.submit = AsyncMock(return_value=SubmitResult(attempt_id=7, notified=True))
    return service


class TestAddingTheBotQueuesTheChat:
    @pytest.mark.asyncio
    async def test_a_fresh_group_files_an_access_request(self) -> None:
        repo, config_service = _make_repos()
        access_requests = _make_service()

        await handle_my_chat_member(
            _make_member_update(), repo, config_service, access_requests, MagicMock()
        )

        access_requests.submit.assert_awaited_once()
        request = access_requests.submit.await_args.args[0]
        assert request.chat_id == GROUP_ID
        assert request.reason == "added", (
            "the card must say the bot was ADDED — calling it an access attempt "
            "sends the admin looking for a message that does not exist"
        )
        assert request.user_id == 500001, "record who added the bot, not nobody"

    @pytest.mark.asyncio
    async def test_the_log_no_longer_claims_approval_was_queued_when_it_was_not(self) -> None:
        """TD-025 *was* this sentence being false.

        `access_request_id` proves the DB row; `notified` is the separate
        question of whether a human heard about it. Collapsing them is how a
        dead notify path reads as healthy — the five-day-silent-transcription
        shape.
        """
        repo, config_service = _make_repos()
        access_requests = _make_service()
        access_requests.submit = AsyncMock(return_value=SubmitResult(attempt_id=7, notified=False))

        with capture_logs() as logs:
            await handle_my_chat_member(
                _make_member_update(), repo, config_service, access_requests, MagicMock()
            )

        entries = [e for e in logs if "notified" in e]
        assert entries, f"the membership log must report `notified`; logs were: {logs}"
        assert entries[0]["notified"] is False
        assert entries[0]["access_request_id"] == 7


class TestItDoesNotCryWolf:
    """Telegram delivers `my_chat_member` for plenty of non-joins."""

    @pytest.mark.asyncio
    async def test_a_private_chat_unblock_files_nothing(self) -> None:
        """The regression this gate exists to prevent.

        Telegram sends `my_chat_member` in a PRIVATE chat when a user blocks or
        unblocks the bot, and an unblock is `kicked -> member` — byte-for-byte
        the shape of a join. Without a chat-type gate, every unblock by any
        stranger would DM every admin a card for a "chat" that is one person's
        DM, and approving it would whitelist that DM into the «Чаты» tab.
        """
        repo, config_service = _make_repos()
        access_requests = _make_service()

        await handle_my_chat_member(
            _make_member_update(chat_type="private", old_status="kicked"),
            repo,
            config_service,
            access_requests,
            MagicMock(),
        )

        access_requests.submit.assert_not_awaited()
        # The chat is still RECORDED — only the approval card is withheld.
        repo.ensure_exists.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_promotion_inside_the_chat_files_nothing(self) -> None:
        """member -> administrator is not a new chat."""
        repo, config_service = _make_repos()
        access_requests = _make_service()

        await handle_my_chat_member(
            _make_member_update(status="administrator", old_status="member"),
            repo,
            config_service,
            access_requests,
            MagicMock(),
        )

        access_requests.submit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejoining_an_already_approved_chat_files_nothing(self) -> None:
        """An enabled chat is not an unauthorized access."""
        repo, config_service = _make_repos(enabled=True)
        access_requests = _make_service()

        await handle_my_chat_member(
            _make_member_update(), repo, config_service, access_requests, MagicMock()
        )

        access_requests.submit.assert_not_awaited()


class TestTheServiceItself:
    def _service(
        self, *, rejected: bool = False, cooldown: NotifyCooldown | None = None
    ) -> tuple[AccessRequestService, AsyncMock, AsyncMock]:
        admin_repo = AsyncMock()
        admin_repo.has_rejected_attempt = AsyncMock(return_value=rejected)
        admin_repo.log_unauthorized = AsyncMock(return_value=42)
        admin_repo.get_admin_language = AsyncMock(return_value="ru")
        notifier = AsyncMock()
        notifier.notify_unauthorized = AsyncMock(return_value=True)
        service = AccessRequestService(
            admin_repo=admin_repo,
            bot_config_repo=AsyncMock(),
            notifier=notifier,
            cooldown=cooldown or NotifyCooldown(),
        )
        return service, admin_repo, notifier

    @pytest.mark.asyncio
    async def test_a_second_request_for_the_same_chat_is_suppressed(self) -> None:
        """One shared cooldown is what stops add-then-post from being two cards."""
        service, admin_repo, _ = self._service()

        first = await service.submit(AccessRequest(chat_id=GROUP_ID), bot=MagicMock())
        second = await service.submit(AccessRequest(chat_id=GROUP_ID), bot=MagicMock())

        assert first.attempt_id == 42
        assert second.suppressed == "cooldown"
        assert admin_repo.log_unauthorized.await_count == 1

    @pytest.mark.asyncio
    async def test_a_prior_rejection_blocks_the_request_entirely(self) -> None:
        """A rejection is an admin-imposed blacklist: no row, no card."""
        service, admin_repo, notifier = self._service(rejected=True)

        result = await service.submit(AccessRequest(chat_id=GROUP_ID), bot=MagicMock())

        assert result.suppressed == "rejected"
        admin_repo.log_unauthorized.assert_not_awaited()
        notifier.notify_unauthorized.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_broken_notifier_never_reaches_the_caller(self) -> None:
        """This runs inside a middleware and a membership handler; raising here
        would take down an update for a notification failure."""
        service, _, notifier = self._service()
        notifier.notify_unauthorized = AsyncMock(side_effect=RuntimeError("telegram down"))

        result = await service.submit(AccessRequest(chat_id=GROUP_ID), bot=MagicMock())

        assert result.suppressed == "error"

    def test_the_cooldown_window_ends_exactly_where_it_says(self) -> None:
        cooldown = NotifyCooldown()
        cooldown.mark(GROUP_ID, now=1000.0)

        assert cooldown.is_cooling(GROUP_ID, now=1000.0 + NOTIFY_COOLDOWN_SECONDS - 1)
        assert not cooldown.is_cooling(GROUP_ID, now=1000.0 + NOTIFY_COOLDOWN_SECONDS)

    def test_expired_entries_are_pruned_so_the_dict_stays_bounded(self) -> None:
        """The prune branch, actually driven.

        This lives in a long-running process and is keyed by chat_id, so without
        pruning it grows for the life of the bot. The previous version of this
        test never crossed the 1000-entry threshold, so the branch it was named
        after never executed.
        """
        cooldown = NotifyCooldown()
        for chat in range(1001):
            cooldown.mark(chat, now=1000.0)
        assert len(cooldown._last) == 1001, "precondition: over the prune threshold"

        # One more mark, long after those expired.
        cooldown.mark(GROUP_ID, now=1000.0 + NOTIFY_COOLDOWN_SECONDS + 1)

        assert len(cooldown._last) == 1, (
            f"expired entries were not pruned: {len(cooldown._last)} still held"
        )
        assert cooldown.is_cooling(GROUP_ID, now=1000.0 + NOTIFY_COOLDOWN_SECONDS + 1)

    def test_a_still_live_entry_survives_the_prune(self) -> None:
        """The paired negative: pruning must not drop a cooldown still in force,
        or the prune itself becomes a way to double-notify."""
        cooldown = NotifyCooldown()
        for chat in range(1001):
            cooldown.mark(chat, now=1000.0)

        cooldown.mark(GROUP_ID, now=1000.0 + 10)

        assert cooldown.is_cooling(0, now=1000.0 + 10), (
            "an entry only 10s old was pruned — the window is 30 minutes"
        )


class TestNotifiedIsNotAssumed:
    """The three ways `notify_unauthorized` sends nothing at all.

    Driven through the REAL AbuseNotificationService. Every other test in this
    file mocks the notifier as an AsyncMock, which always "succeeds" — so the
    case that matters (a request is filed, the cooldown is marked, and no admin
    was told) cannot arise there by construction.
    """

    def _notifier(
        self, *, admin_ids: str = "500042", enabled: bool = True
    ) -> AbuseNotificationService:
        repo = AsyncMock()

        async def _get(key: str) -> object:
            if key == "admin_ids":
                return admin_ids
            if key == "admin_settings":
                return json.dumps({"notifications": {"unauthorized": enabled}})
            return None

        repo.get = AsyncMock(side_effect=_get)
        return AbuseNotificationService(repo)

    @pytest.mark.asyncio
    async def test_no_bot_object_reports_not_sent(self) -> None:
        sent = await self._notifier().notify_unauthorized(
            chat_id=GROUP_ID, chat_title="A group", user_id=1, bot=None
        )
        assert sent is False

    @pytest.mark.asyncio
    async def test_the_notification_type_switched_off_reports_not_sent(self) -> None:
        sent = await self._notifier(enabled=False).notify_unauthorized(
            chat_id=GROUP_ID, chat_title="A group", user_id=1, bot=AsyncMock()
        )
        assert sent is False

    @pytest.mark.asyncio
    async def test_no_admins_configured_reports_not_sent(self) -> None:
        sent = await self._notifier(admin_ids="").notify_unauthorized(
            chat_id=GROUP_ID, chat_title="A group", user_id=1, bot=AsyncMock()
        )
        assert sent is False

    @pytest.mark.asyncio
    async def test_a_delivered_card_reports_sent_and_says_it_was_an_add(self) -> None:
        bot = AsyncMock()

        sent = await self._notifier().notify_unauthorized(
            chat_id=GROUP_ID, chat_title="A group", user_id=1, reason="added", bot=bot
        )

        assert sent is True
        text = bot.send_message.await_args.args[1]
        assert "Unauthorized access" not in text, (
            "a card produced by an ADD must not be headed as an access attempt"
        )
        assert "added" in text.lower()
