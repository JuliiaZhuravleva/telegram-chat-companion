"""Who is speaking, when it is not a person (migration 035).

A message sent "on behalf of a chat" reaches the bot with a placeholder in
`from` and the real speaker in `sender_chat`. Every surface that renders a
speaker used to read `from` alone, so:

- an anonymous admin (`from` = GroupAnonymousBot, `is_bot=true`) and a member
  posting as a channel (`from` = Channel_Bot, `is_bot=true`) rendered as the
  bot ITSELF -- `Bot: ...` in the prompt history, i.e. the model read a
  channel author's comments as its own past replies;
- a channel post auto-forwarded into its discussion group rendered as a
  person called "Telegram" (`from` = 777000, `is_bot=false`);
- any other bot in the chat also rendered as `Bot`.

This module is the one place that decides. `classify_sender` reads a live
`Message`; `sender_label` renders a stored row (or a live message through
`live_sender_label`). Both return "not special" for an ordinary person, and
the caller then renders the name exactly as it did before -- the fallback
order differs between surfaces on purpose (prompt history `username` first,
summary `first_name` first, CLAUDE.md) and is not this module's to unify.

`is_bot_message` keeps its old meaning ("the account is flagged as a bot",
placeholders included), so `is_bot_message = false` filters elsewhere are
unchanged; a person's line is `is_bot_message = false AND sender_kind IS NULL`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from aiogram.types import Message
from aiogram.utils.token import TokenValidationError, extract_bot_id

from src.services.text.prompt_sanitizer import sanitize_history_field

if TYPE_CHECKING:
    from src.models.chat_config import ChatConfig

SENDER_CHANNEL_POST = "channel_post"
SENDER_ANON_ADMIN = "anon_admin"
SENDER_CHANNEL = "channel"

# The bot renders as `Bot` everywhere (prompt history, summary, chunker), so
# the label for the bot itself stays that token.
OWN_BOT_LABEL = "Bot"

# A title or a signature is chat-controlled text that lands in a
# `Name (HH:MM): ...` or `Name: ...` line. Same cap as a Telegram first name.
_MAX_FIELD_CHARS = 64

_TEXT = {
    "ru": {
        "post": "пост канала",
        "post_titled": "пост канала «{title}»",
        "channel": "канал",
        "channel_titled": "{title} (канал)",
        "linked_channel": "{title} (автор канала)",
        "admin": "админ",
        "admin_signed": "{signature} (админ)",
        "bot": "{name} (бот)",
        "bot_unnamed": "бот",
    },
    "en": {
        "post": "channel post",
        "post_titled": "channel post «{title}»",
        "channel": "channel",
        "channel_titled": "{title} (channel)",
        "linked_channel": "{title} (channel author)",
        "admin": "admin",
        "admin_signed": "{signature} (admin)",
        "bot": "{name} (bot)",
        "bot_unnamed": "bot",
    },
}


@dataclass(frozen=True)
class SenderInfo:
    """What `chat_messages` stores about a non-person speaker."""

    kind: str | None = None
    chat_id: int | None = None
    chat_title: str | None = None
    signature: str | None = None


def classify_sender(message: Message) -> SenderInfo:
    """Read the real speaker off a live message.

    Order matters: an auto-forwarded channel post also carries `sender_chat`
    (the channel), and must classify as the post, not as a channel comment.
    """
    sender_chat = message.sender_chat
    if message.is_automatic_forward:
        return SenderInfo(
            kind=SENDER_CHANNEL_POST,
            chat_id=sender_chat.id if sender_chat else None,
            chat_title=sender_chat.title if sender_chat else None,
            signature=message.author_signature,
        )
    if sender_chat is None:
        return SenderInfo()
    if sender_chat.id == message.chat.id:
        return SenderInfo(
            kind=SENDER_ANON_ADMIN,
            chat_id=sender_chat.id,
            signature=message.author_signature,
        )
    return SenderInfo(
        kind=SENDER_CHANNEL,
        chat_id=sender_chat.id,
        chat_title=sender_chat.title,
        signature=message.author_signature,
    )


def is_channel_post(message: Message) -> bool:
    """A channel post copied into its discussion group by Telegram.

    The bot must not speak first on one: the post is the author's broadcast,
    not a turn in the conversation. Its comments are.
    """
    return bool(message.is_automatic_forward)


def is_membership_notice(message: Message) -> bool:
    """A "joined" / "left" line Telegram posts by itself -- nobody said it.

    No handler matched these before the community mode, so the inner
    `dp.message` middlewares never saw them. Now one does, and every
    middleware that treats a message as something a person said must step
    aside: saving (a row with no content), activity (a join counted as
    speech) and moderation rules.
    """
    return bool(message.new_chat_members) or message.left_chat_member is not None


@lru_cache(maxsize=1)
def own_bot_id() -> int | None:
    """The bot's own user id, read off the token the way aiogram's `Bot.id` is.

    Pure, no API call: the id is the token's numeric prefix. Needed because a
    stored bot row is "ours" when `user_id` is NULL (every row the bot writes)
    or 0, OR equals this id (anything imported from elsewhere), and the rendering
    surfaces have no `Bot` instance to ask.
    """
    from src.config import settings

    try:
        return extract_bot_id(settings.telegram_bot_token)
    except (TokenValidationError, ValueError):
        return None


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return sanitize_history_field(value).strip()[:_MAX_FIELD_CHARS].strip()


def sender_label(
    *,
    sender_kind: str | None,
    sender_chat_id: int | None = None,
    sender_chat_title: str | None = None,
    author_signature: str | None = None,
    is_bot_message: bool = False,
    user_id: int | None = None,
    first_name: str | None = None,
    linked_channel_id: int | None = None,
    bot_id: int | None = None,
    language: str = "ru",
) -> str | None:
    """The label for a speaker who is not an ordinary person, else None.

    `linked_channel_id` is compared at render time, not stored with the row:
    a group can be linked to its channel after the message arrived.
    `bot_id` defaults to `own_bot_id()`; tests pass it explicitly.
    """
    text = _TEXT.get(language, _TEXT["en"])
    if sender_kind == SENDER_CHANNEL_POST:
        title = _clean(sender_chat_title)
        return text["post_titled"].format(title=title) if title else text["post"]
    if sender_kind == SENDER_CHANNEL:
        title = _clean(sender_chat_title)
        if not title:
            return text["channel"]
        if linked_channel_id is not None and sender_chat_id == linked_channel_id:
            return text["linked_channel"].format(title=title)
        return text["channel_titled"].format(title=title)
    if sender_kind == SENDER_ANON_ADMIN:
        signature = _clean(author_signature)
        return text["admin_signed"].format(signature=signature) if signature else text["admin"]
    if is_bot_message:
        own = bot_id if bot_id is not None else own_bot_id()
        # 0 is no Telegram id; a writer that had no id to give stored it.
        if not user_id or user_id == own:
            return OWN_BOT_LABEL
        name = _clean(first_name)
        return text["bot"].format(name=name) if name else text["bot_unnamed"]
    return None


def row_sender_label(
    row: Mapping[str, Any],
    *,
    linked_channel_id: int | None = None,
    bot_id: int | None = None,
    language: str = "ru",
) -> str | None:
    """`sender_label` over a `chat_messages` row (a dict or an asyncpg Record).

    Missing keys read as NULL, so a query that does not select the 035
    columns degrades to the pre-035 behaviour rather than raising.
    """
    return sender_label(
        sender_kind=row.get("sender_kind"),
        sender_chat_id=row.get("sender_chat_id"),
        sender_chat_title=row.get("sender_chat_title"),
        author_signature=row.get("author_signature"),
        is_bot_message=bool(row.get("is_bot_message")),
        user_id=row.get("user_id"),
        first_name=row.get("first_name"),
        linked_channel_id=linked_channel_id,
        bot_id=bot_id,
        language=language,
    )


def live_sender_label(
    message: Message,
    *,
    linked_channel_id: int | None = None,
    bot_id: int | None = None,
    language: str = "ru",
) -> str | None:
    """`sender_label` for a live message, before (or without) it being stored."""
    info = classify_sender(message)
    user = message.from_user
    return sender_label(
        sender_kind=info.kind,
        sender_chat_id=info.chat_id,
        sender_chat_title=info.chat_title,
        author_signature=info.signature,
        is_bot_message=bool(user and user.is_bot),
        user_id=user.id if user else None,
        first_name=user.first_name if user else None,
        linked_channel_id=linked_channel_id,
        bot_id=bot_id,
        language=language,
    )


def chat_speaker_label(message: Message, chat_config: ChatConfig) -> str | None:
    """`live_sender_label` with the chat's linked channel and language.

    What a handler passes as the speaker of the message it is answering or
    transcribing: None for an ordinary person, who keeps their own name.
    """
    return live_sender_label(
        message,
        linked_channel_id=chat_config.linked_channel_id,
        language=chat_config.language,
    )
