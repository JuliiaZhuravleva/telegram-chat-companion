"""The owner's rules over the model's facts -- the only place a verdict is made.

The rules are hers (2026-10-28), transcribed as code so that changing one is an
edit here, not a re-tuned prompt:

- Asking "may I give advice?" is never a violation.
- A joke, an answer to another member's question, or agreeing with a decision
  she already made is not advice.
- Telling her that her suffering is made up is the hardest case, advice or not.
- A suggestion she asked for (her request, or a real request in the post
  answered to her) is fine. Unasked, it is a violation when it is about her
  life, her psyche, how to think or feel, or how to run the blog; about
  everyday things (food, sleep, a walk) only when the post is about a hard
  state -- "drink some tea" under a post about depression.
- Support counts only under a post about her feelings or a hard state: a short
  "hugs" is 1, actually engaging with what she feels is 2. Unsolicited advice
  caps it at 1 -- support that arrives wrapped in a lecture is not the thing
  her post "how to support me" asks for.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_HER_DOMAIN_TOPICS = ("life", "psyche", "thinking", "blog")
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


@dataclass(frozen=True)
class Verdict:
    advice: str  # none | unsolicited | requested | permission_asked
    level: str | None  # hard | soft, only for unsolicited
    support: int  # 0..2
    constructive: bool


class FactsParseError(ValueError):
    """The model's answer held no JSON object."""


def parse_facts(text: str) -> dict[str, Any]:
    """The outermost `{...}` of a model answer, as a dict.

    Thinking models sometimes wrap the object in prose or a code fence even in
    JSON mode, so this takes the widest brace span rather than the whole text.
    """
    match = _JSON_OBJECT_RE.search(text or "")
    if match is None:
        raise FactsParseError("no JSON object in model answer")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise FactsParseError(f"bad JSON in model answer: {e.msg}") from e
    if not isinstance(value, dict):
        raise FactsParseError("model answer is not a JSON object")
    return value


def verdict(facts: dict[str, Any], post_facts: dict[str, Any]) -> Verdict:
    """Apply the rules. `post_facts` is `{}` when the message is under no post."""
    f, p = facts, post_facts
    advice, level = "none", None
    if f.get("asks_permission"):
        advice = "permission_asked"
    elif f.get("joke") or f.get("answers_member_question") or f.get("backs_her"):
        advice = "none"
    elif f.get("dismisses_suffering"):
        advice, level = "unsolicited", "hard"
    elif f.get("suggests"):
        if f.get("answers_author_request") or (p.get("real_request") and f.get("to") == "author"):
            advice = "requested"
        elif f.get("topic") in _HER_DOMAIN_TOPICS:
            advice, level = "unsolicited", "hard"
        elif f.get("topic") == "everyday" and p.get("hard_state"):
            advice, level = "unsolicited", "soft"

    support = 0
    if (
        (p.get("about_her_feelings") or p.get("hard_state"))
        and not f.get("joke")
        and not f.get("dismisses_suffering")
    ):
        # A missing `feelings` is "none", not 2: the evaluated script let an
        # answer that skipped the field earn full support credit.
        support = {"short_sympathy": 1, "none": 0}.get(f.get("feelings") or "none", 2)
        if advice == "unsolicited":
            support = min(support, 1)

    return Verdict(
        advice=advice,
        level=level,
        support=support,
        constructive=bool(f.get("substantive")) and not f.get("joke"),
    )
