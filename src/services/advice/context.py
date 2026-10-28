"""What the classifier sees for one comment: the series, its post, and the thread.

A port of the evaluated run's context assembly onto `chat_messages` rows. The
shape matters as much as the prompt: the measured agreement with the owner's
labels came from this exact framing, so the rules below are carried over, not
redesigned.

- **A series is one remark.** People split a thought across messages; the
  model reads a sender's consecutive messages (gaps up to `SERIES_GAP`, others
  may interleave) as one, and one verdict covers them all.
- **The post is found by walking replies** up from the series until a channel
  post appears. A comment that hangs off nothing gets no post.
- **The thread** is what the chat showed when the series ended: up to four
  ancestors in the reply chain (below the post), plus whatever others said
  while the series was being written. With neither a post nor a reply chain,
  the four messages before the series stand in for it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

SERIES_GAP_SECONDS = 180
_THREAD_ANCESTORS = 4
_THREAD_LINE_CHARS = 600
_COMMENT_CHARS = 3000

AUTHOR_LABEL = "автор блога"
_BOT_LABEL = "бот"
_FALLBACK_LABEL = "участник"


@dataclass(frozen=True)
class Line:
    """One `chat_messages` row, as much of it as the context needs."""

    message_id: int
    created_at: datetime
    content: str
    user_id: int | None = None
    first_name: str | None = None
    username: str | None = None
    reply_to: int | None = None
    sender_kind: str | None = None
    sender_chat_title: str | None = None
    is_bot: bool = False

    @property
    def body(self) -> str:
        return (self.content or "").strip()

    @property
    def is_post(self) -> bool:
        return self.sender_kind == "channel_post"

    @property
    def is_person(self) -> bool:
        """An ordinary account -- the only kind of sender the classifier judges."""
        return not self.is_bot and self.sender_kind is None


@dataclass(frozen=True)
class Context:
    series: list[Line]
    post: Line | None
    thread: str
    comment: str

    @property
    def message_ids(self) -> list[int]:
        return [x.message_id for x in self.series]


def who(line: Line) -> str:
    """How a sender is named to the model.

    The author speaks as the channel (posts) or as the anonymous group admin
    (her comments); both are her. A member posting as some other channel is
    named by that channel.
    """
    if line.sender_kind in ("channel_post", "anon_admin"):
        return AUTHOR_LABEL
    if line.sender_kind == "channel":
        return line.sender_chat_title or "канал"
    if line.is_bot:
        return _BOT_LABEL
    return line.first_name or line.username or _FALLBACK_LABEL


def _ts(line: Line) -> float:
    return line.created_at.timestamp()


def find_post(line: Line, by_id: dict[int, Line]) -> Line | None:
    cur: Line = line
    seen: set[int] = set()
    while cur.reply_to in by_id and cur.reply_to not in seen:
        seen.add(cur.reply_to)
        cur = by_id[cur.reply_to]
        if cur.is_post:
            return cur
    return None


def ancestors(line: Line, by_id: dict[int, Line], post: Line | None) -> list[Line]:
    chain: list[Line] = []
    cur: Line = line
    seen: set[int] = set()
    while cur.reply_to in by_id and cur.reply_to not in seen:
        seen.add(cur.reply_to)
        cur = by_id[cur.reply_to]
        if post is not None and cur.message_id == post.message_id:
            break
        chain.append(cur)
    return list(reversed(chain))[-_THREAD_ANCESTORS:]


def series(line: Line, timeline: list[Line]) -> list[Line]:
    """The sender's run around `line`: neighbours no more than SERIES_GAP apart."""
    mine = sorted(
        (x for x in timeline if x.user_id == line.user_id and x.is_person and x.body),
        key=_ts,
    )
    i = [x.message_id for x in mine].index(line.message_id)
    lo = hi = i
    while lo > 0 and _ts(mine[lo]) - _ts(mine[lo - 1]) <= SERIES_GAP_SECONDS:
        lo -= 1
    while hi + 1 < len(mine) and _ts(mine[hi + 1]) - _ts(mine[hi]) <= SERIES_GAP_SECONDS:
        hi += 1
    return mine[lo : hi + 1]


def build(line: Line, timeline: list[Line], by_id: dict[int, Line]) -> Context:
    """Assemble the context for `line`.

    `timeline` is the chat's recent rows in time order (series and "said
    meanwhile" come from it); `by_id` additionally holds the older rows the
    reply chains reach, posts included.
    """
    run = series(line, timeline)
    first, last = run[0], run[-1]
    post = find_post(first, by_id) or find_post(line, by_id)
    before = ancestors(first, by_id, post)
    if not before and post is None:
        idx = [x.message_id for x in timeline].index(first.message_id)
        before = [x for x in timeline[max(0, idx - _THREAD_ANCESTORS) : idx] if x.body]

    run_ids = {x.message_id for x in run}
    t0, t1 = _ts(first), _ts(last)
    inside = [
        x
        for x in timeline
        if t0 <= _ts(x) <= t1 and x.message_id not in run_ids and x.body and not x.is_post
    ]
    thread = _dedupe(sorted(before + inside, key=_ts))
    return Context(
        series=run,
        post=post,
        thread="\n".join(f"— {who(x)}: {x.body[:_THREAD_LINE_CHARS]}" for x in thread),
        comment="\n".join(x.body for x in run)[:_COMMENT_CHARS],
    )


def _dedupe(lines: Iterable[Line]) -> list[Line]:
    seen: set[int] = set()
    out: list[Line] = []
    for x in lines:
        if x.message_id not in seen:
            seen.add(x.message_id)
            out.append(x)
    return out
