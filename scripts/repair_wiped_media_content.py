"""One-off maintenance script: put back the content an ``edited_message`` erased.

Run manually, out of band, **after** the fix in ``MessageRepository.save()`` is
live in production:

    python -m scripts.repair_wiped_media_content              # dry run, changes nothing
    python -m scripts.repair_wiped_media_content --apply

What happened
-------------

``MessageSaverMiddleware`` is registered on ``dp.edited_message`` as well as
``dp.message``, and saves ``message.text or message.caption`` — ``None`` for a
voice note, a video note or an uncaptioned photo. Until the COALESCE fix, the
UPSERT's ``content = EXCLUDED.content`` wrote that ``None`` straight over
content the *bot* had put there: the Whisper transcript, or the
``[Image: ...]`` description from vision analysis.

Measured in production on 2026-09-01: 52 rows emptied this way since
2026-08-03, at two to three a day, in two chats.

Why anything is recoverable at all
----------------------------------

Purely by accident. The same ON CONFLICT branch carried

    original_content = COALESCE(chat_messages.original_content, chat_messages.content)

so the wipe, being the row's *first* content change, moved the doomed text into
``original_content`` on its way past. That column has exactly one writer and
**zero readers** anywhere in the codebase — it is forensic evidence and nothing
else, which is the only reason these words still exist.

Hence the recovery predicate, which is precisely the wipe's signature:

    content IS NULL AND original_content IS NOT NULL

``content`` only ever goes from something back to nothing through two paths:
this defect, and a user clearing a caption they had written (Telegram delivers
``caption=None``, which the old SQL could not tell apart from "this update
carries no content"). The four other writers — ``VoiceTranscriptionService``,
``_update_message_content``, the pipeline's own bot-message save, and the
transcription bookkeeping row — either always pass a value or only ever insert.

The predicate therefore cannot, by itself, distinguish a wiped transcript from a
caption its author deleted on purpose, and restoring the second would put text
back that someone chose to remove. That is what ``shape`` and ``recovered_head``
in the dry-run output are for, and why they are not decoration:

* ``transcript`` — a video note, which cannot carry a caption at all. Unambiguous.
* ``image_description`` — starts with ``[Image:``, so the bot wrote it. Unambiguous.
* ``inspect`` — a voice note or photo whose text could be either. **Read the
  ``recovered_head`` lines before passing ``--apply``.**

Measured on production 2026-09-01 before the first run: all 12 photo rows began
with ``[Image:`` and no voice or video_note row's text was shorter than 31
characters, i.e. none had the shape of a caption. That was checked with a
separate query; printing it here is what makes the same check possible next
time, by someone who is not holding this context.

What this script deliberately does NOT do
-----------------------------------------

* **It does not clear ``original_content``.** Nothing reads it, so leaving it is
  free, and it is the only remaining trace that a row was ever damaged — which
  is what makes this script's own predicate re-checkable afterwards.
* **It does not touch ``edit_count`` / ``edited_at``.** They record that an
  ``edited_message`` really did arrive. That is true, and rewriting history to
  hide it would be a second falsification on top of the first.
* **It does not go through ``MessageRepository.save()``.** That method's UPSERT
  is the thing that caused this; routing the repair through it would stamp
  ``edited_at`` and bump ``edit_count`` on all 52 rows.
* **It does not repair the chunk archive.** Restoring ``content`` does not
  re-chunk anything: ``ChunkRepository``'s watermark is ``MAX(msg_to)`` and the
  fetch is ``message_id > watermark``, so a row already below the watermark is
  never re-read. Rows still *above* their chat's watermark do self-heal on the
  indexer's next pass — which is the whole reason to run this sooner rather
  than later. The dry run prints that split; see ``--help``.

Everything is a dry run until ``--apply`` is passed. The counts printed by a dry
run are the counts a real run would change, and re-running after an apply is a
no-op (the predicate no longer matches).
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

import asyncpg
import structlog

from src.config import Settings
from src.database.connection import close_pool, create_pool

logger = structlog.get_logger(__name__)

# The wipe's signature. Kept in one place because the SELECT that reports and
# the UPDATE that repairs must not be able to drift apart -- a repair whose
# predicate is wider than its preview is exactly the shape of an incident.
_WIPED = "content IS NULL AND original_content IS NOT NULL"


def describe_target(database_url: str) -> str:
    """``host:port/dbname`` for the log line, with credentials stripped.

    Named rather than assumed, because the same command line hits a different
    database depending on which `.env` is in scope: from a laptop venv it is the
    local dev container, from inside the production container it is production.
    Both were exercised while building this script and the console output was
    identical. Worse, the two failure directions are asymmetric but look the
    same: aiming at prod and hitting dev prints `rows_matching=0`, which reads
    as "already repaired" and would close the incident with the damage intact.

    Deliberately parsed rather than echoed: `database_url` carries a password,
    and this line goes to a log an operator will paste into a chat.
    """
    without_scheme = database_url.split("://", 1)[-1]
    # Everything before the last '@' is credentials.
    host_and_db = without_scheme.rsplit("@", 1)[-1]
    return host_and_db.split("?", 1)[0]


async def find_wiped(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Every row matching the wipe signature, with its archive prospects.

    ``above_watermark`` answers the question that decides urgency: a restored
    row whose id is still ahead of its chat's ``MAX(msg_to)`` will be picked up
    by the chunk indexer on a later pass and land in the archive correctly. One
    below it never will, no matter what this script writes.
    """
    return await pool.fetch(
        f"""
        SELECT m.chat_id,
               m.message_id,
               m.message_type,
               m.created_at,
               m.edit_count,
               length(m.original_content) AS recovered_chars,
               left(m.original_content, 60) AS recovered_head,
               CASE
                   WHEN m.message_type = 'video_note' THEN 'transcript'
                   WHEN m.original_content LIKE '[Image:%' THEN 'image_description'
                   ELSE 'inspect'
               END AS shape,
               m.message_id > COALESCE(
                   (SELECT max(c.msg_to) FROM chat_chunks c WHERE c.chat_id = m.chat_id), 0
               ) AS above_watermark
        FROM chat_messages m
        WHERE {_WIPED}
        ORDER BY m.chat_id, m.message_id
        """  # noqa: S608 -- _WIPED is a module constant, never user input
    )


async def repair(pool: asyncpg.Pool, *, apply: bool) -> int:
    """Restore ``content`` from ``original_content``. Returns rows affected.

    A single statement rather than a loop over ids: the predicate is the
    authority on what gets touched, so the set the UPDATE acts on cannot differ
    from the set the preview described by more than whatever arrived in
    between -- and anything that arrived in between is, by construction,
    another damaged row that also wants repairing.
    """
    if not apply:
        return 0
    status = await pool.execute(
        f"UPDATE chat_messages SET content = original_content WHERE {_WIPED}"  # noqa: S608
    )
    # asyncpg returns the command tag, e.g. "UPDATE 52".
    return int(status.rsplit(" ", 1)[-1])


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Restore chat_messages.content that an edited_message erased, "
            "from the copy that survived in original_content."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes; without it the script only reports what it would do",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "target database; defaults to DATABASE_URL from the environment. "
            "Pass it when you want the target to be a choice rather than whatever "
            "the ambient .env happens to say."
        ),
    )
    args = parser.parse_args()

    database_url = args.database_url or Settings().database_url  # type: ignore[call-arg]
    # BEFORE the pool, before the query, before any chance of writing: say which
    # database this is about to touch. See describe_target's docstring.
    logger.info(
        "connecting",
        target=describe_target(database_url),
        source="--database-url" if args.database_url else "environment",
        mode="APPLY" if args.apply else "dry run",
    )
    pool = await create_pool(database_url)

    try:
        rows = await find_wiped(pool)
        by_type: Counter[str] = Counter(row["message_type"] for row in rows)
        salvageable = sum(1 for row in rows if row["above_watermark"])

        for row in rows:
            logger.info(
                "wiped_row",
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                message_type=row["message_type"],
                created_at=str(row["created_at"]),
                edit_count=row["edit_count"],
                recovered_chars=row["recovered_chars"],
                shape=row["shape"],
                recovered_head=row["recovered_head"],
                reaches_chunk_archive=row["above_watermark"],
            )

        updated = await repair(pool, apply=args.apply)
    finally:
        await close_pool(pool)

    logger.info(
        "repair_complete",
        target=describe_target(database_url),
        mode="APPLIED" if args.apply else "DRY RUN — nothing written",
        rows_matching=len(rows),
        rows_updated=updated,
        by_message_type=dict(by_type),
        chars_recovered=sum(row["recovered_chars"] for row in rows),
        will_reach_chunk_archive=salvageable,
        already_past_chunk_watermark=len(rows) - salvageable,
    )
    if rows and not args.apply:
        logger.info(
            "note_dry_run",
            detail=(
                "Nothing was written. Re-run with --apply. Rows reported as "
                "reaches_chunk_archive=false are restored in chat_messages but stay "
                "missing from chat_chunks: the indexer's watermark has already passed them."
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
