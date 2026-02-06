"""
Spreading activation and subgraph extraction.
"""
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.models.activation import SpreadingActivationConfig
from app.models.graph import MemoryEdge, MemoryNode
from app.services.memory_graph import MemoryGraph


_REVERSE_DECAY = 0.7


def _get_node_props(graph: MemoryGraph, node_id: str) -> dict:
    """Get node properties without constructing full MemoryNode."""
    if node_id not in graph.graph:
        return {}
    data = graph.graph.nodes[node_id]
    return data.get("properties") or {}


def _compute_cross_decay(
    source_props: dict,
    target_props: dict,
    edge: MemoryEdge,
    config: SpreadingActivationConfig,
) -> float:
    """Compute additional decay multiplier for cross-perspective/cross-chapter edges."""
    multiplier = 1.0

    # Cross-perspective decay (narrative <-> personal)
    src_persp = source_props.get("perspective")
    tgt_persp = target_props.get("perspective")
    if src_persp and tgt_persp and src_persp != tgt_persp:
        multiplier *= config.perspective_cross_decay

    # Cross-chapter decay (skip for camp nodes)
    if config.current_chapter_id:
        src_chapter = source_props.get("chapter_id")
        tgt_chapter = target_props.get("chapter_id")
        src_scope = source_props.get("scope_type")
        tgt_scope = target_props.get("scope_type")
        # Camp nodes are exempt from cross-chapter decay
        if src_scope != "camp" and tgt_scope != "camp":
            if (
                src_chapter
                and tgt_chapter
                and src_chapter != tgt_chapter
            ):
                multiplier *= config.cross_chapter_decay

    return multiplier


def _apply_causal_floor(
    edge: MemoryEdge,
    source_activation: float,
    signal: float,
    config: SpreadingActivationConfig,
) -> float:
    """Enforce minimum signal for causal edges."""
    if edge.relation in ("caused", "led_to", "resulted_from"):
        min_signal = source_activation * config.causal_min_signal
        if signal < min_signal:
            return min_signal
    return signal


def spread_activation(
    graph: MemoryGraph,
    seeds: Iterable[str],
    config: SpreadingActivationConfig,
) -> Dict[str, float]:
    """Run spreading activation (bidirectional, CRPG-aware)."""
    # Exclude placeholder nodes before activation
    placeholder_ids: set = set()
    for node_id in graph.graph.nodes:
        props = _get_node_props(graph, node_id)
        if props.get("placeholder", False):
            placeholder_ids.add(node_id)

    activation = {
        node_id: 0.0
        for node_id in graph.graph.nodes
        if node_id not in placeholder_ids
    }
    for seed in seeds:
        if seed in activation:
            activation[seed] = 1.0

    if not activation:
        return {}

    # Pre-cache node properties for performance
    node_props_cache: Dict[str, dict] = {}
    for node_id in activation:
        node_props_cache[node_id] = _get_node_props(graph, node_id)

    for _ in range(config.max_iterations):
        new_activation = dict(activation)
        for node_id, act in activation.items():
            if act < config.fire_threshold:
                continue
            degree = graph.degree(node_id)
            src_props = node_props_cache.get(node_id, {})

            # Propagate along outgoing edges
            for neighbor_id, edge in graph.neighbors(node_id):
                if neighbor_id in placeholder_ids:
                    continue
                tgt_props = node_props_cache.get(neighbor_id, {})
                cross_mult = _compute_cross_decay(src_props, tgt_props, edge, config)
                signal = act * edge.weight * config.decay * cross_mult
                if degree > config.hub_threshold:
                    signal *= config.hub_penalty
                signal = _apply_causal_floor(edge, act, signal, config)
                new_activation[neighbor_id] = min(
                    new_activation.get(neighbor_id, 0.0) + signal,
                    config.max_activation,
                )

            # Also propagate along incoming edges (reverse direction)
            for source_id, edge in graph.in_neighbors(node_id):
                if source_id in placeholder_ids:
                    continue
                tgt_props = node_props_cache.get(source_id, {})
                cross_mult = _compute_cross_decay(src_props, tgt_props, edge, config)
                signal = act * edge.weight * config.decay * _REVERSE_DECAY * cross_mult
                if degree > config.hub_threshold:
                    signal *= config.hub_penalty
                signal = _apply_causal_floor(edge, act, signal, config)
                new_activation[source_id] = min(
                    new_activation.get(source_id, 0.0) + signal,
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
