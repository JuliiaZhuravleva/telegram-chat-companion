#!/usr/bin/env python
"""Run the chunk indexer to completion, instead of waiting for the schedule.

The background worker (`ChatChunkIndexer`, S4) already backfills: it walks the
history from its own watermark and stops when it runs out of closed sessions.
On its 15-minute schedule a chat with tens of thousands of messages takes a few
hours to catch up, which is fine unattended and tedious when you want the index
*now* -- before a measurement, after a restore, or while watching whether the
chunker does something sensible to a real conversation.

This is the same code, driven in a loop. It is deliberately not a second
implementation: a backfill that chunks differently from the indexer produces an
index whose two halves disagree, and the natural key would happily store both.

Stops when a pass writes no chunks and embeds nothing, i.e. the index has
caught up with the history. `--max-passes` bounds it anyway, because "no
progress" and "cannot progress" look the same from here and a loop against a
misconfigured provider should end.

``<dsn>`` is a REQUIRED positional with NO default, same rule as the other
scripts here: a tool that can be pointed at a live database must never be able
to *default* onto one. Writes go only to `chat_chunks`; nothing else is
touched, and nothing reads chunks until S5, so a run against production
changes no bot behaviour.

Usage::

    python -m scripts.backfill_chunks postgresql://user:pass@host/db
    python -m scripts.backfill_chunks <dsn> --max-passes 5 --dry-run

Exit codes::

    0   the index caught up (or --max-passes reached with progress still being made)
    2   usage / connection error
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import asyncpg

from src.config import Settings
from src.database.connection import close_pool, create_pool
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.chunks import ChunkRepository
from src.services.ai.router import AIRouter
from src.services.chat_config import ChatConfigService
from src.services.rag.indexer import ChatChunkIndexer

_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 5

_EXIT_OK = 0
_EXIT_BAD_INPUT = 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dsn", help="PostgreSQL DSN (required, no default)")
    parser.add_argument(
        "--max-passes",
        type=int,
        default=100,
        help="stop after this many passes even if work remains (default: 100)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what is pending and exit without writing or embedding",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = Settings()

    try:
        pool = await create_pool(args.dsn, min_size=_POOL_MIN_SIZE, max_size=_POOL_MAX_SIZE)
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"could not connect: {exc}", file=sys.stderr)
        return _EXIT_BAD_INPUT

    try:
        chunks_repo = ChunkRepository(pool)
        before = await chunks_repo.counts()
        print(f"chat_chunks before: {before['total']} rows, {before['pending']} unembedded")

        if args.dry_run:
            return _EXIT_OK

        indexer = ChatChunkIndexer(
            pool=pool,
            ai_router=AIRouter(settings),
            chat_config=ChatConfigService(
                settings.bot,
                BotConfigRepository(pool),
                ChatSettingsRepository(pool),
            ),
            config=settings.chunk_indexer,
        )

        for attempt in range(1, args.max_passes + 1):
            result = await indexer.run_once()
            print(
                f"pass {attempt}: {result['chunks']} chunks written, "
                f"{result['embedded']} embedded, {result['chats']} chats"
            )
            if not result["chunks"] and not result["embedded"]:
                break

        after = await chunks_repo.counts()
        print(f"chat_chunks after:  {after['total']} rows, {after['pending']} unembedded")
    finally:
        await close_pool(pool)

    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
