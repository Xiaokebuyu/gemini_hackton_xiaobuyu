"""分层上下文模型 — Runtime 与 Pipeline 的契约。

Layer 0: 世界常量（背景、地理、势力）
Layer 1: 章节作用域（目标、事件链、转换条件）
Layer 2: 区域（NPC、怪物、子地点、事件状态）
Layer 3: 子地点详情
Layer 4: 玩家/队伍/时间/好感度
Memory: 动态图谱召回结果
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LayeredContext(BaseModel):
    """分层上下文 — 6 层数据包，供 Agentic LLM 使用。"""

    # Layer 0: 世界常量
    world: Dict[str, Any] = Field(
        default_factory=dict,
        description="世界常量：背景设定、地理、势力、基调",
    )

    # Layer 1: 章节作用域
    chapter: Dict[str, Any] = Field(
        default_factory=dict,
        description="当前章节：目标、事件列表、转换条件、节奏控制",
    )

    # Layer 2: 区域
    area: Dict[str, Any] = Field(
        default_factory=dict,
        description="当前区域：NPC、怪物、子地点、事件状态、环境描述",
    )

    # Layer 3: 子地点详情
    location: Optional[Dict[str, Any]] = Field(
        default=None,
        description="当前子地点详情（如果在子地点内）",
    )

    # Layer 4: 动态状态
    dynamic: Dict[str, Any] = Field(
        default_factory=dict,
        description="动态状态：玩家角色、队伍、时间、好感度、对话历史",
    )

    # Memory: 图谱召回
    memory: Optional[Dict[str, Any]] = Field(
        default=None,
        description="扩散激活图谱召回结果",
    )

    def to_flat_dict(self) -> Dict[str, Any]:
        """扁平化为单层字典，供 LLM 注入。"""
        result: Dict[str, Any] = {}
        result["world_context"] = self.world
        result["chapter_context"] = self.chapter
        result["area_context"] = self.area
        if self.location:
            result["location_context"] = self.location
        result["dynamic_state"] = self.dynamic
        if self.memory:
            result["memory_recall"] = self.memory
        return result

    def get_layer_summary(self) -> Dict[str, int]:
        """返回各层数据量摘要（用于调试/监控）。"""
        import json
        return {
            "world_chars": len(json.dumps(self.world, ensure_ascii=False)),
            "chapter_chars": len(json.dumps(self.chapter, ensure_ascii=False)),
            "area_chars": len(json.dumps(self.area, ensure_ascii=False)),
            "location_chars": len(json.dumps(self.location, ensure_ascii=False)) if self.location else 0,
            "dynamic_chars": len(json.dumps(self.dynamic, ensure_ascii=False)),
            "memory_chars": len(json.dumps(self.memory, ensure_ascii=False)) if self.memory else 0,
        }
