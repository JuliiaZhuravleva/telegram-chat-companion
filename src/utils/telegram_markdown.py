"""Turn a received message back into the Markdown its author typed.

Telegram clients convert ``**bold**``, ``__italic__``, ``~~strike~~`` and backticks into
entities when a message is sent, and remove the markers. ``message.text`` therefore holds the
words without them. For a chat instruction pasted into the admin panel that silently changes
what gets saved: a prompt written in Markdown loses its emphasis and its code spans.

``entities_to_markdown`` puts the markers back from the entities. A message without entities
comes back unchanged. Entities that have no Markdown form here (underline, spoiler, links,
quotes, custom emoji) keep only their text, which is what ``message.text`` already gives.
"""

from __future__ import annotations

from datetime import datetime

from aiogram.types import MessageEntity
from aiogram.utils.text_decorations import TextDecoration


class _PlainMarkdown(TextDecoration):
    """Markdown markers for emphasis and code, every character of the text left as it is."""

    def bold(self, value: str) -> str:
        return f"**{value}**"

    def italic(self, value: str) -> str:
        return f"_{value}_"

    def strikethrough(self, value: str) -> str:
        return f"~~{value}~~"

    def code(self, value: str) -> str:
        return f"`{value}`"

    def pre(self, value: str) -> str:
        return f"```\n{value}\n```"

    def pre_language(self, value: str, language: str) -> str:
        return f"```{language}\n{value}\n```"

    # The signatures below are TextDecoration's; the unused arguments are its, not ours.
    def link(self, value: str, link: str) -> str:  # noqa: ARG002
        return value

    def underline(self, value: str) -> str:
        return value

    def spoiler(self, value: str) -> str:
        return value

    def quote(self, value: str) -> str:
        return value

    def custom_emoji(self, value: str, custom_emoji_id: str) -> str:  # noqa: ARG002
        return value

    def blockquote(self, value: str) -> str:
        return value

    def expandable_blockquote(self, value: str) -> str:
        return value

    def date_time(
        self,
        value: str,
        unix_time: int | datetime,  # noqa: ARG002
        date_time_format: str | None = None,  # noqa: ARG002
    ) -> str:
        return value


_DECORATION = _PlainMarkdown()


def entities_to_markdown(text: str, entities: list[MessageEntity] | None) -> str:
    """``text`` with its bold, italic, strikethrough and code entities written back as Markdown."""
    if not entities:
        return text
    return _DECORATION.unparse(text, entities)
