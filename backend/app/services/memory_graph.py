"""
In-memory graph structure and operations.
"""
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import networkx as nx

from app.models.graph import GraphData, MemoryEdge, MemoryNode


class MemoryGraph:
    """NetworkX-backed graph container."""

    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()
        self._edge_index: Dict[str, Tuple[str, str, str]] = {}
        self._type_index: Dict[str, set] = {}
        self._name_index: Dict[str, set] = {}

    def add_node(self, node: MemoryNode) -> None:
        """Add or update a node."""
        if node.id in self.graph:
            self._deindex_node(node.id)
        node_data = node.model_dump()
        self.graph.add_node(node.id, **node_data)
        self._index_node(node.id, node_data)

    def add_edge(self, edge: MemoryEdge) -> None:
        """Add or update an edge (requires existing nodes)."""
        if edge.source not in self.graph or edge.target not in self.graph:
            raise ValueError("edge source/target must exist before adding edge")
        edge_data = edge.model_dump()
        self.graph.add_edge(edge.source, edge.target, key=edge.id, **edge_data)
        self._edge_index[edge.id] = (edge.source, edge.target, edge.id)

    def get_node(self, node_id: str) -> Optional[MemoryNode]:
        """Fetch a node."""
        if node_id not in self.graph:
            return None
        data = dict(self.graph.nodes[node_id])
        return MemoryNode(**data)

    def get_edge(self, edge_id: str) -> Optional[MemoryEdge]:
        """Fetch an edge."""
        edge_key = self._edge_index.get(edge_id)
        if not edge_key:
            return None
        source, target, key = edge_key
        data = self.graph.get_edge_data(source, target, key)
        if not data:
            return None
        return MemoryEdge(**data)

    def update_node(self, node_id: str, **updates) -> Optional[MemoryNode]:
        """Update node attributes."""
        if node_id not in self.graph:
            return None
        self._deindex_node(node_id)
        updates["updated_at"] = datetime.now()
        self.graph.nodes[node_id].update(updates)
        self._index_node(node_id, dict(self.graph.nodes[node_id]))
        return self.get_node(node_id)

    def update_edge(self, edge_id: str, **updates) -> Optional[MemoryEdge]:
        """Update edge attributes."""
        edge_key = self._edge_index.get(edge_id)
        if not edge_key:
            return None
        source, target, key = edge_key
        edge_data = self.graph.get_edge_data(source, target, key)
        if edge_data is None:
            return None
        updates["updated_at"] = datetime.now()
        edge_data.update(updates)
        return MemoryEdge(**edge_data)

    def remove_node(self, node_id: str) -> None:
        """Remove a node and its edges."""
        if node_id not in self.graph:
            return
        self._deindex_node(node_id)
        self.graph.remove_node(node_id)
        self._edge_index = {
            edge_id: key for edge_id, key in self._edge_index.items()
            if key[0] != node_id and key[1] != node_id
        }

    def remove_edge(self, edge_id: str) -> None:
        """Remove an edge."""
        edge_key = self._edge_index.pop(edge_id, None)
        if not edge_key:
            return
        source, target, key = edge_key
        if self.graph.has_edge(source, target, key):
            self.graph.remove_edge(source, target, key)

    def has_node(self, node_id: str) -> bool:
        """Check node existence."""
        return node_id in self.graph

    def has_edge(self, edge_id: str) -> bool:
        """Check edge existence."""
        return edge_id in self._edge_index

    def list_nodes(self) -> List[MemoryNode]:
        """List all nodes."""
        nodes = []
        for node_id in self.graph.nodes:
            data = dict(self.graph.nodes[node_id])
            nodes.append(MemoryNode(**data))
        return nodes

    def find_nodes_by_type(self, node_type: str) -> List[MemoryNode]:
        """Find nodes by type (case-sensitive)."""
        node_ids = self._type_index.get(node_type, set())
        return [self.get_node(node_id) for node_id in node_ids if self.get_node(node_id)]

    def find_nodes_by_name(self, name: str) -> List[MemoryNode]:
        """Find nodes by name (case-insensitive exact match)."""
        node_ids = self._name_index.get(name.lower(), set())
        return [self.get_node(node_id) for node_id in node_ids if self.get_node(node_id)]

    def list_edges(self) -> List[MemoryEdge]:
        """List all edges."""
        edges = []
        for source, target, key, data in self.graph.edges(keys=True, data=True):
            edges.append(MemoryEdge(**data))
            if key not in self._edge_index:
                self._edge_index[key] = (source, target, key)
        return edges

    def neighbors(self, node_id: str) -> List[Tuple[str, MemoryEdge]]:
        """Return outgoing neighbors."""
        if node_id not in self.graph:
            return []
        neighbors: List[Tuple[str, MemoryEdge]] = []
        for _, target, key, data in self.graph.out_edges(node_id, keys=True, data=True):
            neighbors.append((target, MemoryEdge(**data)))
            if key not in self._edge_index:
                self._edge_index[key] = (node_id, target, key)
        return neighbors

    def degree(self, node_id: str) -> int:
        """Return node degree."""
        if node_id not in self.graph:
            return 0
        return int(self.graph.degree(node_id))

    def expand_nodes(
        self,
        seeds: Iterable[str],
        depth: int = 1,
        direction: str = "both",
    ) -> set:
        """Expand from seeds by hop depth."""
        if depth < 0:
            return set()
        direction = direction.lower()
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be one of: out, in, both")

        visited = {node_id for node_id in seeds if node_id in self.graph}
        frontier = set(visited)
        for _ in range(depth):
            next_frontier = set()
            for node_id in frontier:
                if direction in {"out", "both"}:
                    for _, target in self.graph.out_edges(node_id):
                        next_frontier.add(target)
                if direction in {"in", "both"}:
                    for source, _ in self.graph.in_edges(node_id):
                        next_frontier.add(source)
            next_frontier -= visited
            if not next_frontier:
                break
            visited |= next_frontier
            frontier = next_frontier
        return visited

    def to_graph_data(self) -> GraphData:
        """Serialize to GraphData."""
        return GraphData(nodes=self.list_nodes(), edges=self.list_edges())

    def subgraph(self, node_ids: Iterable[str]) -> "MemoryGraph":
        """Extract a subgraph by node ids."""
        sub = MemoryGraph()
        node_set = set(node_ids)
        for node_id in node_set:
            node = self.get_node(node_id)
            if node:
                sub.add_node(node)
        for source, target, key, data in self.graph.edges(keys=True, data=True):
            if source in node_set and target in node_set:
                sub.add_edge(MemoryEdge(**data))
        return sub

    def rebuild_indexes(self) -> None:
        """Rebuild in-memory indexes from current graph."""
        self._type_index = {}
        self._name_index = {}
        for node_id, data in self.graph.nodes(data=True):
            self._index_node(node_id, dict(data))

    @classmethod
    def from_graph_data(cls, graph_data: GraphData) -> "MemoryGraph":
        """Build MemoryGraph from GraphData."""
        graph = cls()
        for node in graph_data.nodes:
            graph.add_node(node)
        for edge in graph_data.edges:
            graph.add_edge(edge)
        return graph

    def _index_node(self, node_id: str, data: Dict) -> None:
        node_type = data.get("type")
        if node_type:
            self._type_index.setdefault(node_type, set()).add(node_id)
        name = data.get("name")
        if name:
            self._name_index.setdefault(name.lower(), set()).add(node_id)

    def _deindex_node(self, node_id: str) -> None:
        if node_id not in self.graph:
            return
        data = dict(self.graph.nodes[node_id])
        node_type = data.get("type")
        if node_type and node_id in self._type_index.get(node_type, set()):
            self._type_index[node_type].discard(node_id)
            if not self._type_index[node_type]:
                self._type_index.pop(node_type, None)
        name = data.get("name")
        if name:
            key = name.lower()
            if node_id in self._name_index.get(key, set()):
                self._name_index[key].discard(node_id)
                if not self._name_index[key]:
                    self._name_index.pop(key, None)
