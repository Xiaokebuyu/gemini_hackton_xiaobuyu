"""
Admin layer protocol models.

Pro-First 架构：Flash 分析 → Flash 执行 → Pro 叙述
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

from app.models.state_delta import StateDelta


# =============================================================================
# 意图解析模型 (Pro-First)
# =============================================================================


class IntentType(str, Enum):
    """玩家意图类型"""

    # 导航相关
    NAVIGATION = "navigation"           # 移动到某地点
    ENTER_SUB_LOCATION = "enter_sub_location"  # 进入子地点
    LEAVE_SUB_LOCATION = "leave_sub_location"  # 离开子地点
    LOOK_AROUND = "look_around"         # 观察环境

    # 对话相关
    NPC_INTERACTION = "npc_interaction"  # 与NPC交互
    TEAM_INTERACTION = "team_interaction"  # 与队友交互
    END_DIALOGUE = "end_dialogue"       # 结束对话

    # 战斗相关
    START_COMBAT = "start_combat"       # 发起战斗
    COMBAT_ACTION = "combat_action"     # 战斗中的行动

    # 时间相关
    WAIT = "wait"                       # 等待/消磨时间
    REST = "rest"                       # 休息

    # 特殊
    SYSTEM_COMMAND = "system_command"   # 系统命令（查看状态等）
    ROLEPLAY = "roleplay"               # 纯角色扮演/对话
    UNKNOWN = "unknown"                 # 无法解析


class ParsedIntent(BaseModel):
    """解析后的玩家意图"""

    intent_type: IntentType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # 意图参数（根据类型不同而不同）
    target: Optional[str] = None        # 目标（地点/NPC等）
    action: Optional[str] = None        # 具体动作
    parameters: Dict[str, Any] = Field(default_factory=dict)

    # 原始输入
    raw_input: str = ""

    # Pro 的理解和解释
    interpretation: Optional[str] = None  # Pro 对玩家意图的解读
    player_emotion: Optional[str] = None  # 推测的玩家情绪

    # 生成的 Flash 请求
    flash_requests: List["FlashRequest"] = Field(default_factory=list)


class IntentParseResult(BaseModel):
    """意图解析完整结果"""

    # 主意图
    primary_intent: ParsedIntent

    # 可能的次要意图（如果玩家输入包含多个意图）
    secondary_intents: List[ParsedIntent] = Field(default_factory=list)

    # 上下文信息
    context_used: Dict[str, Any] = Field(default_factory=dict)

    # 需要确认的歧义
    ambiguities: List[str] = Field(default_factory=list)

    # Pro 的思考过程（可选，用于调试）
    reasoning: Optional[str] = None


# =============================================================================
# Flash 操作模型
# =============================================================================


class FlashOperation(str, Enum):
    """Supported Flash operations."""

    # 实例管理
    SPAWN_PASSERBY = "spawn_passerby"
    NPC_DIALOGUE = "npc_dialogue"
    # 事件系统
    BROADCAST_EVENT = "broadcast_event"
    GRAPHIZE_EVENT = "graphize_event"
    RECALL_MEMORY = "recall_memory"
    # 导航/时间
    NAVIGATE = "navigate"
    UPDATE_TIME = "update_time"
    ENTER_SUBLOCATION = "enter_sublocation"
    # 战斗
    START_COMBAT = "start_combat"
    # 章节
    TRIGGER_NARRATIVE_EVENT = "trigger_narrative_event"


class NPCReaction(BaseModel):
    """Structured NPC reaction for Pro to narrate."""

    npc_id: str
    name: Optional[str] = None
    response: str
    mood: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FlashRequest(BaseModel):
    """Pro -> Flash request."""

    operation: FlashOperation
    parameters: Dict[str, Any]
    priority: Literal["low", "normal", "high"] = "normal"
    context_hint: Optional[str] = None


class AnalysisPlan(BaseModel):
    """Flash 一次性分析结果."""

    intent: ParsedIntent
    operations: List[FlashRequest] = Field(default_factory=list)
    memory_seeds: List[str] = Field(default_factory=list)
    reasoning: str = ""


class FlashResponse(BaseModel):
    """Flash -> Pro response (JSON only)."""

    success: bool
    operation: FlashOperation
    result: Dict[str, Any] = Field(default_factory=dict)
    state_delta: Optional[StateDelta] = None
    npc_reactions: Optional[List[NPCReaction]] = None
    error: Optional[str] = None


class ProResponse(BaseModel):
    """Pro -> Player response (mixed narration + metadata)."""

    narration: str
    speaker: str = "GM"
    flash_requests: List[FlashOperation] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# 协调器响应模型
# =============================================================================


class CoordinatorResponse(BaseModel):
    """协调器完整响应（包含 GM 叙述和队友响应）"""

    # GM 叙述
    narration: str
    speaker: str = "GM"

    # 队友响应（可选）
    teammate_responses: List[Dict[str, Any]] = Field(default_factory=list)

    # 可用操作（选项式交互）
    available_actions: List[Dict[str, Any]] = Field(default_factory=list)

    # 状态变更
    state_delta: Optional[StateDelta] = None

    # 元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 解决前向引用
ParsedIntent.model_rebuild()
