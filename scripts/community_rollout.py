"""Where can the community mode be switched on -- and switch it on there.

    python -m scripts.community_rollout                     # report only
    python -m scripts.community_rollout --apply ID [ID ...]  # the "ordinary group" preset

On production, via the same route as the other scripts:

    bin/ssh-claw '/usr/local/bin/docker exec companion-bot-1 \
        python -m scripts.community_rollout'

"Enabled in the database" is not "the bot is in that chat": `chat_settings`
keeps the row of a group that became a supergroup, of a test chat the bot was
removed from, of a chat that was never approved. So every row is checked
against Telegram itself (`getChatMember(chat, bot)`), and the report says
whether the bot is an administrator there -- without it the `chat_member`
updates never arrive and only the "joined"/"left" service lines are seen,
which a group can hide.

`--apply` takes chat ids explicitly, from a report somebody has read, and
refuses any of them that the report would not call ready. It never picks the
chats itself.

Exit codes: 0 = done; 1 = `--apply` refused at least one chat (nothing was
written to that one); 2 = the check could not be completed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

import structlog
from aiogram import Bot

from src.config import Settings
from src.database.connection import close_pool, create_pool
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.services.chat_config import ChatConfigService

logger = structlog.get_logger(__name__)

# The preset Julia approved for every group the bot is in ("ordinary group"):
# greet on join, welcome back, say goodbye to whoever leaves on their own.
ORDINARY_GROUP: dict[str, bool] = {
    "community_enabled": True,
    "greet_on_first_message": False,
    "welcome_back_enabled": True,
    "farewell_public": True,
    "farewell_dm": False,
    "returner_joke_enabled": False,
}

_PRESENT = frozenset({"member", "administrator", "creator", "restricted"})
_ADMIN = frozenset({"administrator", "creator"})


@dataclass(frozen=True)
class ChatState:
    chat_id: int
    title: str | None
    whitelisted: bool
    community_enabled: bool
    bot_status: str | None
    """`getChatMember` status of the bot, or None when Telegram would not say."""
    error: str | None = None

    @property
    def verdict(self) -> str:
        if self.bot_status is None:
            return f"unreachable ({self.error})" if self.error else "unreachable"
        if self.bot_status not in _PRESENT:
            return f"bot not in chat ({self.bot_status})"
        if not self.whitelisted:
            return "not approved"
        return "ready"

    @property
    def ready(self) -> bool:
        return self.verdict == "ready"

    @property
    def bot_is_admin(self) -> bool:
        return self.bot_status in _ADMIN


def refused(states: list[ChatState], requested: list[int]) -> dict[int, str]:
    """Requested chats `--apply` must not touch, with the reason."""
    by_id = {state.chat_id: state for state in states}
    reasons: dict[int, str] = {}
    for chat_id in requested:
        state = by_id.get(chat_id)
        if state is None:
            reasons[chat_id] = "no such group in chat_settings"
        elif not state.ready:
            reasons[chat_id] = state.verdict
    return reasons


def format_report(states: list[ChatState]) -> str:
    lines = []
    for state in sorted(states, key=lambda s: (not s.ready, s.chat_id)):
        admin = "admin" if state.bot_is_admin else "-"
        mode = "on" if state.community_enabled else "off"
        title = state.title or "—"
        lines.append(
            f"{state.chat_id:>16}  {state.verdict:<28} {admin:<6} community={mode:<4} {title}"
        )
    ready = [s for s in states if s.ready]
    lines.append("")
    lines.append(f"ready: {len(ready)} of {len(states)} groups")
    without_admin = [s for s in ready if not s.bot_is_admin]
    if without_admin:
        lines.append(
            f"{len(without_admin)} ready group(s) without admin rights: only the "
            "service lines are seen there, and a group can hide them"
        )
    return "\n".join(lines)


async def collect(
    bot: Bot, settings_repo: ChatSettingsRepository, configs: ChatConfigService
) -> list[ChatState]:
    rows = await settings_repo.list_groups()
    states = []
    for row in rows:
        config = await configs.get_config(row["chat_id"])
        status: str | None = None
        error: str | None = None
        try:
            member = await bot.get_chat_member(row["chat_id"], bot.id)
            status = str(getattr(member.status, "value", member.status))
        except Exception as exc:
            error = type(exc).__name__
        states.append(
            ChatState(
                chat_id=row["chat_id"],
                title=row["chat_title"],
                whitelisted=config.enabled,
                community_enabled=config.community_enabled,
                bot_status=status,
                error=error,
            )
        )
    return states


async def apply(
    settings_repo: ChatSettingsRepository, states: list[ChatState], requested: list[int]
) -> dict[int, str]:
    """Write the preset to every requested chat that is ready; return the rest."""
    reasons = refused(states, requested)
    for chat_id, reason in reasons.items():
        print(f"REFUSED {chat_id}: {reason}")
    for chat_id in requested:
        if chat_id in reasons:
            continue
        await settings_repo.upsert(chat_id, **ORDINARY_GROUP)
        print(f"applied {chat_id}")
    return reasons


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        nargs="+",
        type=int,
        metavar="CHAT_ID",
        help="write the 'ordinary group' preset to these chats (each must be ready)",
    )
    args = parser.parse_args()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

    settings = Settings()
    pool = await create_pool(settings.database_url)
    bot = Bot(token=settings.telegram_bot_token)
    try:
        settings_repo = ChatSettingsRepository(pool)
        configs = ChatConfigService(
            yaml_settings=settings.bot,
            bot_config_repo=BotConfigRepository(pool),
            chat_settings_repo=settings_repo,
        )
        try:
            states = await collect(bot, settings_repo, configs)
        except Exception as exc:
            print(f"Could not complete the check: {exc}", file=sys.stderr)
            return 2
        print(format_report(states))

        if not args.apply:
            return 0
        reasons = await apply(settings_repo, states, args.apply)
        return 1 if reasons else 0
    finally:
        await bot.session.close()
        await close_pool(pool)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
