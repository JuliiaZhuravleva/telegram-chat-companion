"""Tests for /remember + /kb command handlers (A4, ADR-0003)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from src.bot.handlers.commands import (
    handle_kb_view_dm,
    handle_kb_view_group,
    handle_kb_view_page,
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


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    return bot


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
            _make_bot(),
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
            _make_bot(),
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
            _make_bot(),
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
            _make_bot(),
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
            _make_bot(),
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
            _make_bot(),
        )

        call_kwargs = knowledge_repo.upsert_fact.call_args.kwargs
        assert call_kwargs["authority_level"] == 4


class TestHandleRememberTypingIndicator:
    """Regression guard: the embedding call in handle_remember must run under
    the shared typing_indicator helper (I-5), and message_thread_id — new on
    this handler as of I-5 — must reach it.
    """

    @pytest.mark.asyncio
    async def test_wraps_embedding_generation(self) -> None:
        msg = _make_message(
            text="/remember место: кафе Луна",
            user_id=ORGANIZER_ID,
            reply_to_message=MagicMock(message_id=5),
        )
        cfg = _make_chat_config()
        knowledge_repo = _make_knowledge_repo()
        bot = _make_bot()

        with patch("src.bot.handlers.commands.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_remember(
                msg,
                cfg,
                knowledge_repo,
                _make_bot_config_repo(),
                _make_chat_settings_repo(),
                _make_ai_router(),
                bot,
            )

        mock_indicator.assert_called_once_with(bot, msg.chat.id, None)
        knowledge_repo.upsert_fact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forwards_message_thread_id(self) -> None:
        msg = _make_message(
            text="/remember место: кафе Луна",
            user_id=ORGANIZER_ID,
            reply_to_message=MagicMock(message_id=5),
        )
        cfg = _make_chat_config()
        bot = _make_bot()

        with patch("src.bot.handlers.commands.typing_indicator") as mock_indicator:
            mock_indicator.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_indicator.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_remember(
                msg,
                cfg,
                _make_knowledge_repo(),
                _make_bot_config_repo(),
                _make_chat_settings_repo(),
                _make_ai_router(),
                bot,
                message_thread_id=777,
            )

        mock_indicator.assert_called_once_with(bot, msg.chat.id, 777)

    @pytest.mark.asyncio
    async def test_no_indicator_on_early_return(self) -> None:
        """Malformed input never reaches the embedding call -- the indicator
        must not fire for guard-clause rejections."""
        msg = _make_message(text="/remember justtext", reply_to_message=MagicMock(message_id=5))
        cfg = _make_chat_config()
        bot = _make_bot()

        with patch("src.bot.handlers.commands.typing_indicator") as mock_indicator:
            await handle_remember(
                msg,
                cfg,
                _make_knowledge_repo(),
                _make_bot_config_repo(),
                _make_chat_settings_repo(),
                _make_ai_router(),
                bot,
            )

        mock_indicator.assert_not_called()

    @pytest.mark.asyncio
    async def test_embedding_failure_still_saves_fact_without_embedding(self) -> None:
        """The embedding call is wrapped in a try/except that already
        tolerates provider failures (fact saved with embedding=None); the
        typing_indicator context manager must not interfere with that path.
        """
        msg = _make_message(
            text="/remember место: кафе Луна",
            user_id=ORGANIZER_ID,
            reply_to_message=MagicMock(message_id=5),
        )
        cfg = _make_chat_config()
        knowledge_repo = _make_knowledge_repo()
        bot = _make_bot()
        ai_router = MagicMock()
        ai_router.generate_embedding = AsyncMock(side_effect=RuntimeError("boom"))

        await handle_remember(
            msg,
            cfg,
            knowledge_repo,
            _make_bot_config_repo(),
            _make_chat_settings_repo(),
            ai_router,
            bot,
        )

        knowledge_repo.upsert_fact.assert_awaited_once()
        assert knowledge_repo.upsert_fact.call_args.kwargs["embedding"] is None
        msg.reply.assert_awaited_once()


class TestHandleKbView:
    @pytest.mark.asyncio
    async def test_group_kb_disabled_early_response_no_facts_leaked(self) -> None:
        """S2-8: kb_enabled=False must get an explicit answer, not a silent
        return -- and must not query facts at all."""
        msg = _make_message(chat_type="group")
        cfg = _make_chat_config(kb_enabled=False)
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[
                {
                    "subject": "секрет",
                    "predicate": "факт",
                    "value": "не должно попасть в ответ",
                    "topic": None,
                    "source_user_id": None,
                    "updated_at": None,
                }
            ]
        )

        await handle_kb_view_group(msg, cfg, repo)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "отключена" in text
        assert "секрет" not in text
        repo.get_active_facts.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dm_kb_disabled_early_response_no_facts_leaked(self) -> None:
        """S2-8: same guard for the DM variant."""
        msg = _make_message(chat_type="private")
        cfg = _make_chat_config(kb_enabled=False)
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(
            return_value=[
                {
                    "subject": "секрет",
                    "predicate": "факт",
                    "value": "не должно попасть в ответ",
                    "topic": None,
                    "source_user_id": None,
                    "updated_at": None,
                }
            ]
        )

        await handle_kb_view_dm(msg, cfg, repo)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "отключена" in text
        assert "секрет" not in text
        repo.get_active_facts.assert_not_awaited()

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


def _make_facts(count: int) -> list[dict[str, object]]:
    return [
        {
            "subject": f"subj{i}",
            "predicate": "факт",
            "value": f"val{i}",
            "topic": None,
            "source_user_id": None,
            "updated_at": None,
        }
        for i in range(count)
    ]


def _make_kb_view_callback(data: str, chat_type: str = "group", chat_id: int = 1) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = chat_type
    callback.message.chat.id = chat_id
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = None
    return callback


class TestHandleKbViewPage:
    """Regression coverage for the previously-dead ``kb_view:`` pagination callback."""

    @pytest.mark.asyncio
    async def test_group_second_page_shows_remaining_facts(self) -> None:
        callback = _make_kb_view_callback("kb_view:ru:1", chat_type="group")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=_make_facts(10))  # 8/page -> page 1 has 2

        await handle_kb_view_page(callback, repo)

        callback.message.edit_text.assert_awaited_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "subj8" in text
        assert "subj0" not in text
        assert "2/2" in text

    @pytest.mark.asyncio
    async def test_dm_second_page_renders_html(self) -> None:
        callback = _make_kb_view_callback("kb_view:ru:1", chat_type="private")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=_make_facts(7))  # 5/page -> page 1 has 2

        await handle_kb_view_page(callback, repo)

        callback.message.edit_text.assert_awaited_once()
        kwargs = callback.message.edit_text.call_args.kwargs
        assert kwargs.get("parse_mode") == "HTML"
        text = callback.message.edit_text.call_args[0][0]
        assert "subj5" in text
        assert "subj0" not in text

    @pytest.mark.asyncio
    async def test_answers_without_edit_when_no_facts(self) -> None:
        callback = _make_kb_view_callback("kb_view:ru:0")
        repo = _make_knowledge_repo()
        repo.get_active_facts = AsyncMock(return_value=[])

        await handle_kb_view_page(callback, repo)

        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()
