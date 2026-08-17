"""Grammar of the manual Knowledge Base capture path (`/remember`), S2/KB-07..KB-09.

Pure functions, no I/O and no Telegram types: the handler decides *what text
was captured* (typed argument, replied-to message, manually-highlighted quote),
this module decides *what that text means* -- which part is the topic, which is
a deadline, what the fact's grouping key is, and what identity the row gets.

Two rules shape everything here:

**A degradation saves the fact.** Every path that cannot understand part of the
input still produces a storable fact and reports what it dropped
(`ParsedCapture.notes`). The plan's own words: each degradation is tested as
"save it as a permanent fact", never as a lost record. A parse this module
cannot complete must never cost the user their text.

**Nothing this module returns is trusted markup.** `topic` is user input that
later slices render into the model's prompt, so it is validated on the *write*
path (`normalize_topic`) rather than only escaped on the read path -- a
restriction that cannot be forgotten by a future renderer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from src.utils.display_tz import DISPLAY_TZ

# "До 5 сентября" has to mean the same instant as the date the bot prints next to
# a fact, so both come from one constant now (`src/utils/display_tz.py`). This
# alias is kept because it reads correctly at the call sites here — the timezone
# a deadline is *captured* in — and because it is the name the tests use.
CAPTURE_TZ = DISPLAY_TZ

# A topic is a grouping label, not prose: it goes into `chat_facts.topic`, is
# rendered as a section head in `/kb`, and reaches the model's prompt in a later
# slice. Letters (any script -- `#правила` is the plan's own example), digits,
# `_`, `-` and `:` (ADR-0003's documented `event:summer-meetup` shape). Anything
# else -- whitespace, `<`, `>`, `&`, quotes, slashes, control characters -- is
# refused rather than escaped, because a value that never enters the column
# cannot be mis-rendered by a surface that forgets to escape it.
_TOPIC_ALLOWED = re.compile(r"[\w\-:]{1,32}", re.UNICODE)
_TOPIC_PREFIX = re.compile(r"^#(\S+)\s*")

# Subject is a short grouping head, not the fact. Long enough to disambiguate
# two facts in one topic, short enough to stay a label.
_SUBJECT_MAX_CHARS = 60

# The deliberate `тема: значение` form. The colon MUST be followed by whitespace:
# that is what separates a person writing a label from a colon inside their own
# words (`22:00`, `1:1`, `https://…`), which used to be split and reassembled
# with a space inserted after the colon.
_SUBJECT_VALUE = re.compile(rf"^(?P<subject>[^:\n]{{1,{_SUBJECT_MAX_CHARS}}}):\s+(?P<value>\S.*)$")

_MONTHS_RU = {
    "января": 1, "январь": 1,
    "февраля": 2, "февраль": 2,
    "марта": 3, "март": 3,
    "апреля": 4, "апрель": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7,
    "августа": 8, "август": 8,
    "сентября": 9, "сентябрь": 9,
    "октября": 10, "октябрь": 10,
    "ноября": 11, "ноябрь": 11,
    "декабря": 12, "декабрь": 12,
}  # fmt: skip

_MONTHS_EN = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip

# Anchored at the very END of the input, and only ever `до`/`until` + a date
# shape. Both halves matter:
#
# * Anchoring: `/remember часы: работаем с 10 до 22` is valid today and must
#   keep meaning what it says. An unanchored `до <token>` rule reads `22` as
#   "the 22nd" and gives an opening-hours fact a silent two-week lifespan.
# * The date shapes: only forms with one unambiguous absolute answer. `до
#   пятницы`, `до конца месяца` and `до завтра` are refused *by name*, so the
#   user is told the deadline was not understood instead of having a guess
#   stored. ADR-0003's schema note makes the same call: absolute dates only.
#
# Up to FOUR tokens, because "5 сентября 2026 года" is four and is the *most*
# explicit thing a user can type. At two it was not recognised as a clause at
# all -- so adding the year for clarity produced a permanent fact with no
# warning, which is exactly the silence this design forbids.
#
# The cost of the wider window is the occasional false warning on a trailing
# phrase that was never a deadline ("работаем с 10 до 22 в этом месяце" draws
# "deadline not understood"). That is the right way to be wrong here: the fact is
# still saved with its text intact, whereas the narrow window's failure was
# silence on an explicit date.
_EXPIRY_CLAUSE = re.compile(
    r"(?:^|[\s,;])(?:до|until)\s+(?P<value>[^\s,;]+(?:\s+[^\s,;]+){0,3})\s*$",
    re.IGNORECASE | re.UNICODE,
)

# A trailing `до …` whose value is neither a date nor one of these is not
# treated as a deadline clause at all: `работаем с 10 до 22` keeps its words and
# draws no warning. These markers are the shapes where the user plainly *meant*
# a deadline and has to be told it was not stored -- silence there would leave
# them believing the fact expires when it does not.
#
# Searched anywhere in the clause value, NOT anchored at its first word: the
# qualifier usually comes first ("до следующей недели", "до конца этого месяца",
# "до next friday"), so an anchored match found neither a date nor a marker and
# fell through to silence -- the very outcome the marker list exists to prevent.
_RELATIVE_MARKERS = re.compile(
    r"(?:"
    r"завтра|послезавтра|вечер\w*|утр\w*|ноч\w*|полудн\w*|полноч\w*|полуноч\w*|"
    r"конц\w*|начал\w*|серед\w*|следующ\w*|ближайш\w*|"
    r"недел\w*|месяц\w*|год\w*|лет\w*|зим\w*|весн\w*|осен\w*|"
    r"понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресен\w*|"
    r"tomorrow|next|end|weekend|noon|midnight|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r")",
    re.IGNORECASE | re.UNICODE,
)

# "5-го сентября" / "5е сентября" / "5th September": an ordinal suffix on the day
# is a person writing an absolute date, so it is normalised and parsed rather
# than refused as vague.
_ORDINAL_SUFFIX = re.compile(r"(?<=\d)(?:-?(?:го|е|ое|ого|й|я)|st|nd|rd|th)\b", re.IGNORECASE)

# "This was meant as a date, and we could not read it." Deliberately narrower
# than "contains a digit": a bare number must stay silent, because `работаем с 10
# до 22` is opening hours and not a deadline. A digit pair joined by a date
# separator, or a day followed by a word, is nobody's opening hours.
#
# Without this, a value that is neither parseable NOR a known relative marker
# fell through every branch: `до 31.02` (a typo), `до 05.09 включительно` and
# `до 05.09!` were saved with no deadline and no warning -- the silence KB-09
# exists to forbid, on inputs far more plausible than the ones it caught.
_DATE_SHAPED = re.compile(
    r"(?:\d{1,2}\s*[./\-]\s*\d{1,2}|\d{4}\s*-\s*\d{1,2}|\d{1,2}\s+[^\W\d_]{3,})", re.UNICODE
)
_DATE_NUMERIC = re.compile(r"^(\d{1,2})[.](\d{1,2})(?:[.](\d{2}|\d{4}))?$")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
# The optional third group is the year, with an optional "года"/"year" tail:
# "5 сентября", "5 сентября 2026", "5 сентября 2026 года".
_DATE_DAY_MONTH = re.compile(
    r"^(\d{1,2})\s+([^\W\d_]+)(?:\s+(\d{4}))?(?:\s+(?:года|год|year))?$", re.UNICODE
)


class CaptureNote(StrEnum):
    """What the parser could not honour. Each maps to one line of user copy.

    A note never blocks the write -- it explains what the stored fact does
    *not* carry, next to the confirmation that it was stored.
    """

    TOPIC_REJECTED = "topic_rejected"
    EXPIRY_UNPARSED = "expiry_unparsed"
    EXPIRY_IN_PAST = "expiry_in_past"
    QUOTE_CAPTURED = "quote_captured"
    LONG_FACT = "long_fact"


@dataclass(frozen=True)
class ParsedCapture:
    """One storable fact, plus what the parse had to drop to get there."""

    subject: str
    value: str
    fact_text: str
    topic: str | None
    expires_at: datetime | None
    notes: tuple[CaptureNote, ...]
    rejected_topic: str | None = None
    unparsed_expiry: str | None = None


@dataclass(frozen=True)
class Directives:
    """`args` split into its three parts, before any of them is validated.

    `expiry_clause` and `topic_prefix` are the matched text verbatim
    (`" до пятницы"`, `"#правила "`), kept so a directive that turns out not to
    be one can be put back into the fact exactly as it was typed -- including its
    own `до`/`until` spelling and its own spacing. Both are needed for the same
    reason: this split happens *before* validation, so it cannot yet know which
    of the two things it peeled off will survive it.
    """

    body: str
    topic_raw: str | None
    expiry_raw: str | None
    expiry_clause: str = ""
    topic_prefix: str = ""


def collapse_whitespace(text: str) -> str:
    """Fold every run of whitespace -- newlines included -- into one space.

    Applied on the **write** path, which is what makes it load-bearing rather
    than cosmetic. `_kb_section` renders one fact as one `- ` bullet; a stored
    newline followed by `- ` renders as a second bullet, i.e. user text that
    reads to the model as another curated fact of the chat. KB-08 makes
    multi-line captures ordinary (a quoted message is verbatim text), so the
    shape has to be closed where the text enters, not only where it is drawn.
    The renderer collapses too -- two independent guards, because rows written
    before this slice already contain newlines.
    """
    return " ".join(text.split())


def normalize_topic(raw: str) -> str | None:
    """Validate a `#topic` token. Returns the canonical form, or None to refuse.

    Lowercased so `#Правила` and `#правила` are one topic rather than two
    sections in `/kb`. Refusal is deliberate over sanitisation: the caller
    stores the fact without a topic and says so, which is recoverable, whereas
    a silently-rewritten topic is a fact filed somewhere the user did not ask
    for and cannot guess.
    """
    candidate = raw.strip().lstrip("#").strip()
    if not candidate:
        return None
    if not _TOPIC_ALLOWED.fullmatch(candidate):
        return None
    lowered = candidate.lower()
    # A label made only of separators is not a label.
    if not any(ch.isalnum() for ch in lowered):
        return None
    return lowered


def split_directives(args: str) -> Directives:
    """Peel a leading `#topic` and a trailing `до <date>` clause off the args.

    Both are anchored (start / end) rather than searched for anywhere in the
    text: a fact may legitimately contain a hashtag (`#кофе`) or the word `до`
    (`с 10 до 22`), and an unanchored strip would eat content out of the middle
    of what someone asked to save.
    """
    rest = args.strip()

    topic_raw: str | None = None
    topic_prefix = ""
    match = _TOPIC_PREFIX.match(rest)
    if match:
        topic_raw = match.group(1)
        topic_prefix = match.group(0)
        rest = rest[match.end() :]

    expiry_raw: str | None = None
    expiry_clause = ""
    clause = _EXPIRY_CLAUSE.search(rest)
    if clause:
        expiry_raw = clause.group("value").strip()
        # Normalised to a single leading space, which is the invariant the
        # docstring states and the caller relies on. `(?:^|[\s,;])` usually
        # matches a separator that the slice then carries -- but when the `^`
        # alternative wins (the args are *only* the clause, i.e. the documented
        # reply form `/remember до пятницы`) `clause.start()` is 0 and the slice
        # had no separator at all. Re-joining then glued the clause onto the
        # captured text: `Созвон в 18:00до пятницы`, stored and shown that way.
        expiry_clause = " " + rest[clause.start() :].strip()
        rest = rest[: clause.start()]

    return Directives(
        body=rest.strip(),
        topic_raw=topic_raw,
        expiry_raw=expiry_raw,
        expiry_clause=expiry_clause,
        topic_prefix=topic_prefix,
    )


def _resolve_year(day: int, month: int, today: date, explicit: int | None) -> int:
    """The year the user meant: theirs if given, else the nearest future one.

    A plain `+1` was not enough. `29 февраля` asked for in March 2026 resolved to
    2027, which does not exist, so the date failed to construct and the deadline
    was dropped with no warning -- while the correct nearest-future answer,
    2028-02-29, is four years out. Searching forward for the first year where the
    day/month is both **valid** and not in the past answers 29 February and every
    ordinary date with the same rule.
    """
    if explicit is not None:
        return explicit + 2000 if explicit < 100 else explicit
    for year in range(today.year, today.year + 9):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue  # 29 February in a non-leap year
        if candidate >= today:
            return year
    return today.year


def parse_expiry_date(raw: str, *, today: date) -> date | None:
    """Parse the date out of a `до …` clause. None means "not understood".

    Accepts `05.09`, `05.09.2026`, `5 сентября`, `5 september`, `2026-09-05`.
    Everything relative (`до пятницы`, `до конца месяца`, `до завтра`) and every
    bare number returns None on purpose: those have no single absolute answer,
    and a fact whose deadline was guessed disappears on a date nobody chose.
    """
    text = _ORDINAL_SUFFIX.sub("", raw.strip().lower().rstrip(".,;")).strip()

    iso = _DATE_ISO.match(text)
    if iso:
        year, month, day = (int(g) for g in iso.groups())
        return _safe_date(year, month, day)

    numeric = _DATE_NUMERIC.match(text)
    if numeric:
        day, month = int(numeric.group(1)), int(numeric.group(2))
        raw_year = numeric.group(3)
        year = _resolve_year(day, month, today, int(raw_year) if raw_year else None)
        return _safe_date(year, month, day)

    day_month = _DATE_DAY_MONTH.match(text)
    if day_month:
        day = int(day_month.group(1))
        word = day_month.group(2)
        named_month = _MONTHS_RU.get(word) or _MONTHS_EN.get(word)
        if named_month is None:
            return None
        explicit_year = int(day_month.group(3)) if day_month.group(3) else None
        return _safe_date(_resolve_year(day, named_month, today, explicit_year), named_month, day)

    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        # 31 февраля / month 13. Refused like any other unparseable date.
        return None


def end_of_day(day: date) -> datetime:
    """The last instant of `day` in the bot's display timezone.

    `до 5 сентября` is **inclusive**: the fact is live all through the 5th and
    gone on the 6th. Timezone-aware on purpose -- asyncpg encodes a naive
    datetime through `astimezone()`, i.e. in whatever timezone the *process*
    happens to run in, so a naive value means "expires at 04:00 local in
    production and at midnight on the developer's machine", and any test
    pinning the boundary passes or fails according to the runner's TZ.
    """
    return datetime(day.year, day.month, day.day, 23, 59, 59, 999999, tzinfo=CAPTURE_TZ)


def derive_subject(fact_text: str, topic: str | None) -> str:
    """A short grouping head for a fact that has no explicit `subject: value` split.

    `chat_facts.subject` is NOT NULL and is part of the active-fact key, so a
    captured quote still needs one. It is a *label*, not the fact: every surface
    renders `fact_text` (S2 changed `/kb` to do so precisely because a derived
    head next to the text it was derived from reads as a duplicated sentence).
    """
    head = collapse_whitespace(fact_text)
    if len(head) > _SUBJECT_MAX_CHARS:
        cut = head[:_SUBJECT_MAX_CHARS]
        space = cut.rfind(" ")
        head = (cut[:space] if space > _SUBJECT_MAX_CHARS // 2 else cut).rstrip()
    head = head.strip(" -–—:;,.·•")
    if head:
        return head
    # Text made entirely of punctuation/emoji: fall back to the topic, then to
    # a constant. Never empty -- the column is NOT NULL.
    return topic or "факт"


def split_subject_value(body: str) -> tuple[str, str] | None:
    """The classic `/remember <subject>: <value>` form, or None if it isn't one.

    Three limits, and each one exists because of a shape that reaches this
    function in ordinary use:

    * **Single-line only.** A pasted rules block ("правила: 1. …") would
      otherwise be reinterpreted as one key/value pair.
    * **The colon must be followed by whitespace.** This is what separates a
      person writing a label from a colon that belongs to the words themselves.
      Without it, `магазин работает до 22:00` split into subject `магазин
      работает до 22` / value `00`, and `схема проезда https://example.com/map`
      split at the scheme -- so a URL, a clock time and a ratio were all
      mangled. A human writing a label writes `тема: значение`.
    * **The subject half is capped**, so a long sentence that happens to contain
      a colon does not become a grouping key.
    """
    if "\n" in body:
        return None
    match = _SUBJECT_VALUE.match(body)
    if match is None:
        return None
    subject, value = match.group("subject").strip(), match.group("value").strip()
    if not subject or not value:
        return None
    return subject, value


def build_capture(
    *,
    body: str,
    topic_raw: str | None,
    expiry_raw: str | None,
    today: date,
    expiry_clause: str = "",
    topic_prefix: str = "",
    from_quote: bool = False,
    long_fact_chars: int | None = None,
) -> ParsedCapture:
    """Assemble the fact to store. Never raises; never returns "nothing".

    `body` must be non-empty -- the caller decides what "no content" means
    (there is a distinct refusal for it) and this function is only reached once
    there is something to save.
    """
    notes: list[CaptureNote] = []

    topic: str | None = None
    rejected_topic: str | None = None
    restored_topic_prefix = ""
    if topic_raw is not None:
        topic = normalize_topic(topic_raw)
        if topic is None:
            rejected_topic = topic_raw
            notes.append(CaptureNote.TOPIC_REJECTED)
            # Put the token back, exactly like an unparsed `до …` clause. It was
            # peeled off as a directive and then refused, so those characters are
            # ordinary text the user asked to save -- and `#кофе` in a fact about
            # coffee is a perfectly good sentence. Deleting them made a refused
            # topic the one degradation path that *did* cost the user content.
            restored_topic_prefix = topic_prefix

    expires_at: datetime | None = None
    unparsed_expiry: str | None = None
    if expiry_raw is not None:
        parsed = parse_expiry_date(expiry_raw, today=today)
        if parsed is None and " " in expiry_raw:
            # "05.09 включительно" / "05.09 года": the date is the first token and
            # the rest is a qualifier that adds nothing (the deadline is already
            # inclusive). Retrying on the first token turns a silent miss into the
            # deadline the user asked for.
            parsed = parse_expiry_date(expiry_raw.split()[0], today=today)
        if parsed is not None and parsed < today:
            # Storing this would hide the fact from every read path the moment
            # it is written -- a "successful" save the user can never see.
            unparsed_expiry = expiry_raw
            notes.append(CaptureNote.EXPIRY_IN_PAST)
        elif parsed is not None:
            expires_at = end_of_day(parsed)
        elif _RELATIVE_MARKERS.search(expiry_raw) or _DATE_SHAPED.search(expiry_raw):
            # They meant a deadline and we will not guess it. Say so. Two ways to
            # qualify: a relative word ("следующей недели"), or a date shape we
            # could not turn into a date ("31.02"). Both are someone naming a
            # deadline; only a bare number is left silent, because that is opening
            # hours rather than a date.
            unparsed_expiry = expiry_raw
            notes.append(CaptureNote.EXPIRY_UNPARSED)

    # A clause that did not become a deadline stays in the text, verbatim.
    # Dropping it would delete words the user asked to save on the strength of a
    # parse that failed -- and for `работаем с 10 до 22` there was never a
    # deadline to find, so it draws no warning either.
    # The explicit space is REDUNDANT while `split_directives` keeps its
    # leading-space invariant (which `test_expiry_clause_always_carries_exactly_
    # one_leading_space` pins), and is kept anyway: the failure it guards against
    # silently rewrote stored user text, and `collapse_whitespace` below folds the
    # resulting double space at no cost. Deliberately unfalsifiable-by-itself --
    # a control that removes only this line leaves the suite green, and that is
    # the correct result, not a missing test.
    text = body if expires_at is not None else f"{body} {expiry_clause}"
    text = f"{restored_topic_prefix}{text}"

    text = collapse_whitespace(text)

    # `fact_text` is ALWAYS the user's text verbatim (whitespace-collapsed) --
    # never reassembled from the parts. Rebuilding it as f"{subject}: {value}"
    # rewrote what someone asked to save: a missing space after their colon was
    # inserted, so "работает до 22:00" came back as "работает до 22: 00". This is
    # the column the model reads and `/kb` shows, so a rewrite here is a rewrite
    # everywhere. `subject`/`value` are labels derived FROM the text, not the
    # source of it.
    fact_text = text
    pair = split_subject_value(text)
    if pair is not None:
        subject, value = pair
    else:
        subject = derive_subject(text, topic)
        value = text

    if from_quote:
        notes.append(CaptureNote.QUOTE_CAPTURED)
    if long_fact_chars is not None and len(fact_text) > long_fact_chars:
        notes.append(CaptureNote.LONG_FACT)

    return ParsedCapture(
        subject=subject,
        value=value,
        fact_text=fact_text,
        topic=topic,
        expires_at=expires_at,
        notes=tuple(notes),
        rejected_topic=rejected_topic,
        unparsed_expiry=unparsed_expiry,
    )


def fact_predicate(command_message_id: int) -> str:
    """Append-only identity for a manually-captured fact (KB-07 / D-3).

    Phase 1 passed the constant `"факт"`, which collapsed the designed key
    `(chat_id, subject, predicate)` to `(chat_id, subject)`: a second
    `/remember` about the same subject hit `idx_chat_facts_active_key` and
    **superseded** the first one, so "add another detail" silently deleted a
    fact. Deriving the predicate from the command's own message id makes the
    key unique per capture -- the second write is an INSERT, both facts live.

    The message id (not a timestamp, counter or uuid) because it is the one
    value that is *stable for a given capture*: a redelivered update carries
    the same id, collides on the same unique index, and is answered "already
    saved" instead of writing a duplicate. It also points a maintainer straight
    at the message that created the row.

    Never rendered. `/kb` used to print `predicate` verbatim in its DM view;
    S2 stopped, because fact identity is not something to show a reader.
    """
    return f"m{command_message_id}"
