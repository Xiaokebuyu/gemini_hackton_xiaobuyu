"""Tests for WorldKnowledgeGraph — nodes, edges, spreading activation.

All async calls wrapped with asyncio.run() (no pytest-asyncio installed).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.bootstrap import build_default_world
from app.world_knowledge_graph import EdgeType, WorldKnowledgeGraph


# ------------------------------------------------------------------
# Fixture helpers: build a small graph manually (no WorldInstance)
# ------------------------------------------------------------------


def _make_graph_with_fixture() -> WorldKnowledgeGraph:
    """Manual small graph:
        merchant_tom (character) --located_in--> market (area)
        merchant_tom (character) --sells--> iron_sword (item)
        market (area) --adjacent_to--> town_square (area)
        goblin (monster) --drops--> iron_sword (item)  weight=0.5
        merchant_guild (faction)
        merchant_tom --belongs_to--> merchant_guild
    """
    g = WorldKnowledgeGraph()
    G = g._graph

    G.add_node(
        "merchant_tom",
        node_type="character",
        label="Merchant Tom",
        tags=["merchant", "human"],
        description="A shrewd but fair merchant.",
        metadata={"dialogue_style": "drawl"},
    )
    G.add_node(
        "market",
        node_type="area",
        label="Market District",
        tags=["commerce", "busy"],
        description="",
        metadata={"region": "town"},
    )
    G.add_node(
        "town_square",
        node_type="area",
        label="Town Square",
        tags=["public"],
        description="",
        metadata={},
    )
    G.add_node(
        "iron_sword",
        node_type="item",
        label="Iron Sword",
        tags=["weapon", "sword"],
        description="",
        metadata={"type": "weapon", "rarity": "common"},
    )
    G.add_node(
        "goblin",
        node_type="monster",
        label="Goblin",
        tags=["humanoid", "small"],
        description="",
        metadata={"cr": 0.25},
    )
    G.add_node(
        "merchant_guild",
        node_type="faction",
        label="Merchant Guild",
        tags=["commerce"],
        description="The local trade guild.",
        metadata={"alignment": "neutral"},
    )

    G.add_edge("merchant_tom", "market", relation=EdgeType.LOCATED_IN, weight=1.0)
    G.add_edge("merchant_tom", "iron_sword", relation=EdgeType.SELLS, weight=1.0)
    G.add_edge("merchant_tom", "merchant_guild", relation=EdgeType.BELONGS_TO, weight=1.0)
    G.add_edge("market", "town_square", relation=EdgeType.ADJACENT_TO, weight=1.0)
    G.add_edge("goblin", "iron_sword", relation=EdgeType.DROPS, weight=0.5)

    return g


# ------------------------------------------------------------------
# TestWorldKnowledgeGraph — small manual graph
# ------------------------------------------------------------------


class TestWorldKnowledgeGraph:
    def test_init_graph_empty(self) -> None:
        g = WorldKnowledgeGraph()
        assert g.node_count() == 0
        assert g.edge_count() == 0

    def test_has_node_and_edge(self) -> None:
        g = _make_graph_with_fixture()
        assert g.has_node("merchant_tom")
        assert g.has_node("iron_sword")
        assert g.has_edge("merchant_tom", "iron_sword")
        assert not g.has_edge("iron_sword", "merchant_tom")  # directed

    def test_find_seed_nodes_by_label(self) -> None:
        g = _make_graph_with_fixture()
        seeds = g._find_seed_nodes(["Merchant Tom"])
        assert "merchant_tom" in seeds

    def test_find_seed_nodes_case_insensitive(self) -> None:
        g = _make_graph_with_fixture()
        seeds = g._find_seed_nodes(["merchant tom"])
        assert "merchant_tom" in seeds

    def test_find_seed_nodes_by_tag(self) -> None:
        g = _make_graph_with_fixture()
        seeds = g._find_seed_nodes(["merchant"])
        # merchant_tom has tag "merchant", merchant_guild has label containing merchant
        assert "merchant_tom" in seeds

    def test_find_seed_nodes_by_node_id_substring(self) -> None:
        g = _make_graph_with_fixture()
        seeds = g._find_seed_nodes(["goblin"])
        assert "goblin" in seeds

    def test_find_seed_nodes_no_match(self) -> None:
        g = _make_graph_with_fixture()
        seeds = g._find_seed_nodes(["xyzzy_no_match"])
        assert seeds == []

    def test_find_seed_nodes_empty_keywords(self) -> None:
        g = _make_graph_with_fixture()
        seeds = g._find_seed_nodes([])
        assert seeds == []

    def test_spread_empty_seeds_returns_empty(self) -> None:
        g = _make_graph_with_fixture()
        result = g._spread_activation([], max_depth=2, decay=0.8)
        assert result == {}

    def test_spread_depth_1_reaches_direct_neighbours(self) -> None:
        g = _make_graph_with_fixture()
        # seed = merchant_tom; depth=1 should reach market, iron_sword, merchant_guild
        activation = g._spread_activation(["merchant_tom"], max_depth=1, decay=0.8)
        assert "market" in activation
        assert "iron_sword" in activation
        assert "merchant_guild" in activation

    def test_spread_depth_1_excludes_depth_2(self) -> None:
        g = _make_graph_with_fixture()
        activation = g._spread_activation(["merchant_tom"], max_depth=1, decay=0.8)
        # town_square is depth-2 from merchant_tom (via market)
        assert "town_square" not in activation

    def test_spread_depth_2_reaches_depth_2_nodes(self) -> None:
        g = _make_graph_with_fixture()
        activation = g._spread_activation(["merchant_tom"], max_depth=2, decay=0.8)
        assert "town_square" in activation

    def test_spread_decay_value_at_depth_1(self) -> None:
        g = _make_graph_with_fixture()
        activation = g._spread_activation(["merchant_tom"], max_depth=1, decay=0.8)
        # depth-1 with edge weight 1.0: 1.0 * 0.8 * 1.0 = 0.8
        assert abs(activation["market"] - 0.8) < 1e-9

    def test_spread_decay_value_at_depth_2(self) -> None:
        g = _make_graph_with_fixture()
        activation = g._spread_activation(["merchant_tom"], max_depth=2, decay=0.8)
        # depth-2: 0.8 * 0.8 = 0.64
        assert abs(activation["town_square"] - 0.64) < 1e-9

    def test_spread_drops_edge_weight_reduces_activation(self) -> None:
        g = _make_graph_with_fixture()
        # goblin --drops(0.5)--> iron_sword
        activation = g._spread_activation(["goblin"], max_depth=1, decay=0.8)
        # 1.0 * 0.8 * 0.5 = 0.4
        assert "iron_sword" in activation
        assert abs(activation["iron_sword"] - 0.4) < 1e-9

    def test_spread_undirected_traversal(self) -> None:
        g = _make_graph_with_fixture()
        # iron_sword has no outgoing edges, but undirected: merchant_tom and goblin reachable
        activation = g._spread_activation(["iron_sword"], max_depth=1, decay=0.8)
        assert "merchant_tom" in activation
        assert "goblin" in activation

    def test_query_spread_empty_keywords_returns_empty(self) -> None:
        g = _make_graph_with_fixture()
        result = asyncio.run(g.query_spread("npc_01", [], context={}))
        assert result == []

    def test_query_spread_no_matching_seeds_returns_empty(self) -> None:
        g = _make_graph_with_fixture()
        result = asyncio.run(g.query_spread("npc_01", ["xyzzy"], context={}))
        assert result == []

    def test_query_spread_returns_hits_with_required_keys(self) -> None:
        g = _make_graph_with_fixture()
        result = asyncio.run(g.query_spread("npc_01", ["merchant tom"], context={}))
        assert len(result) > 0
        hit = result[0]
        for key in ("node_id", "node_type", "label", "tags", "activation", "description", "metadata"):
            assert key in hit, f"missing key: {key}"

    def test_query_spread_sorted_by_activation(self) -> None:
        g = _make_graph_with_fixture()
        result = asyncio.run(g.query_spread("npc_01", ["merchant tom"], context={}))
        activations = [h["activation"] for h in result]
        assert activations == sorted(activations, reverse=True)

    def test_query_spread_excludes_seed_nodes(self) -> None:
        g = _make_graph_with_fixture()
        result = asyncio.run(g.query_spread("npc_01", ["merchant tom"], context={}))
        node_ids = {h["node_id"] for h in result}
        # merchant_tom is the seed; should not appear in hits
        assert "merchant_tom" not in node_ids

    def test_query_spread_top_k_limits_results(self) -> None:
        g = _make_graph_with_fixture()
        result = asyncio.run(
            g.query_spread("npc_01", ["merchant tom"], context={}, top_k=2)
        )
        assert len(result) <= 2

    def test_node_to_hit_format(self) -> None:
        g = _make_graph_with_fixture()
        hit = g._node_to_hit("merchant_tom", 0.75)
        assert hit["node_id"] == "merchant_tom"
        assert hit["node_type"] == "character"
        assert hit["label"] == "Merchant Tom"
        assert "merchant" in hit["tags"]
        assert abs(hit["activation"] - 0.75) < 1e-9
        assert "shrewd" in hit["description"]
        assert isinstance(hit["metadata"], dict)

    def test_ensure_seeded_idempotent(self) -> None:
        g = _make_graph_with_fixture()
        world = build_default_world(
            "idempotent_world",
            world_data={
                "characters": {
                    "hero": {"id": "hero", "name": "Hero"},
                },
            },
        )
        count_before = g.node_count()
        g.ensure_seeded(world)
        count_after_first = g.node_count()
        g.ensure_seeded(world)  # second call — must not add duplicate nodes
        count_after_second = g.node_count()
        assert count_after_first == count_after_second
        assert count_after_first > count_before


# ------------------------------------------------------------------
# TestKnowledgeGraphWithWorld — integration with build_default_world
# ------------------------------------------------------------------


class TestKnowledgeGraphWithWorld:
    def test_seed_from_world_with_characters(self) -> None:
        world = build_default_world(
            "test_kg_world",
            world_data={
                "characters": {
                    "npc_alpha": {"id": "npc_alpha", "name": "Alpha", "tags": ["human"]},
                    "npc_beta": {"id": "npc_beta", "name": "Beta", "tags": ["elf"]},
                },
                "factions": {
                    "traders": {"id": "traders", "name": "Traders Guild"},
                },
                "maps": {
                    "village": {
                        "id": "village",
                        "name": "Village",
                        "connections": ["forest"],
                    },
                    "forest": {
                        "id": "forest",
                        "name": "Dark Forest",
                        "connections": [],
                    },
                },
            },
        )
        g = WorldKnowledgeGraph()
        g.ensure_seeded(world)
        assert g.node_count() > 0
        assert g.has_node("npc_alpha")
        assert g.has_node("npc_beta")
        assert g.has_node("traders")
        assert g.has_node("village")
        assert g.has_node("forest")
        # adjacent_to edge
        assert g.has_edge("village", "forest")

    def test_seed_characters_with_faction_edge(self) -> None:
        world = build_default_world(
            "test_faction_world",
            world_data={
                "characters": {
                    "guard": {"id": "guard", "name": "Guard", "faction_id": "city_guard"},
                },
                "factions": {
                    "city_guard": {"id": "city_guard", "name": "City Guard"},
                },
            },
        )
        g = WorldKnowledgeGraph()
        g.ensure_seeded(world)
        assert g.has_edge("guard", "city_guard")

    def test_seed_monster_drops_edge(self) -> None:
        world = build_default_world(
            "test_monster_world",
            world_data={
                "items": {
                    "goblin_ear": {"id": "goblin_ear", "name": "Goblin Ear"},
                },
                "monsters": {
                    "goblin": {
                        "id": "goblin",
                        "name": "Goblin",
                        "loot_table": [{"item_id": "goblin_ear", "chance": 0.8}],
                    },
                },
            },
        )
        g = WorldKnowledgeGraph()
        g.ensure_seeded(world)
        assert g.has_node("goblin")
        assert g.has_node("goblin_ear")
        assert g.has_edge("goblin", "goblin_ear")
        # Edge weight should reflect drop chance
        weight = g._graph["goblin"]["goblin_ear"]["weight"]
        assert abs(weight - 0.8) < 1e-9

    def test_query_spread_with_world_context(self) -> None:
        world = build_default_world(
            "test_spread_world",
            world_data={
                "characters": {
                    "blacksmith": {
                        "id": "blacksmith",
                        "name": "Blacksmith",
                        "area_id": "forge_district",
                    },
                },
                "maps": {
                    "forge_district": {"id": "forge_district", "name": "Forge District"},
                },
            },
        )
        g = WorldKnowledgeGraph()
        context = {"world": world}
        # Graph is empty; ensure_seeded is called lazily inside query_spread
        result = asyncio.run(g.query_spread("some_npc", ["blacksmith"], context=context))
        # Seed nodes (blacksmith itself) are excluded from hits;
        # forge_district should be activated via located_in edge
        node_ids = {h["node_id"] for h in result}
        assert "forge_district" in node_ids
