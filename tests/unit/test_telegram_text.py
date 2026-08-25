"""Splitting a rendered HTML message into pieces Telegram will accept.

The bug these guard: a six-minute voice note transcribed to 4648 characters,
`sendMessage` answered `Bad Request: message is too long`, the exception was
re-raised out of the handler, and — because the global error handler only
replies to CallbackQuery events — the chat saw nothing at all. Four such
transcripts were lost in production the same way.

Two traps specific to testing this, both of which produce a green suite that
proves nothing:

* **`len()` is the wrong ruler.** Telegram counts UTF-16 code units, so a
  message of 2049 emoji is 2049 Python characters and 4098 Telegram ones. A
  budget assertion written with `len()` passes on a message Telegram rejects.
  Every length assertion below goes through `parsed_length`, and
  `test_astral_characters_count_double` is the control that proves the
  difference is real rather than theoretical.
* **A valid-looking piece is not a valid piece.** Telegram rejects a message
  with a broken entity or an unclosed tag *wholesale*, so "each piece is short
  enough" is only half the property. Each test that splits also asserts the
  pieces are well-formed and carry no severed entity.
"""

from __future__ import annotations

import random
import re
from html import escape, unescape

import pytest

from src.services.text.formatter import markdown_to_html
from src.utils.telegram_text import (
    DEFAULT_SPLIT_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    parsed_length,
    split_html,
)

# A `&` that does not begin a well-formed character reference — i.e. an entity
# that was cut in half. Telegram rejects the whole message for one of these.
_SEVERED_ENTITY = re.compile(r"&(?![a-zA-Z][a-zA-Z0-9]*;|#\d+;|#[xX][0-9a-fA-F]+;)")
_TAG = re.compile(r"</?([a-zA-Z-]+)(?:\s[^<>]*)?>")


def _well_formed(html: str) -> bool:
    stack: list[str] = []
    for match in _TAG.finditer(html):
        name = match.group(1).lower()
        if match.group(0).startswith("</"):
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def _visible(html: str) -> str:
    """The text a reader sees: tags gone, entities decoded."""
    return unescape(_TAG.sub("", html))


def _assert_deliverable(pieces: list[str], limit: int) -> None:
    """Every property Telegram enforces, on every piece."""
    assert pieces, "splitting produced no pieces at all"
    for index, piece in enumerate(pieces):
        assert parsed_length(piece) <= limit, (
            f"piece {index} is {parsed_length(piece)} units, over the {limit} budget"
        )
        assert _well_formed(piece), f"piece {index} has unbalanced tags: {piece[:120]!r}"
        assert not _SEVERED_ENTITY.search(piece), (
            f"piece {index} contains a severed entity: {piece[:120]!r}"
        )


class TestFitsAlready:
    """The common path must be untouched — this runs on every message sent."""

    def test_short_html_is_returned_byte_identical(self) -> None:
        html = "🎙 <b>Расшифровка от</b> Jay:\n\nпривет как дела"
        assert split_html(html) == [html]

    def test_a_message_at_exactly_the_budget_is_not_split(self) -> None:
        html = "a" * DEFAULT_SPLIT_LIMIT
        assert parsed_length(html) == DEFAULT_SPLIT_LIMIT
        assert split_html(html) == [html]

    def test_one_unit_over_the_budget_splits(self) -> None:
        html = "a" * (DEFAULT_SPLIT_LIMIT + 1)
        assert len(split_html(html)) == 2


class TestTheProductionRegression:
    """The exact shape that was lost four times in production."""

    def test_a_six_minute_transcript_becomes_deliverable_pieces(self) -> None:
        # 4648 characters is the measured length of the transcript that was
        # rejected; the header pushed the rendered message to 4672 units.
        transcript = escape("слово " * 800)[:4648]
        assert parsed_length(transcript) > TELEGRAM_MESSAGE_LIMIT

        pieces = split_html(transcript)

        assert len(pieces) > 1
        _assert_deliverable(pieces, DEFAULT_SPLIT_LIMIT)

    def test_no_word_is_lost_across_the_split(self) -> None:
        source = "слово " * 2000
        pieces = split_html(escape(source))
        # Without this the test passes on unsplit output, where rejoining is
        # trivially the identity — it would confirm the bug rather than the fix.
        assert len(pieces) > 1
        rejoined = " ".join(_visible(piece) for piece in pieces)
        assert rejoined.split() == source.split()


class TestUtf16Accounting:
    def test_parsed_length_ignores_tags(self) -> None:
        assert parsed_length("<b>abc</b>") == 3

    def test_parsed_length_decodes_entities(self) -> None:
        # 5 raw characters, 1 as Telegram counts it. Budgeting on len() is what
        # made /kb truncate pages Telegram would have accepted.
        assert parsed_length("&amp;") == 1
        assert len("&amp;") == 5

    def test_astral_characters_count_double(self) -> None:
        """The control for the `len()` trap: same Python length, different budget."""
        emoji = "\U0001f600" * 2049
        assert len(emoji) == 2049  # would look comfortably inside 4096
        assert parsed_length(emoji) == 4098  # what Telegram actually counts

        pieces = split_html(emoji)

        assert len(pieces) > 1, "a len()-based budget would have sent this as one message"
        _assert_deliverable(pieces, DEFAULT_SPLIT_LIMIT)

    def test_an_entity_is_never_cut_in_half(self) -> None:
        # limit=101, NOT 100. `len("&amp;")` is 5, which divides 100, so every
        # hard cut would land on an entity boundary by arithmetic — the test
        # then passes with entity awareness deleted outright (measured: replace
        # _ATOM_RE with r"[\s\S]" and the whole file still goes green). 101 is
        # coprime with 5, so a byte-budget cut lands mid-entity and the
        # severed-entity check actually fires.
        pieces = split_html("&amp;" * 6000, limit=101)
        _assert_deliverable(pieces, 101)


class TestMarkupSurvivesTheBoundary:
    @pytest.mark.parametrize(
        "tag",
        ["b", "i", "s", "code", "pre", "blockquote"],
    )
    def test_a_tag_straddling_the_cut_is_closed_and_reopened(self, tag: str) -> None:
        html = f"<{tag}>" + ("word " * 200) + f"</{tag}>"

        pieces = split_html(html, limit=100)

        assert len(pieces) > 1
        _assert_deliverable(pieces, 100)
        for piece in pieces:
            assert piece.startswith(f"<{tag}>")
            assert piece.endswith(f"</{tag}>")

    def test_nested_tags_are_reopened_in_order(self) -> None:
        html = "<blockquote><code>" + ("x" * 500) + "</code></blockquote>"

        pieces = split_html(html, limit=100)

        _assert_deliverable(pieces, 100)
        for piece in pieces:
            assert piece.startswith("<blockquote><code>")

    def test_an_anchor_keeps_its_href_on_every_piece(self) -> None:
        """The summary path injects `<a href="tg://user?id=N">` after formatting."""
        html = '<a href="tg://user?id=42">' + ("n" * 500) + "</a>"

        pieces = split_html(html, limit=100)

        _assert_deliverable(pieces, 100)
        for piece in pieces:
            assert 'href="tg://user?id=42"' in piece

    def test_it_prefers_to_break_between_words(self) -> None:
        # limit=98, NOT 100, for the same reason as the entity test above:
        # `len("word ")` is 5 and divides 100, so a cut at the raw budget would
        # land between words on its own and the word-break preference could be
        # deleted with the output unchanged.
        pieces = split_html("word " * 200, limit=98)
        # `pieces[:-1]` is empty when nothing was split, so the loop below
        # would iterate zero times and pass against a splitter that does not.
        assert len(pieces) > 1
        for piece in pieces[:-1]:
            assert piece.endswith(("word", "word ")), f"cut mid-word: {piece[-12:]!r}"


class TestMalformedInput:
    """`markdown_to_html` can emit crossing tags — Telegram rejects those anyway."""

    def test_crossing_tags_degrade_to_plain_text_rather_than_nonsense(self) -> None:
        # '**a *b** c*' renders as this: <b> and <i> cross, so no tag stack can
        # split it. Telegram rejects it whole, so the words are what is left to
        # save.
        html = "<b>a <i>" + ("b" * 500) + "</b> c</i>"

        pieces = split_html(html, limit=100)

        _assert_deliverable(pieces, 100)
        assert "".join(_visible(p) for p in pieces).replace(" ", "") == ("a" + "b" * 500 + "c")

    def test_the_formatter_really_can_produce_that(self) -> None:
        """Control: the malformed case above is not a straw man."""
        assert not _well_formed(markdown_to_html("**a *b** c*"))


class TestFuzzedFormatterOutput:
    def test_every_split_of_real_formatter_output_is_deliverable(self) -> None:
        """Derived from the threat, not from the implementation.

        Feeds the actual formatter randomised markdown — the producer whose
        output these sends carry — rather than hand-written fixtures shaped
        like the branches `split_html` happens to implement.
        """
        rng = random.Random(20260825)
        fragments = [
            "**bold**", "*italic*", "~~struck~~", "`code`", "```\nblock\n```",
            "> quoted line", "# heading", "plain words", "a & b < c > d",
            "\U0001f600\U0001f600", 'quote " and \' apostrophe', "многобукв " * 5,
        ]  # fmt: skip
        checked = 0
        for _ in range(300):
            source = " ".join(rng.choice(fragments) for _ in range(rng.randint(40, 160)))
            html = markdown_to_html(source)
            if parsed_length(html) <= 400:
                continue
            _assert_deliverable(split_html(html, limit=400), 400)
            checked += 1
        assert checked > 50, f"fuzz corpus produced only {checked} over-length samples"
