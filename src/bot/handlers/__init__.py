"""
Bot handlers - message routing and processing.
"""

from aiogram import Router

from src.bot.handlers.callbacks import router as callbacks_router
from src.bot.handlers.commands import router as commands_router
from src.bot.handlers.message import router as message_router

# Main router that includes all sub-routers.
# Command and callback routers must come before message router
# so /help, /summary, etc. are matched before the generic text handler.
router = Router(name="main")
router.include_router(commands_router)
router.include_router(callbacks_router)
router.include_router(message_router)

__all__ = ["router"]
