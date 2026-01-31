"""
State delta models for admin layer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Literal

from pydantic import BaseModel, Field


class GameTimeState(BaseModel):
    """Lightweight time snapshot stored in state."""

    day: int = 1
    hour: int = 8
    minute: int = 0
    period: Optional[str] = None
    formatted: Optional[str] = None


class StateDelta(BaseModel):
    """State change event (Flash-only write access)."""

    delta_id: str
    timestamp: datetime
    operation: str
    changes: Dict[str, Any] = Field(default_factory=dict)
    previous_values: Dict[str, Any] = Field(default_factory=dict)


class GameState(BaseModel):
    """Session state snapshot managed by Flash."""

    session_id: str
    world_id: str
    player_location: Optional[str] = None
    sub_location: Optional[str] = None
    game_time: GameTimeState = Field(default_factory=GameTimeState)
    chat_mode: Literal["think", "say"] = "think"
    active_dialogue_npc: Optional[str] = None
    combat_id: Optional[str] = None
    narrative_progress: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
