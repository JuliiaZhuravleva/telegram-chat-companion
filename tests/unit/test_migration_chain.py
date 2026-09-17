"""The migration chain must have exactly one head, and it must be connected.

Two branches developed in parallel each added a migration numbered 034 on top
of 033 (2026-09-17). Nothing would have caught it: both branches were green on
their own, because each has a valid chain in isolation. The collision only
exists in the merge result, and it is not a test failure there either -- it is
`alembic upgrade head` refusing to run, which on this project means the
production deployer's rehearsal gate fails, and a failed rehearsal is terminal:
one line in the deploy log, no retry, the release simply never happens until
someone pushes a new commit.

Parsing the files rather than asking alembic keeps this in the `test` job with
no database. `tests/integration/test_alembic_online_upgrade.py` still owns the
question of whether the statements themselves execute.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

_REVISION = re.compile(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
_DOWN = re.compile(
    r"^down_revision(?::\s*[\w|\[\]\s\.]+)?\s*=\s*(?:[\"']([^\"']+)[\"']|None)", re.MULTILINE
)


def _chain() -> dict[str, tuple[str | None, str]]:
    """revision -> (down_revision, filename) for every migration on disk."""
    chain: dict[str, tuple[str | None, str]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name.startswith("__"):
            continue
        text = path.read_text(encoding="utf-8")
        revision = _REVISION.search(text)
        down = _DOWN.search(text)
        assert revision, f"{path.name} declares no revision id"
        assert down, f"{path.name} declares no down_revision"
        assert revision.group(1) not in chain, (
            f"Duplicate revision id {revision.group(1)!r}: "
            f"{path.name} and {chain[revision.group(1)][1]}. "
            "Two branches numbered a migration the same -- renumber the one that merges second."
        )
        chain[revision.group(1)] = (down.group(1), path.name)
    return chain


def test_revision_ids_are_unique():
    """`_chain` asserts it while building; this names the check."""
    assert len(_chain()) >= 30  # sanity: the files were actually found and parsed


def test_exactly_one_head():
    """Two heads make `alembic upgrade head` refuse to run at all.

    Not "run and do the wrong thing" -- refuse, with `Multiple head revisions
    are present`, which is what the production deploy runs on every release.
    """
    chain = _chain()
    referenced = {down for down, _ in chain.values() if down}
    heads = sorted(rev for rev in chain if rev not in referenced)

    assert len(heads) == 1, (
        f"Expected one head, found {heads}. "
        "Point the newer migration's `down_revision` at the older head."
    )


def test_every_down_revision_exists():
    """A gap is the other half of the same mistake.

    Renumbering a migration upward without a matching `down_revision` in the
    tree leaves alembic looking for a revision that was never merged: "Can't
    locate revision identified by ...", again at `upgrade head`, again on the
    deploy.
    """
    chain = _chain()
    missing = {f"{name} -> {down}" for down, name in chain.values() if down and down not in chain}

    assert not missing, f"down_revision points at a migration that is not here: {sorted(missing)}"


def test_the_chain_reaches_the_base_from_the_head():
    """Connected, not merely gap-free: a detached cycle satisfies the checks above."""
    chain = _chain()
    referenced = {down for down, _ in chain.values() if down}
    head = next(rev for rev in chain if rev not in referenced)

    seen: list[str] = []
    cursor: str | None = head
    while cursor is not None:
        assert cursor not in seen, f"cycle in the migration chain at {cursor}"
        seen.append(cursor)
        cursor = chain[cursor][0]

    assert len(seen) == len(chain), (
        f"{len(chain) - len(seen)} migration(s) are not reachable from the head: "
        f"{sorted(set(chain) - set(seen))}"
    )
