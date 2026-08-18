"""Tests for scripts/kb_report.py (S0 / KB-01: the KB similarity-floor report)."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from scripts.kb_report import (
    DEFAULT_FLOORS,
    Report,
    extract_sims,
    fetch_turns,
    format_regime_caution,
    format_report,
    main,
    summarize,
)


class TestSummarize:
    """The pure seam. Everything the report claims is arithmetic over this."""

    def test_counts_turns_facts_and_blind_turns(self) -> None:
        report = summarize([[0.9, 0.5], [], [0.3]], floors=(0.4,))
        assert report.turns_total == 3
        assert report.turns_blind_today == 1
        assert report.turns_with_facts == 2
        assert report.facts_total == 3

    def test_floor_partitions_facts(self) -> None:
        report = summarize([[0.9, 0.5, 0.3, 0.1]], floors=(0.5,))
        row = report.floors[0]
        assert row.facts_kept == 2  # 0.9, 0.5 -- the floor is inclusive
        assert row.facts_cut == 2  # 0.3, 0.1
        assert row.facts_total == 4
        assert row.facts_cut_pct == pytest.approx(50.0)

    def test_a_turn_already_empty_is_not_blamed_on_the_floor(self) -> None:
        """The distinction the whole report hangs on.

        A lookup that returned nothing is blind whatever floor is chosen --
        counting it as "newly blind" would make every floor look destructive
        in a chat whose KB is simply small, which is exactly the situation
        this report is run in.
        """
        report = summarize([[], [], [0.9]], floors=(0.5,))
        assert report.turns_blind_today == 2
        assert report.floors[0].turns_newly_blind == 0

    def test_newly_blind_counts_only_turns_whose_best_fact_falls_below(self) -> None:
        # Turn A's best is 0.62 (survives 0.6); turn B's best is 0.45 (does not).
        report = summarize([[0.62, 0.10], [0.45, 0.44]], floors=(0.6,))
        assert report.floors[0].turns_newly_blind == 1

    def test_floor_at_zero_reproduces_todays_behaviour(self) -> None:
        """min_similarity=0.0 is the documented rollback -- it must be a no-op."""
        turns = [[0.9, 0.01], [0.4], []]
        report = summarize(turns, floors=(0.0,))
        row = report.floors[0]
        assert row.facts_cut == 0
        assert row.turns_newly_blind == 0

    def test_percentiles_use_best_per_turn_and_all_facts_separately(self) -> None:
        # One turn with a strong best fact and three weak ones: the two
        # distributions must not be the same number.
        report = summarize([[0.9, 0.1, 0.1, 0.1]], floors=(0.5,))
        assert report.best_sim_percentiles[50] == pytest.approx(0.9)
        assert report.all_sim_percentiles[50] == pytest.approx(0.1)

    def test_empty_input_does_not_raise(self) -> None:
        report = summarize([], floors=DEFAULT_FLOORS)
        assert report.turns_total == 0
        assert report.facts_total == 0
        assert all(row.facts_cut_pct == 0.0 for row in report.floors)


class TestExtractSims:
    def test_reads_a_decoded_list(self) -> None:
        assert extract_sims([{"sim": 0.7}, {"sim": 0.2}]) == [0.7, 0.2]

    def test_reads_jsonb_delivered_as_a_string(self) -> None:
        """asyncpg returns JSONB as str unless a codec is registered."""
        raw = json.dumps([{"id": 1, "sim": 0.55, "injected": True, "head": "x"}])
        assert extract_sims(raw) == [0.55]

    def test_missing_sim_is_skipped_not_zeroed(self) -> None:
        """A None sim must not become 0.0 -- that would invent a below-floor fact."""
        assert extract_sims([{"sim": None}, {"sim": 0.8}]) == [0.8]

    def test_booleans_are_not_similarities(self) -> None:
        # bool is an int subclass in Python; True would otherwise read as 1.0.
        assert extract_sims([{"sim": True}]) == []

    @pytest.mark.parametrize("payload", ["not json", None, 42, {"sim": 0.5}])
    def test_malformed_payload_counts_as_a_blind_turn(self, payload: Any) -> None:
        """Empty list, not an exception, and not a silent drop from the denominator."""
        assert extract_sims(payload) == []


class _FakeTransaction:
    def __init__(self, recorder: dict[str, Any], kwargs: dict[str, Any]) -> None:
        self._recorder = recorder
        self._kwargs = kwargs

    async def __aenter__(self) -> _FakeTransaction:
        self._recorder["entered"] = True
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeConnection:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        recorder: dict[str, Any],
        regime_row: dict[str, Any] | None = None,
    ) -> None:
        self._rows = rows
        self._recorder = recorder
        # Default: a window entirely after R0, i.e. no caution — so every test
        # written before the caution existed keeps asserting what it asserted.
        self._regime_row = regime_row or {"stripped": 1, "unstripped": 0, "pre_r0": 0}

    def transaction(self, **kwargs: Any) -> _FakeTransaction:
        self._recorder["transaction_kwargs"] = kwargs
        return _FakeTransaction(self._recorder, kwargs)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self._recorder["query"] = query
        self._recorder["args"] = args
        return self._rows

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """The regime-split probe (R0/TD-092).

        Answers "all rows are post-R0" by default, so the existing tests keep
        asserting exactly what they asserted before the caution line existed.
        """
        self._recorder["regime_query"] = query
        self._recorder["regime_args"] = args
        return self._regime_row


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        recorder: dict[str, Any],
        regime_row: dict[str, Any] | None = None,
    ) -> None:
        self._conn = _FakeConnection(rows, recorder, regime_row)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)

    async def close(self) -> None:
        return None


class TestFetchTurnsIsReadOnlyAtTheCallSite:
    """`readonly=True` existing is worthless if the query does not run inside it.

    CLAUDE.md: a correct helper is not a used helper -- assert the call site.
    Deleting `readonly=True` from `fetch_turns` must turn this red.
    """

    @pytest.mark.asyncio
    async def test_query_runs_inside_a_readonly_transaction(self) -> None:
        recorder: dict[str, Any] = {}
        pool = _FakePool([{"results": [{"sim": 0.5}]}], recorder)

        await fetch_turns(pool, since_days=30, chat_id=None)  # type: ignore[arg-type]

        assert recorder["transaction_kwargs"] == {"readonly": True}
        assert recorder["entered"] is True

    @pytest.mark.asyncio
    async def test_window_and_chat_filter_are_passed_as_bound_parameters(self) -> None:
        recorder: dict[str, Any] = {}
        pool = _FakePool([], recorder)

        await fetch_turns(pool, since_days=14, chat_id=-1009999990001)  # type: ignore[arg-type]

        assert recorder["args"] == (14, -1009999990001)

    @pytest.mark.asyncio
    async def test_rows_are_decoded_into_per_turn_similarity_lists(self) -> None:
        recorder: dict[str, Any] = {}
        rows = [
            {"results": [{"sim": 0.9}, {"sim": 0.4}]},
            {"results": []},
            {"results": json.dumps([{"sim": 0.6}])},
        ]
        pool = _FakePool(rows, recorder)

        turns = await fetch_turns(pool, since_days=90, chat_id=None)  # type: ignore[arg-type]

        assert turns == [[0.9, 0.4], [], [0.6]]


class TestQueryIsReadOnlyByInspection:
    """Second, independent guard: the SQL itself must contain no write verb.

    Word-boundary matched, not substring: a naive ``"CREATE" in sql`` fires on
    the column name ``created_at`` and reports a write statement that is not
    there. That false positive is how a detector teaches you to ignore it.
    """

    @pytest.mark.parametrize(
        "verb", ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE", "GRANT"]
    )
    def test_query_contains_no_write_statement(self, verb: str) -> None:
        from scripts.kb_report import _QUERY

        assert re.search(rf"\b{verb}\b", _QUERY, re.IGNORECASE) is None

    def test_the_guard_would_catch_a_real_write(self) -> None:
        """Positive control: a broken pattern returns clean exactly like clean SQL does."""
        hostile = "SELECT 1; DELETE FROM retrieval_log"
        assert re.search(r"\bDELETE\b", hostile, re.IGNORECASE) is not None
        # ...and the column name that broke the naive version still reads clean.
        assert re.search(r"\bCREATE\b", "SELECT created_at FROM t", re.IGNORECASE) is None


class TestMainExitCodes:
    """An empty window must fail loudly. A silent zero reads as 'all clear'."""

    @pytest.mark.asyncio
    async def test_empty_window_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def _fake_create_pool(*_args: Any, **_kwargs: Any) -> _FakePool:
            return _FakePool([], {})

        monkeypatch.setattr("scripts.kb_report.asyncpg.create_pool", _fake_create_pool)

        code = await main(["postgresql://u:p@127.0.0.1:5432/db"])

        assert code == 1
        assert "Nothing was measured" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_measured_window_exits_zero_and_prints_the_sweep(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def _fake_create_pool(*_args: Any, **_kwargs: Any) -> _FakePool:
            return _FakePool([{"results": [{"sim": 0.71}]}], {})

        monkeypatch.setattr("scripts.kb_report.asyncpg.create_pool", _fake_create_pool)

        code = await main(["postgresql://u:p@127.0.0.1:5432/db"])

        assert code == 0
        assert "Floor sweep" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_bad_window_is_rejected_before_connecting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _explode(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("must not connect when the window is invalid")

        monkeypatch.setattr("scripts.kb_report.asyncpg.create_pool", _explode)

        assert await main(["postgresql://u:p@127.0.0.1:5432/db", "--since-days", "0"]) == 2


class TestFormatReport:
    def test_names_both_blind_populations_distinctly(self) -> None:
        report = summarize([[0.9], [], [0.2]], floors=(0.5,))
        out = format_report(report, since_days=90)
        assert "already blind today" in out
        assert "turns newly blind" in out

    def test_markdown_mode_emits_a_table(self) -> None:
        report = summarize([[0.9]], floors=(0.5,))
        out = format_report(report, since_days=90, markdown=True)
        assert "| floor | facts kept | facts cut | turns newly blind |" in out

    def test_zero_turns_with_facts_does_not_divide_by_zero(self) -> None:
        report = summarize([[], []], floors=(0.5,))
        assert isinstance(format_report(report, since_days=7), str)


class TestReportInvariants:
    def test_kept_plus_cut_always_equals_the_corpus(self) -> None:
        report: Report = summarize([[0.9, 0.4], [0.55], [], [0.1, 0.2, 0.3]])
        for row in report.floors:
            assert row.facts_kept + row.facts_cut == report.facts_total

    def test_raising_the_floor_never_keeps_more_facts(self) -> None:
        report = summarize([[0.1, 0.3, 0.5, 0.7, 0.9]], floors=(0.2, 0.4, 0.6, 0.8))
        kept = [row.facts_kept for row in report.floors]
        assert kept == sorted(kept, reverse=True)


class TestRegimeCaution:
    """R0/TD-092 — a window that straddles the deploy is two populations.

    The floor sweep is what a reader tunes `knowledge_base.min_similarity`
    from, and similarities recorded before query hygiene sit measurably
    higher on a miss. A blended percentile table presented as one number is
    the failure this line exists to prevent.
    """

    def test_silent_when_every_row_is_post_r0(self) -> None:
        assert format_regime_caution(12, 3, 0) is None

    def test_warns_when_every_row_is_pre_r0(self) -> None:
        """Homogeneous is not the same as current.

        A window entirely before the deploy has no mixture to complain about
        and is still the wrong ruler — the address was in every one of those
        query embeddings. Staying silent here was the first version, and
        silence in a report is read as "these numbers describe today".
        """
        caution = format_regime_caution(0, 0, 9)

        assert caution is not None
        assert "9 lookup(s) predates" in caution
        assert "do not tune a floor" in caution

    def test_silent_on_an_empty_window(self) -> None:
        assert format_regime_caution(0, 0, 0) is None

    def test_warns_and_counts_both_sides_when_the_window_straddles(self) -> None:
        caution = format_regime_caution(4, 2, 7)

        assert caution is not None
        assert "7 lookup(s) predate" in caution
        # 4 + 2: both post-R0 shapes count as one population, and a reader who
        # saw only "4" would think the mix was smaller than it is.
        assert "6 follow it" in caution

    def test_format_report_renders_a_caution_it_is_given(self) -> None:
        """The renderer's own contract — see the next test for the wiring."""
        report = summarize([[0.8], [0.5]], (0.7,))

        rendered = format_report(report, since_days=90, regime_caution="⚠ CANARY")

        assert "⚠ CANARY" in rendered


class TestTheCautionIsActuallyWired:
    """The call site, driven through `main()` — not the helper, not the renderer.

    CLAUDE.md: a correct helper is not a used helper. The first version of this
    suite asserted `format_report` renders an injected string, which is the
    helper's *consumer*; deleting the one line in `main()` that computes the
    caution left 40/40 green. Nothing here hand-injects: the caution has to
    travel main -> fetch_regime_split -> format_regime_caution -> format_report
    on its own, so a deleted kwarg and a permuted argument order both fail.
    """

    @pytest.mark.asyncio
    async def test_a_straddling_window_prints_the_caution(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        recorder: dict[str, Any] = {}
        pool = _FakePool(
            [{"results": [{"sim": 0.8}]}, {"results": [{"sim": 0.5}]}],
            recorder,
            # Deliberately three different numbers: an argument order permuted
            # anywhere in the chain renders a different sentence.
            regime_row={"stripped": 4, "unstripped": 2, "pre_r0": 7},
        )

        async def _fake_create_pool(*_args: Any, **_kwargs: Any) -> _FakePool:
            return pool

        monkeypatch.setattr("scripts.kb_report.asyncpg.create_pool", _fake_create_pool)

        exit_code = await main(["postgresql://seed/db"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "7 lookup(s) predate" in out
        assert "6 follow it" in out

    @pytest.mark.asyncio
    async def test_a_post_r0_window_prints_no_caution(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The negative control: silence must come from the data, not from a
        broken chain that can never speak."""
        recorder: dict[str, Any] = {}
        pool = _FakePool(
            [{"results": [{"sim": 0.8}]}],
            recorder,
            regime_row={"stripped": 9, "unstripped": 0, "pre_r0": 0},
        )

        async def _fake_create_pool(*_args: Any, **_kwargs: Any) -> _FakePool:
            return pool

        monkeypatch.setattr("scripts.kb_report.asyncpg.create_pool", _fake_create_pool)

        exit_code = await main(["postgresql://seed/db"])

        assert exit_code == 0
        assert "predate R0" not in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_the_regime_probe_is_bound_to_the_same_window_and_chat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A caution derived from a different window would be worse than none."""
        recorder: dict[str, Any] = {}
        pool = _FakePool([{"results": [{"sim": 0.8}]}], recorder)

        async def _fake_create_pool(*_args: Any, **_kwargs: Any) -> _FakePool:
            return pool

        monkeypatch.setattr("scripts.kb_report.asyncpg.create_pool", _fake_create_pool)

        await main(["postgresql://seed/db", "--since-days", "14", "--chat-id", "-100777"])

        assert recorder["regime_args"] == (14, -100777)
        assert recorder["args"] == (14, -100777)
