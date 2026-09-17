"""Turning a provider's error text into advice for the admin.

Written after a 24-hour embedding outage whose cause was one sentence long and
visible nowhere: the provider layer discarded the response body, so both the
log and the alert said "rate limit exceeded (retry in 65.0s)" -- a delay we had
invented -- while the body said the project's prepaid credits were gone.

The three exhaustion kinds behind a single HTTP 429 need opposite reactions,
which is the whole reason this mapping earns its place:

- a per-minute quota passes by itself in a minute,
- a per-day quota passes at the reset hour (11:00 Tbilisi for Gemini),
- depleted prepaid credits never pass until someone pays.

Deliberately returns None for anything unrecognised. A hint is a claim about
what to do; inventing one for an unknown error would send the reader to the
wrong page with our authority behind it.
"""

from __future__ import annotations

import re

# Ordered: the first match wins, so the specific patterns come before the
# generic "quota" one. `prepayment credits` must precede it in particular --
# Google's billing error also contains the word "billing" and arrives with the
# same RESOURCE_EXHAUSTED status as a quota.
_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"prepay(?:ment)?\s+credits?\s+are\s+depleted", re.IGNORECASE),
        "Кредиты провайдера кончились — само не пройдёт, нужно пополнить "
        "(Gemini: https://ai.studio/projects, вкладка Billing)",
    ),
    (
        re.compile(r"api[_ ]key[_ ]invalid|api key not valid", re.IGNORECASE),
        "Ключ недействителен — перевыпустить и обновить секрет в vault",
    ),
    (
        re.compile(r"permission[_ ]denied|caller does not have permission", re.IGNORECASE),
        "Ключу запрещён этот вызов — проверить, включён ли API в проекте",
    ),
    (
        re.compile(r"per\s*day|requestsperday|daily limit", re.IGNORECASE),
        "Выбрана суточная квота — пройдёт само после сброса (у Gemini это 11:00 по Тбилиси)",
    ),
    (
        re.compile(r"per\s*minute|requestsperminute", re.IGNORECASE),
        "Минутная квота — пройдёт само; если держится, воркер бьёт пачкой",
    ),
    (
        re.compile(r"insufficient[_ ]quota|billing", re.IGNORECASE),
        "Похоже на биллинг, а не на скорость — проверить баланс проекта",
    ),
)


def hint_for(error_message: str | None) -> str | None:
    """Advice for this provider error, or None when it is not recognised."""
    if not error_message:
        return None
    for pattern, hint in _HINTS:
        if pattern.search(error_message):
            return hint
    return None
