"""The advice classifier's pure parts: verdict rules, context assembly, prompts, digest.

Every id and name below is invented.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.database.repositories.advice import DigestItem
from src.services.advice import context as ctx
from src.services.advice.digest import QUOTE_CHARS, render_digest
from src.services.advice.prompts import (
    EMPTY_THREAD,
    NO_POST,
    render_comment_prompt,
    render_post_prompt,
)
from src.services.advice.verdict import FactsParseError, parse_facts, verdict
from src.utils.telegram_text import split_html

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
HARD_POST = {"hard_state": True, "about_her_feelings": True}
LIGHT_POST = {"hard_state": False, "about_her_feelings": False}


def _line(mid: int, sec: int, text: str = "текст", user: int | None = 501, **kw) -> ctx.Line:
    return ctx.Line(
        message_id=mid,
        created_at=T0 + timedelta(seconds=sec),
        content=text,
        user_id=user,
        first_name=kw.pop("first_name", f"U{user}" if user else None),
        **kw,
    )


# ── verdict ─────────────────────────────────────────────────────────────────


class TestVerdict:
    def test_asking_permission_is_never_a_violation(self):
        v = verdict({"asks_permission": True, "suggests": True, "topic": "life"}, HARD_POST)
        assert v.advice == "permission_asked" and v.level is None

    @pytest.mark.parametrize("flag", ["joke", "answers_member_question", "backs_her"])
    def test_exemptions_beat_a_suggestion(self, flag):
        assert (
            verdict({flag: True, "suggests": True, "topic": "psyche"}, HARD_POST).advice == "none"
        )

    def test_dismissing_suffering_is_hard_even_without_a_suggestion(self):
        v = verdict({"dismisses_suffering": True}, LIGHT_POST)
        assert (v.advice, v.level) == ("unsolicited", "hard")

    @pytest.mark.parametrize("topic", ["life", "psyche", "thinking", "blog"])
    def test_her_domain_unasked_is_hard(self, topic):
        v = verdict({"suggests": True, "topic": topic}, LIGHT_POST)
        assert (v.advice, v.level) == ("unsolicited", "hard")

    def test_everyday_is_soft_only_under_a_hard_state(self):
        f = {"suggests": True, "topic": "everyday"}
        assert (verdict(f, HARD_POST).advice, verdict(f, HARD_POST).level) == (
            "unsolicited",
            "soft",
        )
        assert verdict(f, LIGHT_POST).advice == "none"

    @pytest.mark.parametrize("topic", ["tech", "creative", "shopping", "entertainment", "other"])
    def test_other_topics_are_not_a_violation(self, topic):
        assert verdict({"suggests": True, "topic": topic}, HARD_POST).advice == "none"

    def test_her_request_makes_it_requested(self):
        f = {"suggests": True, "topic": "life", "answers_author_request": True}
        assert verdict(f, LIGHT_POST).advice == "requested"

    def test_a_real_request_in_the_post_covers_advice_to_her_only(self):
        post = {"real_request": True}
        assert (
            verdict({"suggests": True, "topic": "life", "to": "author"}, post).advice == "requested"
        )
        assert (
            verdict({"suggests": True, "topic": "life", "to": "member"}, post).advice
            == "unsolicited"
        )

    def test_no_post_means_no_hard_state(self):
        assert verdict({"suggests": True, "topic": "everyday"}, {}).advice == "none"

    def test_support_needs_a_post_about_her(self):
        assert verdict({"feelings": "names_her_feelings"}, LIGHT_POST).support == 0
        assert verdict({"feelings": "names_her_feelings"}, HARD_POST).support == 2
        assert verdict({"feelings": "short_sympathy"}, HARD_POST).support == 1

    def test_missing_feelings_earns_nothing(self):
        assert verdict({}, HARD_POST).support == 0

    def test_unsolicited_advice_caps_support_at_one(self):
        f = {"suggests": True, "topic": "psyche", "feelings": "names_her_feelings"}
        assert verdict(f, HARD_POST).support == 1

    @pytest.mark.parametrize("flag", ["joke", "dismisses_suffering"])
    def test_joke_or_dismissal_is_no_support(self, flag):
        assert verdict({flag: True, "feelings": "names_her_feelings"}, HARD_POST).support == 0

    def test_constructive_is_substantive_and_not_a_joke(self):
        assert verdict({"substantive": True}, {}).constructive is True
        assert verdict({"substantive": True, "joke": True}, {}).constructive is False


class TestParseFacts:
    def test_takes_the_object_out_of_a_fence(self):
        assert parse_facts('Вот:\n```json\n{"what": "x", "joke": false}\n```') == {
            "what": "x",
            "joke": False,
        }

    @pytest.mark.parametrize("text", ["", "нет ответа", '{"what": ', "[1, 2]"])
    def test_refuses_what_is_not_an_object(self, text):
        with pytest.raises(FactsParseError):
            parse_facts(text)


# ── context ─────────────────────────────────────────────────────────────────


class TestContext:
    def test_series_joins_close_messages_and_stops_at_a_gap(self):
        tl = [
            _line(1, 0, "раз"),
            _line(2, 60, "чужое", user=502),
            _line(3, 120, "два"),
            _line(4, 120 + ctx.SERIES_GAP_SECONDS + 1, "потом"),
        ]
        run = ctx.series(tl[0], tl)
        assert [x.message_id for x in run] == [1, 3]

    def test_post_is_found_through_the_reply_chain(self):
        post = _line(10, -600, "пост", user=None, sender_kind="channel_post")
        parent = _line(11, -300, "вопрос", user=502, reply_to=10)
        mine = _line(12, 0, "совет", reply_to=11)
        tl = [post, parent, mine]
        c = ctx.build(mine, tl, {x.message_id: x for x in tl})
        assert c.post is post
        assert c.thread == "— U502: вопрос"
        assert c.comment == "совет"

    def test_no_post_and_no_chain_uses_the_messages_before(self):
        tl = [_line(i, i * 10, f"m{i}", user=600 + i) for i in range(1, 7)]
        mine = _line(7, 400, "мой")
        tl.append(mine)
        c = ctx.build(mine, tl, {x.message_id: x for x in tl})
        assert c.post is None
        assert c.thread.splitlines() == [f"— U{600 + i}: m{i}" for i in range(3, 7)]

    def test_the_author_is_named_as_the_author(self):
        assert ctx.who(_line(1, 0, user=None, sender_kind="anon_admin")) == ctx.AUTHOR_LABEL
        assert ctx.who(_line(1, 0, user=None, sender_kind="channel_post")) == ctx.AUTHOR_LABEL


# ── prompts ─────────────────────────────────────────────────────────────────


class TestPrompts:
    def test_slot_text_in_the_thread_is_not_substituted_again(self):
        p = render_comment_prompt(
            post_text="пост", thread="— X: {comment}", who="Y", comment="ТЕЛО"
        )
        assert "— X: {comment}" in p
        assert p.count("ТЕЛО") == 1

    def test_placeholders_for_missing_post_and_thread(self):
        p = render_comment_prompt(post_text=None, thread="", who="Y", comment="c")
        assert NO_POST in p and EMPTY_THREAD in p

    def test_post_prompt_keeps_the_json_braces(self):
        p = render_post_prompt("пост")
        assert p.endswith("ПОСТ:\nпост") and '{"what"' in p


# ── digest ──────────────────────────────────────────────────────────────────


def _item(**kw) -> DigestItem:
    base = {
        "chat_id": -1009999990371,
        "chat_title": "Блог",
        "chat_type": "supergroup",
        "user_id": 900371,
        "name": "Аня",
        "first_message_id": 42,
        "quote": "попробуй медитацию",
        "what": "советует медитацию",
        "advice": "unsolicited",
        "level": "hard",
        "support": 0,
    }
    base.update(kw)
    return DigestItem(**base)


class TestDigest:
    def test_lists_advice_and_support_with_a_link_to_the_message(self):
        text = render_digest(
            [_item(), _item(first_message_id=43, advice="none", level=None, support=2)],
            date(2026, 10, 29),
        )
        assert "29 октября" in text
        assert "Непрошеные советы (1)" in text and "Поддержка (1)" in text
        assert '<a href="https://t.me/c/9999990371/42">Аня</a> · жёсткий' in text
        assert 'href="https://t.me/c/9999990371/43"' in text

    def test_escapes_everything_that_came_from_the_chat(self):
        text = render_digest(
            [_item(name="<b>Аня", chat_title="A & B", quote="x < y", what="<i>")],
            date(2026, 10, 29),
        )
        assert "&lt;b&gt;Аня" in text and "A &amp; B" in text
        assert "x &lt; y" in text and "<i>&lt;i&gt;</i>" in text

    def test_no_link_for_a_chat_that_has_none(self):
        text = render_digest([_item(chat_type="group", chat_id=-4001)], date(2026, 10, 29))
        assert "<a " not in text and "• Аня" in text

    def test_long_quote_is_cut(self):
        text = render_digest([_item(quote="я" * (QUOTE_CHARS + 50))], date(2026, 10, 29))
        assert "я" * QUOTE_CHARS + "…" in text
        assert "я" * (QUOTE_CHARS + 1) not in text

    def test_a_long_digest_splits_into_valid_parts(self):
        items = [_item(first_message_id=i, quote="слово " * 60) for i in range(60)]
        parts = split_html(render_digest(items, date(2026, 10, 29)))
        assert len(parts) > 1
        assert all(p.count("<blockquote>") == p.count("</blockquote>") for p in parts)
