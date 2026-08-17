"""
Bot handlers - message routing and processing.
"""

from aiogram import Router

from src.bot.handlers.admin import router as admin_router
from src.bot.handlers.admin_chat_panel import router as admin_chat_panel_router
from src.bot.handlers.admin_defaults import router as admin_defaults_router
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
#
# Order matters, and the list below is the ACTUAL include order — it drifted from
# this comment, which used to open "admin first" while `admin_router` was in fact
# included sixth. That is not cosmetic: this list is the only thing that decides
# which handler consumes an update, and the ordering hazard it documents is
# precisely the one that bit `/remember` (see `admin_sticker`'s note below).
#
#  1. admin_sticker    — adm_stk_* callbacks + the admin's DM reply handler.
#                        Must precede media_router so an admin's own DM sticker is
#                        never silently auto-learned by handle_sticker_message
#                        (B-1). Its reply handler matches ANY text reply in an
#                        admin's DM, so it carries `~F.text.startswith("/")`:
#                        without that it swallowed `/remember` and `/kb` before
#                        the command handlers ran, and a consumed update whose
#                        body decides to do nothing is indistinguishable from a
#                        broken bot.
#  2. admin_kb         — adm_kb_* callbacks + organizer-add reply handler.
#  3. admin_reactions  — adm_react_* callbacks (R-D1).
#  4. admin_chat_panel — adm_pnl_* callbacks (B-1; own prefix, no overlap).
#  5. admin_defaults   — adm_defs_* / adm_defs_tgl_* callbacks (C-1; own prefix,
#                        replaces the placeholder that used to live in admin).
#  6. admin           — its own commands + adm_ callbacks.
#  7. rules           — ar_ callbacks + FSM state.
#  8. commands        — /start /help /summary /remember /kb + kb_view:/kb_undo:.
#  9. callbacks
# 10. media           — voice / photo / sticker.
# 11. message         — the generic `F.text` catch-all, and therefore LAST: it
#                       matches every group text message, so anything registered
#                       after it is unreachable.
#
# chat_events observes edited_message / my_chat_member, and reactions observes
# message_reaction -- update types no other router handles, so their position
# among the message routers is irrelevant.
router = Router(name="main")
router.include_router(admin_sticker_router)
router.include_router(admin_kb_router)
router.include_router(admin_reactions_router)
router.include_router(admin_chat_panel_router)
router.include_router(admin_defaults_router)
router.include_router(admin_router)
router.include_router(rules_router)
router.include_router(commands_router)
router.include_router(callbacks_router)
router.include_router(chat_events_router)
router.include_router(reactions_router)
router.include_router(media_router)
router.include_router(message_router)

__all__ = ["router"]
