"""The contract that makes the two retrieval stores interchangeable.

`RAGMemoryService` (Q&A pairs in `chat_memory`) and `ChunkRetrievalService`
(conversation sessions in `chat_chunks`) share no implementation and should not
be made to. What they must share is a call signature, because the eval harness
swaps one for the other behind `--backend` and the pipeline is meant to do the
same behind `rag_backend` -- an A/B that is a flag rather than a fork only
works while both sides answer the same question the same way.

**This lives in `src/` rather than beside its first user in `scripts/` on
purpose.** CI type-checks `mypy src/` only, so a Protocol declared in a script
is never checked by anything: two services could drift out of conformance and
the first symptom would be a `TypeError` partway through a paid-for eval run.
Putting it here also pre-empts the wrong dependency direction -- the pipeline
wiring would otherwise be tempted to import it from `scripts/`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol


class SearchBackend(Protocol):
    """What a caller needs of a retrieval store, and nothing more.

    Structural, not a base class: conformance is checked against the two real
    services rather than inherited from a shared parent that would invite
    shared implementation.
    """

    @property
    def max_results(self) -> int: ...

    async def search(
        self,
        chat_id: int,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]: ...


if TYPE_CHECKING:  # pragma: no cover - a type-checking assertion, never executed
    from src.services.rag.chunk_retrieval import ChunkRetrievalService
    from src.services.rag.memory import RAGMemoryService

    def _both_services_conform(memory: RAGMemoryService, chunks: ChunkRetrievalService) -> None:
        """Make `mypy src/` actually check what the Protocol only declares.

        A Protocol on its own verifies nothing: structural conformance is
        checked at *assignment*, so a Protocol nobody assigns to is inert
        documentation, and moving it into the type-checked tree would still
        catch no drift. These two assignments are the check. They are never
        executed -- the function is unreachable at runtime -- and deleting
        either one silently removes the guarantee for that service.
        """
        _memory: SearchBackend = memory
        _chunks: SearchBackend = chunks
