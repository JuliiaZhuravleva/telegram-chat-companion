"""Tests for rules management handlers — focused on confirm-before-delete (TD-004)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.rules import handle_rule_delete, handle_rule_delete_ask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_callback(data: str = "ar_del_ask:ru:42:99:0") -> MagicMock:
    """Mock aiogram CallbackQuery for rules callbacks."""
    from aiogram.types import Message

    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    callback.answer = AsyncMock()

    inner_msg = MagicMock(spec=Message)
    inner_msg.edit_text = AsyncMock()
    inner_msg.chat = MagicMock()
    inner_msg.chat.type = "private"
    callback.message = inner_msg

    return callback


def _make_rules_repo(rule: dict | None = None) -> MagicMock:
    """Mock RulesRepository."""
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=rule)
    repo.delete = AsyncMock()
    repo.get_rules_page = AsyncMock(return_value=([], 0))
    return repo


_SAMPLE_RULE = {
    "id": 42,
    "rule_type": "keyword_trigger",
    "config": {"name": "spam-words", "keywords": ["spam"]},
    "enabled": True,
    "weight": 1,
    "mandatory": False,
    "trigger_count": 0,
    "chat_id": 99,
}


# ---------------------------------------------------------------------------
# handle_rule_delete_ask  (ar_del_ask:)
# ---------------------------------------------------------------------------


class TestHandleRuleDeleteAsk:
    @pytest.mark.asyncio
    async def test_shows_confirm_screen_for_admin(self):
        """Confirm screen shown; no deletion occurs."""
        cb = _make_callback("ar_del_ask:ru:42:99:0")
        repo = _make_rules_repo(_SAMPLE_RULE)

        await handle_rule_delete_ask(cb, repo, is_admin=True)

        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "Удалить" in text or "Delete" in text
        # Rule must NOT be deleted
        repo.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_screen_contains_rule_name(self):
        """The confirm screen displays the rule name from config."""
        cb = _make_callback("ar_del_ask:ru:42:99:0")
        repo = _make_rules_repo(_SAMPLE_RULE)

        await handle_rule_delete_ask(cb, repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "spam-words" in text

    @pytest.mark.asyncio
    async def test_confirm_keyboard_has_yes_and_cancel(self):
        """Yes button targets ar_del:, Cancel returns to ar_list:."""
        cb = _make_callback("ar_del_ask:ru:42:99:0")
        repo = _make_rules_repo(_SAMPLE_RULE)

        await handle_rule_delete_ask(cb, repo, is_admin=True)

        keyboard = cb.message.edit_text.call_args.kwargs.get("reply_markup")
        assert keyboard is not None
        callbacks = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        assert any(c.startswith("ar_del:") for c in callbacks), f"No ar_del: in {callbacks}"
        assert any(c.startswith("ar_list:") for c in callbacks), f"No ar_list: in {callbacks}"

    @pytest.mark.asyncio
    async def test_english_confirm_text(self):
        """English confirm text is rendered correctly."""
        cb = _make_callback("ar_del_ask:en:42:99:0")
        repo = _make_rules_repo(_SAMPLE_RULE)

        await handle_rule_delete_ask(cb, repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "Delete" in text

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        """Non-admin gets access-denied alert; confirm screen not shown."""
        cb = _make_callback("ar_del_ask:ru:42:99:0")
        repo = _make_rules_repo(_SAMPLE_RULE)

        await handle_rule_delete_ask(cb, repo, is_admin=False)

        cb.answer.assert_awaited_once()
        assert cb.answer.call_args.kwargs.get("show_alert") is True
        cb.message.edit_text.assert_not_awaited()
        repo.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_missing_rule(self):
        """Shows not-found message when rule doesn't exist (race condition guard)."""
        cb = _make_callback("ar_del_ask:ru:42:99:0")
        repo = _make_rules_repo(None)  # rule not found

        await handle_rule_delete_ask(cb, repo, is_admin=True)

        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "не найдено" in text or "not found" in text

    @pytest.mark.asyncio
    async def test_handles_json_string_config(self):
        """Rule config stored as JSON string is parsed and name shown."""
        import json

        rule_with_json_config = dict(_SAMPLE_RULE)
        rule_with_json_config["config"] = json.dumps({"name": "json-rule"})
        cb = _make_callback("ar_del_ask:ru:42:99:0")
        repo = _make_rules_repo(rule_with_json_config)

        await handle_rule_delete_ask(cb, repo, is_admin=True)

        text = cb.message.edit_text.call_args.args[0]
        assert "json-rule" in text


# ---------------------------------------------------------------------------
# handle_rule_delete  (ar_del:)
# ---------------------------------------------------------------------------


class TestHandleRuleDelete:
    @pytest.mark.asyncio
    async def test_deletes_rule_and_refreshes_list(self):
        """Confirm step passes — rule is deleted and list refreshed."""
        cb = _make_callback("ar_del:ru:42:99:0")
        repo = _make_rules_repo(_SAMPLE_RULE)

        await handle_rule_delete(cb, repo, is_admin=True)

        repo.delete.assert_awaited_once_with(42)
        cb.answer.assert_awaited_once()
        # List should be re-rendered
        cb.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_non_admin(self):
        """Non-admin cannot execute the delete even if they somehow hit ar_del:."""
        cb = _make_callback("ar_del:ru:42:99:0")
        repo = _make_rules_repo(_SAMPLE_RULE)

        await handle_rule_delete(cb, repo, is_admin=False)

        repo.delete.assert_not_awaited()
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_cancel_stays_on_list(self):
        """Regression: ar_del_ask: Cancel button returns ar_list:, not ar_del:."""
        from src.bot.keyboards.rules import confirm_delete_rule_keyboard

        kb = confirm_delete_rule_keyboard("ru", rule_id=42, chat_id=99, page=0)
        callbacks = [
            btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data
        ]
        # Cancel must go to ar_list:, not ar_del:
        cancel_cbs = [c for c in callbacks if "Отмена" not in c and c.startswith("ar_list:")]
        yes_cbs = [c for c in callbacks if c.startswith("ar_del:")]
        assert cancel_cbs, "Cancel button must target ar_list:"
        assert yes_cbs, "Yes button must target ar_del:"
        # Verify no direct ar_del: in Cancel button
        for c in callbacks:
            if c.startswith("ar_list:"):
                assert "ar_del" not in c


# ---------------------------------------------------------------------------
# Keyboard regression: 🗑 in rules_list_keyboard uses ar_del_ask:
# ---------------------------------------------------------------------------


class TestRulesListKeyboardDeleteButton:
    def test_delete_button_points_to_ask_not_del(self):
        """🗑 in rules list must route through confirmation, not direct delete."""
        from src.bot.keyboards.rules import rules_list_keyboard

        rules = [
            {
                "id": 10,
                "rule_type": "keyword_trigger",
                "config": {"name": "test-rule"},
                "enabled": True,
            }
        ]
        kb = rules_list_keyboard("ru", rules, page=0, total_pages=1, chat_id=99)
        callbacks = [
            btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data
        ]
        # Must have ar_del_ask:, must NOT have ar_del: for the delete action
        assert any(c.startswith("ar_del_ask:") for c in callbacks), "Expected ar_del_ask:"
        # ar_del: should only be in confirm keyboard, not in list keyboard
        assert not any(c.startswith("ar_del:") for c in callbacks), (
            "ar_del: must not appear in list"
        )

    def test_detail_keyboard_delete_uses_ask(self):
        """🗑 Delete button in rule detail view also routes through confirmation."""
        from src.bot.keyboards.rules import rule_detail_keyboard

        kb = rule_detail_keyboard("ru", rule_id=10, chat_id=99)
        callbacks = [
            btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data
        ]
        assert any(c.startswith("ar_del_ask:") for c in callbacks), "Expected ar_del_ask:"
        assert not any(c.startswith("ar_del:") for c in callbacks), (
            "ar_del: must not appear in detail view"
        )
