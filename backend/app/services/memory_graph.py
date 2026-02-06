"""
In-memory graph structure and operations.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple, Union

import networkx as nx

from app.models.graph import GraphData, MemoryEdge, MemoryNode

if TYPE_CHECKING:
    from app.models.graph_scope import GraphScope


class MemoryGraph:
    """NetworkX-backed graph container."""

    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()
        self._edge_index: Dict[str, Tuple[str, str, str]] = {}
        self._type_index: Dict[str, set] = {}
        self._name_index: Dict[str, set] = {}
        # CRPG scope indexes (populated from node properties)
        self._chapter_index: Dict[str, set] = {}
        self._area_index: Dict[str, set] = {}
        self._location_index: Dict[str, set] = {}
        self._day_index: Dict[int, set] = {}
        self._participant_index: Dict[str, set] = {}

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

    def in_neighbors(self, node_id: str) -> List[Tuple[str, MemoryEdge]]:
        """Return incoming neighbors (sources of edges pointing to this node)."""
        if node_id not in self.graph:
            return []
        result: List[Tuple[str, MemoryEdge]] = []
        for source, _, key, data in self.graph.in_edges(node_id, keys=True, data=True):
            result.append((source, MemoryEdge(**data)))
            if key not in self._edge_index:
                self._edge_index[key] = (source, node_id, key)
        return result

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
        self._chapter_index = {}
        self._area_index = {}
        self._location_index = {}
        self._day_index = {}
        self._participant_index = {}
        for node_id, data in self.graph.nodes(data=True):
            self._index_node(node_id, dict(data))

    def find_nodes_by_chapter(self, chapter_id: str) -> List[MemoryNode]:
        """Find nodes belonging to a specific chapter."""
        node_ids = self._chapter_index.get(chapter_id, set())
        return [n for nid in node_ids if (n := self.get_node(nid))]

    def find_nodes_by_area(self, area_id: str) -> List[MemoryNode]:
        """Find nodes belonging to a specific area."""
        node_ids = self._area_index.get(area_id, set())
        return [n for nid in node_ids if (n := self.get_node(nid))]

    def find_nodes_by_location(self, location_id: str) -> List[MemoryNode]:
        """Find nodes belonging to a specific location."""
        node_ids = self._location_index.get(location_id, set())
        return [n for nid in node_ids if (n := self.get_node(nid))]

    def find_nodes_by_day(self, day: int) -> List[MemoryNode]:
        """Find nodes that occurred on a specific game day."""
        node_ids = self._day_index.get(day, set())
        return [n for nid in node_ids if (n := self.get_node(nid))]

    def find_nodes_by_participant(self, participant_id: str) -> List[MemoryNode]:
        """Find nodes involving a specific participant."""
        node_ids = self._participant_index.get(participant_id, set())
        return [n for nid in node_ids if (n := self.get_node(nid))]

    def find_nodes_by_perspective(self, perspective: str) -> List[MemoryNode]:
        """Find nodes by perspective ('narrative' or 'personal')."""
        results = []
        for node_id in self.graph.nodes:
            node = self.get_node(node_id)
            if not node:
                continue
            if (node.properties or {}).get("perspective") == perspective:
                results.append(node)
        return results

    @classmethod
    def from_multi_scope(
        cls,
        scoped_data: Union[
            List[Tuple[GraphScope, GraphData]],
            List[MemoryGraph],
        ],
    ) -> MemoryGraph:
        """Merge multiple graphs into one unified MemoryGraph.

        Accepts either:
        - List[Tuple[GraphScope, GraphData]]: auto-injects scope attributes
          (scope_type, chapter_id, area_id, location_id, character_id) into
          each node's properties before merging.
        - List[MemoryGraph]: merges as-is (no scope injection).

        Nodes with duplicate IDs: later entries overwrite earlier ones.
        Edges are accumulated (MultiDiGraph allows parallel edges).
        """
        from app.models.graph_scope import GraphScope as _GraphScope

        merged = cls()
        pending_edges: List[MemoryEdge] = []

        for item in scoped_data:
            if isinstance(item, tuple) and len(item) == 2:
                scope, graph_data = item
                # Inject scope attributes into node properties
                scope_attrs = {"scope_type": scope.scope_type}
                if scope.chapter_id:
                    scope_attrs["chapter_id"] = scope.chapter_id
                if scope.area_id:
                    scope_attrs["area_id"] = scope.area_id
                if scope.location_id:
                    scope_attrs["location_id"] = scope.location_id
                if scope.character_id:
                    scope_attrs["character_id"] = scope.character_id

                for node in graph_data.nodes:
                    props = dict(node.properties or {})
                    props.update(scope_attrs)
                    injected = MemoryNode(
                        id=node.id,
                        type=node.type,
                        name=node.name,
                        created_at=node.created_at,
                        updated_at=node.updated_at,
                        importance=node.importance,
                        properties=props,
                    )
                    merged.add_node(injected)
                for edge in graph_data.edges:
                    pending_edges.append(edge)
            elif isinstance(item, MemoryGraph):
                for node in item.list_nodes():
                    merged.add_node(node)
                for edge in item.list_edges():
                    pending_edges.append(edge)

        # Second pass: add edges after all nodes are merged so cross-scope
        # references (e.g. character->area perspective edges) are preserved.
        for edge in pending_edges:
            if merged.has_edge(edge.id):
                continue
            if edge.source in merged.graph and edge.target in merged.graph:
                merged.add_edge(edge)

        return merged

    @classmethod
    def from_graph_data(cls, graph_data: GraphData) -> "MemoryGraph":
        """Build MemoryGraph from GraphData."""
        graph = cls()
        node_ids = set()
        for node in graph_data.nodes:
            graph.add_node(node)
            node_ids.add(node.id)
        for edge in graph_data.edges:
            if edge.source not in node_ids:
                graph.add_node(
                    MemoryNode(
                        id=edge.source,
                        type="unknown",
                        name=edge.source,
                        importance=0.0,
                        properties={
                            "placeholder": True,
                            "_placeholder_source": "from_graph_data",
                        },
                    )
                )
                node_ids.add(edge.source)
            if edge.target not in node_ids:
                graph.add_node(
                    MemoryNode(
                        id=edge.target,
                        type="unknown",
                        name=edge.target,
                        importance=0.0,
                        properties={
                            "placeholder": True,
                            "_placeholder_source": "from_graph_data",
                        },
                    )
                )
                node_ids.add(edge.target)
            graph.add_edge(edge)
        return graph

    def _index_node(self, node_id: str, data: Dict) -> None:
        node_type = data.get("type")
        if node_type:
            self._type_index.setdefault(node_type, set()).add(node_id)
        name = data.get("name")
        if name:
            self._name_index.setdefault(name.lower(), set()).add(node_id)
        # CRPG scope indexes from properties
        props = data.get("properties") or {}
        chapter_id = props.get("chapter_id")
        if chapter_id:
            self._chapter_index.setdefault(chapter_id, set()).add(node_id)
        area_id = props.get("area_id")
        if area_id:
            self._area_index.setdefault(area_id, set()).add(node_id)
        location_id = props.get("location_id")
        if location_id:
            self._location_index.setdefault(location_id, set()).add(node_id)
        day = props.get("day", props.get("game_day"))
        if day is not None:
            self._day_index.setdefault(day, set()).add(node_id)
        participants = props.get("participants")
        if isinstance(participants, list):
            for pid in participants:
                self._participant_index.setdefault(pid, set()).add(node_id)

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
        # CRPG scope deindex
        props = data.get("properties") or {}
        for field, index in [
            ("chapter_id", self._chapter_index),
            ("area_id", self._area_index),
            ("location_id", self._location_index),
        ]:
            val = props.get(field)
            if val and node_id in index.get(val, set()):
                index[val].discard(node_id)
                if not index[val]:
                    index.pop(val, None)
        day = props.get("day", props.get("game_day"))
        if day is not None and node_id in self._day_index.get(day, set()):
            self._day_index[day].discard(node_id)
            if not self._day_index[day]:
                self._day_index.pop(day, None)
        participants = props.get("participants")
        if isinstance(participants, list):
            for pid in participants:
                if node_id in self._participant_index.get(pid, set()):
                    self._participant_index[pid].discard(node_id)
                    if not self._participant_index[pid]:
                        self._participant_index.pop(pid, None)
