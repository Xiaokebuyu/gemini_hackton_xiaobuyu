"""
Flash-Pro Bridge Service.

Flash 与 Pro 之间的通信桥接，负责：
- 查询理解：Pro 遇到未知记忆时，理解查询意图
- 记忆检索：使用激活扩散从图谱中检索相关记忆
- 记忆翻译：将图谱数据翻译为自然语言注入 Pro
- 上下文还原：需要时从 transcript 中提取完整对话片段
"""
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.models.graph import GraphData
from app.models.graph_elements import MemoryWithContext
from app.models.npc_instance import MemoryInjection, QueryUnderstanding
from app.models.pro import CharacterProfile
from app.services.spreading_activation import (
    SpreadingActivationConfig,
    extract_subgraph,
    spread_activation,
)

if TYPE_CHECKING:
    from app.services.flash_service import FlashService
    from app.services.graph_store import GraphStore
    from app.services.llm_service import LLMService
    from app.services.memory_graph import MemoryGraph


class FlashProBridge:
    """
    Flash 与 Pro 之间的通信桥接

    Pro 请求 Flash 的场景：
    1. 对话中提到未知实体/事件
    2. 需要回忆特定记忆
    3. 场景进入时预加载相关记忆
    """

    def __init__(
        self,
        flash_service: Optional["FlashService"] = None,
        llm_service: Optional["LLMService"] = None,
        graph_store: Optional["GraphStore"] = None,
    ):
        """
        初始化桥接服务

        Args:
            flash_service: Flash 服务（可选）
            llm_service: LLM 服务（可选）
            graph_store: 图谱存储服务（可选）
        """
        self._flash_service = flash_service
        self._llm_service = llm_service
        self._graph_store = graph_store

    @property
    def flash_service(self) -> "FlashService":
        """懒加载 Flash 服务"""
        if self._flash_service is None:
            from app.services.flash_service import FlashService
            self._flash_service = FlashService()
        return self._flash_service

    @property
    def llm_service(self) -> "LLMService":
        """懒加载 LLM 服务"""
        if self._llm_service is None:
            from app.services.llm_service import LLMService
            self._llm_service = LLMService()
        return self._llm_service

    @property
    def graph_store(self) -> "GraphStore":
        """懒加载图谱存储"""
        if self._graph_store is None:
            from app.services.graph_store import GraphStore
            self._graph_store = GraphStore()
        return self._graph_store

    # ==================== 核心方法 ====================

    async def pro_requests_memory(
        self,
        world_id: str,
        npc_id: str,
        query: str,
        conversation_context: Optional[str] = None,
        npc_profile: Optional[CharacterProfile] = None,
        activation_config: Optional[SpreadingActivationConfig] = None,
    ) -> MemoryInjection:
        """
        Pro 向 Flash 请求记忆

        这是主要的入口方法，完成从查询到记忆注入的完整流程。

        Args:
            world_id: 世界 ID
            npc_id: NPC ID
            query: 查询文本（通常是 Pro 生成的记忆请求）
            conversation_context: 当前对话上下文（可选）
            npc_profile: NPC 配置（可选）
            activation_config: 激活扩散配置（可选）

        Returns:
            MemoryInjection 包含翻译后的记忆文本
        """
        # 1. 获取 NPC 配置
        if npc_profile is None:
            profile_data = await self.graph_store.get_character_profile(world_id, npc_id)
            npc_profile = CharacterProfile(**profile_data) if profile_data else CharacterProfile(name=npc_id)

        # 2. Flash 理解查询意图
        query_understanding = await self.understand_query(
            world_id=world_id,
            npc_id=npc_id,
            query=query,
            conversation_context=conversation_context,
        )

        if not query_understanding.seed_nodes:
            return MemoryInjection(
                text="（没有找到相关的记忆）",
                source_nodes=[],
                confidence=0.0,
                query_intent=query_understanding.intent,
            )

        # 3. 激活扩散检索
        activated_nodes = await self.spread_activation_search(
            world_id=world_id,
            npc_id=npc_id,
            seed_nodes=query_understanding.seed_nodes,
            config=activation_config,
        )

        if not activated_nodes:
            return MemoryInjection(
                text="（记忆模糊，无法想起更多细节）",
                source_nodes=query_understanding.seed_nodes,
                confidence=0.3,
                query_intent=query_understanding.intent,
            )

        # 4. 提取子图
        subgraph = await self.extract_activated_subgraph(
            world_id=world_id,
            npc_id=npc_id,
            activated_nodes=activated_nodes,
        )

        # 5. 翻译为自然语言
        memory_text = await self.translate_memory(
            subgraph=subgraph,
            activated_nodes=activated_nodes,
            npc_profile=npc_profile,
            query_intent=query_understanding.intent,
        )

        # 6. 可选：提取完整上下文
        full_context = await self._extract_full_context(
            subgraph=subgraph,
            activated_nodes=activated_nodes,
        )

        return MemoryInjection(
            text=memory_text,
            source_nodes=list(activated_nodes.keys()),
            confidence=query_understanding.confidence,
            query_intent=query_understanding.intent,
            full_context=full_context,
        )

    async def understand_query(
        self,
        world_id: str,
        npc_id: str,
        query: str,
        conversation_context: Optional[str] = None,
    ) -> QueryUnderstanding:
        """
        理解查询意图并识别种子节点

        Args:
            world_id: 世界 ID
            npc_id: NPC ID
            query: 查询文本
            conversation_context: 对话上下文

        Returns:
            QueryUnderstanding 包含意图和种子节点
        """
        # 获取可用节点
        available_nodes = await self._get_all_nodes_summary(world_id, npc_id)

        if not available_nodes:
            return QueryUnderstanding(
                intent="无法理解查询（图谱为空）",
                seed_nodes=[],
                confidence=0.0,
            )

        # 按类型分组节点
        nodes_by_type = self._group_nodes_by_type(available_nodes)

        # 构建上下文部分
        context_section = ""
        if conversation_context:
            context_section = f"""## 当前对话上下文
{conversation_context}

"""

        prompt = f"""# 角色: 记忆检索引导器

你是角色的潜意识。意识层需要回忆某些事情，你负责理解需要检索什么。

{context_section}## 意识层的请求
"{query}"

## 图谱中存在的节点

### 事件组（完整对话记录）
{self._format_node_list(nodes_by_type.get('event_group', []))}

### 事件（具体事件点）
{self._format_node_list(nodes_by_type.get('event', []))}

### 人物
{self._format_node_list(nodes_by_type.get('person', []))}

### 地点
{self._format_node_list(nodes_by_type.get('location', []))}

### 其他
{self._format_node_list(nodes_by_type.get('other', []))}

## 输出要求

```json
{{
  "seed_nodes": ["从上面的节点中选择最相关的节点ID"],
  "intent": "简短描述检索意图",
  "confidence": 0.0到1.0,
  "search_scope": "recent 或 all 或 specific"
}}
```

## 分析要点

1. 请求中提到了什么实体？映射到已有节点
2. 是在问事实、感受还是具体对话？
3. 如果提到具体的对话，优先选择 event_group 节点
4. seed_nodes 必须是上面列出的实际存在的节点ID
5. 最多选择 5 个最相关的种子节点

只返回JSON，不要其他内容。"""

        result = await self.llm_service.generate_json(prompt)

        if not result:
            return QueryUnderstanding(
                intent="无法理解查询",
                seed_nodes=[],
                confidence=0.0,
            )

        # 验证种子节点存在
        valid_node_ids = {n.get("id") for n in available_nodes}
        seed_nodes = [
            nid for nid in result.get("seed_nodes", [])
            if nid in valid_node_ids
        ]

        return QueryUnderstanding(
            intent=result.get("intent", ""),
            seed_nodes=seed_nodes,
            confidence=float(result.get("confidence", 0.5)),
            search_scope=result.get("search_scope", "all"),
        )

    async def spread_activation_search(
        self,
        world_id: str,
        npc_id: str,
        seed_nodes: List[str],
        config: Optional[SpreadingActivationConfig] = None,
    ) -> Dict[str, float]:
        """
        执行激活扩散检索

        Args:
            world_id: 世界 ID
            npc_id: NPC ID
            seed_nodes: 种子节点列表
            config: 激活扩散配置

        Returns:
            激活的节点及其激活值
        """
        from app.services.memory_graph import MemoryGraph

        # 加载图谱
        graph_data = await self.graph_store.load_graph(world_id, "character", npc_id)
        if not graph_data or not graph_data.nodes:
            return {}

        graph = MemoryGraph.from_graph_data(graph_data)

        # 默认配置
        if config is None:
            config = SpreadingActivationConfig(
                max_iterations=3,
                decay=0.6,
                fire_threshold=0.1,
                output_threshold=0.15,
                lateral_inhibition=True,
                inhibition_factor=0.2,
            )

        # 执行激活扩散
        activated = spread_activation(graph, seed_nodes, config)

        return activated

    async def extract_activated_subgraph(
        self,
        world_id: str,
        npc_id: str,
        activated_nodes: Dict[str, float],
    ) -> GraphData:
        """
        提取激活的子图

        Args:
            world_id: 世界 ID
            npc_id: NPC ID
            activated_nodes: 激活的节点

        Returns:
            GraphData 子图数据
        """
        from app.services.memory_graph import MemoryGraph

        graph_data = await self.graph_store.load_graph(world_id, "character", npc_id)
        if not graph_data:
            return GraphData(nodes=[], edges=[])

        graph = MemoryGraph.from_graph_data(graph_data)
        subgraph = extract_subgraph(graph, activated_nodes)

        return subgraph.to_graph_data()

    async def translate_memory(
        self,
        subgraph: GraphData,
        activated_nodes: Dict[str, float],
        npc_profile: CharacterProfile,
        query_intent: str,
    ) -> str:
        """
        将子图翻译为自然语言记忆

        Args:
            subgraph: 子图数据
            activated_nodes: 激活的节点
            npc_profile: NPC 配置
            query_intent: 查询意图

        Returns:
            自然语言记忆描述
        """
        if not subgraph or not subgraph.nodes:
            return "（没有相关的记忆）"

        # 格式化子图
        subgraph_desc = self._format_subgraph_for_translation(subgraph, activated_nodes)

        prompt = f"""# 角色: 记忆叙述者

你是{npc_profile.name}。以下是你刚刚"想起来"的记忆片段，请用第一人称、符合你性格的方式复述出来。

## 你的性格
{npc_profile.personality or '普通人'}

## 你的说话风格
{npc_profile.speech_pattern or '正常说话'}

## 检索意图
{query_intent}

## 激活的记忆网络
{subgraph_desc}

## 输出要求

用自然的方式描述你想起了什么。要求：
1. 第一人称
2. 符合你的说话风格和性格
3. 按相关度组织（最相关的先说）
4. 如果有完整对话记录（transcript），可以引用具体对话内容
5. 情感色彩要体现出来
6. 不要像在读报告，要像在回忆
7. 适当的长度（50-200字）

直接输出叙述文本，不需要JSON。这段文字会被注入到你的意识中作为记忆参考。"""

        result = await self.llm_service.generate_simple(prompt)
        return result.strip()

    async def _extract_full_context(
        self,
        subgraph: GraphData,
        activated_nodes: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """
        从子图中提取完整的对话上下文

        当检索到 event_group 节点时，提取其中的 transcript
        """
        if not subgraph or not subgraph.nodes:
            return []

        context_snippets = []

        # 按激活值排序节点
        sorted_node_ids = sorted(
            activated_nodes.keys(),
            key=lambda x: activated_nodes[x],
            reverse=True
        )

        for node_id in sorted_node_ids[:5]:  # 最多5个
            for node in subgraph.nodes:
                if node.id != node_id:
                    continue

                # 检查是否有 transcript
                props = node.properties or {}

                if node.type == "event_group" and "transcript" in props:
                    context_snippets.append({
                        "type": "event_group",
                        "id": node.id,
                        "summary": props.get("summary", node.name),
                        "transcript": props.get("transcript", []),
                        "day": props.get("day"),
                        "activation": activated_nodes[node_id],
                    })
                elif node.type == "event" and "transcript_snippet" in props:
                    context_snippets.append({
                        "type": "event",
                        "id": node.id,
                        "summary": props.get("summary", node.name),
                        "transcript_snippet": props.get("transcript_snippet", []),
                        "day": props.get("day"),
                        "activation": activated_nodes[node_id],
                    })

        return context_snippets

    # ==================== 预加载方法 ====================

    async def preload_scene_memory(
        self,
        world_id: str,
        npc_id: str,
        scene_description: str,
        other_characters: List[str],
        npc_profile: Optional[CharacterProfile] = None,
    ) -> MemoryInjection:
        """
        场景进入时预加载相关记忆

        Args:
            world_id: 世界 ID
            npc_id: NPC ID
            scene_description: 场景描述
            other_characters: 场景中其他角色
            npc_profile: NPC 配置

        Returns:
            MemoryInjection 包含场景相关记忆
        """
        # 构建预加载查询
        query = f"我来到了 {scene_description}"
        if other_characters:
            query += f"，在这里我看到了 {', '.join(other_characters)}"
        query += "。让我回想一下与这里、这些人相关的事情。"

        return await self.pro_requests_memory(
            world_id=world_id,
            npc_id=npc_id,
            query=query,
            npc_profile=npc_profile,
            activation_config=SpreadingActivationConfig(
                max_iterations=2,  # 较浅的搜索
                decay=0.5,
                output_threshold=0.2,
            ),
        )

    # ==================== 辅助方法 ====================

    async def _get_all_nodes_summary(
        self,
        world_id: str,
        npc_id: str,
    ) -> List[Dict[str, Any]]:
        """获取所有节点摘要"""
        graph_data = await self.graph_store.load_graph(world_id, "character", npc_id)

        if not graph_data or not graph_data.nodes:
            return []

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

    def _group_nodes_by_type(
        self,
        nodes: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict]]:
        """按类型分组节点"""
        groups: Dict[str, List[Dict]] = {
            "event_group": [],
            "event": [],
            "person": [],
            "location": [],
            "other": [],
        }

        for node in nodes:
            node_type = node.get("type", "")
            if node_type in groups:
                groups[node_type].append(node)
            else:
                groups["other"].append(node)

        return groups

    def _format_node_list(self, nodes: List[Dict[str, Any]]) -> str:
        """格式化节点列表"""
        if not nodes:
            return "（无）"

        lines = []
        for node in nodes[:15]:  # 每类最多15个
            node_id = node.get("id", "unknown")
            node_name = node.get("name", "unknown")
            props = node.get("properties", {})
            extra = ""
            if "day" in props:
                extra = f" [第{props['day']}天]"
            if "summary" in props:
                summary = props["summary"][:50] + "..." if len(props.get("summary", "")) > 50 else props.get("summary", "")
                extra += f" - {summary}"
            lines.append(f"- {node_id}: {node_name}{extra}")

        if len(nodes) > 15:
            lines.append(f"... 还有 {len(nodes) - 15} 个")

        return "\n".join(lines)

    def _format_subgraph_for_translation(
        self,
        subgraph: GraphData,
        activated_nodes: Dict[str, float],
    ) -> str:
        """格式化子图供翻译使用"""
        if not subgraph or not subgraph.nodes:
            return "（无记忆被激活）"

        lines = []

        # 按激活值排序节点
        sorted_nodes = sorted(
            subgraph.nodes,
            key=lambda n: activated_nodes.get(n.id, 0),
            reverse=True
        )

        for node in sorted_nodes[:10]:
            activation = activated_nodes.get(node.id, 0)
            props = node.properties or {}

            if node.type == "event_group":
                summary = props.get("summary", node.name)
                day = props.get("day", "?")
                emotion = props.get("emotion", "")
                lines.append(f"### [{activation:.2f}] 对话回忆（第{day}天）")
                lines.append(f"摘要: {summary}")
                if emotion:
                    lines.append(f"情绪: {emotion}")

                # 添加部分 transcript
                transcript = props.get("transcript", [])
                if transcript:
                    lines.append("对话片段:")
                    for msg in transcript[:5]:
                        lines.append(f"  {msg.get('role', '?')}: {msg.get('content', '')[:100]}")
                    if len(transcript) > 5:
                        lines.append(f"  ... 还有 {len(transcript) - 5} 条对话")
                lines.append("")

            elif node.type == "event":
                summary = props.get("summary", node.name)
                day = props.get("day", "?")
                emotion = props.get("emotion", "")
                lines.append(f"### [{activation:.2f}] 事件（第{day}天）: {node.name}")
                lines.append(f"描述: {summary}")
                if emotion:
                    lines.append(f"情绪: {emotion}")
                lines.append("")

            elif node.type == "person":
                lines.append(f"### [{activation:.2f}] 人物: {node.name}")
                if "relation" in props:
                    lines.append(f"关系: {props['relation']}")
                lines.append("")

            else:
                lines.append(f"### [{activation:.2f}] {node.type}: {node.name}")
                lines.append("")

        # 添加关系信息
        if subgraph.edges:
            lines.append("### 关联关系")
            for edge in subgraph.edges[:8]:
                lines.append(f"- {edge.source} --{edge.relation}--> {edge.target}")

        return "\n".join(lines)
