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
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka

from src.bot.handlers import router as main_router
from src.bot.middleware import (
    AccessControlMiddleware,
    ActivityTrackerMiddleware,
    ChatConfigMiddleware,
    MessageSaverMiddleware,
    RulesMiddleware,
    TopicMiddleware,
)
from src.config import Settings
from src.di import AppProvider, RepositoryProvider, ServiceProvider
from src.services.health.checker import HealthChecker

_REQUIRED_TABLES = ("bot_config", "chat_settings", "custom_rules", "health_log")


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
    settings = Settings()

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

    # Build Dishka DI container
    container = make_async_container(
        AppProvider(),
        RepositoryProvider(),
        ServiceProvider(),
        context={Settings: settings},
    )

    # Resolve the database pool for schema verification
    pool = await container.get(asyncpg.Pool)
    logger.info("Database connection established")

    await _verify_schema(pool)
    logger.info("Database schema verified")

    # Initialize bot
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Initialize dispatcher
    dp = Dispatcher()

    # Wire Dishka into aiogram (registers ContainerMiddleware as outer middleware)
    setup_dishka(container=container, router=dp, auto_inject=True)

    # Register inner middleware (runs after Dishka's ContainerMiddleware).
    # Order matters: outer middleware runs first.
    # 1. ChatConfig — injects chat_config into handler data
    # 2. Topic — extracts message_thread_id for forum support
    # 3. AccessControl — whitelist + admin check (needs chat_config)
    # 4. ActivityTracker — tracks user activity
    # 5. MessageSaver — saves messages to DB (uses message_thread_id)
    chat_config_mw = ChatConfigMiddleware()
    topic_mw = TopicMiddleware()
    access_control_mw = AccessControlMiddleware()
    activity_tracker_mw = ActivityTrackerMiddleware()
    message_saver_mw = MessageSaverMiddleware()
    rules_mw = RulesMiddleware()

    for mw in (chat_config_mw, topic_mw, access_control_mw, activity_tracker_mw, message_saver_mw, rules_mw):
        dp.message.middleware(mw)

    # Callback queries need chat_config, topic, and access control too
    dp.callback_query.middleware(chat_config_mw)
    dp.callback_query.middleware(topic_mw)
    dp.callback_query.middleware(access_control_mw)

    dp.include_router(main_router)

    # Start health checker background task
    health_checker = HealthChecker(pool=pool, bot=bot)
    await health_checker.start()

    try:
        logger.info("Bot started, listening for messages...")
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down...")
        await health_checker.stop()
        await container.close()
        await bot.session.close()


def run() -> None:
    """Run the bot (entry point for script)."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
