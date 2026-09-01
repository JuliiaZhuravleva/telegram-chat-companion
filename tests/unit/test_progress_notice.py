"""The placeholder that announces long work, and then becomes its result.

`ProgressNotice` shipped with no tests at all, and a review found the gap the
hard way: `fail()` had no fallback send, so a placeholder that could not be
edited swallowed the failure report *and* left "🎙 Расшифровываю…" standing
over work that was already dead. That is the durable form of the silence this
class exists to remove, reached through the class's own error path.

So the tests here are mostly about the error paths, not the happy one. The
governing rule for every method: **nothing in ProgressNotice may raise into the
caller, and nothing may leave a promise of work that is not coming.**
"""

from __future__ import annotations

import asyncio

import pytest
from aiogram.exceptions import TelegramNetworkError

from src.bot.progress import MIN_SECONDS_TO_ANNOUNCE, ProgressNotice


class FakeMessage:
    """Enough of aiogram's Message to drive the real class.

    Deliberately not a MagicMock: this repo has been bitten by a mock whose
    method was not awaitable, which makes the code under test silently take a
    fallback path while the test reports success.
    """

    def __init__(self, *, chat_id: int = -100123) -> None:
        self.chat = type("Chat", (), {"id": chat_id})()
        self.sent: list[str] = []
        self.answer_error: Exception | None = None
        # Telegram rejects a send whose quote target has been deleted while
        # accepting the identical send without the quote. Modelled separately
        # from `answer_error` because a fake that fails both ways cannot tell a
        # missing fallback from a dead chat.
        self.quoted_answer_error: Exception | None = None
        self.quoted: list[bool] = []
        self._next_id = 900

    async def answer(self, text: str, **kwargs) -> FakeSent:
        is_quoted = kwargs.get("reply_to_message_id") is not None
        if is_quoted and self.quoted_answer_error is not None:
            raise self.quoted_answer_error
        if self.answer_error is not None:
            raise self.answer_error
        self.sent.append(text)
        self.quoted.append(is_quoted)
        self._next_id += 1
        return FakeSent(self, self._next_id, text)


class FakeSent:
    """A message Telegram handed back: editable, deletable, and observable."""

    def __init__(self, origin: FakeMessage, message_id: int, text: str) -> None:
        self.origin = origin
        self.message_id = message_id
        self.text = text
        self.edits: list[str] = []
        self.deleted = False
        self.edit_error: Exception | None = None
        self.delete_error: Exception | None = None

    async def edit_text(self, text: str, **kwargs) -> FakeSent:
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append(text)
        self.text = text
        return self

    async def delete(self) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted = True


class TestFailPath:
    """The defect the review found, and its control."""

    @pytest.mark.asyncio
    async def test_a_failure_reaches_the_chat_even_when_the_edit_fails(self) -> None:
        message = FakeMessage()
        async with ProgressNotice(message, text="🎙 Расшифровываю…") as notice:
            pass
        placeholder = notice.placeholder
        assert isinstance(placeholder, FakeSent)
        placeholder.edit_error = TelegramNetworkError(method=None, message="connection reset")

        await notice.fail("⚠️ Не удалось расшифровать")

        assert any("Не удалось" in text for text in message.sent), (
            "the failure report must reach the chat by SOME route"
        )
        assert placeholder.deleted, (
            "the stale progress line must not sit over work that already failed"
        )

    @pytest.mark.asyncio
    async def test_the_ordinary_failure_edits_the_placeholder_in_place(self) -> None:
        """Control: the fallback must not fire when the edit works."""
        message = FakeMessage()
        async with ProgressNotice(message, text="🎙 Расшифровываю…") as notice:
            pass
        placeholder = notice.placeholder
        assert isinstance(placeholder, FakeSent)

        await notice.fail("⚠️ Не удалось расшифровать")

        assert placeholder.edits == ["⚠️ Не удалось расшифровать"]
        assert not placeholder.deleted
        assert len(message.sent) == 1, "no second message when the edit succeeded"

    @pytest.mark.asyncio
    async def test_a_disabled_notice_says_nothing_at_all(self) -> None:
        """A feature that only announces LONG work must not report on short work."""
        message = FakeMessage()
        async with ProgressNotice(message, text="…", enabled=False) as notice:
            pass

        await notice.fail("⚠️ Не удалось расшифровать")

        assert message.sent == []
        assert notice.placeholder is None

    @pytest.mark.asyncio
    async def test_a_disabled_notice_speaks_when_told_to(self) -> None:
        """The opt-out, for a failure that means the user's message was dropped
        rather than that the bot has nothing to say.

        Without it the duration gate decides whether a download failure is
        reported, and the six measured production failures ran 21-49 seconds
        against a 20-second gate — one of them clearing it by a single second.
        """
        message = FakeMessage()
        async with ProgressNotice(message, text="…", enabled=False) as notice:
            pass

        await notice.fail("⚠️ Не удалось загрузить", report_when_disabled=True)

        assert any("Не удалось загрузить" in text for text in message.sent)

    @pytest.mark.asyncio
    async def test_the_opt_out_is_off_by_default(self) -> None:
        """Control, restating the original decision rather than deleting it: a
        caller that does not ask for it keeps the old silence."""
        message = FakeMessage()
        async with ProgressNotice(message, text="…", enabled=False) as notice:
            pass

        await notice.fail("⚠️ Не удалось расшифровать", report_when_disabled=False)

        assert message.sent == []

    @pytest.mark.asyncio
    async def test_both_routes_failing_is_swallowed_not_raised(self) -> None:
        message = FakeMessage()
        async with ProgressNotice(message, text="…") as notice:
            pass
        placeholder = notice.placeholder
        assert isinstance(placeholder, FakeSent)
        placeholder.edit_error = TelegramNetworkError(method=None, message="down")
        message.answer_error = TelegramNetworkError(method=None, message="down")

        await notice.fail("⚠️ boom")  # must not raise


class TestExceptionPath:
    @pytest.mark.asyncio
    async def test_an_exception_in_the_block_removes_the_placeholder(self) -> None:
        """`finish`/`fail` are called after the block, so a raise skips both."""
        message = FakeMessage()
        with pytest.raises(ValueError):
            async with ProgressNotice(message, text="🎙 Расшифровываю…") as notice:
                raise ValueError("the provider blew up")

        placeholder = notice.placeholder
        assert placeholder is None or placeholder.deleted


class TestFinishPath:
    @pytest.mark.asyncio
    async def test_the_placeholder_becomes_the_first_part(self) -> None:
        message = FakeMessage()
        async with ProgressNotice(message, text="⏳") as notice:
            pass
        placeholder = notice.placeholder
        assert isinstance(placeholder, FakeSent)

        sent = await notice.finish(["<b>one</b>", "two"], language="ru")

        assert placeholder.edits == ["<b>one</b>"], "part one is an EDIT, not a new message"
        assert [m.message_id for m in sent][0] == placeholder.message_id
        assert len(sent) == 2

    @pytest.mark.asyncio
    async def test_nothing_to_deliver_clears_the_placeholder(self) -> None:
        message = FakeMessage()
        async with ProgressNotice(message, text="⏳") as notice:
            pass
        placeholder = notice.placeholder
        assert isinstance(placeholder, FakeSent)

        assert await notice.finish([], language="ru") == []
        assert placeholder.deleted, "an empty result must not leave a promise standing"

    @pytest.mark.asyncio
    async def test_a_failed_edit_falls_back_to_sending_the_whole_result(self) -> None:
        message = FakeMessage()
        async with ProgressNotice(message, text="⏳") as notice:
            pass
        placeholder = notice.placeholder
        assert isinstance(placeholder, FakeSent)
        placeholder.edit_error = TelegramNetworkError(method=None, message="gone")

        sent = await notice.finish(["part one", "part two"], language="ru")

        assert len(sent) == 2
        assert placeholder.deleted
        assert "part one" in message.sent and "part two" in message.sent


class TestTicker:
    @pytest.mark.asyncio
    async def test_it_refreshes_the_placeholder_while_the_work_runs(self) -> None:
        message = FakeMessage()
        async with ProgressNotice(message, text="⏳ working", interval=0.01) as notice:
            await asyncio.sleep(0.05)
        placeholder = notice.placeholder
        assert isinstance(placeholder, FakeSent)

        assert placeholder.edits, "the placeholder never ticked"
        assert all(edit.startswith("⏳ working") for edit in placeholder.edits)

    @pytest.mark.asyncio
    async def test_a_ticker_that_cannot_edit_does_not_kill_the_operation(self) -> None:
        """The ticker is cosmetic; the work it reports on is not."""
        message = FakeMessage()
        completed = False
        async with ProgressNotice(message, text="⏳", interval=0.01) as notice:
            placeholder = notice.placeholder
            assert isinstance(placeholder, FakeSent)
            placeholder.edit_error = TelegramNetworkError(method=None, message="flood")
            await asyncio.sleep(0.05)
            completed = True

        assert completed

    @pytest.mark.asyncio
    async def test_the_ticker_stops_before_the_result_is_written(self) -> None:
        """Otherwise a tick could overwrite the delivered answer with '· 15 s'."""
        message = FakeMessage()
        async with ProgressNotice(message, text="⏳", interval=0.01) as notice:
            await asyncio.sleep(0.03)
        placeholder = notice.placeholder
        assert isinstance(placeholder, FakeSent)

        await notice.finish(["final answer"], language="ru")
        await asyncio.sleep(0.05)  # any surviving ticker would fire in this window

        assert placeholder.text == "final answer"


def test_the_announce_threshold_is_above_a_typical_short_note() -> None:
    """A placeholder that appears and vanishes inside a second reads as a glitch."""
    assert MIN_SECONDS_TO_ANNOUNCE >= 10


class TestTheFailureSurvivesTheSourceBeingDeleted:
    """Quoting the source was added so a failure line in a busy group names the
    message it is about. Adding it WITHOUT a fallback reintroduced the silence
    this whole class exists to remove — the author can delete the voice note
    during the thirty seconds its download is stalling, and Telegram then
    rejects the quoted send outright.
    """

    @pytest.mark.asyncio
    async def test_it_falls_back_to_an_unquoted_send(self) -> None:
        message = FakeMessage()
        message.quoted_answer_error = TelegramNetworkError(
            method=None, message="message to be replied not found"
        )
        async with ProgressNotice(
            message, text="…", enabled=False, reply_to_message_id=42
        ) as notice:
            pass

        await notice.fail("⚠️ Не удалось загрузить", report_when_disabled=True)

        assert message.sent == ["⚠️ Не удалось загрузить"]
        assert message.quoted == [False], "the fallback must drop the quote, not keep it"

    @pytest.mark.asyncio
    async def test_it_quotes_when_it_can(self) -> None:
        """Control: the fallback must not fire when quoting works, or the
        feature it is protecting never happens."""
        message = FakeMessage()
        async with ProgressNotice(
            message, text="…", enabled=False, reply_to_message_id=42
        ) as notice:
            pass

        await notice.fail("⚠️ Не удалось загрузить", report_when_disabled=True)

        assert message.quoted == [True]
        assert len(message.sent) == 1, "one message, not one per attempt"
