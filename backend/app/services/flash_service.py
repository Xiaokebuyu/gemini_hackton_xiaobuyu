"""
Flash service for single-character memory operations.

支持两种模式：
1. 结构化模式：直接传入节点/边数据
2. 自然语言模式：通过LLM将自然语言转换为结构化数据
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.models.flash import (
    EventIngestRequest,
    EventIngestResponse,
    NaturalEventIngestRequest,
    NaturalEventIngestResponse,
    NaturalRecallRequest,
    NaturalRecallResponse,
    RecallRequest,
    RecallResponse,
)
from app.models.graph import MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope
from app.models.pro import CharacterProfile
from app.services.graph_schema import GraphSchemaOptions, validate_edge, validate_node
from app.services.graph_store import GraphStore
from app.services.memory_graph import MemoryGraph
from app.services.reference_resolver import ReferenceResolver
from app.services.spreading_activation import (
    SpreadingActivationConfig,
    extract_subgraph,
    spread_activation,
)


class FlashService:
    """Character-level memory service."""

    def __init__(
        self,
        graph_store: Optional[GraphStore] = None,
        reference_resolver: Optional[ReferenceResolver] = None,
    ) -> None:
        self.graph_store = graph_store or GraphStore()
        self.reference_resolver = reference_resolver or ReferenceResolver(self.graph_store)
        self._llm_service: Optional["FlashLLMService"] = None

    @property
    def llm_service(self) -> "FlashLLMService":
        """懒加载LLM服务"""
        if self._llm_service is None:
            from app.services.flash_llm_service import FlashLLMService
            self._llm_service = FlashLLMService()
        return self._llm_service

    async def ingest_event(
        self,
        world_id: str,
        character_id: str,
        request: EventIngestRequest,
    ) -> EventIngestResponse:
        event_id = request.event_id or f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        node_count = 0
        edge_count = 0
        char_scope = GraphScope.character(character_id)

        nodes = list(request.nodes)
        node_ids = {node.id for node in nodes}
        if request.edges:
            nodes = self._ensure_edge_nodes(nodes, request.edges)
            node_ids = {node.id for node in nodes}

        if nodes:
            for node in nodes:
                if request.validate_input:
                    options = GraphSchemaOptions(
                        allow_unknown_node_types=not request.strict,
                        allow_unknown_relations=True,
                        validate_event_properties=request.strict,
                    )
                    errors = validate_node(node, options)
                    if errors:
                        raise ValueError(f"Invalid node {node.id}: {errors}")
                await self.graph_store.upsert_node_v2(
                    world_id=world_id,
                    scope=char_scope,
                    node=node,
                )
                node_count += 1

        if request.edges:
            for edge in request.edges:
                if request.validate_input:
                    options = GraphSchemaOptions(
                        allow_unknown_node_types=True,
                        allow_unknown_relations=not request.strict,
                        validate_event_properties=False,
                    )
                    errors = validate_edge(edge, None, options)
                    if errors:
                        raise ValueError(f"Invalid edge {edge.id}: {errors}")
                await self.graph_store.upsert_edge_v2(
                    world_id=world_id,
                    scope=char_scope,
                    edge=edge,
                )
                edge_count += 1

        state_updated = False
        if request.state_updates:
            await self.graph_store.update_character_state(
                world_id,
                character_id,
                request.state_updates,
            )
            state_updated = True

        note = None
        if node_count == 0 and edge_count == 0:
            note = "no structured memory provided"

        return EventIngestResponse(
            event_id=event_id,
            node_count=node_count,
            edge_count=edge_count,
            state_updated=state_updated,
            note=note,
        )

    def _ensure_edge_nodes(self, nodes, edges):
        node_ids = {node.id for node in nodes}
        from app.models.graph import MemoryNode

        def infer_type(node_id: str) -> str:
            if node_id.startswith("person_"):
                return "person"
            if node_id.startswith("location_"):
                return "location"
            if node_id.startswith("item_"):
                return "item"
            return "unknown"

        for edge in edges:
            for node_id in (edge.source, edge.target):
                if node_id not in node_ids:
                    nodes.append(
                        MemoryNode(
                            id=node_id,
                            type=infer_type(node_id),
                            name=node_id,
                            importance=0.0,
                            properties={
                                "placeholder": True,
                                "_placeholder_source": "edge_auto_create",
                            },
                        )
                    )
                    node_ids.add(node_id)
        return nodes

    async def recall_memory(
        self,
        world_id: str,
        character_id: str,
        request: RecallRequest,
    ) -> RecallResponse:
        if not request.seed_nodes:
            return RecallResponse(seed_nodes=[], activated_nodes={}, subgraph=None, used_subgraph=False)

        if request.use_subgraph:
            graph_data = await self.graph_store.load_local_subgraph_v2(
                world_id=world_id,
                scope=GraphScope.character(character_id),
                seed_nodes=request.seed_nodes,
                depth=request.subgraph_depth,
                direction=request.subgraph_direction,
            )
            used_subgraph = True
        else:
            graph_data = await self.graph_store.load_graph_v2(
                world_id,
                GraphScope.character(character_id),
            )
            used_subgraph = False

        graph = MemoryGraph.from_graph_data(graph_data)
        config = request.config or SpreadingActivationConfig()
        activated = spread_activation(graph, request.seed_nodes, config)

        # Filter out placeholder nodes from activation results
        activated = {
            nid: score for nid, score in activated.items()
            if not (graph.get_node(nid) and (graph.get_node(nid).properties or {}).get("placeholder", False))
        }

        subgraph = None
        if request.include_subgraph:
            subgraph_graph = extract_subgraph(graph, activated)
            subgraph = subgraph_graph.to_graph_data()
            # Remove placeholder nodes from subgraph
            subgraph.nodes = [
                n for n in subgraph.nodes
                if not (n.properties or {}).get("placeholder", False)
            ]
            if request.resolve_refs:
                subgraph = await self.reference_resolver.resolve_graph_data(world_id, subgraph)

        return RecallResponse(
            seed_nodes=request.seed_nodes,
            activated_nodes=activated,
            subgraph=subgraph,
            used_subgraph=used_subgraph,
        )

    # ==================== LLM增强方法 ====================

    async def ingest_event_natural(
        self,
        world_id: str,
        character_id: str,
        request: NaturalEventIngestRequest,
    ) -> NaturalEventIngestResponse:
        """
        LLM增强的事件摄入：自然语言 → 结构化记忆

        Args:
            world_id: 世界ID
            character_id: 角色ID
            request: 自然语言事件请求

        Returns:
            摄入结果，包含编码后的节点和边
        """
        # 1. 获取角色profile
        profile_data = await self.graph_store.get_character_profile(world_id, character_id)
        profile = CharacterProfile(**profile_data) if profile_data else CharacterProfile(name=character_id)

        # 2. 获取已有的重要节点（供引用）
        existing_nodes = await self._get_important_nodes(world_id, character_id, limit=30)

        # 3. 调用LLM编码事件
        encoded = await self.llm_service.encode_event(
            event_description=request.event_description,
            character_profile=profile,
            existing_nodes=existing_nodes,
            game_day=request.game_day,
        )

        # 4. 转换为MemoryNode和MemoryEdge
        nodes = [MemoryNode(**n) for n in encoded.get("new_nodes", [])]
        edges = [MemoryEdge(**e) for e in encoded.get("new_edges", [])]

        # 5. 调用现有的ingest_event写入
        ingest_request = EventIngestRequest(
            description=request.event_description,
            game_day=request.game_day,
            location=request.location,
            perspective=request.perspective or "first_person",
            nodes=nodes,
            edges=edges,
            state_updates=encoded.get("state_updates", {}),
            write_indexes=request.write_indexes,
        )

        result = await self.ingest_event(world_id, character_id, ingest_request)

        return NaturalEventIngestResponse(
            event_id=result.event_id,
            node_count=result.node_count,
            edge_count=result.edge_count,
            state_updated=result.state_updated,
            encoded_nodes=nodes,
            encoded_edges=edges,
            note=result.note,
        )

    async def recall_memory_natural(
        self,
        world_id: str,
        character_id: str,
        request: NaturalRecallRequest,
    ) -> NaturalRecallResponse:
        """
        LLM增强的记忆检索：自然语言 → 激活扩散 → 自然语言回忆

        Args:
            world_id: 世界ID
            character_id: 角色ID
            request: 自然语言检索请求

        Returns:
            检索结果，可选包含翻译后的自然语言记忆
        """
        # 1. 获取图谱中的可用节点
        available_nodes = await self._get_all_nodes_summary(world_id, character_id)

        if not available_nodes:
            return NaturalRecallResponse(
                query=request.query,
                note="角色图谱为空，无可用记忆",
            )

        # 2. 调用LLM理解查询，获取种子节点
        query_result = await self.llm_service.understand_query(
            query=request.query,
            recent_conversation=request.recent_conversation,
            available_nodes=available_nodes,
        )

        seed_nodes = query_result.get("seed_nodes", [])
        search_intent = query_result.get("search_intent", "")

        if not seed_nodes:
            return NaturalRecallResponse(
                query=request.query,
                search_intent=search_intent,
                note="无法找到相关的记忆节点",
            )

        # 3. 调用现有的recall_memory执行激活扩散
        recall_request = RecallRequest(
            seed_nodes=seed_nodes,
            include_subgraph=request.include_subgraph or request.translate,
            resolve_refs=request.resolve_refs,
            use_subgraph=request.use_subgraph,
            subgraph_depth=request.subgraph_depth,
        )

        recall_result = await self.recall_memory(world_id, character_id, recall_request)

        # 4. 如果需要翻译，调用LLM翻译记忆
        translated_memory = None
        if request.translate and recall_result.subgraph:
            profile_data = await self.graph_store.get_character_profile(world_id, character_id)
            profile = CharacterProfile(**profile_data) if profile_data else CharacterProfile(name=character_id)

            translated_memory = await self.llm_service.translate_memory(
                subgraph=recall_result.subgraph,
                character_profile=profile,
                seed_nodes=seed_nodes,
                activated_nodes=recall_result.activated_nodes,
            )

        return NaturalRecallResponse(
            query=request.query,
            search_intent=search_intent,
            seed_nodes=seed_nodes,
            activated_nodes=recall_result.activated_nodes,
            subgraph=recall_result.subgraph if request.include_subgraph else None,
            translated_memory=translated_memory,
        )

    async def _get_important_nodes(
        self,
        world_id: str,
        character_id: str,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """获取角色图谱中的重要节点"""
        graph_data = await self.graph_store.load_graph_v2(
            world_id,
            GraphScope.character(character_id),
        )

        if not graph_data or not graph_data.nodes:
            return []

        # 按importance排序，取前N个
        sorted_nodes = sorted(
            graph_data.nodes,
            key=lambda n: n.importance,
            reverse=True
        )

        # 转换为字典格式
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

    async def _get_all_nodes_summary(
        self,
        world_id: str,
        character_id: str,
    ) -> List[Dict[str, Any]]:
        """获取角色图谱中的所有节点摘要"""
        graph_data = await self.graph_store.load_graph_v2(
            world_id,
            GraphScope.character(character_id),
        )

        if not graph_data or not graph_data.nodes:
            return []

        # 返回节点的基本信息
        return [
            {
                "id": n.id,
                "type": n.type,
                "name": n.name,
                "importance": n.importance,
                "properties": n.properties,
            }
            for n in graph_data.nodes
        ]
