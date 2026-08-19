"""
Integration test: hybrid retrieval over `chat_chunks` (S5) -- the read side.

`ChunkRepository.search()` is one SQL statement doing four separable jobs:
two independent rankings, an RRF fusion, an AND->OR relaxation, and a set of
filters that must apply identically to every leg. None of those is checkable
by reading the query -- a rank computed over the wrong row order, a filter
applied to one leg only, or a relaxation that never fires all produce
plausible-looking output. So each is asserted here against a real pg16 with
the migration's own generated `tsvector`.

Vectors are planar by construction (`_planar`): the query sits at 0 degrees
and every chunk at a chosen angle, so cosine similarity is exactly
`cos(angle)` and the vector leg's ranking is arithmetic rather than a fact
about an embedding model. One-hot vectors -- the convention in the sibling
`chat_memory` tests -- give only 1.0 or 0.0, which cannot express "second
best" and so cannot test a ranking at all.
"""

from __future__ import annotations

import math
import pathlib
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.chunks import ChunkRepository

_EMBED_DIM = 768

CHAT_A = -100777000111
CHAT_B = -100777000222

_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _planar(degrees: float, *, dim: int = _EMBED_DIM) -> list[float]:
    """A unit vector at `degrees` from the query vector `_planar(0)`.

    Cosine similarity against `_planar(0)` is exactly `cos(degrees)`, so a
    fixture can order rows by choosing angles and the expected ranking is
    readable from the fixture table itself.
    """
    vec = [0.0] * dim
    vec[0] = math.cos(math.radians(degrees))
    vec[1] = math.sin(math.radians(degrees))
    return vec


QUERY_VEC = _planar(0)

# The fixture. `angle` is the whole vector-leg story; `content` is the whole
# FTS story. They are chosen to disagree, because a fixture where the two legs
# rank alike cannot show that fusing them does anything.
#
#   key        content                                    angle   cos
#   BOTH       "перенести релиз" present, near in space    25     0.906
#   VEC_ONLY   no shared lexeme, nearest in space           5     0.996
#   FTS_ONLY   "релиз" present, orthogonal in space        88     0.035
_ROWS: list[tuple[str, str, float, int]] = [
    ("BOTH", "Договорились перенести релиз на пятницу", 25.0, 1000),
    ("VEC_ONLY", "Всё сдвинули к концу недели, решили не спешить", 5.0, 2000),
    ("FTS_ONLY", "Кто-нибудь помнит пароль от релиза", 88.0, 3000),
    ("YO", "Поставили ёлку в офисе", 70.0, 4000),
]


@pytest_asyncio.fixture(autouse=True)
async def _seed(db_pool: asyncpg.Pool) -> dict[str, int]:
    """Wipe both fixture chats and re-seed. Returns key -> row id."""
    await db_pool.execute(
        "DELETE FROM chat_chunks WHERE chat_id = ANY($1::bigint[])",
        [CHAT_A, CHAT_B],
    )
    ids: dict[str, int] = {}
    for key, content, angle, msg_from in _ROWS:
        ids[key] = await _insert(db_pool, CHAT_A, content, angle, msg_from)
    # CHAT_B holds a row that is a perfect match on BOTH counts. Anything that
    # loses the chat filter surfaces it immediately, in every test.
    ids["OTHER_CHAT"] = await _insert(
        db_pool, CHAT_B, "Договорились перенести релиз на пятницу", 0.0, 1000
    )
    return ids


async def _insert(
    pool: asyncpg.Pool,
    chat_id: int,
    content: str,
    angle: float,
    msg_from: int,
    *,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    senders: list[int] | None = None,
) -> int:
    started = started_at or _T0
    row_id: int = await pool.fetchval(
        """
        INSERT INTO chat_chunks
            (chat_id, thread_id, msg_from, msg_to, part, content, msg_count,
             senders, started_at, ended_at, embedding, emb_model, emb_task_type)
        VALUES ($1, NULL, $2, $3, 0, $4, 5, $5::bigint[], $6, $7, $8,
                'test-model', 'RETRIEVAL_DOCUMENT')
        RETURNING id
        """,
        chat_id,
        msg_from,
        msg_from + 9,
        content,
        senders or [111, 222],
        started,
        ended_at or (started + timedelta(minutes=30)),
        _planar(angle),
    )
    return row_id


@pytest.fixture
def repo(db_pool: asyncpg.Pool) -> ChunkRepository:
    return ChunkRepository(db_pool)


def _keys(rows: list[asyncpg.Record], ids: dict[str, int]) -> list[str]:
    """Result rows as fixture keys, so failures name the row not its id."""
    by_id = {v: k for k, v in ids.items()}
    return [by_id.get(r["id"], f"unknown:{r['id']}") for r in rows]


class TestBothLegsContribute:
    """Each leg must find something the other cannot, or the hybrid is theatre."""

    async def test_the_vector_leg_finds_what_shares_no_words_with_the_query(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        rows = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            fts_weight=0.0,
            limit=1,
        )
        # VEC_ONLY shares not one lexeme with the query; only the vector leg
        # can reach it, and it is nearest in space.
        assert _keys(rows, _seed) == ["VEC_ONLY"]
        assert rows[0]["vec_rank"] == 1
        assert rows[0]["fts_rank"] is None

    async def test_the_fts_leg_reaches_a_row_the_vector_leg_cannot(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """FTS_ONLY sits at 88 degrees -- vector rank 4 of 4, so `depth=2` puts
        it outside the vector leg altogether -- and is the only row carrying
        the token. Reaching it at all is the claim; where it lands among the
        rows the other leg contributed is a separate question (both legs' #1
        score 1/61 and the tie falls to `id`, which is a fixture detail, not
        a property worth asserting)."""
        rows = await repo.search(
            CHAT_A,
            query_text="пароль",
            query_embedding=QUERY_VEC,
            limit=3,
            depth=2,
        )
        assert "FTS_ONLY" in _keys(rows, _seed)
        found = next(r for r in rows if r["id"] == _seed["FTS_ONLY"])
        assert found["fts_rank"] == 1
        assert found["vec_rank"] is None

    async def test_control_the_same_query_with_the_fts_leg_switched_off(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """The negative control for the test above: with `fts_weight=0` the
        lexical hit must be gone, not merely last. A zero weight that only
        zeroes the score still lets its leg nominate candidates -- they arrive
        with `rrf_score = 0` and nothing marks them as unwanted, so they reach
        a prompt exactly like a real hit. That is what this control caught."""
        rows = await repo.search(
            CHAT_A,
            query_text="пароль",
            query_embedding=QUERY_VEC,
            limit=3,
            depth=2,
            fts_weight=0.0,
        )
        assert "FTS_ONLY" not in _keys(rows, _seed)
        assert all(r["rrf_score"] > 0 for r in rows)

    async def test_similarity_is_reported_for_a_row_only_the_fts_leg_found(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """Cosine is computed over the fused set, not carried from the vector
        leg -- otherwise every FTS-only row would arrive with `similarity`
        NULL and a floor calibrated on that column (S6) would reject the whole
        lexical half of retrieval."""
        rows = await repo.search(
            CHAT_A,
            query_text="пароль",
            query_embedding=QUERY_VEC,
            limit=3,
            depth=2,
        )
        found = next(r for r in rows if r["id"] == _seed["FTS_ONLY"])
        assert found["vec_rank"] is None
        assert found["similarity"] == pytest.approx(math.cos(math.radians(88)), abs=1e-4)


class TestRrfFusion:
    async def test_a_row_both_legs_like_beats_the_top_of_either_leg_alone(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """The whole point of fusing. BOTH is second in the vector leg and
        first in the FTS leg; VEC_ONLY is first in the vector leg and absent
        from the FTS leg. RRF: 1/62 + 1/61 > 1/61."""
        rows = await repo.search(
            CHAT_A, query_text="перенести релиз", query_embedding=QUERY_VEC, limit=3
        )
        assert _keys(rows, _seed)[:2] == ["BOTH", "VEC_ONLY"]
        assert rows[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61, abs=1e-9)
        assert rows[1]["rrf_score"] == pytest.approx(1 / 61, abs=1e-9)

    async def test_weights_can_flip_the_order(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """The weights are the tuning knob S6 sweeps, so they have to actually
        move the result -- a knob that is wired to nothing is worse than no
        knob, because a sweep over it reports 'no effect found'."""
        rows = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            limit=2,
            vector_weight=10.0,
            fts_weight=0.1,
        )
        assert _keys(rows, _seed)[0] == "VEC_ONLY"

    async def test_depth_defaults_to_twice_the_limit(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """A row neither leg puts first can still win -- but only if each leg
        was asked for more rows than the caller wants back. At `limit=1` the
        default depth is 2, so BOTH (rank 2 by vector, rank 1 by FTS) enters
        the fusion and 1/62 + 0.9/61 beats VEC_ONLY's 1/61.

        The weights are deliberately unequal. With both at 1.0 the two legs'
        #1 rows score 1/61 each and the winner falls to `id` -- a fixture
        detail. The first version of this test did exactly that and passed
        identically with the default changed to `limit`, i.e. it asserted
        nothing. The mutation run is what said so."""
        rows = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            limit=1,
            fts_weight=0.9,
        )
        assert _keys(rows, _seed) == ["BOTH"]

    async def test_control_depth_one_cannot_see_it(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        rows = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            limit=1,
            depth=1,
            fts_weight=0.9,
        )
        # One row shallower per leg: BOTH is rank 2 by vector and never enters
        # the fusion, so the winner is whichever leg's #1 scores higher --
        # VEC_ONLY at 1/61 against BOTH's own 0.9/61.
        assert _keys(rows, _seed) == ["VEC_ONLY"]
        assert rows[0]["rrf_score"] == pytest.approx(1 / 61, abs=1e-9)


class TestChatScoping:
    async def test_another_chats_perfect_match_is_never_returned(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """CHAT_B holds a row that wins on both legs -- identical text, exact
        vector. Only `WHERE chat_id = $1` keeps it out."""
        rows = await repo.search(
            CHAT_A, query_text="перенести релиз", query_embedding=QUERY_VEC, limit=10
        )
        assert "OTHER_CHAT" not in _keys(rows, _seed)
        assert all(r["chat_id"] == CHAT_A for r in rows)

    async def test_control_the_other_chats_row_would_win_if_it_could(
        self, db_pool: asyncpg.Pool, _seed: dict[str, int]
    ) -> None:
        """The mandatory negative control (same rule as the `chat_memory`
        chat-scoping test): prove the fixture row is not merely absent because
        it ranks badly. Run the vector leg with the chat predicate removed and
        watch it come first."""
        rows = await db_pool.fetch(
            """
            SELECT id FROM chat_chunks
            WHERE embedding IS NOT NULL AND chat_id = ANY($2::bigint[])
            ORDER BY embedding <=> $1::vector
            LIMIT 1
            """,
            QUERY_VEC,
            [CHAT_A, CHAT_B],
        )
        assert [r["id"] for r in rows] == [_seed["OTHER_CHAT"]]


class TestOrRelaxation:
    async def test_a_query_matching_nothing_strictly_falls_back_to_or(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """`websearch_to_tsquery` ANDs its terms, so one unknown word in a
        three-word question silently empties the lexical leg. Relaxation is
        what stops a typo from costing the whole leg."""
        rows = await repo.search(
            CHAT_A,
            query_text="пароль пятница картошка",
            query_embedding=None,
            limit=5,
        )
        assert rows, "relaxed query returned nothing"
        assert rows[0]["fts_relaxed"] is True
        assert set(_keys(rows, _seed)) == {"FTS_ONLY", "BOTH"}

    async def test_no_relaxation_when_the_strict_form_matches(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        rows = await repo.search(
            CHAT_A, query_text="перенести релиз", query_embedding=None, limit=5
        )
        assert _keys(rows, _seed) == ["BOTH"]
        assert rows[0]["fts_relaxed"] is False

    async def test_a_negated_query_is_never_relaxed(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """`картошка -релиз` means "картошка without релиз" and matches nothing.
        OR-ing it would turn the exclusion into `картошка OR NOT релиз`, which
        matches almost everything -- the opposite of what was asked. So the
        empty result stands."""
        rows = await repo.search(
            CHAT_A, query_text="картошка -релиз", query_embedding=None, limit=5
        )
        assert rows == []

    async def test_control_the_same_query_without_the_negation_does_relax(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """Proves the test above is about the negation and not about the words:
        drop the `-` and the identical term set relaxes and returns rows."""
        rows = await repo.search(CHAT_A, query_text="картошка релиз", query_embedding=None, limit=5)
        assert rows, "the un-negated form should have relaxed"
        assert rows[0]["fts_relaxed"] is True


class TestQuerySideNormalisation:
    async def test_yo_matches_ye_in_both_directions(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """Characterisation, **not** a guard -- it cannot fail, and that is
        recorded here rather than left for the next reader to assume otherwise.

        Migration 029 claimed the `translate(content, 'ёЁ', 'еЕ')` in the
        generated column is what makes "ёлка" and "елка" find each other, so
        the query side had to repeat it. Measured on pg16 during S5, the
        `russian` configuration folds ё→е on its own: `to_tsvector('russian',
        'ёлка')` and `...'елка'` are both `'елк'`, and so is "зёшка" → 'зешк',
        a word no stemmer can carry a rule for. Deleting the `translate()`
        from the query side leaves this test green -- the mutation run
        demonstrated exactly that.

        It stays because the behaviour it describes is what users depend on and
        is worth catching if a configuration change ever removes it. The
        falsifiable half of the invariant is the next test."""
        for spelling in ("ёлка", "елка"):
            rows = await repo.search(CHAT_A, query_text=spelling, query_embedding=None, limit=5)
            assert _keys(rows, _seed) == ["YO"], f"query {spelling!r} found nothing"

    def test_the_query_normalises_exactly_as_the_generated_column_does(self) -> None:
        """The invariant that CAN break: the two halves must be edited together.

        Index side and query side each apply a normalisation expression before
        `to_tsvector`/`to_tsquery`. Today both are no-ops, so no behavioural
        test can watch them diverge -- but the day the configuration stops
        folding ё itself, a query still calling `translate` against an index
        that no longer does (or the reverse) silently stops matching half the
        corpus. Comparing the two expressions in source is the only check here
        with a failure mode, so it is the one worth having."""
        migration = pathlib.Path("alembic/versions/029_chat_chunks.py").read_text()
        repo_src = pathlib.Path("src/database/repositories/chunks.py").read_text()
        index_side = "translate(content, 'ёЁ', 'еЕ')"
        assert index_side in migration, "migration 029 no longer normalises this way"
        assert index_side.replace("content", "$7") in repo_src, (
            "the query side no longer mirrors migration 029's normalisation"
        )


class TestFilters:
    async def test_before_excludes_a_chunk_that_had_not_ended_yet(
        self, repo: ChunkRepository, db_pool: asyncpg.Pool, _seed: dict[str, int]
    ) -> None:
        """The eval harness replays a historical question and must not be shown
        the chunk that question sits in -- which is usually the chunk holding
        its answer, i.e. the self-retrieval trap S3-3 found one table over."""
        late = await _insert(
            db_pool,
            CHAT_A,
            "Ещё раз про перенести релиз, теперь позже",
            1.0,
            9000,
            started_at=_T0 + timedelta(days=1),
        )
        rows = await repo.search(
            CHAT_A, query_text="перенести релиз", query_embedding=QUERY_VEC, limit=10
        )
        assert late in [r["id"] for r in rows]

        bounded = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            limit=10,
            before=_T0 + timedelta(hours=1),
        )
        assert late not in [r["id"] for r in bounded]
        assert _seed["BOTH"] in [r["id"] for r in bounded]

    async def test_after_excludes_an_older_chunk(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        rows = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            limit=10,
            after=_T0 + timedelta(days=365),
        )
        assert rows == []

    async def test_senders_filter_applies_to_both_legs(
        self, repo: ChunkRepository, db_pool: asyncpg.Pool, _seed: dict[str, int]
    ) -> None:
        """A filter that reaches only one leg is the quiet failure this shares
        `_CHUNK_FILTERS` to prevent: the excluded row would vanish from the
        vector ranking and walk back in through FTS."""
        other = await _insert(
            db_pool,
            CHAT_A,
            "Договорились перенести релиз, другой автор",
            2.0,
            9500,
            senders=[999],
        )
        rows = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            limit=10,
            senders=[999],
        )
        assert [r["id"] for r in rows] == [other]


class TestDegradedModes:
    async def test_without_an_embedding_the_lexical_leg_still_answers(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """What a failed embedding call should cost: the vector leg, not the
        turn. The Q&A path retrieves nothing at all in this state."""
        rows = await repo.search(
            CHAT_A, query_text="перенести релиз", query_embedding=None, limit=5
        )
        assert _keys(rows, _seed) == ["BOTH"]
        assert rows[0]["similarity"] is None
        assert rows[0]["vec_rank"] is None

    async def test_an_unembedded_chunk_is_not_returned_by_the_vector_leg(
        self, repo: ChunkRepository, db_pool: asyncpg.Pool, _seed: dict[str, int]
    ) -> None:
        """`NULL <=> $vec` is NULL and NULLs sort last in ASC, so on a small
        chat a pending row can drift into the vector leg's LIMIT and be ranked
        as if it were a distant match."""
        pending: int = await db_pool.fetchval(
            """
            INSERT INTO chat_chunks
                (chat_id, thread_id, msg_from, msg_to, part, content, msg_count,
                 senders, started_at, ended_at)
            VALUES ($1, NULL, 9900, 9909, 0, 'ещё не посчитан', 3,
                    '{111}'::bigint[], $2, $2)
            RETURNING id
            """,
            CHAT_A,
            _T0,
        )
        rows = await repo.search(
            CHAT_A, query_text="перенести релиз", query_embedding=QUERY_VEC, limit=50
        )
        assert pending not in [r["id"] for r in rows]

    async def test_a_query_with_no_lexemes_leaves_the_vector_leg_working(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """Stopwords-only, punctuation, or an empty string produce an empty
        tsquery, which matches nothing. That must not take the turn down."""
        rows = await repo.search(
            CHAT_A, query_text="и в на ???", query_embedding=QUERY_VEC, limit=2
        )
        assert _keys(rows, _seed) == ["VEC_ONLY", "BOTH"]
        assert all(r["fts_rank"] is None for r in rows)


class TestFilterArgumentGuards:
    async def test_an_empty_sender_list_is_refused_rather_than_ignored(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """`senders @> '{}'` is TRUE for every row, so an empty list would
        widen the query to the whole chat while the caller believed it had
        narrowed it -- and nothing in the result would say so."""
        with pytest.raises(ValueError, match="senders=\\[\\]"):
            await repo.search(
                CHAT_A,
                query_text="перенести релиз",
                query_embedding=QUERY_VEC,
                senders=[],
            )

    async def test_none_still_means_no_filter(
        self, repo: ChunkRepository, _seed: dict[str, int]
    ) -> None:
        """The control: `None` is the supported way to say "no sender filter",
        so the guard above must not have made both spellings fail."""
        rows = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            senders=None,
            limit=10,
        )
        assert len(rows) > 1

    async def test_senders_means_contains_all_not_any(
        self, repo: ChunkRepository, db_pool: asyncpg.Pool, _seed: dict[str, int]
    ) -> None:
        """`@>` is "contains all of". Asserted because the alternative (`&&`,
        overlap) is the more commonly expected reading, and a query silently
        returning too little is harder to notice than one returning too much."""
        solo = await _insert(
            db_pool,
            CHAT_A,
            "Договорились перенести релиз, один автор",
            3.0,
            9700,
            senders=[111],
        )
        both = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            limit=10,
            senders=[111, 222],
        )
        assert solo not in [r["id"] for r in both], "@> must require every listed sender"
        assert _seed["BOTH"] in [r["id"] for r in both]

        one = await repo.search(
            CHAT_A,
            query_text="перенести релиз",
            query_embedding=QUERY_VEC,
            limit=10,
            senders=[111],
        )
        assert solo in [r["id"] for r in one]


class TestDeterminism:
    async def test_tied_vector_distances_break_on_id_not_at_random(
        self, repo: ChunkRepository, db_pool: asyncpg.Pool, _seed: dict[str, int]
    ) -> None:
        """Characterisation: repeated identical queries return an identical
        ranking. It passes with the tiebreak removed, and that is recorded here
        rather than left to be assumed -- three separate mutations of `, c.id`
        (both windows and the final ORDER BY) survive this test.

        The clause is still load-bearing; the suite just cannot force the
        condition that reveals it. Proven directly against pg16 instead, and
        the result is what the next reader needs:

            CREATE TEMP TABLE t (id bigserial primary key, e vector(3));
            INSERT INTO t(e) VALUES ('[1,0,0]'),('[1,0,0]'),
                                    ('[1,0,0]'),('[1,0,0]');
            UPDATE t SET e = e WHERE id = 1;   -- moves the tuple

            physical order (by ctid): 2,3,4,1
            ORDER BY e <=> '[1,0,0]'          -> 2,3,4,1   (scan order wins)
            ORDER BY e <=> '[1,0,0]', id      -> 1,2,3,4

        So with equidistant rows and no tiebreak, the ranking is a function of
        physical layout -- which `UPDATE`, `VACUUM` and autovacuum all change
        under a live table. Inside this fixture the same `UPDATE` does not
        relocate the tuple, so the inversion cannot be staged here.

        Exact float ties need identical content, which the natural key mostly
        prevents, so the practical exposure is small. The cost of the clause is
        six characters; see `TestPgvectorOrderingWithoutATiebreak` below for
        the falsifiable half.
        """
        first = await _insert(db_pool, CHAT_A, "одинаковое расстояние, раз", 45.0, 9100)
        second = await _insert(db_pool, CHAT_A, "одинаковое расстояние, два", 45.0, 9200)
        orders = []
        for _ in range(5):
            rows = await repo.search(
                CHAT_A,
                query_text="одинаковое расстояние",
                query_embedding=QUERY_VEC,
                fts_weight=0.0,
                limit=10,
            )
            ids = [r["id"] for r in rows if r["id"] in {first, second}]
            orders.append(ids)

        assert all(o == orders[0] for o in orders), f"ranking is not stable: {orders}"
        assert orders[0] == sorted(orders[0]), (
            f"tied rows must fall back to ascending id, got {orders[0]}"
        )


class TestPgvectorOrderingWithoutATiebreak:
    """The property `, c.id` exists for, asserted where it CAN fail.

    Self-contained (a TEMP table, four identical vectors) so it does not depend
    on `chat_chunks`' physical layout, which is what defeats the test above.
    """

    async def test_equidistant_rows_follow_physical_order_without_a_tiebreak(
        self, db_conn: asyncpg.Connection
    ) -> None:
        await db_conn.execute("CREATE TEMP TABLE tie_probe (id bigserial primary key, e vector(3))")
        await db_conn.execute(
            "INSERT INTO tie_probe(e) VALUES ('[1,0,0]'),('[1,0,0]'),('[1,0,0]'),('[1,0,0]')"
        )
        # Relocate row 1 so physical order stops matching id order.
        await db_conn.execute("UPDATE tie_probe SET e = e WHERE id = 1")

        physical = [r["id"] for r in await db_conn.fetch("SELECT id FROM tie_probe ORDER BY ctid")]
        untied = [
            r["id"]
            for r in await db_conn.fetch(
                "SELECT id FROM tie_probe ORDER BY e <=> '[1,0,0]'::vector"
            )
        ]
        tied = [
            r["id"]
            for r in await db_conn.fetch(
                "SELECT id FROM tie_probe ORDER BY e <=> '[1,0,0]'::vector, id"
            )
        ]

        assert physical != [1, 2, 3, 4], (
            "the UPDATE did not relocate the tuple, so this probe proves nothing"
        )
        assert untied == physical, "without a tiebreak the scan order should decide"
        assert tied == [1, 2, 3, 4], "with a tiebreak the ranking must be a function of the data"
