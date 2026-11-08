"""Two-language copy for the admin screens.

Copy lives as `Tr` constants at the top of each screen module, next to the
builder that uses it, so a screen and its words are reviewed together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Lang = Literal["ru", "en"]


@dataclass(frozen=True, slots=True)
class Tr:
    """One string in both languages. ``Tr("Чаты", "Chats")("en") == "Chats"``."""

    ru: str
    en: str

    def __call__(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru


def lang_of(raw: object) -> Lang:
    """Normalise a language token from a payload or a setting: anything but ``"en"`` is ru.

    Russian is the panel default (``AdminRepository.get_admin_language``), so a
    garbled or missing token lands on the language most admins read.
    """
    return "en" if raw == "en" else "ru"


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for ``n``: 1 заявка, 2 заявки, 5 заявок, 11 заявок, 21 заявка."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
