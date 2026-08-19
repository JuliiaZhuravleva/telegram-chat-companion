"""The verdict contract of `scripts/eval_compare.py` (S5).

An exit code is what a wrapper, a cron job or a `&&` reads, and this repo has
already been bitten by the shape where a run that measured nothing exits 0 --
`scripts/eval_rag.py` carries a comment about exactly that. The second half of
the rule was missed on the first pass here and found in review: `measured`
counts cases whose *query embedding* succeeded, which says nothing about
whether either store actually answered. A broken hybrid query fails inside the
per-store loop, gets written into the report as "search failed", and left the
run printing "Wrote 11/11 measured" and exiting 0 with a report containing zero
retrieval rows on one side.
"""

from __future__ import annotations

from scripts.eval_compare import _EXIT_NOTHING_MEASURED, _EXIT_OK, exit_code

MEMORY = "chat_memory (Q&A pairs)"
CHUNKS = "chat_chunks (S5)"


class TestExitCode:
    def test_a_clean_run_passes(self) -> None:
        assert exit_code(11, {}) == _EXIT_OK

    def test_nothing_embedded_is_not_a_result(self) -> None:
        assert exit_code(0, {}) == _EXIT_NOTHING_MEASURED

    def test_one_store_failing_every_case_is_not_a_comparison(self) -> None:
        """The finding. Every case embedded fine, the report looks full, and
        one side of the comparison is empty on every row."""
        assert exit_code(11, {CHUNKS: 11}) == _EXIT_NOTHING_MEASURED

    def test_the_other_store_failing_every_case_is_caught_too(self) -> None:
        """Asserted separately: a check keyed to one label by accident would
        pass the test above and miss the mirror case entirely."""
        assert exit_code(11, {MEMORY: 11}) == _EXIT_NOTHING_MEASURED

    def test_partial_failures_still_pass_but_are_the_caller_s_problem(self) -> None:
        """A comparison with 3 of 11 cases missing on one side is degraded, not
        void -- the remaining 8 are still gradeable. `main()` prints a warning;
        the exit code stays 0 deliberately."""
        assert exit_code(11, {CHUNKS: 3}) == _EXIT_OK

    def test_both_stores_failing_everything_is_caught(self) -> None:
        assert exit_code(11, {MEMORY: 11, CHUNKS: 11}) == _EXIT_NOTHING_MEASURED

    def test_more_failures_than_measured_cases_does_not_slip_through(self) -> None:
        """`>=`, not `==`. Equality is the obvious spelling and it is fragile:
        any future path that counts a failure for a case that did not increment
        `measured` would make the two numbers differ and silently restore the
        exit-0 bug."""
        assert exit_code(5, {CHUNKS: 6}) == _EXIT_NOTHING_MEASURED
