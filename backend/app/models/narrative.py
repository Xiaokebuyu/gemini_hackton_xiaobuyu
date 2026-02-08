"""
Narrative Models - 主线章节系统数据模型

支持 主线 -> 章节 -> 地图 -> 地点 四层结构
进度存储在 GameSessionState.metadata.narrative
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# =============================================================================
# 条件系统
# =============================================================================


class ConditionType(str, Enum):
    """条件类型"""
    # 结构化（确定性，纯机械，无 LLM）
    LOCATION = "location"
    NPC_INTERACTED = "npc_interacted"
    TIME_PASSED = "time_passed"
    ROUNDS_ELAPSED = "rounds_elapsed"
    PARTY_CONTAINS = "party_contains"
    EVENT_TRIGGERED = "event_triggered"
    OBJECTIVE_COMPLETED = "objective_completed"
    GAME_STATE = "game_state"
    # 语义化（由 FlashCPU 评估）
    FLASH_EVALUATE = "flash_evaluate"


class Condition(BaseModel):
    """单个条件"""
    type: ConditionType
    params: Dict[str, Any] = Field(default_factory=dict)


class ConditionGroup(BaseModel):
    """条件组（支持递归嵌套）"""
    operator: Literal["and", "or", "not"] = "and"
    conditions: List[Union[Condition, "ConditionGroup"]] = Field(default_factory=list)


# 解决前向引用
ConditionGroup.model_rebuild()


# =============================================================================
# 事件与章节编排
# =============================================================================


class StoryEvent(BaseModel):
    """章节事件（替代纯 event_id 字符串）"""
    id: str
    name: str
    description: str = ""
    trigger_conditions: ConditionGroup = Field(default_factory=ConditionGroup)
    is_required: bool = False
    is_repeatable: bool = False
    cooldown_rounds: int = 0
    narrative_directive: str = ""
    side_effects: List[Dict[str, Any]] = Field(default_factory=list)


class ChapterTransition(BaseModel):
    """章节转换规则"""
    target_chapter_id: str
    conditions: ConditionGroup = Field(default_factory=ConditionGroup)
    priority: int = 0
    transition_type: Literal["normal", "branch", "failure", "skip"] = "normal"
    narrative_hint: str = ""


class PacingConfig(BaseModel):
    """节奏控制配置"""
    min_rounds: int = 3
    ideal_rounds: int = 10
    max_rounds: int = 30
    stall_threshold: int = 5
    hint_escalation: List[str] = Field(
        default_factory=lambda: [
            "subtle_environmental",
            "npc_reminder",
            "direct_prompt",
            "forced_event",
        ]
    )


# =============================================================================
# 核心模型
# =============================================================================


class ChapterObjective(BaseModel):
    """章节目标"""
    id: str
    description: str
    completed: bool = False
    completed_at: Optional[datetime] = None


class Chapter(BaseModel):
    """剧情章节"""
    id: str
    mainline_id: str
    name: str
    description: str
    type: str = "story"  # "story" | "metadata" | "volume_index"
    objectives: List[ChapterObjective] = Field(default_factory=list)
    available_maps: List[str] = Field(default_factory=list)  # 解锁的地图ID
    trigger_conditions: Dict[str, Any] = Field(default_factory=dict)  # 解锁条件（legacy）
    completion_conditions: Dict[str, Any] = Field(default_factory=dict)  # 完成条件（legacy）
    # v2 新增字段（向后兼容，空 → fallback 到 legacy）
    events: List[StoryEvent] = Field(default_factory=list)
    transitions: List[ChapterTransition] = Field(default_factory=list)
    pacing: PacingConfig = Field(default_factory=PacingConfig)
    entry_conditions: Optional[ConditionGroup] = None
    tags: List[str] = Field(default_factory=list)


class Mainline(BaseModel):
    """主线剧情"""
    id: str
    name: str
    description: str
    chapters: List[str] = Field(default_factory=list)  # 章节ID列表（有序）
    # v2 新增：DAG 结构（空时 fallback 到线性 chapters）
    chapter_graph: Dict[str, List[str]] = Field(default_factory=dict)


class NarrativeProgress(BaseModel):
    """
    叙事进度

    存储在 session.metadata.narrative
    """
    current_mainline: str
    current_chapter: str
    objectives_completed: List[str] = Field(default_factory=list)
    events_triggered: List[str] = Field(default_factory=list)
    chapter_started_at: Optional[datetime] = None
    chapters_completed: List[str] = Field(default_factory=list)
    # v2 新增字段
    rounds_in_chapter: int = 0
    rounds_since_last_progress: int = 0
    active_chapters: List[str] = Field(default_factory=list)
    npc_interactions: Dict[str, int] = Field(default_factory=dict)
    event_cooldowns: Dict[str, int] = Field(default_factory=dict)
    branch_history: List[Dict[str, Any]] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于存储）"""
        return {
            "current_mainline": self.current_mainline,
            "current_chapter": self.current_chapter,
            "objectives_completed": self.objectives_completed,
            "events_triggered": self.events_triggered,
            "chapter_started_at": self.chapter_started_at.isoformat() if self.chapter_started_at else None,
            "chapters_completed": self.chapters_completed,
            "rounds_in_chapter": self.rounds_in_chapter,
            "rounds_since_last_progress": self.rounds_since_last_progress,
            "active_chapters": self.active_chapters,
            "npc_interactions": self.npc_interactions,
            "event_cooldowns": self.event_cooldowns,
            "branch_history": self.branch_history,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NarrativeProgress":
        """从字典创建"""
        chapter_started_at = data.get("chapter_started_at")
        if chapter_started_at and isinstance(chapter_started_at, str):
            chapter_started_at = datetime.fromisoformat(chapter_started_at)

        return cls(
            current_mainline=data.get("current_mainline", ""),
            current_chapter=data.get("current_chapter", ""),
            objectives_completed=data.get("objectives_completed", []),
            events_triggered=data.get("events_triggered", []),
            chapter_started_at=chapter_started_at,
            chapters_completed=data.get("chapters_completed", []),
            rounds_in_chapter=data.get("rounds_in_chapter", 0),
            rounds_since_last_progress=data.get("rounds_since_last_progress", 0),
            active_chapters=data.get("active_chapters", []),
            npc_interactions=data.get("npc_interactions", {}),
            event_cooldowns=data.get("event_cooldowns", {}),
            branch_history=data.get("branch_history", []),
        )


class NarrativeData(BaseModel):
    """叙事数据（从 mainlines.json 加载）"""
    mainlines: List[Mainline] = Field(default_factory=list)
    chapters: List[Chapter] = Field(default_factory=list)
