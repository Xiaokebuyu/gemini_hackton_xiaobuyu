"""Tests for MemoryRetriever Protocol and NullMemoryRetriever (N-1 Phase 1)."""

from __future__ import annotations

import asyncio

from app.game_core.narrative.memory_retriever import MemoryRetriever, NullMemoryRetriever


class TestNullMemoryRetriever:
    def test_retrieve_returns_empty_hits(self) -> None:
        retriever = NullMemoryRetriever()
        result = asyncio.run(retriever.retrieve("npc_01", [], {}))
        assert result["hits"] == []

    def test_retrieve_source_is_null(self) -> None:
        retriever = NullMemoryRetriever()
        result = asyncio.run(
            retriever.retrieve("npc_01", ["sword", "quest"], {"scene": "tavern"})
        )
        assert result["source"] == "null"

    def test_retrieve_ignores_keywords(self) -> None:
        retriever = NullMemoryRetriever()
        result = asyncio.run(retriever.retrieve("npc_01", ["anything", "at", "all"], {}))
        assert result["hits"] == []

    def test_satisfies_protocol(self) -> None:
        """NullMemoryRetriever should satisfy the MemoryRetriever protocol check."""
        retriever = NullMemoryRetriever()
        assert isinstance(retriever, MemoryRetriever)

    def test_result_keys_present(self) -> None:
        retriever = NullMemoryRetriever()
        result = asyncio.run(retriever.retrieve("any_id", [], {}))
        assert "hits" in result
        assert "source" in result
