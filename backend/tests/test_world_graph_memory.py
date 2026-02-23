"""WorldGraph L3 Memory Enhancement Tests -- Phase 1

Tests for the 5 new indexes, 7 new query methods, and index maintenance
added in the L3 M1 milestone of WorldGraph.

Indexes:
  _name_index, _day_index, _participant_index, _owner_index, _edge_id_index

Query methods:
  find_nodes_by_name, find_nodes_by_day, find_nodes_by_participant,
  find_memories_of, in_neighbors, degree, subgraph

Index maintenance:
  add_node (new + replace), _deindex_node, add_edge, remove_edge
"""
from __future__ import annotations

import pytest

from app.world.graph.models import WorldEdgeType, WorldNode, WorldNodeType
from app.world.graph.snapshot import (
    WorldSnapshot,
    capture_snapshot,
    restore_snapshot,
)
from app.world.graph.world_graph import WorldGraph


# =============================================================================
# Helper
# =============================================================================


def _node(
    id: str,
    type: str = "npc",
    name: str = "",
    importance: float = 0.0,
    **props,
) -> WorldNode:
    """Create a minimal WorldNode for testing."""
    return WorldNode(
        id=id,
        type=type,
        name=name,
        importance=importance,
        properties=props,
        state={},
        behaviors=[],
    )


# =============================================================================
# 1. Memory node types
# =============================================================================


class TestMemoryNodeTypes:
    """Verify the 6 new memory node types work with get_by_type."""

    @pytest.mark.parametrize("node_type", [
        WorldNodeType.EVENT_GROUP,
        WorldNodeType.MEMORY_EVENT,
        WorldNodeType.IMPRESSION,
        WorldNodeType.KNOWLEDGE,
        WorldNodeType.RUMOR,
        WorldNodeType.MEMORY,
    ])
    def test_memory_node_type_indexed(self, node_type: WorldNodeType):
        """Add a node with each new memory type, verify get_by_type returns it."""
        wg = WorldGraph()
        wg.add_node(_node(f"mem_{node_type.value}", type=node_type.value, name="Test"))
        result = wg.get_by_type(node_type.value)
        assert f"mem_{node_type.value}" in result

    def test_all_memory_types_coexist(self):
        """Multiple memory types can coexist without interference."""
        wg = WorldGraph()
        types = [
            WorldNodeType.EVENT_GROUP,
            WorldNodeType.MEMORY_EVENT,
            WorldNodeType.IMPRESSION,
            WorldNodeType.KNOWLEDGE,
            WorldNodeType.RUMOR,
            WorldNodeType.MEMORY,
        ]
        for t in types:
            wg.add_node(_node(f"n_{t.value}", type=t.value, name=t.value))

        for t in types:
            result = wg.get_by_type(t.value)
            assert len(result) == 1
            assert result[0] == f"n_{t.value}"

        assert wg.node_count() == len(types)


# =============================================================================
# 2. HAS_MEMORY edge isolation
# =============================================================================


class TestHasMemoryEdgeIsolation:
    """HAS_MEMORY must NOT pollute _entities_at."""

    def test_has_memory_not_in_entities_at(self):
        """Adding a HAS_MEMORY edge should NOT add target to _entities_at."""
        wg = WorldGraph()
        wg.add_node(_node("npc_a", type="npc", name="NPC A"))
        wg.add_node(_node("eg_1", type=WorldNodeType.EVENT_GROUP.value, name="Event Group"))
        wg.add_edge("npc_a", "eg_1", WorldEdgeType.HAS_MEMORY.value, key="hm_1")

        # _entities_at should NOT contain this edge
        entities = wg.get_entities_at("npc_a")
        assert "eg_1" not in entities

    def test_hosts_still_in_entities_at(self):
        """Sanity check: HOSTS edge DOES appear in _entities_at."""
        wg = WorldGraph()
        wg.add_node(_node("loc_a", type="location", name="Location A"))
        wg.add_node(_node("npc_b", type="npc", name="NPC B"))
        wg.add_edge("loc_a", "npc_b", WorldEdgeType.HOSTS.value, key="hosts_b")

        entities = wg.get_entities_at("loc_a")
        assert "npc_b" in entities


# =============================================================================
# 3. _name_index
# =============================================================================


class TestNameIndex:
    """find_nodes_by_name with case-insensitive matching."""

    def test_find_by_exact_name(self):
        """Add nodes with names, find_nodes_by_name returns them."""
        wg = WorldGraph()
        wg.add_node(_node("n1", name="Marcus"))
        wg.add_node(_node("n2", name="Lydia"))

        results = wg.find_nodes_by_name("Marcus")
        assert len(results) == 1
        assert results[0].id == "n1"

    def test_case_insensitive(self):
        """find_nodes_by_name is case insensitive."""
        wg = WorldGraph()
        wg.add_node(_node("n1", name="Marcus"))

        # Lowercase query
        results = wg.find_nodes_by_name("marcus")
        assert len(results) == 1
        assert results[0].id == "n1"

        # Uppercase query
        results = wg.find_nodes_by_name("MARCUS")
        assert len(results) == 1
        assert results[0].id == "n1"

    def test_replace_node_different_name(self):
        """Replace node with different name: old name empty, new name finds it."""
        wg = WorldGraph()
        wg.add_node(_node("n1", name="OldName"))
        assert len(wg.find_nodes_by_name("OldName")) == 1

        # Replace with different name
        wg.add_node(_node("n1", name="NewName"))
        assert len(wg.find_nodes_by_name("OldName")) == 0
        results = wg.find_nodes_by_name("NewName")
        assert len(results) == 1
        assert results[0].id == "n1"

    def test_remove_node_cleans_name_index(self):
        """Remove node clears name from index."""
        wg = WorldGraph()
        wg.add_node(_node("n1", name="Marcus"))
        assert len(wg.find_nodes_by_name("Marcus")) == 1

        wg.remove_node("n1")
        assert len(wg.find_nodes_by_name("Marcus")) == 0

    def test_empty_name_not_indexed(self):
        """Node with empty name string should not be indexed."""
        wg = WorldGraph()
        wg.add_node(_node("n1", name=""))

        # Empty name should not index
        assert len(wg.find_nodes_by_name("")) == 0

    def test_multiple_nodes_same_name(self):
        """Multiple nodes sharing the same name are all found."""
        wg = WorldGraph()
        wg.add_node(_node("n1", name="Guard"))
        wg.add_node(_node("n2", name="Guard"))

        results = wg.find_nodes_by_name("Guard")
        assert len(results) == 2
        ids = {r.id for r in results}
        assert ids == {"n1", "n2"}


# =============================================================================
# 4. _day_index
# =============================================================================


class TestDayIndex:
    """find_nodes_by_day with properties.day and properties.game_day."""

    def test_find_by_day_property(self):
        """Node with properties.day is found by find_nodes_by_day."""
        wg = WorldGraph()
        wg.add_node(_node("evt1", type=WorldNodeType.MEMORY_EVENT.value, name="E1", day=3))

        results = wg.find_nodes_by_day(3)
        assert len(results) == 1
        assert results[0].id == "evt1"

    def test_find_by_game_day_property(self):
        """Node with properties.game_day is also found."""
        wg = WorldGraph()
        wg.add_node(_node("evt2", type=WorldNodeType.MEMORY_EVENT.value, name="E2", game_day=5))

        results = wg.find_nodes_by_day(5)
        assert len(results) == 1
        assert results[0].id == "evt2"

    def test_day_zero_valid_via_game_day(self):
        """day=0 via game_day key should be valid and indexed.

        Note: Using the "day" key with value 0 hits a known or-falsy pitfall
        in the production code (``day = props.get("day") or props.get("game_day")``
        where ``0 or X`` evaluates to X). Using "game_day" as the key avoids
        this since ``None or 0`` evaluates to ``0``.
        """
        wg = WorldGraph()
        wg.add_node(_node("evt0", type=WorldNodeType.MEMORY_EVENT.value, name="Day Zero", game_day=0))

        results = wg.find_nodes_by_day(0)
        assert len(results) == 1
        assert results[0].id == "evt0"

    def test_day_zero_via_day_key(self):
        """day=0 via 'day' key should be correctly indexed."""
        wg = WorldGraph()
        wg.add_node(_node("evt0", type=WorldNodeType.MEMORY_EVENT.value, name="Day Zero", day=0))

        results = wg.find_nodes_by_day(0)
        assert len(results) == 1

    def test_day_not_found(self):
        """Query for nonexistent day returns empty."""
        wg = WorldGraph()
        wg.add_node(_node("evt1", type=WorldNodeType.MEMORY_EVENT.value, name="E1", day=3))

        assert len(wg.find_nodes_by_day(99)) == 0

    def test_remove_node_cleans_day_index(self):
        """Removing node clears its day index entry."""
        wg = WorldGraph()
        wg.add_node(_node("evt1", name="E1", day=3))
        assert len(wg.find_nodes_by_day(3)) == 1

        wg.remove_node("evt1")
        assert len(wg.find_nodes_by_day(3)) == 0


# =============================================================================
# 5. _participant_index
# =============================================================================


class TestParticipantIndex:
    """find_nodes_by_participant with properties.participants list."""

    def test_find_by_participant(self):
        """Node with participants list is found for each participant."""
        wg = WorldGraph()
        wg.add_node(_node(
            "evt1", type=WorldNodeType.MEMORY_EVENT.value, name="Battle",
            participants=["player", "npc_a"],
        ))

        results_player = wg.find_nodes_by_participant("player")
        assert len(results_player) == 1
        assert results_player[0].id == "evt1"

        results_npc = wg.find_nodes_by_participant("npc_a")
        assert len(results_npc) == 1
        assert results_npc[0].id == "evt1"

    def test_participant_not_found(self):
        """Query for nonexistent participant returns empty."""
        wg = WorldGraph()
        wg.add_node(_node(
            "evt1", name="Battle",
            participants=["player"],
        ))
        assert len(wg.find_nodes_by_participant("unknown_npc")) == 0

    def test_remove_node_cleans_participant_index(self):
        """Removing node clears participant index entries."""
        wg = WorldGraph()
        wg.add_node(_node(
            "evt1", name="Battle",
            participants=["player", "npc_a"],
        ))
        assert len(wg.find_nodes_by_participant("player")) == 1
        assert len(wg.find_nodes_by_participant("npc_a")) == 1

        wg.remove_node("evt1")
        assert len(wg.find_nodes_by_participant("player")) == 0
        assert len(wg.find_nodes_by_participant("npc_a")) == 0

    def test_multiple_events_same_participant(self):
        """Participant appearing in multiple events returns all of them."""
        wg = WorldGraph()
        wg.add_node(_node("evt1", name="Battle 1", participants=["player"]))
        wg.add_node(_node("evt2", name="Battle 2", participants=["player", "npc_b"]))

        results = wg.find_nodes_by_participant("player")
        assert len(results) == 2
        ids = {r.id for r in results}
        assert ids == {"evt1", "evt2"}


# =============================================================================
# 6. _owner_index
# =============================================================================


class TestOwnerIndex:
    """find_memories_of with properties.owner."""

    def test_find_by_owner(self):
        """Node with owner property is found by find_memories_of."""
        wg = WorldGraph()
        wg.add_node(_node(
            "mem1", type=WorldNodeType.IMPRESSION.value,
            name="Impression of Player",
            owner="npc_priestess",
        ))

        results = wg.find_memories_of("npc_priestess")
        assert len(results) == 1
        assert results[0].id == "mem1"

    def test_replace_node_changes_owner(self):
        """Replace node's owner: old owner empty, new owner finds it."""
        wg = WorldGraph()
        wg.add_node(_node("mem1", name="Memory", owner="npc_a"))
        assert len(wg.find_memories_of("npc_a")) == 1

        # Replace with different owner
        wg.add_node(_node("mem1", name="Memory", owner="npc_b"))
        assert len(wg.find_memories_of("npc_a")) == 0
        assert len(wg.find_memories_of("npc_b")) == 1

    def test_owner_not_found(self):
        """Query for nonexistent owner returns empty."""
        wg = WorldGraph()
        wg.add_node(_node("mem1", name="Memory", owner="npc_a"))
        assert len(wg.find_memories_of("nobody")) == 0

    def test_remove_node_cleans_owner_index(self):
        """Removing node clears owner index entry."""
        wg = WorldGraph()
        wg.add_node(_node("mem1", name="Memory", owner="npc_priestess"))
        assert len(wg.find_memories_of("npc_priestess")) == 1

        wg.remove_node("mem1")
        assert len(wg.find_memories_of("npc_priestess")) == 0

    def test_multiple_memories_same_owner(self):
        """Owner with multiple memory nodes returns all."""
        wg = WorldGraph()
        wg.add_node(_node("m1", name="Impression", owner="npc_priestess"))
        wg.add_node(_node("m2", name="Knowledge", owner="npc_priestess"))
        wg.add_node(_node("m3", name="Rumor", owner="npc_warrior"))

        results = wg.find_memories_of("npc_priestess")
        assert len(results) == 2
        ids = {r.id for r in results}
        assert ids == {"m1", "m2"}


# =============================================================================
# 7. _edge_id_index
# =============================================================================


class TestEdgeIdIndex:
    """_edge_id_index maps edge_key -> (source, target, key)."""

    def test_add_edge_populates_index(self):
        """add_edge stores entry in _edge_id_index."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_edge("a", "b", WorldEdgeType.HAS_MEMORY.value, key="hm_1")

        assert "hm_1" in wg._edge_id_index
        assert wg._edge_id_index["hm_1"] == ("a", "b", "hm_1")

    def test_remove_edge_cleans_index(self):
        """remove_edge removes key from _edge_id_index."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_edge("a", "b", WorldEdgeType.CAUSED.value, key="caused_1")
        assert "caused_1" in wg._edge_id_index

        wg.remove_edge("a", "b", "caused_1")
        assert "caused_1" not in wg._edge_id_index

    def test_connects_auto_reverse_in_index(self):
        """CONNECTS auto-reverse edge also appears in _edge_id_index."""
        wg = WorldGraph()
        wg.add_node(_node("area1", type="area", name="Area 1"))
        wg.add_node(_node("area2", type="area", name="Area 2"))
        wg.add_edge("area1", "area2", WorldEdgeType.CONNECTS.value, key="conn_1_2")

        # Forward edge
        assert "conn_1_2" in wg._edge_id_index
        assert wg._edge_id_index["conn_1_2"] == ("area1", "area2", "conn_1_2")

        # Auto-reverse edge
        assert "conn_1_2_rev" in wg._edge_id_index
        assert wg._edge_id_index["conn_1_2_rev"] == ("area2", "area1", "conn_1_2_rev")

    def test_remove_connects_cleans_both_index_entries(self):
        """Removing a CONNECTS edge cleans both forward and reverse from _edge_id_index."""
        wg = WorldGraph()
        wg.add_node(_node("a", type="area", name="A"))
        wg.add_node(_node("b", type="area", name="B"))
        wg.add_edge("a", "b", WorldEdgeType.CONNECTS.value, key="conn_ab")

        assert "conn_ab" in wg._edge_id_index
        assert "conn_ab_rev" in wg._edge_id_index

        wg.remove_edge("a", "b", "conn_ab")
        assert "conn_ab" not in wg._edge_id_index
        assert "conn_ab_rev" not in wg._edge_id_index

    def test_default_key_is_relation(self):
        """When key is not specified, the relation value is used as key."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_edge("a", "b", WorldEdgeType.MENTIONS.value)

        expected_key = WorldEdgeType.MENTIONS.value
        assert expected_key in wg._edge_id_index
        assert wg._edge_id_index[expected_key] == ("a", "b", expected_key)


# =============================================================================
# 8. in_neighbors
# =============================================================================


class TestInNeighbors:
    """in_neighbors returns incoming edge sources."""

    def test_basic_in_neighbors(self):
        """Build A->B->C, in_neighbors(B) returns [(A, edge_data)]."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_node(_node("c", name="C"))
        wg.add_edge("a", "b", WorldEdgeType.HAS_MEMORY.value, key="hm_ab")
        wg.add_edge("b", "c", WorldEdgeType.FOLLOWED_BY.value, key="fb_bc")

        in_nb = wg.in_neighbors("b")
        assert len(in_nb) == 1
        source_id, data = in_nb[0]
        assert source_id == "a"
        assert data["relation"] == WorldEdgeType.HAS_MEMORY.value

    def test_in_neighbors_relation_filter(self):
        """in_neighbors with relation filter returns only matching edges."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_node(_node("c", name="C"))
        wg.add_edge("a", "b", WorldEdgeType.CAUSED.value, key="caused_ab")
        wg.add_edge("c", "b", WorldEdgeType.MENTIONS.value, key="mentions_cb")

        # Filter by CAUSED only
        results = wg.in_neighbors("b", relation=WorldEdgeType.CAUSED.value)
        assert len(results) == 1
        assert results[0][0] == "a"

        # Filter by MENTIONS only
        results = wg.in_neighbors("b", relation=WorldEdgeType.MENTIONS.value)
        assert len(results) == 1
        assert results[0][0] == "c"

    def test_in_neighbors_nonexistent_node(self):
        """in_neighbors on nonexistent node returns empty list."""
        wg = WorldGraph()
        assert wg.in_neighbors("nonexistent") == []


# =============================================================================
# 9. degree
# =============================================================================


class TestDegree:
    """degree returns in_degree + out_degree."""

    def test_basic_degree(self):
        """Build A->B<-C, degree(B) == 2."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_node(_node("c", name="C"))
        wg.add_edge("a", "b", WorldEdgeType.CAUSED.value, key="caused_ab")
        wg.add_edge("c", "b", WorldEdgeType.MENTIONS.value, key="mentions_cb")

        assert wg.degree("b") == 2

    def test_degree_with_outgoing(self):
        """Node with both in and out edges counts all."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_node(_node("c", name="C"))
        wg.add_edge("a", "b", WorldEdgeType.CAUSED.value, key="e1")
        wg.add_edge("b", "c", WorldEdgeType.FOLLOWED_BY.value, key="e2")

        # B has 1 incoming + 1 outgoing = 2
        assert wg.degree("b") == 2

    def test_degree_nonexistent_returns_zero(self):
        """degree of nonexistent node returns 0."""
        wg = WorldGraph()
        assert wg.degree("nonexistent") == 0

    def test_degree_isolated_node(self):
        """Isolated node has degree 0."""
        wg = WorldGraph()
        wg.add_node(_node("lonely", name="Lonely"))
        assert wg.degree("lonely") == 0


# =============================================================================
# 10. subgraph
# =============================================================================


class TestSubgraph:
    """subgraph extracts nodes and their inter-edges as a new WorldGraph."""

    def _build_five_node_graph(self) -> WorldGraph:
        """Build a 5-node graph: A->B->C, A->D, D->E."""
        wg = WorldGraph()
        for nid in ["a", "b", "c", "d", "e"]:
            wg.add_node(_node(nid, name=nid.upper()))
        wg.add_edge("a", "b", WorldEdgeType.CAUSED.value, key="e_ab")
        wg.add_edge("b", "c", WorldEdgeType.FOLLOWED_BY.value, key="e_bc")
        wg.add_edge("a", "d", WorldEdgeType.MENTIONS.value, key="e_ad")
        wg.add_edge("d", "e", WorldEdgeType.KNOWS.value, key="e_de")
        return wg

    def test_subgraph_contains_only_specified_nodes(self):
        """Extract subgraph of {a, b, c}: only those 3 nodes present."""
        wg = self._build_five_node_graph()
        sub = wg.subgraph(["a", "b", "c"])

        assert sub.node_count() == 3
        assert sub.has_node("a")
        assert sub.has_node("b")
        assert sub.has_node("c")
        assert not sub.has_node("d")
        assert not sub.has_node("e")

    def test_subgraph_contains_inter_edges_only(self):
        """Only edges between selected nodes are included."""
        wg = self._build_five_node_graph()
        sub = wg.subgraph(["a", "b", "c"])

        # a->b and b->c should exist
        assert sub.get_edge("a", "b", "e_ab") is not None
        assert sub.get_edge("b", "c", "e_bc") is not None

        # a->d should NOT exist (d not in subgraph)
        assert sub.get_edge("a", "d", "e_ad") is None

    def test_subgraph_deep_copy(self):
        """Modifying subgraph node does not affect original."""
        wg = self._build_five_node_graph()
        sub = wg.subgraph(["a", "b"])

        # Modify subgraph node
        sub_node_a = sub.get_node("a")
        sub_node_a.state["modified"] = True

        # Original should be unaffected
        orig_node_a = wg.get_node("a")
        assert "modified" not in orig_node_a.state

    def test_subgraph_type_index(self):
        """Subgraph has correct type index."""
        wg = self._build_five_node_graph()
        sub = wg.subgraph(["a", "b"])

        results = sub.get_by_type("npc")
        assert set(results) == {"a", "b"}

    def test_subgraph_edge_id_index(self):
        """Subgraph populates _edge_id_index for included edges."""
        wg = self._build_five_node_graph()
        sub = wg.subgraph(["a", "b", "c"])

        assert "e_ab" in sub._edge_id_index
        assert "e_bc" in sub._edge_id_index
        # e_ad should not be there since d is not included
        assert "e_ad" not in sub._edge_id_index


# =============================================================================
# 11. _deindex_node completeness
# =============================================================================


class TestDeindexNodeCompleteness:
    """Add node with ALL property-based indexes, remove it, verify all clean."""

    def test_full_deindex(self):
        """Node with name, day, participants, owner -- all indexes cleared on remove."""
        wg = WorldGraph()
        wg.add_node(_node(
            "full_node",
            type=WorldNodeType.MEMORY_EVENT.value,
            name="FullEvent",
            day=5,
            participants=["player", "npc_a", "npc_b"],
            owner="npc_priestess",
        ))

        # Verify all indexes are populated
        assert len(wg.find_nodes_by_name("FullEvent")) == 1
        assert len(wg.find_nodes_by_day(5)) == 1
        assert len(wg.find_nodes_by_participant("player")) == 1
        assert len(wg.find_nodes_by_participant("npc_a")) == 1
        assert len(wg.find_nodes_by_participant("npc_b")) == 1
        assert len(wg.find_memories_of("npc_priestess")) == 1
        assert "full_node" in wg.get_by_type(WorldNodeType.MEMORY_EVENT.value)

        # Remove
        wg.remove_node("full_node")

        # All indexes should be clean
        assert len(wg.find_nodes_by_name("FullEvent")) == 0
        assert len(wg.find_nodes_by_day(5)) == 0
        assert len(wg.find_nodes_by_participant("player")) == 0
        assert len(wg.find_nodes_by_participant("npc_a")) == 0
        assert len(wg.find_nodes_by_participant("npc_b")) == 0
        assert len(wg.find_memories_of("npc_priestess")) == 0
        assert "full_node" not in wg.get_by_type(WorldNodeType.MEMORY_EVENT.value)

    def test_deindex_only_affects_target_node(self):
        """Removing one node does not affect another node's index entries."""
        wg = WorldGraph()
        wg.add_node(_node("n1", name="Alice", owner="npc_a", day=1, participants=["player"]))
        wg.add_node(_node("n2", name="Bob", owner="npc_b", day=2, participants=["player"]))

        wg.remove_node("n1")

        # n2's entries should be intact
        assert len(wg.find_nodes_by_name("Bob")) == 1
        assert len(wg.find_memories_of("npc_b")) == 1
        assert len(wg.find_nodes_by_day(2)) == 1
        # "player" should still be found via n2
        assert len(wg.find_nodes_by_participant("player")) == 1
        assert wg.find_nodes_by_participant("player")[0].id == "n2"


# =============================================================================
# 12. Snapshot compatibility
# =============================================================================


class TestSnapshotCompatibility:
    """Memory nodes survive capture_snapshot + restore_snapshot roundtrip."""

    def test_snapshot_roundtrip_with_memory_nodes(self):
        """Create graph with memory nodes, capture, restore via spawned_nodes path."""
        # Build base graph with npc_priestess as a build-time node
        wg1 = WorldGraph()
        wg1.add_node(_node("world_root", type=WorldNodeType.WORLD.value, name="World"))
        wg1.add_node(_node("npc_priestess", type="npc", name="Priestess"))
        wg1.seal()

        # Add memory nodes (runtime spawned)
        wg1.add_node(_node(
            "eg_1", type=WorldNodeType.EVENT_GROUP.value,
            name="Tavern Encounter",
            owner="npc_priestess",
            day=3,
            participants=["player", "npc_priestess"],
        ))
        wg1.add_node(_node(
            "mem_1", type=WorldNodeType.IMPRESSION.value,
            name="First Impression",
            owner="npc_priestess",
        ))
        # Add edge between build-time node and spawned node
        wg1.add_edge("npc_priestess", "eg_1", WorldEdgeType.HAS_MEMORY.value, key="hm_eg1")

        # Capture snapshot
        snap = capture_snapshot(wg1, "w1", "s1", game_day=3)

        # Verify spawned nodes are captured
        assert len(snap.spawned_nodes) == 2

        # Rebuild fresh graph and restore
        wg2 = WorldGraph()
        wg2.add_node(_node("world_root", type=WorldNodeType.WORLD.value, name="World"))
        wg2.add_node(_node("npc_priestess", type="npc", name="Priestess"))
        wg2.seal()

        restore_snapshot(wg2, snap)

        # Verify memory nodes are back
        assert wg2.has_node("eg_1")
        assert wg2.has_node("mem_1")

        # Verify indexes are populated after restore
        assert len(wg2.find_nodes_by_name("Tavern Encounter")) == 1
        assert len(wg2.find_nodes_by_name("First Impression")) == 1
        assert len(wg2.find_memories_of("npc_priestess")) == 2
        assert len(wg2.find_nodes_by_day(3)) == 1
        assert len(wg2.find_nodes_by_participant("player")) == 1
        assert len(wg2.find_nodes_by_participant("npc_priestess")) == 1

        # Verify type index
        assert "eg_1" in wg2.get_by_type(WorldNodeType.EVENT_GROUP.value)
        assert "mem_1" in wg2.get_by_type(WorldNodeType.IMPRESSION.value)

    def test_snapshot_restore_clears_dirty(self):
        """After restore, dirty tracking is clean and sealed."""
        wg = WorldGraph()
        wg.add_node(_node("root", type=WorldNodeType.WORLD.value, name="World"))
        wg.seal()
        wg.add_node(_node("mem", type=WorldNodeType.MEMORY.value, name="M", owner="npc_a"))

        snap = capture_snapshot(wg, "w1", "s1")

        wg2 = WorldGraph()
        wg2.add_node(_node("root", type=WorldNodeType.WORLD.value, name="World"))
        wg2.add_node(_node("npc_a", type="npc", name="NPC A"))
        wg2.seal()
        restore_snapshot(wg2, snap)

        assert wg2._sealed is True
        assert len(wg2._dirty_nodes) == 0
        assert len(wg2._spawned_nodes) == 0

    def test_snapshot_with_all_memory_edge_types(self):
        """All new memory edge types survive snapshot roundtrip."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_node(_node("c", name="C"))
        wg.seal()

        memory_edges = [
            (WorldEdgeType.HAS_MEMORY, "hm_1"),
            (WorldEdgeType.OCCURRED_AT, "oa_1"),
            (WorldEdgeType.PARTICIPATED, "part_1"),
            (WorldEdgeType.CAUSED, "caused_1"),
            (WorldEdgeType.FOLLOWED_BY, "fb_1"),
            (WorldEdgeType.MENTIONS, "ment_1"),
            (WorldEdgeType.KNOWS, "knows_1"),
            (WorldEdgeType.TRUSTS, "trusts_1"),
        ]
        for edge_type, key in memory_edges:
            wg.add_edge("a", "b", edge_type.value, key=key)

        snap = capture_snapshot(wg, "w1", "s1")
        assert len(snap.modified_edges) == len(memory_edges)

        # Restore
        wg2 = WorldGraph()
        wg2.add_node(_node("a", name="A"))
        wg2.add_node(_node("b", name="B"))
        wg2.add_node(_node("c", name="C"))
        wg2.seal()
        restore_snapshot(wg2, snap)

        for edge_type, key in memory_edges:
            edge = wg2.get_edge("a", "b", key)
            assert edge is not None, f"Edge {key} ({edge_type.value}) missing after restore"
            assert edge["relation"] == edge_type.value


# =============================================================================
# Stats index sizes
# =============================================================================


class TestStatsIndexSizes:
    """stats() reports L3 index sizes."""

    def test_stats_reports_name_and_owner_index(self):
        """stats() includes name_index_size and owner_index_size."""
        wg = WorldGraph()
        wg.add_node(_node("n1", name="Alice", owner="npc_a"))
        wg.add_node(_node("n2", name="Bob", owner="npc_a"))

        stats = wg.stats()
        assert stats["name_index_size"] == 2
        assert stats["owner_index_size"] == 2

    def test_stats_reports_edge_id_index(self):
        """stats() includes edge_id_index_size."""
        wg = WorldGraph()
        wg.add_node(_node("a", name="A"))
        wg.add_node(_node("b", name="B"))
        wg.add_edge("a", "b", WorldEdgeType.CAUSED.value, key="e1")
        wg.add_edge("a", "b", WorldEdgeType.MENTIONS.value, key="e2")

        stats = wg.stats()
        assert stats["edge_id_index_size"] == 2
