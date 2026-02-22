"""
Graph persistence service (Firestore).

GraphScope 统一寻址：
- world    → worlds/{wid}/graphs/world
- chapter  → worlds/{wid}/chapters/{cid}/graph
- area     → worlds/{wid}/chapters/{cid}/areas/{aid}/graph
- location → worlds/{wid}/chapters/{cid}/areas/{aid}/locations/{lid}/graph
- character → worlds/{wid}/characters/{char_id}
- camp     → worlds/{wid}/camp/graph
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple

from google.cloud import firestore

from app.config import settings
from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope


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

    def _get_base_ref_v2(
        self,
        world_id: str,
        scope: GraphScope,
    ) -> firestore.DocumentReference:
        """Resolve a GraphScope to a Firestore document reference.

        Path mappings:
          world    -> worlds/{wid}/graphs/world
          chapter  -> worlds/{wid}/chapters/{cid}/graph
          area     -> worlds/{wid}/chapters/{cid}/areas/{aid}/graph
          location -> worlds/{wid}/chapters/{cid}/areas/{aid}/locations/{lid}/graph
          character -> worlds/{wid}/characters/{char_id}
          camp     -> worlds/{wid}/camp/graph
        """
        worlds_ref = self.db.collection("worlds").document(world_id)

        if scope.scope_type == "world":
            return worlds_ref.collection("graphs").document("world")

        if scope.scope_type == "chapter":
            return (
                worlds_ref.collection("chapters")
                .document(scope.chapter_id)
                .collection("graph")
                .document("data")
            )

        if scope.scope_type == "area":
            return (
                worlds_ref.collection("chapters")
                .document(scope.chapter_id)
                .collection("areas")
                .document(scope.area_id)
                .collection("graph")
                .document("data")
            )

        if scope.scope_type == "location":
            return (
                worlds_ref.collection("chapters")
                .document(scope.chapter_id)
                .collection("areas")
                .document(scope.area_id)
                .collection("locations")
                .document(scope.location_id)
                .collection("graph")
                .document("data")
            )

        if scope.scope_type == "character":
            return worlds_ref.collection("characters").document(scope.character_id)

        if scope.scope_type == "camp":
            return worlds_ref.collection("camp").document("graph")

        raise ValueError(f"Unknown scope_type: {scope.scope_type}")

    def _get_graph_refs_v2(
        self,
        world_id: str,
        scope: GraphScope,
    ) -> Tuple[firestore.CollectionReference, firestore.CollectionReference]:
        """Get nodes/edges collection refs for a GraphScope."""
        base_ref = self._get_base_ref_v2(world_id, scope)
        return base_ref.collection("nodes"), base_ref.collection("edges")

    async def load_graph_v2(
        self,
        world_id: str,
        scope: GraphScope,
    ) -> GraphData:
        """Load a full graph using GraphScope addressing."""
        nodes_ref, edges_ref = self._get_graph_refs_v2(world_id, scope)
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

    async def save_graph_v2(
        self,
        world_id: str,
        scope: GraphScope,
        graph: GraphData,
        merge: bool = True,
    ) -> None:
        """Save a full graph using GraphScope addressing."""
        graph_data = graph
        nodes_ref, edges_ref = self._get_graph_refs_v2(world_id, scope)
        operations = []
        for node in graph_data.nodes:
            operations.append((nodes_ref.document(node.id), node.model_dump(), merge))
        for edge in graph_data.edges:
            operations.append((edges_ref.document(edge.id), edge.model_dump(), merge))
        self._commit_in_batches(operations)

    async def upsert_node_v2(
        self,
        world_id: str,
        scope: GraphScope,
        node: MemoryNode,
        merge: bool = True,
    ) -> None:
        """Upsert a single node using GraphScope addressing."""
        nodes_ref, _ = self._get_graph_refs_v2(world_id, scope)
        effective_merge = merge
        new_props = node.properties or {}
        if merge and not new_props.get("placeholder", False):
            doc = nodes_ref.document(node.id).get()
            if doc.exists:
                existing_props = (doc.to_dict() or {}).get("properties") or {}
                if existing_props.get("placeholder", False):
                    effective_merge = False
        nodes_ref.document(node.id).set(node.model_dump(), merge=effective_merge)

    async def upsert_edge_v2(
        self,
        world_id: str,
        scope: GraphScope,
        edge: MemoryEdge,
        merge: bool = True,
    ) -> None:
        """Upsert a single edge using GraphScope addressing."""
        _, edges_ref = self._get_graph_refs_v2(world_id, scope)
        edges_ref.document(edge.id).set(edge.model_dump(), merge=merge)

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

    async def get_nodes_by_ids_v2(
        self,
        world_id: str,
        scope: GraphScope,
        node_ids: Iterable[str],
    ) -> List[MemoryNode]:
        """Fetch multiple nodes by id using GraphScope addressing."""
        nodes_ref, _ = self._get_graph_refs_v2(world_id, scope)
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


    async def load_local_subgraph_v2(
        self,
        world_id: str,
        scope: GraphScope,
        seed_nodes: Iterable[str],
        depth: int = 1,
        direction: str = "both",
    ) -> GraphData:
        """Load a subgraph by traversing edges using GraphScope addressing."""
        direction = direction.lower()
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be one of: out, in, both")
        nodes_ref, edges_ref = self._get_graph_refs_v2(world_id, scope)

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

        nodes = await self.get_nodes_by_ids_v2(world_id, scope, visited)
        return GraphData(nodes=nodes, edges=list(edges_by_id.values()))



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


def _chunked(items: List[str], size: int) -> List[List[str]]:
    if size <= 0:
        return []
    return [items[i : i + size] for i in range(0, len(items), size)]

