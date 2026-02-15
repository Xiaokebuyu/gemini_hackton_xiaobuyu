"""区域状态模型 — 区域生命周期核心数据结构。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


def _none_to_dict(v: Any) -> Dict[str, Any]:
    """Firestore 中 null 字段转为空 dict，保持下游代码无需 None 检查。"""
    return v if v is not None else {}


class SubLocationDef(BaseModel):
    """子地点定义（来自 WorldInstance.area_registry）。"""
    id: str
    name: str
    description: str = ""
    interaction_type: str = "visit"
    resident_npcs: List[str] = Field(default_factory=list)
    requirements: Dict[str, Any] = Field(default_factory=dict)
    available_actions: List[str] = Field(default_factory=list)
    passerby_spawn_rate: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("requirements", "metadata", mode="before")
    @classmethod
    def _coerce_none_to_dict(cls, v: Any) -> Dict[str, Any]:
        return _none_to_dict(v)


class AreaConnection(BaseModel):
    """区域间连接。"""
    target_area_id: str
    connection_type: str = "travel"
    travel_time: str = "30 minutes"
    requirements: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""

    @field_validator("requirements", mode="before")
    @classmethod
    def _coerce_none_to_dict(cls, v: Any) -> Dict[str, Any]:
        return _none_to_dict(v)


class AreaDefinition(BaseModel):
    """区域静态定义（来自 WorldInstance.area_registry）。

    对应 Firestore 路径: worlds/{world_id}/maps/{area_id}
    """
    area_id: str
    name: str = ""
    description: str = ""
    danger_level: int = 1
    area_type: str = "settlement"
    tags: List[str] = Field(default_factory=list)
    key_features: List[str] = Field(default_factory=list)
    available_actions: List[str] = Field(default_factory=list)
    sub_locations: List[SubLocationDef] = Field(default_factory=list)
    connections: List[AreaConnection] = Field(default_factory=list)
    resident_npcs: List[str] = Field(default_factory=list)
    ambient_description: str = ""
    region: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def get_sub_location(self, sub_id: str) -> Optional[SubLocationDef]:
        """按 ID 查找子地点。"""
        for sl in self.sub_locations:
            if sl.id == sub_id:
                return sl
        return None


class AreaEvent(BaseModel):
    """区域事件 — 4 状态生命周期。

    状态机: locked → available → active → completed
    """
    id: str
    area_id: str
    chapter_id: str
    name: str
    description: str = ""
    importance: str = "side"  # main | side | ambient
    status: str = "locked"   # locked | available | active | completed

    trigger_conditions: Dict[str, Any] = Field(default_factory=dict)
    completion_conditions: Optional[Dict[str, Any]] = None

    on_complete: Optional[Dict[str, Any]] = None
    # on_complete 结构：
    # {
    #   "unlock_events": ["event_id_1"],
    #   "add_items": [{"item_id": "...", "quantity": 1}],
    #   "add_xp": 100,
    #   "narrative_hint": "...",
    # }

    narrative_directive: str = ""
    cooldown_rounds: int = 0
    is_repeatable: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AreaState(BaseModel):
    """区域动态状态（运行时可变）。

    对应 Firestore 路径: worlds/{world_id}/areas/{area_id}/state
    """
    area_id: str
    visit_count: int = 0
    last_visit_day: int = 0
    discovered_sub_locations: List[str] = Field(default_factory=list)
    cleared_encounters: List[str] = Field(default_factory=list)
    custom_state: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None


class VisitSummary(BaseModel):
    """单次区域访问摘要。

    对应 Firestore 路径: worlds/{world_id}/areas/{area_id}/visits/{visit_id}
    """
    visit_id: str
    area_id: str
    session_id: str
    entered_at: datetime = Field(default_factory=datetime.utcnow)
    left_at: Optional[datetime] = None
    actions_taken: List[str] = Field(default_factory=list)
    events_triggered: List[str] = Field(default_factory=list)
    npcs_interacted: List[str] = Field(default_factory=list)
    summary_text: str = ""
    game_day: int = 1


class EventUpdate(BaseModel):
    """事件状态变更记录。"""
    event: AreaEvent
    transition: str  # e.g. "locked→available", "active→completed"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    details: Dict[str, Any] = Field(default_factory=dict)
