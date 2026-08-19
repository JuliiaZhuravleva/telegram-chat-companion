"""The chunk indexer's pure decisions (S4).

`_is_open` and `_closed_sessions` decide when a conversation is finished
enough to be frozen into an index row. They need no database, and they are
where the "last by id is not the newest message" confusion keeps reappearing
-- it already cost PR #52 once, in the fetch order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.services.rag.indexer import _closed_sessions, _is_open
from src.services.rag.models import SourceMessage


def _msg(message_id: int, moment: datetime) -> SourceMessage:
    return SourceMessage(
        message_id=message_id,
        created_at=moment,
        text="реплика",
        user_id=501,
        name="Аня",
        is_bot=False,
    )


def _now() -> datetime:
    return datetime.now(UTC)


class TestIsOpen:
    def test_a_live_conversation_is_open(self) -> None:
        assert _is_open([_msg(1, _now() - timedelta(minutes=1))]) is True

    def test_a_finished_conversation_is_closed(self) -> None:
        assert _is_open([_msg(1, _now() - timedelta(hours=5))]) is False

    def test_one_stale_row_does_not_close_a_live_conversation(self) -> None:
        # The rows arrive ordered by `message_id`, so the last one is the
        # highest id -- not the newest message. Reading its timestamp made a
        # one-minute-old conversation look finished, and the chunk seam then
        # landed mid-sentence with the rest of the exchange starting a fresh
        # session on the next pass.
        session = [
            _msg(1, _now() - timedelta(minutes=1)),
            _msg(2, datetime(2020, 1, 1, tzinfo=UTC)),
        ]

        assert _is_open(session) is True

    def test_naive_timestamps_are_read_as_utc(self) -> None:
        naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)

        assert _is_open([_msg(1, naive)]) is True


class TestClosedSessions:
    def test_the_trailing_session_is_deferred_while_it_can_grow(self) -> None:
        live = [_msg(2, _now() - timedelta(minutes=1))]
        done = [_msg(1, _now() - timedelta(hours=9))]

        assert _closed_sessions([done, live], batch_full=False) == [done]

    def test_everything_is_indexed_once_the_last_session_is_finished(self) -> None:
        done = [_msg(1, _now() - timedelta(hours=9))]

        assert _closed_sessions([done], batch_full=False) == [done]

    def test_a_full_batch_defers_its_last_session(self) -> None:
        first = [_msg(1, _now() - timedelta(hours=9))]
        second = [_msg(2, _now() - timedelta(hours=8))]

        assert _closed_sessions([first, second], batch_full=True) == [first]

    def test_a_single_session_filling_the_batch_is_indexed_anyway(self) -> None:
        # Otherwise it would be skipped on every pass for ever -- there is no
        # earlier session to fall back on.
        only = [_msg(1, _now() - timedelta(hours=9))]

        assert _closed_sessions([only], batch_full=True) == [only]
