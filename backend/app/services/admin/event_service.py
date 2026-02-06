"""
Admin event service for event recording and dispatch.

支持两种模式：
1. 结构化模式：直接传入节点/边数据
2. 自然语言模式：通过LLM解析事件并进行视角转换
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.models.event import (
    CharacterDispatchResult,
    GMEventIngestRequest,
    GMEventIngestResponse,
    NaturalEventIngestRequest,
    NaturalEventIngestResponse,
)
from app.models.flash import EventIngestRequest
from app.models.graph import MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope
from app.models.pro import CharacterProfile
from app.services.event_bus import EventBus
from app.services.flash_service import FlashService
from app.services.graph_schema import GraphSchemaOptions, validate_edge, validate_node
from app.services.graph_store import GraphStore


class AdminEventService:
    """Admin-level event service."""

    def __init__(
        self,
        graph_store: Optional[GraphStore] = None,
        flash_service: Optional[FlashService] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.graph_store = graph_store or GraphStore()
        self.flash_service = flash_service or FlashService(self.graph_store)
        self.event_bus = event_bus or EventBus()
        self._llm_service: Optional["EventLLMService"] = None

    @property
    def llm_service(self) -> "EventLLMService":
        """懒加载事件LLM服务"""
        if self._llm_service is None:
            from app.services.admin.event_llm_service import EventLLMService
            self._llm_service = EventLLMService()
        return self._llm_service

    async def ingest_event(
        self,
        world_id: str,
        request: GMEventIngestRequest,
    ) -> GMEventIngestResponse:
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

        for node in gm_nodes:
            await self.graph_store.upsert_node_v2(
                world_id=world_id,
                scope=gm_scope,
                node=node,
            )
        for edge in gm_edges:
            await self.graph_store.upsert_edge_v2(
                world_id=world_id,
                scope=gm_scope,
                edge=edge,
            )

        if self.event_bus:
            await self.event_bus.publish(event)

        recipients: List[str] = []
        if request.distribute:
            recipients = sorted(self._resolve_recipients(request))
            for character_id in recipients:
                override = request.per_character.get(character_id)
                if override:
                    ingest_request = EventIngestRequest(
                        event_id=event_id,
                        description=event.content.raw,
                        timestamp=event.timestamp,
                        game_day=event.game_day,
                        location=event.location,
                        perspective="gm_dispatch",
                        participants=event.participants,
                        witnesses=event.witnesses,
                        visibility_public=event.visibility.public,
                        nodes=override.nodes,
                        edges=override.edges,
                        state_updates=override.state_updates,
                        write_indexes=override.write_indexes,
                        validate_input=override.validate_input,
                        strict=override.strict,
                    )
                elif request.default_dispatch:
                    ingest_request = EventIngestRequest(
                        event_id=event_id,
                        description=event.content.raw,
                        timestamp=event.timestamp,
                        game_day=event.game_day,
                        location=event.location,
                        perspective="gm_dispatch",
                        participants=event.participants,
                        witnesses=event.witnesses,
                        visibility_public=event.visibility.public,
                        nodes=gm_nodes,
                        edges=gm_edges,
                        write_indexes=request.write_indexes,
                        validate_input=request.validate_input,
                        strict=request.strict,
                    )
                else:
                    continue

                await self.flash_service.ingest_event(
                    world_id=world_id,
                    character_id=character_id,
                    request=ingest_request,
                )

        return GMEventIngestResponse(
            event_id=event_id,
            gm_node_count=len(gm_nodes),
            gm_edge_count=len(gm_edges),
            dispatched=request.distribute,
            recipients=recipients,
        )

    def _resolve_recipients(self, request: GMEventIngestRequest) -> Set[str]:
        event = request.event
        if request.recipients is not None:
            return set(request.recipients)

        recipients = set(event.participants) | set(event.witnesses) | set(event.visibility.known_to)

        if event.visibility.public and request.known_characters:
            recipients |= set(request.known_characters)

        if event.location and request.character_locations:
            for char_id, location in request.character_locations.items():
                if location == event.location:
                    recipients.add(char_id)

        return recipients

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
        """
        LLM增强的事件摄入：自然语言 → GM图谱 + 角色视角分发

        流程：
        1. 解析事件：提取参与者、目击者、地点等结构
        2. 编码GM事件：生成GM图谱的客观记录
        3. 写入GM图谱
        4. 视角转换：为每个接收者生成专属记忆
        5. 写入角色记忆

        Args:
            world_id: 世界ID
            request: 自然语言事件请求

        Returns:
            摄入结果，包含GM图谱统计和各角色分发结果
        """
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

        for node in gm_nodes:
            await self.graph_store.upsert_node_v2(
                world_id=world_id,
                scope=gm_scope,
                node=node,
            )

        for edge in gm_edges:
            await self.graph_store.upsert_edge_v2(
                world_id=world_id,
                scope=gm_scope,
                edge=edge,
            )

        # 6. 确定接收者和视角
        recipients_result: List[CharacterDispatchResult] = []

        if request.distribute:
            recipients_with_perspective = self._determine_perspectives(
                parsed_event=parsed_event,
                known_characters=request.known_characters,
                character_locations=request.character_locations,
            )

            # 7. 对每个接收者进行视角转换和写入
            for character_id, perspective in recipients_with_perspective.items():
                try:
                    result = await self._dispatch_to_character(
                        world_id=world_id,
                        character_id=character_id,
                        event_description=request.event_description,
                        parsed_event=parsed_event,
                        perspective=perspective,
                        game_day=request.game_day,
                        write_indexes=request.write_indexes,
                    )
                    recipients_result.append(result)
                except Exception as e:
                    # 单个角色失败不影响其他角色
                    recipients_result.append(CharacterDispatchResult(
                        character_id=character_id,
                        perspective=perspective,
                        node_count=0,
                        edge_count=0,
                        event_description=f"Error: {str(e)}",
                    ))

        return NaturalEventIngestResponse(
            event_id=event_id,
            parsed_event=parsed_event,
            gm_node_count=len(gm_nodes),
            gm_edge_count=len(gm_edges),
            dispatched=request.distribute,
            recipients=recipients_result,
        )

    def _determine_perspectives(
        self,
        parsed_event: Dict[str, Any],
        known_characters: List[str],
        character_locations: Dict[str, str],
    ) -> Dict[str, str]:
        """
        确定每个角色的视角类型

        规则：
        - 参与者 -> "participant"
        - 目击者 -> "witness"
        - 同一地点的其他角色 -> "bystander"
        - 其他已知角色（暂不分发，可扩展为"rumor"）
        """
        perspectives: Dict[str, str] = {}

        participants = set(parsed_event.get("participants", []))
        witnesses = set(parsed_event.get("witnesses", []))
        event_location = parsed_event.get("location")

        # 参与者
        for char_id in participants:
            perspectives[char_id] = "participant"

        # 目击者
        for char_id in witnesses:
            if char_id not in perspectives:
                perspectives[char_id] = "witness"

        # 同一地点的旁观者
        if event_location and character_locations:
            for char_id, location in character_locations.items():
                if location == event_location and char_id not in perspectives:
                    perspectives[char_id] = "bystander"

        return perspectives

    async def ingest_for_character(
        self,
        world_id: str,
        character_id: str,
        event_description: str,
        parsed_event: Dict[str, Any],
        perspective: str,
        game_day: int,
        write_indexes: bool = False,
        source_character: Optional[str] = None,
    ) -> CharacterDispatchResult:
        """写入单个角色的事件记忆（LLM 视角转换）"""
        return await self._dispatch_to_character(
            world_id=world_id,
            character_id=character_id,
            event_description=event_description,
            parsed_event=parsed_event,
            perspective=perspective,
            game_day=game_day,
            write_indexes=write_indexes,
            source_character=source_character,
        )

    async def _dispatch_to_character(
        self,
        world_id: str,
        character_id: str,
        event_description: str,
        parsed_event: Dict[str, Any],
        perspective: str,
        game_day: int,
        write_indexes: bool = False,
        source_character: Optional[str] = None,
    ) -> CharacterDispatchResult:
        """
        向单个角色分发事件

        Args:
            world_id: 世界ID
            character_id: 角色ID
            event_description: 原始事件描述
            parsed_event: 解析后的事件结构
            perspective: 视角类型
            game_day: 游戏日
            write_indexes: 是否写入索引
            source_character: 传闻来源（如果是rumor视角）

        Returns:
            分发结果
        """
        # 1. 获取角色profile
        profile_data = await self.graph_store.get_character_profile(world_id, character_id)
        profile = CharacterProfile(**profile_data) if profile_data else CharacterProfile(name=character_id)

        # 2. 视角转换
        transformed = await self.llm_service.transform_perspective(
            event_description=event_description,
            parsed_event=parsed_event,
            character_id=character_id,
            character_profile=profile,
            perspective=perspective,  # type: ignore
            game_day=game_day,
            source_character=source_character,
        )

        # 3. 转换为MemoryNode和MemoryEdge
        nodes = [MemoryNode(**n) for n in transformed.get("nodes", [])]
        edges = [MemoryEdge(**e) for e in transformed.get("edges", [])]

        # 4. 写入角色记忆
        ingest_request = EventIngestRequest(
            description=transformed.get("event_description", event_description),
            game_day=game_day,
            location=parsed_event.get("location"),
            perspective=perspective,
            nodes=nodes,
            edges=edges,
            state_updates=transformed.get("state_updates", {}),
            write_indexes=write_indexes,
        )

        result = await self.flash_service.ingest_event(world_id, character_id, ingest_request)

        # 记录实际写入的事件节点 ID，供上游建立 cross-scope perspective_of 边。
        event_node_ids = [
            n.id for n in nodes
            if n.type in {"event", "rumor", "choice"}
        ]

        return CharacterDispatchResult(
            character_id=character_id,
            perspective=perspective,
            node_count=result.node_count,
            edge_count=result.edge_count,
            event_description=transformed.get("event_description"),
            event_node_ids=event_node_ids,
        )

    async def _get_gm_important_nodes(
        self,
        world_id: str,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """获取GM图谱中的重要节点"""
        graph_data = await self.graph_store.load_graph_v2(
            world_id,
            GraphScope.world(),
        )

        if not graph_data or not graph_data.nodes:
            return []

        # 按importance排序，取前N个
        sorted_nodes = sorted(
            graph_data.nodes,
            key=lambda n: n.importance,
            reverse=True
        )

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
