"""Tests for CRPG-specific MemoryGraph indexes and spreading activation features."""
import pytest

from app.models.activation import SpreadingActivationConfig
from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope
from app.services.memory_graph import MemoryGraph
from app.services.spreading_activation import spread_activation


# ---- Helpers ----

def _node(id: str, type: str = "event", name: str = "", **props) -> MemoryNode:
    return MemoryNode(id=id, type=type, name=name or id, properties=props)


def _edge(id: str, source: str, target: str, relation: str = "knows", weight: float = 1.0, **props) -> MemoryEdge:
    return MemoryEdge(id=id, source=source, target=target, relation=relation, weight=weight, properties=props)


# ---- MemoryGraph CRPG Index Tests ----


class TestChapterIndex:
    def test_find_by_chapter(self):
        g = MemoryGraph()
        g.add_node(_node("e1", chapter_id="ch1"))
        g.add_node(_node("e2", chapter_id="ch2"))
        g.add_node(_node("e3", chapter_id="ch1"))

        ch1_nodes = g.find_nodes_by_chapter("ch1")
        assert len(ch1_nodes) == 2
        assert {n.id for n in ch1_nodes} == {"e1", "e3"}

    def test_empty_chapter(self):
        g = MemoryGraph()
        g.add_node(_node("e1", chapter_id="ch1"))
        assert g.find_nodes_by_chapter("ch_missing") == []


class TestAreaIndex:
    def test_find_by_area(self):
        g = MemoryGraph()
        g.add_node(_node("e1", area_id="frontier"))
        g.add_node(_node("e2", area_id="capital"))
        result = g.find_nodes_by_area("frontier")
        assert len(result) == 1
        assert result[0].id == "e1"


class TestLocationIndex:
    def test_find_by_location(self):
        g = MemoryGraph()
        g.add_node(_node("e1", location_id="tavern"))
        g.add_node(_node("e2", location_id="guild"))
        result = g.find_nodes_by_location("tavern")
        assert len(result) == 1
        assert result[0].id == "e1"


class TestDayIndex:
    def test_find_by_day(self):
        g = MemoryGraph()
        g.add_node(_node("e1", day=3))
        g.add_node(_node("e2", day=5))
        g.add_node(_node("e3", game_day=3))  # alternative key

        result = g.find_nodes_by_day(3)
        assert len(result) == 2
        assert {n.id for n in result} == {"e1", "e3"}

    def test_day_zero(self):
        g = MemoryGraph()
        g.add_node(_node("e1", day=0))
        # day=0 is a valid day, should be indexed
        result = g.find_nodes_by_day(0)
        assert len(result) == 1


class TestParticipantIndex:
    def test_find_by_participant(self):
        g = MemoryGraph()
        g.add_node(_node("e1", participants=["goblin_slayer", "priestess"]))
        g.add_node(_node("e2", participants=["priestess", "elf"]))

        result = g.find_nodes_by_participant("priestess")
        assert len(result) == 2

        result = g.find_nodes_by_participant("goblin_slayer")
        assert len(result) == 1
        assert result[0].id == "e1"

    def test_no_participants(self):
        g = MemoryGraph()
        g.add_node(_node("e1"))  # no participants
        assert g.find_nodes_by_participant("anyone") == []


class TestPerspectiveQuery:
    def test_find_by_perspective(self):
        g = MemoryGraph()
        g.add_node(_node("e1", perspective="narrative"))
        g.add_node(_node("e2", perspective="personal"))
        g.add_node(_node("e3", perspective="narrative"))

        narrative = g.find_nodes_by_perspective("narrative")
        assert len(narrative) == 2
        assert {n.id for n in narrative} == {"e1", "e3"}

        personal = g.find_nodes_by_perspective("personal")
        assert len(personal) == 1
        assert personal[0].id == "e2"


class TestDeindexNode:
    def test_update_node_reindexes(self):
        g = MemoryGraph()
        g.add_node(_node("e1", chapter_id="ch1", day=3))
        assert len(g.find_nodes_by_chapter("ch1")) == 1
        assert len(g.find_nodes_by_day(3)) == 1

        # Update node with different chapter
        g.add_node(_node("e1", chapter_id="ch2", day=5))
        assert g.find_nodes_by_chapter("ch1") == []
        assert len(g.find_nodes_by_chapter("ch2")) == 1
        assert g.find_nodes_by_day(3) == []
        assert len(g.find_nodes_by_day(5)) == 1

    def test_remove_node_deindexes(self):
        g = MemoryGraph()
        g.add_node(_node("e1", chapter_id="ch1", participants=["hero"]))
        assert len(g.find_nodes_by_chapter("ch1")) == 1
        assert len(g.find_nodes_by_participant("hero")) == 1

        g.remove_node("e1")
        assert g.find_nodes_by_chapter("ch1") == []
        assert g.find_nodes_by_participant("hero") == []


class TestRebuildIndexes:
    def test_rebuild_includes_crpg_indexes(self):
        g = MemoryGraph()
        g.add_node(_node("e1", chapter_id="ch1", area_id="a1", day=3))
        # Manually clear indexes
        g._chapter_index = {}
        g._area_index = {}
        g._day_index = {}
        # Rebuild
        g.rebuild_indexes()
        assert len(g.find_nodes_by_chapter("ch1")) == 1
        assert len(g.find_nodes_by_area("a1")) == 1
        assert len(g.find_nodes_by_day(3)) == 1


class TestFromMultiScopeMemoryGraph:
    """from_multi_scope with List[MemoryGraph] input."""

    def test_merge_two_graphs(self):
        g1 = MemoryGraph()
        g1.add_node(_node("a", type="person"))
        g1.add_node(_node("b", type="location"))
        g1.add_edge(_edge("e1", "a", "b", "located_in"))

        g2 = MemoryGraph()
        g2.add_node(_node("c", type="event", chapter_id="ch1"))
        g2.add_node(_node("a", type="person"))  # duplicate node
        g2.add_edge(_edge("e2", "a", "c", "participated"))

        merged = MemoryGraph.from_multi_scope([g1, g2])
        assert merged.has_node("a")
        assert merged.has_node("b")
        assert merged.has_node("c")
        assert merged.has_edge("e1")
        assert merged.has_edge("e2")
        assert len(merged.list_nodes()) == 3

    def test_merge_empty_graph(self):
        g1 = MemoryGraph()
        g1.add_node(_node("a"))
        g2 = MemoryGraph()

        merged = MemoryGraph.from_multi_scope([g1, g2])
        assert len(merged.list_nodes()) == 1

    def test_merge_preserves_indexes(self):
        g1 = MemoryGraph()
        g1.add_node(_node("e1", chapter_id="ch1"))

        g2 = MemoryGraph()
        g2.add_node(_node("e2", chapter_id="ch2"))

        merged = MemoryGraph.from_multi_scope([g1, g2])
        assert len(merged.find_nodes_by_chapter("ch1")) == 1
        assert len(merged.find_nodes_by_chapter("ch2")) == 1

    def test_merge_skips_dangling_edges(self):
        """Edges whose source/target nodes weren't merged should be skipped."""
        g1 = MemoryGraph()
        g1.add_node(_node("a"))
        g1.add_node(_node("b"))
        g1.add_edge(_edge("e1", "a", "b"))

        g2 = MemoryGraph()
        g2.add_node(_node("c"))
        g2.add_node(_node("d"))
        g2.add_edge(_edge("e2", "c", "d"))
        g2.add_node(_node("a"))  # need a in g2 to create edge
        g2.add_edge(_edge("e3", "c", "a"))

        merged = MemoryGraph.from_multi_scope([g1, g2])
        assert merged.has_edge("e1")
        assert merged.has_edge("e2")
        assert merged.has_edge("e3")


class TestFromMultiScopeScoped:
    """from_multi_scope with List[Tuple[GraphScope, GraphData]] input."""

    def test_scope_injection(self):
        """GraphScope attributes are injected into node properties."""
        gd = GraphData(
            nodes=[_node("n1", type="event"), _node("n2", type="person")],
            edges=[],
        )
        scope = GraphScope.area("ch1", "frontier")

        merged = MemoryGraph.from_multi_scope([(scope, gd)])
        n1 = merged.get_node("n1")
        assert n1.properties["scope_type"] == "area"
        assert n1.properties["chapter_id"] == "ch1"
        assert n1.properties["area_id"] == "frontier"

    def test_scope_injection_indexes(self):
        """Injected scope attributes are indexed."""
        gd = GraphData(
            nodes=[_node("n1", type="event")],
            edges=[],
        )
        scope = GraphScope.chapter("ch1")

        merged = MemoryGraph.from_multi_scope([(scope, gd)])
        assert len(merged.find_nodes_by_chapter("ch1")) == 1

    def test_multi_scope_merge(self):
        """Multiple scoped graphs merge correctly."""
        gd1 = GraphData(
            nodes=[_node("a", type="person"), _node("b", type="location")],
            edges=[_edge("e1", "a", "b", "located_in")],
        )
        gd2 = GraphData(
            nodes=[_node("c", type="event")],
            edges=[],
        )

        merged = MemoryGraph.from_multi_scope([
            (GraphScope.area("ch1", "frontier"), gd1),
            (GraphScope.chapter("ch1"), gd2),
        ])
        assert merged.has_node("a")
        assert merged.has_node("b")
        assert merged.has_node("c")
        assert merged.has_edge("e1")
        # a and b have area scope, c has chapter scope
        a = merged.get_node("a")
        assert a.properties["scope_type"] == "area"
        c = merged.get_node("c")
        assert c.properties["scope_type"] == "chapter"

    def test_scope_injection_preserves_existing_props(self):
        """Existing node properties are preserved, scope attrs are added."""
        gd = GraphData(
            nodes=[_node("n1", type="event", day=5, custom="value")],
            edges=[],
        )
        scope = GraphScope.character("goblin_slayer")

        merged = MemoryGraph.from_multi_scope([(scope, gd)])
        n1 = merged.get_node("n1")
        assert n1.properties["day"] == 5
        assert n1.properties["custom"] == "value"
        assert n1.properties["scope_type"] == "character"
        assert n1.properties["character_id"] == "goblin_slayer"

    def test_camp_scope_injection(self):
        """Camp scope injects scope_type=camp."""
        gd = GraphData(
            nodes=[_node("camp_event", type="event")],
            edges=[],
        )
        merged = MemoryGraph.from_multi_scope([(GraphScope.camp(), gd)])
        n = merged.get_node("camp_event")
        assert n.properties["scope_type"] == "camp"

    def test_empty_scoped_data(self):
        merged = MemoryGraph.from_multi_scope([])
        assert len(merged.list_nodes()) == 0


# ---- SpreadingActivationConfig CRPG Parameters ----


class TestConfigCRPGDefaults:
    def test_default_values(self):
        config = SpreadingActivationConfig()
        assert config.perspective_cross_decay == 0.5
        assert config.cross_chapter_decay == 0.4
        assert config.causal_min_signal == 0.6
        assert config.current_chapter_id is None

    def test_custom_values(self):
        config = SpreadingActivationConfig(
            perspective_cross_decay=0.3,
            cross_chapter_decay=0.2,
            causal_min_signal=0.8,
            current_chapter_id="ch1",
        )
        assert config.perspective_cross_decay == 0.3
        assert config.current_chapter_id == "ch1"


# ---- CRPG Spreading Activation Behavior ----


class TestCrossPerspectiveDecay:
    def test_cross_perspective_reduces_signal(self):
        """Signal crossing perspective boundary should be weaker."""
        g = MemoryGraph()
        g.add_node(_node("a", perspective="narrative"))
        g.add_node(_node("b", perspective="personal"))
        g.add_edge(_edge("e1", "a", "b", weight=1.0))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=1.0,
            fire_threshold=0.01,
            output_threshold=0.01,
            perspective_cross_decay=0.5,
        )

        result = spread_activation(g, ["a"], config)
        # b should receive signal * 0.5 (cross perspective)
        assert "b" in result
        assert result["b"] < result["a"]

    def test_same_perspective_no_decay(self):
        """Signal within same perspective should not have extra decay."""
        g = MemoryGraph()
        g.add_node(_node("a", perspective="narrative"))
        g.add_node(_node("b", perspective="narrative"))
        g.add_edge(_edge("e1", "a", "b", weight=1.0))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=1.0,
            fire_threshold=0.01,
            output_threshold=0.01,
            perspective_cross_decay=0.5,
        )

        result = spread_activation(g, ["a"], config)
        # b should get full signal (no cross-perspective penalty)
        assert result.get("b", 0) == 1.0  # 1.0 * 1.0 * 1.0 * 1.0 (no cross decay)


class TestCrossChapterDecay:
    def test_cross_chapter_reduces_signal(self):
        """Signal crossing chapter boundary should be weaker."""
        g = MemoryGraph()
        g.add_node(_node("a", chapter_id="ch1"))
        g.add_node(_node("b", chapter_id="ch2"))
        g.add_edge(_edge("e1", "a", "b", weight=1.0))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=1.0,
            fire_threshold=0.01,
            output_threshold=0.01,
            cross_chapter_decay=0.4,
            current_chapter_id="ch1",
        )

        result = spread_activation(g, ["a"], config)
        assert "b" in result
        assert result["b"] == pytest.approx(0.4, abs=0.01)

    def test_same_chapter_no_decay(self):
        g = MemoryGraph()
        g.add_node(_node("a", chapter_id="ch1"))
        g.add_node(_node("b", chapter_id="ch1"))
        g.add_edge(_edge("e1", "a", "b", weight=1.0))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=1.0,
            fire_threshold=0.01,
            output_threshold=0.01,
            cross_chapter_decay=0.4,
            current_chapter_id="ch1",
        )

        result = spread_activation(g, ["a"], config)
        assert result.get("b", 0) == 1.0

    def test_no_current_chapter_skips_decay(self):
        """If current_chapter_id is None, cross-chapter decay is not applied."""
        g = MemoryGraph()
        g.add_node(_node("a", chapter_id="ch1"))
        g.add_node(_node("b", chapter_id="ch2"))
        g.add_edge(_edge("e1", "a", "b", weight=1.0))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=1.0,
            fire_threshold=0.01,
            output_threshold=0.01,
            cross_chapter_decay=0.4,
            current_chapter_id=None,
        )

        result = spread_activation(g, ["a"], config)
        assert result.get("b", 0) == 1.0


class TestCampExemption:
    def test_camp_nodes_exempt_from_cross_chapter(self):
        """Camp nodes should not suffer cross-chapter decay."""
        g = MemoryGraph()
        g.add_node(_node("a", chapter_id="ch1"))
        g.add_node(_node("camp_event", scope_type="camp"))
        g.add_edge(_edge("e1", "a", "camp_event", weight=1.0))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=1.0,
            fire_threshold=0.01,
            output_threshold=0.01,
            cross_chapter_decay=0.4,
            current_chapter_id="ch1",
        )

        result = spread_activation(g, ["a"], config)
        # Camp node should get full signal (no cross-chapter decay)
        assert result.get("camp_event", 0) == 1.0


class TestCausalMinSignal:
    def test_caused_edge_floor(self):
        """Causal edges should enforce minimum signal."""
        g = MemoryGraph()
        g.add_node(_node("choice"))
        g.add_node(_node("consequence"))
        g.add_edge(_edge("e1", "choice", "consequence", relation="caused", weight=0.1))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=0.3,  # very low decay
            fire_threshold=0.01,
            output_threshold=0.01,
            causal_min_signal=0.6,
        )

        result = spread_activation(g, ["choice"], config)
        # Without floor: 1.0 * 0.1 * 0.3 = 0.03
        # With floor: max(0.03, 1.0 * 0.6) = 0.6
        assert result.get("consequence", 0) >= 0.6

    def test_led_to_also_has_floor(self):
        g = MemoryGraph()
        g.add_node(_node("a"))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b", relation="led_to", weight=0.1))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=0.3,
            fire_threshold=0.01,
            output_threshold=0.01,
            causal_min_signal=0.6,
        )

        result = spread_activation(g, ["a"], config)
        assert result.get("b", 0) >= 0.6

    def test_normal_edge_no_floor(self):
        """Non-causal edges should not have a floor."""
        g = MemoryGraph()
        g.add_node(_node("a"))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b", relation="knows", weight=0.1))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=0.3,
            fire_threshold=0.01,
            output_threshold=0.01,
            causal_min_signal=0.6,
        )

        result = spread_activation(g, ["a"], config)
        # 1.0 * 0.1 * 0.3 = 0.03
        assert result.get("b", 0) == pytest.approx(0.03, abs=0.005)


class TestCombinedDecays:
    def test_cross_perspective_and_cross_chapter_stack(self):
        """Both decays should multiply."""
        g = MemoryGraph()
        g.add_node(_node("a", perspective="narrative", chapter_id="ch1"))
        g.add_node(_node("b", perspective="personal", chapter_id="ch2"))
        g.add_edge(_edge("e1", "a", "b", weight=1.0))

        config = SpreadingActivationConfig(
            max_iterations=1,
            decay=1.0,
            fire_threshold=0.01,
            output_threshold=0.01,
            perspective_cross_decay=0.5,
            cross_chapter_decay=0.4,
            current_chapter_id="ch1",
        )

        result = spread_activation(g, ["a"], config)
        # Signal = 1.0 * 1.0 * 1.0 * 0.5 * 0.4 = 0.2
        assert result.get("b", 0) == pytest.approx(0.2, abs=0.01)
