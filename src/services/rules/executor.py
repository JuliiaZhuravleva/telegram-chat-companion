"""Execute rule actions (notify_admin, warn_user, custom_response)."""

from __future__ import annotations

from html import escape

import structlog
from aiogram import Bot
from aiogram.types import Message

from src.database.repositories.bot_config import BotConfigRepository
from src.models.rules import RuleAction
from src.services.rules.engine import RuleActionResult

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
        if not raw:
            return []
        try:
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            logger.warning("Invalid admin_ids format in bot_config", raw=raw)
            return []

    async def _notify_admin(
        self,
        action: RuleActionResult,
        *,
        message: Message,
        bot: Bot,
    ) -> None:
        """Send alert to all admin IDs."""
        admin_ids = await self._get_admin_ids()
        if not admin_ids:
            return

        text = self._format_notification(action, message)

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception:
                logger.warning("Failed to notify admin about rule", admin_id=admin_id)

    @staticmethod
    def _format_notification(action: RuleActionResult, message: Message) -> str:
        """Build HTML notification for a triggered rule."""
        user = message.from_user
        user_display = escape(user.first_name) if user else "Unknown"
        username = f" (@{escape(user.username)})" if user and user.username else ""
        rule_name = escape(
            action.rule.config.get("name", f"Rule #{action.rule.id}")
        )
        excerpt = escape((message.text or message.caption or "")[:100])
        match_detail = escape(action.params.get("match_detail", ""))

        lines = [
            f"📋 <b>Rule triggered:</b> {rule_name}",
            f"<b>Type:</b> {escape(action.rule.rule_type)}",
            f"<b>Chat:</b> <code>{message.chat.id}</code>",
            f"<b>User:</b> {user_display}{username}",
        ]
        if match_detail:
            lines.append(f"<b>Match:</b> {match_detail}")
        if excerpt:
            lines.append(f"<b>Message:</b> {excerpt}")

        return "\n".join(lines)

    @staticmethod
    async def _warn_user(action: RuleActionResult, *, message: Message) -> None:
        """Reply with a warning message (plain text to avoid HTML injection)."""
        warning = (
            action.params.get("warning_message")
            or action.rule.config.get("warning_message")
            or "Warning: you triggered an automated rule."
        )
        await message.reply(warning, parse_mode=None)

    @staticmethod
    async def _custom_response(
        action: RuleActionResult, *, message: Message
    ) -> None:
        """Reply with a template-substituted custom response."""
        template = (
            action.params.get("response_template")
            or action.rule.config.get("response_template")
            or ""
        )
        if not template:
            return

        user = message.from_user
        substitutions = {
            "{first_name}": escape(user.first_name) if user else "Unknown",
            "{username}": escape(user.username or "") if user else "",
            "{rule_name}": escape(
                action.rule.config.get("name", f"Rule #{action.rule.id}")
            ),
        }
        text = template
        for placeholder, value in substitutions.items():
            text = text.replace(placeholder, value)

        await message.reply(text, parse_mode=None)
