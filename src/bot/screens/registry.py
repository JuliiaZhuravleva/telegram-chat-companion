"""Sample screens: every redesigned screen, built from invented data, in one registry.

A sample is the unit both the rules test and ``scripts/dump_screens.py`` walk.
Each area keeps its samples in ``samples/<area>.py`` and decorates a builder
call with `sample`; `load_all()` imports every module in ``samples/`` so that
adding a file is all it takes to register it.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal

from src.bot.screens.i18n import Lang
from src.bot.screens.model import Screen

Nav = Literal["panel", "root", "prompt", "card", "group"]

SECTIONS: Final = (
    "Вход",
    "Панель",
    "Чаты",
    "Стикеры",
    "Состояние",
    "Настройки",
    "Карточки от бота",
    "В чатах",
)
"""Section names of the design's contact sheet, in its order."""

_RULE_IDS: Final = frozenset(f"R{n}" for n in range(1, 14))


@dataclass(frozen=True)
class Sample:
    screen_id: str
    section: str
    title: str
    """What this variant shows, for the contact sheet ("Память: сохранение выключено")."""
    build: Callable[[Lang], Screen]
    nav: Nav
    user_data: tuple[str, ...] = ()
    """Strings that come from chats and people, exempt from the language checks (R8/R9)."""
    exempt: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """Rule id → why this sample may break it. An empty reason is refused."""


_REGISTRY: dict[tuple[str, str], Sample] = {}


def make_sample(
    screen_id: str,
    build: Callable[[Lang], Screen],
    *,
    section: str,
    title: str,
    nav: Nav,
    user_data: tuple[str, ...] = (),
    exempt: Mapping[str, str] | None = None,
) -> Sample:
    """A validated `Sample`, not registered: an unknown section, an unknown
    rule id or an exemption without a reason is refused here, at import time."""
    if section not in SECTIONS:
        raise ValueError(f"sample {screen_id!r}: unknown section {section!r}")
    reasons = dict(exempt or {})
    for rule, reason in reasons.items():
        if rule not in _RULE_IDS:
            raise ValueError(f"sample {screen_id!r}: exempt names unknown rule {rule!r}")
        if not reason.strip():
            raise ValueError(f"sample {screen_id!r}: exemption from {rule} has no reason")
    return Sample(
        screen_id=screen_id,
        section=section,
        title=title,
        build=build,
        nav=nav,
        user_data=user_data,
        exempt=MappingProxyType(reasons),
    )


def sample(
    screen_id: str,
    *,
    section: str,
    title: str,
    nav: Nav,
    user_data: tuple[str, ...] = (),
    exempt: Mapping[str, str] | None = None,
) -> Callable[[Callable[[Lang], Screen]], Callable[[Lang], Screen]]:
    """Register ``fn(lang) -> Screen`` as a sample of ``screen_id``.

    Several samples per id are expected: every state that changes the rows
    (empty list, off, error) is its own variant, told apart by ``title``. Two
    different builders under one ``(screen_id, title)`` are refused — the
    second would silently replace the first in the test and the dump.
    """

    def register(fn: Callable[[Lang], Screen]) -> Callable[[Lang], Screen]:
        item = make_sample(
            screen_id,
            fn,
            section=section,
            title=title,
            nav=nav,
            user_data=user_data,
            exempt=exempt,
        )
        key = (screen_id, title)
        existing = _REGISTRY.get(key)
        if existing is not None and _origin(existing.build) != _origin(fn):
            raise ValueError(f"sample {screen_id!r} / {title!r} is registered twice")
        _REGISTRY[key] = item
        return fn

    return register


def _origin(fn: Callable[..., object]) -> tuple[str, str]:
    # A module imported twice (importlib.reload in a test) re-registers the same
    # builder under a new function object; that is the same sample, not a clash.
    return (getattr(fn, "__module__", "?"), getattr(fn, "__qualname__", "?"))


def load_all() -> dict[str, list[Sample]]:
    """Import every ``samples/`` module and return ``screen id → its variants``."""
    from src.bot.screens import samples

    for module in pkgutil.iter_modules(samples.__path__):
        importlib.import_module(f"{samples.__name__}.{module.name}")
    by_id: dict[str, list[Sample]] = {}
    for item in _REGISTRY.values():
        by_id.setdefault(item.screen_id, []).append(item)
    return by_id
