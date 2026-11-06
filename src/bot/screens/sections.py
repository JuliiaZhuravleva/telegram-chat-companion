"""Which chat-panel section each per-chat setting is shown in.

The panel groups settings by what an admin is trying to do (behaviour, memory,
voice…), which is not the storage grouping in ``settings_fields.FieldGroup``
(modules, kb…). That enum is deliberately NOT changed: the defaults screen and
the existing toggles read it. This table is the UI's own grouping, keyed by the
short field code that already travels in ``adm_pnl_tgl:`` payloads, so a toggle
knows which section to re-render after a write.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

from src.bot.settings_fields import FieldSpec

Section = Literal["behavior", "memory", "voice", "stickers", "reactions", "community", "rules"]

SECTIONS: Final[tuple[Section, ...]] = (
    "behavior",
    "memory",
    "voice",
    "stickers",
    "reactions",
    "community",
    "rules",
)

_MEMBERS: Final[dict[Section, tuple[str, ...]]] = {
    "behavior": ("tw", "rc", "ri", "sp", "lg", "rg", "af", "lc"),
    "memory": ("sm", "rag", "ch", "kb"),
    "voice": ("tv", "tn", "tx", "ia"),
    "stickers": ("sl", "sc", "sr", "sx", "ic", "ix", "tol"),
    "reactions": ("rx", "rh"),
    "community": ("cm", "gf", "wb", "fp", "fd", "rj", "aw"),
    "rules": ("re", "rm"),
}

SECTION_OF: Final[dict[str, Section]] = {
    code: section for section, codes in _MEMBERS.items() for code in codes
}
"""Field code -> section. Every ``FieldSpec.code`` appears exactly once (tested)."""

SECTION_SCREEN: Final[dict[Section, str]] = {
    "behavior": "behavior",
    "memory": "memory",
    "voice": "voice",
    "stickers": "chat.stickers",
    "reactions": "reactions",
    "community": "community",
    "rules": "rules",
}
"""Section -> design screen id. Only stickers differs: ``stickers`` alone is the catalogue."""

LEGACY_GROUPS: Final[dict[str, Section]] = {"modules": "memory", "kb": "memory", "rules": "rules"}
"""Old ``adm_pnl_grp:`` group tokens that are not section names, and where they land now."""


def section_of_token(token: str) -> Section | None:
    """Section for a ``adm_pnl_grp:`` token: a section name or an old group name."""
    if token in _MEMBERS:
        return token  # `in` over Literal keys narrows str to Section
    return LEGACY_GROUPS.get(token)


def shows_common(field: FieldSpec, row: Mapping[str, Any]) -> bool:
    """Whether the ``· общее`` ("inherited") marker belongs next to this field.

    Only a field whose column has no SQL DEFAULT can honestly say it inherits:
    for the legacy columns a NULL never survives ``ensure_exists()``, so the
    value shown IS the chat's own even when nobody chose it.
    """
    return not field.legacy and row.get(field.key) is None
