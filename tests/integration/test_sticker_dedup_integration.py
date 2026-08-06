"""
Integration tests: sticker duplicate detection (ADR-0007, A-2) against real Postgres.

``test_sticker_learning.py::TestDuplicateDetection`` (backend-dev's own A-2 coverage)
exercises ``StickerLearningService.learn()``'s dedup branch entirely against a
*mocked* ``StickerRepository`` — ``get_dedup_candidates`` and ``save_sticker`` are
``AsyncMock``s that hand back exactly the rows the test author typed in. That proves
the branching logic in ``learn()``/``_save_duplicate()`` is correct, but it can't
catch a bug in the real SQL: a wrong ``WHERE`` clause on ``get_dedup_candidates``
(e.g. forgetting ``analysis_failed = false`` and matching against a failed row's
stale ``image_hash``), a column that doesn't round-trip through the real
``INSERT ... ON CONFLICT`` in ``save_sticker``, or the pgvector embedding column
silently dropping precision on copy.

This file drives ``StickerLearningService`` against a real ``StickerRepository``
over a real Postgres+pgvector testcontainer (mirrors the "real repo/DB + mocked
bot boundary" pattern in ``test_admin_defaults_toggle.py``). Only the AI router is
mocked — that's the one true external boundary (Vision/embedding API calls), and
asserting it was *not* awaited is the whole point of ADR-0007's "Vision не
вызывается на дубле" requirement (A-3's item title).

Per A-2's own routing note (envelope, A-2's last_update): "QA (A-3) should still
do: integration-level assert that analyze_image/generate_embedding are not called
on a real DB-backed duplicate path."
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio
from PIL import Image

from src.database.repositories.stickers import StickerRepository
from src.services.ai.base import EmbeddingResult, VisionResult
from src.services.modules.sticker.dedup import compute_image_hash
from src.services.modules.sticker.learning import StickerLearningService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def repo(db_conn: asyncpg.Connection) -> StickerRepository:
    return StickerRepository(db_conn)  # type: ignore[arg-type]


@pytest.fixture
def ai_router() -> MagicMock:
    """Mock AI router boundary. Vision/embedding must NOT be awaited on a
    detected duplicate (that's the whole point of ADR-0007's pre-Vision hash
    check) -- they're wired up here so the *fallback* (non-duplicate) tests
    can assert the opposite: that they ARE awaited when no candidate matches.
    """
    router = MagicMock()
    router.analyze_image = AsyncMock(
        return_value=VisionResult(
            text='{"visual": "A different picture entirely", "emotion": "surprise", '
            '"contexts": ["reaction"], "tags": ["meme"], "character": null}',
            model="gemini-3-flash",
            provider="gemini",
        )
    )
    router.generate_embedding = AsyncMock(
        return_value=EmbeddingResult(
            embedding=[0.4] * 768,
            model="gemini-embedding-001",
            provider="gemini",
            dimensions=768,
        )
    )
    router.generate_text = AsyncMock()
    router.log_usage = AsyncMock()
    return router


@pytest.fixture
def learning_service(ai_router: MagicMock, repo: StickerRepository) -> StickerLearningService:
    return StickerLearningService(ai_router, repo)


def _make_sticker(
    file_id: str,
    file_unique_id: str,
    *,
    # None (not a pack) by default: a set_name with no other pack members
    # triggers learn()'s web-search character-hint enrichment path, which is
    # unrelated to dedup and would need its own generate_text() stubbing.
    set_name: str | None = None,
    emoji: str = "\U0001f600",
) -> MagicMock:
    sticker = MagicMock()
    sticker.file_id = file_id
    sticker.file_unique_id = file_unique_id
    sticker.set_name = set_name
    sticker.emoji = emoji
    sticker.is_animated = False
    sticker.is_video = False
    return sticker


def _real_png_bytes(fill: tuple[int, int, int, int]) -> bytes:
    """A real, Pillow-parseable image -- compute_image_hash() must succeed on
    it (the fake byte strings used elsewhere in the unit suite fail open,
    which would silently skip the whole dedup path this file exists to
    prove)."""
    img = Image.new("RGBA", (64, 64), fill)
    for x in range(10, 30):
        for y in range(10, 30):
            img.putpixel((x, y), (255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _structurally_different_png_bytes() -> bytes:
    """A picture with a genuinely different SILHOUETTE from ``_real_png_bytes``
    (a checkerboard, not a corner-marked square).

    dHash (ADR-0007 Decision 1) is a pure luminance-*gradient* hash: it has no
    notion of color at all, only relative brightness between neighboring
    pixels. Two ``_real_png_bytes()`` calls with different ``fill`` colors but
    the *same* corner-mark shape hash IDENTICALLY (verified empirically while
    writing this file -- distance 0, not merely "close"), because grayscale
    conversion collapses both fills to a similar relative-brightness pattern
    against the white corner. A real "different sticker" control must differ
    in STRUCTURE, not just fill color, or the test is a false negative
    dressed up as a positive control."""
    img = Image.new("RGBA", (64, 64), (20, 20, 20, 255))
    for x in range(0, 64, 8):
        for y in range(0, 64, 8):
            if (x // 8 + y // 8) % 2 == 0:
                for dx in range(8):
                    for dy in range(8):
                        img.putpixel((x + dx, y + dy), (235, 235, 235, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _recompress(image_bytes: bytes) -> bytes:
    """Stand-in for Telegram's WEBP re-encode: re-save through Pillow so the
    bytes differ but the picture doesn't -- the real-world case the dHash
    threshold exists to tolerate (ADR-0007 Decision 3)."""
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def _seed_canonical(
    repo: StickerRepository, *, file_unique_id: str, image_bytes: bytes
) -> str:
    """Insert a fully-analyzed canonical sticker row directly via the real
    repository (as if Vision had already run on it), including its own
    image_hash and embedding -- exactly what get_dedup_candidates() expects
    a legitimate match target to look like."""
    image_hash = compute_image_hash(image_bytes)
    await repo.save_sticker(
        file_unique_id=file_unique_id,
        file_id=f"file-{file_unique_id}",
        set_name="canonical_pack",
        emoji="\U0001f600",
        visual_description="A happy cat waving",
        original_vision_description="A happy cat waving",
        emotion="joy",
        suggested_contexts=["greeting"],
        style_tags=["cute"],
        character_or_meme="Pepe",
        image_hash=image_hash,
    )
    await repo.update_embedding(file_unique_id, [0.2] * 768)
    return image_hash


# ---------------------------------------------------------------------------
# Duplicate path: Vision + embedding must NOT be called, real DB round-trip
# ---------------------------------------------------------------------------


class TestDuplicatePathAgainstRealDb:
    @pytest.mark.asyncio
    async def test_duplicate_skips_vision_and_embedding_calls(
        self,
        learning_service: StickerLearningService,
        repo: StickerRepository,
        ai_router: MagicMock,
    ) -> None:
        canonical_bytes = _real_png_bytes((200, 30, 30, 255))
        await _seed_canonical(repo, file_unique_id="canon-int-001", image_bytes=canonical_bytes)

        duplicate_bytes = _recompress(canonical_bytes)
        sticker = _make_sticker("file-dup-int-001", "dup-int-001")

        result = await learning_service.learn(sticker=sticker, image_data=duplicate_bytes)

        assert result.is_new is True
        assert result.analysis_failed is False
        assert result.duplicate_of == "canon-int-001"
        assert result.visual_description == "A happy cat waving"

        ai_router.analyze_image.assert_not_awaited()
        ai_router.generate_embedding.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_row_persists_copied_fields_in_real_db(
        self,
        learning_service: StickerLearningService,
        repo: StickerRepository,
    ) -> None:
        canonical_bytes = _real_png_bytes((30, 200, 30, 255))
        await _seed_canonical(repo, file_unique_id="canon-int-002", image_bytes=canonical_bytes)

        duplicate_bytes = _recompress(canonical_bytes)
        sticker = _make_sticker("file-dup-int-002", "dup-int-002")
        await learning_service.learn(sticker=sticker, image_data=duplicate_bytes)

        row = await repo.get_by_file_unique_id("dup-int-002")
        assert row is not None
        assert row["duplicate_of_file_unique_id"] == "canon-int-002"
        assert row["visual_description"] == "A happy cat waving"
        assert row["emotion"] == "joy"
        assert row["character_or_meme"] == "Pepe"
        # Embedding was copied (update_embedding), not regenerated -- real
        # pgvector round-trip, not a mocked call.assert_awaited_with.
        embedding = row["description_embedding"]
        assert embedding is not None
        embedding_list = embedding.to_list()
        assert len(embedding_list) == 768
        assert embedding_list[0] == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_duplicate_chain_flattens_to_root_in_real_db(
        self,
        learning_service: StickerLearningService,
        repo: StickerRepository,
    ) -> None:
        """A third copy that hash-matches an already-detected duplicate row
        resolves to that duplicate's own root (ADR-0007 Decision 6), proven
        end-to-end through the real get_dedup_candidates() query -- not the
        hand-typed tuples the unit-level find_duplicate() tests use."""
        root_bytes = _real_png_bytes((30, 30, 200, 255))
        await _seed_canonical(repo, file_unique_id="root-int-003", image_bytes=root_bytes)

        # First copy: becomes a duplicate-of-root row in the real table.
        mid_sticker = _make_sticker("file-mid-int-003", "mid-int-003")
        await learning_service.learn(sticker=mid_sticker, image_data=_recompress(root_bytes))
        mid_row = await repo.get_by_file_unique_id("mid-int-003")
        assert mid_row is not None
        assert mid_row["duplicate_of_file_unique_id"] == "root-int-003"

        # Second copy: hash-matches the mid row (itself already a duplicate).
        # get_dedup_candidates() must surface mid's own duplicate_of_file_unique_id
        # so find_duplicate() can flatten to the root instead of pointing at mid.
        third_sticker = _make_sticker("file-third-int-003", "third-int-003")
        result = await learning_service.learn(
            sticker=third_sticker, image_data=_recompress(root_bytes)
        )

        assert result.duplicate_of == "root-int-003"
        third_row = await repo.get_by_file_unique_id("third-int-003")
        assert third_row is not None
        assert third_row["duplicate_of_file_unique_id"] == "root-int-003"


# ---------------------------------------------------------------------------
# Non-duplicate path: Vision IS called against a real (empty-ish) candidate set
# ---------------------------------------------------------------------------


class TestNonDuplicatePathAgainstRealDb:
    @pytest.mark.asyncio
    async def test_genuinely_different_picture_falls_through_to_vision(
        self,
        learning_service: StickerLearningService,
        repo: StickerRepository,
        ai_router: MagicMock,
    ) -> None:
        canonical_bytes = _real_png_bytes((200, 30, 30, 255))
        await _seed_canonical(repo, file_unique_id="canon-int-004", image_bytes=canonical_bytes)

        # A structurally different picture (not a recompression) -- must NOT match.
        different_bytes = _structurally_different_png_bytes()
        sticker = _make_sticker("file-new-int-004", "new-int-004")

        result = await learning_service.learn(sticker=sticker, image_data=different_bytes)

        assert result.duplicate_of is None
        ai_router.analyze_image.assert_awaited_once()

        row = await repo.get_by_file_unique_id("new-int-004")
        assert row is not None
        assert row["duplicate_of_file_unique_id"] is None
        assert row["image_hash"] is not None


# ---------------------------------------------------------------------------
# get_dedup_candidates() SQL: real WHERE-clause filtering
# ---------------------------------------------------------------------------


class TestGetDedupCandidatesFiltering:
    """Exercises the real SQL in StickerRepository.get_dedup_candidates()
    (ADR-0007 Decision 5's WHERE clause) -- unit tests only ever stub this
    method's *return value*, never its filtering logic."""

    @pytest.mark.asyncio
    async def test_excludes_failed_and_unanalyzed_rows(self, repo: StickerRepository) -> None:
        good_hash = compute_image_hash(_real_png_bytes((100, 100, 100, 255)))
        failed_hash = compute_image_hash(_real_png_bytes((10, 10, 10, 255)))
        unanalyzed_hash = compute_image_hash(_real_png_bytes((250, 250, 250, 255)))

        # Eligible candidate: has both a hash and a description, not failed.
        await repo.save_sticker(
            file_unique_id="cand-eligible",
            file_id="f-cand-eligible",
            visual_description="Eligible sticker",
            image_hash=good_hash,
        )
        # Ineligible: analysis_failed=True (stale hash from a failed render).
        await repo.save_sticker(
            file_unique_id="cand-failed",
            file_id="f-cand-failed",
            visual_description=None,
            analysis_failed=True,
            image_hash=failed_hash,
        )
        # Ineligible: has a hash but was never actually analyzed (no description).
        await repo.save_sticker(
            file_unique_id="cand-unanalyzed",
            file_id="f-cand-unanalyzed",
            visual_description=None,
            image_hash=unanalyzed_hash,
        )

        candidates = await repo.get_dedup_candidates()
        candidate_ids = {c["file_unique_id"] for c in candidates}

        assert "cand-eligible" in candidate_ids
        assert "cand-failed" not in candidate_ids
        assert "cand-unanalyzed" not in candidate_ids

    @pytest.mark.asyncio
    async def test_excludes_rows_with_null_hash(self, repo: StickerRepository) -> None:
        """Rows predating migration 023 (or any hash-computation failure,
        ADR-0007 Decision 4) have image_hash=NULL and must never surface as
        a match target -- isolated from the description/failed filters above
        by giving this row a real description (only the hash is missing)."""
        await repo.save_sticker(
            file_unique_id="cand-null-hash",
            file_id="f-cand-null-hash",
            visual_description="Analyzed, but predates the hash migration",
        )
        candidates = await repo.get_dedup_candidates()
        assert "cand-null-hash" not in {c["file_unique_id"] for c in candidates}
