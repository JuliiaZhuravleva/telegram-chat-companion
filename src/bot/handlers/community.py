"""Joins and leaves for the community mode (migration 036).

A join or a leave reaches the bot twice, in either order: as a `chat_member`
update (delivered only where the bot is an administrator) and as the "joined" /
"left" service message (delivered only where the group does not hide them).
Either may be the only one. Both feed the same repository transitions, which
let exactly one of them through -- the handlers never need to know which.

The whitelist: the service messages are `Message`s and go through
`AccessControlMiddleware` like any other. `ChatMemberUpdated` does NOT -- that
middleware does not understand the type and would pass it through without
gating anything (CLAUDE.md) -- so `chat_config.enabled` is checked in the
handler.

Membership is recorded whenever the chat is whitelisted, community mode or
not: switching the mode on later must still recognise a return. What the bot
says is the service's decision.
"""

from __future__ import annotations

import structlog
from aiogram import Bot, F, Router
from aiogram.types import ChatMemberUnion, ChatMemberUpdated, Message
from dishka.integrations.aiogram import FromDishka

from src.models.chat_config import ChatConfig
from src.services.community import CommunityService, Departure

router = Router(name="community")
logger = structlog.get_logger(__name__)

_GROUP_TYPES = frozenset({"group", "supergroup"})
_PRESENT = frozenset({"member", "administrator", "creator"})


def is_present(member: ChatMemberUnion) -> bool:
    """In the chat right now. `restricted` is both: `is_member` decides.

    A promotion (member -> administrator) or a restriction of someone who stays
    is therefore no transition at all.
    """
    if member.status == "restricted":
        return bool(getattr(member, "is_member", False))
    return member.status in _PRESENT


def departure_from_update(event: ChatMemberUpdated) -> Departure:
    if event.new_chat_member.status == "kicked":
        return Departure("banned")
    if event.from_user.id == event.new_chat_member.user.id:
        return Departure("self")
    return Departure("removed")


@router.chat_member(F.chat.type.in_(_GROUP_TYPES))
async def handle_chat_member(
    event: ChatMemberUpdated,
    bot: Bot,
    chat_config: ChatConfig,
    community: FromDishka[CommunityService],
) -> None:
    if not chat_config.enabled:
        return
    user = event.new_chat_member.user
    if user.is_bot:
        return
    was, now = is_present(event.old_chat_member), is_present(event.new_chat_member)
    if was == now:
        return
    try:
        if now:
            await community.on_join(bot, chat_config, event.chat.id, user, event.date)
        else:
            await community.on_leave(
                bot,
                chat_config,
                event.chat.id,
                event.chat.title,
                user,
                event.date,
                departure_from_update(event),
            )
    except Exception:
        logger.warning("community_member_update_failed", chat_id=event.chat.id, exc_info=True)


# Filtered to humans here, not in the body: a matched handler consumes the
# update (CLAUDE.md), and the bot's OWN arrival must stay where it is handled --
# `my_chat_member` in chat_events.py.
@router.message(
    F.chat.type.in_(_GROUP_TYPES),
    F.new_chat_members.func(lambda users: any(not u.is_bot for u in users)),
)
async def handle_new_chat_members(
    message: Message,
    bot: Bot,
    chat_config: ChatConfig,
    community: FromDishka[CommunityService],
) -> None:
    for user in message.new_chat_members or ():
        if user.is_bot:
            continue
        try:
            await community.on_join(bot, chat_config, message.chat.id, user, message.date)
        except Exception:
            logger.warning("community_join_failed", chat_id=message.chat.id, exc_info=True)


@router.message(
    F.chat.type.in_(_GROUP_TYPES),
    F.left_chat_member,
    ~F.left_chat_member.is_bot,
)
async def handle_left_chat_member(
    message: Message,
    bot: Bot,
    chat_config: ChatConfig,
    community: FromDishka[CommunityService],
) -> None:
    user = message.left_chat_member
    if user is None:
        return
    # The service line cannot tell a removal from a ban; the `chat_member`
    # update can, and whichever arrives first is the one recorded.
    voluntary = message.from_user is not None and message.from_user.id == user.id
    try:
        await community.on_leave(
            bot,
            chat_config,
            message.chat.id,
            message.chat.title,
            user,
            message.date,
            Departure("self" if voluntary else "removed"),
        )
    except Exception:
        logger.warning("community_leave_failed", chat_id=message.chat.id, exc_info=True)
