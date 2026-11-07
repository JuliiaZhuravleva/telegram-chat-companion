"""Status, statistics and costs: the screens behind the hub's "📊 Состояние".

Pure builders over data a handler has already read — nothing here touches a
database, the network or aiogram — so a sample can draw every state from
invented values and the handler stays a thin "read, then `show()`".

``status`` shows the LAST recorded health check; only its "🔁 Обновить" button
runs a new one (the hub reads the same row, so opening the panel never costs a
check). A check that found a warning or a failure is its own screen id,
``status.bad``: the problems lead the text, the counters follow.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape
from typing import Any, Final

from src.bot.screens.cards import hint_line, stored_issue_line
from src.bot.screens.i18n import Tr, lang_of
from src.bot.screens.model import Button, Screen
from src.bot.screens.render import back
from src.bot.screens.routes import cb
from src.utils.display_tz import DISPLAY_TZ

# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

PERIODS: Final = ("1h", "24h", "7d")

INTERVALS: Final[Mapping[str, timedelta]] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}

PERIOD_TITLES: Final[Mapping[str, Tr]] = {
    "1h": Tr("за 1 час", "last 1 hour"),
    "24h": Tr("за 24 часа", "last 24 hours"),
    "7d": Tr("за 7 дней", "last 7 days"),
}

_PERIOD_BUTTONS: Final[Mapping[str, Tr]] = {
    "1h": Tr("1 час", "1 hour"),
    "24h": Tr("24 часа", "24 hours"),
    "7d": Tr("7 дней", "7 days"),
}


def _period_row(prefix: str, lang: str, current: str) -> tuple[Button, ...]:
    """``1 час · • 24 часа · 7 дней``: the current one carries a dot, all three re-render."""
    return tuple(
        Button(
            f"• {_PERIOD_BUTTONS[p](lang)}" if p == current else _PERIOD_BUTTONS[p](lang),
            callback=cb(prefix, lang, p),
        )
        for p in PERIODS
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

_STATUS_TITLE = Tr("Состояние", "Status")

VERDICTS: Final[Mapping[str, tuple[str, Tr]]] = {
    "healthy": ("💚", Tr("Всё работает", "All good")),
    "warning": ("⚠️", Tr("Есть предупреждение", "There is a warning")),
    "critical": ("🔥", Tr("Есть сбой", "Something is failing")),
    "skipped": ("⏭", Tr("Проверка пропущена", "Check skipped")),
}
_UNKNOWN_VERDICT: Final = ("❔", Tr("Состояние неизвестно", "Status unknown"))
_NO_CHECKS = Tr("Проверок пока не было", "No checks yet")
BAD: Final = frozenset({"warning", "critical"})

_DB_OK = Tr("✓ База данных", "✓ Database")
_DB_DOWN = Tr("✖️ База данных недоступна", "✖️ Database unreachable")
_MESSAGES_30M = Tr("💬 Сообщений за 30 минут: {n}", "💬 Messages in 30 minutes: {n}")
_FALLBACKS_15M = Tr("🛟 Запасной ИИ за 15 минут: {n}", "🛟 Fallback AI in 15 minutes: {n}")

_REFRESH = Tr("🔁 Обновить", "🔁 Refresh")
_STATS = Tr("📈 Статистика", "📈 Statistics")
_COSTS = Tr("💰 Расходы", "💰 Costs")


def verdict(row: Mapping[str, Any] | None, lang: str) -> str:
    """``💚 Всё работает`` for a health_log row; the no-data wording for None."""
    if row is None:
        return f"❔ {_NO_CHECKS(lang)}"
    icon, words = VERDICTS.get(str(row.get("status", "")).lower(), _UNKNOWN_VERDICT)
    return f"{icon} {words(lang)}"


def _issues(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = row.get("issues") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    return [issue for issue in raw if isinstance(issue, Mapping)] if isinstance(raw, list) else []


def _when(moment: datetime, now: datetime) -> str:
    """``14:00`` today, ``03.11 14:00`` on another day — in the bot's display zone."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(DISPLAY_TZ)
    today = now.astimezone(DISPLAY_TZ).date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%d.%m %H:%M")


def health_text(row: Mapping[str, Any] | None, lang: str, *, now: datetime | None = None) -> str:
    """The status screen's whole text.

    Every issue carries the provider's own error (``detail``) and the advice
    (``hint``) the alert carried: the panel is what someone opens when they are
    not sure the alert is still true, so it must not say less than the alert.
    Rows written before those fields existed render with the message alone.

    The problem line and the advice are said in the admin's language, with the
    card's words (``cards.stored_issue_line`` / ``hint_line``): the row keeps
    the checker's English message and ``diagnosis``'s Russian advice, and must
    keep them, because de-duplication reads the same row. A kind the screen
    does not know shows its stored text.
    """
    title = f"📊 <b>{_STATUS_TITLE(lang)}</b>"
    if row is None:
        return f"{title}\n\n{_NO_CHECKS(lang)}."
    checked_at = row.get("checked_at")
    if isinstance(checked_at, datetime):
        title += f" · {_when(checked_at, now or datetime.now(UTC))}"

    icon, words = VERDICTS.get(str(row.get("status", "")).lower(), _UNKNOWN_VERDICT)
    lines = [title, "", f"{icon} <b>{words(lang)}</b>"]
    for issue in _issues(row):
        mark = "🔥" if str(issue.get("severity", "warning")) == "critical" else "⚠️"
        lines.append(f"{mark} {escape(stored_issue_line(lang_of(lang), issue))}")
        if issue.get("detail"):
            lines.append(f"     {escape(str(issue['detail']))}")
        hint = hint_line(lang_of(lang), str(issue["hint"])) if issue.get("hint") else None
        if hint:
            lines.append(f"     💡 {escape(hint)}")
    lines += [
        "",
        _DB_OK(lang) if row.get("db_ok", True) else _DB_DOWN(lang),
        _MESSAGES_30M(lang).format(n=int(row.get("messages_30m") or 0)),
        _FALLBACKS_15M(lang).format(n=int(row.get("fallbacks_15m") or 0)),
    ]
    return "\n".join(lines)


def status_screen(
    lang: str, row: Mapping[str, Any] | None, *, now: datetime | None = None
) -> Screen:
    """``status``, or ``status.bad`` when the last check found a warning or a failure."""
    bad = row is not None and str(row.get("status", "")).lower() in BAD
    return Screen(
        id="status.bad" if bad else "status",
        text=health_text(row, lang, now=now),
        rows=(
            (Button(_REFRESH(lang), callback=cb("adm_health:", lang, "now")),),
            (
                Button(_STATS(lang), callback=cb("adm_stats:", lang, "24h")),
                Button(_COSTS(lang), callback=cb("adm_costs:", lang, "24h")),
            ),
            (back("hub", cb("adm_menu:", lang), lang),),
        ),
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

_STATS_TITLE = Tr("Статистика", "Statistics")
_STATS_LINES = Tr(
    "Подключено чатов: {enabled}\n"
    "Активных: {active}\n"
    "Сообщений: {messages}\n"
    "Ответов бота: {responses}\n"
    "Сообщений из чужих чатов: {unauth}",
    "Chats connected: {enabled}\n"
    "Active: {active}\n"
    "Messages: {messages}\n"
    "Bot replies: {responses}\n"
    "Messages from other chats: {unauth}",
)


def stats_screen(
    lang: str,
    period: str,
    *,
    enabled: int,
    active: int,
    messages: int,
    responses: int,
    unauth: int,
) -> Screen:
    body = _STATS_LINES(lang).format(
        enabled=enabled, active=active, messages=messages, responses=responses, unauth=unauth
    )
    return Screen(
        id="stats",
        text=f"📈 <b>{_STATS_TITLE(lang)}</b> · {PERIOD_TITLES[period](lang)}\n\n{body}",
        rows=(
            _period_row("adm_stats:", lang, period),
            (back("status", cb("adm_health:", lang), lang),),
        ),
    )


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------

_COSTS_TITLE = Tr("Расходы", "Costs")
_TOTAL = Tr("Итого", "Total")
_NO_CALLS = Tr("Вызовов не было.", "No calls.")
_CALLS_NOTE = Tr("<i>В скобках: число вызовов.</i>", "<i>In brackets: number of calls.</i>")
_VERIFY = Tr("🔍 Сверить с OpenAI", "🔍 Check against OpenAI")
MODEL_LINES: Final = 8
"""The model breakdown is capped: a long tail of one-call models says nothing."""

TASK_LABELS: Final[Mapping[str, Tr]] = {
    "text": Tr("Текст", "Text"),
    "embedding": Tr("Эмбеддинги", "Embeddings"),
    "vision": Tr("Зрение", "Vision"),
    "transcription": Tr("Транскрипция", "Transcription"),
    "community": Tr("Сообщество", "Community"),
}


def _usd(value: object) -> str:
    return f"${Decimal(str(value or 0)):.4f}"


def costs_screen(
    lang: str,
    period: str,
    *,
    total: Decimal,
    by_model: Sequence[Mapping[str, Any]],
    by_task: Sequence[Mapping[str, Any]],
) -> Screen:
    lines = [
        f"💰 <b>{_COSTS_TITLE(lang)}</b> · {PERIOD_TITLES[period](lang)}",
        "",
        f"<b>{_TOTAL(lang)}: {_usd(total)}</b>",
    ]
    if by_model:
        lines.append("")
        lines += [
            f"{escape(str(row.get('model') or 'unknown'))}: "
            f"{_usd(row.get('total_cost'))} ({int(row.get('call_count') or 0)})"
            for row in by_model[:MODEL_LINES]
        ]
    if by_task:
        lines.append("")
        for row in by_task:
            task = str(row.get("task_type") or "text")
            label = TASK_LABELS[task](lang) if task in TASK_LABELS else escape(task)
            lines.append(
                f"{label}: {_usd(row.get('total_cost'))} ({int(row.get('call_count') or 0)})"
            )
    lines += ["", _CALLS_NOTE(lang) if (by_model or by_task) else _NO_CALLS(lang)]
    return Screen(
        id="costs",
        text="\n".join(lines),
        rows=(
            _period_row("adm_costs:", lang, period),
            (Button(_VERIFY(lang), callback=cb("adm_costs_verify:", lang, period)),),
            (back("status", cb("adm_health:", lang), lang),),
        ),
    )


# ---------------------------------------------------------------------------
# Checking the costs against OpenAI's own report
# ---------------------------------------------------------------------------

_VERIFY_TITLE = Tr("Сверка с OpenAI", "Check against OpenAI")
_VERIFY_AGAIN = Tr("🔁 Сверить ещё раз", "🔁 Check again")
_RETRY = Tr("🔁 Повторить", "🔁 Try again")
_WINDOW = Tr("Окно: последние {h} ч", "Window: last {h}h")
_REPORTED = Tr("По отчёту OpenAI: {v}", "OpenAI reported: {v}")
_OURS = Tr("По нашему счёту: {v}", "Our calculation: {v}")
_DELTA = Tr("Разница: {v}", "Delta: {v}")
_SCOPED = Tr(
    "<i>Только проект <code>{project}</code>.</i>",
    "<i>Scoped to project <code>{project}</code>.</i>",
)
_UNSCOPED = Tr(
    "<i>OpenAI не вернул разбивку по проектам — принадлежность цифры к проекту не подтверждена.</i>",
    "<i>OpenAI returned no per-project breakdown — the figure's scoping could not be confirmed.</i>",
)

ADMIN_KEYS_URL: Final = "https://platform.openai.com/settings/organization/admin-keys"
PROJECTS_URL: Final = "https://platform.openai.com/settings/organization/projects"

MISSING_INTRO = Tr(
    "Сверка требует двух настроек, не хватает:",
    "Cost verification needs two settings; missing:",
)
MISSING_SETTING: Final[Mapping[str, Tr]] = {
    # Both are required and neither substitutes for the other: the key buys
    # access, the project id makes the answer about this bot. Named separately so
    # a half-finished setup says which half is missing.
    "OPENAI_ADMIN_API_KEY": Tr(
        "<code>OPENAI_ADMIN_API_KEY</code> — отдельный ключ организации, "
        "а не тот, которым бот генерирует ответы: биллинговые эндпоинты "
        f'принимают только его. <a href="{ADMIN_KEYS_URL}">Создать</a> '
        "(доступно только владельцу организации).",
        "<code>OPENAI_ADMIN_API_KEY</code> — a separate organization-level "
        "key, not the one the bot generates replies with: the billing "
        f'endpoints accept only an admin key. <a href="{ADMIN_KEYS_URL}">'
        "Create one</a> (organization owners only).",
    ),
    "OPENAI_PROJECT_ID": Tr(
        "<code>OPENAI_PROJECT_ID</code> — проект, по которому считать "
        "расходы (<code>proj_…</code>). Без него OpenAI отвечает по всей "
        f'организации. <a href="{PROJECTS_URL}">Посмотреть проекты</a>.',
        "<code>OPENAI_PROJECT_ID</code> — the project to report on "
        "(<code>proj_…</code>). Without it OpenAI answers organization-wide. "
        f'<a href="{PROJECTS_URL}">View projects</a>.',
    ),
}

BILLING_ERRORS: Final[Mapping[str, Tr]] = {
    # Localised text per OpenAICostReport.error_code, plus the handler's own
    # "ours_failed". Anything unmapped falls back to the client's own English
    # message. No entry names an exception: the class goes to the log.
    "unexpected": Tr(
        "Сверка не удалась из-за непредвиденной ошибки. "
        "Можно повторить, подробности — в логах бота.",
        "The check failed with an unexpected error. Try again; details are in the bot logs.",
    ),
    "ours_failed": Tr(
        "OpenAI ответил, но посчитать наши расходы за тот же период не удалось. "
        "Можно повторить, подробности — в логах бота.",
        "OpenAI answered, but our own costs for the same period could not be read. "
        "Try again; details are in the bot logs.",
    ),
    "no_billing_access": Tr(
        "OpenAI ответил 403: ключ есть, но он не админский ключ организации. "
        f'Нужен ключ отсюда: <a href="{ADMIN_KEYS_URL}">admin keys</a>.',
        "OpenAI returned 403: the key is valid but is not an organization "
        f'admin key. Create one at <a href="{ADMIN_KEYS_URL}">admin keys</a>.',
    ),
    "invalid_key": Tr(
        "OpenAI ответил 401: ключ не принят — недействителен или отозван.",
        "OpenAI returned 401: the key was rejected — invalid or revoked.",
    ),
    "timeout": Tr(
        "OpenAI не ответил вовремя. Можно повторить.",
        "OpenAI did not respond in time. Try again.",
    ),
    "pagination_stuck": Tr(
        "OpenAI не закончил постраничную выдачу — итог был бы завышен, "
        "поэтому сверка отменена. Подробности в логах бота.",
        "OpenAI never finished paginating — the total would be inflated, "
        "so the check was aborted. Details are in the bot logs.",
    ),
    "project_filter_ignored": Tr(
        "OpenAI вернул данные по другим проектам — фильтр не применился. "
        "Цифры были бы по всей организации, поэтому сверка отменена. "
        "Стоит проверить <code>OPENAI_PROJECT_ID</code> в "
        f'<a href="{PROJECTS_URL}">списке проектов</a>.',
        "OpenAI returned data for other projects — the filter did not apply. "
        "The figures would be organization-wide, so the check was aborted. "
        "Verify <code>OPENAI_PROJECT_ID</code> against the "
        f'<a href="{PROJECTS_URL}">project list</a>.',
    ),
}


def _verify_title(lang: str, period: str | None = None) -> str:
    title = f"💰 <b>{_VERIFY_TITLE(lang)}</b>"
    return f"{title} · {PERIOD_TITLES[period](lang)}" if period else title


def _back_to_costs(lang: str, period: str) -> tuple[Button, ...]:
    return (back("costs", cb("adm_costs:", lang, period), lang),)


def costs_verify_screen(
    lang: str,
    period: str,
    *,
    window_h: int,
    reported: Decimal,
    ours: Decimal,
    project_id: str,
    scoped: bool,
) -> Screen:
    """Both figures over the window OpenAI's buckets actually covered.

    ``scoped`` is False when OpenAI's answer carried no project breakdown: the
    filter was requested but could not be confirmed, and the screen says so
    rather than presenting an unverified figure as a checked one.
    """
    note = _SCOPED(lang).format(project=escape(project_id)) if scoped else _UNSCOPED(lang)
    text = "\n".join(
        [
            _verify_title(lang, period),
            "",
            _WINDOW(lang).format(h=window_h),
            _REPORTED(lang).format(v=_usd(reported)),
            _OURS(lang).format(v=_usd(ours)),
            _DELTA(lang).format(v=_usd(reported - ours)),
            "",
            note,
        ]
    )
    return Screen(
        id="costs.verify",
        text=text,
        rows=(
            (Button(_VERIFY_AGAIN(lang), callback=cb("adm_costs_verify:", lang, period)),),
            _back_to_costs(lang, period),
        ),
    )


def costs_missing_screen(lang: str, period: str, missing: Sequence[str]) -> Screen:
    """Which of the two settings is absent — nothing is asked of OpenAI without both."""
    details = "\n\n".join(MISSING_SETTING[name](lang) for name in missing)
    return Screen(
        id="costs.missing",
        text=f"{_verify_title(lang)}\n\n{MISSING_INTRO(lang)}\n\n{details}",
        rows=(_back_to_costs(lang, period),),
    )


def costs_error_screen(lang: str, period: str, *, code: str | None, error: str) -> Screen:
    """OpenAI refused or failed: the localised reason, else its own words, escaped."""
    body = (
        BILLING_ERRORS[code](lang) if code is not None and code in BILLING_ERRORS else escape(error)
    )
    return Screen(
        id="costs.error",
        text=f"{_verify_title(lang)}\n\n{body}",
        rows=(
            (Button(_RETRY(lang), callback=cb("adm_costs_verify:", lang, period)),),
            _back_to_costs(lang, period),
        ),
    )
