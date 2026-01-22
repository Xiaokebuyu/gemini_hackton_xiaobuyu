"""
Spreading activation and subgraph extraction.
"""
from typing import Dict, Iterable, List, Tuple

from pydantic import BaseModel

from app.models.graph import MemoryEdge, MemoryNode
from app.services.memory_graph import MemoryGraph


class SpreadingActivationConfig(BaseModel):
    """Spreading activation parameters."""
    max_iterations: int = 3
    decay: float = 0.6
    fire_threshold: float = 0.1
    output_threshold: float = 0.15
    hub_threshold: int = 20
    hub_penalty: float = 0.5
    max_activation: float = 1.0
    convergence_threshold: float = 0.01
    lateral_inhibition: bool = False
    inhibition_factor: float = 0.1


def spread_activation(
    graph: MemoryGraph,
    seeds: Iterable[str],
    config: SpreadingActivationConfig,
) -> Dict[str, float]:
    """Run spreading activation."""
    activation = {node_id: 0.0 for node_id in graph.graph.nodes}
    for seed in seeds:
        if graph.has_node(seed):
            activation[seed] = 1.0

    if not activation:
        return {}

    for _ in range(config.max_iterations):
        new_activation = dict(activation)
        for node_id, act in activation.items():
            if act < config.fire_threshold:
                continue
            degree = graph.degree(node_id)
            for neighbor_id, edge in graph.neighbors(node_id):
                signal = act * edge.weight * config.decay
                if degree > config.hub_threshold:
                    signal *= config.hub_penalty
                new_activation[neighbor_id] = min(
                    new_activation.get(neighbor_id, 0.0) + signal,
                    config.max_activation,
                )
        if config.lateral_inhibition:
            new_activation = _apply_lateral_inhibition(
                new_activation,
                config.inhibition_factor,
                config.max_activation,
            )
        if _converged(activation, new_activation, config.convergence_threshold):
            activation = new_activation
            break
        activation = new_activation

    return {node_id: act for node_id, act in activation.items() if act > config.output_threshold}


def extract_subgraph(
    graph: MemoryGraph,
    activated_nodes: Dict[str, float],
) -> MemoryGraph:
    """Extract a subgraph from activation results."""
    subgraph = MemoryGraph()
    for node_id, act in activated_nodes.items():
        node = graph.get_node(node_id)
        if not node:
            continue
        node_copy = MemoryNode(**node.model_dump())
        node_copy.properties = dict(node_copy.properties)
        node_copy.properties["activation"] = act
        subgraph.add_node(node_copy)

    for edge in graph.list_edges():
        if edge.source in activated_nodes and edge.target in activated_nodes:
            subgraph.add_edge(edge)

    return subgraph


def find_paths(
    graph: MemoryGraph,
    source: str,
    target: str,
    max_depth: int = 4,
    limit: int = 5,
) -> List[List[Tuple[MemoryEdge, str]]]:
    """Find connection paths."""
    if not graph.has_node(source) or not graph.has_node(target):
        return []

    paths: List[List[Tuple[MemoryEdge, str]]] = []

    def dfs(current: str, depth: int, path: List[Tuple[MemoryEdge, str]], visited: set) -> None:
        if current == target:
            paths.append(list(path))
            return
        if depth >= max_depth:
            return
        for neighbor_id, edge in graph.neighbors(current):
            if neighbor_id in visited:
                continue
            path.append((edge, neighbor_id))
            visited.add(neighbor_id)
            dfs(neighbor_id, depth + 1, path, visited)
            visited.remove(neighbor_id)
            path.pop()

    dfs(source, 0, [], {source})
    paths.sort(key=lambda p: sum(edge.weight for edge, _ in p), reverse=True)
    return paths[:limit]


def _converged(
    prev_activation: Dict[str, float],
    next_activation: Dict[str, float],
    threshold: float,
) -> bool:
    for node_id, prev_value in prev_activation.items():
        if abs(next_activation.get(node_id, 0.0) - prev_value) > threshold:
            return False
    return True


def _apply_lateral_inhibition(
    activation: Dict[str, float],
    inhibition_factor: float,
    max_activation: float,
) -> Dict[str, float]:
    if not activation:
        return activation
    if inhibition_factor <= 0:
        return activation
    mean_activation = sum(activation.values()) / max(len(activation), 1)
    if mean_activation <= 0:
        return activation
    inhibited = {}
    for node_id, value in activation.items():
        adjusted = value - inhibition_factor * mean_activation
        if adjusted < 0:
            adjusted = 0.0
        if adjusted > max_activation:
            adjusted = max_activation
        inhibited[node_id] = adjusted
    return inhibited
