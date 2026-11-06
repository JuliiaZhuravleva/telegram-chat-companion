"""Tests for the custom rules evaluation engine."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.rules import RuleAction
from src.services.rules import engine as engine_module
from src.services.rules.engine import RuleEngine


@pytest.fixture(autouse=True)
def _no_flood_cooldowns() -> Iterator[None]:
    """sticker_flood's rest is process-wide state: no test inherits another's."""
    engine_module._STICKER_FLOOD_FIRED.clear()
    yield
    engine_module._STICKER_FLOOD_FIRED.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(
    text: str | None = "hello world",
    caption: str | None = None,
    user_id: int = 100,
    *,
    sticker: bool = False,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.sticker = MagicMock() if sticker else None
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.first_name = "Alice"
    msg.from_user.username = "alice"
    msg.chat = MagicMock()
    msg.chat.id = -1001
    return msg


def _rule_row(
    *,
    rule_id: int = 1,
    chat_id: int = -1001,
    rule_type: str = "keyword_trigger",
    config: dict | None = None,
    weight: int = 1,
    mandatory: bool = False,
    enabled: bool = True,
) -> dict:
    return {
        "id": rule_id,
        "chat_id": chat_id,
        "rule_type": rule_type,
        "config": config or {},
        "weight": weight,
        "mandatory": mandatory,
        "enabled": enabled,
        "status": "active",
        "trigger_count": 0,
    }


def _make_engine(rules: list[dict] | None = None) -> RuleEngine:
    repo = AsyncMock()
    repo.get_active_rules = AsyncMock(return_value=rules or [])
    repo.record_trigger = AsyncMock()
    repo.count_stickers_in_interval = AsyncMock(return_value=0)
    repo.count_user_messages_in_interval = AsyncMock(return_value=(0, 0))
    return RuleEngine(repo)


# ---------------------------------------------------------------------------
# Keyword trigger tests
# ---------------------------------------------------------------------------


class TestKeywordTrigger:
    @pytest.mark.asyncio
    async def test_contains_match(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("say hello world"),
            rules_mode="all",
        )
        assert len(actions) == 1
        assert actions[0].action == RuleAction.NOTIFY_ADMIN

    @pytest.mark.asyncio
    async def test_set_reaction_action_is_extracted(self) -> None:
        """ "set_reaction" must survive _extract_actions' RuleAction() parse;
        an unknown action string is silently dropped there. The emoji itself
        is read from rule.config by the executor, not from params."""
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "set_reaction",
                        "emoji": "💊",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("say hello world"),
            rules_mode="all",
        )
        assert len(actions) == 1
        assert actions[0].action == RuleAction.SET_REACTION
        assert actions[0].rule.config["emoji"] == "💊"

    @pytest.mark.asyncio
    async def test_contains_no_match(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["goodbye"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello world"),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_exact_match(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "exact",
                        "action": "warn_user",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="all",
        )
        assert len(actions) == 1
        assert actions[0].action == RuleAction.WARN_USER

    @pytest.mark.asyncio
    async def test_exact_no_match_extra_text(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "exact",
                        "action": "warn_user",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello world"),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_regex_match(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": [r"h[aeiou]llo"],
                        "match_type": "regex",
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("say hallo there"),
            rules_mode="all",
        )
        assert len(actions) == 1

    @pytest.mark.asyncio
    async def test_regex_invalid_pattern(self) -> None:
        """Invalid regex should not crash, just not match."""
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["[invalid"],
                        "match_type": "regex",
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("test"),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_case_insensitive(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["HELLO"],
                        "match_type": "contains",
                        "case_sensitive": False,
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello world"),
            rules_mode="all",
        )
        assert len(actions) == 1

    @pytest.mark.asyncio
    async def test_case_sensitive(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["HELLO"],
                        "match_type": "contains",
                        "case_sensitive": True,
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello world"),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_target_users(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "target_users": [200],
                        "action": "notify_admin",
                    }
                )
            ]
        )
        # User 100 is not in target_users
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_exclude_users(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "exclude_users": [100],
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_caption_match(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["photo"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message(text=None, caption="nice photo"),
            rules_mode="all",
        )
        assert len(actions) == 1


# ---------------------------------------------------------------------------
# User-specific tests
# ---------------------------------------------------------------------------


class TestUserSpecific:
    @pytest.mark.asyncio
    async def test_any_message(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_type="user_specific",
                    config={
                        "user_ids": [100],
                        "conditions": {"any_message": True},
                        "action": "notify_admin",
                    },
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("anything"),
            rules_mode="all",
        )
        assert len(actions) == 1

    @pytest.mark.asyncio
    async def test_wrong_user(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_type="user_specific",
                    config={
                        "user_ids": [200],
                        "conditions": {"any_message": True},
                        "action": "notify_admin",
                    },
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("anything"),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_keyword_condition(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_type="user_specific",
                    config={
                        "user_ids": [100],
                        "conditions": {"keywords": ["report"]},
                        "action": "warn_user",
                    },
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("I want to report this"),
            rules_mode="all",
        )
        assert len(actions) == 1

    @pytest.mark.asyncio
    async def test_keyword_condition_no_match(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_type="user_specific",
                    config={
                        "user_ids": [100],
                        "conditions": {"keywords": ["report"]},
                        "action": "warn_user",
                    },
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("just chatting"),
            rules_mode="all",
        )
        assert len(actions) == 0


# ---------------------------------------------------------------------------
# Sticker flood tests
# ---------------------------------------------------------------------------


class TestStickerFlood:
    @pytest.mark.asyncio
    async def test_flood_triggered(self) -> None:
        repo = AsyncMock()
        repo.get_active_rules = AsyncMock(
            return_value=[
                _rule_row(
                    rule_type="sticker_flood",
                    config={
                        "max_stickers_per_interval": 5,
                        "interval_seconds": 60,
                        "action": "warn_user",
                        "warning_message": "Stop spamming stickers!",
                    },
                )
            ]
        )
        repo.record_trigger = AsyncMock()
        repo.count_stickers_in_interval = AsyncMock(return_value=6)

        engine = RuleEngine(repo)
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message(text=None, sticker=True),
            rules_mode="all",
        )
        assert len(actions) == 1
        assert actions[0].action == RuleAction.WARN_USER

    @pytest.mark.asyncio
    async def test_flood_not_triggered(self) -> None:
        repo = AsyncMock()
        repo.get_active_rules = AsyncMock(
            return_value=[
                _rule_row(
                    rule_type="sticker_flood",
                    config={
                        "max_stickers_per_interval": 5,
                        "interval_seconds": 60,
                        "action": "warn_user",
                    },
                )
            ]
        )
        repo.record_trigger = AsyncMock()
        repo.count_stickers_in_interval = AsyncMock(return_value=3)

        engine = RuleEngine(repo)
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message(text=None, sticker=True),
            rules_mode="all",
        )
        assert len(actions) == 0


def _flood(rule_id: int = 1, *, weight: int = 1, interval: int = 60) -> dict:
    return _rule_row(
        rule_id=rule_id,
        rule_type="sticker_flood",
        config={"max_stickers_per_interval": 5, "interval_seconds": interval},
        weight=weight,
    )


def _flood_engine(*rules: dict, count: int = 6) -> tuple[RuleEngine, AsyncMock]:
    repo = AsyncMock()
    repo.get_active_rules = AsyncMock(return_value=list(rules))
    repo.record_trigger = AsyncMock()
    repo.count_stickers_in_interval = AsyncMock(return_value=count)
    repo.count_user_messages_in_interval = AsyncMock(return_value=(0, 0))
    return RuleEngine(repo), repo


async def _fired(engine: RuleEngine, *, sticker: bool = True, mode: str = "all") -> list[int]:
    actions = await engine.evaluate(
        chat_id=-1001,
        user_id=100,
        message=_make_message(text=None if sticker else "hi", sticker=sticker),
        rules_mode=mode,
    )
    return [a.rule.id for a in actions]


class TestStickerFloodFiresOnAStickerOnce:
    """The count is chat-wide, so it must be a sticker that fires the rule, and
    a flood must be one alert, not one per sticker for the rest of the minute."""

    @pytest.mark.asyncio
    async def test_a_text_message_during_a_flood_does_not_fire_it(self) -> None:
        engine, repo = _flood_engine(_flood())

        assert await _fired(engine, sticker=False) == []
        repo.count_stickers_in_interval.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_next_sticker_inside_the_interval_does_not_fire_it_again(self) -> None:
        engine, repo = _flood_engine(_flood())

        assert await _fired(engine) == [1]
        assert await _fired(engine) == []
        assert repo.count_stickers_in_interval.await_count == 1, "a resting rule costs no query"
        repo.record_trigger.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_it_fires_again_once_the_interval_has_passed(self) -> None:
        engine, _ = _flood_engine(_flood(interval=60))
        now = [1000.0]
        with patch.object(engine_module, "monotonic", side_effect=lambda: now[0]):
            assert await _fired(engine) == [1]
            now[0] = 1059.0
            assert await _fired(engine) == [], "59 s later: still resting"
            now[0] = 1060.0
            assert await _fired(engine) == [1], "60 s later: a new flood"

    @pytest.mark.asyncio
    async def test_each_rule_rests_on_its_own(self) -> None:
        engine, _ = _flood_engine(_flood(1), _flood(2))

        assert await _fired(engine) == [1, 2]
        assert await _fired(engine) == []

    @pytest.mark.asyncio
    async def test_two_stickers_at_once_fire_it_once(self) -> None:
        """Both pass the check before the count; only one may act."""
        engine, repo = _flood_engine(_flood())
        release = asyncio.Event()
        waiting = 0

        async def count(*_: object) -> int:
            nonlocal waiting
            waiting += 1
            await release.wait()
            return 6

        repo.count_stickers_in_interval = AsyncMock(side_effect=count)
        first = asyncio.create_task(_fired(engine))
        second = asyncio.create_task(_fired(engine))
        while waiting < 2:
            await asyncio.sleep(0)
        release.set()

        assert sorted([await first, await second]) == [[], [1]]

    @pytest.mark.asyncio
    async def test_a_resting_rule_does_not_shadow_another_under_highest_weight(self) -> None:
        anyone = _rule_row(
            rule_id=2,
            rule_type="user_specific",
            config={"user_ids": [100], "conditions": {"any_message": True}},
            weight=1,
        )
        engine, _ = _flood_engine(_flood(1, weight=5), anyone)

        assert await _fired(engine, mode="highest_weight") == [1]
        assert await _fired(engine, mode="highest_weight") == [2]

    @pytest.mark.asyncio
    async def test_a_match_that_does_not_act_does_not_start_the_rest(self) -> None:
        anyone = _rule_row(
            rule_id=2,
            rule_type="user_specific",
            config={"user_ids": [100], "conditions": {"any_message": True}},
            weight=5,
        )
        engine, _ = _flood_engine(_flood(1, weight=1), anyone)

        assert await _fired(engine, mode="highest_weight") == [2]
        assert engine_module._STICKER_FLOOD_FIRED == {}


# ---------------------------------------------------------------------------
# Spam detect tests
# ---------------------------------------------------------------------------


class TestSpamDetect:
    @pytest.mark.asyncio
    async def test_message_count_triggered(self) -> None:
        repo = AsyncMock()
        repo.get_active_rules = AsyncMock(
            return_value=[
                _rule_row(
                    rule_type="spam_detect",
                    config={
                        "time_window_seconds": 60,
                        "max_messages": 10,
                        "max_chars_per_message": 4000,
                        "action": "notify_admin",
                    },
                )
            ]
        )
        repo.record_trigger = AsyncMock()
        repo.count_user_messages_in_interval = AsyncMock(return_value=(15, 50))

        engine = RuleEngine(repo)
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message(),
            rules_mode="all",
        )
        assert len(actions) == 1

    @pytest.mark.asyncio
    async def test_char_length_triggered(self) -> None:
        repo = AsyncMock()
        repo.get_active_rules = AsyncMock(
            return_value=[
                _rule_row(
                    rule_type="spam_detect",
                    config={
                        "time_window_seconds": 60,
                        "max_messages": 100,
                        "max_chars_per_message": 500,
                        "action": "warn_user",
                    },
                )
            ]
        )
        repo.record_trigger = AsyncMock()
        repo.count_user_messages_in_interval = AsyncMock(return_value=(3, 600))

        engine = RuleEngine(repo)
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message(),
            rules_mode="all",
        )
        assert len(actions) == 1

    @pytest.mark.asyncio
    async def test_not_triggered(self) -> None:
        repo = AsyncMock()
        repo.get_active_rules = AsyncMock(
            return_value=[
                _rule_row(
                    rule_type="spam_detect",
                    config={
                        "time_window_seconds": 60,
                        "max_messages": 10,
                        "max_chars_per_message": 4000,
                        "action": "notify_admin",
                    },
                )
            ]
        )
        repo.record_trigger = AsyncMock()
        repo.count_user_messages_in_interval = AsyncMock(return_value=(5, 100))

        engine = RuleEngine(repo)
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message(),
            rules_mode="all",
        )
        assert len(actions) == 0


# ---------------------------------------------------------------------------
# Execution mode tests
# ---------------------------------------------------------------------------


class TestExecutionModes:
    @pytest.mark.asyncio
    async def test_mode_all(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_id=1,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    },
                ),
                _rule_row(
                    rule_id=2,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "warn_user",
                        "warning_message": "!",
                    },
                ),
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="all",
        )
        assert len(actions) == 2

    @pytest.mark.asyncio
    async def test_mode_highest_weight(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_id=1,
                    weight=1,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    },
                ),
                _rule_row(
                    rule_id=2,
                    weight=10,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "warn_user",
                        "warning_message": "!",
                    },
                ),
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="highest_weight",
        )
        assert len(actions) == 1
        assert actions[0].rule.weight == 10

    @pytest.mark.asyncio
    async def test_mode_weighted_random(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_id=1,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    },
                ),
                _rule_row(
                    rule_id=2,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "warn_user",
                        "warning_message": "!",
                    },
                ),
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="weighted_random",
        )
        # weighted_random picks exactly 1
        assert len(actions) == 1


# ---------------------------------------------------------------------------
# Mandatory rules tests
# ---------------------------------------------------------------------------


class TestMandatoryRules:
    @pytest.mark.asyncio
    async def test_mandatory_always_fires(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_id=1,
                    mandatory=True,
                    weight=1,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    },
                ),
                _rule_row(
                    rule_id=2,
                    mandatory=False,
                    weight=10,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "warn_user",
                        "warning_message": "!",
                    },
                ),
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="highest_weight",
        )
        # mandatory(id=1) + highest_weight optional(id=2) = 2 actions
        assert len(actions) == 2
        rule_ids = {a.rule.id for a in actions}
        assert rule_ids == {1, 2}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_rules(self) -> None:
        engine = _make_engine([])
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_no_text_message(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message(text=None, caption=None),
            rules_mode="all",
        )
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_unknown_action(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "nonexistent_action",
                    }
                )
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="all",
        )
        # Unknown action is skipped
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_invalid_rules_mode_defaults_to_all(self) -> None:
        engine = _make_engine(
            [
                _rule_row(
                    rule_id=1,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    },
                ),
                _rule_row(
                    rule_id=2,
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "warn_user",
                        "warning_message": "!",
                    },
                ),
            ]
        )
        actions = await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="invalid_mode",
        )
        assert len(actions) == 2

    @pytest.mark.asyncio
    async def test_trigger_recorded(self) -> None:
        repo = AsyncMock()
        repo.get_active_rules = AsyncMock(
            return_value=[
                _rule_row(
                    config={
                        "keywords": ["hello"],
                        "match_type": "contains",
                        "action": "notify_admin",
                    }
                )
            ]
        )
        repo.record_trigger = AsyncMock()

        engine = RuleEngine(repo)
        await engine.evaluate(
            chat_id=-1001,
            user_id=100,
            message=_make_message("hello"),
            rules_mode="all",
        )
        repo.record_trigger.assert_called_once_with(1)
