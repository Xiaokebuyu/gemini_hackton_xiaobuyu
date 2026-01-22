"""
Graph schema validation helpers.
"""
from dataclasses import dataclass
from typing import List, Optional, Set

from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.models.graph_schema import EVENT_REQUIRED_PROPERTIES, GRAPH_NODE_TYPES, GRAPH_RELATIONS


DEFAULT_NODE_TYPES: Set[str] = GRAPH_NODE_TYPES
DEFAULT_RELATIONS: Set[str] = GRAPH_RELATIONS


@dataclass
class GraphSchemaOptions:
    allow_unknown_node_types: bool = True
    allow_unknown_relations: bool = True
    validate_event_properties: bool = False


def validate_node(node: MemoryNode, options: GraphSchemaOptions) -> List[str]:
    errors = []
    if not node.id:
        errors.append("node.id is required")
    if not node.type:
        errors.append("node.type is required")
    if not node.name:
        errors.append("node.name is required")
    if not options.allow_unknown_node_types and node.type not in DEFAULT_NODE_TYPES:
        errors.append(f"unknown node.type: {node.type}")
    if options.validate_event_properties and node.type == "event":
        props = node.properties or {}
        missing = [key for key in EVENT_REQUIRED_PROPERTIES if key not in props]
        if missing:
            errors.append(f"event missing properties: {missing}")
    return errors


def validate_edge(
    edge: MemoryEdge,
    node_ids: Optional[Set[str]],
    options: GraphSchemaOptions,
) -> List[str]:
    errors = []
    if not edge.id:
        errors.append("edge.id is required")
    if not edge.source:
        errors.append("edge.source is required")
    if not edge.target:
        errors.append("edge.target is required")
    if not edge.relation:
        errors.append("edge.relation is required")
    if not options.allow_unknown_relations and edge.relation not in DEFAULT_RELATIONS:
        errors.append(f"unknown edge.relation: {edge.relation}")
    if node_ids is not None:
        if edge.source not in node_ids:
            errors.append(f"edge.source not in graph: {edge.source}")
        if edge.target not in node_ids:
            errors.append(f"edge.target not in graph: {edge.target}")
    return errors


def validate_graph_data(graph: GraphData, options: GraphSchemaOptions) -> List[str]:
    errors: List[str] = []
    node_ids: Set[str] = set()
    edge_ids: Set[str] = set()

    for node in graph.nodes:
        if node.id in node_ids:
            errors.append(f"duplicate node id: {node.id}")
        node_ids.add(node.id)
        errors.extend(validate_node(node, options))

    for edge in graph.edges:
        if edge.id in edge_ids:
            errors.append(f"duplicate edge id: {edge.id}")
        edge_ids.add(edge.id)
        errors.extend(validate_edge(edge, node_ids, options))

    return errors
