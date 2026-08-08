"""Push the command registry to Telegram, then check that it took.

Rendering half of :mod:`src.bot.command_registry` — that module declares which
commands exist and where they belong, this one talks to the Bot API:

1. **push** every scope × language variant declared in the registry,
2. **clean up** per-admin scopes belonging to people who are no longer admins,
3. **read back** what Telegram actually stored and diff it against what was sent.

Step 3 is the point. ``set_my_commands`` returning ``True`` only means the call
was accepted; it says nothing about a scope some earlier deploy set and this one
no longer knows about, or about a menu edited by hand through BotFather. Since
merging to ``main`` deploys unattended, "we pushed it" is not evidence.

Language handling (this is a real Bot API trap): Telegram resolves a user's
command list per scope by trying the user's ``language_code`` **first and the
language-less variant second** — it does *not* fall back from "es" to "en". The
previous implementation only ever pushed ``language_code="ru"`` and ``"en"``, so
a client on any third locale fell through every scope and saw no commands at
all. Every scope is therefore pushed three times: once per supported language,
and once with no language at all (English text) as the fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from html import escape

import structlog
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

from src.bot.command_registry import (
    SUPPORTED_LANGUAGES,
    CommandScope,
    RegistryAudit,
    SpecViolation,
    audit_registry,
    specs_for_scope,
    validate_specs,
)
from src.database.repositories.bot_config import BotConfigRepository

logger = structlog.get_logger(__name__)

# bot_config key holding the chat ids we last pushed a per-admin scope to.
# Without it a demoted admin keeps their /admin and /costs menu forever: the
# Bot API has no "list every scope you ever set", so the only way to delete one
# is to remember that we created it.
PUSHED_SCOPES_KEY = "command_scopes_pushed"

# bot_config key holding the digest of the last drift alert sent to admins, so a
# restart loop cannot turn one unfixed problem into a stream of identical DMs.
DRIFT_ALERT_KEY = "command_sync_last_alert"

# Push variants: one per supported language, plus the language-less fallback
# (None) that clients on any other locale resolve to. English text for the
# fallback — it is the wider of the two audiences for an unknown locale.
_PUSH_LANGUAGES: tuple[str | None, ...] = (None, *SUPPORTED_LANGUAGES)

# Read-back is bounded on purpose. The two global scopes are verified in full
# (6 calls); per-admin scopes only get their language-less variant checked, so
# a deployment with many admins does not turn startup into dozens of round
# trips. The admin scopes' remaining variants are still *pushed*, and a failure
# to push is caught directly.
_VERIFY_LANGUAGES_ADMIN: tuple[str | None, ...] = (None,)

# Telegram rejects a message body over 4096 characters outright, and a mangled
# admin_ids list yields one ~140-char problem per admin. Cap the list rather
# than lose the whole alert; the remainder is counted, and the full set is in
# the log and in `scripts/verify_commands`.
_MAX_ALERT_PROBLEMS = 15

# Bare 9-11 digit integers are auto-linked by Telegram as dead "phone" links
# (the same trap documented for the admin panel), and every chat id in an alert
# line is exactly that shape. Wrapping them in <code> also makes them tappable
# to copy, which is what an operator wants to do with them.
_BARE_ID_RE = re.compile(r"(?<![\w:/])(\d{6,15})(?![\w])")


# The three Bot API scope types this project uses. Spelled as a union rather
# than the `BotCommandScope` base class because aiogram's set_my_commands is
# typed against the concrete union, and the base class does not satisfy it.
TelegramScope = BotCommandScopeAllGroupChats | BotCommandScopeAllPrivateChats | BotCommandScopeChat


# Global scopes this project deliberately keeps EMPTY, and actively empties.
#
# Telegram resolves a menu most-specific-scope-first and never merges lists: the
# first scope that has anything set wins outright. So a scope we do not manage
# is not neutral — it silently *replaces* the one we do. Concretely, on
# 2026-08-08 the production bot was found still holding an
# `all_chat_administrators` list of `[help, summary]` left by a version that
# predates the registry. Every chat administrator therefore saw that stale pair
# and never `/kb`, while `all_group_chats` — correctly carrying `/kb`, verified
# green on every deploy — was never reached. Ordinary members were unaffected,
# which is why it survived so long.
#
# Both are safe to keep empty: `all_private_chats` and `all_group_chats` between
# them cover every chat type this bot serves, so nothing legitimate resolves
# here. Emptiness is the intended state, not merely the convenient one.
UNMANAGED_GLOBAL_SCOPES: tuple[
    tuple[str, BotCommandScopeAllChatAdministrators | BotCommandScopeDefault], ...
] = (
    ("all_chat_administrators", BotCommandScopeAllChatAdministrators()),
    ("default", BotCommandScopeDefault()),
)


@dataclass(frozen=True)
class ScopeTarget:
    """One Telegram command scope we manage."""

    label: str
    scope: CommandScope
    telegram_scope: TelegramScope
    verify_languages: tuple[str | None, ...]


@dataclass(frozen=True)
class LiveCommandDiff:
    """Difference between what we pushed and what Telegram reports back."""

    target: str
    language: str | None
    missing: tuple[str, ...] = ()
    """Expected by the registry, absent from Telegram."""

    unexpected: tuple[str, ...] = ()
    """Present at Telegram, not in the registry — a stale or hand-made entry."""

    changed: tuple[str, ...] = ()
    """Same command, different description."""

    @property
    def empty(self) -> bool:
        return not (self.missing or self.unexpected or self.changed)

    def describe(self) -> str:
        lang = self.language or "default"
        parts: list[str] = []
        if self.missing:
            parts.append("missing " + ", ".join(f"/{c}" for c in self.missing))
        if self.unexpected:
            parts.append("unexpected " + ", ".join(f"/{c}" for c in self.unexpected))
        if self.changed:
            parts.append("description differs for " + ", ".join(f"/{c}" for c in self.changed))
        return f"{self.target}[{lang}]: " + "; ".join(parts)


@dataclass
class CommandSyncReport:
    """Everything one sync run learned. Consumed by startup and by the CLI."""

    pushed: tuple[str, ...] = ()
    """Scope labels successfully pushed."""

    deleted_scopes: tuple[int, ...] = ()
    """Per-admin scopes removed because the user is no longer an admin."""

    stale_scopes: tuple[int, ...] = ()
    """Per-admin scopes that still exist for non-admins and were NOT removed —
    a read-only run reports them, a pushing run empties this by deleting them."""

    push_failures: tuple[str, ...] = ()
    live_diffs: tuple[LiveCommandDiff, ...] = ()
    unverified: tuple[str, ...] = ()
    """Scopes whose read-back failed — the state is UNKNOWN, not clean."""

    shadow_scopes: tuple[str, ...] = ()
    """Unmanaged global scopes found non-empty — each one hides a managed scope."""

    registry_audit: RegistryAudit | None = None
    spec_violations: tuple[SpecViolation, ...] = ()
    notes: tuple[str, ...] = ()
    """Non-problems worth printing (skipped cleanup, verification disabled…)."""

    @property
    def ok(self) -> bool:
        return not self.problems()

    def problems(self) -> tuple[str, ...]:
        """Flat list of everything that needs a human."""
        lines: list[str] = []
        lines.extend(f"registry: {v.command} — {v.problem}" for v in self.spec_violations)
        if self.registry_audit is not None:
            lines.extend(self.registry_audit.problems())
        lines.extend(f"push failed: {failure}" for failure in self.push_failures)
        lines.extend(f"live: {diff.describe()}" for diff in self.live_diffs if not diff.empty)
        lines.extend(f"NOT verified: {entry}" for entry in self.unverified)
        lines.extend(
            f"shadowing scope: {entry} — Telegram resolves this before the scopes "
            "the registry manages, so those commands are what users actually see"
            for entry in self.shadow_scopes
        )
        lines.extend(
            f"stale scope: chat {chat_id} still has an admin command menu but is not an admin"
            for chat_id in self.stale_scopes
        )
        return tuple(lines)

    def render_text(self, lang: str = "ru") -> str:
        """HTML message body for the admin DM (bot-wide parse_mode is HTML).

        Capped at :data:`_MAX_ALERT_PROBLEMS` entries. A broken ``admin_ids``
        list produces one problem per admin, and a body over Telegram's 4096
        characters is rejected outright — losing the whole alert exactly when
        it has the most to report. The count of what was cut is kept.
        """
        header = (
            "⚠️ <b>Команды бота разошлись с реестром</b>"
            if lang == "ru"
            else "⚠️ <b>Bot commands drifted from the registry</b>"
        )
        problems = self.problems()
        shown, hidden = problems[:_MAX_ALERT_PROBLEMS], len(problems) - _MAX_ALERT_PROBLEMS
        lines = [header, ""]
        lines.extend(f"• {_escape_alert_line(problem)}" for problem in shown)
        if hidden > 0:
            lines.append(f"• …и ещё {hidden}" if lang == "ru" else f"• …and {hidden} more")
        footer = (
            "\n<i>Проверить: <code>python -m scripts.verify_commands</code>, "
            "починить: <code>--fix</code></i>"
            if lang == "ru"
            else "\n<i>Inspect with <code>python -m scripts.verify_commands</code>, "
            "repair with <code>--fix</code></i>"
        )
        lines.append(footer)
        return "\n".join(lines)


def _escape_alert_line(problem: str) -> str:
    """HTML-escape one alert line, then wrap bare chat ids in ``<code>``.

    Order matters: escaping first means the ``<code>`` tags added here are the
    only markup that survives, so a Telegram error string containing ``<`` can
    never become markup.
    """
    return _BARE_ID_RE.sub(r"<code>\1</code>", escape(problem))


def build_commands(scope: CommandScope, language: str | None) -> list[BotCommand]:
    """Render the registry's entries for one scope into Bot API objects."""
    lang = language or "en"
    return [
        BotCommand(command=spec.command, description=spec.description_for(lang))
        for spec in specs_for_scope(scope)
    ]


def scope_targets(admin_ids: list[int]) -> tuple[ScopeTarget, ...]:
    """The scopes this deployment manages, global ones first."""
    targets = [
        ScopeTarget(
            label="groups",
            scope=CommandScope.GROUPS,
            telegram_scope=BotCommandScopeAllGroupChats(),
            verify_languages=_PUSH_LANGUAGES,
        ),
        ScopeTarget(
            label="private",
            scope=CommandScope.PRIVATE,
            telegram_scope=BotCommandScopeAllPrivateChats(),
            verify_languages=_PUSH_LANGUAGES,
        ),
    ]
    targets.extend(
        ScopeTarget(
            label=f"admin:{admin_id}",
            scope=CommandScope.ADMIN,
            telegram_scope=BotCommandScopeChat(chat_id=admin_id),
            verify_languages=_VERIFY_LANGUAGES_ADMIN,
        )
        for admin_id in admin_ids
    )
    return tuple(targets)


async def _push_target(bot: Bot, target: ScopeTarget) -> list[str]:
    """Push all language variants for one scope. Returns failure descriptions.

    A per-admin scope for someone who has never opened a DM with the bot fails
    with "chat not found" — a real condition (that admin has no command menu)
    but one with a specific, non-obvious cause, so it gets its own wording
    rather than a raw API string repeated three times.
    """
    failures: list[str] = []
    for language in _PUSH_LANGUAGES:
        try:
            await bot.set_my_commands(
                build_commands(target.scope, language),
                scope=target.telegram_scope,
                language_code=language,
            )
        except TelegramBadRequest as exc:
            # The cause is the chat, not the language, so the remaining
            # variants would fail identically: report once, stop calling.
            failures.append(
                f"{target.label}: Telegram cannot address this chat ({exc.message}) — "
                "either the id in admin_ids is wrong, or that admin has never "
                "started a chat with the bot"
            )
            logger.warning(
                "command_push_rejected",
                target=target.label,
                language=language,
                error=str(exc),
            )
            break
        except Exception as exc:
            failures.append(f"{target.label}[{language or 'default'}]: {exc}")
            logger.warning(
                "command_push_failed",
                target=target.label,
                language=language,
                error=str(exc),
            )
    return failures


async def _verify_target(bot: Bot, target: ScopeTarget) -> tuple[list[LiveCommandDiff], list[str]]:
    """Read back what Telegram stored for one scope and diff it.

    Returns ``(diffs, unverified)``. A read-back that *fails* is reported as
    ``unverified``, never dropped: a scope we could not read produces no diff,
    which is byte-for-byte what a clean scope produces. Swallowing the error
    would let a rate limit or a network blip render as "✓ registry, handlers
    and Telegram agree" — a verifier whose silence means "fine" verifies
    nothing.
    """
    diffs: list[LiveCommandDiff] = []
    unverified: list[str] = []
    for language in target.verify_languages:
        expected = {cmd.command: cmd.description for cmd in build_commands(target.scope, language)}
        try:
            live_commands = await bot.get_my_commands(
                scope=target.telegram_scope,
                language_code=language,
            )
        except Exception as exc:
            logger.warning(
                "command_readback_failed",
                target=target.label,
                language=language,
                error=str(exc),
            )
            unverified.append(f"{target.label}[{language or 'default'}]: {exc}")
            continue
        live = {cmd.command: cmd.description for cmd in live_commands}

        diff = LiveCommandDiff(
            target=target.label,
            language=language,
            missing=tuple(sorted(set(expected) - set(live))),
            unexpected=tuple(sorted(set(live) - set(expected))),
            changed=tuple(
                sorted(name for name in set(expected) & set(live) if expected[name] != live[name])
            ),
        )
        if not diff.empty:
            diffs.append(diff)
    return diffs, unverified


@dataclass(frozen=True)
class UnmanagedScopeReport:
    """Outcome of one reconcile pass over :data:`UNMANAGED_GLOBAL_SCOPES`."""

    shadows: tuple[str, ...] = ()
    """Still shadowing when this returned — a problem needing a human. A delete
    that failed is reported here, with its cause, rather than also as a separate
    failure: one condition should produce one problem line, and the line that
    matters says users are seeing the wrong menu, not that a call errored."""

    cleared: tuple[str, ...] = ()
    """Found non-empty and successfully emptied — fixed, worth saying out loud."""

    unverified: tuple[str, ...] = ()


async def _reconcile_unmanaged_scopes(bot: Bot, *, clear: bool) -> UnmanagedScopeReport:
    """Read the scopes the registry does not manage; optionally empty them.

    Read-then-delete rather than delete-unconditionally, because the read *is*
    the audit and the steady state is "nothing to delete": 6 API calls per
    startup instead of 12, with the deletes appearing only on the one run that
    actually finds something. This file already bounds its read-back on purpose
    (see :data:`_VERIFY_LANGUAGES_ADMIN`) and doubling every startup's traffic
    to re-delete six empty scopes forever would have gone against that.

    Nothing else writes these scopes, so the gap between the read and the delete
    costs nothing; a list that appears in between is caught by the next startup.

    Only the language variants *we* could have created are examined — the Bot
    API cannot enumerate which languages a scope was set for. The language-less
    variant is the one that shadows every locale, and it is always covered.
    """
    shadows: list[str] = []
    cleared: list[str] = []
    unverified: list[str] = []

    for label, scope in UNMANAGED_GLOBAL_SCOPES:
        for language in _PUSH_LANGUAGES:
            where = f"{label}[{language or 'default'}]"
            try:
                live = await bot.get_my_commands(scope=scope, language_code=language)
            except Exception as exc:
                unverified.append(f"{where}: {exc}")
                logger.warning(
                    "command_scope_audit_failed", target=label, language=language, error=str(exc)
                )
                continue
            if not live:
                continue

            found = f"{where} holds " + ", ".join(f"/{cmd.command}" for cmd in live)
            if not clear:
                shadows.append(found)
                continue
            try:
                await bot.delete_my_commands(scope=scope, language_code=language)
            except Exception as exc:
                # Still shadowing: reporting it as merely a failed call would
                # lose the fact that users are, right now, seeing the wrong menu.
                shadows.append(f"{found} (delete failed: {exc})")
                logger.warning(
                    "command_scope_clear_failed", target=label, language=language, error=str(exc)
                )
                continue
            cleared.append(found)
            logger.info("command_scope_cleared", target=label, language=language)

    return UnmanagedScopeReport(
        shadows=tuple(shadows),
        cleared=tuple(cleared),
        unverified=tuple(unverified),
    )


async def detect_stale_scopes(
    admin_ids: list[int],
    bot_config_repo: BotConfigRepository,
) -> list[int]:
    """Chat ids holding a per-admin command menu whose owner is no longer admin.

    Pure database read — no Bot API call — so a read-only run can report them
    without touching Telegram. The Bot API offers no way to enumerate the
    scopes a bot has set, which is why we remember them ourselves.
    """
    previous = _parse_scope_ids(await bot_config_repo.get(PUSHED_SCOPES_KEY))
    return sorted(set(previous) - set(admin_ids))


async def _cleanup_stale_scopes(
    bot: Bot,
    admin_ids: list[int],
    bot_config_repo: BotConfigRepository,
) -> tuple[list[int], list[int], list[str]]:
    """Delete per-admin scopes for users who are no longer admins.

    Returns ``(deleted_ids, still_stale_ids, failures)``. Every language variant
    has to be deleted separately — dropping the language-less list leaves the
    ru/en ones in place, which is exactly the menu the ex-admin still sees.

    ``TelegramBadRequest`` ("chat not found", "PEER_ID_INVALID") counts as
    *resolved*, not as a failure: Telegram is saying the chat is not something
    this bot can address, so no scope can exist there and there is nothing left
    to clean. Treating it as a failure — the first version did — leaves the id
    in the stored list forever, and a mistyped admin id then reports drift on
    every single restart. Transport and server errors still count as failures
    and keep the id for the next attempt.
    """
    failures: list[str] = []
    stale = await detect_stale_scopes(admin_ids, bot_config_repo)

    deleted: list[int] = []
    for chat_id in stale:
        scope = BotCommandScopeChat(chat_id=chat_id)
        chat_ok = True
        for language in _PUSH_LANGUAGES:
            try:
                await bot.delete_my_commands(scope=scope, language_code=language)
            except TelegramBadRequest as exc:
                logger.info(
                    "command_scope_unreachable",
                    chat_id=chat_id,
                    language=language,
                    error=str(exc),
                )
            except Exception as exc:
                chat_ok = False
                failures.append(f"delete admin:{chat_id}[{language or 'default'}]: {exc}")
                logger.warning(
                    "command_scope_delete_failed",
                    chat_id=chat_id,
                    language=language,
                    error=str(exc),
                )
        if chat_ok:
            deleted.append(chat_id)

    # Keep any id we failed to clear in the stored list so the next start
    # retries it, instead of forgetting the scope exists.
    still_stale = sorted(set(stale) - set(deleted))
    retained = sorted(set(admin_ids) | set(still_stale))
    await bot_config_repo.set(
        PUSHED_SCOPES_KEY,
        retained,
        description="Chat ids that currently hold a per-admin command scope",
    )
    return deleted, still_stale, failures


def _parse_scope_ids(raw: object) -> list[int]:
    """Defensively parse the stored scope list (asyncpg may hand back a str)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


async def sync_bot_commands(
    bot: Bot,
    admin_ids: list[int],
    *,
    bot_config_repo: BotConfigRepository | None = None,
    router: Router | None = None,
    push: bool = True,
    verify: bool = True,
) -> CommandSyncReport:
    """Push the registry to Telegram, clean stale scopes, verify the result.

    ``push=False`` turns this into a read-only audit (what
    ``scripts.verify_commands`` does without ``--fix``). Passing ``router``
    additionally audits the registry against the handlers actually registered —
    done here rather than at each call site so no caller can forget half the
    check.
    """
    targets = scope_targets(admin_ids)
    pushed: list[str] = []
    failures: list[str] = []
    deleted: list[int] = []
    stale: list[int] = []
    notes: list[str] = []

    if push:
        for target in targets:
            target_failures = await _push_target(bot, target)
            failures.extend(target_failures)
            if not target_failures:
                pushed.append(target.label)

        if bot_config_repo is not None:
            deleted_ids, still_stale, cleanup_failures = await _cleanup_stale_scopes(
                bot, admin_ids, bot_config_repo
            )
            deleted.extend(deleted_ids)
            stale.extend(still_stale)
            failures.extend(cleanup_failures)
        else:
            notes.append("stale-scope cleanup skipped: no BotConfigRepository provided")
    else:
        notes.append("read-only run: commands were not pushed")
        if bot_config_repo is not None:
            stale.extend(await detect_stale_scopes(admin_ids, bot_config_repo))

    diffs: list[LiveCommandDiff] = []
    unverified: list[str] = []
    if verify:
        for target in targets:
            target_diffs, target_unverified = await _verify_target(bot, target)
            diffs.extend(target_diffs)
            unverified.extend(target_unverified)
    else:
        notes.append("read-back verification skipped")

    # Deliberately not gated on `verify` alone: this reads in order to know what
    # to delete, so a pushing run cannot skip it without also skipping the fix.
    # On a read-only run it is the whole point — nothing cleared the scope, and
    # reporting it is the only way anyone finds out. A caller asking for neither
    # gets neither, so "read-back verification skipped" stays true.
    unmanaged = UnmanagedScopeReport()
    if push or verify:
        unmanaged = await _reconcile_unmanaged_scopes(bot, clear=push)
        unverified.extend(unmanaged.unverified)
        notes.extend(f"cleared shadowing scope: {entry}" for entry in unmanaged.cleared)

    report = CommandSyncReport(
        pushed=tuple(pushed),
        deleted_scopes=tuple(deleted),
        stale_scopes=tuple(stale),
        push_failures=tuple(failures),
        live_diffs=tuple(diffs),
        unverified=tuple(unverified),
        shadow_scopes=unmanaged.shadows,
        registry_audit=audit_registry(router) if router is not None else None,
        spec_violations=validate_specs(),
        notes=tuple(notes),
    )
    logger.info(
        "command_sync_complete",
        pushed=len(pushed),
        admin_scopes=len(admin_ids),
        deleted_scopes=deleted,
        problems=len(report.problems()),
    )
    return report


async def notify_command_drift(
    bot: Bot,
    report: CommandSyncReport,
    admin_ids: list[int],
    bot_config_repo: BotConfigRepository,
    *,
    lang: str = "ru",
) -> bool:
    """DM the admins about drift — once per distinct problem set.

    The bot restarts on every deploy, and a crash-looping container restarts a
    lot more often than that, so an unconditional "alert on drift" turns one
    unfixed problem into a stream of identical DMs. The sha256 of the rendered
    problem list is stored and compared; the stored digest is cleared once the
    drift is gone, so the *same* problem returning later does alert again.

    Returns True when a message was actually sent. Never raises — this runs on
    the startup path.
    """
    problems = report.problems()
    try:
        if not problems:
            if await bot_config_repo.get(DRIFT_ALERT_KEY):
                await bot_config_repo.set(DRIFT_ALERT_KEY, None)
            return False

        digest = sha256("\n".join(problems).encode("utf-8")).hexdigest()
        if await bot_config_repo.get(DRIFT_ALERT_KEY) == digest:
            logger.info("command_drift_alert_suppressed", digest=digest[:12])
            return False

        text = report.render_text(lang)
        sent = False
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
                sent = True
            except Exception as exc:
                logger.warning("command_drift_alert_failed", admin_id=admin_id, error=str(exc))

        if sent:
            await bot_config_repo.set(
                DRIFT_ALERT_KEY,
                digest,
                description="sha256 of the last command-drift alert already sent to admins",
            )
        return sent
    except Exception as exc:
        logger.warning("command_drift_alert_error", error=str(exc), exc_info=True)
        return False


async def sync_and_report(
    bot: Bot,
    admin_ids: list[int],
    *,
    bot_config_repo: BotConfigRepository,
    router: Router | None = None,
) -> CommandSyncReport:
    """The whole startup routine: push, verify, log, alert. Never raises.

    Runs as a background task (see ``src/main.py``) because it makes roughly
    a dozen sequential Bot API round trips, and none of them need to finish
    before the bot starts answering: a stale autocomplete hint for a second is
    not worth delaying the first polled update, and a rate-limited or slow
    Telegram would otherwise hold up startup with no timeout.

    Being cancellable mid-flight is fine and deliberate: a shutdown between the
    push and the ``command_scopes_pushed`` write leaves Telegram partly updated
    and the record unchanged, which the next successful start converges — the
    push is idempotent and the stale-scope diff is recomputed from scratch.
    """
    report = await setup_bot_commands(
        bot,
        admin_ids,
        bot_config_repo=bot_config_repo,
        router=router,
    )
    if report.ok:
        logger.info(
            "command_registry_synced",
            scopes=len(report.pushed),
            deleted_scopes=list(report.deleted_scopes),
        )
    else:
        # Warn, but never refuse to run: command menus are autocomplete hints,
        # and a bot that will not serve is strictly worse than a stale menu.
        logger.warning(
            "command_registry_drift",
            problems=list(report.problems()),
            pushed=list(report.pushed),
        )
    # Called on both paths on purpose: on drift it DMs the admins (once per
    # distinct problem set), and on a clean run it clears the stored digest so
    # the *same* drift returning later alerts again instead of being suppressed
    # by a stale hash.
    await notify_command_drift(bot, report, admin_ids, bot_config_repo)
    return report


async def setup_bot_commands(
    bot: Bot,
    admin_ids: list[int],
    *,
    bot_config_repo: BotConfigRepository | None = None,
    router: Router | None = None,
) -> CommandSyncReport:
    """Startup entry point: register commands and report what Telegram holds.

    Sets different command menus depending on context:
    - Group chats: the registry's ``GROUPS`` scope
    - Private chats (default): the ``PRIVATE`` scope
    - Admin private chats: the ``ADMIN`` scope, pushed per admin id

    Never raises: an unreachable Bot API at startup must not stop the bot over
    autocomplete hints. Problems come back in the report for the caller to log
    and notify about.
    """
    try:
        return await sync_bot_commands(
            bot,
            admin_ids,
            bot_config_repo=bot_config_repo,
            router=router,
        )
    except Exception as exc:
        logger.warning("command_sync_failed", error=str(exc), exc_info=True)
        return CommandSyncReport(push_failures=(f"command sync aborted: {exc}",))
