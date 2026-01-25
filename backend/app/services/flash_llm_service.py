"""
Flash LLM Service - Flash的LLM能力层

负责三个核心任务：
1. 事件编码 (encode_event): 自然语言 → 结构化节点/边
2. 查询理解 (understand_query): 自然语言请求 → 种子节点
3. 记忆翻译 (translate_memory): 子图 → 角色视角自然语言
"""
from typing import Any, Dict, List, Optional

from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.models.pro import CharacterProfile
from app.services.llm_service import LLMService


class FlashLLMService:
    """Flash的LLM能力层"""

    def __init__(self, llm_service: Optional[LLMService] = None) -> None:
        self.llm = llm_service or LLMService()

    async def encode_event(
        self,
        event_description: str,
        character_profile: CharacterProfile,
        existing_nodes: List[Dict[str, Any]],
        game_day: int,
    ) -> Dict[str, Any]:
        """
        事件编码：将自然语言事件描述编码为结构化的记忆节点和关系

        Args:
            event_description: 事件的自然语言描述
            character_profile: 角色信息
            existing_nodes: 已有的重要节点（供引用和连接）
            game_day: 当前游戏日

        Returns:
            {
                "new_nodes": [...],
                "new_edges": [...],
                "state_updates": {...}
            }
        """
        # 格式化已有节点供参考
        nodes_reference = self._format_nodes_for_prompt(existing_nodes)

        prompt = f"""# 角色: 记忆编码器

你是{character_profile.name}的潜意识记忆系统。你的任务是将发生的事件编码为结构化的记忆节点和关系。

## 当前角色信息
- 名字: {character_profile.name}
- 职业: {character_profile.occupation or '未知'}
- 性格: {character_profile.personality or '未知'}

## 已有的重要节点（可以引用这些节点建立连接）
{nodes_reference}

## 需要编码的事件
{event_description}

## 输出要求

请分析这个事件，输出JSON格式的记忆结构。

节点类型(type)可选值: identity, person, location, event, rumor, knowledge, item, organization, goal, emotion
关系类型(relation)可选值: located_in, part_of, owns, works_at, family, friend, enemy, colleague, knows, participated, witnessed, heard_about, caused, believes, suspects, knows_that, likes, fears, trusts, hates, refers_to

```json
{{
  "new_nodes": [
    {{
      "id": "event_xxx",
      "type": "event",
      "name": "简短的事件名",
      "importance": 0.0到1.0,
      "properties": {{
        "day": {game_day},
        "summary": "从我的视角描述这件事（第一人称）",
        "emotion": "我对此的情绪反应"
      }}
    }}
  ],
  "new_edges": [
    {{
      "id": "edge_xxx",
      "source": "节点ID",
      "target": "节点ID",
      "relation": "关系类型",
      "weight": 0.0到1.0
    }}
  ],
  "state_updates": {{
    "mood": "如果情绪有变化填写，否则不填",
    "goals": ["如果目标有变化填写，否则不填"]
  }}
}}
```

## 编码原则

1. 只记录我能感知到的信息（在场/被告知）
2. 用第一人称视角描述 summary
3. 包含情绪反应（这是我的记忆，不是客观记录）
4. 与已有节点建立合理的连接（如果有相关的人物、地点）
5. 重要度基于这件事对我的影响程度
6. 节点ID使用有意义的前缀，如 event_, person_, location_ 等
7. state_updates 只在确实需要更新时才填写，否则留空对象 {{}}

只返回JSON，不要其他内容。"""

        result = await self.llm.generate_json(prompt)

        if not result:
            # 解析失败，返回空结果
            return {
                "new_nodes": [],
                "new_edges": [],
                "state_updates": {}
            }

        # 验证和清理结果
        return self._validate_encode_result(result, game_day)

    async def understand_query(
        self,
        query: str,
        recent_conversation: Optional[str],
        available_nodes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        查询理解：理解Pro的记忆请求，生成种子节点列表

        Args:
            query: Pro的自然语言记忆请求
            recent_conversation: 近期对话上下文
            available_nodes: 图谱中的主要节点列表

        Returns:
            {
                "seed_nodes": ["node_id_1", "node_id_2"],
                "search_intent": "描述检索意图",
                "expected_depth": 2,
                "filters": {...}
            }
        """
        # 按类型整理可用节点
        nodes_by_type = self._group_nodes_by_type(available_nodes)

        conversation_context = ""
        if recent_conversation:
            conversation_context = f"""## 当前对话上下文
{recent_conversation}

"""

        prompt = f"""# 角色: 记忆检索引导器

你是角色的潜意识。当意识层需要回忆某些事情时，你负责理解需要检索什么。

{conversation_context}## 意识层的请求
"{query}"

## 图谱中存在的节点

### 人物节点
{self._format_node_list(nodes_by_type.get('person', []))}

### 地点节点
{self._format_node_list(nodes_by_type.get('location', []))}

### 事件节点
{self._format_node_list(nodes_by_type.get('event', []))}

### 其他节点
{self._format_node_list(nodes_by_type.get('other', []))}

## 输出要求

```json
{{
  "seed_nodes": ["从上面的节点中选择相关的节点ID"],
  "search_intent": "描述检索意图（简短）",
  "expected_depth": 2,
  "filters": {{
    "time_range": "recent 或 all",
    "node_types": ["需要的节点类型，如 event, person 等，可选"]
  }}
}}
```

## 分析要点

1. 请求中提到了什么实体？映射到已有节点
2. 是在问事实还是感受？
3. 需要多深的关联？（直接记忆 vs 推理出的关联）
4. seed_nodes 必须是上面列出的实际存在的节点ID
5. 如果找不到完全匹配的节点，选择最相关的

只返回JSON，不要其他内容。"""

        result = await self.llm.generate_json(prompt)

        if not result:
            return {
                "seed_nodes": [],
                "search_intent": "无法理解查询",
                "expected_depth": 2,
                "filters": {}
            }

        # 验证种子节点确实存在
        valid_node_ids = {n.get("id") for n in available_nodes}
        result["seed_nodes"] = [
            nid for nid in result.get("seed_nodes", [])
            if nid in valid_node_ids
        ]

        return result

    async def translate_memory(
        self,
        subgraph: GraphData,
        character_profile: CharacterProfile,
        seed_nodes: List[str],
        activated_nodes: Dict[str, float],
    ) -> str:
        """
        记忆翻译：将激活的子图翻译成角色视角的自然语言

        Args:
            subgraph: 激活的子图数据
            character_profile: 角色信息
            seed_nodes: 种子节点（检索起点）
            activated_nodes: 激活的节点及其激活值

        Returns:
            角色视角的自然语言记忆描述
        """
        # 格式化子图为可读描述
        subgraph_desc = self._format_subgraph_for_prompt(subgraph, activated_nodes)

        # 格式化激活路径
        activation_desc = self._format_activation_for_prompt(seed_nodes, activated_nodes)

        prompt = f"""# 角色: 记忆叙述者

你是{character_profile.name}。以下是你刚刚"想起来"的记忆片段，请用第一人称、符合你性格的方式复述出来。

## 你的性格
{character_profile.personality or '普通人'}

## 你的说话风格
{character_profile.speech_pattern or '正常说话'}

## 激活的记忆网络
{subgraph_desc}

## 激活强度（数值越高越相关）
{activation_desc}

## 输出要求

用自然的方式描述你想起了什么。要求：
1. 第一人称
2. 符合你的说话风格和性格
3. 按激活强度排序（最相关的先说）
4. 情感色彩要体现出来
5. 不要像在读报告，要像在回忆
6. 简洁，不要太长（100-300字）

直接输出叙述文本，不需要JSON。这段文字会被直接注入到你的意识中。"""

        result = await self.llm.generate_simple(prompt)
        return result.strip()

    def _format_nodes_for_prompt(self, nodes: List[Dict[str, Any]]) -> str:
        """格式化节点列表供prompt使用"""
        if not nodes:
            return "（暂无已有节点）"

        lines = []
        for node in nodes[:20]:  # 限制数量
            node_id = node.get("id", "unknown")
            node_type = node.get("type", "unknown")
            node_name = node.get("name", "unknown")
            lines.append(f"- {node_id} ({node_type}): {node_name}")

        if len(nodes) > 20:
            lines.append(f"... 还有 {len(nodes) - 20} 个节点")

        return "\n".join(lines)

    def _group_nodes_by_type(self, nodes: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
        """按类型分组节点"""
        groups: Dict[str, List[Dict]] = {
            "person": [],
            "location": [],
            "event": [],
            "other": []
        }

        for node in nodes:
            node_type = node.get("type", "")
            if node_type == "person":
                groups["person"].append(node)
            elif node_type == "location":
                groups["location"].append(node)
            elif node_type == "event":
                groups["event"].append(node)
            else:
                groups["other"].append(node)

        return groups

    def _format_node_list(self, nodes: List[Dict[str, Any]]) -> str:
        """格式化单个类型的节点列表"""
        if not nodes:
            return "（无）"

        lines = []
        for node in nodes[:10]:  # 每类最多10个
            node_id = node.get("id", "unknown")
            node_name = node.get("name", "unknown")
            props = node.get("properties", {})
            extra = ""
            if "day" in props:
                extra = f" [第{props['day']}天]"
            lines.append(f"- {node_id}: {node_name}{extra}")

        if len(nodes) > 10:
            lines.append(f"... 还有 {len(nodes) - 10} 个")

        return "\n".join(lines)

    def _format_subgraph_for_prompt(
        self,
        subgraph: GraphData,
        activated_nodes: Dict[str, float]
    ) -> str:
        """格式化子图供prompt使用"""
        if not subgraph or not subgraph.nodes:
            return "（无记忆被激活）"

        lines = []

        # 按激活值排序节点
        sorted_nodes = sorted(
            subgraph.nodes,
            key=lambda n: activated_nodes.get(n.id, 0),
            reverse=True
        )

        lines.append("### 记忆节点")
        for node in sorted_nodes[:15]:
            activation = activated_nodes.get(node.id, 0)
            props = node.properties or {}

            if node.type == "event":
                summary = props.get("summary", node.name)
                emotion = props.get("emotion", "")
                day = props.get("day", "?")
                lines.append(f"- [{activation:.2f}] 事件(第{day}天): {summary}")
                if emotion:
                    lines.append(f"  情绪: {emotion}")
            elif node.type == "person":
                lines.append(f"- [{activation:.2f}] 人物: {node.name}")
            else:
                lines.append(f"- [{activation:.2f}] {node.type}: {node.name}")

        # 添加关系信息
        if subgraph.edges:
            lines.append("\n### 关系")
            for edge in subgraph.edges[:10]:
                lines.append(f"- {edge.source} --{edge.relation}--> {edge.target}")

        return "\n".join(lines)

    def _format_activation_for_prompt(
        self,
        seed_nodes: List[str],
        activated_nodes: Dict[str, float]
    ) -> str:
        """格式化激活信息"""
        lines = [f"种子节点: {', '.join(seed_nodes)}"]

        # 按激活值排序
        sorted_items = sorted(activated_nodes.items(), key=lambda x: x[1], reverse=True)

        lines.append("激活传播结果:")
        for node_id, activation in sorted_items[:10]:
            lines.append(f"  {node_id}: {activation:.2f}")

        return "\n".join(lines)

    def _validate_encode_result(self, result: Dict[str, Any], game_day: int) -> Dict[str, Any]:
        """验证和清理编码结果"""
        validated = {
            "new_nodes": [],
            "new_edges": [],
            "state_updates": result.get("state_updates", {}) or {}
        }

        # 验证节点
        for node in result.get("new_nodes", []):
            if not isinstance(node, dict):
                continue
            if not node.get("id") or not node.get("type") or not node.get("name"):
                continue

            # 确保properties存在
            if "properties" not in node:
                node["properties"] = {}

            # 事件节点确保有day
            if node["type"] == "event" and "day" not in node["properties"]:
                node["properties"]["day"] = game_day

            # 确保importance在范围内
            importance = node.get("importance", 0.5)
            node["importance"] = max(0.0, min(1.0, float(importance)))

            validated["new_nodes"].append(node)

        # 验证边
        for edge in result.get("new_edges", []):
            if not isinstance(edge, dict):
                continue
            if not edge.get("id") or not edge.get("source") or not edge.get("target"):
                continue
            if not edge.get("relation"):
                continue

            # 确保weight在范围内
            weight = edge.get("weight", 0.5)
            edge["weight"] = max(0.0, min(1.0, float(weight)))

            if "properties" not in edge:
                edge["properties"] = {}

            validated["new_edges"].append(edge)

        # 清理state_updates
        if validated["state_updates"]:
            # 移除空值
            validated["state_updates"] = {
                k: v for k, v in validated["state_updates"].items()
                if v is not None and v != "" and v != []
            }

        return validated
