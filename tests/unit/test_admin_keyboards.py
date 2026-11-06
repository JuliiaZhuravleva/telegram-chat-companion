"""Tests for the buttons of the hub area's screens.

The keyboards used to be built by hand in ``src/bot/keyboards/admin.py``; they
are now rows of the `Screen` builders in ``src/bot/screens/{hub,status,settings}.py``.
What is pinned here is what a tap does: the payload behind each button, the
period marker, the pager, and the payloads' length at the widest invented id.
The rules every screen shares (button widths, back buttons, notices) live in
``test_screen_rules.py``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.bot.screens.hub import (
    forget_ask_screen,
    off_ask_screen,
    rejected_screen,
    requests_screen,
)
from src.bot.screens.model import Screen
from src.bot.screens.settings import lang_screen, notif_screen, settings_screen
from src.bot.screens.status import (
    costs_error_screen,
    costs_missing_screen,
    costs_screen,
    costs_verify_screen,
    stats_screen,
    status_screen,
)

# The widest values a payload can carry: a 13-digit signed supergroup id and a
# large attempt id. Invented.
CHAT_ID = -1009999999999
ATTEMPT_ID = 2_147_483_647


def _attempt(**extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": ATTEMPT_ID,
        "chat_id": CHAT_ID,
        "chat_title": "Test Chat",
        "chat_type": "supergroup",
        "user_id": 555000111,
        "user_first_name": "Alice",
        "user_username": "alice",
        "message_text": "hello",
    }
    row.update(extra)
    return row


def _payloads(screen: Screen) -> list[str]:
    return [b.callback for row in screen.rows for b in row if b.callback]


def _labels(screen: Screen) -> list[str]:
    return [b.label for row in screen.rows for b in row]


def _stats(period: str = "24h", lang: str = "ru") -> Screen:
    return stats_screen(lang, period, enabled=1, active=1, messages=1, responses=1, unauth=0)


def _costs(period: str = "24h", lang: str = "ru") -> Screen:
    return costs_screen(lang, period, total=Decimal(0), by_model=[], by_task=[])


# ---------------------------------------------------------------------------
# Payload length: Telegram refuses callback_data over 64 bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "screen",
    [
        requests_screen("en", _attempt(), 98, 99),
        rejected_screen("en", _attempt(status="rejected"), 98, 99),
        forget_ask_screen("en", _attempt(), 98),
        off_ask_screen("en", CHAT_ID, "Test Chat", 98),
        _stats("7d", "en"),
        _costs("7d", "en"),
        costs_verify_screen(
            "en",
            "7d",
            window_h=168,
            reported=Decimal(1),
            ours=Decimal(1),
            project_id="proj_x",
            scoped=True,
        ),
        costs_missing_screen("en", "7d", ["OPENAI_ADMIN_API_KEY"]),
        costs_error_screen("en", "7d", code="http_error", error="HTTP 502"),
        status_screen("en", None),
        settings_screen("en"),
        notif_screen("en", {"sticker": "on", "unauthorized": True}),
        lang_screen("en"),
    ],
    ids=lambda s: s.id,
)
def test_every_payload_fits_in_64_bytes(screen: Screen):
    payloads = _payloads(screen)
    assert payloads  # positive control: the screen has buttons to measure
    for payload in payloads:
        assert len(payload.encode()) <= 64, payload


# ---------------------------------------------------------------------------
# requests and rejected: one per page
# ---------------------------------------------------------------------------


class TestRequests:
    def test_approve_and_reject_carry_the_attempt_and_the_page(self):
        screen = requests_screen("ru", _attempt(), 2, 5)

        assert screen.rows[0][0].callback == f"adm_wl_apr:ru:{ATTEMPT_ID}:2"
        assert screen.rows[0][0].style == "success"
        assert screen.rows[0][1].callback == f"adm_wl_rej:ru:{ATTEMPT_ID}:2"

    def test_pager_walks_the_requests(self):
        payloads = _payloads(requests_screen("ru", _attempt(), 2, 5))

        assert "adm_wl_pending:ru:1" in payloads
        assert "adm_wl_pending:ru:3" in payloads
        assert payloads[-1] == "adm_pnl:ru:0"

    def test_one_request_has_no_pager(self):
        payloads = _payloads(requests_screen("ru", _attempt(), 0, 1))

        assert not any(p.startswith("adm_wl_pending:") for p in payloads)

    def test_the_count_is_one_based(self):
        assert "Заявка 3 из 5" in requests_screen("ru", _attempt(), 2, 5).text
        assert "Request 1 of 1" in requests_screen("en", _attempt(), 0, 1).text

    def test_empty_is_only_back(self):
        assert _payloads(requests_screen("en", None, 0, 0)) == ["adm_pnl:en:0"]

    def test_a_title_is_escaped(self):
        screen = requests_screen("ru", _attempt(chat_title="<b>x</b> & y"), 0, 1)

        assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in screen.text


class TestRejected:
    def test_restore_and_forget_carry_the_attempt_and_the_page(self):
        screen = rejected_screen("en", _attempt(status="rejected"), 1, 3)

        assert screen.rows[0][0].callback == f"adm_wl_restore:en:{ATTEMPT_ID}:1"
        assert screen.rows[0][1].callback == f"adm_wl_del_ask:en:{ATTEMPT_ID}:1"
        payloads = _payloads(screen)
        assert "adm_wl_rejected:en:0" in payloads
        assert "adm_wl_rejected:en:2" in payloads

    def test_empty_is_only_back(self):
        assert _payloads(rejected_screen("ru", None, 0, 0)) == ["adm_pnl:ru:0"]

    def test_forget_ask_keeps_the_page_on_both_answers(self):
        screen = forget_ask_screen("ru", _attempt(), 4)

        assert _payloads(screen) == [f"adm_wl_del:ru:{ATTEMPT_ID}:4", "adm_wl_rejected:ru:4"]
        assert screen.rows[0][1].kind == "cancel"


class TestTurnOff:
    def test_off_keeps_the_chat_list_page_and_cancel_goes_back_to_the_chat(self):
        screen = off_ask_screen("en", CHAT_ID, "Test Chat", 3)

        assert _payloads(screen) == [f"adm_wl_rm:en:{CHAT_ID}:3", f"adm_pnl_menu:en:{CHAT_ID}"]
        assert screen.rows[0][1].kind == "cancel"

    def test_says_the_settings_are_kept_and_promises_nothing_else(self):
        text = off_ask_screen("ru", CHAT_ID, "Test Chat", 0).text

        assert "Настройки сохранятся" in text


# ---------------------------------------------------------------------------
# status, stats, costs
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.parametrize("row", [None, {"status": "healthy"}, {"status": "critical"}])
    def test_rows(self, row: dict[str, Any] | None):
        assert _payloads(status_screen("ru", row)) == [
            "adm_health:ru:now",
            "adm_stats:ru:24h",
            "adm_costs:ru:24h",
            "adm_menu:ru",
        ]

    def test_bad_check_changes_the_id(self):
        assert status_screen("en", {"status": "healthy"}).id == "status"
        assert status_screen("en", {"status": "warning"}).id == "status.bad"
        assert status_screen("en", {"status": "critical"}).id == "status.bad"
        assert status_screen("en", None).id == "status"


class TestPeriods:
    @pytest.mark.parametrize("period", ["1h", "24h", "7d"])
    def test_stats_marks_the_current_period(self, period: str):
        screen = _stats(period, "en")

        marked = [b.label for b in screen.rows[0] if b.label.startswith("• ")]
        assert len(marked) == 1
        assert _payloads(screen) == [
            "adm_stats:en:1h",
            "adm_stats:en:24h",
            "adm_stats:en:7d",
            "adm_health:en",
        ]

    def test_costs_rows(self):
        screen = _costs("7d", "ru")

        assert _payloads(screen) == [
            "adm_costs:ru:1h",
            "adm_costs:ru:24h",
            "adm_costs:ru:7d",
            "adm_costs_verify:ru:7d",
            "adm_health:ru",
        ]
        assert screen.rows[0][2].label == "• 7 дней"


class TestCostsText:
    def test_model_and_task_lines(self):
        screen = costs_screen(
            "en",
            "24h",
            total=Decimal("0.0405"),
            by_model=[{"model": "gpt-5-nano", "total_cost": Decimal("0.0213"), "call_count": 41}],
            by_task=[{"task_type": "vision", "total_cost": Decimal("0.0102"), "call_count": 18}],
        )

        assert "Total: $0.0405" in screen.text
        assert "gpt-5-nano: $0.0213 (41)" in screen.text
        assert "Vision: $0.0102 (18)" in screen.text

    def test_no_calls(self):
        assert "No calls." in _costs(lang="en").text

    def test_verify_keeps_the_window_and_the_unscoped_line(self):
        screen = costs_verify_screen(
            "ru",
            "7d",
            window_h=168,
            reported=Decimal("0.31"),
            ours=Decimal("0.29"),
            project_id="proj_test",
            scoped=False,
        )

        assert "Окно: последние 168 ч" in screen.text
        assert "не подтверждена" in screen.text


# ---------------------------------------------------------------------------
# settings, notif, lang
# ---------------------------------------------------------------------------


class TestSettings:
    def test_rows(self):
        assert _payloads(settings_screen("ru")) == [
            "adm_defs:ru:0",
            "adm_notif:ru",
            "adm_lang:ru",
            "adm_menu:ru",
        ]


class TestNotif:
    @pytest.mark.parametrize(
        ("mode", "ru", "en"),
        [("off", "выкл", "off"), ("on", "кратко", None), ("detailed", "подробно", None)],
    )
    def test_sticker_label(self, mode: str, ru: str, en: str | None):
        screen = notif_screen("ru", {"sticker": mode, "unauthorized": True})

        assert screen.rows[0][0].label == f"🖼 Новые стикеры: {ru}"
        if en:
            assert notif_screen("en", {"sticker": mode}).rows[0][0].label.endswith(en)

    def test_only_two_switches(self):
        screen = notif_screen(
            "en", {"sticker": "on", "unauthorized": False, "jailbreak": True, "blacklist": True}
        )

        assert _payloads(screen) == ["adm_nstk:en", "adm_ntog:en:unauthorized", "adm_settings:en"]
        assert screen.rows[1][0].label.startswith("⚪")


class TestLang:
    def test_both_languages_and_back(self):
        assert _payloads(lang_screen("ru")) == [
            "adm_lang_set:ru:ru",
            "adm_lang_set:ru:en",
            "adm_settings:ru",
        ]

    def test_english_screen_names_russian_in_english(self):
        labels = _labels(lang_screen("en"))

        assert labels[:2] == ["🇷🇺 Russian", "🇬🇧 English"]
