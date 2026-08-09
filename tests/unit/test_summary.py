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


class TestSummaryMentionsAdversarial:
    """Broader adversarial pass on first_name (M-2), extending M-1's base HTML-injection test.

    first_name is fully attacker-controlled (any Telegram user sets their own). The only
    contract that must hold is: whatever the name contains, it can never (a) escape the
    ``<a>`` element it's rendered into, (b) forge a *different* ``href`` than the
    ``tg://user?id={real_user_id}`` the code itself builds from a trusted DB column, and
    (c) get re-interpreted as a second ``@@uN@@`` token during resolution.
    """

    @pytest.mark.asyncio
    async def test_name_cannot_forge_a_fake_mention_anchor(
        self, summary_service, ai_router, message_repo
    ):
        """A name containing a hand-built ``<a href="tg://user?id=<victim>">`` must not
        survive as real markup — that would let an attacker impersonate a mention to a
        user_id of their choosing (phishing / spoofed victim link)."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(
                user_id=7, first_name='</a><a href="tg://user?id=999">Admin', username=None
            )
        ]
        ai_router.generate_text.return_value = _make_text_result("Писал @@u0@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert 'href="tg://user?id=999"' not in result
        assert result.count("<a href=") == 1
        assert '<a href="tg://user?id=7">' in result

    @pytest.mark.asyncio
    async def test_name_cannot_break_out_via_quote_characters(
        self, summary_service, ai_router, message_repo
    ):
        """Quotes in the name must not terminate the href attribute early — even though the
        name is rendered as element *text*, not as an attribute value, this pins that fact."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=3, first_name='"><script>alert(1)</script>', username=None)
        ]
        ai_router.generate_text.return_value = _make_text_result("Писал @@u0@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert "<script>" not in result
        assert result.count("<a href=") == 1
        assert '<a href="tg://user?id=3">' in result

    @pytest.mark.asyncio
    async def test_name_scheme_lookalike_does_not_change_the_href(
        self, summary_service, ai_router, message_repo
    ):
        """A name that merely *looks* like a URL/scheme has no effect on the href — it's
        rendered as inert text, and the href always comes from the trusted user_id column."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=13, first_name="javascript:alert(document.cookie)")
        ]
        ai_router.generate_text.return_value = _make_text_result("Писал @@u0@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert '<a href="tg://user?id=13">javascript:alert(document.cookie)</a>' in result

    @pytest.mark.asyncio
    async def test_rtl_override_characters_do_not_break_anchor_structure(
        self, summary_service, ai_router, message_repo
    ):
        """Bidi control chars (U+202E etc.) are a known *visual*-spoofing vector but must not
        also break the HTML structure we emit — exactly one well-formed anchor either way."""
        spoofed_name = "Alice‮⁦>a/<⁩"  # RLO + isolates around fake closing tag
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=21, first_name=spoofed_name, username=None)
        ]
        ai_router.generate_text.return_value = _make_text_result("Писал @@u0@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert result.count("<a href=") == 1
        assert result.count("</a>") == 1
        assert '<a href="tg://user?id=21">' in result

    @pytest.mark.asyncio
    async def test_extremely_long_name_does_not_crash_and_stays_escaped(
        self, summary_service, ai_router, message_repo
    ):
        """No length limit is enforced at this layer — pin that a pathologically long,
        HTML-hostile name is still fully escaped and doesn't raise."""
        long_name = "<script>" * 5000  # ~40KB, all HTML-hostile
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=55, first_name=long_name, username=None)
        ]
        ai_router.generate_text.return_value = _make_text_result("Писал @@u0@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert "<script>" not in result
        assert result.count("<a href=") == 1

    @pytest.mark.asyncio
    async def test_name_equal_to_another_participants_token_is_not_re_resolved(
        self, summary_service, ai_router, message_repo
    ):
        """A name that is itself literal ``@@u1@@`` text must not be re-substituted into a
        second anchor — resolution is a single non-recursive pass over the model's output,
        and the inserted name text must never be re-scanned for tokens."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=1, first_name="@@u1@@", username=None, content="hi"),
            _make_message_row(user_id=2, first_name="Bob", username=None, content="hey"),
        ]
        ai_router.generate_text.return_value = _make_text_result("Писали @@u0@@ и @@u1@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        # Exactly two real anchors (one per genuine participant) — the literal "@@u1@@"
        # text injected via user 0's name must not spawn a third, forged anchor.
        assert result.count("<a href=") == 2
        assert '<a href="tg://user?id=1">@@u1@@</a>' in result
        assert '<a href="tg://user?id=2">Bob</a>' in result

    @pytest.mark.asyncio
    async def test_name_with_null_and_control_bytes_does_not_crash(
        self, summary_service, ai_router, message_repo
    ):
        """Defensive: a name with embedded control characters must not raise, even though a
        real NUL byte can't reach us via Postgres in practice (text columns reject it)."""
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=8, first_name="Al\x00ice\x07", username=None)
        ]
        ai_router.generate_text.return_value = _make_text_result("Писал @@u0@@.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert result.count("<a href=") == 1
        assert '<a href="tg://user?id=8">' in result


class TestSummaryEmojiInstruction:
    """System prompt instructs the model to use emoji freely (E-1).

    Owner decision [E-1]=B: free placement, model's own discretion, no fixed
    skeleton of blocks/emoji. This only pins that the instruction is present
    in both language branches and says "free" rather than dictating a fixed
    set — it does not (and cannot) assert anything about model output, since
    ``generate_text`` is mocked. Interaction with markdown_to_html() (e.g.
    ``- 🔥 topic`` → ``• 🔥 topic``) is qa-owned (E-2).
    """

    @pytest.mark.asyncio
    async def test_ru_system_prompt_instructs_free_emoji_use(self, summary_service, ai_router):
        await summary_service.generate(chat_id=-1, language="ru")

        system_prompt = ai_router.generate_text.call_args.kwargs["system_prompt"]
        assert "emoji" in system_prompt
        # Free placement, not a dictated fixed skeleton/set (owner decision [E-1]=B).
        assert "без фиксированного" in system_prompt

    @pytest.mark.asyncio
    async def test_en_system_prompt_instructs_free_emoji_use(self, summary_service, ai_router):
        await summary_service.generate(chat_id=-1, language="en")

        system_prompt = ai_router.generate_text.call_args.kwargs["system_prompt"]
        assert "emoji" in system_prompt
        assert "no fixed" in system_prompt

    @pytest.mark.asyncio
    async def test_emoji_instruction_does_not_leak_into_prompt_content(
        self, summary_service, ai_router
    ):
        """The instruction lives in system_prompt only — not duplicated into the
        conversation payload sent as ``prompt``."""
        await summary_service.generate(chat_id=-1, language="ru")

        prompt = ai_router.generate_text.call_args.kwargs["prompt"]
        assert "фиксированного" not in prompt


class TestSummaryMentionTokenAndCodeInteraction:
    """E-2 live quality run (2026-08-04): calling the real SummaryService.generate()
    pipeline against real gemini-3-flash-preview and gpt-5-nano (both cheap-tier
    defaults, config/default.yml) surfaced two interactions between the M-1
    mention-token mechanism and markdown_to_html()/model output that a fully
    mocked suite can't see, because the mock always echoes the token in the
    exact shape the test author expects.
    """

    @pytest.mark.asyncio
    async def test_single_at_token_variant_from_live_gpt5_nano_does_not_leak(
        self, summary_service, ai_router, message_repo
    ):
        """Live-observed (E-2, 2026-08-04, gpt-5-nano, ru): told to reuse
        "@@u0@@" verbatim, the model wrote "@u0". The loose pattern repairs it
        into a real mention instead of leaking the raw token as visible text.
        """
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=42, first_name="Alice", username="alice")
        ]
        ai_router.generate_text.return_value = _make_text_result("Автор: @u0.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert "@u0" not in result
        assert '<a href="tg://user?id=42">Alice</a>' in result

    @pytest.mark.asyncio
    async def test_loose_token_with_unknown_index_is_left_untouched(
        self, summary_service, ai_router, message_repo
    ):
        """The repair must not fire on prose that merely looks like a token —
        an index nobody owns is far likelier to be ordinary text, so it is left
        exactly as written rather than replaced with the generic fallback.
        """
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=42, first_name="Alice", username="alice")
        ]
        ai_router.generate_text.return_value = _make_text_result(
            "Обсуждали ветку @u77 и адрес bug@u3.example."
        )

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert "@u77" in result
        assert "bug@u3.example" in result
        assert "участник" not in result

    @pytest.mark.asyncio
    async def test_backtick_wrapped_token_resolves_inside_code_tag(
        self, summary_service, ai_router, message_repo
    ):
        """Live-observed (E-2, 2026-08-04, gemini-3-flash-preview, en run): the
        model sometimes wraps its own token echo in backticks (inline code),
        e.g. `` `@@u0@@` ``. markdown_to_html() converts that to
        ``<code>@@u0@@</code>`` *before* _resolve_mentions() runs, and
        resolution still matches the token text inside the <code> tag,
        producing ``<code><a href="...">Name</a></code>`` -- an <a> nested
        inside a <code> element.

        The E-2 follow-up has since been resolved against a real sendMessage
        (2026-08-04): Telegram ACCEPTS that markup with HTTP 200 but returns the
        message carrying only the `code` entity -- the text_mention is silently
        dropped, so the mention renders as dead monospace text. The wrapper is
        therefore removed when it holds nothing but the token, keeping the
        mention clickable.
        """
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=1, first_name="Alice", username="alice")
        ]
        ai_router.generate_text.return_value = _make_text_result("Автор: `@@u0@@`.")

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert '<a href="tg://user?id=1">Alice</a>' in result
        assert "<code>" not in result

    @pytest.mark.asyncio
    async def test_token_inside_a_larger_code_block_degrades_to_plain_name(
        self, summary_service, ai_router, message_repo
    ):
        """A token surrounded by other content inside a fenced block cannot be
        unwrapped without mangling the block, and Telegram discards an anchor
        placed there. It resolves to the bare (escaped) name instead, so we
        never ship markup that is known to be thrown away.
        """
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=1, first_name="Alice", username="alice")
        ]
        ai_router.generate_text.return_value = _make_text_result(
            "Итог:\n```\n@@u0@@ согласился\n```"
        )

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert "<pre>" in result
        assert "Alice согласился" in result
        assert "<a href=" not in result
        assert "@@u0@@" not in result

    @pytest.mark.asyncio
    async def test_attacker_name_stays_escaped_when_it_lands_inside_code(
        self, summary_service, ai_router, message_repo
    ):
        """The plain-name branch is a second insertion point for an
        attacker-controlled first_name, so it must carry the same escaping as
        the anchor branch.
        """
        message_repo.get_for_summary.return_value = [
            _make_message_row(user_id=1, first_name="</code><b>pwn", username=None)
        ]
        ai_router.generate_text.return_value = _make_text_result(
            "Итог:\n```\n@@u0@@ согласился\n```"
        )

        result = await summary_service.generate(chat_id=-1, language="ru")

        assert "</code><b>pwn" not in result
        assert "&lt;/code&gt;&lt;b&gt;pwn" in result


class TestSummaryCountParam:
    """E-1: /summary <n> — count flows into the DB fetch limit, and the header
    reflects the number of messages actually summarized, not the request
    (e.g. requesting 500 when only 2 exist must not claim 500 were used).
    """

    @pytest.mark.asyncio
    async def test_count_forwarded_as_fetch_limit(self, summary_service, message_repo) -> None:
        await summary_service.generate(chat_id=-1, count=500, language="ru")

        message_repo.get_for_summary.assert_awaited_once()
        assert message_repo.get_for_summary.call_args.kwargs["limit"] == 500

    @pytest.mark.asyncio
    async def test_header_reflects_actual_fetched_count_not_requested_ru(
        self, summary_service, ai_router, message_repo
    ) -> None:
        message_repo.get_for_summary.return_value = [
            _make_message_row(content="one"),
            _make_message_row(content="two"),
        ]
        ai_router.generate_text.return_value = _make_text_result("A short summary.")

        result = await summary_service.generate(chat_id=-1, count=500, language="ru")

        assert "(2 сообщений)" in result
        assert "500" not in result

    @pytest.mark.asyncio
    async def test_header_reflects_actual_fetched_count_en(
        self, summary_service, ai_router, message_repo
    ) -> None:
        message_repo.get_for_summary.return_value = [_make_message_row(content="one")]
        ai_router.generate_text.return_value = _make_text_result("A short summary.")

        result = await summary_service.generate(chat_id=-1, count=500, language="en")

        assert "(1 messages)" in result
        assert "500" not in result


class TestSummaryConversationTruncation:
    """E-1: a conservative safety net protects the model call from a
    pathologically large prompt now that /summary can request up to 1000
    messages (see ``_MAX_CONVERSATION_CHARS`` — deliberately coarse, not a
    tuned per-provider context-window limit).
    """

    @pytest.mark.asyncio
    async def test_oversized_conversation_is_trimmed_to_recent_messages(
        self, summary_service, ai_router, message_repo
    ) -> None:
        from src.services.modules import summary as summary_module

        # get_for_summary returns rows newest-first (real query: ORDER BY
        # created_at DESC), so index 0 here is the newest message and 999
        # the oldest — matching what generate() assumes when it reverses
        # the list into chronological order before building lines.
        long_content = "x" * 1000
        rows = [_make_message_row(user_id=1, content=f"{long_content}-{i}") for i in range(1000)]
        message_repo.get_for_summary.return_value = rows
        ai_router.generate_text.return_value = _make_text_result("Summary.")

        await summary_service.generate(chat_id=-1, count=1000, language="ru")

        prompt = ai_router.generate_text.call_args.kwargs["prompt"]
        assert len(prompt) < summary_module._MAX_CONVERSATION_CHARS + 100
        # Newest message (row index 0) survives; the oldest (row index 999)
        # is dropped to make room.
        assert "-0" in prompt
        assert "-999" not in prompt
        # Truncation kept some but not all 1000 messages.
        assert 0 < prompt.count("@@u0@@") < 1000

    @pytest.mark.asyncio
    async def test_truncation_logs_a_warning(
        self, summary_service, ai_router, message_repo, monkeypatch
    ) -> None:
        from src.services.modules import summary as summary_module

        mock_logger = MagicMock()
        monkeypatch.setattr(summary_module, "logger", mock_logger)

        long_content = "x" * 1000
        rows = [_make_message_row(user_id=1, content=f"{long_content}-{i}") for i in range(1000)]
        message_repo.get_for_summary.return_value = rows
        ai_router.generate_text.return_value = _make_text_result("Summary.")

        await summary_service.generate(chat_id=-1, count=1000, language="ru")

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "summary_conversation_truncated"

    @pytest.mark.asyncio
    async def test_small_conversation_is_not_truncated_and_no_warning(
        self, summary_service, ai_router, monkeypatch
    ) -> None:
        from src.services.modules import summary as summary_module

        mock_logger = MagicMock()
        monkeypatch.setattr(summary_module, "logger", mock_logger)
        ai_router.generate_text.return_value = _make_text_result("Summary.")

        result = await summary_service.generate(chat_id=-1, count=100, language="ru")

        mock_logger.warning.assert_not_called()
        assert "(1 сообщений)" in result
