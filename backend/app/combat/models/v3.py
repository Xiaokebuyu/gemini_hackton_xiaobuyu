"""Combat v3 interface models."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EnemySpec(BaseModel):
    """Structured enemy specification for v3 combat entry."""

    enemy_id: str = Field(..., min_length=1)
    count: int = Field(default=1, ge=1, le=20)
    level: int = Field(default=1, ge=1, le=20)
    variant: Optional[str] = None
    template_version: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    overrides: Dict[str, Any] = Field(default_factory=dict)


class CombatActionOptionV3(BaseModel):
    """Extended action option payload for frontend/agentic usage."""

    action_id: str
    action_type: str
    display_name: str
    description: str = ""
    target_id: Optional[str] = None
    requirements: Dict[str, Any] = Field(default_factory=dict)
    resource_cost: Dict[str, Any] = Field(default_factory=dict)
    hit_formula: Optional[str] = None
    damage_formula: Optional[str] = None
    effect_refs: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CombatResolutionV3(BaseModel):
    """Structured combat resolution object for admin sync/event writing."""

    combat_id: str
    result: str
    summary: str = ""
    rewards: Dict[str, Any] = Field(default_factory=dict)
    player_state: Dict[str, Any] = Field(default_factory=dict)
    character_deltas: Dict[str, Any] = Field(default_factory=dict)
    loot_events: List[Dict[str, Any]] = Field(default_factory=list)
    relationship_impacts: List[Dict[str, Any]] = Field(default_factory=list)
    event_payload_ref: Optional[str] = None
