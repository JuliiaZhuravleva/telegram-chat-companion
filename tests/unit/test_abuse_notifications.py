"""The access card's text: links, ids, escaping.

The card used to be formatted inside ``AbuseNotificationService``; it is now
built by ``screens/cards.py`` and the service only delivers it. The checks
below moved with it and read the rendered text, which is what the admin sees.
"""

from __future__ import annotations

from src.bot.screens import cards
from src.bot.screens.render import render_text


def _fmt(**overrides):
    """Render the access card with sensible defaults."""
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
    return render_text(cards.access_card("en", cards.Applicant(**params), attempt_id=None))


class TestAccessCardText:
    def test_supergroup_title_renders_as_link(self):
        text = _fmt(
            chat_id=-1001234567890,
            chat_title="Test Supergroup",
            chat_type="supergroup",
            chat_username=None,
        )
        assert '<a href="https://t.me/c/1234567890">Test Supergroup</a>' in text

    def test_username_takes_priority_over_cid_link(self):
        text = _fmt(
            chat_id=-1001234567890,
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
        chat_line = next(line for line in text.splitlines() if "Legacy Group" in line)
        assert "<a href=" not in chat_line

    def test_user_link_still_present(self):
        text = _fmt(user_id=999, user_first_name="Alice")
        assert "tg://user?id=999" in text

    def test_chat_id_still_shown(self):
        text = _fmt(chat_id=-1001234567890, chat_type="supergroup")
        assert "<code>-1001234567890</code>" in text

    def test_no_chat_title_falls_back_to_id_label(self):
        text = _fmt(chat_title=None, chat_id=-1001234567890, chat_type="supergroup")
        # A neutral label, still a link; the id follows once, in <code>
        assert '<a href="https://t.me/c/1234567890">Untitled chat</a>' in text
        assert text.count("-1001234567890") == 1

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
            chat_id=1234567890,
            chat_title="Alice Private",
            chat_type="private",
            chat_username=None,
            user_id=1234567890,
        )
        # The chat line is plain text
        chat_line = text.split("\n")[2]
        assert "Alice Private" in chat_line
        assert "<a href=" not in chat_line
        # The person's line still carries the user link
        assert "tg://user?id=1234567890" in text

    def test_an_add_is_not_headed_as_a_request(self):
        """A card produced by the bot being ADDED is not someone asking."""
        text = _fmt(added=True)
        assert text.startswith("➕")
        assert "asks for access" not in text

    def test_the_message_is_quoted_and_cut(self):
        text = _fmt(message_text="x" * 300)
        assert "💬 " + "x" * cards.MESSAGE_PREVIEW_CHARS in text
        assert "x" * (cards.MESSAGE_PREVIEW_CHARS + 1) not in text

    def test_admin_language_is_the_card_language(self):
        ru = render_text(cards.access_card("ru", cards.Applicant(chat_id=-100), attempt_id=None))
        assert ru.startswith("🙏 <b>Новый чат просит доступ</b>")


class TestEverySenderHasACaller:
    """A sender nobody calls is a notification admins can never receive.

    ``notify_jailbreak``, ``notify_blacklist`` and ``notify_ai_fallback`` sat
    here with no caller while their settings suggested they worked. Each public
    ``notify_*`` method must be called somewhere in ``src/`` outside this module.
    """

    def test_each_notify_method_is_called_from_src(self):
        import inspect
        import re
        from pathlib import Path

        from src.services.abuse import notifications
        from src.services.abuse.notifications import AbuseNotificationService

        senders = [
            name
            for name, _ in inspect.getmembers(AbuseNotificationService, inspect.isfunction)
            if name.startswith("notify_")
        ]
        assert senders, "the control: the service has at least one sender"

        own = Path(notifications.__file__).resolve()
        src = own.parents[2]  # src/services/abuse/notifications.py → src
        sources = [p.read_text() for p in src.rglob("*.py") if p.resolve() != own]
        uncalled = [
            name for name in senders if not any(re.search(rf"\.{name}\(", text) for text in sources)
        ]
        assert uncalled == []
