"""Tests for the rule action executor."""

from __future__ import annotations

from html import escape
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import InlineKeyboardMarkup, ReactionTypeEmoji

from src.bot.screens import rules as screens
from src.bot.screens.render import render_text, to_markup
from src.models.rules import Rule, RuleAction, RuleType
from src.services.rules.engine import RuleActionResult, RuleEngine
from src.services.rules.executor import RuleActionExecutor
from src.utils.telegram_text import TELEGRAM_MESSAGE_LIMIT, parsed_length

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
# The alert fits one message
# ---------------------------------------------------------------------------

_WIDE = "😀"
"""Two UTF-16 units each: Telegram's 4096 counts those, not characters."""

_LONGEST_TITLE = (_WIDE + "&") * 64
"""128 characters, Telegram's cap on a chat title; ``&`` is five bytes escaped."""


def _worst_message(text: str) -> MagicMock:
    """A message from the longest name and username Telegram allows, in the longest title."""
    msg = _make_message(text=text, first_name=_WIDE * 64, username="u" * 32)
    msg.sticker = None
    msg.chat.title = _LONGEST_TITLE
    return msg


def _sent_texts(bot: AsyncMock) -> list[str]:
    return [call.args[1] for call in bot.send_message.await_args_list]


class TestTheAlertFitsOneMessage:
    """``card.rule`` goes out in one ``send_message`` to every admin: past 4096
    Telegram refuses it, the executor logs a warning, and no admin hears of the
    rule at all. Its parts come from a JSON rule, which bounds no word, and from
    whoever wrote in the chat."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("lang", ["ru", "en"])
    async def test_an_unnamed_rule_of_three_hundred_words_still_reaches_every_admin(
        self, lang: str
    ) -> None:
        longest = _WIDE * 2000
        words = [longest] + [f"{_WIDE * 6}&<{i:05d}>" for i in range(300)]
        # The ingredients alone are over the limit: what follows is not vacuous.
        assert parsed_length(escape(", ".join(words))) > TELEGRAM_MESSAGE_LIMIT
        repo = AsyncMock()
        repo.get_active_rules = AsyncMock(
            return_value=[
                {
                    "id": 7,
                    "chat_id": -1001,
                    "rule_type": "keyword_trigger",
                    "config": {"keywords": words, "action": "notify_admin"},
                    "weight": 1,
                    "mandatory": False,
                    "enabled": True,
                }
            ]
        )
        # 4096 units, the longest message Telegram delivers.
        msg = _worst_message(longest + "&" * 96)
        actions = await RuleEngine(repo).evaluate(
            chat_id=-1001, user_id=100, message=msg, rules_mode="all"
        )
        assert [a.action for a in actions] == [RuleAction.NOTIFY_ADMIN]
        assert actions[0].params["match_detail"] == f"contains:{longest}"

        executor = _make_executor("111,222", f'{{"lang": "{lang}"}}')
        bot = AsyncMock()
        for action in actions:
            await executor.execute(action, message=msg, bot=bot)

        sent = _sent_texts(bot)
        assert len(sent) == 2
        for text in sent:
            assert parsed_length(text) <= TELEGRAM_MESSAGE_LIMIT, parsed_length(text)
        shown = screens.cap_name(", ".join(words))
        assert len(shown) == screens.NAME_CHARS
        assert f"</b> {escape(shown)}\n" in sent[0], "the rule is still named by its words"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("lang", ["ru", "en"])
    @pytest.mark.parametrize(
        ("config", "detail"),
        [
            ({"name": "Я&" * 3000}, "contains:x"),
            ({}, "exact:" + "&" * 5000),
            ({}, "regex:" + _WIDE * 3000),
            ({}, "user:100,kw:" + _WIDE * 3000),
            ({}, "stickers:" + "9" * 3000 + "/" + "9" * 3000),
            ({}, "spam:chars=" + "9" * 3000 + "/" + "9" * 3000),
            ({}, "spam:msgs=" + "9" * 3000 + "/" + "9" * 3000),
            ({}, "something-else:" + "&" * 5000),
        ],
        ids=[
            "stored-name",
            "exact",
            "regex",
            "user-word",
            "stickers",
            "spam-chars",
            "spam-messages",
            "unknown",
        ],
    )
    async def test_no_part_of_the_card_outgrows_it(
        self, lang: str, config: dict, detail: str
    ) -> None:
        """A name written before names were cut, and every kind of match line."""
        assert parsed_length(escape(config.get("name", detail))) > TELEGRAM_MESSAGE_LIMIT
        executor = _make_executor("111", f'{{"lang": "{lang}"}}')
        bot = AsyncMock()
        action = RuleActionResult(
            rule=_make_rule(rule_id=7, config={"keywords": ["x"], **config}),
            action=RuleAction.NOTIFY_ADMIN,
            params={"match_detail": detail},
        )
        await executor.execute(action, message=_worst_message(_WIDE * 2048), bot=bot)

        (text,) = _sent_texts(bot)
        assert parsed_length(text) <= TELEGRAM_MESSAGE_LIMIT, parsed_length(text)


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
# One Telegram message: 4096 UTF-16 units of what is actually sent
# ---------------------------------------------------------------------------

_ASTRAL_NAME = "😀" * 64
"""The longest first name Telegram allows, each character two UTF-16 units."""


def _sent(msg: MagicMock) -> str:
    msg.reply.assert_awaited_once()
    assert msg.reply.call_args.kwargs == {"parse_mode": None}
    text = msg.reply.call_args.args[0]
    assert isinstance(text, str)
    return text


class TestOneMessage:
    """The executor swallows every exception, so a send Telegram would reject
    is silent here; what is asserted is the text handed to ``reply``."""

    @pytest.mark.asyncio
    async def test_a_reply_that_a_long_name_pushes_over_the_limit_is_cut_to_fit(self) -> None:
        """Typed as 3990 characters it fit; with a 64-emoji name it is 4118 units."""
        msg = _make_message(first_name=_ASTRAL_NAME)
        template = "{first_name} " + "я" * 3990
        action = RuleActionResult(
            rule=_make_rule(config={"name": "r", "response_template": template}),
            action=RuleAction.CUSTOM_RESPONSE,
            params={"response_template": template},
        )

        await _make_executor().execute(action, message=msg, bot=AsyncMock())

        text = _sent(msg)
        assert parsed_length(escape(text, quote=False)) <= TELEGRAM_MESSAGE_LIMIT
        assert text.startswith(_ASTRAL_NAME + " яяя")
        assert text.endswith("…")

    @pytest.mark.asyncio
    async def test_a_reply_that_fits_is_sent_whole(self) -> None:
        msg = _make_message(first_name=_ASTRAL_NAME)
        template = "{first_name} " + "я" * 3967  # 128 + 1 + 3967 = 4096 units exactly
        action = RuleActionResult(
            rule=_make_rule(config={"response_template": template}),
            action=RuleAction.CUSTOM_RESPONSE,
            params={},
        )

        await _make_executor().execute(action, message=msg, bot=AsyncMock())

        assert _sent(msg) == _ASTRAL_NAME + " " + "я" * 3967

    @pytest.mark.asyncio
    async def test_an_old_over_long_warning_is_cut_not_dropped(self) -> None:
        """A rule saved before the builder checked lengths: 2049 emoji are 4098 units."""
        msg = _make_message()
        warning = "😀" * 2049
        action = RuleActionResult(
            rule=_make_rule(config={"warning_message": warning}),
            action=RuleAction.WARN_USER,
            params={"warning_message": warning},
        )

        await _make_executor().execute(action, message=msg, bot=AsyncMock())

        text = _sent(msg)
        assert parsed_length(escape(text, quote=False)) <= TELEGRAM_MESSAGE_LIMIT
        assert text == "😀" * 2047 + "…"

    @pytest.mark.asyncio
    async def test_a_name_is_never_read_as_a_placeholder(self) -> None:
        """Sequential replacing filled ``{first_name}`` first and then read the
        name itself for ``{rule_name}``."""
        msg = _make_message(first_name="{rule_name}", username="{first_name}")
        action = RuleActionResult(
            rule=_make_rule(config={"name": "R", "response_template": "{first_name}/{username}"}),
            action=RuleAction.CUSTOM_RESPONSE,
            params={},
        )

        await _make_executor().execute(action, message=msg, bot=AsyncMock())

        assert _sent(msg) == "{rule_name}/{first_name}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [["Привет"], {"t": "Привет"}, 7])
    async def test_a_text_field_that_is_not_text_counts_as_none(self, bad: object) -> None:
        """Hand-written JSON: a list went to ``reply`` and Telegram refused it."""
        msg = _make_message()
        action = RuleActionResult(
            rule=_make_rule(config={"warning_message": bad}),
            action=RuleAction.WARN_USER,
            params={"warning_message": bad},
        )

        await _make_executor().execute(action, message=msg, bot=AsyncMock())

        assert _sent(msg) == screens.WARN_FALLBACK("ru")


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
