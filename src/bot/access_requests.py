"""Queueing a chat for whitelist approval, from wherever it becomes visible.

This logic used to live entirely inside `AccessControlMiddleware`, which is
registered on `message` / `callback_query` / `edited_message` only. So the one
event that most obviously means "a new chat wants in" — the bot actually being
added to it — queued nothing and notified nobody (TD-025). The chat sat in
`chat_settings` with `enabled = false`, absent from the «Ожидают» tab (which
reads `unauthorized_attempts`), until some human happened to post there. The
log line even claimed the chat was "awaiting whitelist approval" while nothing
had been enqueued for approval.

Extracted rather than called across: `AccessControlMiddleware._extract_event_info`
returns `(None, None, None)` for anything that is not a `Message` or a
`CallbackQuery`, so handing it a `ChatMemberUpdated` would have produced a card
with no chat title and no user — the documented silent no-op, one layer down.

Lives under `src/bot/` and not `src/services/` on purpose: it builds the
approve/reject keyboard, and no module under `src/services/` imports from
`src/bot/` today. Putting it there would be the first inversion of that arrow.

**`NotifyCooldown` is `Scope.APP` and that is the entire point.** The cooldown
was per-instance state on the single middleware object; two callers each
holding their own would notify twice for one chat. One process-lifetime
instance is what makes "the bot was added, then someone posted" a single card.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog

from src.bot.keyboards.admin import access_keyboard
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.services.abuse.notifications import AbuseNotificationService

logger = structlog.get_logger(__name__)

# Max one notification per chat per half hour.
NOTIFY_COOLDOWN_SECONDS = 1800


@dataclass(frozen=True)
class AccessRequest:
    """Everything the queue row and the admin card need, already extracted.

    Deliberately a plain value: extracting it from a `Message`, a
    `CallbackQuery` or a `ChatMemberUpdated` is the caller's business, and each
    of those three knows its own shape. Note `user_last_name` and
    `chat_username` feed the NOTIFICATION only — `AdminRepository.log_unauthorized`
    accepts neither, and passing them would be a TypeError that both of the
    swallows below would hide completely.
    """

    chat_id: int
    chat_title: str | None = None
    chat_type: str | None = None
    chat_username: str | None = None
    user_id: int | None = None
    user_first_name: str | None = None
    user_last_name: str | None = None
    user_username: str | None = None
    message_text: str | None = None
    reason: str = "message"  # "message" | "added"


@dataclass(frozen=True)
class SubmitResult:
    """What actually happened, split so no field can over-claim.

    `attempt_id` proves the row was INSERTed. It does not prove an admin was
    told: `notify_unauthorized` returns silently when there is no bot object,
    when the `unauthorized` notification type is switched off, and when
    `admin_ids` is empty. Logging one number for both outcomes would reproduce
    TD-025's own defect — a log line asserting something that did not
    happen — one layer down.
    """

    attempt_id: int | None = None
    notified: bool = False
    # None on the happy path. Otherwise, in the order they can occur:
    #   "cooldown"       — inside the per-chat notification window, nothing done
    #   "rejected"       — an admin had already rejected this chat
    #   "db_error"       — the queue row could not be written
    #   "keyboard_error" — row written, but the card has no Approve/Reject buttons
    #   "error"          — anything else; see the log
    suppressed: str | None = None


class NotifyCooldown:
    """Per-chat notification cooldown, shared process-wide.

    In-memory on purpose and unchanged from the behaviour it replaces: a
    restart between "bot added" and "first message" produces two pending rows
    for one chat. Harmless in the admin UI — approving one calls
    `approve_all_for_chat`, which flips every pending row for that chat — but
    it is why this is not idempotent, and saying so is cheaper than implying
    otherwise.
    """

    def __init__(self) -> None:
        self._last: dict[int, float] = {}

    def is_cooling(self, chat_id: int, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        last = self._last.get(chat_id)
        return last is not None and now - last < NOTIFY_COOLDOWN_SECONDS

    def mark(self, chat_id: int, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if len(self._last) > 1000:
            self._last = {k: v for k, v in self._last.items() if now - v < NOTIFY_COOLDOWN_SECONDS}
        self._last[chat_id] = now


class AccessRequestService:
    """Files a chat for approval and tells the admins, at most once per window."""

    def __init__(
        self,
        *,
        admin_repo: AdminRepository,
        bot_config_repo: BotConfigRepository,
        notifier: AbuseNotificationService,
        cooldown: NotifyCooldown,
    ) -> None:
        self._admin_repo = admin_repo
        self._bot_config_repo = bot_config_repo
        self._notifier = notifier
        self._cooldown = cooldown

    async def submit(self, request: AccessRequest, *, bot: Any = None) -> SubmitResult:
        """Queue the chat and notify admins. Never raises."""
        # Check AND set, with no await in between. Marking at the end instead
        # leaves four awaits — two queries, an INSERT and one HTTP call per
        # admin — between the gate and the flag, and `start_polling` runs every
        # update as its own task: three messages pasted into an unauthorized
        # group in quick succession would all pass the gate and produce three
        # rows and three identical cards. The middleware this replaced had the
        # same window, but the class docstring now claims one card per chat, so
        # the claim has to be true rather than nearly true.
        if self._cooldown.is_cooling(request.chat_id):
            return SubmitResult(suppressed="cooldown")
        self._cooldown.mark(request.chat_id)

        try:
            # A prior REJECTION is an admin-imposed blacklist: skip the row and
            # the card entirely. The cooldown is already marked above, which is
            # what keeps a spamming chat off the database.
            if await self._admin_repo.has_rejected_attempt(request.chat_id):
                logger.info(
                    "Suppressing access request — chat has a prior rejection",
                    chat_id=request.chat_id,
                )
                return SubmitResult(suppressed="rejected")

            # Two separate failures, deliberately not one try. The row and the
            # keyboard fail independently: `log_unauthorized` can succeed and
            # `get_admin_language` / `access_keyboard` still raise, and sharing
            # one except made that case report `attempt_id` set, `suppressed`
            # None — the happy path — while logging "failed to log to the DB"
            # (false) and sending the admin a card with no Approve/Reject
            # buttons. That is a log asserting an outcome that did not happen,
            # which is the exact defect this whole module was written to end.
            attempt_id: int | None = None
            try:
                attempt_id = await self._admin_repo.log_unauthorized(
                    chat_id=request.chat_id,
                    chat_title=request.chat_title,
                    chat_type=request.chat_type,
                    user_id=request.user_id,
                    user_first_name=request.user_first_name,
                    user_username=request.user_username,
                    message_text=request.message_text,
                )
            except Exception:
                logger.warning("Failed to log the access request to the DB", exc_info=True)

            keyboard = None
            if attempt_id is not None:
                try:
                    lang = await self._admin_repo.get_admin_language(self._bot_config_repo)
                    keyboard = access_keyboard(lang, attempt_id)
                except Exception:
                    logger.warning(
                        "Access request queued but its approve/reject keyboard could not "
                        "be built — the admin card will arrive without buttons",
                        chat_id=request.chat_id,
                        attempt_id=attempt_id,
                        exc_info=True,
                    )

            notified = await self._notifier.notify_unauthorized(
                chat_id=request.chat_id,
                chat_title=request.chat_title,
                chat_type=request.chat_type,
                chat_username=request.chat_username,
                user_id=request.user_id,
                user_first_name=request.user_first_name,
                user_last_name=request.user_last_name,
                user_username=request.user_username,
                message_text=request.message_text,
                reason=request.reason,
                bot=bot,
                reply_markup=keyboard,
            )
            if attempt_id is None:
                suppressed = "db_error"
            elif keyboard is None:
                suppressed = "keyboard_error"
            else:
                suppressed = None
            return SubmitResult(
                attempt_id=attempt_id,
                notified=bool(notified),
                suppressed=suppressed,
            )
        except Exception:
            logger.warning("Failed to submit the access request", exc_info=True)
            return SubmitResult(suppressed="error")
