"""Tests for SummaryService — focus on log_usage() call (B-1 regression)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.ai.base import AIProviderError, TextGenerationResult
from src.services.modules.summary import SummaryService


def _make_text_result(text: str = "Summary text") -> TextGenerationResult:
    return TextGenerationResult(
        text=text,
        model="gpt-5-nano",
        provider="openai",
        tokens_input=200,
        tokens_output=80,
    )


def _make_message_row(**overrides):
    row = {
        "user_id": 111,
        "username": "alice",
        "first_name": "Alice",
        "content": "Hello there",
        "created_at": MagicMock(strftime=lambda _fmt: "12:00"),
        "is_bot_message": False,
    }
    row.update(overrides)
    return row


@pytest.fixture
def message_repo():
    repo = AsyncMock()
    repo.get_for_summary = AsyncMock(return_value=[_make_message_row()])
    return repo


@pytest.fixture
def ai_router():
    router = AsyncMock()
    router.generate_text = AsyncMock(return_value=_make_text_result())
    router.log_usage = AsyncMock()
    return router


@pytest.fixture
def summary_service(message_repo, ai_router):
    return SummaryService(message_repo, ai_router)


class TestSummaryLogUsage:
    """SummaryService must call router.log_usage() after successful generation."""

    @pytest.mark.asyncio
    async def test_calls_log_usage_on_success(self, summary_service, ai_router):
        """log_usage() must be scheduled when generate_text() succeeds."""
        await summary_service.generate(chat_id=-100123, language="ru")

        # Let fire-and-forget tasks complete
        await asyncio.sleep(0.05)

        ai_router.log_usage.assert_awaited_once()
        call_kwargs = ai_router.log_usage.call_args
        assert call_kwargs.kwargs["chat_id"] == -100123
        assert call_kwargs.kwargs["task_type"] == "summary"

    @pytest.mark.asyncio
    async def test_does_not_call_log_usage_on_ai_error(self, summary_service, ai_router):
        """log_usage() must NOT be called when generate_text() raises."""
        ai_router.generate_text.side_effect = AIProviderError("down", provider="openai")

        await summary_service.generate(chat_id=-100123, language="ru")
        await asyncio.sleep(0.05)

        ai_router.log_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_result_passed_to_log_usage(self, summary_service, ai_router):
        """The TextGenerationResult from generate_text() must be forwarded to log_usage()."""
        expected_result = _make_text_result("A short summary.")
        ai_router.generate_text.return_value = expected_result

        await summary_service.generate(chat_id=-999, language="en")
        await asyncio.sleep(0.05)

        positional_args = ai_router.log_usage.call_args.args
        assert positional_args[0] is expected_result

    @pytest.mark.asyncio
    async def test_no_messages_returns_early_without_log(
        self, summary_service, ai_router, message_repo
    ):
        """When there are no messages, generate_text is never called."""
        message_repo.get_for_summary.return_value = []

        result = await summary_service.generate(chat_id=-100123, language="ru")
        await asyncio.sleep(0.05)

        ai_router.generate_text.assert_not_awaited()
        ai_router.log_usage.assert_not_awaited()
        assert result  # returns an explanatory message


class TestSummaryMentions:
    """Placeholder-token mention resolution (M-1).

    The model never sees a real name or id — only an opaque ``@@uN@@`` token
    built from DB rows. Resolution into a safe ``<a href="tg://user?id=...">``
    anchor happens after markdown_to_html(), from data the model never touched.
    """

    @pytest.mark.asyncio
    async def test_conversation_uses_placeholder_not_real_name(
        self, summary_service, ai_router, message_repo
    ):
        """The prompt sent to the model must carry the token, never the real name."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=111, first_name="Alice", username="alice")
        ]

        await summary_service.generate(chat_id=-1, language="ru")

        prompt = ai_router.generate_text.call_args.kwargs["prompt"]
        assert "@@u0@@" in prompt
        assert "Alice" not in prompt
        assert "alice" not in prompt

    @pytest.mark.asyncio
    async def test_valid_token_resolved_to_inline_mention(
        self, summary_service, ai_router, message_repo
    ):
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=42, first_name="Alice", username="alice")
        ]
        ai_router.generate_text.return_value = _make_text_result("Главный участник: @@u0@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert '<a href="tg://user?id=42">Alice</a>' in result
        assert "@@u0@@" not in result

    @pytest.mark.asyncio
    async def test_first_name_html_injection_is_escaped_in_mention(
        self, summary_service, ai_router, message_repo
    ):
        """first_name is attacker-controlled — must be html-escaped inside the anchor."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=7, first_name="</a><b>pwn", username=None)
        ]
        ai_router.generate_text.return_value = _make_text_result("Писал @@u0@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert "</a><b>" not in result  # raw tag never reaches the output
        assert "&lt;/a&gt;&lt;b&gt;pwn" in result
        assert result.count("<a href=") == 1  # only our own anchor tag is real HTML

    @pytest.mark.asyncio
    async def test_bot_messages_never_get_a_mention_token(
        self, summary_service, ai_router, message_repo
    ):
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=1, is_bot_message=True, first_name="Bot"),
        ]

        await summary_service.generate(chat_id=-1, language="ru")

        prompt = ai_router.generate_text.call_args.kwargs["prompt"]
        assert "@@u0@@" not in prompt
        assert "] Bot:" in prompt

    @pytest.mark.asyncio
    async def test_unknown_token_degrades_to_generic_label(
        self, summary_service, ai_router, message_repo
    ):
        """A hallucinated index must never leak the placeholder or break markup."""
        message_repo.get_for_summary.return_value = [_make_message_row(user_id=1)]
        ai_router.generate_text.return_value = _make_text_result("Спросил @@u7@@ про билд.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert "@@u7@@" not in result
        assert "участник" in result
        assert "<a href=" not in result

    @pytest.mark.asyncio
    async def test_unknown_token_english_fallback(self, summary_service, ai_router, message_repo):
        message_repo.get_for_summary.return_value = [_make_message_row(user_id=1)]
        ai_router.generate_text.return_value = _make_text_result("Asked by @@u7@@ about it.")

        result = await summary_service.generate(chat_id=-1, language="en")

        assert "@@u7@@" not in result
        assert "participant" in result

    @pytest.mark.asyncio
    async def test_anonymous_message_no_user_id_gets_plain_name_not_token(
        self, summary_service, ai_router, message_repo
    ):
        """Messages with no user_id (anonymous admin / channel post) can't be mentioned."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=None, first_name="Admin")
        ]

        await summary_service.generate(chat_id=-1, language="ru")

        prompt = ai_router.generate_text.call_args.kwargs["prompt"]
        assert "@@u0@@" not in prompt
        assert "] Admin:" in prompt

    @pytest.mark.asyncio
    async def test_same_user_multiple_messages_reuses_same_token(
        self, summary_service, ai_router, message_repo
    ):
        """rows arrive newest-first (matches get_for_summary's ORDER BY ... DESC)."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=9, content="third"),  # newest
            _make_message_row(user_id=5, content="second"),
            _make_message_row(user_id=5, content="first"),  # oldest
        ]

        await summary_service.generate(chat_id=-1, language="ru")

        prompt = ai_router.generate_text.call_args.kwargs["prompt"]
        # Chronological order (oldest first) assigns user 5 the first token.
        assert prompt.count("@@u0@@") == 2
        assert prompt.count("@@u1@@") == 1
