"""Community mode: greetings, welcome-backs and farewells (migration 036).

The state machine lives in `ChatMemberStateRepository`; this module decides what
the bot says about a transition and to whom. Every line it posts is built the
same way, whatever wrote it -- a chat's own template, the cheap model, or the
neutral fallback below:

1. the text is HTML-escaped as a whole, `{name}` included;
2. only then is `{name}` replaced with the escaped name (a `tg://user` mention
   when the line greets a join, so the newcomer is notified).

So nothing a template author, a model or a display name contains can become
markup, and a name is never re-scanned for `{name}` (`str.replace` does not
look at what it inserted).

Russian has no neutral past tense ("ушёл"/"ушла"), and the bot does not know
anyone's gender: every fallback is worded around it, and the model is told to.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any

import structlog
from aiogram.types import User

from src.database.repositories.aliases import AliasRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.member_state import ChatMemberStateRepository, LeaveOutcome
from src.models.chat_config import ChatConfig
from src.services.ai.router import AIRouter
from src.utils import parse_admin_ids
from src.utils.aliases import build_alias_view, primary_alias

logger = structlog.get_logger(__name__)

KINDS = ("new", "back", "farewell", "joke")

# Neutral in gender and free of links: what the chat sees when it has no
# templates of its own and the model is unavailable or said something unusable.
FALLBACK_TEXTS: dict[str, tuple[str, ...]] = {
    "new": ("Добро пожаловать, {name}!", "Привет, {name}! Рады видеть в чате."),
    "back": ("С возвращением, {name}!", "{name}, рады снова видеть!"),
    "farewell": ("{name} покидает чат. Удачи!", "Удачи, {name}! Заглядывай."),
    "joke": ("Не прощаемся, {name}: отсюда всегда возвращаются.",),
}

_INSTRUCTIONS = {
    "new": "A new person has just joined the chat. Greet them.",
    "back": "Someone who used to be in the chat has come back. Welcome them back.",
    "farewell": "Someone has just left the chat on their own. Say goodbye.",
    "joke": (
        "Someone has just left the chat, and it is not the first time they leave. "
        "Say goodbye with a light, kind joke that they will surely be back."
    ),
}

_RULES = (
    "Write ONE short line (at most 200 characters) in the chat's language, in your usual "
    "voice. Refer to the person exactly once, as the literal placeholder {name}. "
    "Never guess their gender: in Russian avoid past-tense verbs and adjectives that "
    "agree with the person. No links, no @mentions, no hashtags, no quotes around the "
    "line. Output only the line."
)

# Anything the model writes that could reach outside the chat, or that is too
# long to be a greeting, sends the line to the fallback instead.
_UNSAFE_OUTPUT = re.compile(r"https?://|www\.|t\.me|tg://|@\w", re.IGNORECASE)
MAX_LINE_CHARS = 300

# Raid guard: at most this many public lines per chat per window. A burst of
# joins (a raid, an invite link posted somewhere) must not turn the bot into a
# second flood. Admin summaries get a wider allowance of their own.
LIMITS = {"public": 3, "dm": 10}
LIMIT_WINDOW_SECONDS = 300.0


class CommunityLimiter:
    """Per-chat sliding-window counter, shared process-wide (`Scope.APP`).

    In memory on purpose, like `NotifyCooldown`: a restart forgets the window,
    which can at worst let one more burst through -- cheaper than a table.
    `allowed()` does not spend; `spend()` does. Checking first is what lets the
    first-message path leave a greeting pending when the window is full.
    """

    def __init__(self) -> None:
        self._events: dict[tuple[int, str], list[float]] = {}

    def _recent(self, chat_id: int, bucket: str, now: float) -> list[float]:
        key = (chat_id, bucket)
        recent = [t for t in self._events.get(key, ()) if now - t < LIMIT_WINDOW_SECONDS]
        if recent:
            self._events[key] = recent
        else:
            self._events.pop(key, None)
        return recent

    def allowed(self, chat_id: int, bucket: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return len(self._recent(chat_id, bucket, now)) < LIMITS[bucket]

    def spend(self, chat_id: int, bucket: str, *, now: float | None = None) -> bool:
        """Take one slot; False (and nothing taken) when the window is full."""
        now = time.monotonic() if now is None else now
        recent = self._recent(chat_id, bucket, now)
        if len(recent) >= LIMITS[bucket]:
            return False
        self._events[(chat_id, bucket)] = [*recent, now]
        return True


def parse_templates(raw: Any) -> dict[str, list[str]]:
    """`chat_settings.community_templates` -> {kind: [text, ...]}, defensively.

    asyncpg returns a JSONB column decoded or as a string depending on how it
    was written; anything malformed degrades to "no templates", never raises.
    A template without `{name}` is dropped: it would greet nobody in particular.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for kind in KINDS:
        texts = raw.get(kind)
        if isinstance(texts, list):
            usable = [t for t in texts if isinstance(t, str) and "{name}" in t]
            if usable:
                out[kind] = usable
    return out


def render_line(template: str, name_html: str) -> str:
    """Escape the whole line, then put the (already escaped) name in."""
    return escape(template, quote=False).replace("{name}", name_html)


def usable_model_line(text: str) -> str | None:
    """The model's line if it is safe to post as a template, else None."""
    line = text.strip().strip('"«»').strip()
    if not line or "\n" in line or len(line) > MAX_LINE_CHARS:
        return None
    if line.count("{name}") != 1 or _UNSAFE_OUTPUT.search(line):
        return None
    return line


def display_name(user: User, alias: str | None) -> str:
    """What the bot calls someone in a greeting: alias, else the account name.

    The order is this surface's own (CLAUDE.md, aliases): a greeting addresses
    a person, so `first_name` comes before `username`.
    """
    return alias or user.first_name or (user.username and f"@{user.username}") or "друг"


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name, quote=False)}</a>'


@dataclass(frozen=True)
class Departure:
    """How someone left, as far as the event says."""

    how: str  # "self" | "removed" | "banned"

    @property
    def status(self) -> str:
        return "kicked" if self.how == "banned" else "left"

    @property
    def voluntary(self) -> bool:
        return self.how == "self"


_HOW_RU = {"self": "по своей воле", "removed": "удаление админом", "banned": "бан"}


class CommunityService:
    """Turns membership transitions into what the bot says."""

    def __init__(
        self,
        *,
        member_repo: ChatMemberStateRepository,
        chat_settings_repo: ChatSettingsRepository,
        bot_config_repo: BotConfigRepository,
        alias_repo: AliasRepository,
        ai_router: AIRouter,
        limiter: CommunityLimiter,
    ) -> None:
        self._members = member_repo
        self._chat_settings = chat_settings_repo
        self._bot_config = bot_config_repo
        self._aliases = alias_repo
        self._router = ai_router
        self._limiter = limiter

    # ── Joins ─────────────────────────────────────────────────────────────

    async def on_join(
        self, bot: Any, chat_config: ChatConfig, chat_id: int, user: User, at: datetime
    ) -> None:
        """Record a join and, in the greet-on-join mode, greet it.

        Recorded whatever the mode: switching community mode on later must
        still recognise a return. Only one of the two paths a join arrives by
        gets an outcome back; the other sees a duplicate and stops here.
        """
        outcome = await self._members.record_join(
            chat_id, user.id, at, greet=chat_config.community_enabled
        )
        if outcome is None or not chat_config.community_enabled:
            return
        logger.info("community_join", chat_id=chat_id, kind=outcome.kind)
        if chat_config.greet_on_first_message:
            return  # pending; the first message claims it

        # Claimed before the limiter: a greeting the raid guard swallows at
        # join time is dropped, not left to surface out of context later.
        kind = await self._members.claim_greeting(chat_id, user.id)
        if kind is None:
            return
        if kind == "back" and not chat_config.welcome_back_enabled:
            return
        if not self._limiter.spend(chat_id, "public"):
            logger.info("community_greeting_rate_limited", chat_id=chat_id)
            return
        name = await self._name_for(chat_id, user)
        text = await self._line(chat_config, chat_id, kind, mention(user.id, name))
        await bot.send_message(chat_id, text, parse_mode="HTML")

    async def greet_first_message(self, chat_config: ChatConfig, message: Any) -> None:
        """Reply to a newcomer's first message with the greeting their join left.

        The limiter is checked BEFORE the claim, so a newcomer the raid guard
        holds back stays pending and is greeted on a later message.
        """
        user = message.from_user
        chat_id = message.chat.id
        if not self._limiter.allowed(chat_id, "public"):
            return
        kind = await self._members.claim_greeting(chat_id, user.id)
        if kind is None:
            return
        if kind == "back" and not chat_config.welcome_back_enabled:
            return
        self._limiter.spend(chat_id, "public")
        name = await self._name_for(chat_id, user)
        # A reply already notifies them: no mention needed.
        text = await self._line(chat_config, chat_id, kind, escape(name, quote=False))
        await message.reply(text, parse_mode="HTML")

    # ── Leaves ────────────────────────────────────────────────────────────

    async def on_leave(
        self,
        bot: Any,
        chat_config: ChatConfig,
        chat_id: int,
        chat_title: str | None,
        user: User,
        at: datetime,
        departure: Departure,
    ) -> None:
        outcome = await self._members.record_leave(chat_id, user.id, at, status=departure.status)
        if outcome is None or not chat_config.community_enabled:
            return
        logger.info(
            "community_leave", chat_id=chat_id, how=departure.how, count=outcome.leave_count
        )
        if chat_config.farewell_dm:
            await self._notify_admins(bot, chat_id, chat_title, user, departure, outcome)
        if not departure.voluntary:
            return  # a removal is not a goodbye the chat should hear
        kind = (
            "joke"
            if chat_config.returner_joke_enabled and outcome.leave_count >= 2
            else "farewell"
            if chat_config.farewell_public
            else None
        )
        if kind is None:
            return
        if not self._limiter.spend(chat_id, "public"):
            logger.info("community_farewell_rate_limited", chat_id=chat_id)
            return
        name = await self._name_for(chat_id, user)
        text = await self._line(chat_config, chat_id, kind, escape(name, quote=False))
        await bot.send_message(chat_id, text, parse_mode="HTML")

    async def _notify_admins(
        self,
        bot: Any,
        chat_id: int,
        chat_title: str | None,
        user: User,
        departure: Departure,
        outcome: LeaveOutcome,
    ) -> None:
        if not self._limiter.spend(chat_id, "dm"):
            logger.info("community_dm_rate_limited", chat_id=chat_id)
            return
        admin_ids = parse_admin_ids(await self._bot_config.get("admin_ids"))
        if not admin_ids:
            return
        first, last = await self._members.message_span(chat_id, user.id)
        text = format_leave_summary(
            chat_title=chat_title,
            user=user,
            departure=departure,
            outcome=outcome,
            first_message_at=first,
            last_message_at=last,
            now=datetime.now(UTC),
        )
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception as exc:
                logger.warning(
                    "community_dm_failed", admin_id=admin_id, error_type=type(exc).__name__
                )

    # ── Text ──────────────────────────────────────────────────────────────

    async def _name_for(self, chat_id: int, user: User) -> str:
        try:
            view = build_alias_view(await self._aliases.load_active(chat_id))
            alias = primary_alias(view, user.id)
        except Exception:
            logger.warning("community_alias_lookup_failed", chat_id=chat_id, exc_info=True)
            alias = None
        return display_name(user, alias)

    async def _line(self, chat_config: ChatConfig, chat_id: int, kind: str, name_html: str) -> str:
        template = await self._template(chat_id, kind)
        if template is None:
            template = await self._generate(chat_config, chat_id, kind)
        if template is None:
            template = random.choice(FALLBACK_TEXTS[kind])  # noqa: S311 -- not crypto
        return render_line(template, name_html)

    async def _template(self, chat_id: int, kind: str) -> str | None:
        try:
            row = await self._chat_settings.get(chat_id)
        except Exception:
            logger.warning("community_templates_read_failed", chat_id=chat_id, exc_info=True)
            return None
        texts = parse_templates((row or {}).get("community_templates")).get(kind)
        return random.choice(texts) if texts else None  # noqa: S311

    async def _generate(self, chat_config: ChatConfig, chat_id: int, kind: str) -> str | None:
        persona = chat_config.system_prompt or "Friendly chat participant."
        try:
            result = await self._router.generate_text(
                _INSTRUCTIONS[kind],
                system_prompt=f"{persona}\n\n{_RULES}",
                max_tokens=1024,
            )
        except Exception as exc:
            logger.warning("community_generation_failed", chat_id=chat_id, error=str(exc))
            return None
        await self._router.log_usage(result, chat_id=chat_id, task_type="community")
        line = usable_model_line(result.text or "")
        if line is None:
            logger.info("community_generation_rejected", chat_id=chat_id, kind=kind)
        return line


def format_leave_summary(
    *,
    chat_title: str | None,
    user: User,
    departure: Departure,
    outcome: LeaveOutcome,
    first_message_at: datetime | None,
    last_message_at: datetime | None,
    now: datetime,
) -> str:
    """The admin's note about a departure. Every value is escaped."""
    who = escape(user.full_name or "без имени", quote=False)
    if user.username:
        who += f" (@{escape(user.username, quote=False)})"
    lines = [
        f"👋 Уход из чата «{escape(chat_title or 'без названия', quote=False)}»",
        f"Кто: {who}, <code>{user.id}</code>",
        f"Как: {_HOW_RU[departure.how]}",
    ]
    since = outcome.joined_at or first_message_at
    if since is not None:
        days = max((now - since).days, 0)
        lines.append(f"В чате: {days} дн. (с {since:%d.%m.%Y})")
    if last_message_at is not None:
        lines.append(f"Последнее сообщение: {last_message_at:%d.%m.%Y}")
    else:
        lines.append("Сообщений бот не видел")
    if outcome.leave_count >= 2:
        lines.append(f"Уходит уже {outcome.leave_count}-й раз")
    return "\n".join(lines)
