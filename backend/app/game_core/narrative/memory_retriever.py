"""MemoryRetriever — L6 memory recall injection boundary.

Defines the Protocol that separates game_core (pure Python, zero external deps)
from app-layer memory implementations (NetworkX + Firestore, Phase 2-3).

Decision record: D-N14-Phase1 (narrative.md)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryRetriever(Protocol):
    """L6 memory recall injection point.

    game_core only sees this Protocol.  Real implementations
    (e.g. NetworkXMemoryRetriever with spreading activation) live in app/.

    Return format:
        {"hits": list[dict], "source": str}

    Each hit dict is untyped in Phase 1; Phase 2-3 will define a MemoryNode
    schema with keys: id, type, content, relevance, tags.
    """

    async def retrieve(
        self,
        actor_id: str,
        keywords: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Retrieve relevant memories via spreading activation.

        Args:
            actor_id: NPC or teammate character ID.
            keywords: Topic keywords extracted from current scene (Phase 2-3).
                      Phase 1 callers pass an empty list.
            context: Optional scene context dict (Phase 2-3).
                     Phase 1 callers pass an empty dict.

        Returns:
            {"hits": list[dict], "source": str}
        """
        ...


class NullMemoryRetriever:
    """Safe default: no IO, always returns empty hit list.

    Used when no memory system is configured — tests, development, Phase 1
    rollout.  Satisfies MemoryRetriever Protocol.
    """

    async def retrieve(
        self,
        actor_id: str,
        keywords: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {"hits": [], "source": "null"}
