"""Community mode: greetings, welcome-backs, farewells (migration 036).

The state transitions themselves are measured against PostgreSQL in
`tests/integration/test_member_state.py`; this file covers what the bot SAYS
about a transition, to whom, and which updates reach that decision at all.

Messages and users are real aiogram models: a MagicMock answers every
attribute with something truthy, and `new_chat_members` / `sender_chat` are
exactly the attributes the guards read. Every id below is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Router
from aiogram.types import (
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    User,
)

from src.bot.access_requests import AccessRequestService
from src.bot.handlers import router as main_router
from src.bot.handlers.community import (
    departure_from_update,
    handle_chat_member,
    handle_left_chat_member,
    handle_new_chat_members,
    is_present,
)
from src.bot.handlers.community import router as community_router
from src.bot.middleware.access_control import AccessControlMiddleware
from src.bot.middleware.activity_tracker import ActivityTrackerMiddleware
from src.bot.middleware.chat_config import _extract_chat_id, _extract_chat_info
from src.bot.middleware.community import CommunityGreetingMiddleware, awaits_greeting_check
from src.bot.middleware.message_saver import MessageSaverMiddleware
from src.bot.middleware.rules import RulesMiddleware
from src.database.repositories.member_state import JoinOutcome, LeaveOutcome
from src.services.community import (
    FALLBACK_TEXTS,
    LIMIT_WINDOW_SECONDS,
    LIMITS,
    CommunityLimiter,
    CommunityService,
    Departure,
    display_name,
    format_leave_summary,
    mention,
    parse_templates,
    render_line,
    usable_model_line,
)
from src.utils.senders import is_membership_notice

GROUP_ID = -1009990101
ADMIN_ID = 900101
PERSON_ID = 900102
OTHER_ID = 900103
OWN_BOT_ID = 900104

_GROUP = Chat(id=GROUP_ID, type="supergroup", title="Клуб")
_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _user(user_id: int = PERSON_ID, *, first_name: str = "Аня", **kw: Any) -> User:
    return User(id=user_id, is_bot=kw.pop("is_bot", False), first_name=first_name, **kw)


def _message(**fields: Any) -> Message:
    base: dict[str, Any] = {"message_id": 10, "date": _AT, "chat": _GROUP}
    base.update(fields)
    return Message(**base)


def _joined(*users: User) -> Message:
    return _message(from_user=users[0], new_chat_members=list(users))


def _left(user: User, *, by: User | None = None) -> Message:
    return _message(from_user=by or user, left_chat_member=user)


def _text(user: User | None = None, text: str = "привет") -> Message:
    return _message(from_user=user or _user(), text=text)


# ── Pure helpers ──────────────────────────────────────────────────────────


class TestParseTemplates:
    def test_reads_a_decoded_dict(self) -> None:
        assert parse_templates({"new": ["Привет, {name}!"]}) == {"new": ["Привет, {name}!"]}

    def test_reads_a_json_string(self) -> None:
        """asyncpg hands JSONB back as text unless a codec is registered."""
        assert parse_templates('{"back": ["С возвращением, {name}!"]}') == {
            "back": ["С возвращением, {name}!"]
        }

    @pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]", 42, ["x"]])
    def test_malformed_input_is_no_templates(self, raw: Any) -> None:
        assert parse_templates(raw) == {}

    def test_a_template_without_the_placeholder_is_dropped(self) -> None:
        assert parse_templates({"new": ["Всем привет!", "Привет, {name}!"]}) == {
            "new": ["Привет, {name}!"]
        }

    def test_unknown_kinds_and_non_list_values_are_ignored(self) -> None:
        assert parse_templates({"hello": ["{name}"], "new": "{name}"}) == {}


class TestRenderLine:
    def test_the_template_is_escaped(self) -> None:
        assert render_line("<b>Hi</b> & {name}", "Аня") == "&lt;b&gt;Hi&lt;/b&gt; &amp; Аня"

    def test_the_inserted_name_is_not_rescanned_for_the_placeholder(self) -> None:
        assert render_line("Привет, {name}!", "x{name}y") == "Привет, x{name}y!"


class TestUsableModelLine:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Привет, {name}!", "Привет, {name}!"),
            ('  "Привет, {name}!"  ', "Привет, {name}!"),
            ("«С возвращением, {name}!»", "С возвращением, {name}!"),
            ("{name}, 100% рады!", "{name}, 100% рады!"),
        ],
    )
    def test_accepts_a_single_safe_line(self, raw: str, expected: str) -> None:
        assert usable_model_line(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "Привет!",
            "{name} и снова {name}",
            "Привет,\n{name}",
            "Привет, {name}! " + "а" * 300,
            "Привет, {name}! https://example.org",
            "Привет, {name}! www.example.org",
            "Привет, {name}! t.me/somewhere",
            "Привет, {name}! tg://resolve?domain=x",
            "Привет, {name}! Пиши @someone",
        ],
    )
    def test_refuses_anything_else(self, raw: str) -> None:
        assert usable_model_line(raw) is None


class TestNames:
    def test_alias_wins(self) -> None:
        assert display_name(_user(username="anya"), "Анечка") == "Анечка"

    def test_first_name_before_username(self) -> None:
        assert display_name(_user(username="anya"), None) == "Аня"

    def test_username_when_there_is_no_first_name(self) -> None:
        assert display_name(_user(first_name="", username="anya"), None) == "@anya"

    def test_a_last_resort_word(self) -> None:
        assert display_name(_user(first_name=""), None) == "друг"

    def test_mention_escapes_the_name(self) -> None:
        assert mention(PERSON_ID, "<Аня>") == (
            f'<a href="tg://user?id={PERSON_ID}">&lt;Аня&gt;</a>'
        )


class TestDeparture:
    @pytest.mark.parametrize(
        ("how", "status", "voluntary"),
        [("self", "left", True), ("removed", "left", False), ("banned", "kicked", False)],
    )
    def test_status_and_voluntariness(self, how: str, status: str, voluntary: bool) -> None:
        departure = Departure(how)
        assert (departure.status, departure.voluntary) == (status, voluntary)


class TestLeaveSummary:
    def _summary(self, **overrides: Any) -> str:
        args: dict[str, Any] = {
            "chat_title": "Клуб",
            "user": _user(username="anya"),
            "departure": Departure("self"),
            "outcome": LeaveOutcome(status="left", leave_count=1, joined_at=None),
            "first_message_at": None,
            "last_message_at": None,
            "now": _AT,
        }
        args.update(overrides)
        return format_leave_summary(**args)

    def test_every_dynamic_value_is_escaped(self) -> None:
        text = self._summary(
            chat_title="<i>Клуб</i>",
            user=_user(first_name="<b>Аня</b>", username="a_b"),
        )
        assert "<i>" not in text and "<b>" not in text
        assert "&lt;i&gt;Клуб&lt;/i&gt;" in text and "&lt;b&gt;Аня&lt;/b&gt;" in text

    def test_names_how_and_for_how_long(self) -> None:
        text = self._summary(
            departure=Departure("banned"),
            outcome=LeaveOutcome(
                status="kicked", leave_count=1, joined_at=datetime(2026, 10, 10, tzinfo=UTC)
            ),
            last_message_at=datetime(2026, 10, 19, tzinfo=UTC),
        )
        assert "Как: бан" in text
        assert "В чате: 10 дн. (с 10.10.2026)" in text
        assert "Последнее сообщение: 19.10.2026" in text
        assert "-й раз" not in text

    def test_first_message_stands_in_for_an_unseen_join(self) -> None:
        text = self._summary(first_message_at=datetime(2026, 10, 15, tzinfo=UTC))
        assert "(с 15.10.2026)" in text

    def test_someone_who_never_wrote_and_left_again(self) -> None:
        text = self._summary(outcome=LeaveOutcome(status="left", leave_count=3, joined_at=None))
        assert "Сообщений бот не видел" in text
        assert "В чате" not in text
        assert "Уходит уже 3-й раз" in text


class TestLimiter:
    def test_allows_the_limit_then_refuses(self) -> None:
        limiter = CommunityLimiter()
        spent = [limiter.spend(GROUP_ID, "public", now=1.0) for _ in range(LIMITS["public"] + 1)]
        assert spent == [True] * LIMITS["public"] + [False]
        assert not limiter.allowed(GROUP_ID, "public", now=1.0)

    def test_allowed_does_not_spend(self) -> None:
        limiter = CommunityLimiter()
        for _ in range(10):
            assert limiter.allowed(GROUP_ID, "public", now=1.0)
        assert limiter.spend(GROUP_ID, "public", now=1.0)

    def test_the_window_slides(self) -> None:
        limiter = CommunityLimiter()
        for _ in range(LIMITS["public"]):
            limiter.spend(GROUP_ID, "public", now=1.0)
        assert not limiter.allowed(GROUP_ID, "public", now=1.0 + LIMIT_WINDOW_SECONDS - 1)
        assert limiter.allowed(GROUP_ID, "public", now=1.0 + LIMIT_WINDOW_SECONDS)

    def test_chats_and_buckets_are_independent(self) -> None:
        limiter = CommunityLimiter()
        for _ in range(LIMITS["public"]):
            limiter.spend(GROUP_ID, "public", now=1.0)
        assert limiter.allowed(GROUP_ID - 1, "public", now=1.0)
        assert limiter.allowed(GROUP_ID, "dm", now=1.0)


# ── The service ───────────────────────────────────────────────────────────


class _Harness:
    """A `CommunityService` over fakes, with the knobs the tests turn."""

    def __init__(self) -> None:
        self.members = AsyncMock()
        self.members.record_join.return_value = JoinOutcome(kind="new", join_count=1, leave_count=0)
        self.members.claim_greeting.return_value = "new"
        self.members.record_leave.return_value = LeaveOutcome(
            status="left", leave_count=1, joined_at=None
        )
        self.members.message_span.return_value = (None, None)
        self.chat_settings = AsyncMock()
        self.chat_settings.get.return_value = {
            "community_templates": {
                "new": ["Привет, {name}!"],
                "back": ["С возвращением, {name}!"],
                "farewell": ["Пока, {name}!"],
                "joke": ["{name} ещё вернётся"],
            }
        }
        self.bot_config = AsyncMock()
        self.bot_config.get.return_value = [ADMIN_ID]
        self.aliases = AsyncMock()
        self.aliases.load_active.return_value = []
        self.router = AsyncMock()
        self.router.generate_text.side_effect = AssertionError("templates were configured")
        self.limiter = CommunityLimiter()
        self.bot = AsyncMock()
        self.service = CommunityService(
            member_repo=self.members,
            chat_settings_repo=self.chat_settings,
            bot_config_repo=self.bot_config,
            alias_repo=self.aliases,
            ai_router=self.router,
            limiter=self.limiter,
        )

    def sent(self) -> list[tuple[int, str]]:
        return [(c.args[0], c.args[1]) for c in self.bot.send_message.await_args_list]

    def exhaust(self, bucket: str = "public") -> None:
        while self.limiter.spend(GROUP_ID, bucket):
            pass


@pytest.fixture
def h() -> _Harness:
    return _Harness()


@pytest.fixture
def community_on(make_chat_config: Any) -> Any:
    def _cfg(**overrides: Any) -> Any:
        fields = {
            "chat_id": GROUP_ID,
            "community_enabled": True,
            "greet_on_first_message": False,
            "welcome_back_enabled": True,
            "farewell_public": True,
            "farewell_dm": False,
            "returner_joke_enabled": False,
        }
        fields.update(overrides)
        return make_chat_config(**fields)

    return _cfg


class TestJoin:
    async def test_greets_on_join_with_a_mention(self, h: _Harness, community_on: Any) -> None:
        await h.service.on_join(h.bot, community_on(), GROUP_ID, _user(), _AT)

        assert h.sent() == [
            (GROUP_ID, f'Привет, <a href="tg://user?id={PERSON_ID}">Аня</a>!'),
        ]
        assert h.bot.send_message.await_args.kwargs["parse_mode"] == "HTML"

    async def test_mode_off_records_the_join_and_says_nothing(
        self, h: _Harness, community_on: Any
    ) -> None:
        """Recorded anyway: switching the mode on later must still know who left."""
        await h.service.on_join(
            h.bot, community_on(community_enabled=False), GROUP_ID, _user(), _AT
        )

        h.members.record_join.assert_awaited_once_with(GROUP_ID, PERSON_ID, _AT, greet=False)
        h.members.claim_greeting.assert_not_awaited()
        assert h.sent() == []

    async def test_a_duplicate_join_says_nothing(self, h: _Harness, community_on: Any) -> None:
        h.members.record_join.return_value = None

        await h.service.on_join(h.bot, community_on(), GROUP_ID, _user(), _AT)

        h.members.claim_greeting.assert_not_awaited()
        assert h.sent() == []

    async def test_first_message_mode_leaves_the_greeting_pending(
        self, h: _Harness, community_on: Any
    ) -> None:
        await h.service.on_join(
            h.bot, community_on(greet_on_first_message=True), GROUP_ID, _user(), _AT
        )

        h.members.record_join.assert_awaited_once_with(GROUP_ID, PERSON_ID, _AT, greet=True)
        h.members.claim_greeting.assert_not_awaited()
        assert h.sent() == []

    async def test_a_return_is_welcomed_back(self, h: _Harness, community_on: Any) -> None:
        h.members.claim_greeting.return_value = "back"

        await h.service.on_join(h.bot, community_on(), GROUP_ID, _user(), _AT)

        assert h.sent()[0][1].startswith("С возвращением, ")

    async def test_a_return_is_silent_without_welcome_back(
        self, h: _Harness, community_on: Any
    ) -> None:
        h.members.claim_greeting.return_value = "back"

        await h.service.on_join(
            h.bot, community_on(welcome_back_enabled=False), GROUP_ID, _user(), _AT
        )

        assert h.sent() == []

    async def test_the_raid_guard_drops_the_greeting(self, h: _Harness, community_on: Any) -> None:
        """At join time the greeting is claimed and dropped, not left pending."""
        h.exhaust()

        await h.service.on_join(h.bot, community_on(), GROUP_ID, _user(), _AT)

        h.members.claim_greeting.assert_awaited_once()
        assert h.sent() == []

    async def test_the_alias_is_used_and_escaped(self, h: _Harness, community_on: Any) -> None:
        h.aliases.load_active.return_value = [
            {"user_id": PERSON_ID, "alias": "<Анечка>", "role": "primary"}
        ]

        await h.service.on_join(h.bot, community_on(), GROUP_ID, _user(), _AT)

        assert ">&lt;Анечка&gt;</a>" in h.sent()[0][1]

    async def test_an_alias_failure_falls_back_to_the_account_name(
        self, h: _Harness, community_on: Any
    ) -> None:
        h.aliases.load_active.side_effect = RuntimeError("db down")

        await h.service.on_join(h.bot, community_on(), GROUP_ID, _user(), _AT)

        assert ">Аня</a>" in h.sent()[0][1]


class TestFirstMessage:
    async def test_replies_without_a_mention(self, h: _Harness, community_on: Any) -> None:
        message = MagicMock(from_user=_user(first_name="<Аня>"), chat=_GROUP)
        message.reply = AsyncMock()

        await h.service.greet_first_message(community_on(greet_on_first_message=True), message)

        message.reply.assert_awaited_once_with("Привет, &lt;Аня&gt;!", parse_mode="HTML")

    async def test_a_full_window_leaves_the_greeting_pending(
        self, h: _Harness, community_on: Any
    ) -> None:
        """Checked BEFORE the claim: a claim here would lose the greeting."""
        h.exhaust()
        message = MagicMock(from_user=_user(), chat=_GROUP)
        message.reply = AsyncMock()

        await h.service.greet_first_message(community_on(greet_on_first_message=True), message)

        h.members.claim_greeting.assert_not_awaited()
        message.reply.assert_not_awaited()

    async def test_nothing_pending_is_nothing_said(self, h: _Harness, community_on: Any) -> None:
        h.members.claim_greeting.return_value = None
        message = MagicMock(from_user=_user(), chat=_GROUP)
        message.reply = AsyncMock()

        await h.service.greet_first_message(community_on(greet_on_first_message=True), message)

        message.reply.assert_not_awaited()
        assert all(h.limiter.spend(GROUP_ID, "public") for _ in range(LIMITS["public"])), (
            "a message that greeted no one must not use up the raid budget"
        )


class TestLeave:
    async def test_a_voluntary_leave_is_said_goodbye_to(
        self, h: _Harness, community_on: Any
    ) -> None:
        await h.service.on_leave(
            h.bot, community_on(), GROUP_ID, "Клуб", _user(), _AT, Departure("self")
        )

        h.members.record_leave.assert_awaited_once_with(GROUP_ID, PERSON_ID, _AT, status="left")
        assert h.sent() == [(GROUP_ID, "Пока, Аня!")]

    @pytest.mark.parametrize(("how", "status"), [("removed", "left"), ("banned", "kicked")])
    async def test_a_removal_is_not_announced(
        self, h: _Harness, community_on: Any, how: str, status: str
    ) -> None:
        await h.service.on_leave(
            h.bot, community_on(), GROUP_ID, "Клуб", _user(), _AT, Departure(how)
        )

        h.members.record_leave.assert_awaited_once_with(GROUP_ID, PERSON_ID, _AT, status=status)
        assert h.sent() == []

    async def test_no_public_farewell_when_it_is_off(self, h: _Harness, community_on: Any) -> None:
        await h.service.on_leave(
            h.bot,
            community_on(farewell_public=False),
            GROUP_ID,
            "Клуб",
            _user(),
            _AT,
            Departure("self"),
        )

        assert h.sent() == []

    async def test_a_second_leave_gets_the_joke(self, h: _Harness, community_on: Any) -> None:
        h.members.record_leave.return_value = LeaveOutcome(
            status="left", leave_count=2, joined_at=None
        )

        await h.service.on_leave(
            h.bot,
            community_on(returner_joke_enabled=True, farewell_public=False),
            GROUP_ID,
            "Клуб",
            _user(),
            _AT,
            Departure("self"),
        )

        assert h.sent() == [(GROUP_ID, "Аня ещё вернётся")]

    async def test_a_first_leave_never_gets_the_joke(self, h: _Harness, community_on: Any) -> None:
        await h.service.on_leave(
            h.bot,
            community_on(returner_joke_enabled=True),
            GROUP_ID,
            "Клуб",
            _user(),
            _AT,
            Departure("self"),
        )

        assert h.sent() == [(GROUP_ID, "Пока, Аня!")]

    async def test_mode_off_records_and_says_nothing(self, h: _Harness, community_on: Any) -> None:
        await h.service.on_leave(
            h.bot,
            community_on(community_enabled=False, farewell_dm=True),
            GROUP_ID,
            "Клуб",
            _user(),
            _AT,
            Departure("self"),
        )

        h.members.record_leave.assert_awaited_once()
        assert h.sent() == []

    async def test_admins_get_a_summary_even_for_a_removal(
        self, h: _Harness, community_on: Any
    ) -> None:
        h.bot_config.get.return_value = [ADMIN_ID, ADMIN_ID + 1]
        h.bot.send_message.side_effect = [RuntimeError("blocked the bot"), None]

        await h.service.on_leave(
            h.bot,
            community_on(farewell_dm=True, farewell_public=False),
            GROUP_ID,
            "Клуб",
            _user(),
            _AT,
            Departure("removed"),
        )

        recipients = [chat for chat, _ in h.sent()]
        assert recipients == [ADMIN_ID, ADMIN_ID + 1], "one blocked admin must not stop the rest"
        assert "Как: удаление админом" in h.sent()[1][1]


class TestGeneratedLines:
    async def test_without_templates_the_model_writes_the_line(
        self, h: _Harness, community_on: Any
    ) -> None:
        h.chat_settings.get.return_value = {"community_templates": None}
        result = SimpleNamespace(text="Заходи, {name}, <чай> готов!")
        h.router.generate_text.side_effect = None
        h.router.generate_text.return_value = result

        await h.service.on_join(h.bot, community_on(), GROUP_ID, _user(), _AT)

        assert h.sent()[0][1] == (
            f'Заходи, <a href="tg://user?id={PERSON_ID}">Аня</a>, &lt;чай&gt; готов!'
        )
        h.router.log_usage.assert_awaited_once_with(result, chat_id=GROUP_ID, task_type="community")

    async def test_an_unsafe_model_line_falls_back(self, h: _Harness, community_on: Any) -> None:
        h.chat_settings.get.return_value = None
        h.router.generate_text.side_effect = None
        h.router.generate_text.return_value = SimpleNamespace(text="Привет, {name}! t.me/spam")

        await h.service.on_join(h.bot, community_on(), GROUP_ID, _user(), _AT)

        rendered = {render_line(t, mention(PERSON_ID, "Аня")) for t in FALLBACK_TEXTS["new"]}
        assert h.sent()[0][1] in rendered

    async def test_a_model_failure_falls_back(self, h: _Harness, community_on: Any) -> None:
        h.chat_settings.get.side_effect = RuntimeError("db down")
        h.router.generate_text.side_effect = RuntimeError("all providers failed")

        await h.service.on_leave(
            h.bot, community_on(), GROUP_ID, "Клуб", _user(), _AT, Departure("self")
        )

        rendered = {render_line(t, "Аня") for t in FALLBACK_TEXTS["farewell"]}
        assert h.sent()[0][1] in rendered


# ── Handlers ──────────────────────────────────────────────────────────────


def _member_update(
    old: Any, new: Any, *, by: User | None = None, user: User | None = None
) -> ChatMemberUpdated:
    person = user or _user()
    return ChatMemberUpdated(
        chat=_GROUP,
        from_user=by or person,
        date=_AT,
        old_chat_member=old(user=person),
        new_chat_member=new(user=person),
    )


def _banned(user: User) -> Any:
    return SimpleNamespace(status="kicked", user=user)


class TestPresence:
    @pytest.mark.parametrize(
        ("status", "is_member", "present"),
        [
            ("member", None, True),
            ("administrator", None, True),
            ("creator", None, True),
            ("left", None, False),
            ("kicked", None, False),
            ("restricted", True, True),
            ("restricted", False, False),
        ],
    )
    def test_is_present(self, status: str, is_member: bool | None, present: bool) -> None:
        assert is_present(SimpleNamespace(status=status, is_member=is_member)) is present

    def test_departure_by_self(self) -> None:
        event = _member_update(ChatMemberMember, ChatMemberLeft)
        assert departure_from_update(event).how == "self"

    def test_departure_by_an_admin(self) -> None:
        event = _member_update(ChatMemberMember, ChatMemberLeft, by=_user(ADMIN_ID))
        assert departure_from_update(event).how == "removed"

    def test_departure_by_ban(self) -> None:
        event = MagicMock(new_chat_member=_banned(_user()), from_user=_user(ADMIN_ID))
        assert departure_from_update(event).how == "banned"


class TestChatMemberHandler:
    async def test_a_join_goes_to_on_join(self, make_chat_config: Any) -> None:
        community = AsyncMock()
        event = _member_update(ChatMemberLeft, ChatMemberMember)
        config = make_chat_config(chat_id=GROUP_ID)

        await handle_chat_member(event, AsyncMock(), config, community)

        community.on_join.assert_awaited_once()
        assert community.on_join.await_args.args[2:] == (GROUP_ID, event.new_chat_member.user, _AT)

    async def test_a_leave_goes_to_on_leave(self, make_chat_config: Any) -> None:
        community = AsyncMock()
        event = _member_update(ChatMemberMember, ChatMemberLeft)

        await handle_chat_member(event, AsyncMock(), make_chat_config(chat_id=GROUP_ID), community)

        community.on_leave.assert_awaited_once()
        assert community.on_leave.await_args.args[-1] == Departure("self")

    async def test_a_non_whitelisted_chat_is_ignored(self, make_chat_config: Any) -> None:
        """`AccessControlMiddleware` does not gate this update type (CLAUDE.md)."""
        community = AsyncMock()
        event = _member_update(ChatMemberLeft, ChatMemberMember)

        await handle_chat_member(
            event, AsyncMock(), make_chat_config(chat_id=GROUP_ID, enabled=False), community
        )

        community.on_join.assert_not_awaited()

    async def test_bots_are_ignored(self, make_chat_config: Any) -> None:
        community = AsyncMock()
        event = _member_update(
            ChatMemberLeft, ChatMemberMember, user=_user(OWN_BOT_ID, is_bot=True)
        )

        await handle_chat_member(event, AsyncMock(), make_chat_config(chat_id=GROUP_ID), community)

        community.on_join.assert_not_awaited()

    async def test_a_promotion_is_no_transition(self, make_chat_config: Any) -> None:
        community = AsyncMock()
        event = MagicMock(
            chat=_GROUP,
            old_chat_member=SimpleNamespace(status="member", user=_user()),
            new_chat_member=SimpleNamespace(status="administrator", user=_user()),
        )

        await handle_chat_member(event, AsyncMock(), make_chat_config(chat_id=GROUP_ID), community)

        community.on_join.assert_not_awaited()
        community.on_leave.assert_not_awaited()

    async def test_a_service_failure_is_contained(self, make_chat_config: Any) -> None:
        community = AsyncMock()
        community.on_join.side_effect = RuntimeError("db down")
        event = _member_update(ChatMemberLeft, ChatMemberMember)

        await handle_chat_member(event, AsyncMock(), make_chat_config(chat_id=GROUP_ID), community)


class TestServiceMessageHandlers:
    async def test_each_person_in_a_join_is_recorded_and_bots_skipped(
        self, make_chat_config: Any
    ) -> None:
        community = AsyncMock()
        bot_user = _user(OWN_BOT_ID, is_bot=True)
        message = _joined(_user(), bot_user, _user(OTHER_ID, first_name="Боря"))

        await handle_new_chat_members(
            message, AsyncMock(), make_chat_config(chat_id=GROUP_ID), community
        )

        joined = [call.args[3].id for call in community.on_join.await_args_list]
        assert joined == [PERSON_ID, OTHER_ID]

    async def test_leaving_by_oneself(self, make_chat_config: Any) -> None:
        community = AsyncMock()

        await handle_left_chat_member(
            _left(_user()), AsyncMock(), make_chat_config(chat_id=GROUP_ID), community
        )

        assert community.on_leave.await_args.args[-1] == Departure("self")

    async def test_removed_by_someone_else(self, make_chat_config: Any) -> None:
        community = AsyncMock()

        await handle_left_chat_member(
            _left(_user(), by=_user(ADMIN_ID)),
            AsyncMock(),
            make_chat_config(chat_id=GROUP_ID),
            community,
        )

        assert community.on_leave.await_args.args[-1] == Departure("removed")


class TestRouting:
    def test_chat_member_updates_are_requested_from_telegram(self) -> None:
        """`start_polling` derives `allowed_updates` from registered observers.

        Asked of the main router, not of a `Dispatcher` it is included into: a
        router attaches to one parent for the life of the process, and
        `test_reactions_allowed_updates.py` already does that. The dispatcher
        registers no handlers of its own, so the answer is the same.
        """
        assert "chat_member" in main_router.resolve_used_update_types()

    def test_control_a_bare_router_does_not_request_them(self) -> None:
        assert "chat_member" not in Router().resolve_used_update_types()

    async def test_no_earlier_router_consumes_a_service_line(self) -> None:
        """A matched handler consumes the update (CLAUDE.md): if any router
        included before `community` accepted a "joined" line, the community
        handler would never see it -- with every unit test above still green."""
        message = _joined(_user())
        bot = AsyncMock()
        kwargs = {"bot": bot, "raw_state": None, "event_chat": _GROUP, "event_from_user": _user()}
        swallowed = []
        for router in main_router.sub_routers:
            if router is community_router:
                break
            for handler in router.message.handlers:
                matched, _ = await handler.check(message, **kwargs)
                if matched:
                    swallowed.append(f"{router.name}:{handler.callback.__name__}")
        assert swallowed == []

    async def test_control_the_community_handler_does_accept_it(self) -> None:
        message = _joined(_user())
        checks = [await h.check(message) for h in community_router.message.handlers]
        assert [matched for matched, _ in checks] == [True, False]

    async def test_the_bots_own_arrival_is_not_taken(self) -> None:
        """`my_chat_member` in chat_events.py owns that; the line is left alone."""
        message = _joined(_user(OWN_BOT_ID, is_bot=True))
        checks = [await h.check(message) for h in community_router.message.handlers]
        assert not any(matched for matched, _ in checks)


# ── Middlewares ───────────────────────────────────────────────────────────


class TestMembershipNotice:
    def test_a_join_line(self) -> None:
        assert is_membership_notice(_joined(_user()))

    def test_a_leave_line(self) -> None:
        assert is_membership_notice(_left(_user()))

    def test_an_ordinary_message(self) -> None:
        assert not is_membership_notice(_text())


class TestServiceLinesSkipTheMessagePipeline:
    """A service line is not something anyone said. Before the community router
    existed no handler matched it, so the inner middlewares never ran on it;
    now they do, and each must step aside."""

    @pytest.mark.parametrize(
        "middleware",
        [MessageSaverMiddleware(), ActivityTrackerMiddleware(), RulesMiddleware()],
        ids=["saver", "activity", "rules"],
    )
    @pytest.mark.parametrize("line", ["joined", "left"])
    async def test_nothing_is_stored_or_evaluated(
        self, middleware: Any, line: str, make_chat_config: Any
    ) -> None:
        message = _joined(_user()) if line == "joined" else _left(_user())
        container = AsyncMock()
        handler = AsyncMock()
        data = {
            "dishka_container": container,
            "chat_config": make_chat_config(chat_id=GROUP_ID, rules_enabled=True),
            "bot": AsyncMock(),
        }

        await middleware(handler, message, data)

        container.get.assert_not_awaited()
        handler.assert_awaited_once()

    @pytest.mark.parametrize(
        "middleware",
        [MessageSaverMiddleware(), ActivityTrackerMiddleware(), RulesMiddleware()],
        ids=["saver", "activity", "rules"],
    )
    async def test_control_an_ordinary_message_is_processed(
        self, middleware: Any, make_chat_config: Any
    ) -> None:
        container = AsyncMock()
        data = {
            "dishka_container": container,
            "chat_config": make_chat_config(chat_id=GROUP_ID, rules_enabled=True),
            "bot": AsyncMock(),
        }

        await middleware(AsyncMock(), _text(), data)

        container.get.assert_awaited()

    async def test_a_join_in_an_unapproved_chat_does_not_ask_for_access(
        self, make_chat_config: Any
    ) -> None:
        """The access request is for a chat where someone talks to the bot."""
        requested = await self._unapproved(make_chat_config, _joined(_user()))
        assert AccessRequestService not in requested

    async def test_control_a_message_there_does_ask(self, make_chat_config: Any) -> None:
        requested = await self._unapproved(make_chat_config, _text())
        assert AccessRequestService in requested

    @staticmethod
    async def _unapproved(make_chat_config: Any, message: Message) -> list[type]:
        requested: list[type] = []

        async def get(kind: type) -> Any:
            requested.append(kind)
            repo = AsyncMock()
            repo.get.return_value = "[]"
            return repo

        container = MagicMock()
        container.get = get
        handler = AsyncMock()
        data = {
            "dishka_container": container,
            "chat_config": make_chat_config(chat_id=GROUP_ID, enabled=False),
            "bot": AsyncMock(),
        }

        result = await AccessControlMiddleware()(handler, message, data)

        assert result is None
        handler.assert_not_awaited()
        return requested


class TestChatConfigForMemberUpdates:
    def test_the_chat_is_found(self) -> None:
        event = _member_update(ChatMemberLeft, ChatMemberMember)
        assert _extract_chat_id(event) == GROUP_ID
        assert _extract_chat_info(event) == ("Клуб", "supergroup", False)


class TestAwaitsGreeting:
    def _cfg(self, make_chat_config: Any, **overrides: Any) -> Any:
        fields = {
            "chat_id": GROUP_ID,
            "community_enabled": True,
            "greet_on_first_message": True,
        }
        fields.update(overrides)
        return make_chat_config(**fields)

    def test_a_person_speaking(self, make_chat_config: Any) -> None:
        assert awaits_greeting_check(_text(), self._cfg(make_chat_config))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"enabled": False},
            {"community_enabled": False},
            {"greet_on_first_message": False},
        ],
    )
    def test_not_in_that_mode(self, make_chat_config: Any, overrides: dict[str, Any]) -> None:
        assert not awaits_greeting_check(_text(), self._cfg(make_chat_config, **overrides))

    def test_no_config(self) -> None:
        assert not awaits_greeting_check(_text(), None)

    def test_a_bot(self, make_chat_config: Any) -> None:
        message = _text(_user(OWN_BOT_ID, is_bot=True))
        assert not awaits_greeting_check(message, self._cfg(make_chat_config))

    def test_an_anonymous_admin(self, make_chat_config: Any) -> None:
        message = _message(
            from_user=_user(1087968824, first_name="Group", is_bot=True),
            sender_chat=_GROUP,
            text="привет",
        )
        assert not awaits_greeting_check(message, self._cfg(make_chat_config))

    def test_a_channel_speaking(self, make_chat_config: Any) -> None:
        message = _message(
            from_user=_user(OTHER_ID),
            sender_chat=Chat(id=GROUP_ID - 7, type="channel", title="Канал"),
            text="привет",
        )
        assert not awaits_greeting_check(message, self._cfg(make_chat_config))

    def test_a_service_line(self, make_chat_config: Any) -> None:
        assert not awaits_greeting_check(_joined(_user()), self._cfg(make_chat_config))

    def test_a_private_chat(self, make_chat_config: Any) -> None:
        message = _message(
            chat=Chat(id=PERSON_ID, type="private"), from_user=_user(), text="привет"
        )
        assert not awaits_greeting_check(message, self._cfg(make_chat_config))


class TestGreetingMiddleware:
    def _data(self, make_chat_config: Any, community: Any) -> dict[str, Any]:
        container = AsyncMock()
        container.get.return_value = community
        return {
            "dishka_container": container,
            "chat_config": make_chat_config(
                chat_id=GROUP_ID, community_enabled=True, greet_on_first_message=True
            ),
        }

    async def test_greets_then_passes_the_message_on(self, make_chat_config: Any) -> None:
        community = AsyncMock()
        handler = AsyncMock()
        message = _text()

        await CommunityGreetingMiddleware()(
            handler, message, self._data(make_chat_config, community)
        )

        community.greet_first_message.assert_awaited_once()
        handler.assert_awaited_once()

    async def test_a_failure_never_blocks_the_message(self, make_chat_config: Any) -> None:
        community = AsyncMock()
        community.greet_first_message.side_effect = RuntimeError("db down")
        handler = AsyncMock()

        await CommunityGreetingMiddleware()(
            handler, _text(), self._data(make_chat_config, community)
        )

        handler.assert_awaited_once()

    async def test_outside_the_mode_the_service_is_not_even_built(
        self, make_chat_config: Any
    ) -> None:
        data = self._data(make_chat_config, AsyncMock())
        data["chat_config"] = make_chat_config(chat_id=GROUP_ID)
        handler = AsyncMock()

        await CommunityGreetingMiddleware()(handler, _text(), data)

        data["dishka_container"].get.assert_not_awaited()
        handler.assert_awaited_once()
