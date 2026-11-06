"""Tests for the rule action executor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import InlineKeyboardMarkup, ReactionTypeEmoji

from src.bot.screens import rules as screens
from src.bot.screens.render import render_text, to_markup
from src.models.rules import Rule, RuleAction, RuleType
from src.services.rules.engine import RuleActionResult
from src.services.rules.executor import RuleActionExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    rule_id: int = 1,
    rule_type: str = "keyword_trigger",
    config: dict | None = None,
) -> Rule:
    return Rule(
        id=rule_id,
        chat_id=-1001,
        rule_type=RuleType(rule_type),
        config=config or {},
    )


def _make_message(
    text: str = "test message",
    user_id: int = 100,
    first_name: str = "Alice",
    username: str = "alice",
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = None
    msg.message_id = 42
    msg.chat = MagicMock()
    msg.chat.id = -1001
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.first_name = first_name
    msg.from_user.username = username
    msg.from_user.is_bot = False
    msg.reply = AsyncMock()
    return msg


def _make_executor(admin_ids: str = "12345", admin_settings: object = None) -> RuleActionExecutor:
    repo = AsyncMock()
    config = {"admin_ids": admin_ids, "admin_settings": admin_settings}
    repo.get = AsyncMock(side_effect=lambda key: config.get(key))
    return RuleActionExecutor(repo)


# ---------------------------------------------------------------------------
# notify_admin tests
# ---------------------------------------------------------------------------


class TestNotifyAdmin:
    @pytest.mark.asyncio
    async def test_sends_to_all_admins(self) -> None:
        executor = _make_executor("111,222")
        bot = AsyncMock()

        action = RuleActionResult(
            rule=_make_rule(config={"name": "spam-words"}),
            action=RuleAction.NOTIFY_ADMIN,
            params={"match_detail": "contains:spam"},
        )
        await executor.execute(action, message=_make_message(), bot=bot)

        assert bot.send_message.call_count == 2
        # First admin
        call_args_1 = bot.send_message.call_args_list[0]
        assert call_args_1[0][0] == 111
        assert "spam-words" in call_args_1[0][1]
        # Second admin
        call_args_2 = bot.send_message.call_args_list[1]
        assert call_args_2[0][0] == 222

    @pytest.mark.asyncio
    async def test_no_admins(self) -> None:
        executor = _make_executor("")
        bot = AsyncMock()

        action = RuleActionResult(
            rule=_make_rule(),
            action=RuleAction.NOTIFY_ADMIN,
            params={"match_detail": "test"},
        )
        await executor.execute(action, message=_make_message(), bot=bot)
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_html_escaping(self) -> None:
        executor = _make_executor("111")
        bot = AsyncMock()

        msg = _make_message(first_name="<script>alert(1)</script>")
        action = RuleActionResult(
            rule=_make_rule(config={"name": "<b>evil</b>"}),
            action=RuleAction.NOTIFY_ADMIN,
            params={"match_detail": "test"},
        )
        await executor.execute(action, message=msg, bot=bot)

        sent_text = bot.send_message.call_args[0][1]
        assert "<script>" not in sent_text
        assert "&lt;script&gt;" in sent_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("admin_settings", "lang"),
        [
            (None, "ru"),
            ('{"lang": "en"}', "en"),
            ({"lang": "en"}, "en"),
            ('{"lang": "de"}', "ru"),
            ("not json", "ru"),
            ("[1, 2]", "ru"),
        ],
        ids=["unset", "en", "en-as-dict", "unknown", "garbled", "not-a-dict"],
    )
    async def test_sends_the_rule_card_in_the_panels_language(
        self, admin_settings: object, lang: str
    ) -> None:
        """The card the admin DM shows, built by the screen itself: its button
        opens the rule as a new message, so the card stays as the record."""
        executor = _make_executor("111", admin_settings)
        bot = AsyncMock()
        msg = _make_message(text="buy spam here")
        msg.chat.title = "Test chat"

        action = RuleActionResult(
            rule=_make_rule(rule_id=7, config={"name": "spam-words", "keywords": ["spam"]}),
            action=RuleAction.NOTIFY_ADMIN,
            params={"match_detail": "contains:spam"},
        )
        await executor.execute(action, message=msg, bot=bot)

        expected = screens.rule_card(
            lang,
            rule_id=7,
            chat_id=-1001,
            name="spam-words",
            chat_title="Test chat",
            first_name="Alice",
            username="alice",
            match_detail="contains:spam",
            excerpt="buy spam here",
        )
        bot.send_message.assert_awaited_once_with(
            111, render_text(expected), parse_mode="HTML", reply_markup=to_markup(expected)
        )
        markup = bot.send_message.await_args.kwargs["reply_markup"]
        assert isinstance(markup, InlineKeyboardMarkup)
        assert markup.inline_keyboard[0][0].callback_data == f"ar_view:{lang}:7:-1001:n"

    @pytest.mark.asyncio
    async def test_a_chat_without_a_title_is_named_by_its_id(self) -> None:
        executor = _make_executor("111")
        bot = AsyncMock()
        msg = _make_message()
        msg.chat.title = None

        action = RuleActionResult(
            rule=_make_rule(config={"keywords": ["spam"]}),
            action=RuleAction.NOTIFY_ADMIN,
            params={},
        )
        await executor.execute(action, message=msg, bot=bot)

        text = bot.send_message.await_args.args[1]
        assert f"Чат: <code>{screens.chat_id_text(-1001)}</code>" in text
        assert "spam" in text, "an unnamed rule is shown by its words"

    @pytest.mark.asyncio
    async def test_send_failure_logged_not_raised(self) -> None:
        executor = _make_executor("111")
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("Network error")

        action = RuleActionResult(
            rule=_make_rule(),
            action=RuleAction.NOTIFY_ADMIN,
            params={"match_detail": "test"},
        )
        # Should not raise
        await executor.execute(action, message=_make_message(), bot=bot)


# ---------------------------------------------------------------------------
# warn_user tests
# ---------------------------------------------------------------------------


class TestWarnUser:
    @pytest.mark.asyncio
    async def test_replies_with_warning(self) -> None:
        executor = _make_executor()
        bot = AsyncMock()
        msg = _make_message()

        action = RuleActionResult(
            rule=_make_rule(config={"warning_message": "No spam allowed!"}),
            action=RuleAction.WARN_USER,
            params={"warning_message": "No spam allowed!"},
        )
        await executor.execute(action, message=msg, bot=bot)

        msg.reply.assert_called_once_with("No spam allowed!", parse_mode=None)

    @pytest.mark.asyncio
    async def test_default_warning(self) -> None:
        executor = _make_executor()
        bot = AsyncMock()
        msg = _make_message()

        action = RuleActionResult(
            rule=_make_rule(),
            action=RuleAction.WARN_USER,
            params={},
        )
        await executor.execute(action, message=msg, bot=bot)

        msg.reply.assert_called_once_with(screens.WARN_FALLBACK("ru"), parse_mode=None)


# ---------------------------------------------------------------------------
# custom_response tests
# ---------------------------------------------------------------------------


class TestCustomResponse:
    @pytest.mark.asyncio
    async def test_template_substitution(self) -> None:
        executor = _make_executor()
        bot = AsyncMock()
        msg = _make_message(first_name="Bob", username="bob")

        action = RuleActionResult(
            rule=_make_rule(
                config={
                    "name": "greeting",
                    "response_template": "Hello {first_name}! @{username} triggered {rule_name}",
                }
            ),
            action=RuleAction.CUSTOM_RESPONSE,
            params={
                "response_template": "Hello {first_name}! @{username} triggered {rule_name}",
            },
        )
        await executor.execute(action, message=msg, bot=bot)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Bob" in reply_text
        assert "bob" in reply_text
        assert "greeting" in reply_text

    @pytest.mark.asyncio
    async def test_values_go_in_as_they_are_because_the_reply_is_plain_text(self) -> None:
        """``parse_mode=None``: Telegram shows the text verbatim, so an escaped
        value would reach the chat as ``Tom &amp; Jerry``, and markup in a
        name cannot render as markup."""
        executor = _make_executor()
        bot = AsyncMock()
        msg = _make_message(first_name='Tom & <a href="https://x.test">J</a>', username="t_j")

        action = RuleActionResult(
            rule=_make_rule(
                config={
                    "name": "R&D",
                    "response_template": "Hi {first_name} (@{username}), {rule_name}",
                }
            ),
            action=RuleAction.CUSTOM_RESPONSE,
            params={},
        )
        await executor.execute(action, message=msg, bot=bot)

        msg.reply.assert_awaited_once_with(
            'Hi Tom & <a href="https://x.test">J</a> (@t_j), R&D', parse_mode=None
        )

    @pytest.mark.asyncio
    async def test_empty_template_skipped(self) -> None:
        executor = _make_executor()
        bot = AsyncMock()
        msg = _make_message()

        action = RuleActionResult(
            rule=_make_rule(),
            action=RuleAction.CUSTOM_RESPONSE,
            params={},
        )
        await executor.execute(action, message=msg, bot=bot)

        msg.reply.assert_not_called()


# ---------------------------------------------------------------------------
# set_reaction tests
# ---------------------------------------------------------------------------


def _reaction_action(emoji: object = None) -> RuleActionResult:
    config: dict = {"name": "pill"}
    if emoji is not None:
        config["emoji"] = emoji
    return RuleActionResult(
        rule=_make_rule(config=config),
        action=RuleAction.SET_REACTION,
        params={"match_detail": "contains:kw"},
    )


class TestSetReaction:
    @pytest.mark.asyncio
    async def test_sets_reaction_on_triggering_message(self) -> None:
        executor = _make_executor()
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(return_value=True)
        msg = _make_message()

        await executor.execute(_reaction_action(emoji="💊"), message=msg, bot=bot)

        bot.set_message_reaction.assert_awaited_once()
        kwargs = bot.set_message_reaction.call_args.kwargs
        assert kwargs["chat_id"] == -1001
        assert kwargs["message_id"] == 42
        assert len(kwargs["reaction"]) == 1
        reaction = kwargs["reaction"][0]
        assert isinstance(reaction, ReactionTypeEmoji)
        assert reaction.emoji == "💊"

    @pytest.mark.asyncio
    async def test_non_reaction_emoji_is_dropped(self) -> None:
        """Fail-closed: an emoji outside Telegram's reaction set never reaches
        the API (it would be rejected there with a BadRequest anyway)."""
        executor = _make_executor()
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(return_value=True)

        await executor.execute(_reaction_action(emoji="🚀"), message=_make_message(), bot=bot)

        bot.set_message_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_emoji_is_dropped(self) -> None:
        executor = _make_executor()
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(return_value=True)

        await executor.execute(_reaction_action(), message=_make_message(), bot=bot)

        bot.set_message_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_string_emoji_is_dropped_not_crashed(self) -> None:
        """Free-form admin JSON can carry a non-string emoji; it must take the
        rejection path, not blow up inside the selector's normalization.

        Calls _set_reaction directly on purpose: through execute() the blanket
        except would swallow the AttributeError and the test could not tell
        the guard from the crash.
        """
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(return_value=True)

        for bad in (128138, ["💊"], {"e": "💊"}):
            await RuleActionExecutor._set_reaction(
                _reaction_action(emoji=bad), message=_make_message(), bot=bot
            )

        bot.set_message_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_unexpected_error_is_swallowed_by_executor(self) -> None:
        """Telegram API errors are already swallowed inside the responder; a
        non-Telegram error propagates out of it, so this exercises execute()'s
        own try/except."""
        executor = _make_executor()
        bot = MagicMock()
        bot.set_message_reaction = AsyncMock(side_effect=RuntimeError("boom"))

        # Should not raise
        await executor.execute(_reaction_action(emoji="💊"), message=_make_message(), bot=bot)
