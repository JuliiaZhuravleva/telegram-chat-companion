"""
Bot handlers - message routing and processing.
"""

from aiogram import Router

from src.bot.handlers.admin import router as admin_router
from src.bot.handlers.admin_chat_panel import router as admin_chat_panel_router
from src.bot.handlers.admin_kb import router as admin_kb_router
from src.bot.handlers.admin_reactions import router as admin_reactions_router
from src.bot.handlers.admin_sticker import router as admin_sticker_router
from src.bot.handlers.callbacks import router as callbacks_router
from src.bot.handlers.chat_events import router as chat_events_router
from src.bot.handlers.commands import router as commands_router
from src.bot.handlers.media import router as media_router
from src.bot.handlers.message import router as message_router
from src.bot.handlers.reactions import router as reactions_router
from src.bot.handlers.rules import router as rules_router

# Main router that includes all sub-routers.
# Order matters: admin first (own commands + adm_ callbacks), then admin_sticker
# (adm_stk_* callbacks + reply handler), then admin_kb (adm_kb_* callbacks +
# organizer-add reply handler), then admin_reactions (adm_react_* callbacks,
# R-D1), then admin_chat_panel (adm_pnl_* callbacks, B-1 -- own prefix, no
# overlap with the others), then rules (ar_ callbacks + FSM state), then
# commands, then callbacks, media (voice/photo/sticker), then the generic
# text handler last.
#
# chat_events observes edited_message / my_chat_member, and reactions observes
# message_reaction -- update types no other router handles, so their position
# among the message routers is irrelevant.
router = Router(name="main")
router.include_router(admin_sticker_router)
router.include_router(admin_kb_router)
router.include_router(admin_reactions_router)
router.include_router(admin_chat_panel_router)
router.include_router(admin_router)
router.include_router(rules_router)
router.include_router(commands_router)
router.include_router(callbacks_router)
router.include_router(chat_events_router)
router.include_router(reactions_router)
router.include_router(media_router)
router.include_router(message_router)

__all__ = ["router"]
