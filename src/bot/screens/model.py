"""What a redesigned admin screen IS, independent of how it is sent.

A builder returns a `Screen`; `render.show()` turns it into a Telegram message.
Nothing in this module imports aiogram: a screen is plain data, so the rules
test (`lint.check_screen`) and `scripts/dump_screens.py` can read every screen
without a bot, a database or an event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["action", "back", "cancel", "exit", "pager", "noop"]
"""What a button does to the user's position, which is what the lint rules read:
``back`` goes to the screen's tree parent, ``cancel`` leaves an input prompt,
``exit`` closes the panel, ``pager``/``noop`` are the page arrows and counter."""

Style = Literal["primary", "success", "danger"]
"""Bot API 9.x button colour (``InlineKeyboardButton.style``)."""


@dataclass(frozen=True, slots=True)
class Button:
    """One inline button. Exactly one of ``callback``, ``url``, ``copy_text`` is set."""

    label: str
    callback: str | None = None
    url: str | None = None
    copy_text: str | None = None
    kind: Kind = "action"
    style: Style | None = None
    goes_to: str | None = None
    """Screen id this button opens. ``None`` means "ask `routes.target_of()`",
    which reads it off the callback prefix — set it only where the prefix alone
    cannot tell (a back button whose target is chosen by the builder)."""

    def __post_init__(self) -> None:
        targets = sum(value is not None for value in (self.callback, self.url, self.copy_text))
        if targets != 1:
            raise ValueError(
                f"button {self.label!r} must have exactly one of callback/url/copy_text, "
                f"has {targets}"
            )


@dataclass(frozen=True, slots=True)
class RequestUsers:
    """``KeyboardButtonRequestUsers`` as data. ``request_id`` comes from `routes.REQUEST_IDS`."""

    request_id: int
    max_quantity: int = 1
    user_is_bot: bool = False
    request_name: bool = True
    request_username: bool = True


@dataclass(frozen=True, slots=True)
class ReplyButton:
    """One button of a reply keyboard (the keyboard under the input field)."""

    label: str
    request_users: RequestUsers | None = None


@dataclass(frozen=True, slots=True)
class Screen:
    """A whole message: text plus at most one keyboard.

    ``rows`` (inline) and ``reply`` are mutually exclusive — Telegram attaches
    one markup per message. ``remove_reply`` sends ``ReplyKeyboardRemove`` and
    therefore also excludes ``rows``. A screen with ``reply`` or
    ``remove_reply`` is always delivered as a NEW message: a reply keyboard
    cannot be edited into an existing one.
    """

    id: str
    """Design screen id (``hub``, ``chat``, ``rule.type``…)."""

    text: str
    """HTML. The first line is the title line: emoji, space, ``<b>title</b>``."""

    rows: tuple[tuple[Button, ...], ...] = ()
    reply: tuple[tuple[ReplyButton, ...], ...] | None = None
    remove_reply: bool = False

    notice: str | None = None
    """What just happened, shown above the text as ``<i>✓ {notice}</i>``.
    Plain text, escaped by the renderer; pass it WITHOUT the ``✓``."""

    notice_raw: str | None = None
    """A complete, already-formatted HTML notice line, shown verbatim. Only for
    the stale-button fallback, whose line is not a "✓ done" confirmation."""

    link_preview: bool = False

    placeholder: str | None = None
    """Placeholder in the input field while a reply keyboard is shown."""
