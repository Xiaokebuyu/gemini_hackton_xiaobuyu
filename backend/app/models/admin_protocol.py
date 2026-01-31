"""
Admin layer protocol models.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

from app.models.state_delta import StateDelta


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
