"""
Telegram Chat Companion - Entry point

An AI-powered Telegram bot that acts as a chat participant,
not just a command responder.
"""

import asyncio
import logging

import asyncpg
import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.handlers import router as main_router
from src.bot.middleware import ChatConfigMiddleware
from src.config import settings
from src.database.connection import close_pool, create_pool
from src.database.repositories import BotConfigRepository, ChatSettingsRepository
from src.services.chat_config import ChatConfigService

_REQUIRED_TABLES = ("bot_config", "chat_settings")


async def _verify_schema(pool: asyncpg.Pool) -> None:
    """Check that required tables exist. Raises RuntimeError if not."""
    for table in _REQUIRED_TABLES:
        exists = await pool.fetchval(
            "SELECT EXISTS("
            "  SELECT 1 FROM information_schema.tables"
            "  WHERE table_name = $1"
            ")",
            table,
        )
        if not exists:
            raise RuntimeError(
                f"Required table '{table}' not found. "
                "Run: psql $DATABASE_URL -f sql/schema.sql"
            )


async def main() -> None:
    """Initialize and start the bot."""
    # Configure logging
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
            if settings.logging.format == "json"
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    logging.basicConfig(
        level=getattr(logging, settings.logging.level.upper()),
        format="%(message)s",
    )

    logger = structlog.get_logger()
    logger.info("Starting Telegram Chat Companion", version="0.1.0")

    # Initialize database pool
    pool = await create_pool(settings.database_url)
    logger.info("Database connection established")

    # Verify schema
    await _verify_schema(pool)
    logger.info("Database schema verified")

    # Initialize repositories and services
    bot_config_repo = BotConfigRepository(pool)
    chat_settings_repo = ChatSettingsRepository(pool)
    config_service = ChatConfigService(
        yaml_settings=settings.bot,
        bot_config_repo=bot_config_repo,
        chat_settings_repo=chat_settings_repo,
    )

    # Initialize bot
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Initialize dispatcher
    dp = Dispatcher()
    dp.message.middleware(ChatConfigMiddleware(config_service))
    dp.include_router(main_router)

    # Store references for access in handlers
    dp["db_pool"] = pool
    dp["config_service"] = config_service

    try:
        logger.info("Bot started, listening for messages...")
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down...")
        await close_pool(pool)
        await bot.session.close()


def run() -> None:
    """Run the bot (entry point for script)."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
