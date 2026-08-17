"""Declarative registry of the bot's slash commands, plus a pure code↔registry audit.

Single source of truth for *which* commands exist, *where* Telegram should
advertise them, and *how* they are described. ``src/bot/commands.py`` renders
this into ``set_my_commands`` calls; ``tests/unit/test_command_registry.py``
audits it against the handlers actually registered on the router; and
``scripts/verify_commands.py`` compares it with what the live bot really has.

Why a registry at all: the three hardcoded dicts this replaces had no link to
the handlers, and had already drifted — ``/kb`` and ``/remember`` were
implemented and advertised nowhere. Merging to ``main`` deploys, so drift used
to ship silently.

Everything here is pure: no Bot, no network, no database. That is what lets the
CI ``test`` job — the check the deployer waits on — run the audit.

Scope model (a deliberate subset of the Bot API's scopes, see
https://core.telegram.org/bots/api#botcommandscope):

- ``GROUPS``  → ``BotCommandScopeAllGroupChats``
- ``PRIVATE`` → ``BotCommandScopeAllPrivateChats``
- ``ADMIN``   → ``BotCommandScopeChat(admin_id)``, one per bot admin

A spec with **no** scopes is deliberately hidden and must say why
(``hidden_reason``). Hidden ≠ missing: the audit treats an unexplained absence
as drift, and an explained one as a decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from aiogram import Router
from aiogram.filters import Command

from src.bot.filters.admin import IsAdmin


class CommandScope(StrEnum):
    """Where Telegram should advertise a command."""

    GROUPS = "groups"
    PRIVATE = "private"
    ADMIN = "admin"


@dataclass(frozen=True)
class CommandSpec:
    """One slash command, as advertised to Telegram."""

    command: str
    """Command name without the leading slash."""

    scopes: frozenset[CommandScope]
    """Scopes to advertise in. Empty ⇒ implemented but deliberately hidden."""

    description: dict[str, str]
    """i18n description, ``{"ru": ..., "en": ...}``. Telegram caps it at 256 chars."""

    admin_only: bool = False
    """True when the handler is gated by ``IsAdmin``. Cross-checked by the audit:
    an admin-gated command must not be advertised outside ``ADMIN``, and a
    command advertised only to admins should be gated."""

    hidden_reason: str | None = None
    """Required iff ``scopes`` is empty — why this command is not advertised."""

    def description_for(self, lang: str) -> str:
        """Resolve the description, falling back to en then ru."""
        return self.description.get(lang) or self.description.get("en") or self.description["ru"]


# Declaration order == the order Telegram shows them in.
COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        command="start",
        scopes=frozenset({CommandScope.PRIVATE, CommandScope.ADMIN}),
        description={"ru": "Начать", "en": "Start the bot"},
    ),
    CommandSpec(
        command="help",
        scopes=frozenset({CommandScope.GROUPS, CommandScope.PRIVATE, CommandScope.ADMIN}),
        description={"ru": "Возможности бота", "en": "Bot features"},
    ),
    CommandSpec(
        command="summary",
        # Groups only: the DM handler exists purely to answer "group chats
        # only" instead of leaving a silent no-op, which is not something worth
        # advertising in the private menu.
        scopes=frozenset({CommandScope.GROUPS}),
        description={"ru": "Саммари чата", "en": "Chat summary"},
    ),
    CommandSpec(
        command="summary500",
        # Same scope story as /summary: groups only, with a DM handler that
        # exists solely to avoid a silent no-op.
        scopes=frozenset({CommandScope.GROUPS}),
        description={"ru": "Саммари по 500 сообщениям", "en": "Summary of 500 messages"},
    ),
    CommandSpec(
        command="kb",
        scopes=frozenset({CommandScope.GROUPS, CommandScope.PRIVATE, CommandScope.ADMIN}),
        description={"ru": "База знаний чата", "en": "Chat knowledge base"},
    ),
    CommandSpec(
        command="remember",
        scopes=frozenset(),
        description={
            "ru": "Сохранить факт в базу знаний чата",
            "en": "Save a fact to the chat knowledge base",
        },
        hidden_reason=(
            "Restricted to chat organizers / bot admins "
            "(handlers/commands.py:handle_remember). No Telegram scope expresses "
            "'organizers of this chat', so advertising it would offer every member "
            "a command that answers them with a refusal. The reply requirement is "
            "gone since S2/KB-09 -- free text works too -- but the authority gate "
            "is what keeps this hidden."
        ),
    ),
    CommandSpec(
        command="admin",
        scopes=frozenset({CommandScope.ADMIN}),
        description={"ru": "Панель администратора", "en": "Admin panel"},
        admin_only=True,
    ),
    CommandSpec(
        command="settings",
        scopes=frozenset({CommandScope.ADMIN}),
        description={"ru": "Настройки бота", "en": "Bot settings"},
        admin_only=True,
    ),
    CommandSpec(
        command="costs",
        scopes=frozenset({CommandScope.ADMIN}),
        description={"ru": "Расходы на AI за 24ч", "en": "AI cost summary (24h)"},
        admin_only=True,
    ),
    CommandSpec(
        command="panel",
        scopes=frozenset({CommandScope.ADMIN}),
        description={
            "ru": "Настройки чата по ссылке/названию",
            "en": "Chat settings by link/title",
        },
        admin_only=True,
    ),
)

SPECS_BY_COMMAND: dict[str, CommandSpec] = {spec.command: spec for spec in COMMANDS}

# Languages we push descriptions for. Every scope is ALSO pushed without a
# language_code (see commands.py) — Telegram does not fall back from "es" to
# "en", it falls back to the language-less variant, and without one a client on
# a third locale sees no commands at all.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("ru", "en")

# Telegram's own constraint on command names (Bot API: 1-32 chars, lowercase
# letters, digits and underscores).
COMMAND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
MAX_DESCRIPTION_LEN = 256


def specs_for_scope(scope: CommandScope) -> tuple[CommandSpec, ...]:
    """Specs advertised in ``scope``, in declaration order."""
    return tuple(spec for spec in COMMANDS if scope in spec.scopes)


# ---------------------------------------------------------------------------
# Handler discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HandlerFacts:
    """What the router says about one command name."""

    command: str
    handlers: tuple[str, ...]
    """Qualified handler names, for error messages."""

    admin_gated: bool
    """True when *every* handler for this command carries an ``IsAdmin`` filter.

    All-not-any deliberately: ``/summary`` has one group handler and one DM
    handler, and a command is only genuinely admin-restricted when no ungated
    path into it exists.
    """


def _walk_routers(router: Router) -> Iterator[Router]:
    yield router
    for sub in router.sub_routers:
        yield from _walk_routers(sub)


def discover_handler_commands(router: Router) -> dict[str, HandlerFacts]:
    """Collect the commands actually handled under ``router``.

    Reads the ``Command`` filter objects off each message handler. Chat-type
    filters (``F.chat.type == ...``) are deliberately NOT introspected: they are
    ``MagicFilter`` instances whose operation chain is a private implementation
    detail, so scope stays declared in :data:`COMMANDS` rather than inferred.
    ``IsAdmin`` is a plain class instance and is safe to detect.
    """
    found: dict[str, list[tuple[str, bool]]] = {}

    for sub_router in _walk_routers(router):
        observer = sub_router.observers.get("message")
        if observer is None:
            continue
        for handler in observer.handlers:
            names: list[str] = []
            admin_gated = False
            for flt in handler.filters or []:
                callback = flt.callback
                if isinstance(callback, Command):
                    names.extend(str(cmd) for cmd in callback.commands)
                elif isinstance(callback, IsAdmin):
                    admin_gated = True
            if not names:
                continue
            handler_name = getattr(handler.callback, "__name__", repr(handler.callback))
            for name in names:
                found.setdefault(name, []).append((handler_name, admin_gated))

    return {
        name: HandlerFacts(
            command=name,
            handlers=tuple(h for h, _ in entries),
            admin_gated=all(gated for _, gated in entries),
        )
        for name, entries in sorted(found.items())
    }


# ---------------------------------------------------------------------------
# Audit: code ↔ registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryAudit:
    """Result of comparing :data:`COMMANDS` with the router's handlers."""

    unregistered: tuple[str, ...] = ()
    """Handled by the bot, absent from the registry — working but unadvertised."""

    orphaned: tuple[str, ...] = ()
    """In the registry, no handler — advertised but dead."""

    admin_mismatch: tuple[str, ...] = ()
    """Admin gating and advertised scope disagree (each entry explains how)."""

    hidden: tuple[str, ...] = ()
    """Informational: deliberately unadvertised commands, with their reason."""

    @property
    def ok(self) -> bool:
        """True when nothing needs a human. ``hidden`` is informational only."""
        return not (self.unregistered or self.orphaned or self.admin_mismatch)

    def problems(self) -> tuple[str, ...]:
        """Flat, human-readable list of everything wrong (empty when ``ok``)."""
        lines: list[str] = []
        lines.extend(
            f"unregistered: /{name} — handled but advertised nowhere" for name in self.unregistered
        )
        lines.extend(f"orphaned: /{name} — advertised but no handler" for name in self.orphaned)
        lines.extend(f"admin: {detail}" for detail in self.admin_mismatch)
        return tuple(lines)


def audit_registry(router: Router) -> RegistryAudit:
    """Compare the registry with the handlers registered under ``router``."""
    handlers = discover_handler_commands(router)

    unregistered = tuple(name for name in handlers if name not in SPECS_BY_COMMAND)
    orphaned = tuple(spec.command for spec in COMMANDS if spec.command not in handlers)

    admin_mismatch: list[str] = []
    for spec in COMMANDS:
        facts = handlers.get(spec.command)
        if facts is None:
            continue  # already reported as orphaned
        advertised_publicly = bool(spec.scopes - {CommandScope.ADMIN})
        if facts.admin_gated and advertised_publicly:
            admin_mismatch.append(
                f"/{spec.command} is IsAdmin-gated ({', '.join(facts.handlers)}) "
                f"but advertised in {sorted(s.value for s in spec.scopes)}"
            )
        if spec.admin_only and not facts.admin_gated:
            admin_mismatch.append(
                f"/{spec.command} is declared admin_only but its handlers "
                f"({', '.join(facts.handlers)}) carry no IsAdmin filter"
            )
        if facts.admin_gated and not spec.admin_only:
            admin_mismatch.append(
                f"/{spec.command} is IsAdmin-gated in code but not declared admin_only"
            )

    hidden = tuple(
        f"/{spec.command}: {spec.hidden_reason or 'NO REASON GIVEN'}"
        for spec in COMMANDS
        if not spec.scopes
    )

    return RegistryAudit(
        unregistered=unregistered,
        orphaned=orphaned,
        admin_mismatch=tuple(admin_mismatch),
        hidden=hidden,
    )


# ---------------------------------------------------------------------------
# Static self-checks on the registry itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecViolation:
    """One malformed :class:`CommandSpec`."""

    command: str
    problem: str


def validate_specs(specs: tuple[CommandSpec, ...] = COMMANDS) -> tuple[SpecViolation, ...]:
    """Check the registry against Telegram's own limits and our conventions.

    Separate from :func:`audit_registry` because these are properties of the
    declaration alone — no router needed, and a violation here means the push
    itself would be rejected or would advertise nonsense.
    """
    violations: list[SpecViolation] = []
    seen: set[str] = set()

    for spec in specs:
        if not COMMAND_NAME_RE.match(spec.command):
            violations.append(
                SpecViolation(spec.command, "name must match ^[a-z0-9_]{1,32}$ (Bot API limit)")
            )
        if spec.command in seen:
            violations.append(SpecViolation(spec.command, "duplicate entry in the registry"))
        seen.add(spec.command)

        for lang in SUPPORTED_LANGUAGES:
            text = spec.description.get(lang)
            if not text:
                violations.append(SpecViolation(spec.command, f"missing {lang!r} description"))
            elif len(text) > MAX_DESCRIPTION_LEN:
                violations.append(
                    SpecViolation(
                        spec.command,
                        f"{lang!r} description is {len(text)} chars, "
                        f"Telegram allows {MAX_DESCRIPTION_LEN}",
                    )
                )

        if not spec.scopes and not spec.hidden_reason:
            violations.append(
                SpecViolation(spec.command, "advertised nowhere but carries no hidden_reason")
            )
        if spec.scopes and spec.hidden_reason:
            violations.append(
                SpecViolation(spec.command, "has a hidden_reason but is advertised anyway")
            )

    return tuple(violations)
