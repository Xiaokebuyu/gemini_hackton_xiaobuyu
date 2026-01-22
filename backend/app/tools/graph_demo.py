"""
Memory graph demo script.

Run:
    cd backend
    python -m app.tools.graph_demo
    python -m app.tools.graph_demo --save --world demo_world --graph gm
    python -m app.tools.graph_demo --save --world demo_world --graph character --character gorn
"""
import argparse
import asyncio
from pathlib import Path

from app.config import settings
from app.models.graph import MemoryEdge, MemoryNode
from app.services.graph_store import GraphStore
from app.services.memory_graph import MemoryGraph
from app.services.spreading_activation import (
    SpreadingActivationConfig,
    extract_subgraph,
    find_paths,
    spread_activation,
)


def build_sample_graph() -> MemoryGraph:
    """Build a small demo graph for testing."""
    graph = MemoryGraph()

    nodes = [
        MemoryNode(id="person_gorn", type="person", name="Gorn", importance=0.7),
        MemoryNode(id="person_marcus", type="person", name="Marcus", importance=0.6),
        MemoryNode(id="location_forest", type="location", name="Eastern Forest", importance=0.5),
        MemoryNode(id="event_rescue", type="event", name="Rescue in the Forest", importance=0.9),
    ]
    for node in nodes:
        graph.add_node(node)

    edges = [
        MemoryEdge(
            id="edge_gorn_knows_marcus",
            source="person_gorn",
            target="person_marcus",
            relation="knows",
            weight=0.8,
        ),
        MemoryEdge(
            id="edge_marcus_rescued",
            source="person_marcus",
            target="event_rescue",
            relation="participated",
            weight=0.9,
        ),
        MemoryEdge(
            id="edge_event_location",
            source="event_rescue",
            target="location_forest",
            relation="located_in",
            weight=0.7,
        ),
        MemoryEdge(
            id="edge_gorn_heard_event",
            source="person_gorn",
            target="event_rescue",
            relation="heard_about",
            weight=0.6,
        ),
    ]
    for edge in edges:
        graph.add_edge(edge)

    return graph


def run_demo() -> None:
    """Run the demo locally."""
    graph = build_sample_graph()
    print("== MemoryGraph Demo ==")
    print(f"Nodes: {len(graph.list_nodes())}, Edges: {len(graph.list_edges())}")

    seeds = ["person_gorn"]
    config = SpreadingActivationConfig(max_iterations=2)
    activation = spread_activation(graph, seeds, config)

    print("\nActivated Nodes:")
    for node_id, score in sorted(activation.items(), key=lambda x: x[1], reverse=True):
        node = graph.get_node(node_id)
        name = node.name if node else node_id
        print(f"  {node_id} ({name}) -> {score:.3f}")

    subgraph = extract_subgraph(graph, activation)
    print(f"\nSubgraph Nodes: {len(subgraph.list_nodes())}, Edges: {len(subgraph.list_edges())}")

    paths = find_paths(graph, "person_gorn", "location_forest", max_depth=3)
    print("\nPaths from Gorn to Forest:")
    if not paths:
        print("  (no paths)")
    for path in paths:
        parts = ["person_gorn"]
        weight_sum = 0.0
        for edge, node_id in path:
            parts.append(f"-[{edge.relation}:{edge.weight:.2f}]-> {node_id}")
            weight_sum += edge.weight
        print(f"  {' '.join(parts)} (score={weight_sum:.2f})")


async def save_demo_graph(world_id: str, graph_type: str, character_id: str | None) -> None:
    """Save the demo graph into Firestore."""
    credentials_path = Path(settings.google_application_credentials)
    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Missing credentials: {settings.google_application_credentials}"
        )

    graph = build_sample_graph()
    store = GraphStore()
    await store.save_graph(world_id=world_id, graph_type=graph_type, graph=graph, character_id=character_id)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Memory graph demo")
    parser.add_argument("--save", action="store_true", help="Save demo graph to Firestore")
    parser.add_argument("--world", default="demo_world", help="World ID for Firestore")
    parser.add_argument(
        "--graph",
        default="gm",
        help="Graph type: gm/ontology/character",
    )
    parser.add_argument("--character", default=None, help="Character ID when graph=character")
    args = parser.parse_args()

    run_demo()

    if args.save:
        try:
            asyncio.run(save_demo_graph(args.world, args.graph, args.character))
            print("\nSaved demo graph to Firestore.")
        except Exception as exc:
            print(f"\nSave failed: {exc}")


if __name__ == "__main__":
    main()
