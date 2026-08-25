#!/usr/bin/env python3
"""Drive the real Bot API against `split_html` output. Not part of CI.

Every test of the splitter in `tests/unit/test_telegram_text.py` asserts on our
own arithmetic. That is necessary and not sufficient: `message.answer` is an
AsyncMock in every unit test in this repo, so it accepts a severed entity, an
unclosed <blockquote> and a 4097-unit body identically. The rejection this code
exists to prevent only exists on Telegram's side, and the bug that motivated it
(four transcripts silently lost in production) was invisible to a green suite.

So this sends the real bodies to a real chat and asserts on what comes back --
including the ENTITIES, because Telegram answers 200 while silently dropping
entities it dislikes, and a status check would call that a pass.

It ends with a NEGATIVE CONTROL: one deliberately un-split body that must be
REJECTED. Without it a probe that had stopped exercising anything would report
success just as loudly.

Usage (never in CI -- it posts to, and then deletes from, a real chat)::

    TELEGRAM_BOT_TOKEN=... PROBE_CHAT_ID=-100... uv run python scripts/probe_telegram_limits.py

`PROBE_CHAT_ID` is required rather than defaulted: this repo is public and a
chat id committed into it cannot be taken back (CLAUDE.md, "scrub real Telegram
ids"). Use a throwaway test chat the bot is a member of.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.modules.voice.transcription import VoiceTranscriptionService
from src.services.text.formatter import markdown_to_html
from src.utils.telegram_text import parsed_length, split_html


def _utf16(text: str) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


# Cases whose whole point is that markup survives the split. A 200 with no
# entities back means Telegram accepted the text and threw the markup away.
_MUST_KEEP_ENTITIES = frozenset({"transcription", "formatter", "anchor"})


def _cases() -> list[tuple[str, list[str]]]:
    """Shapes derived from the threat, not from the splitter's branches."""
    transcript = "Привет, я хотел рассказать про то, как прошёл день. " * 92
    markdown = (
        "**жирный** *курсив* ~~зачёркнуто~~ `код` a & b < c > d "
        "> цитата строка\n\n```\nblock\n```\n"
    ) * 60
    # A URL anchor, not the tg://user?id= form the summary path uses: proving
    # the href survives being re-opened on each piece needs Telegram to actually
    # emit an entity, and it only emits text_mention for a REAL user id — which
    # must never enter this public repo. A text_link exercises the same
    # attribute-carrying property with nothing to leak.
    anchor = '<a href="https://example.com/x">ссылка</a> и текст: ' * 400
    return [
        ("transcription", VoiceTranscriptionService.format_reply_parts("Jay & Co", transcript)),
        ("formatter", split_html(markdown_to_html(markdown))),
        ("anchor", split_html(anchor)),
        ("astral emoji", split_html("\U0001f600" * 2400)),
    ]


async def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    raw_chat = os.environ.get("PROBE_CHAT_ID")
    if not token or not raw_chat:
        print("set TELEGRAM_BOT_TOKEN and PROBE_CHAT_ID (see this file's docstring)")
        return 2
    chat_id = int(raw_chat)
    base = f"https://api.telegram.org/bot{token}"

    sent: list[int] = []
    failures: list[str] = []

    async with httpx.AsyncClient(timeout=30) as client:

        async def send(text: str) -> dict:
            resp = await client.post(
                f"{base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    # Explicit: without it Telegram parses nothing and every
                    # malformed-markup case would "pass" against plain text.
                    "parse_mode": "HTML",
                    "disable_notification": True,
                },
            )
            return resp.json()

        for name, parts in _cases():
            print(f"\n=== {name}: {len(parts)} part(s) {[parsed_length(p) for p in parts]}")
            # A case that fits in one message exercises no boundary at all, so
            # it cannot show that markup survives being re-opened. Say so
            # loudly rather than reporting a pass for a check that never ran.
            if len(parts) < 2:
                failures.append(f"{name}: only {len(parts)} part - this case no longer splits")
            for index, part in enumerate(parts, start=1):
                body = await send(part)
                if not body.get("ok"):
                    failures.append(f"{name} part {index}: {body.get('description')}")
                    print(f"  part {index}: REJECTED -> {body.get('description')}")
                    continue
                result = body["result"]
                sent.append(result["message_id"])
                theirs = _utf16(result.get("text", ""))
                mine = parsed_length(part)
                kinds = sorted({e["type"] for e in result.get("entities", [])})
                # An empty entity list on a case built from markup means
                # Telegram silently dropped it -- a 200 that proves nothing.
                if name in _MUST_KEEP_ENTITIES and not kinds:
                    failures.append(
                        f"{name} part {index}: Telegram returned NO entities; the markup "
                        f"this case exists to check was dropped"
                    )
                # Telegram trims edge whitespace and caps AFTER the trim, so
                # our count may exceed theirs. It must never fall below.
                if theirs > mine:
                    failures.append(f"{name} part {index}: UNDER-counted {mine} < {theirs}")
                if theirs > 4096:
                    failures.append(f"{name} part {index}: {theirs} units, over the limit")
                print(f"  part {index}: ok, telegram={theirs} ours={mine}, entities={kinds}")

        transcript = "Привет, я хотел рассказать про то, как прошёл день. " * 92
        whole = VoiceTranscriptionService.format_reply("Jay & Co", transcript)
        control = await send(whole)
        print(f"\n=== NEGATIVE CONTROL: un-split, {parsed_length(whole)} units")
        if control.get("ok"):
            sent.append(control["result"]["message_id"])
            failures.append(
                "negative control was ACCEPTED -- the probe is no longer exercising "
                "the limit and proves nothing"
            )
        elif "too long" not in (control.get("description") or "").lower():
            failures.append(
                f"negative control failed for another reason: {control.get('description')}"
            )
        else:
            print(f"  correctly rejected -> {control['description']}")

        for message_id in sent:
            await client.post(
                f"{base}/deleteMessage", json={"chat_id": chat_id, "message_id": message_id}
            )
        print(f"\ncleaned up {len(sent)} probe messages")

    if failures:
        print("\nFAILURES:\n  " + "\n  ".join(failures))
        return 1
    print("\nALL LIVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
