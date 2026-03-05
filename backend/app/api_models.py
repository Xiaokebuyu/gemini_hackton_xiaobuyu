"""Pydantic models for the first-pass HTTP API shell."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiErrorDetail(BaseModel):
    """Stable machine-readable error detail payload."""

    code: str
    message: str


class ApiErrorResponse(BaseModel):
    """Standard API error wrapper."""

    detail: ApiErrorDetail


class HealthResponse(BaseModel):
    """Health-check response for the API shell."""

    status: str
    service: str
    mode: str


class WorldSummaryResponse(BaseModel):
    """One world card shown on the world-selection screen."""

    world_id: str
    name: str
    description: str
    cover_image: str
    player_count: int


class SessionSummaryBody(BaseModel):
    """Compact player-facing summary for one stored session."""

    player_name: str
    player_class: str
    level: int
    location: str
    day: int | None
    play_time_hours: float | None


class SessionSummaryResponse(BaseModel):
    """Stored-session card for the save management screen."""

    session_id: str
    created_at: float
    last_played: float
    phase: str
    summary: SessionSummaryBody


class SessionLifecycleResponse(BaseModel):
    """Minimal response for session create/resume lifecycle endpoints."""

    world_id: str
    session_id: str
    phase: str


class CharacterCreationOptionsResponse(BaseModel):
    """Character creation options exposed by the API shell."""

    races: list[dict[str, Any]] = Field(default_factory=list)
    classes: list[dict[str, Any]] = Field(default_factory=list)
    backgrounds: list[dict[str, Any]] = Field(default_factory=list)


class CharacterCreationRequest(BaseModel):
    """Request body for first-pass character creation."""

    name: str
    race_id: str | None = None
    race: str | None = None
    class_id: str | None = None
    character_class: str | None = None
    background_id: str | None = None
    background: str | None = None
    ability_scores: dict[str, int]
    skill_proficiencies: list[str] = Field(default_factory=list)
    backstory: str = ""


class CharacterPanelResponse(BaseModel):
    """Character panel payload."""

    phase: str
    player: dict[str, Any]


class InventoryPanelResponse(BaseModel):
    """Inventory panel payload."""

    gold: int
    inventory: list[dict[str, Any]] = Field(default_factory=list)
    equipment: dict[str, Any] = Field(default_factory=dict)


class MapAreaSummary(BaseModel):
    """Map summary card for one area."""

    id: str
    name: str
    danger_level: float | None
    exploration: str
    tags: list[str] = Field(default_factory=list)
    sub_locations: list[dict[str, str]] = Field(default_factory=list)


class MapPanelResponse(BaseModel):
    """Map panel payload."""

    current_area: str
    current_location: str | None
    discovered_area_ids: list[str] = Field(default_factory=list)
    areas: list[MapAreaSummary] = Field(default_factory=list)


class QuestPanelResponse(BaseModel):
    """Quest panel payload."""

    milestone_states: dict[str, Any] = Field(default_factory=dict)
    dynamic_quests: dict[str, Any] = Field(default_factory=dict)
    chapter_completion: dict[str, float] = Field(default_factory=dict)


class PlayerLocationBody(BaseModel):
    """Current player location summary."""

    area_id: str
    location_id: str | None


class ActionExecutionResponse(BaseModel):
    """Structured action execution result for JSON endpoints."""

    success: bool
    action_type: str
    time_cost: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    player_location: PlayerLocationBody


class NavigateRequest(BaseModel):
    """Request body for the JSON navigation endpoint."""

    action: str
    area_id: str | None = None
    location_id: str | None = None


class StructuredActionRequest(BaseModel):
    """Request body for streaming structured actions."""

    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    source: str = "player"
    context: dict[str, Any] | None = None


class TextInputRequest(BaseModel):
    """Request body for the free-text input stream."""

    text: str


class InteractRequest(BaseModel):
    """Request body for the interaction stream."""

    target_kind: str | None = None
    target_id: str | None = None
    npc_id: str | None = None
    intent: str
    item_id: str | None = None
    quest_id: str | None = None
    count: int = 1
    message: str | None = None


class PrivateChatRequest(BaseModel):
    """Request body for the private chat stream."""

    npc_id: str
    message: str


class CompanionRequest(BaseModel):
    """Request body for companion recruit/dismiss."""

    npc_id: str
