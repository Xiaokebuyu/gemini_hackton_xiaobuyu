"""Tests for WorldKnowledgeGraph Phase 3b:
write_episode (LLM triple extraction from dialogue) and ensure_lore_enriched.

All async calls wrapped with asyncio.run() — no pytest-asyncio installed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.adapters.llm import LlmResponse
from app.game_core.narrative.context_window import WindowMessage
from app.world_knowledge_graph import EdgeType, WorldKnowledgeGraph


# ------------------------------------------------------------------
# Stubs
# ------------------------------------------------------------------


class _StubLlm:
    """Controllable LLM stub that returns preset tool_calls."""

    def __init__(
        self,
        tool_calls: list[dict[str, Any]] | None = None,
        raise_on_call: bool = False,
    ) -> None:
        self.preset_tool_calls = tool_calls or []
        self.raise_on_call = raise_on_call
        self.call_count = 0

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        self.call_count += 1
        if self.raise_on_call:
            raise RuntimeError("LLM unavailable")
        return LlmResponse(tool_calls=list(self.preset_tool_calls))


class _StubLoreEntry:
    def __init__(self, id: str, name: str, description: str = "") -> None:
        self.id = id
        self.name = name
        self.description = description


class _CaptureStubLlm(_StubLlm):
    def __init__(self) -> None:
        super().__init__(tool_calls=[{
            "name": "record_triple",
            "args": {
                "subject": "Merchant Tom",
                "relation": "interacted_with",
                "object": "Iron Sword",
            },
        }])
        self.last_dialogue: str = ""

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        if history:
            parts = history[0].get("parts", [])
            if parts and isinstance(parts[0], dict):
                self.last_dialogue = str(parts[0].get("text", ""))
        return await super().generate(system_prompt, history, declarations)


class _StubRegistry:
    def __init__(self, entries: list[Any]) -> None:
        self._entries = entries

    def list_all(self) -> list[Any]:
        return list(self._entries)


class _StubWorld:
    """Minimal WorldInstance stub for testing ensure_lore_enriched."""

    def __init__(
        self,
        world_id: str = "test_world",
        lore_entries: list[Any] | None = None,
        char_entries: list[Any] | None = None,
    ) -> None:
        self.world_id = world_id
        self._lore = _StubRegistry(lore_entries or [])
        self._chars = _StubRegistry(char_entries or [])
        self._registered: dict[str, bool] = {
            "lore": lore_entries is not None,
            "characters": char_entries is not None,
        }

    def has_registry(self, name: str) -> bool:
        return self._registered.get(name, False)

    @property
    def lore(self) -> _StubRegistry:
        return self._lore

    @property
    def characters(self) -> _StubRegistry:
        return self._chars


def _make_msg(role: str, content: str) -> WindowMessage:
    return WindowMessage(role=role, content=content, token_count=1, metadata={})


def _make_graph_with_nodes(llm: _StubLlm | None = None) -> WorldKnowledgeGraph:
    """Small fixture graph with two nodes: merchant_tom (character) + iron_sword (item)."""
    g = WorldKnowledgeGraph(llm=llm)
    g._graph.add_node(
        "merchant_tom",
        node_type="character",
        label="Merchant Tom",
        tags=["merchant"],
        description="",
        metadata={},
    )
    g._graph.add_node(
        "iron_sword",
        node_type="item",
        label="Iron Sword",
        tags=["weapon"],
        description="",
        metadata={},
    )
    return g


# ------------------------------------------------------------------
# TestWriteEpisode
# ------------------------------------------------------------------


class TestWriteEpisode:
    def test_no_llm_returns_immediately(self) -> None:
        """No LLM → write_episode is a no-op without raising."""
        g = WorldKnowledgeGraph()  # llm=None
        msgs = [_make_msg("user", "Hello")]
        asyncio.run(g.write_episode("npc_01", msgs, {}))
        assert g.edge_count() == 0

    def test_empty_messages_returns_immediately(self) -> None:
        """Empty messages → LLM should not be called."""
        llm = _StubLlm()
        g = WorldKnowledgeGraph(llm=llm)
        asyncio.run(g.write_episode("npc_01", [], {}))
        assert llm.call_count == 0

    def test_inserts_edge_from_triple(self) -> None:
        """LLM returns a valid triple → actor-private edge inserted."""
        llm = _StubLlm(tool_calls=[{
            "name": "record_triple",
            "args": {
                "subject": "Merchant Tom",
                "relation": "interacted_with",
                "object": "Iron Sword",
            },
        }])
        g = _make_graph_with_nodes(llm=llm)
        msgs = [_make_msg("user", "Tom sold the sword.")]
        asyncio.run(g.write_episode("npc_01", msgs, {}, session_id="test"))
        assert g.has_actor_edge("npc_01", "merchant_tom", "iron_sword", session_id="test")

    def test_skips_unknown_subject(self) -> None:
        """Triple with unresolvable subject → silently skipped, no crash."""
        llm = _StubLlm(tool_calls=[{
            "name": "record_triple",
            "args": {
                "subject": "UnknownNPC",
                "relation": "knows_about",
                "object": "Iron Sword",
            },
        }])
        g = _make_graph_with_nodes(llm=llm)
        msgs = [_make_msg("user", "some dialogue")]
        asyncio.run(g.write_episode("npc_01", msgs, {}, session_id="test"))
        assert g.actor_edge_count("npc_01", session_id="test") == 0

    def test_skips_unknown_object(self) -> None:
        """Triple with unresolvable object → silently skipped."""
        llm = _StubLlm(tool_calls=[{
            "name": "record_triple",
            "args": {
                "subject": "Merchant Tom",
                "relation": "knows_about",
                "object": "UnknownItem",
            },
        }])
        g = _make_graph_with_nodes(llm=llm)
        msgs = [_make_msg("user", "dialogue")]
        asyncio.run(g.write_episode("npc_01", msgs, {}, session_id="test"))
        assert g.actor_edge_count("npc_01", session_id="test") == 0

    def test_llm_exception_returns_gracefully(self) -> None:
        """LLM raises exception → error is swallowed, no propagation."""
        llm = _StubLlm(raise_on_call=True)
        g = _make_graph_with_nodes(llm=llm)
        msgs = [_make_msg("user", "dialogue")]
        asyncio.run(g.write_episode("npc_01", msgs, {}, session_id="test"))  # must not raise
        assert g.actor_edge_count("npc_01", session_id="test") == 0

    def test_multiple_triples_inserted(self) -> None:
        """Multiple valid tool_calls → multiple actor-private edges inserted."""
        llm = _StubLlm(tool_calls=[
            {
                "name": "record_triple",
                "args": {
                    "subject": "Merchant Tom",
                    "relation": "interacted_with",
                    "object": "Iron Sword",
                },
            },
            {
                "name": "record_triple",
                "args": {
                    "subject": "Iron Sword",
                    "relation": "related_to",
                    "object": "Merchant Tom",
                },
            },
        ])
        g = _make_graph_with_nodes(llm=llm)
        msgs = [_make_msg("user", "two facts")]
        asyncio.run(g.write_episode("npc_01", msgs, {}, session_id="test"))
        assert g.has_actor_edge("npc_01", "merchant_tom", "iron_sword", session_id="test")
        assert g.has_actor_edge("npc_01", "iron_sword", "merchant_tom", session_id="test")

    def test_weight_propagated_to_edge(self) -> None:
        """weight field in triple is correctly written to the graph edge."""
        llm = _StubLlm(tool_calls=[{
            "name": "record_triple",
            "args": {
                "subject": "Merchant Tom",
                "relation": "has_opinion_of",
                "object": "Iron Sword",
                "weight": 0.42,
            },
        }])
        g = _make_graph_with_nodes(llm=llm)
        msgs = [_make_msg("user", "opinion")]
        asyncio.run(g.write_episode("npc_01", msgs, {}, session_id="test"))
        assert g.has_actor_edge("npc_01", "merchant_tom", "iron_sword", session_id="test")
        actor_graph = g._sessions["test"].actor_graphs["npc_01"]
        edge_data = actor_graph["merchant_tom"]["iron_sword"]
        assert abs(edge_data["weight"] - 0.42) < 1e-9

    def test_recent_events_context_is_included(self) -> None:
        """recent_events context should be embedded into LLM input."""
        llm = _CaptureStubLlm()
        g = _make_graph_with_nodes(llm=llm)
        msgs = [_make_msg("user", "greet")]
        asyncio.run(g.write_episode(
            "npc_01",
            msgs,
            {
                "world": _StubWorld("test_world"),
                "recent_events": [
                    {
                        "action": "navigate",
                        "summary": "scouted the old bridge",
                        "tags": ["NAVIGATION"],
                    },
                    {
                        "action": "skill_check",
                        "summary": "locked pick test passed",
                        "tags": ["SKILL_CHECK"],
                    },
                ],
            },
        ))
        assert "scouted the old bridge" in llm.last_dialogue
        assert "Recent player companion observations" in llm.last_dialogue

    def test_remember_creates_actor_private_memory_node(self) -> None:
        g = _make_graph_with_nodes()

        result = asyncio.run(
            g.remember("npc_01", "Player fears the hidden cellar.", {}, session_id="test")
        )

        assert result["status"] == "ok"
        memory_id = result["memory_id"]
        actor_graph = g._sessions["test"].actor_graphs["npc_01"]
        assert actor_graph.nodes[memory_id]["node_type"] == "memory_note"
        assert "hidden cellar" in actor_graph.nodes[memory_id]["description"].lower()


# ------------------------------------------------------------------
# TestEnsureLoreEnriched
# ------------------------------------------------------------------


class TestEnsureLoreEnriched:
    def test_no_llm_no_op(self) -> None:
        """No LLM → ensure_lore_enriched completes without error."""
        g = WorldKnowledgeGraph()  # llm=None
        world = _StubWorld(lore_entries=[
            _StubLoreEntry("lore_1", "The Prophecy", "A great evil stirs.")
        ])
        asyncio.run(g.ensure_lore_enriched(world))
        assert g.edge_count() == 0

    def test_idempotent(self) -> None:
        """Two calls for the same world → LLM invoked only once."""
        llm = _StubLlm()  # returns no triples
        g = WorldKnowledgeGraph(llm=llm)
        world = _StubWorld(lore_entries=[
            _StubLoreEntry("lore_1", "Lore", "Some lore text.")
        ])
        asyncio.run(g.ensure_lore_enriched(world))
        asyncio.run(g.ensure_lore_enriched(world))
        assert llm.call_count == 1

    def test_inserts_lore_edges(self) -> None:
        """LLM returns a lore triple → graph edge inserted."""
        llm = _StubLlm(tool_calls=[{
            "name": "record_triple",
            "args": {
                "subject": "Merchant Tom",
                "relation": "related_to",
                "object": "Iron Sword",
            },
        }])
        g = _make_graph_with_nodes(llm=llm)
        world = _StubWorld(lore_entries=[
            _StubLoreEntry("lore_1", "Sword History", "Tom forged the Iron Sword.")
        ])
        asyncio.run(g.ensure_lore_enriched(world))
        assert g.has_edge("merchant_tom", "iron_sword")

    def test_no_lore_registry_no_llm_call(self) -> None:
        """World has no lore or character registry → LLM not called."""
        llm = _StubLlm()
        g = WorldKnowledgeGraph(llm=llm)
        # No lore_entries or char_entries → has_registry returns False for both
        world = _StubWorld()
        asyncio.run(g.ensure_lore_enriched(world))
        assert llm.call_count == 0


# ------------------------------------------------------------------
# TestApplyTriple
# ------------------------------------------------------------------


class TestApplyTriple:
    def test_apply_creates_edge_when_both_nodes_exist(self) -> None:
        """_apply_triple inserts an edge into the session overlay when both endpoints exist."""
        g = _make_graph_with_nodes()
        g._apply_triple({
            "subject": "Merchant Tom",
            "relation": "knows_about",
            "object": "Iron Sword",
        }, session_id="test")
        # Edge goes into the session overlay, not the static base graph
        assert g._sessions["test"].overlay.has_edge("merchant_tom", "iron_sword")

    def test_apply_skips_when_subject_not_in_graph(self) -> None:
        """_apply_triple silently skips if subject is unresolvable."""
        g = _make_graph_with_nodes()
        g._apply_triple({
            "subject": "Ghost NPC",
            "relation": "knows_about",
            "object": "Iron Sword",
        }, session_id="test")
        # Base graph unmodified; session overlay has no edges
        assert g.edge_count() == 0
        session = g._sessions.get("test")
        assert session is None or session.overlay.number_of_edges() == 0

    def test_apply_skips_self_loop(self) -> None:
        """_apply_triple does not create a self-loop when subject == object."""
        g = _make_graph_with_nodes()
        g._apply_triple({
            "subject": "Merchant Tom",
            "relation": "related_to",
            "object": "Merchant Tom",
        }, session_id="test")
        assert not g.has_edge("merchant_tom", "merchant_tom")
