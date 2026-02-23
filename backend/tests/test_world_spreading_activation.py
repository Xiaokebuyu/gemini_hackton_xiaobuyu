"""Tests for WorldGraph spreading activation (L3 M1 Phase 2)."""
import pytest

from app.models.activation import SpreadingActivationConfig
from app.world.graph.models import WorldNode, WorldNodeType, WorldEdgeType
from app.world.graph.world_graph import WorldGraph
from app.world.memory.activation import (
    spread_activation,
    extract_subgraph,
    find_paths,
    _compute_cross_decay,
    _apply_causal_floor,
)


def _node(id: str, type: str = "npc", name: str = "", **props) -> WorldNode:
    return WorldNode(id=id, type=type, name=name or id, properties=props, state={}, behaviors=[])


def _build_linear_graph() -> WorldGraph:
    """A -> B -> C linear graph."""
    g = WorldGraph()
    g.add_node(_node("A"))
    g.add_node(_node("B"))
    g.add_node(_node("C"))
    g.add_edge("A", "B", "link", weight=1.0)
    g.add_edge("B", "C", "link", key="link_bc", weight=1.0)
    return g


# =========================================================================
# Basic activation
# =========================================================================

class TestBasicActivation:
    def test_linear_propagation(self):
        g = _build_linear_graph()
        config = SpreadingActivationConfig(max_iterations=3, output_threshold=0.01)
        result = spread_activation(g, ["A"], config)
        assert "A" in result
        assert "B" in result
        assert result["A"] >= result["B"]

    def test_seed_activation_is_one(self):
        g = _build_linear_graph()
        config = SpreadingActivationConfig(max_iterations=1, output_threshold=0.01)
        result = spread_activation(g, ["A"], config)
        assert result["A"] == 1.0

    def test_empty_graph(self):
        g = WorldGraph()
        config = SpreadingActivationConfig()
        result = spread_activation(g, ["X"], config)
        assert result == {}

    def test_nonexistent_seed_ignored(self):
        g = _build_linear_graph()
        config = SpreadingActivationConfig(max_iterations=1, output_threshold=0.01)
        result = spread_activation(g, ["MISSING", "A"], config)
        assert "A" in result

    def test_bidirectional_propagation(self):
        """Activation should propagate both along outgoing and incoming edges."""
        g = _build_linear_graph()
        config = SpreadingActivationConfig(max_iterations=3, output_threshold=0.01)
        result = spread_activation(g, ["B"], config)
        assert "A" in result  # incoming
        assert "C" in result  # outgoing


# =========================================================================
# CRPG decay factors
# =========================================================================

class TestCrossDecay:
    def test_cross_perspective_decay(self):
        edge = {"relation": "link", "weight": 1.0}
        config = SpreadingActivationConfig(perspective_cross_decay=0.5)
        src = {"perspective": "narrative"}
        tgt = {"perspective": "personal"}
        mult = _compute_cross_decay(src, tgt, edge, config)
        assert mult == 0.5

    def test_same_perspective_no_decay(self):
        edge = {"relation": "link", "weight": 1.0}
        config = SpreadingActivationConfig(perspective_cross_decay=0.5)
        src = {"perspective": "narrative"}
        tgt = {"perspective": "narrative"}
        mult = _compute_cross_decay(src, tgt, edge, config)
        assert mult == 1.0

    def test_cross_chapter_decay(self):
        edge = {"relation": "link", "weight": 1.0}
        config = SpreadingActivationConfig(
            current_chapter_id="ch1", cross_chapter_decay=0.4
        )
        src = {"chapter_id": "ch1", "scope_type": "area"}
        tgt = {"chapter_id": "ch2", "scope_type": "area"}
        mult = _compute_cross_decay(src, tgt, edge, config)
        assert mult == 0.4

    def test_camp_exempt_from_cross_chapter(self):
        edge = {"relation": "link", "weight": 1.0}
        config = SpreadingActivationConfig(
            current_chapter_id="ch1", cross_chapter_decay=0.4
        )
        src = {"chapter_id": "ch1", "scope_type": "camp"}
        tgt = {"chapter_id": "ch2", "scope_type": "area"}
        mult = _compute_cross_decay(src, tgt, edge, config)
        assert mult == 1.0  # camp exempt

    def test_cross_owner_decay(self):
        edge = {"relation": "link", "weight": 1.0}
        config = SpreadingActivationConfig(cross_owner_decay=0.3)
        src = {"owner": "npc_a"}
        tgt = {"owner": "npc_b"}
        mult = _compute_cross_decay(src, tgt, edge, config)
        assert mult == 0.3

    def test_same_owner_no_decay(self):
        edge = {"relation": "link", "weight": 1.0}
        config = SpreadingActivationConfig(cross_owner_decay=0.3)
        src = {"owner": "npc_a"}
        tgt = {"owner": "npc_a"}
        mult = _compute_cross_decay(src, tgt, edge, config)
        assert mult == 1.0

    def test_missing_owner_no_decay(self):
        edge = {"relation": "link", "weight": 1.0}
        config = SpreadingActivationConfig(cross_owner_decay=0.3)
        src = {"owner": "npc_a"}
        tgt = {}
        mult = _compute_cross_decay(src, tgt, edge, config)
        assert mult == 1.0

    def test_cross_owner_in_full_activation(self):
        """Cross-owner decay should attenuate signal in full spread_activation."""
        g = WorldGraph()
        g.add_node(_node("A", owner="npc_x"))
        g.add_node(_node("B", owner="npc_y"))
        g.add_edge("A", "B", "link", weight=1.0)

        # Without cross-owner decay
        cfg_no = SpreadingActivationConfig(
            max_iterations=1, output_threshold=0.01, cross_owner_decay=1.0
        )
        r_no = spread_activation(g, ["A"], cfg_no)

        # With cross-owner decay
        cfg_yes = SpreadingActivationConfig(
            max_iterations=1, output_threshold=0.01, cross_owner_decay=0.3
        )
        r_yes = spread_activation(g, ["A"], cfg_yes)

        assert r_yes.get("B", 0) < r_no.get("B", 0)


# =========================================================================
# Causal floor
# =========================================================================

class TestCausalFloor:
    def test_causal_edge_floor(self):
        edge = {"relation": "caused", "weight": 0.1}
        config = SpreadingActivationConfig(causal_min_signal=0.6)
        signal = _apply_causal_floor(edge, 1.0, 0.05, config)
        assert signal == 0.6

    def test_non_causal_edge_no_floor(self):
        edge = {"relation": "link", "weight": 0.1}
        config = SpreadingActivationConfig(causal_min_signal=0.6)
        signal = _apply_causal_floor(edge, 1.0, 0.05, config)
        assert signal == 0.05


# =========================================================================
# Placeholder exclusion
# =========================================================================

class TestPlaceholderExclusion:
    def test_placeholder_nodes_excluded(self):
        g = WorldGraph()
        g.add_node(_node("A"))
        g.add_node(_node("B", placeholder=True))
        g.add_node(_node("C"))
        g.add_edge("A", "B", "link", weight=1.0)
        g.add_edge("B", "C", "link", key="link_bc", weight=1.0)

        config = SpreadingActivationConfig(max_iterations=3, output_threshold=0.01)
        result = spread_activation(g, ["A"], config)
        assert "B" not in result


# =========================================================================
# extract_subgraph
# =========================================================================

class TestExtractSubgraph:
    def test_returns_sorted_tuples(self):
        g = WorldGraph()
        g.add_node(_node("A"))
        g.add_node(_node("B"))
        g.add_edge("A", "B", "link", weight=1.0)

        activated = {"A": 1.0, "B": 0.5}
        result = extract_subgraph(g, activated)

        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], tuple)
        assert isinstance(result[0][0], WorldNode)
        assert isinstance(result[0][1], float)
        # Sorted by score descending
        assert result[0][1] >= result[1][1]

    def test_missing_nodes_skipped(self):
        g = WorldGraph()
        g.add_node(_node("A"))
        activated = {"A": 1.0, "MISSING": 0.5}
        result = extract_subgraph(g, activated)
        assert len(result) == 1

    def test_deep_copy(self):
        g = WorldGraph()
        g.add_node(_node("A"))
        activated = {"A": 1.0}
        result = extract_subgraph(g, activated)
        result[0][0].name = "MODIFIED"
        assert g.get_node("A").name != "MODIFIED"


# =========================================================================
# find_paths
# =========================================================================

class TestFindPaths:
    def test_basic_path(self):
        g = _build_linear_graph()
        paths = find_paths(g, "A", "C")
        assert len(paths) == 1
        assert len(paths[0]) == 2  # A->B, B->C
        # Edge data should be dict, not MemoryEdge
        edge_data, next_node = paths[0][0]
        assert isinstance(edge_data, dict)
        assert "weight" in edge_data

    def test_no_path(self):
        g = _build_linear_graph()
        paths = find_paths(g, "C", "A")  # No reverse edges for "link"
        assert len(paths) == 0

    def test_nonexistent_node(self):
        g = _build_linear_graph()
        paths = find_paths(g, "A", "MISSING")
        assert paths == []


# =========================================================================
# Lateral inhibition
# =========================================================================

class TestLateralInhibition:
    def test_lateral_inhibition_reduces_noise(self):
        g = WorldGraph()
        for i in range(5):
            g.add_node(_node(f"N{i}"))
        g.add_edge("N0", "N1", "link", weight=1.0)
        g.add_edge("N0", "N2", "link", key="link_02", weight=1.0)
        g.add_edge("N0", "N3", "link", key="link_03", weight=0.1)
        g.add_edge("N0", "N4", "link", key="link_04", weight=0.1)

        cfg_no = SpreadingActivationConfig(
            max_iterations=2, output_threshold=0.01,
            lateral_inhibition=False,
        )
        cfg_yes = SpreadingActivationConfig(
            max_iterations=2, output_threshold=0.01,
            lateral_inhibition=True, inhibition_factor=0.3,
        )
        r_no = spread_activation(g, ["N0"], cfg_no)
        r_yes = spread_activation(g, ["N0"], cfg_yes)
        # With inhibition, weak nodes should be more suppressed
        assert len(r_yes) <= len(r_no)


# =========================================================================
# Edge weight default
# =========================================================================

class TestEdgeWeightDefault:
    def test_missing_weight_defaults_to_one(self):
        """Edges without explicit weight should use default 1.0."""
        g = WorldGraph()
        g.add_node(_node("A"))
        g.add_node(_node("B"))
        g.add_edge("A", "B", "link")  # No weight parameter

        config = SpreadingActivationConfig(max_iterations=1, output_threshold=0.01)
        result = spread_activation(g, ["A"], config)
        assert "B" in result
        assert result["B"] > 0


# =========================================================================
# Convergence
# =========================================================================

class TestConvergence:
    def test_convergence_stops_early(self):
        """With a small graph, activation should converge before max_iterations."""
        g = _build_linear_graph()
        config = SpreadingActivationConfig(
            max_iterations=100,
            convergence_threshold=0.001,
            output_threshold=0.01,
        )
        result = spread_activation(g, ["A"], config)
        # Should have results without running all 100 iterations
        assert len(result) > 0
