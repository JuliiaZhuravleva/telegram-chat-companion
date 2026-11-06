"""Invented sample data. No value here ever comes from production.

``CHAT_ID`` is deliberately the longest id shape a supergroup can have, so a
callback that fits with it fits with every real one (lint R6).
"""

from __future__ import annotations

from typing import Final

CHAT_ID: Final = -1009999999999
CHAT_ID_SHORT: Final = -100200300
CHAT_ID_OTHER: Final = -100200301
CHAT_ID_NEW: Final = -100200302
USER_ID: Final = 555000111
USER_ID_OTHER: Final = 555000222
RULE_ID: Final = 99999
ATTEMPT_ID: Final = 99999
FUID: Final = "AgADZZZZZZZZZZZZZZZZ"
PACK: Final = "test_pack"
PACK_REF: Final = "0123456789ab"
"""Shape of a 12-hex pack reference (``left(md5(set_name), 12)``); not computed from ``PACK``."""

CHAT_TITLE: Final = "Тестовый чат"
CHAT_TITLE_EN: Final = "Test chat"
CHAT_TITLE_OTHER: Final = "Второй чат"
CHAT_TITLE_NEW: Final = "Новый чат"
PACK_TITLE: Final = "Тестовый пак"
PERSON: Final = "Тест Тестов"
PERSON_OTHER: Final = "Другой Человек"
USERNAME: Final = "@test_user"
