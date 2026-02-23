"""
Admin event service for event recording.

世界级事件持久化（直连 Firestore，world scope）。
Per-character 分发已移除 — 在线事件走 BehaviorEngine → SceneBus → ContextWindow → MemoryGraphizer。
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from app.config import settings
from app.models.event import (
    GMEventIngestRequest,
    GMEventIngestResponse,
    NaturalEventIngestRequest,
    NaturalEventIngestResponse,
)
from app.models.graph import MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope
from app.world.events.event_bus import EventBus
from app.world.graph.schema import GraphSchemaOptions, validate_edge, validate_node

logger = logging.getLogger(__name__)


class AdminEventService:
    """Admin-level event service — world-scope persistence only."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._db = firestore.Client(database=settings.firestore_database)
        self.event_bus = event_bus or EventBus()
        self._llm_service: Optional["EventLLMService"] = None

    @property
    def llm_service(self) -> "EventLLMService":
        """懒加载事件LLM服务"""
        if self._llm_service is None:
            from app.services.event_llm_service import EventLLMService
            self._llm_service = EventLLMService()
        return self._llm_service

    async def ingest_event(
        self,
        world_id: str,
        request: GMEventIngestRequest,
    ) -> GMEventIngestResponse:
        logger.info("事件摄入开始: world=%s, type=%s", world_id, request.event.type.value)
        event = request.event
        event_id = event.id or f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        gm_nodes = list(event.nodes)
        gm_edges = list(event.edges)
        gm_scope = GraphScope.world()

        if not gm_nodes:
            gm_nodes = [
                MemoryNode(
                    id=event_id,
                    type="event",
                    name=event.type.value,
                    importance=0.5,
                    properties={
                        "day": event.game_day,
                        "summary": event.content.raw or "",
                        "location": event.location,
                        "participants": event.participants,
                        "witnesses": event.witnesses,
                    },
                )
            ]

        gm_nodes, gm_edges = self._ensure_participant_links(event, event_id, gm_nodes, gm_edges)

        if request.validate_input:
            options = GraphSchemaOptions(
                allow_unknown_node_types=not request.strict,
                allow_unknown_relations=not request.strict,
                validate_event_properties=request.strict,
            )
            for node in gm_nodes:
                errors = validate_node(node, options)
                if errors:
                    raise ValueError(f"Invalid node {node.id}: {errors}")
            for edge in gm_edges:
                errors = validate_edge(edge, None, options)
                if errors:
                    raise ValueError(f"Invalid edge {edge.id}: {errors}")

        nodes_ref, edges_ref = self._get_scope_refs(world_id, gm_scope)
        for node in gm_nodes:
            nodes_ref.document(node.id).set(node.model_dump(), merge=True)
        for edge in gm_edges:
            edges_ref.document(edge.id).set(edge.model_dump(), merge=True)

        if self.event_bus:
            await self.event_bus.publish(event)

        logger.info(
            "事件摄入完成: event_id=%s, nodes=%d, edges=%d",
            event_id, len(gm_nodes), len(gm_edges),
        )
        return GMEventIngestResponse(
            event_id=event_id,
            gm_node_count=len(gm_nodes),
            gm_edge_count=len(gm_edges),
            dispatched=False,
            recipients=[],
        )

    # ==================== Firestore 寻址 ====================

    def _get_scope_refs(
        self,
        world_id: str,
        scope: GraphScope,
    ) -> tuple:
        """将 GraphScope 映射为 Firestore nodes/edges 集合引用。"""
        worlds_ref = self._db.collection("worlds").document(world_id)
        if scope.scope_type == "world":
            base = worlds_ref.collection("graphs").document("world")
        elif scope.scope_type == "character":
            base = worlds_ref.collection("characters").document(scope.character_id)
        else:
            raise ValueError(f"EventService 不支持的 scope: {scope.scope_type}")
        return base.collection("nodes"), base.collection("edges")

    # ==================== 内部辅助 ====================

    def _ensure_participant_links(
        self,
        event,
        event_id: str,
        gm_nodes: List[MemoryNode],
        gm_edges: List,
    ) -> tuple[List[MemoryNode], List]:
        if gm_edges:
            return gm_nodes, gm_edges

        node_ids = {node.id for node in gm_nodes}
        edges = list(gm_edges)
        new_nodes = list(gm_nodes)

        participant_map = self._normalize_people(event.participants)
        witness_map = self._normalize_people(event.witnesses)

        for raw_id, node_id in {**participant_map, **witness_map}.items():
            if node_id in node_ids:
                continue
            new_nodes.append(
                MemoryNode(
                    id=node_id,
                    type="person",
                    name=raw_id,
                    importance=0.4,
                    properties={},
                )
            )
            node_ids.add(node_id)

        seen_edges = set()
        for raw_id, node_id in participant_map.items():
            edge_id = f"edge_{node_id}_{event_id}_participated"
            if edge_id in seen_edges:
                continue
            edges.append(
                {
                    "id": edge_id,
                    "source": node_id,
                    "target": event_id,
                    "relation": "participated",
                    "weight": 0.8,
                    "properties": {},
                }
            )
            seen_edges.add(edge_id)

        for raw_id, node_id in witness_map.items():
            edge_id = f"edge_{node_id}_{event_id}_witnessed"
            if edge_id in seen_edges:
                continue
            edges.append(
                {
                    "id": edge_id,
                    "source": node_id,
                    "target": event_id,
                    "relation": "witnessed",
                    "weight": 0.7,
                    "properties": {},
                }
            )
            seen_edges.add(edge_id)

        from app.models.graph import MemoryEdge

        normalized_edges: List[MemoryEdge] = []
        for edge in edges:
            if isinstance(edge, MemoryEdge):
                normalized_edges.append(edge)
            else:
                normalized_edges.append(MemoryEdge(**edge))

        return new_nodes, normalized_edges

    def _normalize_people(self, people: List[str]) -> dict:
        normalized = {}
        for raw in people or []:
            if raw.startswith("person_"):
                normalized[raw] = raw
            else:
                normalized[raw] = f"person_{raw}"
        return normalized

    # ==================== LLM增强方法 ====================

    async def ingest_event_natural(
        self,
        world_id: str,
        request: NaturalEventIngestRequest,
    ) -> NaturalEventIngestResponse:
        """LLM增强的事件摄入：自然语言 → GM图谱（仅世界级持久化）。"""
        logger.info("自然语言事件摄入开始: world=%s", world_id)
        # 1. 解析事件结构
        parsed_event = await self.llm_service.parse_event(
            event_description=request.event_description,
            known_characters=request.known_characters,
            known_locations=request.known_locations,
        )

        # 2. 获取GM图谱中已有的重要节点
        existing_nodes = await self._get_gm_important_nodes(world_id, limit=30)

        # 3. 编码为GM图谱数据
        gm_encoded = await self.llm_service.encode_gm_event(
            event_description=request.event_description,
            parsed_event=parsed_event,
            game_day=request.game_day,
            existing_nodes=existing_nodes,
        )

        # 4. 生成事件ID
        event_id = f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # 5. 写入GM图谱
        gm_nodes = [MemoryNode(**n) for n in gm_encoded.get("nodes", [])]
        gm_edges = [MemoryEdge(**e) for e in gm_encoded.get("edges", [])]
        gm_scope = GraphScope.world()

        nodes_ref, edges_ref = self._get_scope_refs(world_id, gm_scope)
        for node in gm_nodes:
            nodes_ref.document(node.id).set(node.model_dump(), merge=True)
        for edge in gm_edges:
            edges_ref.document(edge.id).set(edge.model_dump(), merge=True)

        return NaturalEventIngestResponse(
            event_id=event_id,
            parsed_event=parsed_event,
            gm_node_count=len(gm_nodes),
            gm_edge_count=len(gm_edges),
            dispatched=False,
            recipients=[],
        )

    async def _get_gm_important_nodes(
        self,
        world_id: str,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """获取GM图谱中的重要节点"""
        nodes_ref, _ = self._get_scope_refs(world_id, GraphScope.world())
        nodes = []
        for doc in nodes_ref.stream():
            data = doc.to_dict()
            if not data:
                continue
            if "id" not in data:
                data["id"] = doc.id
            nodes.append(MemoryNode(**data))

        if not nodes:
            return []

        # 按importance排序，取前N个
        sorted_nodes = sorted(nodes, key=lambda n: n.importance, reverse=True)

        return [
            {
                "id": n.id,
                "type": n.type,
                "name": n.name,
                "importance": n.importance,
                "properties": n.properties,
            }
            for n in sorted_nodes[:limit]
        ]
