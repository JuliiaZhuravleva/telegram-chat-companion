"""
Bot handlers - message routing and processing.
"""

from aiogram import Router

from src.bot.handlers.admin import router as admin_router
from src.bot.handlers.admin_sticker import router as admin_sticker_router
from src.bot.handlers.callbacks import router as callbacks_router
from src.bot.handlers.commands import router as commands_router
from src.bot.handlers.media import router as media_router
from src.bot.handlers.message import router as message_router
from src.bot.handlers.rules import router as rules_router

# Main router that includes all sub-routers.
# Order matters: admin first (own commands + adm_ callbacks), then admin_sticker
# (adm_stk_* callbacks + reply handler), then rules (ar_ callbacks + FSM state),
# then commands, then callbacks, media (voice/photo/sticker),
# then the generic text handler last.
router = Router(name="main")
router.include_router(admin_sticker_router)
router.include_router(admin_router)
router.include_router(rules_router)
router.include_router(commands_router)
router.include_router(callbacks_router)
router.include_router(media_router)
router.include_router(message_router)

__all__ = ["router"]
