"""
队伍和队友模型。

队友是特殊的 NPC：
- 有独立记忆图谱 (worlds/{world_id}/characters/{character_id}/)
- 跟随玩家移动
- 自动加入事件（作为目击者）
- 战斗中作为盟友
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TeammateRole(str, Enum):
    """队友职责"""
    WARRIOR = "warrior"       # 战士
    HEALER = "healer"         # 治疗师
    MAGE = "mage"             # 法师
    ROGUE = "rogue"           # 盗贼
    SUPPORT = "support"       # 辅助
    SCOUT = "scout"           # 斥候
    SCHOLAR = "scholar"       # 学者


class TeammateModelConfig(BaseModel):
    """队友 AI 模型配置"""

    # 平常状态（快速响应）
    casual_model: str = "gemini-3-flash-preview"
    casual_thinking: Optional[Literal["lowest", "low", "medium", "high"]] = None

    # 显式对话（深度交流）
    dialogue_model: str = "gemini-3-pro-preview"
    dialogue_thinking: Literal["lowest", "low", "medium", "high"] = "low"


class PartyMember(BaseModel):
    """队伍成员"""

    character_id: str
    name: str
    role: TeammateRole = TeammateRole.SUPPORT
    personality: str = ""                      # 性格描述
    response_tendency: float = Field(default=0.5, ge=0.0, le=1.0)  # 回复倾向 (0=沉默, 1=健谈)

    # 状态
    joined_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True                     # 是否活跃（可能因剧情离队）
    current_mood: str = "neutral"              # 当前情绪

    # 图谱引用
    graph_ref: str = ""                        # worlds/{world_id}/characters/{character_id}/

    # 模型配置
    model_config_override: Optional[TeammateModelConfig] = None

    def get_graph_path(self, world_id: str) -> str:
        """获取队友图谱路径"""
        if self.graph_ref:
            return self.graph_ref
        return f"worlds/{world_id}/characters/{self.character_id}/"


class Party(BaseModel):
    """队伍"""

    party_id: str
    world_id: str
    session_id: str
    leader_id: str                             # 通常是玩家
    members: List[PartyMember] = Field(default_factory=list)
    formed_at: datetime = Field(default_factory=datetime.utcnow)

    # 配置
    max_size: int = 4
    auto_follow: bool = True                   # 队友自动跟随
    share_events: bool = True                  # 共享事件到队友图谱

    # 当前位置（与玩家同步）
    current_location: Optional[str] = None
    current_sub_location: Optional[str] = None

    def get_active_members(self) -> List[PartyMember]:
        """获取活跃成员"""
        return [m for m in self.members if m.is_active]

    def get_member(self, character_id: str) -> Optional[PartyMember]:
        """获取指定成员"""
        for member in self.members:
            if member.character_id == character_id:
                return member
        return None

    def is_full(self) -> bool:
        """队伍是否已满"""
        return len(self.get_active_members()) >= self.max_size


class TeammateResponseDecision(BaseModel):
    """队友回复决策"""

    character_id: str
    should_respond: bool
    reason: str = ""                           # 决策原因
    priority: int = 0                          # 回复优先级 (越高越先)
    suggested_tone: Optional[str] = None       # 建议的语气


class TeammateResponseResult(BaseModel):
    """队友回复结果"""

    character_id: str
    name: str
    response: Optional[str] = None             # None = 选择不回复
    reaction: str = ""                         # 表情/动作描述
    model_used: str = ""                       # 使用的模型
    thinking_level: Optional[str] = None       # 思考级别
    latency_ms: int = 0                        # 响应延迟


class TeammateRoundResult(BaseModel):
    """一轮队友响应的完整结果"""

    responses: List[TeammateResponseResult] = Field(default_factory=list)
    total_latency_ms: int = 0
    responding_count: int = 0                  # 实际回复的队友数量
