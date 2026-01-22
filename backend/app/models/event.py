"""
Event models for GM and dispatching.
"""
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.graph import MemoryEdge, MemoryNode


class EventType(str, Enum):
    """Event categories."""

    SCENE_CHANGE = "scene_change"
    DIALOGUE = "dialogue"
    ACTION = "action"
    COMBAT = "combat"
    SYSTEM = "system"
    CUSTOM = "custom"


class EventContent(BaseModel):
    """Event content container."""

    raw: Optional[str] = None
    structured: Dict = Field(default_factory=dict)


class EventVisibility(BaseModel):
    """Visibility rules for an event."""

    public: bool = False
    known_to: List[str] = Field(default_factory=list)


class Event(BaseModel):
    """Event payload."""

    id: Optional[str] = None
    type: EventType = EventType.CUSTOM
    timestamp: datetime = Field(default_factory=datetime.now)
    game_day: Optional[int] = None
    location: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    witnesses: List[str] = Field(default_factory=list)
    content: EventContent = Field(default_factory=EventContent)
    visibility: EventVisibility = Field(default_factory=EventVisibility)
    nodes: List[MemoryNode] = Field(default_factory=list)
    edges: List[MemoryEdge] = Field(default_factory=list)


class EventDispatchOverride(BaseModel):
    """Override dispatch payload for a character."""

    nodes: List[MemoryNode] = Field(default_factory=list)
    edges: List[MemoryEdge] = Field(default_factory=list)
    state_updates: Dict = Field(default_factory=dict)
    write_indexes: bool = False
    validate: bool = False
    strict: bool = False


class GMEventIngestRequest(BaseModel):
    """GM ingest request (write + dispatch)."""

    event: Event
    distribute: bool = True
    recipients: Optional[List[str]] = None
    known_characters: List[str] = Field(default_factory=list)
    character_locations: Dict[str, str] = Field(default_factory=dict)
    per_character: Dict[str, EventDispatchOverride] = Field(default_factory=dict)
    default_dispatch: bool = True
    write_indexes: bool = False
    validate: bool = False
    strict: bool = False


class GMEventIngestResponse(BaseModel):
    """GM ingest response."""

    event_id: str
    gm_node_count: int
    gm_edge_count: int
    dispatched: bool
    recipients: List[str] = Field(default_factory=list)
