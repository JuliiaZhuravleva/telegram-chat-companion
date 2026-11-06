"""Execute rule actions (notify_admin, warn_user, custom_response, set_reaction)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog
from aiogram import Bot
from aiogram.types import Message

from src.database.repositories.bot_config import BotConfigRepository
from src.models.rules import RuleAction
from src.services.modules.reactions.responder import set_reaction
from src.services.modules.reactions.selector import ReactionSelector
from src.services.rules.engine import RuleActionResult
from src.utils import parse_admin_ids

if TYPE_CHECKING:
    from src.bot.screens.model import Screen

logger = structlog.get_logger(__name__)


class RuleActionExecutor:
    """Execute actions for triggered rules."""

    def __init__(self, bot_config_repo: BotConfigRepository) -> None:
        self._bot_config_repo = bot_config_repo

    async def execute(
        self,
        action: RuleActionResult,
        *,
        message: Message,
        bot: Bot,
    ) -> None:
        """Execute a single rule action."""
        try:
            if action.action == RuleAction.NOTIFY_ADMIN:
                await self._notify_admin(action, message=message, bot=bot)
            elif action.action == RuleAction.WARN_USER:
                await self._warn_user(action, message=message)
            elif action.action == RuleAction.CUSTOM_RESPONSE:
                await self._custom_response(action, message=message)
            elif action.action == RuleAction.SET_REACTION:
                await self._set_reaction(action, message=message, bot=bot)
        except Exception:
            logger.warning(
                "Rule action execution failed",
                rule_id=action.rule.id,
                action=action.action,
                exc_info=True,
            )

    async def _get_admin_ids(self) -> list[int]:
        """Resolve admin IDs from bot_config table."""
        raw = await self._bot_config_repo.get("admin_ids")
        return parse_admin_ids(raw)

    async def _admin_lang(self) -> str:
        """The admin panel's language, read where the panel reads it.

        ``admin_settings.lang`` (``AdminRepository.get_admin_language``); the card
        goes to admins, so the chat's own language plays no part. Anything
        unreadable is Russian, the panel's default.
        """
        from src.bot.screens.i18n import lang_of

        raw = await self._bot_config_repo.get("admin_settings")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return "ru"
        return lang_of(data.get("lang") if isinstance(data, dict) else None)

    async def _notify_admin(
        self,
        action: RuleActionResult,
        *,
        message: Message,
        bot: Bot,
    ) -> None:
        """Send the rule's card to every admin, in the panel's language."""
        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        # Imported here: the screens are the bot layer's, and this service is
        # constructed by the DI container long before any of them is needed.
        from src.bot.screens.render import render_text, to_markup

        card = self._card(action, message, await self._admin_lang())
        text, markup = render_text(card), to_markup(card)

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=markup)
            except Exception as exc:
                logger.warning(
                    "Failed to notify admin about rule",
                    admin_id=admin_id,
                    error_type=type(exc).__name__,
                )

    @staticmethod
    def _card(action: RuleActionResult, message: Message, lang: str) -> Screen:
        """``card.rule`` for one firing: the screen builder escapes every value."""
        from src.bot.screens.rules import RuleView, rule_card, rule_name

        rule = action.rule
        view = RuleView(
            id=rule.id, chat_id=rule.chat_id, rule_type=str(rule.rule_type), config=rule.config
        )
        user = message.from_user
        title = message.chat.title
        detail = action.params.get("match_detail")
        return rule_card(
            lang,
            rule_id=rule.id,
            chat_id=message.chat.id,
            name=rule_name(view, lang),
            # A private chat or a test double has no title: the id stands alone.
            chat_title=title if isinstance(title, str) else None,
            first_name=user.first_name if user else None,
            username=user.username if user else None,
            match_detail=detail if isinstance(detail, str) else "",
            excerpt=message.text or message.caption or "",
        )

    @staticmethod
    async def _warn_user(action: RuleActionResult, *, message: Message) -> None:
        """Reply with a warning message (plain text to avoid HTML injection).

        A rule without its own text posts the Russian default: it lands in the
        chat, whose language the bot does not know.
        """
        from src.bot.screens.rules import WARN_FALLBACK

        warning = (
            action.params.get("warning_message")
            or action.rule.config.get("warning_message")
            or WARN_FALLBACK("ru")
        )
        await message.reply(warning, parse_mode=None)

    @staticmethod
    async def _set_reaction(
        action: RuleActionResult,
        *,
        message: Message,
        bot: Bot,
    ) -> None:
        """Set an emoji reaction on the triggering message.

        Fail-closed via ``ReactionSelector``: a config emoji outside
        Telegram's reaction set is dropped with a log line rather than sent
        and rejected by the API. Gating (``rules_enabled``) lives in
        ``RulesMiddleware``, as for every other action.
        """
        candidate = action.rule.config.get("emoji")
        # Free-form admin JSON: a non-string here would crash the selector's
        # normalization and get mislabeled as a code bug by execute()'s
        # blanket except -- degrade it to the rejection path instead.
        resolved = ReactionSelector.select(candidate if isinstance(candidate, str) else None)
        if resolved is None:
            logger.warning(
                "Rule reaction emoji rejected",
                rule_id=action.rule.id,
                emoji=candidate,
            )
            return
        await set_reaction(
            bot,
            chat_id=message.chat.id,
            message_id=message.message_id,
            emoji=resolved,
        )

    @staticmethod
    async def _custom_response(action: RuleActionResult, *, message: Message) -> None:
        """Reply with a template-substituted custom response.

        Sent with ``parse_mode=None``, so the values go in as they are: escaping
        them here showed a name like ``Tom & Jerry`` as ``Tom &amp; Jerry``, and
        plain text cannot carry markup to inject.
        """
        template = (
            action.params.get("response_template")
            or action.rule.config.get("response_template")
            or ""
        )
        if not template:
            return

        user = message.from_user
        substitutions = {
            "{first_name}": user.first_name if user else "Unknown",
            "{username}": (user.username or "") if user else "",
            "{rule_name}": str(action.rule.config.get("name", f"Rule #{action.rule.id}")),
        }
        text = template
        for placeholder, value in substitutions.items():
            text = text.replace(placeholder, value)

        await message.reply(text, parse_mode=None)
