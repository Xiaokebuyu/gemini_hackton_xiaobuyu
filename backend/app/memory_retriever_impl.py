"""KnowledgeGraphMemoryRetriever — wraps MemoryGraphPort as MemoryRetriever.

Bridges the game_core MemoryRetriever Protocol with the app-layer
WorldKnowledgeGraph (or any MemoryGraphPort implementation).

Decision record: D-N15 (narrative.md)
"""

from __future__ import annotations

from typing import Any

from app.game_core.adapters.memory_graph_port import MemoryGraphPort


class KnowledgeGraphMemoryRetriever:
    """Adapter: MemoryGraphPort → MemoryRetriever Protocol.

    Passes keywords and context through to the graph's query_spread,
    then wraps the result in the standard {"hits": …, "source": …} envelope.
    """

    def __init__(self, graph: MemoryGraphPort) -> None:
        self._graph = graph

    async def retrieve(
        self,
        actor_id: str,
        keywords: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Retrieve relevant world knowledge via spreading activation.

        Returns {"hits": list[dict], "source": "knowledge_graph"}.
        When keywords is empty, returns empty hits immediately without
        touching the graph.
        """
        if not keywords:
            return {"hits": [], "source": "knowledge_graph"}
        hits = await self._graph.query_spread(
            actor_id=actor_id,
            keywords=keywords,
            context=context,
        )
        return {"hits": hits, "source": "knowledge_graph"}
