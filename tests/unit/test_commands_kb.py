"""Tests for /remember + /kb command handlers (A4, ADR-0003)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.commands import (
    handle_kb_view_dm,
    handle_kb_view_group,
    handle_remember,
)

ADMIN_ID = 111
ORGANIZER_ID = 222
RANDOM_USER_ID = 333


def _make_chat_config(language: str = "ru", kb_enabled: bool = True, chat_id: int = 1) -> MagicMock:
    cfg = MagicMock()
    cfg.language = language
    cfg.kb_enabled = kb_enabled
    cfg.chat_id = chat_id
    return cfg


def _make_message(
    *,
    text: str = "/remember тема: значение",
    user_id: int = ADMIN_ID,
    reply_to_message: MagicMock | None = None,
    chat_id: int = 1,
    chat_type: str = "group",
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.reply_to_message = reply_to_message
    msg.reply = AsyncMock()
    msg.answer = AsyncMock()
    msg.bot = None
    return msg


def _make_bot_config_repo(admin_ids: list[int] | None = None) -> MagicMock:
    repo = MagicMock()
    # BotConfigRepository.get() already returns json.loads() output (a parsed
    # list), not a raw JSON string -- see src/utils/parse_admin_ids docstring.
    repo.get = AsyncMock(return_value=admin_ids or [ADMIN_ID])
    return repo


def _make_chat_settings_repo(organizer_ids: list[int] | None = None) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(
        return_value={"kb_organizer_ids": json.dumps(organizer_ids or [ORGANIZER_ID])}
    )
    return repo


def _make_knowledge_repo() -> MagicMock:
    repo = MagicMock()
    repo.upsert_fact = AsyncMock(return_value=42)
    repo.get_active_facts = AsyncMock(return_value=[])
    return repo


def _make_ai_router() -> MagicMock:
    router = MagicMock()
    result = MagicMock()
    result.embedding = [0.1, 0.2]
    router.generate_embedding = AsyncMock(return_value=result)
    return router


class TestHandleRemember:
    @pytest.mark.asyncio
    async def test_no_reply_to_message(self) -> None:
        msg = _make_message(reply_to_message=None)
        cfg = _make_chat_config()

        await handle_remember(
            msg,
            cfg,
            _make_knowledge_repo(),
            _make_bot_config_repo(),
            _make_chat_settings_repo(),
            _make_ai_router(),
        )

        msg.reply.assert_awaited_once()
        assert "ответ на сообщение" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_kb_disabled(self) -> None:
        msg = _make_message(reply_to_message=MagicMock(message_id=5))
        cfg = _make_chat_config(kb_enabled=False)

        await handle_remember(
            msg,
            cfg,
            _make_knowledge_repo(),
            _make_bot_config_repo(),
            _make_chat_settings_repo(),
            _make_ai_router(),
        )

        msg.reply.assert_awaited_once()
        assert "отключена" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_permission_denied_for_random_user(self) -> None:
        msg = _make_message(user_id=RANDOM_USER_ID, reply_to_message=MagicMock(message_id=5))
        cfg = _make_chat_config()

        await handle_remember(
            msg,
            cfg,
            _make_knowledge_repo(),
            _make_bot_config_repo(),
            _make_chat_settings_repo(),
            _make_ai_router(),
        )

        msg.reply.assert_awaited_once()
        assert "организаторы" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_malformed_no_colon(self) -> None:
        msg = _make_message(text="/remember justtext", reply_to_message=MagicMock(message_id=5))
        cfg = _make_chat_config()

        await handle_remember(
            msg,
            cfg,
            _make_knowledge_repo(),
            _make_bot_config_repo(),
            _make_chat_settings_repo(),
            _make_ai_router(),
        )

        msg.reply.assert_awaited_once()
        assert "Не смог распознать" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success_by_organizer(self) -> None:
        msg = _make_message(
            text="/remember место: кафе Луна",
            user_id=ORGANIZER_ID,
            reply_to_message=MagicMock(message_id=5),
        )
        cfg = _make_chat_config()
        knowledge_repo = _make_knowledge_repo()

        await handle_remember(
            msg,
            cfg,
            knowledge_repo,
            _make_bot_config_repo(),
            _make_chat_settings_repo(),
            _make_ai_router(),
        )

        knowledge_repo.upsert_fact.assert_awaited_once()
        call_kwargs = knowledge_repo.upsert_fact.call_args.kwargs
        assert call_kwargs["subject"] == "место"
        assert call_kwargs["value"] == "кафе Луна"
        assert call_kwargs["source"] == "manual"
        assert call_kwargs["authority_level"] == 3  # organizer, not bot admin
        msg.reply.assert_awaited_once()
        assert "Сохранено" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_success_by_bot_admin_gets_authority_4(self) -> None:
        msg = _make_message(
            text="/remember дата: 24.07",
            user_id=ADMIN_ID,
            reply_to_message=MagicMock(message_id=5),
        )
        cfg = _make_chat_config()
        knowledge_repo = _make_knowledge_repo()

        await handle_remember(
            msg,
            cfg,
            knowledge_repo,
            _make_bot_config_repo(),
            _make_chat_settings_repo(organizer_ids=[]),
            _make_ai_router(),
        )

        call_kwargs = knowledge_repo.upsert_fact.call_args.kwargs
        assert call_kwargs["authority_level"] == 4


class TestHandleKbView:
    @pytest.mark.asyncio
    async def test_group_empty(self) -> None:
        msg = _make_message(chat_type="group")
        cfg = _make_chat_config()
        repo = _make_knowledge_repo()

        await handle_kb_view_group(msg, cfg, repo)

        msg.answer.assert_awaited_once()
        assert "пуста" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_group_terse_no_predicate(self) -> None:
        msg = _make_message(chat_type="group")
        cfg = _make_chat_config()
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[
                {
                    "subject": "место",
                    "predicate": "факт",
                    "value": "Кафе Луна",
                    "topic": None,
                    "source_user_id": None,
                    "updated_at": None,
                }
            ]
        )

        await handle_kb_view_group(msg, cfg, repo)

        text = msg.answer.call_args[0][0]
        assert "место — Кафе Луна" in text
        assert "факт:" not in text  # predicate hidden in group mode

    @pytest.mark.asyncio
    async def test_dm_empty(self) -> None:
        msg = _make_message(chat_type="private")
        cfg = _make_chat_config()
        repo = _make_knowledge_repo()

        await handle_kb_view_dm(msg, cfg, repo)

        msg.answer.assert_awaited_once()
        assert "пуста" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_dm_sectioned_by_topic(self) -> None:
        msg = _make_message(chat_type="private")
        cfg = _make_chat_config()
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[
                {
                    "subject": "место",
                    "predicate": "факт",
                    "value": "Кафе Луна",
                    "topic": "event:лето",
                    "source_user_id": None,
                    "updated_at": None,
                }
            ]
        )

        await handle_kb_view_dm(msg, cfg, repo)

        msg.answer.assert_awaited_once()
        html = msg.answer.call_args[0][0]
        assert "event:лето" in html
        assert "Кафе Луна" in html
