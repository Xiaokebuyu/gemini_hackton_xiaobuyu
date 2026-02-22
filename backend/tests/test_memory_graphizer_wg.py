"""
Tests for MemoryGraphizer WorldGraph write path (L3 M2 Phase 3).

Tests cover:
- _merge_to_world_graph: synchronous merge of extracted elements into WorldGraph
- _get_important_nodes_from_wg: synchronous retrieval of important nodes from WorldGraph
- _extract_metadata: extraction of story_event_ids, transition_target, chapter_id
- graphize(..., world_graph=wg): async path routing to WorldGraph
"""
import sys
import types

def _install_mcp_stubs():
    if "mcp.client.session" in sys.modules:
        return
    mcp_mod = types.ModuleType("mcp")
    client_mod = types.ModuleType("mcp.client")
    session_mod = types.ModuleType("mcp.client.session")
    sse_mod = types.ModuleType("mcp.client.sse")
    stdio_mod = types.ModuleType("mcp.client.stdio")
    streamable_http_mod = types.ModuleType("mcp.client.streamable_http")
    session_mod.ClientSession = object
    sse_mod.sse_client = object
    stdio_mod.StdioServerParameters = object
    stdio_mod.stdio_client = object
    streamable_http_mod.streamable_http_client = object
    mcp_mod.client = client_mod
    client_mod.session = session_mod
    client_mod.sse = sse_mod
    client_mod.stdio = stdio_mod
    client_mod.streamable_http = streamable_http_mod
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.client"] = client_mod
    sys.modules["mcp.client.session"] = session_mod
    sys.modules["mcp.client.sse"] = sse_mod
    sys.modules["mcp.client.stdio"] = stdio_mod
    sys.modules["mcp.client.streamable_http"] = streamable_http_mod

_install_mcp_stubs()

import asyncio

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.world.world_graph import WorldGraph
from app.world.models import WorldNode
from app.models.graph_elements import (
    ExtractedElements,
    EventGroupNode,
    EventNode,
    GraphEdgeSpec,
    MergeResult,
    TranscriptMessage,
)
from app.models.context_window import GraphizeRequest, WindowMessage
from app.services.memory_graphizer import MemoryGraphizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_extraction(
    eg_id="eg_1",
    eg_location="tavern",
    sub_events=None,
    new_nodes=None,
    edges=None,
    state_updates=None,
    transcript=None,
    participants=None,
):
    """Build an ExtractedElements fixture with sensible defaults."""
    if transcript is None:
        transcript = [TranscriptMessage(role="player", content="Hello")]
    if participants is None:
        participants = ["player", "npc_1"]

    extraction = ExtractedElements(
        event_group=EventGroupNode(
            id=eg_id,
            name="Test Event Group",
            day=1,
            location=eg_location,
            summary="A meeting",
            emotion="neutral",
            importance=0.7,
            participants=participants,
            transcript=transcript,
            message_count=len(transcript),
            token_count=10,
        ),
        sub_events=sub_events or [],
        new_nodes=new_nodes or [],
        edges=edges or [],
        state_updates=state_updates or {},
    )
    return extraction


def _make_wg_with_npc(npc_id="npc_1", add_player=True, add_location=None):
    """Build a WorldGraph with an NPC node (and optionally player / location)."""
    wg = WorldGraph()
    wg.add_node(WorldNode(
        id=npc_id, type="npc", name="Test NPC",
        properties={"owner": npc_id},
    ))
    if add_player:
        wg.add_node(WorldNode(
            id="player", type="npc", name="Player",
        ))
    if add_location:
        wg.add_node(WorldNode(
            id=add_location, type="location", name=add_location.title(),
        ))
    return wg


# ===========================================================================
# 1. test_merge_to_world_graph_creates_event_group
# ===========================================================================

class TestMergeToWorldGraphCreatesEventGroup:
    def test_event_group_node_added(self):
        wg = _make_wg_with_npc()
        extraction = _make_extraction()
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        # event_group node should exist
        eg_node = wg.get_node("eg_1")
        assert eg_node is not None
        assert eg_node.type == "event_group"
        assert eg_node.properties.get("owner") == "npc_1"
        assert result.new_nodes >= 1
        assert "eg_1" in result.new_node_ids


# ===========================================================================
# 2. test_merge_to_world_graph_creates_sub_events
# ===========================================================================

class TestMergeToWorldGraphCreatesSubEvents:
    def test_sub_events_become_world_nodes_and_contains_edges(self):
        wg = _make_wg_with_npc()
        extraction = _make_extraction(
            sub_events=[
                EventNode(
                    id="ev_1", name="Greeting", day=1,
                    summary="Player greeted NPC", emotion="happy",
                    importance=0.5, participants=["player"],
                ),
                EventNode(
                    id="ev_2", name="Farewell", day=1,
                    summary="Player said goodbye", emotion="sad",
                    importance=0.4, participants=["player"],
                ),
            ],
        )
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        # Sub-event nodes exist
        ev1 = wg.get_node("ev_1")
        ev2 = wg.get_node("ev_2")
        assert ev1 is not None
        assert ev1.type == "memory_event"
        assert ev1.properties.get("owner") == "npc_1"
        assert ev2 is not None

        # Contains edges from event_group to sub_events
        edge_data = wg.get_edge("eg_1", "ev_1")
        assert edge_data is not None
        assert edge_data.get("relation") == "contains"

        edge_data_2 = wg.get_edge("eg_1", "ev_2")
        assert edge_data_2 is not None
        assert edge_data_2.get("relation") == "contains"

        # Both sub-events counted
        assert "ev_1" in result.new_node_ids
        assert "ev_2" in result.new_node_ids


# ===========================================================================
# 3. test_merge_to_world_graph_creates_has_memory_edge
# ===========================================================================

class TestMergeToWorldGraphCreatesHasMemoryEdge:
    def test_npc_to_event_group_has_memory(self):
        wg = _make_wg_with_npc()
        extraction = _make_extraction()
        graphizer = MemoryGraphizer()

        graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        # npc_1 --has_memory--> eg_1
        edge = wg.get_edge("npc_1", "eg_1", key=f"edge_npc_1_has_eg_1")
        assert edge is not None
        assert edge.get("relation") == "has_memory"


# ===========================================================================
# 4. test_merge_to_world_graph_creates_new_nodes
# ===========================================================================

class TestMergeToWorldGraphCreatesNewNodes:
    def test_new_nodes_become_world_nodes_with_owner(self):
        wg = _make_wg_with_npc()
        extraction = _make_extraction(
            new_nodes=[
                {"id": "item_sword", "type": "item", "name": "Sword",
                 "importance": 0.3, "properties": {}},
                {"id": "person_bob", "type": "person", "name": "Bob",
                 "importance": 0.6, "properties": {"role": "merchant"}},
            ],
        )
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        sword = wg.get_node("item_sword")
        assert sword is not None
        assert sword.type == "item"
        assert sword.properties.get("owner") == "npc_1"

        bob = wg.get_node("person_bob")
        assert bob is not None
        assert bob.properties.get("owner") == "npc_1"
        # Original properties preserved
        assert bob.properties.get("role") == "merchant"

        assert "item_sword" in result.new_node_ids
        assert "person_bob" in result.new_node_ids


# ===========================================================================
# 5. test_merge_to_world_graph_anchor_edges_guarded
# ===========================================================================

class TestMergeToWorldGraphAnchorEdgesGuarded:
    def test_location_edge_skipped_when_node_missing(self):
        """Anchor edge to a non-existent location should be silently skipped."""
        wg = _make_wg_with_npc()  # no location node
        extraction = _make_extraction(eg_location="tavern")
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        # located_in edge should NOT exist (tavern node missing)
        edge = wg.get_edge("eg_1", "tavern")
        assert edge is None

    def test_location_edge_created_when_node_exists(self):
        """When the location node exists, the anchor edge should be created."""
        wg = _make_wg_with_npc(add_location="tavern")
        extraction = _make_extraction(eg_location="tavern")
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        edge = wg.get_edge("eg_1", "tavern")
        assert edge is not None
        assert edge.get("relation") == "located_in"
        # The edge should be counted
        assert result.new_edges >= 1


# ===========================================================================
# 6. test_merge_to_world_graph_llm_edges_both_exist
# ===========================================================================

class TestMergeToWorldGraphLLMEdges:
    def test_llm_edge_created_when_both_nodes_exist(self):
        wg = _make_wg_with_npc()
        # Add target node for the edge
        wg.add_node(WorldNode(
            id="item_sword", type="item", name="Sword",
        ))
        extraction = _make_extraction(
            new_nodes=[],
            edges=[
                GraphEdgeSpec(
                    id="edge_eg1_sword", source="eg_1",
                    target="item_sword", relation="mentions", weight=0.5,
                ),
            ],
        )
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        edge = wg.get_edge("eg_1", "item_sword", key="edge_eg1_sword")
        assert edge is not None
        assert edge.get("relation") == "mentions"
        assert "edge_eg1_sword" in result.new_edge_ids

    def test_llm_edge_skipped_when_target_missing(self):
        wg = _make_wg_with_npc()
        extraction = _make_extraction(
            edges=[
                GraphEdgeSpec(
                    id="edge_eg1_ghost", source="eg_1",
                    target="ghost_node", relation="mentions", weight=0.5,
                ),
            ],
        )
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        # Edge should NOT exist because ghost_node is not in graph
        assert "edge_eg1_ghost" not in result.new_edge_ids

    def test_llm_edge_skipped_when_source_missing(self):
        wg = _make_wg_with_npc()
        wg.add_node(WorldNode(id="item_x", type="item", name="X"))
        extraction = _make_extraction(
            edges=[
                GraphEdgeSpec(
                    id="edge_phantom_x", source="phantom_source",
                    target="item_x", relation="owns", weight=0.5,
                ),
            ],
        )
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        assert "edge_phantom_x" not in result.new_edge_ids


# ===========================================================================
# 7. test_merge_to_world_graph_state_updates
# ===========================================================================

class TestMergeToWorldGraphStateUpdates:
    def test_state_updates_applied_via_merge_state(self):
        wg = _make_wg_with_npc()
        extraction = _make_extraction(
            state_updates={"mood": "happy", "trust": 5},
        )
        graphizer = MemoryGraphizer()

        graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        state = wg.get_node_state("npc_1")
        assert state.get("mood") == "happy"
        assert state.get("trust") == 5


# ===========================================================================
# 8. test_merge_to_world_graph_ensures_owner_node
# ===========================================================================

class TestMergeToWorldGraphEnsuresOwnerNode:
    def test_owner_node_created_when_missing(self):
        """If npc_id is not in the graph, it should be auto-created."""
        wg = WorldGraph()  # empty graph, no npc_1
        wg.add_node(WorldNode(id="player", type="npc", name="Player"))
        extraction = _make_extraction()
        graphizer = MemoryGraphizer()

        graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        # Owner node should have been auto-created
        owner = wg.get_node("npc_1")
        assert owner is not None
        assert owner.type == "npc"
        assert owner.properties.get("character_id") == "npc_1"
        assert owner.properties.get("owner") == "npc_1"

    def test_owner_node_not_duplicated_when_exists(self):
        """If npc_id already exists, it should not be replaced."""
        wg = _make_wg_with_npc()
        original_name = wg.get_node("npc_1").name
        extraction = _make_extraction()
        graphizer = MemoryGraphizer()

        graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        # Should keep original name
        assert wg.get_node("npc_1").name == original_name


# ===========================================================================
# 9. test_get_important_nodes_from_wg_character_first
# ===========================================================================

class TestGetImportantNodesFromWGCharacterFirst:
    def test_owner_indexed_nodes_come_first(self):
        wg = _make_wg_with_npc()
        # Add several memory nodes owned by npc_1
        for i, imp in enumerate([0.9, 0.3, 0.7]):
            wg.add_node(WorldNode(
                id=f"mem_{i}", type="event_group", name=f"Memory {i}",
                importance=imp,
                properties={"owner": "npc_1", "day": 1},
            ))
        # Add a world-level node (not owned by npc_1)
        wg.add_node(WorldNode(
            id="city_1", type="location", name="City",
            importance=1.0,
        ))

        graphizer = MemoryGraphizer()
        nodes = graphizer._get_important_nodes_from_wg(wg, "npc_1", limit=50)

        # Character nodes should appear first
        char_nodes = [n for n in nodes if n["_scope"] == "character"]
        world_nodes = [n for n in nodes if n["_scope"] == "world"]
        assert len(char_nodes) >= 3
        # Character nodes sorted by importance descending
        importances = [n["importance"] for n in char_nodes]
        assert importances == sorted(importances, reverse=True)

        # World nodes come after character nodes
        if world_nodes:
            first_world_idx = next(
                i for i, n in enumerate(nodes) if n["_scope"] == "world"
            )
            last_char_idx = max(
                i for i, n in enumerate(nodes) if n["_scope"] == "character"
            )
            assert first_world_idx > last_char_idx


# ===========================================================================
# 10. test_get_important_nodes_from_wg_scene_neighbors
# ===========================================================================

class TestGetImportantNodesFromWGSceneNeighbors:
    def test_scene_neighbors_in_results(self):
        wg = _make_wg_with_npc()
        # Add a scene location with neighbors
        wg.add_node(WorldNode(
            id="scene_tavern", type="location", name="Tavern",
            importance=0.5,
        ))
        wg.add_node(WorldNode(
            id="npc_barkeeper", type="npc", name="Barkeeper",
            importance=0.6,
        ))
        wg.add_node(WorldNode(
            id="item_ale", type="item", name="Ale",
            importance=0.2,
        ))
        # Connect neighbors to scene
        wg.add_edge("scene_tavern", "npc_barkeeper", "hosts",
                     key="edge_tavern_barkeeper")
        wg.add_edge("scene_tavern", "item_ale", "has_item",
                     key="edge_tavern_ale")

        graphizer = MemoryGraphizer()
        nodes = graphizer._get_important_nodes_from_wg(
            wg, "npc_1", limit=50, current_scene="scene_tavern",
        )

        node_ids = {n["id"] for n in nodes}
        # Neighbors should be present
        assert "npc_barkeeper" in node_ids
        assert "item_ale" in node_ids


# ===========================================================================
# 11. test_get_important_nodes_from_wg_world_fill
# ===========================================================================

class TestGetImportantNodesFromWGWorldFill:
    def test_world_type_nodes_fill_remaining_quota(self):
        wg = _make_wg_with_npc()
        # Add world-type nodes (location, npc, area, item)
        for i in range(5):
            wg.add_node(WorldNode(
                id=f"loc_{i}", type="location", name=f"Location {i}",
                importance=0.5 + i * 0.1,
            ))
        graphizer = MemoryGraphizer()

        nodes = graphizer._get_important_nodes_from_wg(
            wg, "npc_1", limit=50,
        )

        world_nodes = [n for n in nodes if n["_scope"] == "world"]
        world_ids = {n["id"] for n in world_nodes}
        # All 5 location nodes should be in the world fill
        for i in range(5):
            assert f"loc_{i}" in world_ids

    def test_world_fill_respects_limit(self):
        wg = _make_wg_with_npc()
        # Add many world nodes exceeding the world_limit (limit - char_limit)
        for i in range(40):
            wg.add_node(WorldNode(
                id=f"loc_{i}", type="location", name=f"Location {i}",
                importance=0.01 * i,
            ))
        graphizer = MemoryGraphizer()

        # With limit=35, char_limit=min(35,30)=30, world_limit=5
        nodes = graphizer._get_important_nodes_from_wg(
            wg, "npc_1", limit=35,
        )
        world_nodes = [n for n in nodes if n["_scope"] == "world"]
        assert len(world_nodes) <= 5


# ===========================================================================
# 12. test_extract_metadata
# ===========================================================================

class TestExtractMetadata:
    def test_extracts_story_events_transition_chapter(self):
        extraction = _make_extraction(
            transcript=[
                TranscriptMessage(
                    role="player", content="Let's go",
                    metadata={
                        "story_events": ["event_A", "event_B"],
                        "transition": "forest_clearing",
                        "chapter_id": "ch_2",
                    },
                ),
                TranscriptMessage(
                    role="npc", content="Follow me",
                    metadata={
                        "story_events": ["event_B", "event_C"],
                    },
                ),
            ],
        )
        graphizer = MemoryGraphizer()

        story_ids, transition, chapter = graphizer._extract_metadata(extraction)

        # story_event_ids should be sorted unique set
        assert story_ids == ["event_A", "event_B", "event_C"]
        # transition_target from first message with it
        assert transition == "forest_clearing"
        # chapter_id from first message with it
        assert chapter == "ch_2"

    def test_empty_metadata(self):
        extraction = _make_extraction(
            transcript=[
                TranscriptMessage(role="player", content="Hi"),
            ],
        )
        graphizer = MemoryGraphizer()

        story_ids, transition, chapter = graphizer._extract_metadata(extraction)

        assert story_ids == []
        assert transition == ""
        assert chapter == ""

    def test_no_event_group(self):
        """When event_group is None, _extract_metadata returns defaults."""
        extraction = ExtractedElements()
        graphizer = MemoryGraphizer()

        story_ids, transition, chapter = graphizer._extract_metadata(extraction)

        assert story_ids == []
        assert transition == ""
        assert chapter == ""


# ===========================================================================
# Bonus: graphize() routes to WorldGraph path
# ===========================================================================

class TestGraphizeRoutesToWorldGraph:
    def test_graphize_uses_world_graph_path(self):
        """graphize(..., world_graph=wg) should call _merge_to_world_graph."""
        async def _run():
            wg = _make_wg_with_npc()

            graphizer = MemoryGraphizer()

            # Mock the LLM extraction to return a controlled result
            extraction = _make_extraction()
            graphizer._extract_graph_elements = AsyncMock(return_value=extraction)

            request = GraphizeRequest(
                npc_id="npc_1",
                world_id="test_world",
                messages=[
                    WindowMessage(
                        id="msg_1", role="user", content="Hello",
                        token_count=5,
                    ),
                ],
                game_day=1,
                current_scene="tavern",
            )

            result = await graphizer.graphize(request, world_graph=wg)

            assert result.success is True
            assert result.messages_processed == 1
            # The event_group should be in the WorldGraph
            assert wg.has_node("eg_1")

        asyncio.run(_run())

    def test_graphize_wg_gets_npc_profile_from_wg(self):
        """When npc_profile is None and world_graph is provided,
        graphize should get the profile from world_graph."""
        async def _run():
            wg = _make_wg_with_npc()

            graphizer = MemoryGraphizer()
            extraction = _make_extraction()
            graphizer._extract_graph_elements = AsyncMock(return_value=extraction)

            request = GraphizeRequest(
                npc_id="npc_1",
                world_id="test_world",
                messages=[
                    WindowMessage(
                        id="msg_1", role="user", content="Hello",
                        token_count=5,
                    ),
                ],
                game_day=1,
            )

            result = await graphizer.graphize(request, world_graph=wg)

            assert result.success is True
            # _extract_graph_elements should have been called with a CharacterProfile
            call_kwargs = graphizer._extract_graph_elements.call_args
            npc_profile = call_kwargs.kwargs.get("npc_profile") or call_kwargs[1].get("npc_profile")
            # The profile name should come from the WorldGraph node
            assert npc_profile.name == "Test NPC"

        asyncio.run(_run())

    def test_graphize_wg_empty_messages_returns_early(self):
        """graphize with empty messages should return early."""
        async def _run():
            wg = _make_wg_with_npc()
            graphizer = MemoryGraphizer()

            request = GraphizeRequest(
                npc_id="npc_1",
                world_id="test_world",
                messages=[],
                game_day=1,
            )

            result = await graphizer.graphize(request, world_graph=wg)

            assert result.success is True
            assert result.messages_processed == 0

        asyncio.run(_run())


# ===========================================================================
# Edge cases
# ===========================================================================

class TestMergeEdgeCases:
    def test_participant_edge_only_if_node_exists(self):
        """Participant anchor edges should only be created if participant node exists."""
        wg = _make_wg_with_npc(add_player=False)
        extraction = _make_extraction(participants=["player", "npc_1", "npc_2"])
        graphizer = MemoryGraphizer()

        # npc_2 does not exist in graph
        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        # No edge to player (player not in graph)
        edge_player = wg.get_edge("eg_1", "player")
        assert edge_player is None

        # participated edge to npc_1 (owner) should exist
        edge_owner = wg.get_edge("eg_1", "npc_1")
        assert edge_owner is not None
        assert edge_owner.get("relation") == "participated"

    def test_no_event_group_still_creates_new_nodes(self):
        """Even without event_group, new_nodes should still be created."""
        wg = _make_wg_with_npc()
        extraction = ExtractedElements(
            new_nodes=[
                {"id": "item_potion", "type": "item", "name": "Potion",
                 "importance": 0.4, "properties": {}},
            ],
        )
        graphizer = MemoryGraphizer()

        result = graphizer._merge_to_world_graph(wg, "npc_1", extraction)

        potion = wg.get_node("item_potion")
        assert potion is not None
        assert potion.properties.get("owner") == "npc_1"
        assert "item_potion" in result.new_node_ids
