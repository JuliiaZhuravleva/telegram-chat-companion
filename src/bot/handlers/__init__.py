"""
Bot handlers - message routing and processing.
"""

from aiogram import Router

from src.bot.handlers.message import router as message_router

# Main router that includes all sub-routers
router = Router(name="main")
router.include_router(message_router)

__all__ = ["router"]
