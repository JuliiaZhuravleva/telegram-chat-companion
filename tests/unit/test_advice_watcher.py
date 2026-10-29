"""AdviceWatcher: which chats, when a series is ready, the cap, giving up, and the digest.

The repository is an in-memory fake with the real one's contract (the real one
is covered against PostgreSQL in tests/integration/test_advice_repository.py).
Every id and name below is invented.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import AdviceWatchSettings
from src.database.repositories.advice import DigestItem
from src.services.advice import watcher as watcher_mod
from src.services.advice.context import Line
from src.services.advice.watcher import DIGEST_KEY, TASK_TYPE, AdviceWatcher
from src.services.ai.base import AIProviderError, RateLimitError, TextGenerationResult
from src.services.ai.router import AIRouter

CHAT = -1009999990381
CHANNEL = -1009999990382
ADMIN = 900380
MEMBER = 900381
# 21:30 in Tbilisi (UTC+4): past the default digest hour.
EVENING = datetime(2026, 10, 29, 17, 30, tzinfo=UTC)
NOON = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)

COMMENT_FACTS = {"what": "советует", "suggests": True, "topic": "psyche"}
POST_FACTS = {"what": "пост", "hard_state": True, "about_her_feelings": True}


class FakeRepo:
    def __init__(self, lines: list[Line]) -> None:
        self.lines = {x.message_id: x for x in lines}
        self.checked: set[int] = set()
        self.verdicts: list[dict[str, Any]] = []
        self.post_facts: dict[int, dict[str, Any]] = {}
        self.calls = 0
        self.notified_upto: datetime | None = None
        self.digest: list[DigestItem] = []

    async def unchecked_candidates(self, chat_id, since, exclude_user_ids=()):
        return sorted(
            (
                x
                for x in self.lines.values()
                if x.is_person
                and x.user_id not in exclude_user_ids
                and x.created_at >= since
                and x.content.strip()
                and x.message_id not in self.checked
            ),
            key=lambda x: (x.created_at, x.message_id),
        )

    async def timeline(self, chat_id, since):
        return sorted(
            (x for x in self.lines.values() if x.created_at >= since),
            key=lambda x: (x.created_at, x.message_id),
        )

    async def lines_by_id(self, chat_id, ids):
        return [self.lines[i] for i in ids if i in self.lines]

    async def get_post_facts(self, chat_id, post_id):
        return self.post_facts.get(post_id)

    async def put_post_facts(self, chat_id, post_id, facts):
        self.post_facts.setdefault(post_id, facts)

    async def save_verdict(self, chat_id, *, message_ids, verdict, **kw):
        self.checked.update(message_ids)
        self.verdicts.append({"message_ids": list(message_ids), "verdict": verdict, **kw})
        return True

    async def mark_checked(self, chat_id, ids):
        self.checked.update(ids)

    async def calls_since(self, since):
        return self.calls

    async def digest_items(self, cutoff):
        return list(self.digest)

    async def mark_notified(self, cutoff):
        self.notified_upto = cutoff
        n = len(self.digest)
        self.digest = []
        return n


class FakeRouter:
    """Answers post prompts with POST_FACTS and comment prompts from `answers`."""

    def __init__(self, answers: list[Any] | None = None) -> None:
        self.answers = list(answers or [])
        self.prompts: list[str] = []
        self.kwargs: list[dict[str, Any]] = []
        self.logged: list[dict[str, Any]] = []

    async def generate_text(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        if prompt.startswith("Перед тобой пост"):
            answer: Any = POST_FACTS
        else:
            answer = self.answers.pop(0) if self.answers else COMMENT_FACTS
        if isinstance(answer, Exception):
            raise answer
        text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        return TextGenerationResult(text=text, model="@cf/test", provider="cloudflare")

    async def log_usage(self, result, *, chat_id=0, task_type="text"):
        self.logged.append({"chat_id": chat_id, "task_type": task_type})

    @property
    def comment_calls(self) -> int:
        return sum(1 for p in self.prompts if not p.startswith("Перед тобой пост"))


class FakeBotConfig:
    store: dict[str, Any] = {}

    def __init__(self, pool) -> None:
        pass

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, description=None):
        self.store[key] = value


def _post(mid: int, at: datetime) -> Line:
    return Line(message_id=mid, created_at=at, content="тяжёлый день", sender_kind="channel_post")


def _comment(mid: int, at: datetime, text: str = "попробуй медитацию", **kw) -> Line:
    kw.setdefault("user_id", MEMBER)
    kw.setdefault("first_name", "Аня")
    return Line(message_id=mid, created_at=at, content=text, **kw)


@pytest.fixture(autouse=True)
def _repos(monkeypatch):
    FakeBotConfig.store = {}
    monkeypatch.setattr(watcher_mod, "BotConfigRepository", FakeBotConfig)
    messages = MagicMock()
    messages.return_value.list_chat_ids = AsyncMock(return_value=[CHAT])
    monkeypatch.setattr(watcher_mod, "MessageRepository", messages)


def _watcher(
    lines,
    *,
    router=None,
    now=NOON,
    config=None,
    chat_config_overrides=None,
    admin_ids=(ADMIN,),
    make_chat_config,
):
    overrides = {"advice_watch_enabled": True, "linked_channel_id": CHANNEL}
    overrides.update(chat_config_overrides or {})
    chat_config = SimpleNamespace(
        get_config=AsyncMock(return_value=make_chat_config(chat_id=CHAT, **overrides))
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    w = AdviceWatcher(
        pool=MagicMock(),
        ai_router=router or FakeRouter(),
        chat_config=chat_config,
        bot=bot,
        admin_ids=list(admin_ids),
        config=config or AdviceWatchSettings(enabled=True),
        clock=lambda: now,
    )
    repo = FakeRepo(lines)
    w._repo = repo  # type: ignore[assignment]
    return w, repo, bot


def _thread(now: datetime = NOON) -> list[Line]:
    """A post and one closed comment under it, ten minutes old."""
    return [
        _post(10, now - timedelta(hours=1)),
        _comment(11, now - timedelta(minutes=10), reply_to=10),
    ]


class TestWhichChats:
    async def test_a_watched_chat_gets_a_verdict(self, make_chat_config):
        router = FakeRouter()
        w, repo, _ = _watcher(_thread(), router=router, make_chat_config=make_chat_config)

        stats = await w.run_once()

        assert stats["verdicts"] == 1
        (saved,) = repo.verdicts
        assert saved["message_ids"] == [11]
        assert saved["post_message_id"] == 10
        assert (saved["verdict"].advice, saved["verdict"].level) == ("unsolicited", "hard")
        assert router.kwargs[-1]["model"] == AdviceWatchSettings().model
        assert router.kwargs[-1]["response_mime_type"] == "application/json"
        assert {x["task_type"] for x in router.logged} == {TASK_TYPE}

    async def test_flag_off_reads_nothing(self, make_chat_config):
        router = FakeRouter()
        w, repo, _ = _watcher(
            _thread(),
            router=router,
            chat_config_overrides={"advice_watch_enabled": False},
            make_chat_config=make_chat_config,
        )
        await w.run_once()
        assert router.prompts == [] and repo.verdicts == []

    async def test_flag_on_without_a_linked_channel_reads_nothing(self, make_chat_config):
        """The global default must not turn the blog's rules on in ordinary groups."""
        router = FakeRouter()
        w, repo, _ = _watcher(
            _thread(),
            router=router,
            chat_config_overrides={"linked_channel_id": None},
            make_chat_config=make_chat_config,
        )
        await w.run_once()
        assert router.prompts == [] and repo.verdicts == []


class TestSeries:
    async def test_an_open_series_waits_for_the_next_pass(self, make_chat_config):
        lines = [
            _post(10, NOON - timedelta(hours=1)),
            _comment(11, NOON - timedelta(seconds=30), reply_to=10),
        ]
        router = FakeRouter()
        w, repo, _ = _watcher(lines, router=router, make_chat_config=make_chat_config)

        await w.run_once()

        assert router.comment_calls == 0 and repo.checked == set()

    async def test_one_series_is_one_call_and_one_verdict(self, make_chat_config):
        lines = [
            _post(10, NOON - timedelta(hours=1)),
            _comment(11, NOON - timedelta(minutes=12), "раз", reply_to=10),
            _comment(12, NOON - timedelta(minutes=11), "два"),
            _comment(13, NOON - timedelta(minutes=10), "три"),
        ]
        router = FakeRouter()
        w, repo, _ = _watcher(lines, router=router, make_chat_config=make_chat_config)

        await w.run_once()

        assert router.comment_calls == 1
        assert [v["message_ids"] for v in repo.verdicts] == [[11, 12, 13]]

    async def test_post_facts_are_asked_once_per_post(self, make_chat_config):
        lines = [
            _post(10, NOON - timedelta(hours=2)),
            _comment(11, NOON - timedelta(hours=1), reply_to=10),
            _comment(12, NOON - timedelta(minutes=10), user_id=MEMBER + 1, reply_to=10),
        ]
        router = FakeRouter()
        w, repo, _ = _watcher(lines, router=router, make_chat_config=make_chat_config)

        await w.run_once()

        assert len(router.prompts) == 3 and router.comment_calls == 2
        assert repo.post_facts == {10: POST_FACTS}

    async def test_a_comment_under_no_post_is_judged_without_one(self, make_chat_config):
        router = FakeRouter([{"suggests": True, "topic": "everyday"}])
        w, repo, _ = _watcher(
            [_comment(11, NOON - timedelta(minutes=10))],
            router=router,
            make_chat_config=make_chat_config,
        )

        await w.run_once()

        assert router.comment_calls == 1 and len(router.prompts) == 1
        (saved,) = repo.verdicts
        assert saved["post_message_id"] is None
        # Everyday advice is a violation only under a hard-state post.
        assert saved["verdict"].advice == "none"

    async def test_admins_are_not_judged(self, make_chat_config):
        lines = [
            _post(10, NOON - timedelta(hours=1)),
            _comment(11, NOON - timedelta(minutes=10), user_id=ADMIN, reply_to=10),
        ]
        router = FakeRouter()
        w, repo, _ = _watcher(lines, router=router, make_chat_config=make_chat_config)
        await w.run_once()
        assert router.prompts == []


class TestCostAndFailures:
    async def test_the_cap_stops_the_pass(self, make_chat_config):
        lines = [_post(10, NOON - timedelta(hours=3))] + [
            _comment(20 + i, NOON - timedelta(minutes=60 - i * 5), user_id=MEMBER + i, reply_to=10)
            for i in range(5)
        ]
        router = FakeRouter()
        w, repo, _ = _watcher(
            lines,
            router=router,
            config=AdviceWatchSettings(enabled=True, daily_call_cap=10),
            make_chat_config=make_chat_config,
        )
        repo.calls = 7  # already spent today

        await w.run_once()

        # 3 left: the post's facts and two comments.
        assert len(router.prompts) == 3 and len(repo.verdicts) == 2

    async def test_a_spent_cap_makes_no_call(self, make_chat_config):
        router = FakeRouter()
        w, repo, _ = _watcher(
            _thread(),
            router=router,
            config=AdviceWatchSettings(enabled=True, daily_call_cap=5),
            make_chat_config=make_chat_config,
        )
        repo.calls = 5
        await w.run_once()
        assert router.prompts == []

    async def test_a_failing_series_is_given_up_after_max_attempts(self, make_chat_config):
        router = FakeRouter(["не JSON"] * 3)
        w, repo, _ = _watcher(
            _thread(),
            router=router,
            config=AdviceWatchSettings(enabled=True, max_attempts=3),
            make_chat_config=make_chat_config,
        )

        for expected_checked in (set(), set(), {11}):
            await w.run_once()
            assert repo.checked == expected_checked
        assert repo.verdicts == []
        await w.run_once()
        assert router.comment_calls == 3

    async def test_a_provider_error_counts_as_a_failed_attempt(self, make_chat_config):
        router = FakeRouter([AIProviderError("down", "cloudflare", retriable=True)])
        w, repo, _ = _watcher(_thread(), router=router, make_chat_config=make_chat_config)

        stats = await w.run_once()

        assert stats["failed"] == 1 and repo.checked == set()
        await w.run_once()
        assert len(repo.verdicts) == 1

    async def test_a_rate_limit_ends_the_pass_without_counting_an_attempt(self, make_chat_config):
        lines = _thread() + [
            _comment(12, NOON - timedelta(minutes=5), user_id=MEMBER + 1, reply_to=10)
        ]
        router = FakeRouter([RateLimitError("429", "cloudflare")])
        w, repo, _ = _watcher(
            lines,
            router=router,
            config=AdviceWatchSettings(enabled=True, max_attempts=1),
            make_chat_config=make_chat_config,
        )

        await w.run_once()

        assert router.comment_calls == 1  # the second comment was not tried
        assert repo.checked == set()  # max_attempts=1 would have given up on a counted failure

    async def test_a_rate_limit_through_the_real_router_is_not_a_failure(
        self, make_chat_config, mock_provider
    ):
        # The fake router above raises a bare RateLimitError; the real one never
        # does -- it re-raises a fresh AIProviderError *from* it. Only the real
        # router shows whether the watcher still recognises a spent quota
        # (2026-10-29: counted as failures, series given up without a verdict).
        # Shaped like production: a text chain without Cloudflare, which is
        # reached only because the classifier names its model.
        settings = MagicMock()
        settings.openai_api_key = settings.grok_api_key = settings.deepseek_api_key = None
        settings.gemini_api_key = "gemini-test-key"
        settings.cloudflare_api_token = "cf-test-token"
        settings.cloudflare_account_id = "cf-test-account"
        settings.ai.default_provider = "gemini"
        settings.ai.tasks = {}
        router = AIRouter(settings)
        router._providers["gemini"] = mock_provider(name="gemini")
        router._providers["cloudflare"] = mock_provider(
            name="cloudflare", error=RateLimitError("4006 daily allocation", "cloudflare")
        )
        w, repo, _ = _watcher(
            _thread(),
            router=router,
            config=AdviceWatchSettings(enabled=True, max_attempts=1),
            make_chat_config=make_chat_config,
        )

        stats = await w.run_once()

        assert "generate_text" in router._providers["cloudflare"].call_log
        assert router._providers["gemini"].call_log == []
        assert stats["failed"] == 0, "a spent quota was counted as a failed attempt"
        assert repo.checked == set(), "the series was given up without a verdict"


class TestDigest:
    def _finding(self) -> DigestItem:
        return DigestItem(
            chat_id=CHAT,
            chat_title="Блог",
            chat_type="supergroup",
            user_id=MEMBER,
            name="Аня",
            first_message_id=11,
            quote="попробуй медитацию",
            what="советует",
            advice="unsolicited",
            level="hard",
            support=0,
        )

    async def test_sent_once_in_the_evening_and_findings_closed(self, make_chat_config):
        w, repo, bot = _watcher([], now=EVENING, make_chat_config=make_chat_config)
        repo.digest = [self._finding()]

        stats = await w.run_once()

        assert stats["digest_items"] == 1
        bot.send_message.assert_awaited_once()
        assert bot.send_message.await_args.args[0] == ADMIN
        assert bot.send_message.await_args.kwargs["parse_mode"] == "HTML"
        assert repo.notified_upto == EVENING
        assert FakeBotConfig.store[DIGEST_KEY] == "2026-10-29"

        repo.digest = [self._finding()]
        await w.run_once()
        bot.send_message.assert_awaited_once()  # not again the same day

    async def test_not_before_the_digest_hour(self, make_chat_config):
        w, repo, bot = _watcher([], now=NOON, make_chat_config=make_chat_config)
        repo.digest = [self._finding()]
        await w.run_once()
        bot.send_message.assert_not_awaited()
        assert repo.notified_upto is None

    async def test_an_empty_day_sends_nothing_but_closes_the_day(self, make_chat_config):
        w, repo, bot = _watcher([], now=EVENING, make_chat_config=make_chat_config)
        await w.run_once()
        bot.send_message.assert_not_awaited()
        assert repo.notified_upto == EVENING
        assert FakeBotConfig.store[DIGEST_KEY] == "2026-10-29"

    async def test_undelivered_findings_are_kept_for_the_next_pass(self, make_chat_config):
        w, repo, bot = _watcher([], now=EVENING, make_chat_config=make_chat_config)
        bot.send_message.side_effect = RuntimeError("blocked")
        repo.digest = [self._finding()]

        await w.run_once()

        assert repo.notified_upto is None and DIGEST_KEY not in FakeBotConfig.store
        bot.send_message.side_effect = None
        await w.run_once()
        assert repo.notified_upto == EVENING

    async def test_a_digest_cut_short_is_not_sent_again(self, make_chat_config):
        w, repo, bot = _watcher([], now=EVENING, make_chat_config=make_chat_config)
        bot.send_message.side_effect = [None, RuntimeError("flood"), None, None]
        repo.digest = [
            DigestItem(**{**vars(self._finding()), "first_message_id": i, "quote": "слово " * 60})
            for i in range(60)
        ]

        await w.run_once()
        await w.run_once()

        assert bot.send_message.await_count == 2  # part one, then the failed part two
        assert repo.notified_upto == EVENING

    async def test_no_admins_keeps_the_findings(self, make_chat_config):
        w, repo, bot = _watcher([], now=EVENING, admin_ids=(), make_chat_config=make_chat_config)
        repo.digest = [self._finding()]
        await w.run_once()
        bot.send_message.assert_not_awaited()
        assert repo.notified_upto is None


class TestLifecycle:
    async def test_disabled_by_config_starts_nothing(self, make_chat_config):
        w, _, _ = _watcher(
            [], config=AdviceWatchSettings(enabled=False), make_chat_config=make_chat_config
        )
        await w.start()
        assert w._task is None
        await w.stop()
