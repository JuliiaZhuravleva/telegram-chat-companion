"""Bot command registration with Telegram API.

Registers commands with proper scopes so users see autocomplete hints.
Different command sets for groups, private chats, and admin users.
"""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Command definitions per language
# ---------------------------------------------------------------------------

_GROUP_COMMANDS: dict[str, list[BotCommand]] = {
    "ru": [
        BotCommand(command="help", description="Возможности бота"),
        BotCommand(command="summary", description="Саммари чата"),
    ],
    "en": [
        BotCommand(command="help", description="Bot features"),
        BotCommand(command="summary", description="Chat summary"),
    ],
}

_PRIVATE_COMMANDS: dict[str, list[BotCommand]] = {
    "ru": [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="Возможности бота"),
    ],
    "en": [
        BotCommand(command="start", description="Start the bot"),
        BotCommand(command="help", description="Bot features"),
    ],
}

_ADMIN_COMMANDS: dict[str, list[BotCommand]] = {
    "ru": [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="Возможности бота"),
        BotCommand(command="admin", description="Панель администратора"),
        BotCommand(command="settings", description="Настройки бота"),
        BotCommand(command="costs", description="Расходы на AI за 24ч"),
    ],
    "en": [
        BotCommand(command="start", description="Start the bot"),
        BotCommand(command="help", description="Bot features"),
        BotCommand(command="admin", description="Admin panel"),
        BotCommand(command="settings", description="Bot settings"),
        BotCommand(command="costs", description="AI cost summary (24h)"),
    ],
}


async def setup_bot_commands(bot: Bot, admin_ids: list[int]) -> None:
    """Register bot commands with Telegram for autocomplete hints.

    Sets different command menus depending on context:
    - Group chats: /help, /summary
    - Private chats (default): /start, /help
    - Admin private chats: /start, /help, /admin, /settings
    """
    # Group chats — /help, /summary
    for lang, commands in _GROUP_COMMANDS.items():
        await bot.set_my_commands(
            commands,
            scope=BotCommandScopeAllGroupChats(),
            language_code=lang,
        )

    # Private chats (default) — /start, /help
    for lang, commands in _PRIVATE_COMMANDS.items():
        await bot.set_my_commands(
            commands,
            scope=BotCommandScopeAllPrivateChats(),
            language_code=lang,
        )

    # Admin users — extended command set in private chats
    for admin_id in admin_ids:
        for lang, commands in _ADMIN_COMMANDS.items():
            try:
                await bot.set_my_commands(
                    commands,
                    scope=BotCommandScopeChat(chat_id=admin_id),
                    language_code=lang,
                )
            except Exception:
                logger.warning(
                    "Failed to set commands for admin",
                    admin_id=admin_id,
                    lang=lang,
                )

    logger.info(
        "Bot commands registered",
        admin_count=len(admin_ids),
    )
