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


class ResumeLocationVisualResponse(BaseModel):
    """Minimal visual hints for restoring the current scene."""

    area_id: str
    location_id: str | None
    background_key: str
    present_character_ids: list[str] = Field(default_factory=list)


class ResumeSessionResponse(BaseModel):
    """Full resume payload used to hydrate the game view in one request."""

    world_id: str
    session_id: str
    phase: str
    player: dict[str, Any]
    scene: dict[str, Any]
    party: dict[str, Any] = Field(default_factory=dict)
    location_visual: ResumeLocationVisualResponse
    resume_narration: str


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
    """Quest panel payload.

    ``dynamic_quests`` contains application-layer quest views, not raw
    ``QuestSlice`` payloads. Persisted fields such as ``requires_report`` and
    ``reported`` may appear there, while fields such as ``can_report``,
    ``ui_state`` and ``badge`` are derived read-model values.
    """

    milestone_states: dict[str, Any] = Field(default_factory=dict)
    dynamic_quests: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Application-layer dynamic quest views. Top-level report flags are "
            "persisted state; can_report/ui_state/badge are derived fields."
        ),
    )
    chapter_completion: dict[str, float] = Field(default_factory=dict)


class PlayerLocationBody(BaseModel):
    """Current player location summary."""

    area_id: str
    location_id: str | None


class ActionExecutionResponse(BaseModel):
    """Structured action execution result for JSON endpoints."""

    executed: bool
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

    scope: str | None = None
    target_kind: str | None = None
    target_id: str | None = None
    npc_id: str | None = None
    intent: str
    item_id: str | None = None
    quest_id: str | None = Field(
        default=None,
        description=(
            "Quest target for quest-related NPC intents. Required for "
            "accept_quest/report_quest and used by quest-focused ask_* reads."
        ),
    )
    count: int = 1
    message: str | None = None
    check_skill: str | None = None
    check_dc: int | None = None


class PrivateChatRequest(BaseModel):
    """Request body for the private chat stream."""

    npc_id: str
    message: str


class CompanionRequest(BaseModel):
    """Request body for companion recruit/dismiss."""

    npc_id: str
