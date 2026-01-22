"""
Graph persistence service (Firestore).
"""
from typing import Iterable, List, Optional, Tuple, Union

from google.cloud import firestore

from app.config import settings
from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.services.memory_graph import MemoryGraph


class GraphStore:
    """Graph storage service."""

    def __init__(self, firestore_client: Optional[firestore.Client] = None) -> None:
        self.db = firestore_client or firestore.Client(database=settings.firestore_database)

    def _get_base_ref(
        self,
        world_id: str,
        graph_type: str,
        character_id: Optional[str] = None,
    ) -> firestore.DocumentReference:
        if graph_type == "character":
            if not character_id:
                raise ValueError("character graph requires character_id")
            return (
                self.db.collection("worlds")
                .document(world_id)
                .collection("characters")
                .document(character_id)
            )
        return (
            self.db.collection("worlds")
            .document(world_id)
            .collection("graphs")
            .document(graph_type)
        )

    def _get_graph_refs(
        self,
        world_id: str,
        graph_type: str,
        character_id: Optional[str] = None,
    ) -> Tuple[firestore.CollectionReference, firestore.CollectionReference]:
        base_ref = self._get_base_ref(world_id, graph_type, character_id)
        return base_ref.collection("nodes"), base_ref.collection("edges")

    async def load_graph(
        self,
        world_id: str,
        graph_type: str,
        character_id: Optional[str] = None,
    ) -> GraphData:
        """Load a full graph."""
        nodes_ref, edges_ref = self._get_graph_refs(world_id, graph_type, character_id)
        nodes = []
        for doc in nodes_ref.stream():
            data = doc.to_dict()
            if not data:
                continue
            if "id" not in data:
                data["id"] = doc.id
            nodes.append(MemoryNode(**data))
        edges = []
        for doc in edges_ref.stream():
            data = doc.to_dict()
            if not data:
                continue
            if "id" not in data:
                data["id"] = doc.id
            edges.append(MemoryEdge(**data))
        return GraphData(nodes=nodes, edges=edges)

    async def save_graph(
        self,
        world_id: str,
        graph_type: str,
        graph: Union[GraphData, MemoryGraph],
        character_id: Optional[str] = None,
        merge: bool = True,
        build_indexes: bool = False,
    ) -> None:
        """Save a full graph (merge by default, does not delete)."""
        graph_data = graph.to_graph_data() if isinstance(graph, MemoryGraph) else graph
        nodes_ref, edges_ref = self._get_graph_refs(world_id, graph_type, character_id)
        operations = []
        for node in graph_data.nodes:
            operations.append((nodes_ref.document(node.id), node.model_dump(), merge))
        for edge in graph_data.edges:
            operations.append((edges_ref.document(edge.id), edge.model_dump(), merge))
        if build_indexes:
            base_ref = self._get_base_ref(world_id, graph_type, character_id)
            for node in graph_data.nodes:
                operations.extend(self._index_node_operations(base_ref, node))
        self._commit_in_batches(operations)

    async def upsert_node(
        self,
        world_id: str,
        graph_type: str,
        node: MemoryNode,
        character_id: Optional[str] = None,
        merge: bool = True,
        index: bool = False,
    ) -> None:
        """Upsert a single node."""
        nodes_ref, _ = self._get_graph_refs(world_id, graph_type, character_id)
        operations = [(nodes_ref.document(node.id), node.model_dump(), merge)]
        if index:
            base_ref = self._get_base_ref(world_id, graph_type, character_id)
            operations.extend(self._index_node_operations(base_ref, node))
        self._commit_in_batches(operations)

    async def upsert_edge(
        self,
        world_id: str,
        graph_type: str,
        edge: MemoryEdge,
        character_id: Optional[str] = None,
        merge: bool = True,
    ) -> None:
        """Upsert a single edge."""
        _, edges_ref = self._get_graph_refs(world_id, graph_type, character_id)
        edges_ref.document(edge.id).set(edge.model_dump(), merge=merge)

    async def get_node(
        self,
        world_id: str,
        graph_type: str,
        node_id: str,
        character_id: Optional[str] = None,
    ) -> Optional[MemoryNode]:
        """Fetch a single node."""
        nodes_ref, _ = self._get_graph_refs(world_id, graph_type, character_id)
        doc = nodes_ref.document(node_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        if "id" not in data:
            data["id"] = doc.id
        return MemoryNode(**data)

    async def get_edge(
        self,
        world_id: str,
        graph_type: str,
        edge_id: str,
        character_id: Optional[str] = None,
    ) -> Optional[MemoryEdge]:
        """Fetch a single edge."""
        _, edges_ref = self._get_graph_refs(world_id, graph_type, character_id)
        doc = edges_ref.document(edge_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        if "id" not in data:
            data["id"] = doc.id
        return MemoryEdge(**data)

    async def clear_graph(
        self,
        world_id: str,
        graph_type: str,
        character_id: Optional[str] = None,
    ) -> None:
        """Clear a graph (destructive)."""
        nodes_ref, edges_ref = self._get_graph_refs(world_id, graph_type, character_id)
        for doc in nodes_ref.stream():
            doc.reference.delete()
        for doc in edges_ref.stream():
            doc.reference.delete()

    async def update_character_state(
        self,
        world_id: str,
        character_id: str,
        updates: dict,
    ) -> None:
        """Update character state on character document."""
        base_ref = self._get_base_ref(world_id, "character", character_id)
        base_ref.set({"state": updates}, merge=True)

    async def get_character_state(
        self,
        world_id: str,
        character_id: str,
    ) -> dict:
        """Get character state."""
        base_ref = self._get_base_ref(world_id, "character", character_id)
        doc = base_ref.get()
        if not doc.exists:
            return {}
        data = doc.to_dict() or {}
        return data.get("state", {}) or {}

    async def get_character_profile(
        self,
        world_id: str,
        character_id: str,
    ) -> dict:
        """Get character profile."""
        base_ref = self._get_base_ref(world_id, "character", character_id)
        doc = base_ref.get()
        if not doc.exists:
            return {}
        data = doc.to_dict() or {}
        return data.get("profile", {}) or {}

    async def set_character_profile(
        self,
        world_id: str,
        character_id: str,
        profile: dict,
        merge: bool = True,
    ) -> None:
        """Set character profile."""
        base_ref = self._get_base_ref(world_id, "character", character_id)
        base_ref.set({"profile": profile}, merge=merge)

    async def get_nodes_by_ids(
        self,
        world_id: str,
        graph_type: str,
        node_ids: Iterable[str],
        character_id: Optional[str] = None,
    ) -> List[MemoryNode]:
        """Fetch multiple nodes by id."""
        nodes_ref, _ = self._get_graph_refs(world_id, graph_type, character_id)
        doc_refs = [nodes_ref.document(node_id) for node_id in node_ids]
        if not doc_refs:
            return []
        docs = self.db.get_all(doc_refs)
        nodes: List[MemoryNode] = []
        for doc in docs:
            if not doc.exists:
                continue
            data = doc.to_dict() or {}
            if "id" not in data:
                data["id"] = doc.id
            nodes.append(MemoryNode(**data))
        return nodes

    async def query_index_by_type(
        self,
        world_id: str,
        graph_type: str,
        node_type: str,
        character_id: Optional[str] = None,
    ) -> List[dict]:
        """Query nodes by type index."""
        base_ref = self._get_base_ref(world_id, graph_type, character_id)
        nodes_ref = (
            base_ref.collection("type_index")
            .document(node_type)
            .collection("nodes")
        )
        return [doc.to_dict() for doc in nodes_ref.stream() if doc.to_dict()]

    async def query_index_by_name(
        self,
        world_id: str,
        graph_type: str,
        name: str,
        character_id: Optional[str] = None,
    ) -> List[dict]:
        """Query nodes by name index."""
        base_ref = self._get_base_ref(world_id, graph_type, character_id)
        name_key = self._sanitize_index_key(name.lower())
        nodes_ref = (
            base_ref.collection("name_index")
            .document(name_key)
            .collection("nodes")
        )
        return [doc.to_dict() for doc in nodes_ref.stream() if doc.to_dict()]

    async def query_index_by_day(
        self,
        world_id: str,
        graph_type: str,
        day: str,
        character_id: Optional[str] = None,
    ) -> List[dict]:
        """Query events by day index."""
        base_ref = self._get_base_ref(world_id, graph_type, character_id)
        day_key = self._sanitize_index_key(str(day))
        events_ref = (
            base_ref.collection("timeline")
            .document(day_key)
            .collection("events")
        )
        return [doc.to_dict() for doc in events_ref.stream() if doc.to_dict()]

    async def load_local_subgraph(
        self,
        world_id: str,
        graph_type: str,
        seed_nodes: Iterable[str],
        depth: int = 1,
        direction: str = "both",
        character_id: Optional[str] = None,
    ) -> GraphData:
        """Load a subgraph by traversing edges in Firestore."""
        direction = direction.lower()
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be one of: out, in, both")
        nodes_ref, edges_ref = self._get_graph_refs(world_id, graph_type, character_id)

        visited = {node_id for node_id in seed_nodes if node_id}
        frontier = set(visited)
        edges_by_id = {}

        for _ in range(depth):
            if not frontier:
                break
            next_frontier = set()

            if direction in {"out", "both"}:
                for chunk in _chunked(list(frontier), 10):
                    for doc in edges_ref.where("source", "in", chunk).stream():
                        data = doc.to_dict() or {}
                        if not data:
                            continue
                        if "id" not in data:
                            data["id"] = doc.id
                        edges_by_id[data["id"]] = MemoryEdge(**data)
                        target = data.get("target")
                        if target:
                            next_frontier.add(target)

            if direction in {"in", "both"}:
                for chunk in _chunked(list(frontier), 10):
                    for doc in edges_ref.where("target", "in", chunk).stream():
                        data = doc.to_dict() or {}
                        if not data:
                            continue
                        if "id" not in data:
                            data["id"] = doc.id
                        edges_by_id[data["id"]] = MemoryEdge(**data)
                        source = data.get("source")
                        if source:
                            next_frontier.add(source)

            next_frontier -= visited
            visited |= next_frontier
            frontier = next_frontier

        nodes = await self.get_nodes_by_ids(world_id, graph_type, visited, character_id)
        return GraphData(nodes=nodes, edges=list(edges_by_id.values()))

    async def rebuild_indexes(
        self,
        world_id: str,
        graph_type: str,
        character_id: Optional[str] = None,
        clear_first: bool = False,
    ) -> int:
        """Rebuild indexes for an existing graph."""
        if clear_first:
            await self.clear_indexes(world_id, graph_type, character_id)
        nodes_ref, _ = self._get_graph_refs(world_id, graph_type, character_id)
        base_ref = self._get_base_ref(world_id, graph_type, character_id)
        operations = []
        count = 0
        for doc in nodes_ref.stream():
            data = doc.to_dict() or {}
            if not data:
                continue
            if "id" not in data:
                data["id"] = doc.id
            node = MemoryNode(**data)
            operations.extend(self._index_node_operations(base_ref, node))
            count += 1
            if len(operations) >= 400:
                self._commit_in_batches(operations)
                operations = []
        if operations:
            self._commit_in_batches(operations)
        return count

    async def clear_indexes(
        self,
        world_id: str,
        graph_type: str,
        character_id: Optional[str] = None,
    ) -> None:
        """Clear all index collections for a graph."""
        base_ref = self._get_base_ref(world_id, graph_type, character_id)
        _clear_subcollection(base_ref.collection("type_index"), "nodes")
        _clear_subcollection(base_ref.collection("name_index"), "nodes")
        _clear_subcollection(base_ref.collection("timeline"), "events")

    def _sanitize_index_key(self, value: str) -> str:
        return value.replace("/", "_").strip()

    def _index_node_operations(
        self,
        base_ref: firestore.DocumentReference,
        node: MemoryNode,
    ) -> list:
        operations = []
        payload = {
            "node_id": node.id,
            "name": node.name,
            "type": node.type,
        }
        if node.type:
            type_ref = (
                base_ref.collection("type_index")
                .document(node.type)
                .collection("nodes")
                .document(node.id)
            )
            operations.append((type_ref, payload, True))
        if node.name:
            name_key = self._sanitize_index_key(node.name.lower())
            name_ref = (
                base_ref.collection("name_index")
                .document(name_key)
                .collection("nodes")
                .document(node.id)
            )
            operations.append((name_ref, payload, True))
        if node.type == "event":
            props = node.properties or {}
            day_value = props.get("day", props.get("game_day"))
            if day_value is not None:
                day_key = self._sanitize_index_key(str(day_value))
                timeline_ref = (
                    base_ref.collection("timeline")
                    .document(day_key)
                    .collection("events")
                    .document(node.id)
                )
                timeline_payload = dict(payload)
                timeline_payload["day"] = day_value
                operations.append((timeline_ref, timeline_payload, True))
        return operations


def _chunked(items: List[str], size: int) -> List[List[str]]:
    if size <= 0:
        return []
    return [items[i : i + size] for i in range(0, len(items), size)]


def _clear_subcollection(
    collection_ref: firestore.CollectionReference,
    child_collection_name: str,
) -> None:
    for doc in collection_ref.stream():
        child_ref = doc.reference.collection(child_collection_name)
        for child_doc in child_ref.stream():
            child_doc.reference.delete()
        doc.reference.delete()

    def _commit_in_batches(self, operations: Iterable[Tuple[firestore.DocumentReference, dict, bool]]) -> None:
        """Commit in batches to avoid Firestore limits."""
        batch = self.db.batch()
        op_count = 0
        for doc_ref, payload, merge in operations:
            batch.set(doc_ref, payload, merge=merge)
            op_count += 1
            if op_count >= 450:
                batch.commit()
                batch = self.db.batch()
                op_count = 0
        if op_count:
            batch.commit()
