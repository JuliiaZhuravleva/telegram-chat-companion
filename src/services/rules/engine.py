"""Custom rules evaluation engine.

Evaluates per-chat rules against incoming messages and returns actions to execute.
Supports four rule types (keyword_trigger, user_specific, sticker_flood, spam_detect)
and three execution modes (all, highest_weight, weighted_random).
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

import structlog
from aiogram.types import Message

from src.database.repositories.rules import RulesRepository
from src.models.rules import Rule, RuleAction, RuleMatch, RulesMode, RuleType, stored_weight

logger = structlog.get_logger(__name__)

_STICKER_FLOOD_FIRED: dict[int, float] = {}
"""sticker_flood rule id -> ``monotonic()`` of its last firing.

Module-level because the engine is built per update: a cooldown held on the
instance would be forgotten by the next sticker. One process runs the bot, so
this is the whole memory of it; a restart forgets it, which costs at most one
early repeat.
"""


def _flood_cooling(rule_id: int, interval: float, now: float) -> bool:
    fired = _STICKER_FLOOD_FIRED.get(rule_id)
    return fired is not None and now - fired < interval


def _flood_interval(rule: Rule) -> float:
    value = rule.config.get("interval_seconds", 60)
    return float(value) if isinstance(value, int | float) else 60.0


@dataclass
class RuleActionResult:
    """Action to execute for a triggered rule."""

    rule: Rule
    action: RuleAction
    params: dict[str, Any] = field(default_factory=dict)


class RuleEngine:
    """Evaluate rules and determine actions."""

    def __init__(self, rules_repo: RulesRepository) -> None:
        self._rules_repo = rules_repo

    async def evaluate(
        self,
        *,
        chat_id: int,
        user_id: int,
        message: Message,
        rules_mode: str,
    ) -> list[RuleActionResult]:
        """Evaluate all active rules for a chat against the current message.

        Returns list of actions to execute.
        """
        raw_rules = await self._rules_repo.get_active_rules(chat_id)
        if not raw_rules:
            return []

        rules = [self._parse_rule(r) for r in raw_rules]

        # Evaluate each rule
        matches: list[RuleMatch] = []
        for rule in rules:
            match = await self._evaluate_rule(rule, chat_id, user_id, message)
            if match.triggered:
                matches.append(match)
                logger.debug(
                    "Rule triggered",
                    rule_id=rule.id,
                    rule_type=rule.rule_type,
                    detail=match.match_detail,
                )

        if not matches:
            return []

        # Separate mandatory vs optional
        mandatory = [m for m in matches if m.rule.mandatory]
        optional = [m for m in matches if not m.rule.mandatory]

        # Apply execution mode to optional rules
        try:
            mode = RulesMode(rules_mode)
        except ValueError:
            mode = RulesMode.ALL
        selected = self._apply_mode(optional, mode)

        # Combine mandatory (always) + selected optional
        final = self._claim_flood_cooldowns(mandatory + selected)

        # Build action results
        actions: list[RuleActionResult] = []
        for match in final:
            actions.extend(self._extract_actions(match))

        # Record triggers
        for match in final:
            try:
                await self._rules_repo.record_trigger(match.rule.id)
            except Exception:
                logger.warning("Failed to record rule trigger", rule_id=match.rule.id)

        return actions

    @staticmethod
    def _parse_rule(row: dict[str, Any]) -> Rule:
        """Convert DB row to Rule dataclass."""
        return Rule(
            id=row["id"],
            chat_id=row["chat_id"],
            rule_type=RuleType(row["rule_type"]),
            config=row["config"]
            if isinstance(row["config"], dict)
            else json.loads(row["config"])
            if isinstance(row["config"], str)
            else {},
            # Read, not trusted: a stored 0, negative or NULL weight made
            # `_apply_mode` raise, and with it every rule of the message.
            weight=stored_weight(row.get("weight")),
            mandatory=row.get("mandatory", False),
            enabled=row.get("enabled", True),
            status=row.get("status", "active"),
            trigger_count=row.get("trigger_count", 0),
        )

    async def _evaluate_rule(
        self,
        rule: Rule,
        chat_id: int,
        user_id: int,
        message: Message,
    ) -> RuleMatch:
        """Evaluate a single rule against the message."""
        try:
            if rule.rule_type == RuleType.KEYWORD_TRIGGER:
                return self._eval_keyword(rule, user_id, message)
            if rule.rule_type == RuleType.USER_SPECIFIC:
                return self._eval_user_specific(rule, user_id, message)
            if rule.rule_type == RuleType.STICKER_FLOOD:
                return await self._eval_sticker_flood(rule, chat_id, message)
            if rule.rule_type == RuleType.SPAM_DETECT:
                return await self._eval_spam_detect(rule, chat_id, user_id)
        except Exception:
            logger.warning("Rule evaluation failed", rule_id=rule.id, exc_info=True)
        return RuleMatch(rule=rule, triggered=False)

    @staticmethod
    def _eval_keyword(rule: Rule, user_id: int, message: Message) -> RuleMatch:
        """Evaluate keyword_trigger rule."""
        cfg = rule.config
        keywords: list[str] = cfg.get("keywords", [])
        match_type: str = cfg.get("match_type", "contains")
        case_sensitive: bool = cfg.get("case_sensitive", False)
        target_users: list[int] = cfg.get("target_users", [])
        exclude_users: list[int] = cfg.get("exclude_users", [])

        # Check user filters
        if target_users and user_id not in target_users:
            return RuleMatch(rule=rule, triggered=False)
        if user_id in exclude_users:
            return RuleMatch(rule=rule, triggered=False)

        text = message.text or message.caption or ""
        if not case_sensitive:
            text = text.lower()

        for kw in keywords:
            check_kw = kw if case_sensitive else kw.lower()
            if match_type == "exact" and text.strip() == check_kw:
                return RuleMatch(rule=rule, triggered=True, match_detail=f"exact:{kw}")
            if match_type == "contains" and check_kw in text:
                return RuleMatch(rule=rule, triggered=True, match_detail=f"contains:{kw}")
            if match_type == "regex":
                try:
                    flags = 0 if case_sensitive else re.IGNORECASE
                    if re.search(kw, message.text or message.caption or "", flags):
                        return RuleMatch(rule=rule, triggered=True, match_detail=f"regex:{kw}")
                except re.error:
                    logger.warning("Invalid regex in rule", rule_id=rule.id, pattern=kw)

        return RuleMatch(rule=rule, triggered=False)

    @staticmethod
    def _eval_user_specific(rule: Rule, user_id: int, message: Message) -> RuleMatch:
        """Evaluate user_specific rule."""
        cfg = rule.config
        target_user_ids: list[int] = cfg.get("user_ids", [])
        conditions: dict[str, Any] = cfg.get("conditions", {})

        if user_id not in target_user_ids:
            return RuleMatch(rule=rule, triggered=False)

        if conditions.get("any_message", False):
            return RuleMatch(rule=rule, triggered=True, match_detail=f"user:{user_id}")

        kw_list: list[str] = conditions.get("keywords", [])
        text = (message.text or message.caption or "").lower()
        for kw in kw_list:
            if kw.lower() in text:
                return RuleMatch(
                    rule=rule,
                    triggered=True,
                    match_detail=f"user:{user_id},kw:{kw}",
                )

        return RuleMatch(rule=rule, triggered=False)

    async def _eval_sticker_flood(self, rule: Rule, chat_id: int, message: Message) -> RuleMatch:
        """Evaluate sticker_flood rule: a sticker that takes the chat over the limit.

        Only a sticker can fire it: the count is chat-wide, so before this a text
        message from anyone, sent while the window still held enough stickers,
        fired it again, every message for up to a whole interval. And once fired it
        rests for an interval (`_claim_flood_cooldowns`), so a flood of thirty
        stickers is one alert, not twenty.
        """
        if message.sticker is None:
            return RuleMatch(rule=rule, triggered=False)
        cfg = rule.config
        max_stickers = cfg.get("max_stickers_per_interval", 10)
        interval = cfg.get("interval_seconds", 60)
        # Checked before the count as well: a resting rule costs no query and,
        # under a weight mode, is not picked over a rule that will act.
        if _flood_cooling(rule.id, _flood_interval(rule), monotonic()):
            return RuleMatch(rule=rule, triggered=False)

        count = await self._rules_repo.count_stickers_in_interval(chat_id, interval)
        if count >= max_stickers:
            return RuleMatch(
                rule=rule,
                triggered=True,
                match_detail=f"stickers:{count}/{max_stickers}",
            )
        return RuleMatch(rule=rule, triggered=False)

    async def _eval_spam_detect(self, rule: Rule, chat_id: int, user_id: int) -> RuleMatch:
        """Evaluate spam_detect rule."""
        cfg = rule.config
        time_window = cfg.get("time_window_seconds", 60)
        max_messages = cfg.get("max_messages", 20)
        max_chars = cfg.get("max_chars_per_message", 4000)

        msg_count, max_len = await self._rules_repo.count_user_messages_in_interval(
            chat_id, user_id, time_window
        )

        if msg_count >= max_messages:
            return RuleMatch(
                rule=rule,
                triggered=True,
                match_detail=f"spam:msgs={msg_count}/{max_messages}",
            )
        if max_chars > 0 and max_len >= max_chars:
            return RuleMatch(
                rule=rule,
                triggered=True,
                match_detail=f"spam:chars={max_len}/{max_chars}",
            )
        return RuleMatch(rule=rule, triggered=False)

    @staticmethod
    def _claim_flood_cooldowns(matches: list[RuleMatch]) -> list[RuleMatch]:
        """Start the rest of every sticker_flood rule about to act; drop one already resting.

        Synchronous on purpose: two stickers evaluated concurrently both passed
        `_eval_sticker_flood`'s check across its awaits, and only a check and a
        stamp with no await between them lets exactly one of them act.
        """
        now = monotonic()
        kept: list[RuleMatch] = []
        for match in matches:
            rule = match.rule
            if rule.rule_type == RuleType.STICKER_FLOOD:
                if _flood_cooling(rule.id, _flood_interval(rule), now):
                    continue
                _STICKER_FLOOD_FIRED[rule.id] = now
            kept.append(match)
        return kept

    @staticmethod
    def _apply_mode(matches: list[RuleMatch], mode: RulesMode) -> list[RuleMatch]:
        """Apply execution mode to optional (non-mandatory) matches."""
        if not matches:
            return []
        if mode == RulesMode.ALL:
            return matches
        if mode == RulesMode.HIGHEST_WEIGHT:
            max_weight = max(m.rule.weight for m in matches)
            top = [m for m in matches if m.rule.weight == max_weight]
            return [random.choice(top)]  # noqa: S311
        if mode == RulesMode.WEIGHTED_RANDOM:
            weights = [m.rule.weight for m in matches]
            return random.choices(matches, weights=weights, k=1)  # noqa: S311
        return matches

    @staticmethod
    def _extract_actions(match: RuleMatch) -> list[RuleActionResult]:
        """Extract action(s) from a triggered rule's config."""
        cfg = match.rule.config
        action_str = cfg.get("action", "notify_admin")

        try:
            action = RuleAction(action_str)
        except ValueError:
            logger.warning("Unknown rule action", action=action_str, rule_id=match.rule.id)
            return []

        return [
            RuleActionResult(
                rule=match.rule,
                action=action,
                params={
                    "match_detail": match.match_detail,
                    "warning_message": cfg.get("warning_message"),
                    "response_template": cfg.get("response_template"),
                },
            )
        ]
