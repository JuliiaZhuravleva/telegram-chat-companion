"""One-off maintenance script: populate ``chat_messages.transcribed_message_id``
for voice transcriptions that predate migration 028.

Run manually, out of band:

    python -m scripts.backfill_transcription_links              # dry run, changes nothing
    python -m scripts.backfill_transcription_links --apply
    python -m scripts.backfill_transcription_links --apply --infer-links

Read this before trusting the output
------------------------------------

**Most historical transcriptions are not recoverable, and no script can change
that.** Until migration 028 the bot never stored the message it posted: the old
save path UPSERTed the transcript onto the *voice* message's own row, under the
voice message's id. The Telegram message id of the transcription itself was
therefore never written down anywhere, and the Bot API cannot read chat history
back to look it up. A row that does not exist cannot be linked.

So the script does two different jobs, and keeps their numbers apart:

1. **Repair (exact, always run).** The old code saved with
   ``message_type='transcription'`` while meaning "this is the voice message,
   now with its transcript". Normally the UPSERT's DO UPDATE dropped that label
   on the floor, because ``MessageSaverMiddleware`` had already inserted the row
   as 'voice'. But in a chat with ``save_messages`` disabled there was no prior
   row, so the INSERT really did land labelled 'transcription' — a row that is
   the *speaker's* words wearing the label that, since migration 028, means
   "bot bookkeeping, keep out of the prompt". Left alone, those utterances would
   silently disappear from the model's history. They are identifiable without
   guessing (``is_bot_message`` set, ``reply_to_message_id = message_id`` — a
   row that replies to itself, which nothing else produces) and are relabelled
   back to 'voice'.

2. **Infer (heuristic, opt-in via --infer-links).** Where a user replied to a
   transcription, that user's row still carries ``reply_to_message_id = T``,
   the transcription's real id, even though we hold no row for T. If exactly one
   voice/video-note message sits close enough before T in the same chat, T is
   almost certainly its transcription, and a linking row is created. This is an
   inference from adjacency, not a record — it is off by default, it refuses
   whenever more than one candidate is in range, and ``--max-gap`` bounds how
   far back it will look. Anything ambiguous is reported, never guessed.

Everything is a dry run until ``--apply`` is passed. The counts printed by a dry
run are the counts a real run would change.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

import asyncpg
import structlog

from src.config import Settings
from src.database.connection import close_pool, create_pool

logger = structlog.get_logger(__name__)

# How far back (in message-id distance) an inferred transcription may sit from
# its audio. A transcription is posted seconds after the voice note, so in a
# quiet chat the gap is 1; a busy chat can interleave a few messages while
# Whisper runs. Beyond that the "nearest preceding voice message" stops being
# evidence and becomes a coin flip.
#
# Deliberately tight, because the candidate set is dirtier than it first looks:
# an "orphan" reply target is ANY bot message we do not store — `/help` output,
# admin sticker cards, spend warnings — not just a transcription. A generous
# window would happily attach one of those to whatever voice note happened to
# precede it, and the cost of a wrong link is attributing one person's words to
# another. Missing a real link is cheap; inventing one is not.
_DEFAULT_MAX_GAP = 3


@dataclass
class Counts:
    repaired: int = 0
    linked: int = 0
    ambiguous: int = 0
    no_candidate: int = 0
    already_linked: int = 0


async def repair_mislabelled_rows(pool: asyncpg.Pool, *, apply: bool) -> int:
    """Relabel self-replying 'transcription' rows back to their audio type.

    These are voice messages that the old save path mislabelled (see job 1 in
    the module docstring). ``reply_to_message_id = message_id`` is the tell: a
    genuine transcription row created by the new code points at a *different*
    message, and nothing else in the schema writes a row that replies to itself.
    """
    rows = await pool.fetch(
        """
        SELECT chat_id, message_id, content
        FROM chat_messages
        WHERE message_type = 'transcription'
          AND transcribed_message_id IS NULL
          AND reply_to_message_id = message_id
        ORDER BY chat_id, message_id
        """
    )
    for row in rows:
        logger.info(
            "mislabelled_transcription_row",
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            has_content=row["content"] is not None,
        )
    if rows and apply:
        # 'voice' rather than 'video_note': the two are indistinguishable at
        # this point (the old row kept no media type), and 'voice' is both the
        # overwhelmingly common case and harmless if wrong — message_type is
        # only used to keep bookkeeping rows out of the prompt, and either
        # value achieves that.
        await pool.execute(
            """
            UPDATE chat_messages
               SET message_type = 'voice'
             WHERE message_type = 'transcription'
               AND transcribed_message_id IS NULL
               AND reply_to_message_id = message_id
            """
        )
    return len(rows)


async def infer_links(pool: asyncpg.Pool, *, apply: bool, max_gap: int, counts: Counts) -> None:
    """Create linking rows for transcriptions users demonstrably replied to.

    Heuristic and opt-in. See job 2 in the module docstring for what this does
    and does not prove.
    """
    # Transcription message ids we can see only as the target of somebody's
    # reply, and for which we hold no row of our own.
    orphans = await pool.fetch(
        """
        SELECT DISTINCT u.chat_id, u.reply_to_message_id AS target_id
        FROM chat_messages u
        WHERE u.reply_to_message_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM chat_messages t
               WHERE t.chat_id = u.chat_id
                 AND t.message_id = u.reply_to_message_id
          )
        ORDER BY u.chat_id, target_id
        """
    )

    for orphan in orphans:
        chat_id = orphan["chat_id"]
        target_id = orphan["target_id"]

        candidates = await pool.fetch(
            """
            SELECT message_id
            FROM chat_messages src
            WHERE src.chat_id = $1
              AND src.message_type IN ('voice', 'video_note')
              -- A transcript on the row is what proves the bot actually
              -- transcribed this one. A voice note that failed Whisper, or
              -- arrived while the module was off, never produced a
              -- transcription message and so cannot be the target's source.
              AND src.content IS NOT NULL
              -- Already linked: something after migration 028 recorded a real
              -- transcription for this audio, so the orphan cannot also be one.
              -- Without this the script happily attaches an ordinary bot
              -- message (a spend warning, /help output -- none of which are
              -- stored) to a voice note that already has its transcription,
              -- and every later reply to that bot message then resolves as
              -- "a transcription by <speaker>": the bot stops answering it and
              -- the prompt is handed words that person never said in that
              -- context. That is precisely the forged state the column exists
              -- to make impossible, produced by our own maintenance script.
              AND NOT EXISTS (
                  SELECT 1 FROM chat_messages t2
                   WHERE t2.chat_id = src.chat_id
                     AND t2.transcribed_message_id = src.message_id
              )
              AND src.message_id < $2
              AND src.message_id >= $2 - $3
            ORDER BY src.message_id DESC
            """,
            chat_id,
            target_id,
            max_gap,
        )

        if not candidates:
            counts.no_candidate += 1
            logger.info("no_candidate_audio", chat_id=chat_id, target_id=target_id)
            continue
        if len(candidates) > 1:
            # Two voice notes inside the window means the reply target could
            # belong to either. Refuse rather than write a plausible lie: a
            # wrong link makes the bot attribute one person's words to another.
            counts.ambiguous += 1
            logger.warning(
                "ambiguous_audio_candidates",
                chat_id=chat_id,
                target_id=target_id,
                candidates=[c["message_id"] for c in candidates],
            )
            continue

        source_id = candidates[0]["message_id"]
        logger.info(
            "inferred_transcription_link",
            chat_id=chat_id,
            transcription_message_id=target_id,
            source_message_id=source_id,
            gap=target_id - source_id,
        )
        counts.linked += 1

        if apply:
            # created_at is copied from the source audio, not left to default
            # to NOW(). These rows are historical; stamping them with the
            # migration's own runtime would sort every backfilled row to the
            # top of `get_recent()`'s newest-first window and push real recent
            # messages out of the relevancy judge's context until enough new
            # traffic arrived to flush them.
            await pool.execute(
                """
                INSERT INTO chat_messages (
                    chat_id, message_id, message_type, is_bot_message,
                    reply_to_message_id, transcribed_message_id, created_at
                )
                SELECT $1, $2, 'transcription', true, $3, $3, src.created_at
                  FROM chat_messages src
                 WHERE src.chat_id = $1 AND src.message_id = $3
                ON CONFLICT (chat_id, message_id) DO NOTHING
                """,
                chat_id,
                target_id,
                source_id,
            )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes; without it the script only reports what it would do",
    )
    parser.add_argument(
        "--infer-links",
        action="store_true",
        help="also create links inferred from reply adjacency (heuristic, see module docstring)",
    )
    parser.add_argument(
        "--max-gap",
        type=int,
        default=_DEFAULT_MAX_GAP,
        help=f"how many message ids back to look for the audio (default {_DEFAULT_MAX_GAP})",
    )
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    pool = await create_pool(settings.database_url)
    counts = Counts()

    try:
        counts.already_linked = (
            await pool.fetchval(
                "SELECT count(*) FROM chat_messages WHERE transcribed_message_id IS NOT NULL"
            )
            or 0
        )
        counts.repaired = await repair_mislabelled_rows(pool, apply=args.apply)
        if args.infer_links:
            await infer_links(pool, apply=args.apply, max_gap=args.max_gap, counts=counts)
    finally:
        await close_pool(pool)

    mode = "APPLIED" if args.apply else "DRY RUN — nothing written"
    logger.info(
        "backfill_complete",
        mode=mode,
        already_linked=counts.already_linked,
        mislabelled_rows_repaired=counts.repaired,
        links_inferred=counts.linked,
        ambiguous_skipped=counts.ambiguous,
        no_candidate=counts.no_candidate,
        inference_enabled=args.infer_links,
    )
    if not args.infer_links:
        logger.info(
            "note_inference_disabled",
            detail=(
                "Transcriptions posted before migration 028 have no row of their own and "
                "cannot be linked exactly. Pass --infer-links to reconstruct the ones users "
                "replied to, from reply adjacency."
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
