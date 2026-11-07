"""Turning the bot off in one chat, and the ways back, through the real router.

Each tap is delivered through ``src.bot.handlers.router`` (`screen_routing.deliver`)
and its handler runs on in-memory repositories, so what is asserted is what the
admin's phone would get: the rows of the edited message and the value written.
The next tap is the payload read off that message, never one typed into the test.

Ways back covered here: «↩️ Вернуть» on the list right after «🔌 Отключить»
(``adm_wl_on:``), «🔌 Отключить» on an approved access card (the same question
the chat's screen asks), and «🙏 Запросы · N» on the hub (the requests in one tap).
Also what must NOT offer a way back: a chat that was off before the tap, whose
«↩️ Вернуть» would turn it on, and ▶ from the undo render, which must not skip
a chat (`SHORT_PAGES`). And what «↩️ Вернуть» must not do when it is tapped
late: turn on a chat rejected since the turn-off (`REJECTED_BEFORE` keeps an
older rejection from counting), or one the bot was removed from since, which
would let the next re-add skip its access request.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import GetChatMember
from aiogram.types import (
    Chat,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberRestricted,
    ChatMemberUpdated,
    User,
)
from structlog.testing import capture_logs

from src.bot.handlers import admin as A
from src.bot.screens import cards
from src.bot.screens.chat import REJECTED_BEFORE, SHORT_PAGES, build_chats, chats_per_page
from src.bot.screens.hub import build_hub
from src.bot.screens.lint import check_screen
from src.bot.screens.routes import ROUTES, cb
from src.bot.screens.samples import fake
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.services.chat_config import ChatConfigService
from tests.unit.screen_routing import ADMIN_ID, FakeState, admin_tap, deliver, first_match

# Invented values only (the repository is public): the samples' fakes, plus the
# test suite's long-standing -1001234567890 for the fifth chat.
CHAT_ID = fake.CHAT_ID
OTHERS = (fake.CHAT_ID_SHORT, fake.CHAT_ID_OTHER, fake.CHAT_ID_NEW, -1001234567890)
TITLE = fake.CHAT_TITLE
BOT_ID = 4242  # invented
BOT_USER = User(id=BOT_ID, is_bot=True, first_name="Bot")


class _Settings:
    """`ChatSettingsRepository.get` / ``set_field`` over a dict of rows."""

    def __init__(self, rows: dict[int, dict[str, Any]]) -> None:
        self.rows = rows
        self.writes: list[tuple[int, str, Any]] = []

    async def get(self, chat_id: int) -> dict[str, Any] | None:
        row = self.rows.get(chat_id)
        return dict(row) if row is not None else None

    async def set_field(self, chat_id: int, field: str, value: Any) -> bool:
        self.writes.append((chat_id, field, value))
        if chat_id not in self.rows:
            return False
        self.rows[chat_id][field] = value
        return True

    async def ensure_exists(self, chat_id: int, title: Any, chat_type: Any) -> dict[str, Any]:
        """What the bot being added again reads (`chat_events`): the row as it is."""
        return dict(self.rows[chat_id])


class _BotConfig:
    """`BotConfigRepository.get` for ``admin_ids``: the tapping admin is the one admin."""

    async def get(self, key: str) -> Any:
        assert key == "admin_ids", key
        return [ADMIN_ID]


class _Admin:
    """The `AdminRepository` reads the chat list, the hub and the requests make."""

    def __init__(self, settings: _Settings, *, pending: int = 1, rejected: int = 2) -> None:
        self._settings = settings
        self._counts = {"pending": pending, "rejected": rejected}
        self.rejected_before = False
        self.attempts: dict[int, dict[str, Any]] = {}
        self.fetched: list[tuple[int, int]] = []

    async def count_attempts(self, status: str) -> int:
        return self._counts[status]

    async def has_rejected_attempt(self, chat_id: int) -> bool:
        return self.rejected_before or any(
            a["chat_id"] == chat_id and a["status"] == "rejected" for a in self.attempts.values()
        )

    async def get_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        attempt = self.attempts.get(attempt_id)
        return dict(attempt) if attempt is not None else None

    async def update_attempt_status(self, attempt_id: int, status: str) -> dict[str, Any]:
        self.attempts[attempt_id]["status"] = status
        return dict(self.attempts[attempt_id])

    async def get_enabled_chats_page_by_activity(
        self, page: int, per_page: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
        self.fetched.append((page, per_page))
        on = [r for r in self._settings.rows.values() if r.get("enabled")]
        return on[page * per_page : (page + 1) * per_page], len(on)

    async def get_pending_attempts_page(
        self, page: int, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        attempt = {
            "id": fake.ATTEMPT_ID,
            "chat_id": OTHERS[3],
            "chat_title": "Новый чат",
            "chat_type": "supergroup",
            "user_id": fake.USER_ID,
            "user_first_name": "Аня",
            "user_username": None,
            "message_text": "привет",
            "created_at": None,
        }
        return [attempt], 1


class _Container:
    def __init__(self, deps: dict[type, Any]) -> None:
        self._deps = deps

    async def get(self, dependency: type) -> Any:
        return self._deps[dependency]


def _world(*, enabled: bool = True) -> tuple[_Settings, _Admin, MagicMock, dict[str, Any]]:
    """Five connected chats, ``CHAT_ID`` the busiest; one request waits, two were rejected."""
    rows = {
        CHAT_ID: {
            "chat_id": CHAT_ID,
            "chat_title": TITLE,
            "chat_type": "supergroup",
            "enabled": enabled,
            "message_count_24h": 148,
        }
    }
    for n, cid in enumerate(OTHERS):
        rows[cid] = {
            "chat_id": cid,
            "chat_title": f"Чат {n + 1}",
            "chat_type": "supergroup",
            "enabled": True,
            "message_count_24h": 10 - n,
        }
    settings = _Settings(rows)
    admin = _Admin(settings)
    service = MagicMock(spec=ChatConfigService)
    bot_config = _BotConfig()
    data = {
        "chat_settings_repo": settings,
        "admin_repo": admin,
        "bot_config_repo": bot_config,
        "dishka_container": _Container(
            {AdminRepository: admin, ChatConfigService: service, BotConfigRepository: bot_config}
        ),
    }
    return settings, admin, service, data


class _Bot:
    """`Bot.get_chat_member`, the one Bot API call the undo makes.

    ``answer`` is what Telegram says about the bot in any chat it is asked
    about: a `ChatMember`, or an exception it raises. Every question is kept.
    """

    id = BOT_ID

    def __init__(self, answer: Any = None) -> None:
        self.answer = ChatMemberMember(user=BOT_USER) if answer is None else answer
        self.asked: list[tuple[int, int]] = []

    async def get_chat_member(self, chat_id: int, user_id: int) -> Any:
        self.asked.append((chat_id, user_id))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def _tap(payload: str, data: dict[str, Any], *, bot: _Bot | None = None) -> MagicMock:
    """Deliver a tap; Telegram says the bot is in the chat unless ``bot`` says otherwise."""
    tap = admin_tap(payload)
    tap.bot = bot if bot is not None else _Bot()
    tap.message.edit_text = AsyncMock()
    await deliver("callback_query", tap, FakeState(), call=True, **data)
    return tap


def _labels(tap: MagicMock) -> list[list[str]]:
    markup = tap.message.edit_text.await_args.kwargs["reply_markup"]
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _payload(tap: MagicMock, label: str) -> str:
    markup = tap.message.edit_text.await_args.kwargs["reply_markup"]
    found = [b.callback_data for row in markup.inline_keyboard for b in row if b.text == label]
    assert len(found) == 1, _labels(tap)
    return str(found[0])


def _text(tap: MagicMock) -> str:
    return str(tap.message.edit_text.await_args.args[0])


# ── «🔌 Отключить» and «↩️ Вернуть» ─────────────────────────────────────────


async def test_turn_off_offers_a_way_back() -> None:
    settings, _, service, data = _world()

    ask = await _tap(cb("adm_wl_rm_ask:", "ru", CHAT_ID, 0), data)
    off = await _tap(_payload(ask, "🔌 Отключить"), data)

    assert settings.rows[CHAT_ID]["enabled"] is False
    service.invalidate.assert_called_with(CHAT_ID)
    assert _text(off).splitlines()[0] == f"<i>✓ Бот отключён в чате «{TITLE}»</i>"
    assert _labels(off) == [
        ["↩️ Вернуть"],
        ["🙏 Запросы доступа · 1"],
        ["Чат 1 · 10"],
        ["Чат 2 · 9"],
        ["◀", "1/2", "▶"],
        ["🚫 Отклонённые · 2", "⬅️ Панель"],
    ], "undo first, under the notice it takes back; the page one chat shorter"
    assert _payload(off, "↩️ Вернуть") == f"adm_wl_on:ru:{CHAT_ID}:0"


async def test_turning_off_the_only_chat_still_offers_the_way_back() -> None:
    """The list is empty then (``chats.empty``) and the undo row must survive it."""
    settings, admin, _, data = _world()
    for cid in OTHERS:
        del settings.rows[cid]
    admin._counts = {"pending": 0, "rejected": 0}

    off = await _tap(cb("adm_wl_rm:", "ru", CHAT_ID, 0), data)

    assert settings.rows[CHAT_ID]["enabled"] is False
    assert _labels(off) == [["↩️ Вернуть"], ["⬅️ Панель"]]
    on = await _tap(_payload(off, "↩️ Вернуть"), data)
    assert settings.rows[CHAT_ID]["enabled"] is True
    assert _labels(on) == [[f"{TITLE} · 148"], ["⬅️ Панель"]]


async def test_undo_turns_the_bot_back_on_and_offers_no_second_undo() -> None:
    settings, _, service, data = _world()
    off = await _tap(cb("adm_wl_rm:", "ru", CHAT_ID, 0), data)
    service.reset_mock()

    on = await _tap(_payload(off, "↩️ Вернуть"), data)

    assert settings.rows[CHAT_ID]["enabled"] is True
    assert settings.writes[-1] == (CHAT_ID, "enabled", True)
    service.invalidate.assert_called_once_with(CHAT_ID)
    assert _text(on).splitlines()[0] == f"<i>✓ Бот снова работает в чате «{TITLE}»</i>"
    assert _labels(on) == [
        ["🙏 Запросы доступа · 1"],
        [f"{TITLE} · 148"],
        ["Чат 1 · 10"],
        ["Чат 2 · 9"],
        ["◀", "1/2", "▶"],
        ["🚫 Отклонённые · 2", "⬅️ Панель"],
    ]


async def test_undo_when_already_on_writes_nothing_and_says_the_same() -> None:
    """Another admin or an approval got there first, or the button was tapped twice."""
    settings, _, _, data = _world(enabled=True)

    on = await _tap(cb("adm_wl_on:", "en", CHAT_ID, 0), data)

    assert settings.writes == []
    assert _text(on).splitlines()[0] == f"<i>✓ The bot is back on in «{TITLE}»</i>"
    assert "↩️ Undo" not in sum(_labels(on), [])


async def test_a_garbled_undo_is_stale_and_writes_nothing() -> None:
    settings, _, _, data = _world(enabled=False)

    with patch.object(A, "show_stale", new=AsyncMock()) as stale:
        await _tap("adm_wl_on:ru:not-a-chat:0", data)

    stale.assert_awaited_once()
    assert settings.writes == []
    assert settings.rows[CHAT_ID]["enabled"] is False


def test_the_undo_prefix_is_its_own() -> None:
    """``adm_wl_on:`` reaches its handler and no neighbour reaches it, either way round."""
    landing = {
        cb("adm_wl_on:", "ru", CHAT_ID, 0): "admin.handle_wl_on",
        cb("adm_wl_rm:", "ru", CHAT_ID, 0): "admin.handle_wl_remove",
        cb("adm_wl_rm_ask:", "ru", CHAT_ID, 0): "admin.handle_wl_remove_ask",
        cb("adm_wl_rejected:", "ru", 0): "admin.handle_wl_rejected",
        cb("adm_wl_pending:", "ru", 0): "admin.handle_wl_pending",
    }
    assert {p: first_match(p) for p in landing} == landing
    others = [p for p in ROUTES if p != "adm_wl_on:"]
    assert len(others) > 50  # positive control: the walk sees the whole table
    assert [p for p in others if p.startswith("adm_wl_on:") or "adm_wl_on:".startswith(p)] == []


def test_the_longest_undo_payload_fits_telegram() -> None:
    payload = cb("adm_wl_on:", "ru", -1009999999999, 99, REJECTED_BEFORE)

    assert payload == "adm_wl_on:ru:-1009999999999:99:r"
    assert len(payload.encode()) <= 64


# ── «↩️ Вернуть» tapped late ───────────────────────────────────────────────
#
# The undo row stays on its message while the admin works elsewhere: a card for
# the same chat may arrive and be rejected, or the chat may remove the bot. The
# old button must then write nothing and say why.


def _refusals(logs: list[dict[str, Any]]) -> list[tuple[Any, Any]]:
    return [(e["chat_id"], e["reason"]) for e in logs if e["event"] == "undo_refused"]


def _request_while_off(admin: _Admin) -> None:
    """Someone wrote in the chat after the turn-off: a request is waiting."""
    admin.attempts[fake.ATTEMPT_ID] = {
        "id": fake.ATTEMPT_ID,
        "chat_id": CHAT_ID,
        "chat_title": TITLE,
        "chat_type": "supergroup",
        "status": "pending",
        "user_id": fake.USER_ID,
        "user_first_name": "Аня",
        "user_username": None,
        "message_text": "привет",
        "created_at": None,
    }


async def test_undo_after_the_chat_was_rejected_writes_nothing() -> None:
    """Off, then its request rejected on the card, then the old «↩️ Вернуть»."""
    settings, admin, service, data = _world()
    off = await _tap(cb("adm_wl_rm:", "ru", CHAT_ID, 0), data)
    undo = _payload(off, "↩️ Вернуть")
    _request_while_off(admin)
    card = admin_tap(cb("adm_reject:", "ru", fake.ATTEMPT_ID))
    card.bot, card.message.text, card.message.date = None, "card", datetime.now(UTC)
    card.message.edit_text = AsyncMock()
    reject = card.data
    assert first_match(reject) == "access_card.handle_reject_notification"
    await deliver("callback_query", card, FakeState(), call=True, **data)
    assert admin.attempts[fake.ATTEMPT_ID]["status"] == "rejected", "the real card handler ran"
    writes, bot = len(settings.writes), _Bot()
    service.reset_mock()

    with capture_logs() as logs:
        on = await _tap(undo, data, bot=bot)

    assert settings.writes[writes:] == []
    assert settings.rows[CHAT_ID]["enabled"] is False
    service.invalidate.assert_not_called()
    assert _text(on).splitlines()[0] == (
        f"<i>Чат «{TITLE}» отклонили после отключения, ничего не изменилось. Чтобы вернуть "
        "бота, перенесите чат из «🚫 Отклонённые» в запросы и одобрите.</i>"
    )
    assert "↩️ Вернуть" not in sum(_labels(on), [])
    assert _refusals(logs) == [(CHAT_ID, "rejected_since")]
    assert bot.asked == [], "refused before Telegram is asked anything"


async def test_a_rejection_older_than_the_turn_off_does_not_block_the_undo() -> None:
    """A chat approved after an earlier rejection: the rule must not refuse it."""
    settings, admin, _, data = _world()
    admin.rejected_before = True

    off = await _tap(cb("adm_wl_rm:", "en", CHAT_ID, 0), data)
    undo = _payload(off, "↩️ Undo")
    assert undo == f"adm_wl_on:en:{CHAT_ID}:0:{REJECTED_BEFORE}"
    assert first_match(undo) == "admin.handle_wl_on"
    on = await _tap(undo, data)

    assert settings.rows[CHAT_ID]["enabled"] is True
    assert _text(on).splitlines()[0] == f"<i>✓ The bot is back on in «{TITLE}»</i>"


async def test_the_same_old_rejection_without_the_mark_is_refused() -> None:
    """Control for the one above: the mark is what lets it through."""
    settings, admin, _, data = _world(enabled=False)
    admin.rejected_before = True

    on = await _tap(cb("adm_wl_on:", "en", CHAT_ID, 0), data)

    assert settings.writes == []
    assert _text(on).splitlines()[0].startswith(f"<i>«{TITLE}» was rejected after the bot")


def _bot_update(old: Any, new: Any) -> ChatMemberUpdated:
    return ChatMemberUpdated(
        chat=Chat(id=CHAT_ID, type="supergroup", title=TITLE),
        from_user=User(id=fake.USER_ID, is_bot=False, first_name="Аня"),
        date=datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        old_chat_member=old,
        new_chat_member=new,
    )


_KICKED = ChatMemberBanned(user=BOT_USER, until_date=datetime(1970, 1, 1, tzinfo=UTC))


async def test_undo_after_the_bot_was_kicked_writes_nothing_and_the_re_add_asks() -> None:
    """Off, then kicked, then the old «↩️ Вернуть», then added back by anyone.

    The kick writes only what the turn-off wrote already, so Telegram is asked.
    Had the undo turned the chat on, the re-add would have filed no request.
    """
    settings, _, _, data = _world()
    off = await _tap(cb("adm_wl_rm:", "ru", CHAT_ID, 0), data)
    undo = _payload(off, "↩️ Вернуть")
    access = MagicMock()
    access.submit = AsyncMock()
    events = {
        "chat_settings_repo": settings,
        "chat_config_service": MagicMock(spec=ChatConfigService),
        "access_requests": access,
    }
    kick = _bot_update(ChatMemberMember(user=BOT_USER), _KICKED)
    assert await deliver("my_chat_member", kick, FakeState()) == (
        "chat_events.handle_my_chat_member"
    )
    await deliver("my_chat_member", kick, FakeState(), call=True, **events)
    assert settings.writes[-1] == (CHAT_ID, "enabled", False), "all a kick records"
    writes, bot = len(settings.writes), _Bot(_KICKED)

    with capture_logs() as logs:
        on = await _tap(undo, data, bot=bot)

    assert settings.writes[writes:] == []
    assert settings.rows[CHAT_ID]["enabled"] is False
    assert bot.asked == [(CHAT_ID, BOT_ID)]
    assert _text(on).splitlines()[0] == (
        f"<i>Бота уже нет в чате «{TITLE}», ничего не изменилось. Чтобы вернуть, снова "
        "добавьте его в чат.</i>"
    )
    assert "↩️ Вернуть" not in sum(_labels(on), [])
    assert _refusals(logs) == [(CHAT_ID, "bot_gone")]

    readd = _bot_update(_KICKED, ChatMemberMember(user=BOT_USER))
    await deliver("my_chat_member", readd, FakeState(), call=True, **events)
    access.submit.assert_awaited_once()


def _restricted(*, is_member: bool) -> ChatMemberRestricted:
    rights = {
        name: False
        for name, field in ChatMemberRestricted.model_fields.items()
        if field.is_required() and name.startswith("can_")
    }
    return ChatMemberRestricted(
        user=BOT_USER,
        is_member=is_member,
        until_date=datetime(1970, 1, 1, tzinfo=UTC),
        **rights,
    )


_ASK = GetChatMember(chat_id=CHAT_ID, user_id=BOT_ID)


@pytest.mark.parametrize(
    "answer",
    [
        ChatMemberLeft(user=BOT_USER),
        _KICKED,
        _restricted(is_member=False),
        TelegramForbiddenError(_ASK, "Forbidden: bot was kicked from the supergroup chat"),
        TelegramBadRequest(_ASK, "Bad Request: chat not found"),
    ],
    ids=["left", "kicked", "restricted-out", "forbidden", "bad-request"],
)
async def test_every_answer_that_the_bot_is_gone_keeps_the_chat_off(answer: Any) -> None:
    settings, _, _, data = _world(enabled=False)
    settings.rows[CHAT_ID]["chat_title"] = "<b>x</b> & y"

    on = await _tap(cb("adm_wl_on:", "en", CHAT_ID, 0), data, bot=_Bot(answer))

    assert settings.writes == []
    assert _text(on).splitlines()[0] == (
        "<i>The bot is no longer in «&lt;b&gt;x&lt;/b&gt; &amp; y», nothing changed. To bring "
        "it back, add it to the chat again.</i>"
    )


@pytest.mark.parametrize(
    "answer",
    [ChatMemberMember(user=BOT_USER), _restricted(is_member=True)],
    ids=["member", "restricted-in"],
)
async def test_every_answer_that_the_bot_is_there_turns_it_on(answer: Any) -> None:
    """The rule must not refuse a chat the bot is still in, restricted or not."""
    settings, _, _, data = _world(enabled=False)

    await _tap(cb("adm_wl_on:", "ru", CHAT_ID, 0), data, bot=_Bot(answer))

    assert settings.writes == [(CHAT_ID, "enabled", True)]


@pytest.mark.parametrize("marked", [False, True])
async def test_undo_when_telegram_does_not_answer_writes_nothing_and_stays(marked: bool) -> None:
    """Fail closed: nothing is known, so nothing is written, and the undo is offered again."""
    settings, admin, _, data = _world(enabled=False)
    admin.rejected_before = marked
    undo = cb("adm_wl_on:", "ru", CHAT_ID, 1, *([REJECTED_BEFORE] if marked else []))
    down = _Bot(TelegramNetworkError(_ASK, "Request timeout error"))

    with capture_logs() as logs:
        on = await _tap(undo, data, bot=down)

    assert settings.writes == []
    assert _text(on).splitlines()[0] == (
        f"<i>Не получилось проверить, что бот ещё в чате «{TITLE}», ничего не изменилось. "
        "Попробуйте «↩️ Вернуть» ещё раз.</i>"
    )
    assert _payload(on, "↩️ Вернуть") == undo, "the same button, mark and page included"
    assert _refusals(logs) == [(CHAT_ID, "membership_unknown")]
    await _tap(_payload(on, "↩️ Вернуть"), data)
    assert settings.writes == [(CHAT_ID, "enabled", True)], "the retry works once Telegram answers"


async def test_the_normal_undo_asks_telegram_with_the_bots_own_id() -> None:
    settings, _, _, data = _world()
    off = await _tap(cb("adm_wl_rm:", "ru", CHAT_ID, 0), data)
    bot = _Bot()

    await _tap(_payload(off, "↩️ Вернуть"), data, bot=bot)

    assert bot.asked == [(CHAT_ID, BOT_ID)]
    assert settings.rows[CHAT_ID]["enabled"] is True


async def test_a_private_chat_is_not_asked_about() -> None:
    """A DM has no membership to lose; the answer would refuse it, so asking would show."""
    settings, _, _, data = _world()
    _admin_dm(settings)
    off = await _tap(cb("adm_wl_rm:", "ru", ADMIN_ID, 0), data)
    bot = _Bot(_KICKED)

    await _tap(_payload(off, "↩️ Вернуть"), data, bot=bot)

    assert bot.asked == []
    assert settings.rows[ADMIN_ID]["enabled"] is True


class _RowGoneBeforeTheWrite(_Settings):
    """The row is read, then deleted before the write lands (``set_field`` → False)."""

    async def get(self, chat_id: int) -> dict[str, Any] | None:
        row = await super().get(chat_id)
        self.rows.pop(chat_id, None)
        return row


async def test_undo_whose_write_matched_no_row_does_not_say_back_on() -> None:
    settings, _, service, data = _world(enabled=False)
    gone = _RowGoneBeforeTheWrite(settings.rows)
    data["chat_settings_repo"] = gone

    on = await _tap(cb("adm_wl_on:", "ru", CHAT_ID, 0), data)

    assert gone.writes == [(CHAT_ID, "enabled", True)], "the write was tried"
    service.invalidate.assert_not_called()
    assert _text(on).splitlines()[0] == (
        "<i>Этого чата нет в настройках, ничего не изменилось. Вот подключённые чаты.</i>"
    )
    assert "снова работает" not in _text(on)


# ── a chat that was off before the tap ─────────────────────────────────────


async def test_the_question_on_a_chat_already_off_asks_nothing() -> None:
    """An approved card tapped after another admin turned the chat off."""
    settings, _, _, data = _world(enabled=False)

    ask = await _tap(cb("adm_wl_rm_ask:", "ru", CHAT_ID, 0), data)

    text = _text(ask)
    assert text.splitlines()[0] == "🔌 <b>Бот здесь уже отключён</b>"
    assert "Вернуть сразу" not in text
    assert "Позже — когда в чате напишут, появится запрос доступа" in text
    assert _labels(ask) == [["⬅️ Чат"]], "no «🔌 Отключить»: there is nothing to turn off"
    assert first_match(_payload(ask, "⬅️ Чат")) == "admin_chat_panel.handle_chat_panel_menu"
    assert settings.writes == []


async def test_turning_off_a_chat_already_off_writes_nothing_and_offers_no_undo() -> None:
    """The old ask screen still in the chat, tapped late: «↩️ Вернуть» would turn it ON."""
    settings, _, service, data = _world(enabled=False)

    off = await _tap(cb("adm_wl_rm:", "ru", CHAT_ID, 0), data)

    assert settings.writes == []
    assert settings.rows[CHAT_ID]["enabled"] is False
    service.invalidate.assert_not_called()
    assert _text(off).splitlines()[0] == (
        f"<i>Бот в чате «{TITLE}» уже был отключён, ничего не изменилось.</i>"
    )
    assert "↩️ Вернуть" not in sum(_labels(off), [])
    assert _labels(off)[1] == ["Чат 1 · 10"], "an ordinary page: three chats under the requests"


async def test_the_already_off_notice_escapes_the_title() -> None:
    """``notice_raw`` is trusted HTML: the title is data, so it is escaped by hand."""
    settings, _, _, data = _world(enabled=False)
    settings.rows[CHAT_ID]["chat_title"] = "<b>x</b> & y"

    off = await _tap(cb("adm_wl_rm:", "en", CHAT_ID, 0), data)

    assert _text(off).splitlines()[0] == (
        "<i>The bot was already off in «&lt;b&gt;x&lt;/b&gt; &amp; y», nothing changed.</i>"
    )


# ── an admin's own private chat ────────────────────────────────────────────


def _admin_dm(settings: _Settings, *, enabled: bool = True) -> None:
    settings.rows[ADMIN_ID] = {
        "chat_id": ADMIN_ID,
        "chat_title": "Админ",
        "chat_type": "private",
        "enabled": enabled,
        "message_count_24h": 3,
    }


async def test_an_admins_private_chat_is_not_promised_silence() -> None:
    """`AccessControlMiddleware` answers an admin's DM whatever ``enabled`` says."""
    settings, _, _, data = _world()
    _admin_dm(settings)

    ask = await _tap(cb("adm_wl_rm_ask:", "ru", ADMIN_ID, 0), data)

    lines = _text(ask).splitlines()
    assert lines[0] == "🔌 <b>Отключить этот чат?</b>"
    assert lines[3].startswith("Это личный чат администратора: здесь бот отвечает всегда")
    assert "Бот перестанет отвечать" not in _text(ask)
    assert lines[-1] == "Позже запроса не будет: из личного чата администратора он не приходит."
    off = await _tap(_payload(ask, "🔌 Отключить"), data)

    assert settings.rows[ADMIN_ID]["enabled"] is False
    assert _text(off).splitlines()[0] == (
        "<i>✓ Чат «Админ» убран из «🗂 Чаты», бот отвечает в нём, как раньше</i>"
    )
    assert _payload(off, "↩️ Вернуть") == f"adm_wl_on:ru:{ADMIN_ID}:0"


async def test_a_private_chat_of_someone_else_is_an_ordinary_chat() -> None:
    """Positive control for the DM variant: the same row, a user who is not an admin."""
    settings, _, _, data = _world()
    _admin_dm(settings)
    other = ADMIN_ID + 1
    settings.rows[other] = {**settings.rows.pop(ADMIN_ID), "chat_id": other}

    ask = await _tap(cb("adm_wl_rm_ask:", "ru", other, 0), data)

    assert _text(ask).splitlines()[0] == "🔌 <b>Отключить бота здесь?</b>"
    assert "Бот перестанет отвечать" in _text(ask)


async def test_an_admins_private_chat_already_off_says_both() -> None:
    settings, _, _, data = _world()
    _admin_dm(settings, enabled=False)

    ask = await _tap(cb("adm_wl_rm_ask:", "en", ADMIN_ID, 0), data)

    lines = _text(ask).splitlines()
    assert lines[0] == "🔌 <b>This chat is already off</b>"
    assert lines[3].startswith("This is an admin's private chat: the bot always answers here")
    assert _labels(ask) == [["⬅️ Chat"]]


# ── paging from the undo render ────────────────────────────────────────────


async def test_next_from_the_undo_render_skips_no_chat() -> None:
    """The undo page holds two chats; ▶ must open chats 3–4, not 4–6."""
    settings, admin, _, data = _world()
    off = await _tap(cb("adm_wl_rm:", "ru", CHAT_ID, 0), data)
    assert admin.fetched[-1] == (0, 2)
    nxt = _payload(off, "▶")
    assert nxt == f"adm_pnl:ru:1:{SHORT_PAGES}"
    assert first_match(nxt) == "admin_chat_panel.handle_chat_panel_picker"

    page = await _tap(nxt, data)

    assert admin.fetched[-1] == (1, 2), "the same page size as the render it came from"
    assert "↩️ Вернуть" not in sum(_labels(page), [])
    assert _labels(page)[1:3] == [["Чат 3 · 8"], ["Чат 4 · 7"]]
    assert "показаны 3–4" in _text(page)
    back_to_first = _payload(page, "◀")
    assert back_to_first == f"adm_pnl:ru:0:{SHORT_PAGES}", "the marker survives the walk"
    first = await _tap(back_to_first, data)
    assert _labels(first)[1:3] == [["Чат 1 · 10"], ["Чат 2 · 9"]]


async def test_an_ordinary_page_has_no_marker() -> None:
    """Positive control: the list opened from the hub pages at its own size."""
    _, admin, _, data = _world()

    shown = await _tap(cb("adm_pnl:", "ru", 0), data)

    assert admin.fetched[-1] == (0, 3)
    assert _payload(shown, "▶") == "adm_pnl:ru:1"


@pytest.mark.parametrize("page", [0, 1])
def test_the_undo_page_keeps_the_rows_cap(page: int) -> None:
    """Undo + requests + rejected + pager: the fullest the list gets."""
    per_page = chats_per_page(1, 2, undo=True)
    rows = [
        {"chat_id": cid, "chat_title": f"Чат {cid}", "chat_type": "supergroup"}
        for cid in OTHERS[:per_page]
    ]
    screen = build_chats(
        "ru", rows, total=4, page=page, pages=2, requests=1, rejected=2, undo=CHAT_ID
    )

    assert len(screen.rows) == 6
    assert screen.rows[0][0].label == "↩️ Вернуть"
    assert screen.rows[0][0].callback == f"adm_wl_on:ru:{CHAT_ID}:{page}"
    assert len(screen.rows[2:-2]) == per_page
    if page == 1:
        assert "показаны 3–4" in screen.text
    assert check_screen(screen, None, "ru") == []


# ── the approved card and the hub ──────────────────────────────────────────


async def test_the_approved_cards_turn_off_opens_the_question_for_that_chat() -> None:
    settings, _, _, data = _world()
    card = cards.access_approved("ru", cards.Applicant(chat_id=CHAT_ID, chat_title=TITLE))
    (row,) = card.rows
    assert [b.label for b in row] == ["⚙️ Настройки чата", "🔌 Отключить"]

    ask = await _tap(row[1].callback or "", data)

    assert "Отключить бота здесь?" in _text(ask)
    assert f"<b>{TITLE}</b>" in _text(ask)
    assert settings.writes == [], "a question, not a write"
    assert _payload(ask, "🔌 Отключить") == f"adm_wl_rm:ru:{CHAT_ID}:0"
    keep = _payload(ask, "⬅️ Не отключать")
    assert first_match(keep) == "admin_chat_panel.handle_chat_panel_menu"


async def test_the_hub_opens_the_requests_in_one_tap() -> None:
    _, _, _, data = _world()
    hub = build_hub("ru", health=None, pending=1)
    requests = next(b for row in hub.rows for b in row if b.label.startswith("🙏"))
    assert requests.label == "🙏 Запросы · 1"

    shown = await _tap(requests.callback or "", data)

    assert "Новый чат" in _text(shown)
    assert "👍 Одобрить" in sum(_labels(shown), [])
