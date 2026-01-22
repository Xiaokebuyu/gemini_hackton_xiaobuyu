"""
Game loop models.
"""
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class SceneState(BaseModel):
    """Scene state."""
    scene_id: Optional[str] = None
    description: str = ""
    location: Optional[str] = None
    atmosphere: Optional[str] = None
    participants: List[str] = Field(default_factory=list)


class CombatContext(BaseModel):
    """Combat context for later dispatch."""
    location: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    witnesses: List[str] = Field(default_factory=list)
    visibility_public: bool = False
    known_characters: List[str] = Field(default_factory=list)
    character_locations: Dict[str, str] = Field(default_factory=dict)


class GameSessionState(BaseModel):
    """Game session state."""
    session_id: str
    world_id: str
    status: str = "idle"
    current_scene: Optional[SceneState] = None
    participants: List[str] = Field(default_factory=list)
    active_combat_id: Optional[str] = None
    combat_context: Optional[CombatContext] = None
    updated_at: datetime = Field(default_factory=datetime.now)
    metadata: Dict = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    """Create session request."""
    session_id: Optional[str] = None
    participants: List[str] = Field(default_factory=list)


class CreateSessionResponse(BaseModel):
    """Create session response."""
    session: GameSessionState


class UpdateSceneRequest(BaseModel):
    """Update scene request."""
    scene: SceneState


class CombatStartRequest(BaseModel):
    """Start combat request."""
    player_state: Dict
    enemies: List[Dict]
    allies: List[Dict] = Field(default_factory=list)
    environment: Dict = Field(default_factory=dict)
    combat_context: CombatContext = Field(default_factory=CombatContext)


class CombatStartResponse(BaseModel):
    """Start combat response."""
    combat_id: str
    combat_state: Dict
    session: GameSessionState


class CombatResolveRequest(BaseModel):
    """Resolve combat request."""
    combat_id: Optional[str] = None
    use_engine: bool = True
    result_override: Optional[Dict] = None
    summary_override: Optional[str] = None
    dispatch: bool = True
    recipients: Optional[List[str]] = None
    per_character: Dict = Field(default_factory=dict)
    write_indexes: bool = False
    validate: bool = False
    strict: bool = False


class CombatResolveResponse(BaseModel):
    """Resolve combat response."""
    combat_id: str
    event_id: Optional[str] = None
    dispatched: bool = False
