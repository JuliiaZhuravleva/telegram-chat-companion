r"""Query hygiene: what gets embedded for retrieval, as opposed to what was said.

R0 of the RAG revision (TD-092). The retrieval embedding used to be computed
from the raw message, and every addressed message opens with ``бот``/``bot``.
That token is semantically loud and it is *about the bot*, so it dragged the
query toward the bot's own self-referential memories. Two indexes were probed
on live data, and the rows are kept apart here because their similarities are
not comparable -- reading a gap across the two is how these figures get
garbled:

    | index | probe                          | addressed | address removed |
    |-------|--------------------------------|-----------|-----------------|
    | RAG   | a real question                |     0.675 |           0.821 |
    | KB    | a real question                |     0.706 |           0.719 |
    | KB    | a question it cannot answer    |     0.640 |           0.524 |

Hits are pushed down and misses are pushed up, which is what makes the effect
worse than a constant offset. Taking the two KB rows, the only pair measured
against one index, the signal/noise gap goes from **0.066** (0.706 - 0.640) to
**0.195** (0.719 - 0.524). On the RAG side the hit alone moves 0.146, and the
0.7 floor is what turned that into silence: a question with a 0.821 match in
the index returned nothing at all.

(Query text withheld here on purpose: this repository is public and those are
real messages. The probes themselves are recorded with TD-092.)

**Only an address at the head is removed.** The distinction is the whole
design, and it comes from the corpus rather than from intuition: of 523
production messages that fire the trigger, the majority carry ``бот`` in the
middle of a sentence -- the shape of "мне нравится этот бот", "кажется, бот
сегодня молчит", "у бота странная манера отвечать". There the word *is* the
content, and dropping it would silently ask a different question. A vocative at
the head ("бот, ...", or without any comma at all, "Бот что мы решили месяц
назад?") carries no topic whatsoever.

Deliberately NOT stripped, each for a measured reason:

* **Trailing "..., бот".** Tried and rejected against the corpus: a first draft
  removed it and turned sentences of the shape "ну и зануда ты бот" into "ну и
  зануда ты", i.e. it deleted the predicate. Trailing position is a predicate
  far more often than a vocative, and it is 25 of 523 messages -- nowhere near
  worth that false-positive rate.
* **A leading @handle.** The plan for R0 called for this too; the corpus
  refused it. Of 667 production messages that open with a handle, **635 address
  a person, not a bot** -- so peeling the head would delete the addressee from
  95% of them to clean up the other 5%. Doing it correctly needs the bot's own
  username, which is not plumbed to this layer today; it is deferred to TD-088,
  where that identity has to arrive anyway.
* **Inflected or glued forms** (``бота``, ``боты``, ``ботификация``,
  ``бот-переводчик``). Matching is whole-word and the address must be delimited
  by whitespace or the end of the message, so a compound keeps its head. Note
  this is *stricter* than ``should_respond``, whose ``(?:^|\s)<trigger>`` has no
  closing boundary and therefore fires on "ботификация"; such a message is
  still answered, it just keeps its text. That includes a genuine plural
  vocative ("боты, что думаете?") -- recognising it would need morphology, and
  cutting words by prefix to get there is the larger error.

Accepted collateral, stated rather than hidden: a leading trigger in subject
position is stripped too ("бот сломался" -> "сломался"), because nothing in the
text distinguishes it from a vocative. Those messages are statements rather
than questions, so retrieval quality on them is worth little; the alternative
(demanding a comma) loses the comma-less "Бот что мы решили месяц назад?",
which is exactly the memory-seeking shape the change exists for. Measured on
production with the rule as it stands: of 523 trigger-matching messages, 209
change and 7 are the bare word "Бот" and fall back to their own text by the
guard below. The corpus also chose the punctuation class -- what follows a
leading trigger is a comma (145) or a space (57) and essentially nothing else,
so anything wider than that would be invention rather than coverage.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# How many leading addresses may be peeled. "бот, бот, ты тут?" needs two; the
# bound exists so a message built entirely of addresses cannot turn the loop
# into a scan of its own length.
_MAX_STRIPS = 4

# A query must still say something. Punctuation and emoji do not count: "Бот?"
# peeling to "?" is the same defect as peeling to "" -- a string that embeds to
# noise and then gets recorded in retrieval_log as the question that was asked.
_HAS_WORD = re.compile(r"\w")


def _leading_trigger_pattern(trigger_words: Sequence[str]) -> re.Pattern[str] | None:
    r"""One alternation matching an address at the head of a string.

    ``trigger_words`` is chat-configurable (``chat_settings.trigger_words``,
    editable from the admin panel), so it is untrusted input to a regex:
    every alternative is ``re.escape``d, and blank entries -- which would
    otherwise compile to an alternative matching the empty string, i.e. a
    strip at every position -- are dropped.

    Longest-first ordering matters when one trigger is a prefix of another
    ("бот" and "бот привет"): Python's alternation is first-match, not
    longest-match, so without the sort the shorter one would win and leave a
    stray word behind.

    The boundaries are lookarounds rather than ``\b`` on purpose: ``\b``
    assumes the trigger starts and ends with a word character, and a chat is
    free to configure a handle such as "@some_bot". ``(?<!\w)`` is satisfied at
    the start of a string regardless of what follows.

    The tail is the part that took two attempts. An address has to be
    *delimited*: closing punctuation is allowed, but the run must then reach
    whitespace or the end of the message. Accepting a bare punctuation run
    instead -- the first version -- ate the hyphen of a compound and turned
    "бот-переводчик не работает" into a question about a translator, "Бот-то
    умный какой" into a fragment, and "Bot's memory" into "'s memory". The same
    laxity left orphans behind: "Бот?" became "?" and "бот)) привет" became
    ")) привет", because the punctuation that was not in the class simply
    survived while the word in front of it did not. Requiring the delimiter
    makes every one of those a non-match, which is the conservative outcome.

    The ``\s*`` before the punctuation run is not decoration: without it
    "бот , привет" matched only up to the space and left the comma orphaned at
    the head of the query -- the same defect the delimiter rule exists to
    prevent, one keystroke away. Backtracking keeps the plain "бот привет"
    working, because the greedy ``\s*`` gives the space back to the ``\s+``
    that follows.
    """
    usable = sorted((t.strip() for t in trigger_words if t and t.strip()), key=len, reverse=True)
    if not usable:
        return None
    alternation = "|".join(re.escape(word) for word in usable)
    return re.compile(
        # leading space | the trigger, whole-word | glued closing punctuation
        # | either the end of the message, or whitespace optionally followed by
        # a spaced dash ("бот — ты как?").
        rf"^\s*(?<!\w)(?:{alternation})(?!\w)\s*[,:;!—–]*(?:$|\s+(?:[—–-]+\s+)?)",
        re.IGNORECASE,
    )


def strip_bot_address(text: str, trigger_words: Sequence[str]) -> str:
    """Return ``text`` with a leading address removed, for embedding purposes.

    The result is what should be *embedded*; the caller keeps the original for
    the prompt, for storage and for every other consumer. Surrounding
    whitespace is always normalised away, so a caller can test "was an address
    removed?" as ``result != text.strip()`` and get an answer about addresses
    rather than about trailing newlines -- Telegram delivers plenty of those,
    and the flag that reaches ``retrieval_log`` has to mean what it says.

    A message is never reduced to something that is not a query: if peeling
    leaves no word character at all (the bare "Бот", 7 occurrences in the
    production corpus), the message's own text is returned instead. A message
    that had no word character to begin with normalises to the empty string by
    the same path -- deliberately one exit rather than an early return for
    blank input, which was the first version and left "   " coming back
    untouched, i.e. the one input for which the caller's "was an address
    removed?" test answered yes about a message that has nothing in it.
    """
    pattern = _leading_trigger_pattern(trigger_words)
    remainder = text
    if pattern is not None:
        for _ in range(_MAX_STRIPS):
            match = pattern.match(remainder)
            # A zero-width match cannot happen (the tail requires a delimiter
            # or the end of input, and the trigger itself is non-empty), but
            # slicing on one would loop without progress, so it is excluded
            # rather than reasoned about at every future edit.
            if not match or match.end() == 0:
                break
            remainder = remainder[match.end() :]

    stripped = remainder.strip()
    if not _HAS_WORD.search(stripped):
        return text.strip()
    return stripped
