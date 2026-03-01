"""MemoryGraphPort — knowledge graph persistence and retrieval boundary.

game_core only sees this Protocol; the real NetworkX / Firestore
implementations live in app/.

Phase 2: static world knowledge graph + spreading activation query.
Phase 3a: write_episode stub (interface only; implementation in Phase 3b).
Phase 3b: write_episode fills LLM triple extraction from dialogue overflow.

Decision record: D-N15, D-N16 (narrative.md)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryGraphPort(Protocol):
    """Knowledge graph persistence and retrieval boundary.

    Implementors live in app/ (e.g. WorldKnowledgeGraph).
    game_core only depends on this Protocol.
    """

    async def query_spread(
        self,
        actor_id: str,
        keywords: list[str],
        context: dict[str, Any],
        *,
        max_depth: int = 2,
        decay: float = 0.8,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Spreading activation retrieval.

        Finds seed nodes matching *keywords*, then propagates activation
        along graph edges with exponential decay.

        Args:
            actor_id: NPC or teammate character ID (reserved for Phase 3
                      per-actor memory partitioning).
            keywords: Topic keywords extracted from the current scene.
            context: Scene context dict.  Implementors may read
                     ``context["world"]`` (WorldInstance) to perform
                     lazy graph seeding.
            max_depth: Maximum BFS traversal depth (default 2).
            decay: Per-hop activation multiplier (default 0.8).
            top_k: Maximum number of hits to return (default 10).

        Returns:
            list of hit dicts, each containing:
                node_id, node_type, label, tags, activation,
                description, metadata
        """
        ...

    async def write_episode(
        self,
        actor_id: str,
        messages: list[Any],
        context: dict[str, Any],
    ) -> None:
        """Write a compressed dialogue episode to the knowledge graph.

        Called when a ContextWindow overflows (Phase 3a: overflow detected,
        messages popped).  Implementors extract semantic triples from the
        dialogue and insert them as dynamic edges in the graph.

        Phase 3a: stub — implementations return immediately (no-op).
        Phase 3b: LLM triple extraction fills the implementation.

        Args:
            actor_id: The NPC whose conversation window overflowed.
            messages: WindowMessage objects popped by pop_oldest_for_graphize.
            context: Runtime context dict (may contain ``"world"``,
                     ``"npc_id"``, ``"player_message"``).
        """
        ...


class NullMemoryGraphPort:
    """Safe default: no IO, always returns an empty list / does nothing.

    Used in tests and when the knowledge graph has not been configured.
    """

    async def query_spread(
        self,
        actor_id: str,
        keywords: list[str],
        context: dict[str, Any],
        *,
        max_depth: int = 2,
        decay: float = 0.8,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        return []

    async def write_episode(
        self,
        actor_id: str,
        messages: list[Any],
        context: dict[str, Any],
    ) -> None:
        return
