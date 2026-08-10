"""
Telegram Chat Companion - Entry point

An AI-powered Telegram bot that acts as a chat participant,
not just a command responder.
"""

import asyncio
import contextlib
import json
import logging
from typing import Any

import asyncpg
import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka

from src.bot.commands import sync_and_report
from src.bot.errors import handle_unexpected_error
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
from src.database.repositories.bot_config import BotConfigRepository
from src.di import AppProvider, RepositoryProvider, ServiceProvider
from src.services.ai.router import AIRouter
from src.services.health.checker import HealthChecker
from src.services.maintenance.cleanup import RetentionCleaner
from src.services.modules.sticker.scheduler import StickerSetSyncScheduler
from src.services.rag.backfill import EmbeddingBackfillWorker
from src.utils import parse_admin_ids
from src.utils.background import fire_and_forget

_REQUIRED_TABLES = ("bot_config", "chat_settings", "custom_rules", "health_log")


async def _verify_schema(pool: asyncpg.Pool) -> None:
    """Check that required tables exist. Raises RuntimeError if not."""
    for table in _REQUIRED_TABLES:
        exists = await pool.fetchval(
            "SELECT EXISTS(  SELECT 1 FROM information_schema.tables  WHERE table_name = $1)",
            table,
        )
        if not exists:
            raise RuntimeError(f"Required table '{table}' not found. Run: alembic upgrade head")


def build_log_processors(log_format: str) -> list[Any]:
    """The structlog processor chain, as a value so it can be tested.

    Extracted from ``main()`` because the property that matters here is only
    observable in *rendered* output, and ``structlog.testing.capture_logs()``
    — the obvious way to test logging — replaces the configured processors
    with a bare collector. It therefore asserts on the pre-render event dict
    and is structurally incapable of catching a renderer-level defect, which
    is exactly the defect this chain had.

    ``format_exc_info`` is load-bearing, not decoration. structlog does not
    forward ``exc_info`` to stdlib logging — it is just another key in the
    event dict — so with no processor to turn it into text, ``JSONRenderer``
    meets a non-serializable exception object and falls back to ``repr()``.
    Every ``exc_info=True`` and ``logger.exception(...)`` site in this codebase
    was rendering as a bare ``"ValueError('x')"``: no file, no line, no stack.
    Measured, not assumed. ``ConsoleRenderer`` formats ``exc_info`` itself,
    which is precisely why this stayed invisible in local development while
    production — where ``logging.format`` defaults to ``json`` — lost every
    traceback it wrote.

    Deliberately NOT ``dict_tracebacks``, the more obvious "structured"
    choice: it captures local variables into the log record, and the locals in
    this process include the Telegram token, provider API keys and user
    message content. Verified by rendering a frame holding a token — it comes
    out in the output. Structured frames are not worth writing credentials to
    a log file.
    """
    is_json = log_format == "json"
    return [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        # ConsoleRenderer does its own exc_info handling; adding this ahead of
        # it would turn a readable traceback into an escaped blob.
        *([structlog.processors.format_exc_info] if is_json else []),
        structlog.processors.JSONRenderer() if is_json else structlog.dev.ConsoleRenderer(),
    ]


async def main() -> None:
    """Initialize and start the bot."""
    settings = Settings()

    structlog.configure(
        processors=build_log_processors(settings.logging.format),
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

    for mw in (
        chat_config_mw,
        topic_mw,
        access_control_mw,
        activity_tracker_mw,
        message_saver_mw,
        rules_mw,
    ):
        dp.message.middleware(mw)

    # Callback queries need chat_config, topic, and access control too
    dp.callback_query.middleware(chat_config_mw)
    dp.callback_query.middleware(topic_mw)
    dp.callback_query.middleware(access_control_mw)

    # Edited messages: gate on the whitelist, then let MessageSaverMiddleware
    # re-save the message so save()'s ON CONFLICT branch records the edit.
    # ActivityTracker and Rules are deliberately left out — an edit is not new
    # activity and must not re-fire rule actions on the same message.
    for mw in (chat_config_mw, topic_mw, access_control_mw, message_saver_mw):
        dp.edited_message.middleware(mw)

    # Reactions (ADR-0004, R-1): chat_config only.
    #
    # AccessControlMiddleware is deliberately NOT registered here, and adding it
    # would be a silent no-op rather than a fix: its _extract_event_info() only
    # matches Message/CallbackQuery (middleware/access_control.py:193-204), so a
    # MessageReactionUpdated resolves to (None, None, None) and the middleware
    # early-returns into the handler having gated nothing. The whitelist check
    # therefore lives in handle_message_reaction() itself as an explicit
    # `if not chat_config.enabled: return`.
    #
    # (The module's own `reactions_enabled` toggle is NOT sufficient as the
    # whitelist gate: it resolves through the three-layer merge, so a global
    # bot_config.default_reactions_enabled=true would switch the module on for
    # every chat including never-approved ones, and de-whitelisting a chat never
    # clears the per-chat column.)
    dp.message_reaction.middleware(chat_config_mw)

    dp.include_router(main_router)

    # Global safety net for handler exceptions (TD-062). Registered on the
    # dispatcher rather than on a router because aiogram propagates errors up
    # the router tree — this is the only place that covers every handler,
    # including ones added later. Without it, an exception before a callback
    # handler reaches its `callback.answer()` leaves the button spinning until
    # Telegram times it out, and the traceback goes only to aiogram's logger.
    dp.errors.register(handle_unexpected_error)

    # Register bot commands with Telegram API for autocomplete hints, then check
    # that Telegram really holds what the registry declares. A deploy is the one
    # moment this can be verified for free, and merging to main deploys
    # unattended — see src/bot/command_registry.py.
    admin_ids_raw = await pool.fetchval("SELECT value FROM bot_config WHERE key = 'admin_ids'")
    if admin_ids_raw is not None:
        admin_ids_raw = json.loads(admin_ids_raw)
    admin_ids = parse_admin_ids(admin_ids_raw)
    bot_config_repo = BotConfigRepository(pool)
    # Backgrounded on purpose: the sync makes ~a dozen sequential Bot API calls
    # and nothing downstream waits on their result, so keeping it on the startup
    # path only means a slow or rate-limited Telegram delays the first answered
    # message. `fire_and_forget` holds the strong reference asyncio does not;
    # the task is cancelled in the shutdown block below, before the session it
    # uses is closed.
    command_sync_task = fire_and_forget(
        sync_and_report(
            bot,
            admin_ids,
            bot_config_repo=bot_config_repo,
            router=main_router,
        )
    )

    # Cache bot identity for handlers (avoids per-message getMe calls)
    dp["bot_id"] = (await bot.me()).id

    # Start background tasks
    health_checker = HealthChecker(pool=pool, bot=bot)
    await health_checker.start()
    dp["health_checker"] = health_checker

    sticker_sync = StickerSetSyncScheduler(pool=pool, bot=bot)
    await sticker_sync.start()

    retention_cleaner = RetentionCleaner(pool=pool, config=settings.maintenance)
    await retention_cleaner.start()

    # S2-10: retries embeddings for chat_memory rows that failed to embed at
    # write time (RAGMemoryService.store() persists them with embedding=NULL
    # instead of dropping them). Shares the AI router with request handling.
    ai_router = await container.get(AIRouter)
    embedding_backfill = EmbeddingBackfillWorker(
        pool=pool, ai_router=ai_router, config=settings.embedding_backfill
    )
    await embedding_backfill.start()

    try:
        logger.info("Bot started, listening for messages...")
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down...")
        # First: the command sync may still be mid-flight on this bot session,
        # and cancelling it after the session closes turns a clean shutdown into
        # a stack trace. Cancelling is safe — see sync_and_report's docstring.
        command_sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await command_sync_task
        await embedding_backfill.stop()
        await retention_cleaner.stop()
        await sticker_sync.stop()
        await health_checker.stop()
        # Isolated on purpose. container.close() now has real work to do — the
        # AIRouter teardown became reachable when its provider started yielding
        # — and Dishka aggregates any failing exit into an ExitError it
        # re-raises. Left sequential, that exception would skip the next line
        # and leak the Bot's own HTTP session, i.e. adding a teardown would have
        # introduced a leak elsewhere. Each close reports and continues.
        for label, closer in (("container", container.close), ("bot_session", bot.session.close)):
            try:
                await closer()
            except Exception as exc:
                logger.warning(
                    "shutdown_close_failed",
                    component=label,
                    error_type=type(exc).__name__,
                    exc_info=exc,
                )


def run() -> None:
    """Run the bot (entry point for script)."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
