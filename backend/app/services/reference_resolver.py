"""
Reference resolver for graph nodes.
"""
from typing import Optional

from app.models.graph import GraphData, MemoryNode
from app.services.graph_store import GraphStore


class ReferenceResolver:
    """Resolve reference nodes against other graphs."""

    def __init__(self, graph_store: GraphStore) -> None:
        self.graph_store = graph_store

    async def resolve_graph_data(
        self,
        world_id: str,
        graph_data: GraphData,
        default_target_graph: str = "ontology",
    ) -> GraphData:
        for node in graph_data.nodes:
            if not _is_reference_node(node):
                continue
            target_graph = node.properties.get("target_graph", default_target_graph)
            target_id = (
                node.properties.get("target_id")
                or node.properties.get("ref_id")
                or node.properties.get("target")
            )
            if not target_id:
                continue
            target_node = await self.graph_store.get_node(world_id, target_graph, target_id)
            if not target_node:
                continue
            node.properties = dict(node.properties or {})
            node.properties["resolved"] = target_node.model_dump()
        return graph_data


def _is_reference_node(node: MemoryNode) -> bool:
    if node.type and node.type.endswith("_ref"):
        return True
    return bool(node.id and node.id.startswith("ref:"))
