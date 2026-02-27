"""
Memory Graphizer Service.

将工作记忆（对话/事件）转换为图谱节点和边，支持：
- 层级事件结构（event_group -> event）
- 完整对话记录保存
- 使用 Flash 进行结构化提取
- 合并到角色记忆图谱
"""
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.exceptions import CATCHABLE_EXCEPTIONS
from app.models.context_window import GraphizeRequest, WindowMessage
from app.models.graph_elements import (
    EventGroupNode,
    EventNode,
    ExtractedElements,
    GraphEdgeSpec,
    GraphExtractionPrompt,
    GraphizeResult,
    MergeResult,
    TranscriptMessage,
    TranscriptRange,
)
from app.models.character_profile import CharacterProfile

if TYPE_CHECKING:
    from app.services.llm_service import LLMService


class MemoryGraphizer:
    """
    将工作记忆转换为图谱

    功能：
    1. 将消息序列切分为事件组
    2. 使用 Flash/LLM 提取结构化数据
    3. 创建层级事件结构（event_group -> event）
    4. 合并到角色记忆图谱
    5. 持久化到 Firestore
    """

    MODE_SESSION_HISTORY = "session_history"

    def __init__(
        self,
        llm_service: Optional["LLMService"] = None,
    ):
        """
        初始化图谱化器

        Args:
            llm_service: LLM 服务（可选）
        """
        self._llm_service = llm_service

    @property
    def llm_service(self) -> "LLMService":
        """懒加载 LLM 服务"""
        if self._llm_service is None:
            from app.services.llm_service import LLMService
            self._llm_service = LLMService()
        return self._llm_service

    async def graphize(
        self,
        request: GraphizeRequest,
        npc_profile: Optional[CharacterProfile] = None,
        existing_nodes: Optional[List[Dict[str, Any]]] = None,
        mode: str = MODE_SESSION_HISTORY,
        world_graph=None,
    ) -> GraphizeResult:
        """
        将消息序列图谱化

        Args:
            request: 图谱化请求
            npc_profile: NPC 配置（可选）
            existing_nodes: 已有的重要节点（可选）

        Returns:
            GraphizeResult 包含图谱化结果
        """
        start_time = time.time()

        if not request.messages:
            return GraphizeResult(
                success=True,
                messages_processed=0,
            )
        if world_graph is None:
            return GraphizeResult(
                success=False,
                error="world_graph is required",
                messages_processed=len(request.messages),
            )

        try:

            # 1. 获取 NPC 配置
            if npc_profile is None:
                npc_node = world_graph.get_node(request.npc_id)
                npc_name = npc_node.name if npc_node else request.npc_id
                npc_profile = CharacterProfile(name=npc_name)

            # 2. 获取已有的重要节点 (L3 read, delegated to graph_writer)
            from app.world.memory.graph_writer import get_context_nodes, merge_extraction
            if existing_nodes is None:
                existing_nodes = get_context_nodes(
                    world_graph, request.npc_id, limit=50,
                    current_scene=request.current_scene,
                )

            # 3. 转换消息为对话记录格式
            transcript = self._messages_to_transcript(request.messages)

            # 4. 调用 LLM 提取结构化数据
            extraction = await self._extract_graph_elements(
                transcript=transcript,
                npc_id=request.npc_id,
                npc_profile=npc_profile,
                game_day=request.game_day,
                location=request.current_scene,
                existing_nodes=existing_nodes,
            )

            # 5. 合并到图谱 (L3 write, delegated to graph_writer)
            merge_result = merge_extraction(
                world_graph=world_graph,
                npc_id=request.npc_id,
                extraction=extraction,
            )

            processing_time_ms = int((time.time() - start_time) * 1000)

            return GraphizeResult(
                success=True,
                nodes_added=merge_result.new_nodes,
                nodes_updated=merge_result.updated_nodes,
                event_groups_created=1 if extraction.event_group else 0,
                sub_events_created=len(extraction.sub_events),
                edges_added=merge_result.new_edges,
                messages_processed=len(request.messages),
                tokens_processed=sum(m.token_count for m in request.messages),
                created_node_ids=merge_result.new_node_ids,
                processing_time_ms=processing_time_ms,
            )

        except CATCHABLE_EXCEPTIONS as e:
            return GraphizeResult(
                success=False,
                error=str(e),
                messages_processed=len(request.messages),
            )

    def _messages_to_transcript(
        self,
        messages: List[WindowMessage],
    ) -> List[TranscriptMessage]:
        """将窗口消息转换为对话记录格式"""
        transcript = []
        for msg in messages:
            role = msg.role
            # 将 assistant 转换为 NPC 角色
            if role == "assistant":
                role = "npc"
            elif role == "user":
                role = "player"

            transcript.append(TranscriptMessage(
                role=role,
                content=msg.content,
                timestamp=msg.timestamp,
                metadata=msg.metadata,
            ))
        return transcript

    async def _extract_graph_elements(
        self,
        transcript: List[TranscriptMessage],
        npc_id: str,
        npc_profile: CharacterProfile,
        game_day: int,
        location: Optional[str],
        existing_nodes: List[Dict[str, Any]],
    ) -> ExtractedElements:
        """
        使用 LLM 从对话中提取图谱元素

        Args:
            transcript: 对话记录
            npc_id: NPC ID
            npc_profile: NPC 配置
            game_day: 游戏日
            location: 当前场景
            existing_nodes: 已有节点

        Returns:
            ExtractedElements 包含提取的元素
        """
        # 格式化对话记录
        transcript_text = self._format_transcript(transcript)

        # 格式化已有节点
        nodes_reference = self._format_nodes_for_prompt(existing_nodes)

        prompt = f"""# 任务：从对话中提取层级事件结构

你是{npc_profile.name}的潜意识记忆系统。你的任务是将这段对话编码为结构化的记忆。

## 当前角色信息
- 名字: {npc_profile.name}
- 职业: {npc_profile.occupation or '未知'}
- 性格: {npc_profile.personality or '未知'}
- 视角: 第一人称（从我的视角记录）

## 输入对话
{transcript_text}

## 当前上下文
- 游戏日: 第{game_day}天
- 地点: {location or '未知'}

## 已有的重要节点（优先引用，不要重复创建）
{nodes_reference}

**重要**: 如果对话中提到的人物/地点/组织已存在于以上节点列表中（无论是角色节点还是世界通识节点），请直接引用其 ID 建立边连接，不要创建新节点。特别是世界通识节点中的地点和人物，请通过 edges 建立 located_in、mentions、concerns 等关系，这样你的记忆才能与世界知识相连。

## 输出要求

请分析这段对话，输出JSON格式的记忆结构。

1. 创建一个 event_group 节点表示整个对话
2. 识别对话中的关键事件点，创建 event 子节点
3. 每个子事件标记对应的对话片段索引
4. 识别提到的新实体（人物、地点等）
5. 建立与已有节点的关联

节点类型(type)可选值: event_group, event, person, location, item, knowledge, rumor, goal, emotion, organization
关系类型(relation)可选值: part_of, caused, followed_by, participated, witnessed, located_in, mentions, concerns, affects, knows, trusts, distrusts, allied_with, enemy_of, owns, wants

```json
{{
  "event_group": {{
    "id": "event_group_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    "name": "简短描述整个对话主题",
    "importance": 0.5,
    "day": {game_day},
    "location": "{location or ''}",
    "summary": "从我的视角总结这段对话（第一人称，2-3句话）",
    "emotion": "整体情绪基调",
    "participants": ["player", "其他参与者ID"]
  }},
  "sub_events": [
    {{
      "id": "event_{datetime.now().strftime('%Y%m%d')}_描述",
      "name": "子事件名",
      "importance": 0.0-1.0,
      "day": {game_day},
      "summary": "从我的视角描述这个事件（第一人称）",
      "emotion": "当时的情绪",
      "participants": ["参与者ID"],
      "transcript_range": {{
        "start_idx": 0,
        "end_idx": 2
      }}
    }}
  ],
  "new_nodes": [
    {{
      "id": "person_xxx 或 location_xxx 等",
      "type": "person/location/item/etc",
      "name": "名称",
      "importance": 0.0-1.0,
      "properties": {{}}
    }}
  ],
  "edges": [
    {{"id": "edge_xxx", "source": "event_group_id", "target": "event_id", "relation": "part_of", "weight": 1.0}},
    {{"id": "edge_xxx", "source": "event_id", "target": "person_id", "relation": "participated", "weight": 0.8}}
  ],
  "state_updates": {{
    "mood": "如果情绪有变化",
    "goals": ["如果目标有变化"]
  }}
}}
```

## 编码原则

1. event_group 保存对话主题和整体情绪
2. 每个 sub_event 是一个具体的事件点（重要的转折、信息、情感变化等）
3. transcript_range 的索引对应对话消息的位置（从0开始）
4. 从我的第一人称视角描述所有 summary
5. 只记录我能感知到的信息
6. 重要度基于这件事对我的影响程度
7. 与已有节点建立合理的关联

只返回JSON，不要其他内容。"""

        result = await self.llm_service.generate_json(prompt)

        if not result:
            # 创建一个基本的事件组
            return self._create_fallback_extraction(
                transcript, npc_id, game_day, location
            )

        # 验证和转换结果
        return self._validate_and_convert_extraction(
            result, transcript, npc_id, game_day, location
        )

    def _format_transcript(self, transcript: List[TranscriptMessage]) -> str:
        """格式化对话记录"""
        lines = []
        for i, msg in enumerate(transcript):
            lines.append(f"[{i}] {msg.role}: {msg.content}")
        return "\n".join(lines)

    def _format_nodes_for_prompt(self, nodes: List[Dict[str, Any]]) -> str:
        """格式化节点列表供prompt使用，区分角色节点和世界通识节点。"""
        if not nodes:
            return "（暂无已有节点）"

        char_lines = []
        world_lines = []
        for node in nodes:
            node_id = node.get("id", "unknown")
            node_type = node.get("type", "unknown")
            node_name = node.get("name", "unknown")
            line = f"- {node_id} ({node_type}): {node_name}"
            if node.get("_scope") == "world":
                world_lines.append(line)
            else:
                char_lines.append(line)

        parts = []
        if char_lines:
            parts.append("### 角色已有节点（你的记忆）")
            parts.extend(char_lines[:30])
            if len(char_lines) > 30:
                parts.append(f"... 还有 {len(char_lines) - 30} 个节点")
        if world_lines:
            parts.append("### 世界通识节点（地点、组织、人物等公共知识）")
            parts.extend(world_lines[:20])
            if len(world_lines) > 20:
                parts.append(f"... 还有 {len(world_lines) - 20} 个节点")

        return "\n".join(parts)

    def _create_fallback_extraction(
        self,
        transcript: List[TranscriptMessage],
        npc_id: str,
        game_day: int,
        location: Optional[str],
    ) -> ExtractedElements:
        """创建降级的提取结果"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        event_group = EventGroupNode(
            id=f"event_group_{timestamp}",
            name="对话记录",
            importance=0.5,
            day=game_day,
            location=location,
            summary="与玩家进行了一段对话",
            emotion="neutral",
            participants=["player"],
            transcript=transcript,
            message_count=len(transcript),
            token_count=sum(len(m.content) // 4 for m in transcript),
        )

        return ExtractedElements(event_group=event_group)

    def _validate_and_convert_extraction(
        self,
        result: Dict[str, Any],
        transcript: List[TranscriptMessage],
        npc_id: str,
        game_day: int,
        location: Optional[str],
    ) -> ExtractedElements:
        """验证并转换 LLM 输出为 ExtractedElements"""
        # 处理 event_group
        event_group_data = result.get("event_group", {})
        event_group = None

        if event_group_data:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            event_group = EventGroupNode(
                id=event_group_data.get("id", f"event_group_{timestamp}"),
                name=event_group_data.get("name", "对话记录"),
                importance=float(event_group_data.get("importance", 0.5)),
                day=event_group_data.get("day", game_day),
                location=event_group_data.get("location", location),
                summary=event_group_data.get("summary", ""),
                emotion=event_group_data.get("emotion"),
                participants=event_group_data.get("participants", ["player"]),
                transcript=transcript,
                message_count=len(transcript),
                token_count=sum(len(m.content) // 4 for m in transcript),
            )

        # 处理 sub_events
        sub_events = []
        for ev_data in result.get("sub_events", []):
            if not isinstance(ev_data, dict):
                continue

            # 处理 transcript_range
            tr_data = ev_data.get("transcript_range", {})
            transcript_range = None
            if tr_data and event_group:
                transcript_range = TranscriptRange(
                    parent_id=event_group.id,
                    start_idx=tr_data.get("start_idx", 0),
                    end_idx=tr_data.get("end_idx", len(transcript) - 1),
                )

                # 提取片段
                start = transcript_range.start_idx
                end = min(transcript_range.end_idx + 1, len(transcript))
                snippet = transcript[start:end]
            else:
                snippet = []

            sub_events.append(EventNode(
                id=ev_data.get("id", f"event_{uuid.uuid4().hex[:8]}"),
                name=ev_data.get("name", "事件"),
                importance=float(ev_data.get("importance", 0.5)),
                day=ev_data.get("day", game_day),
                summary=ev_data.get("summary", ""),
                emotion=ev_data.get("emotion"),
                participants=ev_data.get("participants", []),
                transcript_range=transcript_range,
                transcript_snippet=snippet,
            ))

        # 处理 new_nodes
        new_nodes = []
        for node_data in result.get("new_nodes", []):
            if not isinstance(node_data, dict):
                continue
            if not node_data.get("id") or not node_data.get("type"):
                continue
            new_nodes.append(node_data)

        # 处理 edges
        edges = []
        for edge_data in result.get("edges", []):
            if not isinstance(edge_data, dict):
                continue
            if not edge_data.get("source") or not edge_data.get("target"):
                continue

            edges.append(GraphEdgeSpec(
                id=edge_data.get("id", f"edge_{uuid.uuid4().hex[:8]}"),
                source=edge_data.get("source"),
                target=edge_data.get("target"),
                relation=edge_data.get("relation", "related_to"),
                weight=float(edge_data.get("weight", 1.0)),
                properties=edge_data.get("properties", {}),
            ))

        # 处理 state_updates
        state_updates = result.get("state_updates", {})
        if state_updates:
            state_updates = {
                k: v for k, v in state_updates.items()
                if v is not None and v != "" and v != []
            }

        return ExtractedElements(
            event_group=event_group,
            sub_events=sub_events,
            new_nodes=new_nodes,
            edges=edges,
            state_updates=state_updates,
        )

    # NOTE: _extract_metadata, _merge_to_world_graph, _get_important_nodes_from_wg
    # have been extracted to app/world/memory/graph_writer.py (L3 layer purification).
