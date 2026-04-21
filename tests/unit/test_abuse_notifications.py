"""Tests for admin abuse notification formatting."""

from __future__ import annotations

from src.services.abuse.notifications import AbuseNotificationService


def _fmt(**overrides):
    """Convenience: call _format_unauthorized with sensible defaults."""
    params = {
        "chat_id": -100,
        "chat_title": "Alpha",
        "chat_type": "group",
        "chat_username": None,
        "user_id": 999,
        "user_first_name": "Alice",
        "user_last_name": None,
        "user_username": "alice",
        "message_text": None,
    }
    params.update(overrides)
    return AbuseNotificationService._format_unauthorized(**params)


class TestFormatUnauthorized:
    def test_supergroup_title_renders_as_link(self):
        text = _fmt(
            chat_id=-1003632335671,
            chat_title="Test Supergroup",
            chat_type="supergroup",
            chat_username=None,
        )
        assert '<a href="https://t.me/c/3632335671">Test Supergroup</a>' in text

    def test_username_takes_priority_over_cid_link(self):
        text = _fmt(
            chat_id=-1003632335671,
            chat_title="Public Chat",
            chat_type="supergroup",
            chat_username="publicchat",
        )
        assert '<a href="https://t.me/publicchat">Public Chat</a>' in text
        assert "t.me/c/" not in text

    def test_old_group_title_plain_text_no_link(self):
        text = _fmt(
            chat_id=-100,
            chat_title="Legacy Group",
            chat_type="group",
            chat_username=None,
        )
        # No anchor wrapping the title
        assert "<a href=" not in text.split("User:")[0]
        assert "Legacy Group" in text

    def test_user_link_still_present(self):
        text = _fmt(user_id=999, user_first_name="Alice")
        assert "tg://user?id=999" in text

    def test_chat_id_still_shown(self):
        text = _fmt(chat_id=-1003632335671, chat_type="supergroup")
        assert "<code>-1003632335671</code>" in text

    def test_no_chat_title_falls_back_to_id_label(self):
        text = _fmt(chat_title=None, chat_id=-1003632335671, chat_type="supergroup")
        # Chat label is the chat_id, still wrapped in link
        assert '<a href="https://t.me/c/3632335671">-1003632335671</a>' in text

    def test_html_escaped_in_title(self):
        text = _fmt(
            chat_title="<script>bad</script>",
            chat_type="group",
            chat_username=None,
        )
        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    def test_private_chat_title_not_wrapped_in_link(self):
        # For private DMs the User block already has a tg://user?id= link,
        # so the Chat line must NOT also render one (avoid duplicate link).
        text = _fmt(
            chat_id=5870677432,
            chat_title="Alice Private",
            chat_type="private",
            chat_username=None,
            user_id=5870677432,
        )
        # Chat: line is plain text
        chat_line = text.split("\n")[2]
        assert "Alice Private" in chat_line
        assert "<a href=" not in chat_line
        # User: line still carries the user link
        assert "tg://user?id=5870677432" in text
