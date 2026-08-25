"""Integration tests: participant aliases against real Postgres (TD-150).

Everything here is an invariant that lives in the *database* and cannot be
demonstrated against a mock. Two of them are partial unique indexes, and a
partial index has a property that is easy to state and easy to forget: rows
outside the predicate are not in it. A test that only ever inserts active rows
proves the index exists and nothing about the case the repository was written
to handle.

Fixture discipline for this file:

* ``db_pool``, never ``db_conn`` -- ``set_primary`` and ``add_alternate`` call
  ``self._pool.acquire()``, which a bare ``asyncpg.Connection`` does not have.
  Isolation therefore comes from a chat-id band nothing else uses rather than
  from transaction rollback, and each test picks its own id inside it.
* Invented ids and invented names. This repository is public, gitleaks cannot
  see a bare Telegram id (it has no credential shape), and the plan-artifact
  guard does not scan ``tests/``, so a real id pasted into a fixture would
  clear every automated gate on its way in.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.database.repositories.aliases import AliasRepository, AliasWriteOutcome
from src.utils.aliases import build_alias_view, normalize_alias, parse_alias

# These tests cannot roll back (see the module docstring), so the chat ids must
# collide with nothing else in tests/integration. Checked, not assumed --
# `grep -rhoE '\-[0-9]{4,}' tests/integration/*.py` shows the -95xxxxx band is
# entirely unused, while the -94xxxxx band this file first claimed overlaps
# test_kb_capture.py at eight ids. Re-run that grep before moving it again.
CHAT_BASE = -950_001
ANNA = 111_001
BORIS = 111_002


@pytest_asyncio.fixture
async def repo(db_pool: asyncpg.Pool) -> AliasRepository:
    return AliasRepository(db_pool)


def _chat(offset: int) -> int:
    return CHAT_BASE - offset


async def _set(repo: AliasRepository, chat_id: int, user_id: int, name: str):
    parsed = parse_alias(name)
    assert parsed.ok, f"fixture name is not storable: {name!r}"
    return await repo.set_primary(
        chat_id=chat_id, user_id=user_id, alias=parsed.display, source="self"
    )


async def _add(repo: AliasRepository, chat_id: int, user_id: int, name: str):
    parsed = parse_alias(name)
    assert parsed.ok, f"fixture name is not storable: {name!r}"
    return await repo.add_alternate(
        chat_id=chat_id, user_id=user_id, alias=parsed.display, source="self"
    )


class TestSetPrimary:
    async def test_a_name_is_stored_and_read_back(self, repo: AliasRepository) -> None:
        chat_id = _chat(1)
        outcome, _ = await _set(repo, chat_id, ANNA, "Аня")

        assert outcome is AliasWriteOutcome.SET
        view = build_alias_view(await repo.load_active(chat_id))
        assert view.primary_by_user == {ANNA: "Аня"}

    async def test_renaming_supersedes_instead_of_accumulating(
        self, repo: AliasRepository, db_pool: asyncpg.Pool
    ) -> None:
        """Two active primaries for one person is what the first partial unique
        forbids, and the repository must retire the old row *before* inserting
        -- the index is checked per statement, so they may not coexist even
        inside the transaction.
        """
        chat_id = _chat(2)
        await _set(repo, chat_id, ANNA, "Аня")
        await _set(repo, chat_id, ANNA, "Анна")

        view = build_alias_view(await repo.load_active(chat_id))
        assert view.primary_by_user == {ANNA: "Анна"}

        statuses = await db_pool.fetch(
            "SELECT alias, status FROM chat_user_aliases WHERE chat_id = $1 ORDER BY id",
            chat_id,
        )
        assert [(r["alias"], r["status"]) for r in statuses] == [
            ("Аня", "superseded"),
            ("Анна", "active"),
        ]

    async def test_the_same_command_twice_changes_nothing(
        self, repo: AliasRepository, db_pool: asyncpg.Pool
    ) -> None:
        """Telegram redelivers updates. Without the equality pre-check this
        would retire a row and insert an identical one -- a supersession record
        for an event that never happened, and two confirmations for one rename.
        """
        chat_id = _chat(3)
        await _set(repo, chat_id, ANNA, "Аня")
        outcome, _ = await _set(repo, chat_id, ANNA, "аня")  # same name, different case

        assert outcome is AliasWriteOutcome.UNCHANGED
        rows = await db_pool.fetchval(
            "SELECT count(*) FROM chat_user_aliases WHERE chat_id = $1", chat_id
        )
        assert rows == 1

    async def test_a_name_another_member_holds_is_refused_by_name(
        self, repo: AliasRepository
    ) -> None:
        """Reported as TAKEN with the owner, not raised: a UniqueViolationError
        cannot say *who* holds the name, and that is the only useful reply.
        """
        chat_id = _chat(4)
        await _set(repo, chat_id, ANNA, "Аня")
        outcome, owner = await _set(repo, chat_id, BORIS, "АНЯ")

        assert outcome is AliasWriteOutcome.TAKEN
        assert owner == ANNA

    async def test_the_same_name_in_another_chat_is_free(self, repo: AliasRepository) -> None:
        """Both uniques are scoped per chat; a name is not a global resource."""
        await _set(repo, _chat(5), ANNA, "Аня")
        outcome, _ = await _set(repo, _chat(6), BORIS, "Аня")

        assert outcome is AliasWriteOutcome.SET


class TestPrimaryOutranksAlternate:
    """The rule that resolves the defect this file shipped without covering.

    Found by five independent review lenses and reproduced before it was fixed:
    the ownership pre-check asked only WHO holds a name, so a row belonging to
    the same user fell through -- but that row could be their own *alternate*,
    which the primary-scoped query cannot see either. The INSERT then hit the
    chat-wide name index, the retry repeated the identical deterministic
    sequence, and the user was told "try again" for ever. The handler seeds
    every renamed person's own account names as alternates, so the feature
    manufactured its own dead end.
    """

    async def test_you_can_promote_your_own_seeded_alternate(self, repo: AliasRepository) -> None:
        """The exact reproduction: rename, get your account name seeded, then
        ask for that account name.
        """
        chat_id = _chat(20)
        await _set(repo, chat_id, ANNA, "Нюра")
        await _add(repo, chat_id, ANNA, "Аня")  # what the seeding loop writes

        outcome, _ = await _set(repo, chat_id, ANNA, "Аня")

        assert outcome is AliasWriteOutcome.SET
        assert build_alias_view(await repo.load_active(chat_id)).primary_by_user == {ANNA: "Аня"}

    async def test_a_second_member_may_claim_a_name_only_an_alternate_held(
        self, repo: AliasRepository
    ) -> None:
        """Two people called Аня. The first renames themselves to something
        else and the seeding loop quietly claims "Аня" for them as a
        recognition name; the second must still be able to BE Аня.
        """
        chat_id = _chat(21)
        await _set(repo, chat_id, ANNA, "Нюра")
        await _add(repo, chat_id, ANNA, "Аня")

        outcome, _ = await _set(repo, chat_id, BORIS, "Аня")

        assert outcome is AliasWriteOutcome.SET
        view = build_alias_view(await repo.load_active(chat_id))
        assert view.primary_by_user[BORIS] == "Аня"
        # ...and the alternate yielded rather than lingering as a second active
        # row, which the chat-wide index would have rejected anyway.
        assert "Аня" not in view.entries[0].alternates

    async def test_a_primary_still_loses_to_another_primary(self, repo: AliasRepository) -> None:
        """The mirror. Only an ALTERNATE yields; a name somebody is actually
        called stays theirs, and the caller is told who holds it.
        """
        chat_id = _chat(22)
        await _set(repo, chat_id, ANNA, "Аня")

        outcome, owner = await _set(repo, chat_id, BORIS, "Аня")

        assert outcome is AliasWriteOutcome.TAKEN
        assert owner == ANNA

    async def test_an_alternate_never_displaces_anything(self, repo: AliasRepository) -> None:
        """Automation writes alternates, so the alternate path must be the
        harmless one: it yields to every active row rather than taking a name.
        """
        chat_id = _chat(23)
        await _set(repo, chat_id, ANNA, "Аня")

        outcome, owner = await _add(repo, chat_id, BORIS, "Аня")

        assert outcome is AliasWriteOutcome.TAKEN
        assert owner == ANNA
        assert build_alias_view(await repo.load_active(chat_id)).primary_by_user == {ANNA: "Аня"}


class TestNormalisationIsTheRepositorysJob:
    async def test_the_stored_norm_is_derived_not_supplied(
        self, repo: AliasRepository, db_pool: asyncpg.Pool
    ) -> None:
        """`alias_norm` used to be a second parameter, i.e. a promise every
        call site had to keep. A row whose norm disagrees with the
        application's is invisible to lookups and unprotected by the index.
        """
        chat_id = _chat(24)
        await repo.set_primary(chat_id=chat_id, user_id=ANNA, alias="  АЛЁНА  ", source="self")

        row = await db_pool.fetchrow(
            "SELECT alias, alias_norm FROM chat_user_aliases WHERE chat_id = $1", chat_id
        )
        # The display form is stored verbatim; only the comparison form is folded.
        assert row["alias"] == "  АЛЁНА  "
        assert row["alias_norm"] == normalize_alias("  АЛЁНА  ") == "алена"

    async def test_case_and_yo_collide_through_the_repository(self, repo: AliasRepository) -> None:
        chat_id = _chat(25)
        await _set(repo, chat_id, ANNA, "Алёна")

        outcome, owner = await _set(repo, chat_id, BORIS, "АЛЕНА")

        assert outcome is AliasWriteOutcome.TAKEN
        assert owner == ANNA


class TestPartialIndexesAreActuallyPartial:
    """The property the repository's pre-checks exist because of."""

    async def test_a_retired_name_frees_itself_for_someone_else(
        self, repo: AliasRepository
    ) -> None:
        chat_id = _chat(7)
        await _set(repo, chat_id, ANNA, "Аня")
        assert await repo.retire(chat_id, "Аня") == 1

        outcome, _ = await _set(repo, chat_id, BORIS, "Аня")
        assert outcome is AliasWriteOutcome.SET

    async def test_a_person_may_take_back_a_name_they_dropped(self, repo: AliasRepository) -> None:
        """A retired row is history, not a conflict. Copying `append_fact`'s
        "exists in any status" check wholesale would have made this impossible,
        which is why this repository's guard is deliberately narrower.
        """
        chat_id = _chat(8)
        await _set(repo, chat_id, ANNA, "Аня")
        await repo.retire(chat_id, "Аня")

        outcome, _ = await _set(repo, chat_id, ANNA, "Аня")

        assert outcome is AliasWriteOutcome.SET
        assert build_alias_view(await repo.load_active(chat_id)).primary_by_user == {ANNA: "Аня"}

    async def test_the_database_itself_refuses_a_duplicate_active_name(
        self, db_pool: asyncpg.Pool
    ) -> None:
        """The backstop, exercised directly. The repository's checks handle the
        sequential case; only the index covers two writers racing past them,
        because with no existing row there is nothing to lock.
        """
        chat_id = _chat(9)
        insert = """
            INSERT INTO chat_user_aliases
                (chat_id, user_id, alias, alias_norm, role, source)
            VALUES ($1, $2, $3, $4, 'primary', 'self')
        """
        await db_pool.execute(insert, chat_id, ANNA, "Аня", "аня")

        with pytest.raises(asyncpg.UniqueViolationError):
            await db_pool.execute(insert, chat_id, BORIS, "Аня", "аня")

    async def test_the_database_itself_refuses_a_second_active_primary(
        self, db_pool: asyncpg.Pool
    ) -> None:
        chat_id = _chat(10)
        insert = """
            INSERT INTO chat_user_aliases
                (chat_id, user_id, alias, alias_norm, role, source)
            VALUES ($1, $2, $3, $4, 'primary', 'self')
        """
        await db_pool.execute(insert, chat_id, ANNA, "Аня", "аня")

        with pytest.raises(asyncpg.UniqueViolationError):
            await db_pool.execute(insert, chat_id, ANNA, "Анна", "анна")

    async def test_two_alternates_for_one_person_are_fine(self, db_pool: asyncpg.Pool) -> None:
        """The mirror of the test above: the primary-uniqueness predicate must
        not accidentally bind alternates, which are supposed to accumulate.
        """
        chat_id = _chat(11)
        insert = """
            INSERT INTO chat_user_aliases
                (chat_id, user_id, alias, alias_norm, role, source)
            VALUES ($1, $2, $3, $4, 'alternate', 'self')
        """
        await db_pool.execute(insert, chat_id, ANNA, "Анька", "анька")
        await db_pool.execute(insert, chat_id, ANNA, "Нюра", "нюра")

        count = await db_pool.fetchval(
            "SELECT count(*) FROM chat_user_aliases WHERE chat_id = $1", chat_id
        )
        assert count == 2


class TestAlternates:
    async def test_alternates_accumulate_under_one_primary(self, repo: AliasRepository) -> None:
        chat_id = _chat(12)
        await _set(repo, chat_id, ANNA, "Аня")
        await _add(repo, chat_id, ANNA, "Анька")
        await _add(repo, chat_id, ANNA, "Нюра")

        entry = build_alias_view(await repo.load_active(chat_id)).entries[0]
        assert entry.primary == "Аня"
        assert set(entry.alternates) == {"Анька", "Нюра"}

    async def test_an_alternate_someone_else_holds_is_refused(self, repo: AliasRepository) -> None:
        chat_id = _chat(13)
        await _set(repo, chat_id, ANNA, "Аня")
        outcome, owner = await _add(repo, chat_id, BORIS, "аня")

        assert outcome is AliasWriteOutcome.TAKEN
        assert owner == ANNA

    async def test_adding_the_same_alternate_twice_is_a_no_op(
        self, repo: AliasRepository, db_pool: asyncpg.Pool
    ) -> None:
        chat_id = _chat(14)
        await _set(repo, chat_id, ANNA, "Аня")
        await _add(repo, chat_id, ANNA, "Анька")
        outcome, _ = await _add(repo, chat_id, ANNA, "АНЬКА")

        assert outcome is AliasWriteOutcome.UNCHANGED
        count = await db_pool.fetchval(
            "SELECT count(*) FROM chat_user_aliases WHERE chat_id = $1", chat_id
        )
        assert count == 2


class TestRetire:
    async def test_retiring_a_name_nobody_holds_reports_zero(self, repo: AliasRepository) -> None:
        """The caller needs to tell "removed" from "there was nothing there",
        or it reports a removal that did not happen.
        """
        assert await repo.retire(_chat(15), "Несуществующее") == 0

    async def test_a_retired_primary_leaves_the_roster(self, repo: AliasRepository) -> None:
        chat_id = _chat(16)
        await _set(repo, chat_id, ANNA, "Аня")
        await repo.retire(chat_id, "Аня")

        assert not build_alias_view(await repo.load_active(chat_id))


class TestLoadActive:
    async def test_the_primary_arrives_before_its_alternates(self, repo: AliasRepository) -> None:
        """`build_alias_view` takes the first primary it sees, and the roster's
        alternate order follows row order. An `ORDER BY role` would sort
        'alternate' first -- alphabetically -- which reads as correct and is
        the opposite of the intent.
        """
        chat_id = _chat(17)
        await _add(repo, chat_id, ANNA, "Анька")
        await _set(repo, chat_id, ANNA, "Аня")

        roles = [r["role"] for r in await repo.load_active(chat_id)]
        assert roles[0] == "primary"

    async def test_only_this_chat_is_returned(self, repo: AliasRepository) -> None:
        await _set(repo, _chat(18), ANNA, "Аня")
        await _set(repo, _chat(19), BORIS, "Борис")

        view = build_alias_view(await repo.load_active(_chat(18)))
        assert view.primary_by_user == {ANNA: "Аня"}
