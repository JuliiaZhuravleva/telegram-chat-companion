"""`scripts/community_rollout.py`: which groups are ready, and what --apply refuses."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus

from scripts.community_rollout import (
    ORDINARY_GROUP,
    ChatState,
    apply,
    collect,
    format_report,
    refused,
)

BOT_ID = 900201


def _state(chat_id: int = -1009990201, **overrides: Any) -> ChatState:
    fields: dict[str, Any] = {
        "chat_id": chat_id,
        "title": "Клуб",
        "whitelisted": True,
        "community_enabled": False,
        "bot_status": "administrator",
    }
    fields.update(overrides)
    return ChatState(**fields)


class TestVerdict:
    @pytest.mark.parametrize(
        ("overrides", "verdict"),
        [
            ({}, "ready"),
            ({"bot_status": "member"}, "ready"),
            ({"bot_status": "restricted"}, "ready"),
            ({"bot_status": "left"}, "bot not in chat (left)"),
            ({"bot_status": "kicked"}, "bot not in chat (kicked)"),
            ({"whitelisted": False}, "not approved"),
            (
                {"bot_status": None, "error": "TelegramBadRequest"},
                "unreachable (TelegramBadRequest)",
            ),
        ],
    )
    def test_matrix(self, overrides: dict[str, Any], verdict: str) -> None:
        assert _state(**overrides).verdict == verdict

    def test_a_chat_the_bot_left_is_not_ready_even_when_approved(self) -> None:
        """The whole point of asking Telegram: the row says yes, the chat says no."""
        assert not _state(whitelisted=True, bot_status="left").ready


class TestRefused:
    def test_only_ready_chats_pass(self) -> None:
        states = [
            _state(-1),
            _state(-2, bot_status="left"),
            _state(-3, whitelisted=False),
        ]

        assert refused(states, [-1, -2, -3, -4]) == {
            -2: "bot not in chat (left)",
            -3: "not approved",
            -4: "no such group in chat_settings",
        }

    def test_control_all_ready_refuses_nothing(self) -> None:
        assert refused([_state(-1), _state(-2, bot_status="member")], [-1, -2]) == {}


class TestReport:
    def test_ready_first_and_admin_rights_flagged(self) -> None:
        text = format_report(
            [
                _state(-3, bot_status="left", title="Старый"),
                _state(-2, bot_status="member", title="Без прав"),
                _state(-1, title="Клуб"),
            ]
        )
        lines = text.splitlines()
        assert [line.split()[-1] for line in lines[:3]] == ["прав", "Клуб", "Старый"]
        assert "ready: 2 of 3 groups" in text
        assert "1 ready group(s) without admin rights" in text

    def test_no_admin_warning_when_every_ready_chat_has_rights(self) -> None:
        assert "without admin rights" not in format_report([_state()])


class TestCollect:
    async def test_asks_telegram_about_every_row(self, make_chat_config: Any) -> None:
        repo = AsyncMock()
        repo.list_groups.return_value = [
            {"chat_id": -11, "chat_title": "Есть"},
            {"chat_id": -12, "chat_title": "Ушёл"},
            {"chat_id": -13, "chat_title": "Нет такого"},
        ]
        configs = AsyncMock()
        configs.get_config.side_effect = lambda chat_id: make_chat_config(
            chat_id=chat_id, enabled=True
        )
        answers = {
            -11: SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR),
            -12: SimpleNamespace(status="left"),
            -13: RuntimeError("chat not found"),
        }

        async def get_chat_member(chat_id: int, user_id: int) -> Any:
            assert user_id == BOT_ID
            answer = answers[chat_id]
            if isinstance(answer, Exception):
                raise answer
            return answer

        bot = SimpleNamespace(id=BOT_ID, get_chat_member=get_chat_member)

        states = await collect(bot, repo, configs)  # type: ignore[arg-type]

        assert [(s.chat_id, s.verdict) for s in states] == [
            (-11, "ready"),
            (-12, "bot not in chat (left)"),
            (-13, "unreachable (RuntimeError)"),
        ]
        assert states[0].bot_status == "administrator", "the enum must print as its value"


def test_the_preset_is_the_approved_one() -> None:
    """Greet on join, welcome back, public goodbye; nothing to admins, no joke."""
    assert ORDINARY_GROUP == {
        "community_enabled": True,
        "greet_on_first_message": False,
        "welcome_back_enabled": True,
        "farewell_public": True,
        "farewell_dm": False,
        "returner_joke_enabled": False,
    }


class TestApply:
    async def test_writes_only_ready_chats(self) -> None:
        repo = AsyncMock()
        states = [_state(-1), _state(-2, bot_status="left")]

        reasons = await apply(repo, states, [-1, -2])

        assert reasons == {-2: "bot not in chat (left)"}
        repo.upsert.assert_awaited_once_with(-1, **ORDINARY_GROUP)

    async def test_never_writes_a_chat_nobody_named(self) -> None:
        repo = AsyncMock()

        await apply(repo, [_state(-1), _state(-2)], [-2])

        repo.upsert.assert_awaited_once_with(-2, **ORDINARY_GROUP)
