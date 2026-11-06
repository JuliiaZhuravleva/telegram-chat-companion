"""The admin panel's root screen (``hub``) and its one delivery path, `show_hub`.

Created by the foundation with TODAY's look — nine rows, the same labels and
payloads ``keyboards.admin.main_menu_keyboard`` builds — so the stale fallback
and every caller have a stable entry before the hub itself is redesigned.
The hub area owns this file from here on; `show_hub`'s signature is shared.

`sample_hub` and `SAMPLE_HUB_EXEMPT` are shared too. The stale fallback's
registered sample (``samples/foundation.py``) is the hub under one extra line,
so it draws whatever `sample_hub` returns and carries whatever exemptions this
module declares. Whoever changes the hub changes both here, in the same file,
and the stale sample follows without an edit to the foundation's module.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from aiogram.types import CallbackQuery, Message

from src.bot.screens.i18n import Tr
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import show
from src.bot.screens.routes import cb

TITLE = Tr("<b>Панель администратора</b>", "<b>Admin Panel</b>")

_WHITELIST = Tr("📋 Whitelist", "📋 Whitelist")
_RULES = Tr("📏 Правила", "📏 Rules")
_STICKERS = Tr("🎨 Стикеры", "🎨 Stickers")
_DEFAULTS = Tr("🌍 Глобальные настройки", "🌍 Global settings")
_STATISTICS = Tr("📊 Статистика", "📊 Statistics")
_COSTS = Tr("💰 Расходы", "💰 Costs")
_HEALTH = Tr("💚 Здоровье", "💚 Health")
_NOTIFICATIONS = Tr("🔔 Уведомления", "🔔 Notifications")
_KB = Tr("📚 База знаний", "📚 Knowledge Base")
_REACTIONS = Tr("😀 Реакции", "😀 Reactions")
_CHAT_PANEL = Tr("⚙️ Настройки чата", "⚙️ Chat settings")
_LANGUAGE = Tr("🌐 Язык / Language", "🌐 Language / Язык")
_CLOSE = Tr("✖️ Закрыть", "✖️ Close")


def build_hub(lang: str, *, notice: str | None = None, notice_raw: str | None = None) -> Screen:
    """Today's hub as a `Screen`.

    The four retired entries (whitelist, rules, knowledge base, reactions) are
    written as raw strings: `cb()` refuses retired prefixes, and these buttons
    are exactly the ones the redesigned hub drops.
    """
    lg = "en" if lang == "en" else "ru"
    return Screen(
        id="hub",
        text=TITLE(lg),
        notice=notice,
        notice_raw=notice_raw,
        rows=(
            (
                Button(_WHITELIST(lg), callback=f"adm_wl:{lg}"),
                Button(_RULES(lg), callback=f"adm_rules:{lg}:0"),
            ),
            (
                Button(_STICKERS(lg), callback=cb("adm_stk_sets:", lg, 0)),
                Button(_DEFAULTS(lg), callback=cb("adm_defs:", lg, 0)),
            ),
            (
                Button(_STATISTICS(lg), callback=cb("adm_stats:", lg, "24h")),
                Button(_COSTS(lg), callback=cb("adm_costs:", lg, "24h")),
            ),
            (Button(_HEALTH(lg), callback=cb("adm_health:", lg)),),
            (Button(_NOTIFICATIONS(lg), callback=cb("adm_notif:", lg)),),
            (
                Button(_KB(lg), callback=f"adm_kb:{lg}:0"),
                Button(_REACTIONS(lg), callback=f"adm_react:{lg}:0"),
            ),
            (Button(_CHAT_PANEL(lg), callback=cb("adm_pnl:", lg, 0)),),
            (Button(_LANGUAGE(lg), callback=cb("adm_lang:", lg)),),
            (Button(_CLOSE(lg), callback=cb("adm_close:", lg), kind="exit"),),
        ),
    )


def sample_hub(lang: str) -> Screen:
    """The hub as a sample draws it: from invented data, nothing read from a database.

    Today's hub reads nothing, so this is `build_hub`. A hub that grows a
    status line builds it here from ``samples/fake.py`` values instead.
    """
    return build_hub(lang)


_TODAYS_HUB = "today's hub, kept as it is until the hub is redesigned"

SAMPLE_HUB_EXEMPT: Final[Mapping[str, str]] = MappingProxyType(
    {
        "R2": f"nine rows: {_TODAYS_HUB}",
        "R5": f"the title has no emoji: {_TODAYS_HUB}",
        "R7": (
            "Whitelist, Rules, Knowledge base and Reactions write the retired "
            f"adm_wl:, adm_rules:, adm_kb:, adm_react: — {_TODAYS_HUB}"
        ),
        "R9": f"'Whitelist' and 'Language / Язык' mix languages: {_TODAYS_HUB}",
        "R10": f"'Глобальные настройки' truncates on a phone: {_TODAYS_HUB}",
    }
)
"""The rules `sample_hub` breaks, each with its reason (`registry.make_sample`'s
``exempt``). ``test_every_exemption_is_needed`` fails on one that stops firing,
so the hub's redesign empties this in the same change that fixes the screen."""


async def show_hub(
    target: CallbackQuery | Message,
    lang: str,
    *,
    notice: str | None = None,
    notice_raw: str | None = None,
    data: Mapping[str, Any],  # noqa: ARG001 — handler kwargs; the hub's status line will read repos from it
) -> Message | None:
    """Show the hub: edit in place for a button, a new message for a command.

    ``data`` is the calling handler's ``**kwargs`` (``dishka_container`` and
    friends): the stable signature lets the hub's text grow a status line
    without changing a single caller.
    """
    return await show(target, build_hub(lang, notice=notice, notice_raw=notice_raw))
