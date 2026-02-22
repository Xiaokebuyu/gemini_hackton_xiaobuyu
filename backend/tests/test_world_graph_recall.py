"""Tests for WorldGraphRecallOrchestrator (L3 M2 Phase 4).

Covers: recall, recall_for_role, seed expansion, intent config mapping,
placeholder filtering, disposition-biased weights, empty graph.
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

from app.world.world_graph import WorldGraph
from app.world.models import WorldNode
from app.world.recall import WorldGraphRecallOrchestrator, RECALL_CONFIGS


def _run(coro):
    return asyncio.run(coro)


def _build_test_graph():
    wg = WorldGraph()
    # NPC with memories
    wg.add_node(WorldNode(id="npc_sarah", type="npc", name="Sarah", properties={"owner": "npc_sarah"}))
    wg.add_node(WorldNode(id="player", type="npc", name="Player"))
    wg.add_node(WorldNode(id="eg_1", type="event_group", name="Meeting at tavern", importance=0.8,
                           properties={"owner": "npc_sarah", "summary": "Player met Sarah at tavern"}))
    wg.add_node(WorldNode(id="eg_2", type="event_group", name="Combat in forest", importance=0.6,
                           properties={"owner": "npc_sarah", "summary": "Fought goblins together"}))
    wg.add_node(WorldNode(id="tavern", type="location", name="Tavern"))
    wg.add_node(WorldNode(id="forest", type="location", name="Dark Forest"))
    wg.add_node(WorldNode(id="camp", type="location", name="Camp"))
    # Edges
    wg.add_edge("npc_sarah", "eg_1", "has_memory", key="e1")
    wg.add_edge("npc_sarah", "eg_2", "has_memory", key="e2")
    wg.add_edge("eg_1", "tavern", "located_in", key="e3")
    wg.add_edge("eg_2", "forest", "located_in", key="e4")
    wg.add_edge("eg_1", "player", "participated", key="e5", weight=0.9)
    wg.add_edge("eg_2", "player", "participated", key="e6", weight=0.9)
    return wg


# =========================================================================
# Tests
# =========================================================================


def test_recall_basic():
    """Basic recall with valid seed nodes returns non-empty activated_nodes."""
    wg = _build_test_graph()
    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall(
        character_id="npc_sarah",
        seed_nodes=["npc_sarah"],
    ))
    assert result.activated_nodes, "Expected non-empty activated_nodes"
    assert "npc_sarah" in result.activated_nodes


def test_recall_no_valid_seeds():
    """Seeds that don't match any node produce empty activated_nodes."""
    wg = _build_test_graph()
    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall(
        character_id="npc_sarah",
        seed_nodes=["nonexistent_xyz_123"],
    ))
    assert result.activated_nodes == {}


def test_recall_seed_prefix_expansion():
    """Seed 'tavern' exists directly and should activate nearby nodes."""
    wg = _build_test_graph()
    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall(
        character_id="npc_sarah",
        seed_nodes=["tavern"],
    ))
    assert result.activated_nodes, "Expected non-empty activated_nodes for direct seed"
    assert "tavern" in result.activated_nodes


def test_recall_for_role_npc_prepends_character_id():
    """NPC role should prepend character_id to seeds."""
    wg = _build_test_graph()
    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall_for_role(
        role="npc",
        character_id="npc_sarah",
        seed_nodes=["tavern"],
    ))
    # npc_sarah prepended, so both npc_sarah and tavern are seeds
    assert "npc_sarah" in result.activated_nodes
    assert result.activated_nodes, "Expected non-empty activated_nodes for NPC role"


def test_recall_for_role_teammate_prepends_camp():
    """Teammate role should prepend character_id + 'camp' to seeds."""
    wg = _build_test_graph()
    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall_for_role(
        role="teammate",
        character_id="npc_sarah",
        seed_nodes=["tavern"],
    ))
    # Both npc_sarah, camp, and tavern should be valid seeds
    assert "npc_sarah" in result.activated_nodes
    assert "camp" in result.activated_nodes
    assert result.activated_nodes, "Expected non-empty activated_nodes for teammate role"


def test_recall_intent_config_mapping():
    """Different intent types should produce different SA configs."""
    # Verify exploration uses depth=1
    assert RECALL_CONFIGS["exploration"]["depth"] == 1
    # Verify dialogue uses depth=2
    assert RECALL_CONFIGS["dialogue"]["depth"] == 2
    # Verify recall uses depth=3
    assert RECALL_CONFIGS["recall"]["depth"] == 3

    # Functional test: exploration with depth=1 should still work
    wg = _build_test_graph()
    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall(
        character_id="npc_sarah",
        seed_nodes=["npc_sarah"],
        intent_type="exploration",
    ))
    assert isinstance(result.activated_nodes, dict)


def test_recall_placeholder_filtered():
    """Nodes with properties.placeholder=True should be filtered out."""
    wg = _build_test_graph()
    wg.add_node(WorldNode(
        id="ghost_node", type="npc", name="Ghost",
        properties={"placeholder": True},
    ))
    wg.add_edge("npc_sarah", "ghost_node", "relates_to", key="e_ghost")

    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall(
        character_id="npc_sarah",
        seed_nodes=["npc_sarah"],
    ))
    assert "ghost_node" not in result.activated_nodes, \
        "Placeholder node should be filtered from results"


def test_build_seed_weights_with_disposition():
    """Disposition on NPC node state should bias seed weights."""
    wg = _build_test_graph()
    # Set disposition on npc_sarah towards player
    wg.set_state("npc_sarah", "dispositions", {
        "player": {"approval": 80},  # high approval
    })

    orch = WorldGraphRecallOrchestrator(wg)
    weights = orch._build_seed_weights("npc_sarah", ["player", "tavern"])
    # player should have a biased weight (approval=80 -> bias=(80+100)/200=0.9 -> weight=0.5+0.9*0.5=0.95)
    assert "player" in weights
    assert weights["player"] > 0.5, "High approval should produce above-baseline weight"


def test_recall_empty_graph():
    """Recall on empty graph returns empty activated_nodes."""
    wg = WorldGraph()
    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall(
        character_id="npc_sarah",
        seed_nodes=["anything"],
    ))
    assert result.activated_nodes == {}


def test_recall_subgraph_is_none():
    """WorldGraph version doesn't create subgraphs; result.subgraph should be None."""
    wg = _build_test_graph()
    orch = WorldGraphRecallOrchestrator(wg)
    result = _run(orch.recall(
        character_id="npc_sarah",
        seed_nodes=["npc_sarah"],
    ))
    assert result.subgraph is None
    assert result.used_subgraph is False
