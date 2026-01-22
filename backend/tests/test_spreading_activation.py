import pytest

from app.models.graph import MemoryEdge, MemoryNode
from app.services.memory_graph import MemoryGraph
from app.services.spreading_activation import (
    SpreadingActivationConfig,
    extract_subgraph,
    find_paths,
    spread_activation,
)


def _build_linear_graph() -> MemoryGraph:
    graph = MemoryGraph()
    for node_id in ["A", "B", "C"]:
        graph.add_node(MemoryNode(id=node_id, type="test", name=node_id))
    graph.add_edge(
        MemoryEdge(
            id="edge_ab",
            source="A",
            target="B",
            relation="link",
            weight=1.0,
        )
    )
    graph.add_edge(
        MemoryEdge(
            id="edge_bc",
            source="B",
            target="C",
            relation="link",
            weight=1.0,
        )
    )
    return graph


def test_spread_activation_single_step():
    graph = MemoryGraph()
    graph.add_node(MemoryNode(id="A", type="test", name="A"))
    graph.add_node(MemoryNode(id="B", type="test", name="B"))
    graph.add_node(MemoryNode(id="C", type="test", name="C"))
    graph.add_edge(
        MemoryEdge(
            id="edge_ab",
            source="A",
            target="B",
            relation="link",
            weight=0.5,
        )
    )
    graph.add_edge(
        MemoryEdge(
            id="edge_ac",
            source="A",
            target="C",
            relation="link",
            weight=0.1,
        )
    )

    config = SpreadingActivationConfig(max_iterations=1, decay=0.6, output_threshold=0.15)
    activation = spread_activation(graph, ["A"], config)

    assert set(activation.keys()) == {"A", "B"}
    assert activation["B"] == pytest.approx(0.3, rel=1e-3)


def test_spread_activation_multi_hop():
    graph = _build_linear_graph()
    config = SpreadingActivationConfig(max_iterations=2, decay=0.5, output_threshold=0.1)
    activation = spread_activation(graph, ["A"], config)

    assert activation["A"] == pytest.approx(1.0, rel=1e-3)
    assert activation["B"] == pytest.approx(1.0, rel=1e-3)
    assert activation["C"] == pytest.approx(0.25, rel=1e-3)


def test_extract_subgraph_includes_activation():
    graph = _build_linear_graph()
    config = SpreadingActivationConfig(max_iterations=1, decay=0.5, output_threshold=0.1)
    activation = spread_activation(graph, ["A"], config)
    subgraph = extract_subgraph(graph, activation)

    node = subgraph.get_node("A")
    assert node is not None
    assert node.properties["activation"] == pytest.approx(1.0, rel=1e-3)


def test_find_paths_orders_by_weight():
    graph = MemoryGraph()
    for node_id in ["A", "B", "C"]:
        graph.add_node(MemoryNode(id=node_id, type="test", name=node_id))

    graph.add_edge(
        MemoryEdge(
            id="edge_ab",
            source="A",
            target="B",
            relation="link",
            weight=0.9,
        )
    )
    graph.add_edge(
        MemoryEdge(
            id="edge_ac",
            source="A",
            target="C",
            relation="link",
            weight=0.6,
        )
    )
    graph.add_edge(
        MemoryEdge(
            id="edge_cb",
            source="C",
            target="B",
            relation="link",
            weight=0.6,
        )
    )

    paths = find_paths(graph, "A", "B", max_depth=3)
    assert len(paths) >= 2
    first_score = sum(edge.weight for edge, _ in paths[0])
    second_score = sum(edge.weight for edge, _ in paths[1])
    assert first_score >= second_score


def test_find_nodes_by_name_and_type():
    graph = MemoryGraph()
    graph.add_node(MemoryNode(id="n1", type="person", name="Marcus"))
    graph.add_node(MemoryNode(id="n2", type="location", name="Forest"))

    by_type = graph.find_nodes_by_type("person")
    by_name = graph.find_nodes_by_name("marcus")

    assert len(by_type) == 1
    assert by_type[0].id == "n1"
    assert len(by_name) == 1
    assert by_name[0].id == "n1"
