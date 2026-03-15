"""Tests for KnowledgeGraphMemoryRetriever and NullMemoryGraphPort.

All async calls wrapped with asyncio.run() (no pytest-asyncio installed).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.adapters.memory_graph_port import MemoryGraphPort, NullMemoryGraphPort
from app.memory_retriever_impl import KnowledgeGraphMemoryRetriever


# ------------------------------------------------------------------
# Stub MemoryGraphPort for unit testing the retriever
# ------------------------------------------------------------------


class _StubGraphPort:
    """Controllable stub: returns preset hits or nothing."""

    def __init__(self, preset_hits: list[dict[str, Any]] | None = None) -> None:
        self.preset_hits = preset_hits or []
        self.last_call: dict[str, Any] | None = None

    async def query_spread(
        self,
        actor_id: str,
        keywords: list[str],
        context: dict[str, Any],
        *,
        max_depth: int = 2,
        decay: float = 0.8,
        top_k: int = 10,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        self.last_call = {
            "actor_id": actor_id,
            "keywords": keywords,
            "context": context,
            "max_depth": max_depth,
            "decay": decay,
            "top_k": top_k,
            "session_id": session_id,
        }
        return list(self.preset_hits)


# ------------------------------------------------------------------
# TestKnowledgeGraphMemoryRetriever
# ------------------------------------------------------------------


class TestKnowledgeGraphMemoryRetriever:
    def test_retrieve_empty_keywords_returns_empty_hits(self) -> None:
        stub = _StubGraphPort(preset_hits=[{"node_id": "some_node"}])
        retriever = KnowledgeGraphMemoryRetriever(stub)
        result = asyncio.run(retriever.retrieve("npc_01", [], {}))
        assert result["hits"] == []

    def test_retrieve_empty_keywords_does_not_call_graph(self) -> None:
        stub = _StubGraphPort()
        retriever = KnowledgeGraphMemoryRetriever(stub)
        asyncio.run(retriever.retrieve("npc_01", [], {}))
        assert stub.last_call is None

    def test_retrieve_source_is_knowledge_graph_when_empty(self) -> None:
        stub = _StubGraphPort()
        retriever = KnowledgeGraphMemoryRetriever(stub)
        result = asyncio.run(retriever.retrieve("npc_01", [], {}))
        assert result["source"] == "knowledge_graph"

    def test_retrieve_source_is_knowledge_graph_when_hits(self) -> None:
        hit = {"node_id": "a", "node_type": "item", "label": "Sword",
               "tags": [], "activation": 0.9, "description": "", "metadata": {}}
        stub = _StubGraphPort(preset_hits=[hit])
        retriever = KnowledgeGraphMemoryRetriever(stub)
        result = asyncio.run(retriever.retrieve("npc_01", ["sword"], {}))
        assert result["source"] == "knowledge_graph"

    def test_retrieve_with_keywords_calls_graph(self) -> None:
        stub = _StubGraphPort()
        retriever = KnowledgeGraphMemoryRetriever(stub)
        asyncio.run(retriever.retrieve("npc_42", ["sword", "market"], {"world": None}))
        assert stub.last_call is not None
        assert stub.last_call["actor_id"] == "npc_42"
        assert stub.last_call["keywords"] == ["sword", "market"]

    def test_retrieve_hit_format_preserved(self) -> None:
        hit = {
            "node_id": "merchant_tom",
            "node_type": "character",
            "label": "Merchant Tom",
            "tags": ["merchant"],
            "activation": 0.75,
            "description": "Shrewd",
            "metadata": {"alignment": "neutral"},
        }
        stub = _StubGraphPort(preset_hits=[hit])
        retriever = KnowledgeGraphMemoryRetriever(stub)
        result = asyncio.run(retriever.retrieve("npc_01", ["merchant"], {}))
        assert result["hits"] == [hit]

    def test_retrieve_multiple_hits_preserved_order(self) -> None:
        hits = [
            {"node_id": f"node_{i}", "node_type": "item", "label": f"Node {i}",
             "tags": [], "activation": 1.0 - i * 0.1, "description": "", "metadata": {}}
            for i in range(5)
        ]
        stub = _StubGraphPort(preset_hits=hits)
        retriever = KnowledgeGraphMemoryRetriever(stub)
        result = asyncio.run(retriever.retrieve("npc_01", ["node"], {}))
        assert result["hits"] == hits


# ------------------------------------------------------------------
# TestNullMemoryGraphPort
# ------------------------------------------------------------------


class TestNullMemoryGraphPort:
    def test_query_spread_returns_empty_list(self) -> None:
        port = NullMemoryGraphPort()
        result = asyncio.run(port.query_spread("npc_01", [], {}))
        assert result == []

    def test_query_spread_always_empty_regardless_keywords(self) -> None:
        port = NullMemoryGraphPort()
        result = asyncio.run(port.query_spread("npc_01", ["sword", "merchant"], {}))
        assert result == []

    def test_result_is_list_not_none(self) -> None:
        port = NullMemoryGraphPort()
        result = asyncio.run(port.query_spread("npc_01", ["anything"], {}))
        assert isinstance(result, list)

    def test_satisfies_protocol(self) -> None:
        port = NullMemoryGraphPort()
        assert isinstance(port, MemoryGraphPort)
