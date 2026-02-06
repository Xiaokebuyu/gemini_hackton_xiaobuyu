"""
Graph persistence service (Firestore).

支持的图谱类型：
- character: 角色级图谱 (worlds/{world_id}/characters/{character_id}/)
- gm/world: 世界级图谱 (worlds/{world_id}/graphs/{graph_type}/)

v2 新增（GraphScope 统一寻址）：
- chapter: 章节级 (worlds/{wid}/chapters/{cid}/graph/)
- area: 区域级 (worlds/{wid}/chapters/{cid}/areas/{aid}/graph/)
- location_v2: 地点级 (worlds/{wid}/chapters/{cid}/areas/{aid}/locations/{lid}/graph/)
- camp: 营地 (worlds/{wid}/camp/graph/)
- dispositions: 好感度 (worlds/{wid}/characters/{cid}/dispositions/{tid})
- choices: 选择后果 (worlds/{wid}/choices/{choice_id})
"""
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from google.cloud import firestore

from app.config import settings
from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope
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
        graph: Union[GraphData, MemoryGraph],
        merge: bool = True,
    ) -> None:
        """Save a full graph using GraphScope addressing."""
        graph_data = graph.to_graph_data() if isinstance(graph, MemoryGraph) else graph
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

    # ---- Disposition (好感度) interfaces ----

    def _get_disposition_ref(
        self,
        world_id: str,
        character_id: str,
        target_id: str,
    ) -> firestore.DocumentReference:
        """Get Firestore ref for a disposition document."""
        return (
            self.db.collection("worlds")
            .document(world_id)
            .collection("characters")
            .document(character_id)
            .collection("dispositions")
            .document(target_id)
        )

    async def get_disposition(
        self,
        world_id: str,
        character_id: str,
        target_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get disposition of character toward target.

        Returns dict with keys: approval, trust, fear, romance,
        last_updated, history. Returns None if no disposition exists.
        """
        doc = self._get_disposition_ref(world_id, character_id, target_id).get()
        if not doc.exists:
            return None
        return doc.to_dict()

    async def update_disposition(
        self,
        world_id: str,
        character_id: str,
        target_id: str,
        deltas: Dict[str, int],
        reason: str = "",
        game_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update disposition with deltas and append to history.

        Args:
            deltas: e.g. {"approval": +5, "trust": -10}
            reason: human-readable reason for the change
            game_day: in-game day number

        Returns:
            Updated disposition dict.
        """
        ref = self._get_disposition_ref(world_id, character_id, target_id)
        doc = ref.get()

        if doc.exists:
            data = doc.to_dict() or {}
        else:
            data = {
                "approval": 0,
                "trust": 0,
                "fear": 0,
                "romance": 0,
                "history": [],
            }

        # Apply deltas with clamping
        clamp_ranges = {
            "approval": (-100, 100),
            "trust": (-100, 100),
            "fear": (0, 100),
            "romance": (0, 100),
        }
        history_entry = {"reason": reason, "day": game_day}
        for field, delta in deltas.items():
            if field not in clamp_ranges:
                continue
            lo, hi = clamp_ranges[field]
            current = data.get(field, 0)
            data[field] = max(lo, min(hi, current + delta))
            history_entry[f"delta_{field}"] = delta

        data["last_updated"] = datetime.now()

        # Append history (keep last 50 entries)
        history = data.get("history", [])
        if not isinstance(history, list):
            history = []
        history.append(history_entry)
        if len(history) > 50:
            history = history[-50:]
        data["history"] = history

        ref.set(data)
        return data

    async def get_all_dispositions(
        self,
        world_id: str,
        character_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Get all dispositions for a character.

        Returns:
            Dict mapping target_id to disposition data.
        """
        base_ref = (
            self.db.collection("worlds")
            .document(world_id)
            .collection("characters")
            .document(character_id)
            .collection("dispositions")
        )
        result = {}
        for doc in base_ref.stream():
            data = doc.to_dict()
            if data:
                result[doc.id] = data
        return result

    # ---- Choice (选择后果追踪) interfaces ----

    def _get_choice_ref(
        self,
        world_id: str,
        choice_id: str,
    ) -> firestore.DocumentReference:
        """Get Firestore ref for a choice document."""
        return (
            self.db.collection("worlds")
            .document(world_id)
            .collection("choices")
            .document(choice_id)
        )

    def _build_choice_node(
        self,
        choice_id: str,
        description: str,
        chapter_id: Optional[str],
        consequences: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
        character_id: str,
        resolved: bool,
    ) -> MemoryNode:
        """Build a choice node for character graph recall."""
        props: Dict[str, Any] = {
            "scope_type": "character",
            "character_id": character_id,
            "choice_id": choice_id,
            "chapter_id": chapter_id,
            "consequences": consequences,
            "resolved": resolved,
            "created_by": "player",
            "source": "choices_collection",
        }
        if metadata:
            props["metadata"] = metadata

        return MemoryNode(
            id=f"choice_{choice_id}",
            type="choice",
            name=description[:80] if description else choice_id,
            importance=0.9,
            properties=props,
        )

    async def record_choice(
        self,
        world_id: str,
        choice_id: str,
        description: str,
        chapter_id: Optional[str] = None,
        consequences: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        character_id: Optional[str] = None,
        write_to_character_graph: bool = True,
    ) -> Dict[str, Any]:
        """Record a player choice with its potential consequences.

        Args:
            choice_id: unique identifier for the choice
            description: what the player chose
            chapter_id: which chapter the choice was made in
            consequences: list of dicts, each with:
                - description: str
                - resolved: bool (default False)
                - resolved_at: Optional[datetime]
            metadata: additional context (game_day, location, etc.)
            character_id: owning character for choice-node graph write
            write_to_character_graph: whether to mirror as character choice node

        Returns:
            The stored choice document.
        """
        normalized_consequences = consequences or []
        data: Dict[str, Any] = {
            "choice_id": choice_id,
            "description": description,
            "chapter_id": chapter_id,
            "consequences": normalized_consequences,
            "resolved": False,
            "created_at": datetime.now(),
        }
        if metadata:
            data["metadata"] = metadata
        if character_id:
            data["character_id"] = character_id

        self._get_choice_ref(world_id, choice_id).set(data)

        if character_id and write_to_character_graph:
            choice_node = self._build_choice_node(
                choice_id=choice_id,
                description=description,
                chapter_id=chapter_id,
                consequences=normalized_consequences,
                metadata=metadata,
                character_id=character_id,
                resolved=False,
            )
            await self.upsert_node_v2(
                world_id=world_id,
                scope=GraphScope.character(character_id),
                node=choice_node,
                merge=True,
            )

        return data

    async def get_choice(
        self,
        world_id: str,
        choice_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a choice record by ID."""
        doc = self._get_choice_ref(world_id, choice_id).get()
        if not doc.exists:
            return None
        return doc.to_dict()

    async def get_unresolved_consequences(
        self,
        world_id: str,
        chapter_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all choices with unresolved consequences.

        Args:
            chapter_id: filter by chapter (optional)

        Returns:
            List of choice documents with at least one unresolved consequence.
        """
        choices_ref = (
            self.db.collection("worlds")
            .document(world_id)
            .collection("choices")
        )
        query = choices_ref.where("resolved", "==", False)
        if chapter_id:
            query = query.where("chapter_id", "==", chapter_id)

        results = []
        for doc in query.stream():
            data = doc.to_dict()
            if data:
                data["choice_id"] = doc.id
                results.append(data)
        return results

    async def resolve_consequence(
        self,
        world_id: str,
        choice_id: str,
        consequence_index: int,
        resolution: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Mark a specific consequence as resolved.

        Args:
            consequence_index: index into the consequences array
            resolution: description of how it was resolved

        Returns:
            Updated choice document, or None if not found.
        """
        ref = self._get_choice_ref(world_id, choice_id)
        doc = ref.get()
        if not doc.exists:
            return None

        data = doc.to_dict() or {}
        consequences = data.get("consequences", [])
        if consequence_index < 0 or consequence_index >= len(consequences):
            return data

        consequences[consequence_index]["resolved"] = True
        consequences[consequence_index]["resolved_at"] = datetime.now()
        if resolution:
            consequences[consequence_index]["resolution"] = resolution

        # Check if all consequences are resolved
        all_resolved = all(c.get("resolved", False) for c in consequences)
        data["consequences"] = consequences
        data["resolved"] = all_resolved

        ref.set(data)

        # Keep character choice node in sync when available.
        character_id = data.get("character_id")
        if isinstance(character_id, str) and character_id:
            try:
                choice_node = self._build_choice_node(
                    choice_id=choice_id,
                    description=data.get("description", ""),
                    chapter_id=data.get("chapter_id"),
                    consequences=consequences,
                    metadata=data.get("metadata"),
                    character_id=character_id,
                    resolved=all_resolved,
                )
                await self.upsert_node_v2(
                    world_id=world_id,
                    scope=GraphScope.character(character_id),
                    node=choice_node,
                    merge=True,
                )
            except Exception:
                # Choice collection update succeeded; keep API response successful.
                pass

        return data

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

        # Placeholder backfill: if existing node is a placeholder and new node
        # carries real data, do a full replace instead of merge
        effective_merge = merge
        new_props = node.properties or {}
        if merge and not new_props.get("placeholder", False):
            doc = nodes_ref.document(node.id).get()
            if doc.exists:
                existing_props = (doc.to_dict() or {}).get("properties") or {}
                if existing_props.get("placeholder", False):
                    effective_merge = False

        operations = [(nodes_ref.document(node.id), node.model_dump(), effective_merge)]
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


def _clear_subcollection(
    collection_ref: firestore.CollectionReference,
    child_collection_name: str,
) -> None:
    for doc in collection_ref.stream():
        child_ref = doc.reference.collection(child_collection_name)
        for child_doc in child_ref.stream():
            child_doc.reference.delete()
