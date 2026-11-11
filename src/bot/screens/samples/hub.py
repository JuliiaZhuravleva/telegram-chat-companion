"""Samples of the hub area: the hub, requests and rejected chats, status, costs, settings.

One sample per state that changes the rows (a pager, an empty list, a request
switch that is off) and a few that only change the text where the text is the
point (a failed check, an OpenAI refusal). Every value is invented
(``fake.py``); chat titles, names and quoted messages are listed as user data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from src.bot.screens.hub import (
    build_hub,
    forget_ask_screen,
    off_ask_screen,
    rejected_screen,
    requests_screen,
    sample_hub,
)
from src.bot.screens.i18n import Lang
from src.bot.screens.model import Screen
from src.bot.screens.registry import sample
from src.bot.screens.samples import fake
from src.bot.screens.sections import SECTIONS, Section
from src.bot.screens.settings import (
    SECTION_NAMES,
    defs_screen,
    defs_section_screen,
    lang_screen,
    notif_screen,
    settings_screen,
)
from src.bot.screens.status import (
    costs_error_screen,
    costs_missing_screen,
    costs_screen,
    costs_verify_screen,
    stats_screen,
    status_screen,
)
from src.bot.settings_fields import new_fields
from src.models.chat_config import ChatConfig

# A fixed clock, so the status line's time does not move between two dumps.
_NOW: Final = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
_CHECKED: Final = datetime(2026, 1, 15, 11, 55, tzinfo=UTC)

_SAID_RU: Final = "Привет, бот, добавь нас"
_SAID_EN: Final = "Hello bot, please add us"
_PEOPLE: Final = (
    fake.CHAT_TITLE,
    fake.CHAT_TITLE_EN,
    fake.CHAT_TITLE_OTHER,
    fake.PERSON,
    _SAID_RU,
    _SAID_EN,
)

# Issues exactly as ``health_log.issues`` stores them: the checker's English
# message, its key, the provider's words and ``diagnosis``'s advice. The
# screen says them in the admin's language; only the provider text is shown as
# it came. Written out rather than raised through the checker because its
# message measures "holding since" against the real clock.
_CREDITS_ERROR: Final = (
    "gemini: 429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted. "
    "Please go to AI Studio at https://ai.studio/projects to manage your project and billing."
)


def _attempt(lang: Lang, *, title: str | None = None) -> dict[str, Any]:
    return {
        "id": fake.ATTEMPT_ID,
        "chat_id": fake.CHAT_ID,
        "chat_title": title or (fake.CHAT_TITLE_EN if lang == "en" else fake.CHAT_TITLE),
        "chat_type": "supergroup",
        "user_id": fake.USER_ID,
        "user_first_name": fake.PERSON,
        "user_username": fake.USERNAME.lstrip("@"),
        "message_text": _SAID_EN if lang == "en" else _SAID_RU,
    }


# ---------------------------------------------------------------------------
# hub
# ---------------------------------------------------------------------------


@sample("hub", section="Панель", title="Один запрос доступа ждёт", nav="root")
def hub_pending(lang: Lang) -> Screen:
    return sample_hub(lang)


@sample("hub", section="Панель", title="Запросов доступа нет", nav="root")
def hub_quiet(lang: Lang) -> Screen:
    return build_hub(lang, health={"status": "healthy"}, spend=Decimal("0.0031"), pending=0)


@sample("hub", section="Панель", title="Последняя проверка нашла сбой", nav="root")
def hub_bad(lang: Lang) -> Screen:
    return build_hub(lang, health={"status": "critical"}, spend=Decimal("1.27"), pending=3)


@sample("hub", section="Панель", title="Запросов доступа больше сотни", nav="root")
def hub_many(lang: Lang) -> Screen:
    return build_hub(lang, health={"status": "healthy"}, spend=Decimal("0.0031"), pending=120)


@sample("hub", section="Панель", title="База не ответила", nav="root")
def hub_unknown(lang: Lang) -> Screen:
    return build_hub(lang, known=False)


# ---------------------------------------------------------------------------
# requests, rejected, rejected.forget_ask, chat.off_ask
# ---------------------------------------------------------------------------


@sample(
    "requests", section="Чаты", title="Несколько запросов доступа", nav="panel", user_data=_PEOPLE
)
def requests_many(lang: Lang) -> Screen:
    return requests_screen(lang, _attempt(lang), 0, 3)


@sample("requests", section="Чаты", title="Один запрос доступа", nav="panel", user_data=_PEOPLE)
def requests_one(lang: Lang) -> Screen:
    return requests_screen(lang, _attempt(lang), 0, 1)


@sample("requests", section="Чаты", title="Следующая после отказа", nav="panel", user_data=_PEOPLE)
def requests_after_reject(lang: Lang) -> Screen:
    from src.bot.screens.hub import REJECTED

    return requests_screen(lang, _attempt(lang), 1, 2, notice=REJECTED(lang))


@sample("requests", section="Чаты", title="Запросов доступа нет", nav="panel")
def requests_empty(lang: Lang) -> Screen:
    return requests_screen(lang, None, 0, 0)


@sample("rejected", section="Чаты", title="Несколько отклонённых", nav="panel", user_data=_PEOPLE)
def rejected_many(lang: Lang) -> Screen:
    return rejected_screen(lang, _attempt(lang, title=fake.CHAT_TITLE_OTHER), 0, 2)


@sample("rejected", section="Чаты", title="Отклонённых нет", nav="panel")
def rejected_empty(lang: Lang) -> Screen:
    return rejected_screen(lang, None, 0, 0)


@sample("rejected", section="Чаты", title="Возвращён в запросы", nav="panel", user_data=_PEOPLE)
def rejected_after_restore(lang: Lang) -> Screen:
    """What `handle_wl_restore` shows: the next rejected chat, and the requests one tap away."""
    from src.bot.screens.hub import RESTORED

    attempt = _attempt(lang, title=fake.CHAT_TITLE_OTHER)
    return rejected_screen(lang, attempt, 0, 2, notice=RESTORED(lang), requests=12)


@sample(
    "rejected.forget_ask",
    section="Чаты",
    title="Забыть отказ",
    nav="prompt",
    user_data=_PEOPLE,
)
def forget_ask(lang: Lang) -> Screen:
    return forget_ask_screen(lang, _attempt(lang, title=fake.CHAT_TITLE_OTHER), 0)


@sample("chat.off_ask", section="Чаты", title="Отключить бота", nav="prompt", user_data=_PEOPLE)
def off_ask(lang: Lang) -> Screen:
    return off_ask_screen(lang, fake.CHAT_ID, fake.CHAT_TITLE, 0)


@sample(
    "chat.off_ask",
    section="Чаты",
    title="Отключить бота: чат когда-то отклоняли",
    nav="prompt",
    user_data=_PEOPLE,
)
def off_ask_rejected(lang: Lang) -> Screen:
    return off_ask_screen(lang, fake.CHAT_ID, fake.CHAT_TITLE, 0, rejected=True)


@sample(
    "chat.off_ask",
    section="Чаты",
    title="Отключить бота: канал",
    nav="prompt",
    user_data=_PEOPLE,
)
def off_ask_channel(lang: Lang) -> Screen:
    return off_ask_screen(lang, fake.CHAT_ID, fake.CHAT_TITLE, 0, channel=True)


@sample(
    "chat.off_ask",
    section="Чаты",
    title="Отключить бота: личный чат администратора",
    nav="prompt",
    user_data=_PEOPLE,
)
def off_ask_admin_dm(lang: Lang) -> Screen:
    return off_ask_screen(lang, fake.USER_ID, fake.PERSON, 0, admin_dm=True)


@sample(
    "chat.off_ask",
    section="Чаты",
    title="Бот уже отключён",
    nav="panel",
    user_data=_PEOPLE,
)
def off_ask_already_off(lang: Lang) -> Screen:
    return off_ask_screen(lang, fake.CHAT_ID, fake.CHAT_TITLE, 0, already_off=True)


@sample(
    "chat.off_ask",
    section="Чаты",
    title="Уже отключён: личный чат администратора",
    nav="panel",
    user_data=_PEOPLE,
)
def off_ask_admin_dm_already_off(lang: Lang) -> Screen:
    return off_ask_screen(lang, fake.USER_ID, fake.PERSON, 0, admin_dm=True, already_off=True)


# ---------------------------------------------------------------------------
# status, status.bad, stats
# ---------------------------------------------------------------------------


def _health(status: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": status,
        "checked_at": _CHECKED,
        "db_ok": True,
        "messages_30m": 17,
        "fallbacks_15m": 0,
        "issues": [],
    }
    row.update(extra)
    return row


@sample("status", section="Состояние", title="Всё работает", nav="panel")
def status_healthy(lang: Lang) -> Screen:
    return status_screen(lang, _health("healthy"), now=_NOW, board=True)


@sample("status", section="Состояние", title="Проверок не было", nav="panel")
def status_none(lang: Lang) -> Screen:
    return status_screen(lang, None, now=_NOW)


@sample("status.bad", section="Состояние", title="Предупреждение с советом", nav="panel")
def status_warning(lang: Lang) -> Screen:
    from src.services.health.checker import _issue_row, fallback_issue
    from src.services.health.diagnosis import hint_for

    hint = hint_for(_CREDITS_ERROR)
    failure = {
        "severity": "warning",
        "message": (
            "AI calls failed outright in last 15 min: embeddings x12 (gemini), "
            "holding since 15.01 09:50 UTC (2h 5m)"
        ),
        "key": f"ai_failure:embeddings:RateLimitError:{hint}",
        "detail": _CREDITS_ERROR,
        "hint": hint,
    }
    issues = [_issue_row(fallback_issue(3)), failure]
    return status_screen(lang, _health("warning", issues=issues, fallbacks_15m=3), now=_NOW)


@sample("status.bad", section="Состояние", title="Сбой, база недоступна", nav="panel")
def status_critical(lang: Lang) -> Screen:
    from src.services.health.checker import _issue_row, db_issue

    issues = [_issue_row(db_issue("connection"))]
    row = _health("critical", issues=issues, db_ok=False, messages_30m=0, fallbacks_15m=0)
    return status_screen(lang, row, now=_NOW)


@sample("stats", section="Состояние", title="За сутки", nav="panel")
def stats(lang: Lang) -> Screen:
    return stats_screen(lang, "24h", enabled=4, active=3, messages=812, responses=64, unauth=2)


# ---------------------------------------------------------------------------
# costs and the check against OpenAI
# ---------------------------------------------------------------------------


@sample("costs", section="Состояние", title="С вызовами", nav="panel")
def costs_full(lang: Lang) -> Screen:
    by_model = [
        {"model": "gpt-6-luna", "total_cost": Decimal("0.0213"), "call_count": 41},
        {"model": "gemini-3-flash", "total_cost": Decimal("0.0102"), "call_count": 18},
        {"model": "gpt-4o-mini-transcribe", "total_cost": Decimal("0.0090"), "call_count": 3},
    ]
    by_task = [
        {"task_type": "text", "total_cost": Decimal("0.0213"), "call_count": 41},
        {"task_type": "vision", "total_cost": Decimal("0.0102"), "call_count": 18},
        {"task_type": "transcription", "total_cost": Decimal("0.0090"), "call_count": 3},
    ]
    return costs_screen(lang, "24h", total=Decimal("0.0405"), by_model=by_model, by_task=by_task)


@sample("costs", section="Состояние", title="Вызовов не было", nav="panel")
def costs_empty(lang: Lang) -> Screen:
    return costs_screen(lang, "1h", total=Decimal(0), by_model=[], by_task=[])


@sample("costs.verify", section="Состояние", title="Сверка по проекту", nav="panel")
def costs_verify_scoped(lang: Lang) -> Screen:
    return costs_verify_screen(
        lang,
        "24h",
        window_h=24,
        reported=Decimal("0.0412"),
        ours=Decimal("0.0405"),
        project_id="proj_test",
        scoped=True,
    )


@sample("costs.verify", section="Состояние", title="Без разбивки по проектам", nav="panel")
def costs_verify_unscoped(lang: Lang) -> Screen:
    return costs_verify_screen(
        lang,
        "7d",
        window_h=168,
        reported=Decimal("0.31"),
        ours=Decimal("0.29"),
        project_id="proj_test",
        scoped=False,
    )


@sample("costs.missing", section="Состояние", title="Нет обеих настроек", nav="panel")
def costs_missing(lang: Lang) -> Screen:
    return costs_missing_screen(lang, "24h", ["OPENAI_ADMIN_API_KEY", "OPENAI_PROJECT_ID"])


@sample("costs.error", section="Состояние", title="Ключ не админский", nav="panel")
def costs_error_mapped(lang: Lang) -> Screen:
    return costs_error_screen(lang, "24h", code="no_billing_access", error="HTTP 403")


@sample("costs.error", section="Состояние", title="Ответ OpenAI как есть", nav="panel")
def costs_error_raw(lang: Lang) -> Screen:
    return costs_error_screen(lang, "24h", code="http_error", error="OpenAI returned HTTP 502")


@sample("costs.error", section="Состояние", title="Непредвиденная ошибка", nav="panel")
def costs_error_unexpected(lang: Lang) -> Screen:
    return costs_error_screen(lang, "24h", code="unexpected", error="Unexpected error")


@sample("costs.error", section="Состояние", title="Свои расходы не прочитаны", nav="panel")
def costs_error_ours(lang: Lang) -> Screen:
    return costs_error_screen(lang, "24h", code="ours_failed", error="Own costs could not be read")


# ---------------------------------------------------------------------------
# settings, notif, lang, defs, defs.section
# ---------------------------------------------------------------------------


@sample("settings", section="Настройки", title="Общие настройки", nav="panel")
def settings(lang: Lang) -> Screen:
    return settings_screen(lang)


@sample("notif", section="Настройки", title="Запросы доступа приходят", nav="panel")
def notif_on(lang: Lang) -> Screen:
    return notif_screen(lang, {"sticker": "detailed", "unauthorized": True})


@sample("notif", section="Настройки", title="Запросы доступа не присылаются", nav="panel")
def notif_requests_off(lang: Lang) -> Screen:
    return notif_screen(lang, {"sticker": "off", "unauthorized": False})


@sample("lang", section="Настройки", title="Язык панели", nav="panel")
def lang(lang: Lang) -> Screen:
    return lang_screen(lang)


@sample("defs", section="Настройки", title="Семь разделов", nav="panel")
def defs(lang: Lang) -> Screen:
    return defs_screen(lang)


def _defaults() -> dict[str, object]:
    base = ChatConfig(chat_id=0)
    values: dict[str, object] = {f.key: getattr(base, f.key) for f in new_fields()}
    # One switched on, so a section shows both glyphs.
    values["community_enabled"] = True
    values["greet_on_first_message"] = True
    return values


def _register_section(section: Section) -> None:
    title = f"Раздел: {SECTION_NAMES[section][1]('ru')}"

    def build(lang: Lang) -> Screen:
        return defs_section_screen(lang, section, _defaults())

    build.__qualname__ = f"defs_section_{section}"
    sample("defs.section", section="Настройки", title=title, nav="panel")(build)


for _section in SECTIONS:
    _register_section(_section)
