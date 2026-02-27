"""
Event LLM Service - 事件LLM能力层

负责：
1. 事件解析 (parse_event): 从自然语言中提取事件结构
2. 事件编码 (encode_gm_event): 编码为事件图谱的结构化数据
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventLLMService:
    """事件LLM能力层"""

    def __init__(self, llm_service: Optional[Any] = None) -> None:
        if llm_service is None:
            from app.services.llm_service import LLMService
            llm_service = LLMService()
        self.llm = llm_service

    async def parse_event(
        self,
        event_description: str,
        known_characters: Optional[List[str]] = None,
        known_locations: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        解析事件：从自然语言中提取事件结构

        Args:
            event_description: 事件的自然语言描述
            known_characters: 已知的角色列表（帮助识别）
            known_locations: 已知的地点列表（帮助识别）

        Returns:
            {
                "event_type": "action/combat/dialogue/scene_change/system",
                "summary": "事件简述",
                "location": "地点",
                "participants": ["参与者列表"],
                "witnesses": ["目击者列表"],
                "importance": 0.0-1.0,
                "consequences": ["可能的后果"]
            }
        """
        characters_hint = ""
        if known_characters:
            characters_hint = f"\n已知角色: {', '.join(known_characters)}"

        locations_hint = ""
        if known_locations:
            locations_hint = f"\n已知地点: {', '.join(known_locations)}"

        prompt = f"""# 角色: 事件解析器

你是一个TRPG游戏的事件解析器。请从以下事件描述中提取结构化信息。
{characters_hint}{locations_hint}

## 事件描述
{event_description}

## 输出要求

请分析这个事件，输出JSON格式：

```json
{{
  "event_type": "action/combat/dialogue/scene_change/system中的一个",
  "summary": "事件的简短概述（客观视角，第三人称）",
  "location": "事件发生的地点（如果能推断）",
  "participants": ["直接参与事件的角色ID列表"],
  "witnesses": ["目睹但未直接参与的角色ID列表"],
  "importance": 0.0到1.0之间的数值,
  "consequences": ["这个事件可能带来的后果"]
}}
```

## 解析规则

1. **参与者(participants)**: 直接参与事件的人（战斗的双方、对话的双方、行动的执行者等）
2. **目击者(witnesses)**: 在场看到但未直接参与的人
3. **event_type**:
   - action: 一般行动（帮忙、制作、移动等）
   - combat: 战斗相关
   - dialogue: 重要对话
   - scene_change: 场景/时间变化
   - system: 系统事件
4. **importance**: 基于事件对游戏世界的影响程度
5. 角色ID使用小写，如果原文用的是名字，转为ID格式（如"Marcus" -> "marcus"）

只返回JSON，不要其他内容。"""

        logger.info("事件解析 LLM 调用开始")
        result = await self.llm.generate_json(prompt)

        if not result:
            raise ValueError("事件解析 LLM 返回空结果")

        # 验证和清理
        return self._validate_parse_result(result)

    async def encode_gm_event(
        self,
        event_description: str,
        parsed_event: Dict[str, Any],
        game_day: int,
        existing_nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        编码GM事件：生成GM图谱的结构化数据

        Args:
            event_description: 事件的自然语言描述
            parsed_event: parse_event的结果
            game_day: 游戏日
            existing_nodes: GM图谱中已有的重要节点

        Returns:
            {
                "nodes": [...],
                "edges": [...]
            }
        """
        nodes_reference = self._format_nodes_for_prompt(existing_nodes or [])

        prompt = f"""# 角色: GM记忆编码器

你是游戏GM的记忆系统。请将以下事件编码为结构化的图谱数据。
这是**GM视角**的客观记录，包含所有信息。

## 已有的重要节点（可以引用）
{nodes_reference}

## 事件信息
描述: {event_description}
类型: {parsed_event.get('event_type', 'action')}
地点: {parsed_event.get('location', '未知')}
参与者: {', '.join(parsed_event.get('participants', []))}
目击者: {', '.join(parsed_event.get('witnesses', []))}
重要度: {parsed_event.get('importance', 0.5)}

## 输出要求

节点类型(type)可选值: person, location, event, item, organization
关系类型(relation)可选值: located_in, participated, witnessed, caused, owns, knows

```json
{{
  "nodes": [
    {{
      "id": "event_xxx",
      "type": "event",
      "name": "事件名称",
      "importance": 0.0-1.0,
      "properties": {{
        "day": {game_day},
        "summary": "客观的事件描述（第三人称）",
        "location": "地点",
        "participants": ["参与者"],
        "witnesses": ["目击者"]
      }}
    }}
  ],
  "edges": [
    {{
      "id": "edge_xxx",
      "source": "节点ID",
      "target": "节点ID",
      "relation": "关系类型",
      "weight": 0.0-1.0
    }}
  ]
}}
```

## 编码原则

1. 为事件创建一个event节点
2. 为新出现的人物/地点创建节点（如果不在已有节点中）
3. 建立参与者(participated)和目击者(witnessed)的边
4. 如果涉及地点，建立located_in边
5. 节点ID使用有意义的前缀

只返回JSON，不要其他内容。"""

        logger.info("GM 事件编码 LLM 调用开始: game_day=%d", game_day)
        result = await self.llm.generate_json(prompt)

        if not result:
            raise ValueError("GM 事件编码 LLM 返回空结果")

        return self._validate_encode_result(result, game_day)

    # ==================== 内部辅助 ====================

    def _format_nodes_for_prompt(self, nodes: List[Dict[str, Any]]) -> str:
        """格式化节点列表供prompt使用"""
        if not nodes:
            return "（暂无已有节点）"

        lines = []
        for node in nodes[:20]:
            node_id = node.get("id", "unknown")
            node_type = node.get("type", "unknown")
            node_name = node.get("name", "unknown")
            lines.append(f"- {node_id} ({node_type}): {node_name}")

        if len(nodes) > 20:
            lines.append(f"... 还有 {len(nodes) - 20} 个节点")

        return "\n".join(lines)

    def _validate_parse_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """验证解析结果"""
        validated = {
            "event_type": result.get("event_type", "action"),
            "summary": result.get("summary", ""),
            "location": result.get("location"),
            "participants": result.get("participants", []),
            "witnesses": result.get("witnesses", []),
            "importance": max(0.0, min(1.0, float(result.get("importance", 0.5)))),
            "consequences": result.get("consequences", [])
        }

        # 确保participants和witnesses是列表
        if not isinstance(validated["participants"], list):
            validated["participants"] = []
        if not isinstance(validated["witnesses"], list):
            validated["witnesses"] = []

        # 规范化角色ID
        validated["participants"] = [p.lower().replace(" ", "_") for p in validated["participants"]]
        validated["witnesses"] = [w.lower().replace(" ", "_") for w in validated["witnesses"]]

        return validated

    def _validate_encode_result(self, result: Dict[str, Any], game_day: int) -> Dict[str, Any]:
        """验证编码结果"""
        validated = {"nodes": [], "edges": []}

        for node in result.get("nodes", []):
            if not isinstance(node, dict):
                continue
            if not node.get("id") or not node.get("type") or not node.get("name"):
                continue

            if "properties" not in node:
                node["properties"] = {}
            if node["type"] == "event" and "day" not in node["properties"]:
                node["properties"]["day"] = game_day

            node["importance"] = max(0.0, min(1.0, float(node.get("importance", 0.5))))
            validated["nodes"].append(node)

        for edge in result.get("edges", []):
            if not isinstance(edge, dict):
                continue
            if not edge.get("id") or not edge.get("source") or not edge.get("target"):
                continue
            if not edge.get("relation"):
                continue

            edge["weight"] = max(0.0, min(1.0, float(edge.get("weight", 0.5))))
            if "properties" not in edge:
                edge["properties"] = {}
            validated["edges"].append(edge)

        return validated
