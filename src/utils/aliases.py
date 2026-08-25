"""Cleaning and normalising the name a chat calls one of its people.

Two forms come out of one parse, and keeping them in one function is the whole
point of this module: ``display`` is what gets rendered into a prompt and
echoed back to the user, ``norm`` is what uniqueness is decided on. Derive
them separately anywhere and they drift, and the drift is silent -- an alias
that looks taken but is not, or two rows the database happily accepts because
one carries a stray double space.

**Why the case-fold is real work here, unlike the identical-looking line in
``ChunkRepository.search``.** That one is ``translate($7, 'ёЁ', 'еЕ')`` mirroring
an FTS expression, and on the ``russian`` configuration both sides are a no-op
because PostgreSQL folds ё→е itself (measured, migration 029) -- it exists so
the two sides stay one edit apart. ``alias_norm`` feeds a plain btree unique
index, which folds nothing at all. "Алёна" and "Алена" are two different
strings to it, and a chat where one person holds both is exactly the confusion
this feature exists to remove. So the folding has to happen here.

**Whitespace is collapsed rather than rejected, and that is a security
property, not tidiness.** An alias is arbitrary user text rendered into two
line-oriented prompt blocks: the chat-history line ``[uid:N] {name}: {text}``
and a roster bullet. A newline inside it forges a whole extra row and puts
words in another person's mouth. The renderers sanitise too -- defence in
depth, because a future autocollector or a direct DB write does not come
through this function -- but collapsing at the door means the stored value is
already safe and the user sees what the bot will actually call them.

What is *not* rejected is as deliberate as what is. A colon is allowed: the
``[uid:N]`` prefix, not the name, is what identifies a speaker in the history
block, so a colon cannot impersonate anyone. A leading ``-`` is allowed: with
newlines already collapsed it stays on its own bullet's line. Rejecting either
would be a rule that wrongly refuses a name someone actually wanted, which is
a defect of the same size as one that wrongly accepts.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# Borrowed from the chunker's MAX_NAME_CHARS, and for the same reason: a name
# renders once per history row, ~20-30 rows per prompt, and there is no history
# token budget to absorb it (ADR-0001's HISTORY_BUDGET_TOKENS was never
# shipped). An unbounded alias is unbounded prompt growth on every single turn.
MAX_ALIAS_CHARS = 64

# The literal marker that opens a history row. Neutralised by
# ``sanitize_history_field`` at render time anyway; refused here because a name
# containing it is not a name, and "no" is a better answer than a mangled
# rendering the user never asked for.
_ROW_MARKER = "[uid:"


class AliasRejection(StrEnum):
    """Why a proposed alias cannot be stored. Maps 1:1 to a user-facing string."""

    EMPTY = "empty"
    TOO_LONG = "too_long"
    FORBIDDEN_CHARS = "forbidden_chars"


@dataclass(frozen=True)
class AliasParse:
    """Both forms of one alias, or the reason there is neither.

    ``rejection is None`` iff ``display`` and ``norm`` are both non-empty. The
    invariant is stated so callers can branch on one field instead of three.
    """

    display: str
    norm: str
    rejection: AliasRejection | None

    @property
    def ok(self) -> bool:
        return self.rejection is None


def _collapse(raw: str) -> str:
    """Every run of whitespace -- newlines included -- becomes one space."""
    return " ".join(raw.split())


def _has_forbidden(text: str) -> bool:
    """Control and format characters that survived the whitespace collapse.

    ``Cc`` covers NUL and friends; ``Cf`` covers the bidirectional overrides
    (U+202E and its family), which render as nothing and can make a name read
    on screen as a completely different name. Neither can appear in something a
    person means to be called.
    """
    return any(unicodedata.category(ch) in {"Cc", "Cf"} for ch in text)


def normalize_alias(raw: str) -> str:
    """The comparison form: collapsed, case-folded, ё→е.

    ``casefold`` rather than ``lower`` -- it is the one meant for
    caseless matching, and unlike ``lower`` it handles the pairs where a
    lowercase form is not the folded form.
    """
    return _collapse(raw).casefold().replace("ё", "е").replace("Ё", "е")


def parse_alias(raw: str) -> AliasParse:
    """Clean, validate and normalise in one pass, so the two forms cannot drift.

    Length is measured on the *collapsed* form, because that is what will be
    stored and rendered -- charging a user for whitespace they did not intend
    to type would be a rule that refuses for no reason.
    """
    display = _collapse(raw)
    if not display:
        return AliasParse("", "", AliasRejection.EMPTY)
    if len(display) > MAX_ALIAS_CHARS:
        return AliasParse("", "", AliasRejection.TOO_LONG)
    if _has_forbidden(display) or _ROW_MARKER in display.casefold():
        return AliasParse("", "", AliasRejection.FORBIDDEN_CHARS)
    return AliasParse(display, normalize_alias(display), None)


@dataclass(frozen=True)
class AliasEntry:
    """One person, as the chat refers to them."""

    user_id: int
    primary: str
    alternates: tuple[str, ...]


@dataclass(frozen=True)
class AliasView:
    """Everything the prompt layer needs to know about who is called what.

    Built once per turn by :func:`build_alias_view`, from one query. Two
    projections of the same rows live here together on purpose: rendering a
    history line needs an O(1) lookup by ``user_id`` while the roster needs a
    stable ordered list, and deriving one of them separately at a call site is
    how a prompt ends up naming somebody two different ways in one turn.
    """

    entries: tuple[AliasEntry, ...] = ()
    primary_by_user: dict[int, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.entries)


def primary_alias(aliases: AliasView, user_id: Any) -> str | None:
    """The name this chat calls ``user_id``, length-capped, or ``None``.

    Every surface that renders a participant goes through here so the cap
    cannot drift between them, while each keeps its own *fallback* order --
    the prompt's history block prefers ``username`` next, the summary prefers
    ``first_name``, and folding those together would silently rewrite one of
    them for every person who has no alias at all.

    The cap is applied on read even though ``parse_alias`` bounds the write
    path: a row can reach the table without passing it (a hand-written UPDATE,
    or the autocollector a later slice adds), and a name renders once per
    history row on every turn.
    """
    if not isinstance(user_id, int):
        return None
    alias = aliases.primary_by_user.get(user_id)
    return alias[:MAX_ALIAS_CHARS] if alias else None


def build_alias_view(rows: Iterable[Mapping[str, Any]]) -> AliasView:
    """Fold ``(user_id, alias, role)`` rows into the two projections above.

    Only people with a ``primary`` get a roster entry: an alternate is a name
    the bot should *recognise*, and listing one for somebody the bot has no
    agreed name for would invite it to start using the nickname instead. The
    alternates of such a person are still dropped rather than promoted --
    silently promoting one would be automation choosing what to call a human,
    which is the single thing migration 033 forbids.

    Row order decides alternate order (the repository sorts ``role, id``, so
    ``primary`` arrives first and alternates in the order they were added);
    this function preserves it rather than sorting, because "the name added
    first" is meaningful and alphabetical order is not.
    """
    primaries: dict[int, str] = {}
    alternates: dict[int, list[str]] = {}
    order: list[int] = []

    for row in rows:
        raw_uid = row.get("user_id")
        alias = str(row.get("alias") or "").strip()
        role = str(row.get("role") or "")
        if not isinstance(raw_uid, int) or not alias:
            continue
        if raw_uid not in alternates:
            alternates[raw_uid] = []
            order.append(raw_uid)
        if role == "primary":
            # First primary wins. The partial unique index makes a second one
            # impossible in practice; preferring the first keeps this a pure
            # function of its input rather than of which duplicate came last.
            primaries.setdefault(raw_uid, alias)
        else:
            alternates[raw_uid].append(alias)

    entries = tuple(
        AliasEntry(
            user_id=uid,
            primary=primaries[uid],
            alternates=tuple(a for a in alternates[uid] if a != primaries[uid]),
        )
        for uid in order
        if uid in primaries
    )
    return AliasView(entries=entries, primary_by_user=dict(primaries))
