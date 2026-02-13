"""
Game loop models.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class GamePhase(str, Enum):
    """游戏阶段"""
    CHARACTER_CREATION = "character_creation"
    IDLE = "idle"
    SCENE = "scene"
    DIALOGUE = "dialogue"
    COMBAT = "combat"
    ENDED = "ended"


class ChatMode(str, Enum):
    """玩家输入模式"""
    THINK = "think"  # 脑内说话
    SAY = "say"      # 脑外说话（聊天室广播）


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
    model_config = ConfigDict(populate_by_name=True)

    combat_id: Optional[str] = None
    use_engine: bool = True
    result_override: Optional[Dict] = None
    summary_override: Optional[str] = None
    dispatch: bool = True
    recipients: Optional[List[str]] = None
    per_character: Dict = Field(default_factory=dict)
    write_indexes: bool = False
    validate_input: bool = Field(default=False, alias="validate")
    strict: bool = False


class CombatResolveResponse(BaseModel):
    """Resolve combat response."""
    combat_id: str
    event_id: Optional[str] = None
    dispatched: bool = False


# ==================== Phase 6: 游戏大师相关模型 ====================


class EnterSceneRequest(BaseModel):
    """进入场景请求"""
    scene: SceneState
    generate_description: bool = True


class EnterSceneResponse(BaseModel):
    """进入场景响应"""
    scene: SceneState
    description: str = ""
    npc_memories: Dict[str, str] = Field(default_factory=dict)


class PlayerInputRequest(BaseModel):
    """玩家输入请求"""
    input: str
    input_type: Optional[str] = None  # narration/dialogue/combat/system
    mode: Optional[str] = None  # think/say（可选，覆盖当前模式）
    is_private: bool = False  # 私密对话模式
    private_target: Optional[str] = None  # 私密对话目标队友 character_id


class PlayerInputResponse(BaseModel):
    """玩家输入响应"""
    type: str  # narration/dialogue/combat/system/error
    response: str
    speaker: str = "GM"
    narration: Optional[str] = None
    npc_id: Optional[str] = None
    event_recorded: bool = False
    tool_called: bool = False
    recalled_memory: Optional[str] = None
    available_actions: List[Dict] = Field(default_factory=list)
    state_changes: Dict = Field(default_factory=dict)
    responses: List[Dict] = Field(default_factory=list)  # 额外回应（聊天室模式）


class StartDialogueRequest(BaseModel):
    """开始对话请求"""
    npc_id: str


class StartDialogueResponse(BaseModel):
    """开始对话响应"""
    npc_id: str
    npc_name: str
    greeting: str
    narration: Optional[str] = None


class TriggerCombatRequest(BaseModel):
    """触发战斗请求"""
    enemies: List[Dict]
    player_state: Dict
    combat_description: str = ""
    environment: Dict = Field(default_factory=dict)


class TriggerCombatResponse(BaseModel):
    """触发战斗响应"""
    combat_id: str
    narration: str
    combat_state: Dict
    available_actions: List[Dict] = Field(default_factory=list)


class CombatActionRequest(BaseModel):
    """战斗行动请求"""
    action_id: str


class CombatActionResponse(BaseModel):
    """战斗行动响应"""
    phase: str  # action/end
    narration: str
    action_result: Optional[Dict] = None
    combat_result: Optional[Dict] = None
    available_actions: List[Dict] = Field(default_factory=list)


class DiceRollAPIRequest(BaseModel):
    """玩家主动掷骰请求"""
    skill: str = ""
    ability: str = ""
    dc: Optional[int] = None
    context: str = ""


class GameContextResponse(BaseModel):
    """游戏上下文响应"""
    world_id: str
    session_id: str
    phase: GamePhase
    game_day: int
    current_scene: Optional[SceneState] = None
    current_npc: Optional[str] = None
    known_characters: List[str] = Field(default_factory=list)


# ==================== Phase 7: 区域导航相关模型 ====================


class GameTimeResponse(BaseModel):
    """游戏时间响应"""
    day: int
    hour: int
    minute: int
    period: str  # dawn/day/dusk/night
    formatted: str


class LocationResponse(BaseModel):
    """当前位置响应"""
    location_id: str
    location_name: str
    description: str
    atmosphere: str = ""
    danger_level: str = "low"
    available_destinations: List[Dict] = Field(default_factory=list)
    npcs_present: List[str] = Field(default_factory=list)
    available_actions: List[str] = Field(default_factory=list)
    time: GameTimeResponse


class NavigateRequest(BaseModel):
    """导航请求"""
    destination: Optional[str] = None  # 目的地 ID 或名称
    direction: Optional[str] = None    # 方向（north/south/east/west）


class TravelSegment(BaseModel):
    """旅途分段"""
    from_id: str
    from_name: str
    to_id: str
    to_name: str
    travel_time: str
    time_minutes: int
    danger_level: str
    narration: str = ""
    event: Optional[Dict] = None


class NavigateResponse(BaseModel):
    """导航响应"""
    success: bool
    narration: str = ""
    segments: List[TravelSegment] = Field(default_factory=list)
    new_location: Optional[LocationResponse] = None
    time_elapsed_minutes: int = 0
    events: List[Dict] = Field(default_factory=list)
    time: Optional[GameTimeResponse] = None
    error: Optional[str] = None
