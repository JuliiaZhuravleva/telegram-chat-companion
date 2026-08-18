"""Conversation-session chunking of chat history (S4).

The RAG index today stores `"Q: <сообщение>\nA: <ответ бота>"` pairs, written
only on turns where the bot answered -- 4-8% of a live chat's history, and not
a random 4-8%: it is exactly the part where people talked *to the bot*. This
module produces the replacement index unit: a slice of the conversation itself,
rendered the way the group actually wrote it.

Why sessions and not fixed windows. A chat is bursty -- forty messages in ten
minutes, then silence until tomorrow. A pause is the group's own paragraph
break, and event-boundary segmentation measurably helps multi-hop recall
(2602.01313). The parameters below were measured in the sibling archive
projects and are deliberately *not* re-derived here.

Why verbatim `Имя (ЧЧ:ММ): текст` lines. GroupMemBench's core finding is that
ingestion pipelines lose to a plain BM25 baseline precisely when they erase
speaker and lexical structure. Keeping names, times and the group's own
vocabulary in the text is what makes both retrieval legs -- FTS and vector --
able to find it later.

**A chunk is a rendering, not an archive.** `chat_messages` remains the
verbatim record; the text stored here is normalised for the two consumers it
has (an embedding model and, from S5, a prompt):

- every user-controlled field goes through `sanitize_history_field`, the same
  rule the live history block uses, so one message can never forge another's
  line;
- one message is exactly one line (newlines collapse to spaces), which is what
  makes the chunk boundaries, the overlap and the line format well-defined;
- a single message longer than `HARD_MAX_CHARS` is truncated, so that one
  4000-character rant cannot produce a chunk the embedding API rejects -- a
  row that fails to embed is invisible to retrieval forever, which is worse
  than a truncated one.

Residual risk, accepted and documented rather than fixed: the line format is
`Имя (ЧЧ:ММ): текст`, so a message whose *body* contains something shaped like
`Аня (12:00): я согласна` reads as a second speaker inside one line. This is
strictly weaker than a forged newline (which the sanitizer removes) and the
chunk arrives in the prompt framed as a possibly-irrelevant fragment. Adding
`[uid:N]` prefixes would close it, and costs more than it saves: the ids are
~18 characters of digits per ~65-character message, i.e. a quarter of the
index would be semantically empty tokens -- the same class of embedding noise
R0 was written to remove.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from src.services.rag.models import Chunk, SourceMessage
from src.services.text.prompt_sanitizer import sanitize_history_field
from src.utils.display_tz import DISPLAY_TZ

# --- Parameters (measured in the sibling archive projects; do not re-derive) ---

SESSION_PAUSE = timedelta(hours=3)
TARGET_CHARS = 1200
HARD_MAX_CHARS = 2600
MAX_MESSAGES = 80
OVERLAP_MESSAGES = 2
OVERLAP_MAX_CHARS = 400

# The bot renders as `Bot` in the live history block (`prompt_builder.
# _format_message`). Same token here on purpose: at S5 both land in one prompt,
# and two labels for one speaker is a distinction the model has to spend
# attention resolving.
BOT_SPEAKER = "Bot"
ANONYMOUS_SPEAKER = "Аноним"

_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def source_messages(rows: Iterable[Any]) -> list[SourceMessage]:
    """Convert `chat_messages` rows into chunker input, dropping what cannot
    be chunked.

    Three exclusions, all deliberate and all in one place so that the reason a
    row is missing from the index is answerable:

    - **no text** -- stickers, photos and empty rows carry nothing for either
      retrieval leg. They still happened, but a line reading `Аня (12:00):`
      contributes noise to the embedding and nothing to FTS.
    - **no `created_at`** -- the column is nullable (migration 002 declares no
      `NOT NULL`), and without a timestamp a message can neither be placed in
      a session nor rendered with a time. Dropping it here is visible in the
      indexer's counters; carrying it would silently corrupt session
      boundaries for every message around it.
    - **transcription bookkeeping rows** (migration 028) -- content-free by
      design; the transcript itself lives on the voice message's own row, so
      including these would render an empty `Bot (12:00):` line.
    """
    out: list[SourceMessage] = []
    for row in rows:
        if row["message_type"] == "transcription":
            continue
        created_at = row["created_at"]
        if created_at is None:
            continue
        text = (row["content"] or "").strip()
        if not text:
            continue
        out.append(
            SourceMessage(
                message_id=row["message_id"],
                created_at=created_at,
                text=text,
                user_id=row["user_id"],
                name=row["first_name"] or row["username"],
                is_bot=bool(row["is_bot_message"]),
            )
        )
    return out


def split_sessions(messages: Sequence[SourceMessage]) -> list[list[SourceMessage]]:
    """Group messages into conversation sessions on `SESSION_PAUSE` gaps.

    Input arrives in `message_id` order -- Telegram's own send order, and the
    axis the indexer's watermark advances along. The timestamps are therefore
    *mostly* ascending but not guaranteed to be: 1.8% of adjacent pairs in
    production disagree, mostly rows written by the n8n-era import.

    So the gap is measured against the latest moment seen so far, not against
    the previous message. With a bare `previous`, one row carrying a stale
    timestamp makes the *next* message look like it arrived hours later and
    splits a session in the middle of a live conversation.
    """
    sessions: list[list[SourceMessage]] = []
    current: list[SourceMessage] = []
    latest: datetime | None = None
    for message in messages:
        if latest is not None and message.created_at - latest > SESSION_PAUSE:
            sessions.append(current)
            current = []
            latest = None
        current.append(message)
        latest = message.created_at if latest is None else max(latest, message.created_at)
    if current:
        sessions.append(current)
    return sessions


def build_chunks(
    messages: Sequence[SourceMessage],
    *,
    chat_id: int,
    thread_id: int | None,
    chat_title: str | None,
) -> list[Chunk]:
    """Chunk one `(chat_id, thread_id)` message run, oldest first."""
    chunks: list[Chunk] = []
    for session in split_sessions(messages):
        chunks.extend(
            _pack_session(session, chat_id=chat_id, thread_id=thread_id, chat_title=chat_title)
        )
    return chunks


def _pack_session(
    session: Sequence[SourceMessage],
    *,
    chat_id: int,
    thread_id: int | None,
    chat_title: str | None,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    buffer: list[tuple[SourceMessage, str]] = []
    size = 0
    part = 0

    # Every iteration ends with an append, so at the top of one a non-empty
    # buffer always holds at least one message that is not carried-over
    # overlap. That is what makes the two branches below sufficient: a chunk
    # can never be emitted with nothing but overlap in it. The invariant is
    # pinned by `test_every_chunk_adds_something_new`.
    for message in session:
        line = _render_line(message)
        if buffer and _should_close(size, len(buffer), len(line)):
            chunks.append(
                _make_chunk(
                    buffer, part, chat_id=chat_id, thread_id=thread_id, chat_title=chat_title
                )
            )
            part += 1
            buffer = _overlap_tail(buffer)
            size = _buffer_size(buffer)

        if buffer and size + 1 + len(line) > HARD_MAX_CHARS:
            # Carried-over overlap with no room left for the message that
            # follows it. Two things go wrong if the overlap simply stays:
            # the chunk overflows `HARD_MAX_CHARS` (the check above ran
            # against the *pre-flush* buffer), and, when the message after it
            # closes the chunk too, the result is a chunk whose every line is
            # already in the previous one. The overlap is a convenience, not
            # data -- those messages are stored either way -- so it is what
            # gets dropped.
            buffer = []
            size = 0

        size += (1 if buffer else 0) + len(line)
        buffer.append((message, line))

    if buffer:
        chunks.append(
            _make_chunk(buffer, part, chat_id=chat_id, thread_id=thread_id, chat_title=chat_title)
        )
    return chunks


def _should_close(size: int, messages: int, next_line: int) -> bool:
    """Whether the buffer must be closed before the next line joins it."""
    return size >= TARGET_CHARS or messages >= MAX_MESSAGES or size + 1 + next_line > HARD_MAX_CHARS


def _buffer_size(buffer: Sequence[tuple[SourceMessage, str]]) -> int:
    if not buffer:
        return 0
    return sum(len(line) for _, line in buffer) + len(buffer) - 1


def _overlap_tail(
    buffer: Sequence[tuple[SourceMessage, str]],
) -> list[tuple[SourceMessage, str]]:
    """The last messages of a closed chunk, repeated at the head of the next.

    Two messages at most and `OVERLAP_MAX_CHARS` at most, taken from the end
    backwards: the point is to keep the seam readable ("...и что решили?" /
    "решили в субботу"), so contiguity with the boundary is what matters. A
    long final message therefore yields no overlap at all rather than an
    overlap taken from further back.
    """
    tail: list[tuple[SourceMessage, str]] = []
    total = 0
    for entry in reversed(buffer[-OVERLAP_MESSAGES:]):
        if total + len(entry[1]) > OVERLAP_MAX_CHARS:
            break
        tail.insert(0, entry)
        total += len(entry[1])
    return tail


def _make_chunk(
    buffer: Sequence[tuple[SourceMessage, str]],
    part: int,
    *,
    chat_id: int,
    thread_id: int | None,
    chat_title: str | None,
) -> Chunk:
    messages = [message for message, _ in buffer]
    header = _render_header(chat_title, messages[0].created_at, messages[-1].created_at)
    body = "\n".join(line for _, line in buffer)
    senders = tuple(sorted({m.user_id for m in messages if m.user_id is not None and not m.is_bot}))
    ids = [m.message_id for m in messages]
    return Chunk(
        chat_id=chat_id,
        thread_id=thread_id,
        # min/max rather than first/last: the indexer orders by time, and a
        # message whose id does not follow its timestamp (a backfilled import,
        # a chat migration) would otherwise produce a range running backwards
        # -- which the watermark reads as "already indexed up to here".
        msg_from=min(ids),
        msg_to=max(ids),
        part=part,
        content=f"{header}\n{body}",
        senders=senders,
        msg_count=len(messages),
        started_at=min(m.created_at for m in messages),
        ended_at=max(m.created_at for m in messages),
    )


def _render_header(chat_title: str | None, started_at: datetime, ended_at: datetime) -> str:
    """`Чат «Название», 18 августа 2026` -- the chunk's own dateline.

    The date is spelled out rather than numeric because it has to survive both
    retrieval legs: `августа` is a lexeme the FTS leg can match against "что
    было в августе", `18.08.2026` is not.

    No topic name: we store `message_thread_id` but never the topic's title,
    and a bare `тема #4231` is five digits of noise in every chunk of a forum
    chat. The slot is here for when the title becomes available.
    """
    title = (chat_title or "").strip()
    prefix = f"Чат «{sanitize_history_field(title)}»" if title else "Чат"
    start = _format_date(started_at)
    end = _format_date(ended_at)
    when = start if start == end else f"{start} — {end}"
    return f"{prefix}, {when}"


def _format_date(moment: datetime) -> str:
    local = _to_display_tz(moment)
    return f"{local.day} {_MONTHS_GENITIVE[local.month - 1]} {local.year}"


def _render_line(message: SourceMessage) -> str:
    """`Имя (ЧЧ:ММ): текст`, truncated so one message can never overflow a
    chunk.

    The truncation budget is the whole `HARD_MAX_CHARS`, which keeps the
    invariant the packer relies on: any single line fits in an empty chunk, so
    a chunk body never exceeds `HARD_MAX_CHARS` and the embedding API never
    sees an oversized input.
    """
    speaker = _speaker(message)
    local = _to_display_tz(message.created_at)
    prefix = f"{speaker} ({local:%H:%M}): "
    text = sanitize_history_field(message.text)
    allowed = HARD_MAX_CHARS - len(prefix)
    if len(text) > allowed:
        text = text[: max(allowed - 1, 0)].rstrip() + "…"
    return f"{prefix}{text}"


def _speaker(message: SourceMessage) -> str:
    if message.is_bot:
        return BOT_SPEAKER
    name = (message.name or "").strip()
    if not name:
        return ANONYMOUS_SPEAKER
    return sanitize_history_field(name)


def _to_display_tz(moment: datetime) -> datetime:
    """Naive timestamps are read as UTC -- asyncpg returns aware ones for
    `TIMESTAMPTZ`, so this only bites hand-built test data and would otherwise
    shift a chunk's dateline by four hours."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC).astimezone(DISPLAY_TZ)
    return moment.astimezone(DISPLAY_TZ)
