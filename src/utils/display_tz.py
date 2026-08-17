"""The one timezone the bot renders dates and times in.

Every user-visible date is the same question — "what day is it for the people in
this chat?" — and it had three independent answers: `prompt_builder._MEMORY_DATE_TZ`
(dates beside RAG memories), `capture.CAPTURE_TZ` (what "до 5 сентября" means),
and `summary.py`'s bare UTC `strftime` (which was simply wrong for anything after
20:00 local). Three definitions of one constant is how "до 5 сентября" and the
date printed next to a fact drift apart without a single test failing.

Not a per-chat setting, deliberately: making it one means every stored deadline
needs a zone alongside it (a fact captured in one zone and read in another has no
single correct calendar day), and no chat has asked. When that changes, this
module is the seam to widen — the callers already funnel through it.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("Asia/Tbilisi")
