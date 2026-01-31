"""
Narrative Models - 主线章节系统数据模型

支持 主线 -> 章节 -> 地图 -> 地点 四层结构
进度存储在 GameSessionState.metadata.narrative
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
    objectives: List[ChapterObjective] = Field(default_factory=list)
    available_maps: List[str] = Field(default_factory=list)  # 解锁的地图ID
    trigger_conditions: Dict[str, Any] = Field(default_factory=dict)  # 解锁条件
    completion_conditions: Dict[str, Any] = Field(default_factory=dict)  # 完成条件


class Mainline(BaseModel):
    """主线剧情"""
    id: str
    name: str
    description: str
    chapters: List[str] = Field(default_factory=list)  # 章节ID列表（有序）


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

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于存储）"""
        return {
            "current_mainline": self.current_mainline,
            "current_chapter": self.current_chapter,
            "objectives_completed": self.objectives_completed,
            "events_triggered": self.events_triggered,
            "chapter_started_at": self.chapter_started_at.isoformat() if self.chapter_started_at else None,
            "chapters_completed": self.chapters_completed,
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
        )


class NarrativeData(BaseModel):
    """叙事数据（从 mainlines.json 加载）"""
    mainlines: List[Mainline] = Field(default_factory=list)
    chapters: List[Chapter] = Field(default_factory=list)
