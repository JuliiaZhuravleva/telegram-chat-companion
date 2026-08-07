"""Check (and optionally repair) the bot's registered slash commands.

    python -m scripts.verify_commands            # report, exit 1 on drift
    python -m scripts.verify_commands --json     # machine-readable report
    python -m scripts.verify_commands --fix      # re-push + clean stale scopes

The same check runs at startup (``src/main.py``), which covers every deploy.
This wrapper exists for the times in between: after editing ``admin_ids`` by
hand, after someone touches the menu through BotFather, or when the startup
alert says something drifted and you want the detail without restarting the
bot. On production, via the same route as the backfill script:

    bin/ssh-claw '/usr/local/bin/docker exec companion-bot-1 \
        python -m scripts.verify_commands'

It shares every comparison with the runtime path (``src.bot.commands``) rather
than reimplementing one — a verifier that computes "correct" differently from
the code it verifies checks nothing but itself.

Exit codes: 0 = registry, handlers and Telegram agree; 1 = drift (details on
stdout); 2 = the check could not be completed (bad token, unreachable API).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import structlog
from aiogram import Bot

from src.bot.command_registry import CommandScope, specs_for_scope
from src.bot.commands import CommandSyncReport, sync_bot_commands
from src.bot.handlers import router as main_router
from src.config import Settings
from src.database.connection import close_pool, create_pool
from src.database.repositories.bot_config import BotConfigRepository
from src.utils import parse_admin_ids

logger = structlog.get_logger(__name__)


def _report_to_dict(report: CommandSyncReport, admin_ids: list[int]) -> dict[str, Any]:
    """Machine-readable form of a report (``--json``)."""
    audit = report.registry_audit
    return {
        "ok": report.ok,
        "problems": list(report.problems()),
        "admin_ids": admin_ids,
        "registry": {
            scope.value: [spec.command for spec in specs_for_scope(scope)] for scope in CommandScope
        },
        "audit": {
            "unregistered": list(audit.unregistered) if audit else [],
            "orphaned": list(audit.orphaned) if audit else [],
            "admin_mismatch": list(audit.admin_mismatch) if audit else [],
            "hidden": list(audit.hidden) if audit else [],
        },
        "spec_violations": [
            {"command": v.command, "problem": v.problem} for v in report.spec_violations
        ],
        "live_diffs": [
            {
                "target": d.target,
                "language": d.language,
                "missing": list(d.missing),
                "unexpected": list(d.unexpected),
                "changed": list(d.changed),
            }
            for d in report.live_diffs
        ],
        "unverified": list(report.unverified),
        "pushed": list(report.pushed),
        "deleted_scopes": list(report.deleted_scopes),
        "stale_scopes": list(report.stale_scopes),
        "notes": list(report.notes),
    }


def _print_human(report: CommandSyncReport, admin_ids: list[int], *, fixed: bool) -> None:
    print("Command registry")
    for scope in CommandScope:
        names = ", ".join(f"/{spec.command}" for spec in specs_for_scope(scope)) or "—"
        print(f"  {scope.value:<8} {names}")

    audit = report.registry_audit
    if audit and audit.hidden:
        print("\nDeliberately hidden")
        for entry in audit.hidden:
            print(f"  {entry}")

    if report.notes:
        print("\nNotes")
        for note in report.notes:
            print(f"  {note}")

    print(f"\nAdmin scopes checked: {len(admin_ids)}")
    if fixed:
        print(f"Pushed: {', '.join(report.pushed) or '—'}")
        if report.deleted_scopes:
            print(f"Deleted stale scopes: {report.deleted_scopes}")

    problems = report.problems()
    if problems:
        print(f"\nDRIFT ({len(problems)}):")
        for problem in problems:
            print(f"  ✗ {problem}")
        if not fixed:
            print("\nRe-run with --fix to push the registry and clear stale scopes.")
    else:
        print("\n✓ registry, handlers and Telegram agree")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="push the registry to Telegram and delete stale per-admin scopes",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args()

    # Logs to stderr, the report to stdout. structlog's default is stdout, which
    # made `--json | jq` fail on "Extra data" — the one thing a machine-readable
    # flag must not do.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

    settings = Settings()
    pool = await create_pool(settings.database_url)
    bot = Bot(token=settings.telegram_bot_token)

    try:
        bot_config_repo = BotConfigRepository(pool)
        admin_ids = parse_admin_ids(await bot_config_repo.get("admin_ids"))
        report = await sync_bot_commands(
            bot,
            admin_ids,
            bot_config_repo=bot_config_repo,
            router=main_router,
            push=args.fix,
            verify=True,
        )
    except Exception as exc:
        # Exit 2, not 1: "could not check" must not read as "checked, all good"
        # to whatever runs this, and must not read as "drift" either.
        logger.error("verify_commands_failed", error=str(exc), exc_info=True)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"Could not complete the check: {exc}", file=sys.stderr)
        return 2
    finally:
        await bot.session.close()
        await close_pool(pool)

    if args.json:
        print(json.dumps(_report_to_dict(report, admin_ids), ensure_ascii=False, indent=2))
    else:
        _print_human(report, admin_ids, fixed=args.fix)

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
