"""同伴状态模型 — Phase 5 使用。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CompactEvent(BaseModel):
    """紧凑事件记录（同伴共享事件列表）。

    替代每轮 perspective_transform LLM 调用，
    事件完成时直接追加结构化记录。
    """
    event_id: str
    event_name: str
    summary: str
    area_id: str
    game_day: int
    importance: str = "side"  # main | side | ambient
    player_role: str = ""     # 玩家在事件中的角色描述
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CompanionEmotionalState(BaseModel):
    """同伴情感状态。

    在同伴响应时一并更新（无额外 LLM 调用）。
    """
    mood: str = "neutral"
    trust_level: float = Field(default=0.5, ge=0.0, le=1.0)
    loyalty: float = Field(default=0.5, ge=0.0, le=1.0)
    recent_interactions: int = 0
    last_interaction_day: int = 0
    relationship_tags: List[str] = Field(default_factory=list)
    notes: str = ""

    def update_from_response(self, mood: str, interaction_quality: float = 0.0) -> None:
        """响应后更新情感状态。"""
        self.mood = mood
        self.recent_interactions += 1
        if interaction_quality > 0:
            self.trust_level = min(1.0, self.trust_level + interaction_quality * 0.05)
        elif interaction_quality < 0:
            self.trust_level = max(0.0, self.trust_level + interaction_quality * 0.05)


class CompanionAreaSummary(BaseModel):
    """同伴视角的区域访问摘要。"""
    area_id: str
    area_name: str
    visit_day: int
    key_events: List[str] = Field(default_factory=list)
    interactions_with_player: int = 0
    mood_during_visit: str = "neutral"
    summary: str = ""
