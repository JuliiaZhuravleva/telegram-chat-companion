"""Turning the bot off in one chat, and the ways back, through the real router.

Each tap is delivered through ``src.bot.handlers.router`` (`screen_routing.deliver`)
and its handler runs on in-memory repositories, so what is asserted is what the
admin's phone would get: the rows of the edited message and the value written.
The next tap is the payload read off that message, never one typed into the test.

Ways back covered here: «↩️ Вернуть» on the list right after «🔌 Отключить»
(``adm_wl_on:``), «🔌 Отключить» on an approved access card (the same question
the chat's screen asks), and «🙏 Запросы · N» on the hub (the requests in one tap).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers import admin as A
from src.bot.screens import cards
from src.bot.screens.chat import build_chats, chats_per_page
from src.bot.screens.hub import build_hub
from src.bot.screens.lint import check_screen
from src.bot.screens.routes import ROUTES, cb
from src.bot.screens.samples import fake
from src.database.repositories.admin import AdminRepository
from src.services.chat_config import ChatConfigService
from tests.unit.screen_routing import FakeState, admin_tap, deliver, first_match

# Invented values only (the repository is public): the samples' fakes, plus the
# test suite's long-standing -1001234567890 for the fifth chat.
CHAT_ID = fake.CHAT_ID
OTHERS = (fake.CHAT_ID_SHORT, fake.CHAT_ID_OTHER, fake.CHAT_ID_NEW, -1001234567890)
TITLE = fake.CHAT_TITLE


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


class _Admin:
    """The `AdminRepository` reads the chat list, the hub and the requests make."""

    def __init__(self, settings: _Settings, *, pending: int = 1, rejected: int = 2) -> None:
        self._settings = settings
        self._counts = {"pending": pending, "rejected": rejected}
        self.rejected_before = False

    async def count_attempts(self, status: str) -> int:
        return self._counts[status]

    async def has_rejected_attempt(self, chat_id: int) -> bool:
        return self.rejected_before

    async def get_enabled_chats_page_by_activity(
        self, page: int, per_page: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
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
    data = {
        "chat_settings_repo": settings,
        "admin_repo": admin,
        "dishka_container": _Container({AdminRepository: admin, ChatConfigService: service}),
    }
    return settings, admin, service, data


async def _tap(payload: str, data: dict[str, Any]) -> MagicMock:
    tap = admin_tap(payload)
    tap.bot = None
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
    payload = cb("adm_wl_on:", "ru", -1009999999999, 99)

    assert payload == "adm_wl_on:ru:-1009999999999:99"
    assert len(payload.encode()) <= 64


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
