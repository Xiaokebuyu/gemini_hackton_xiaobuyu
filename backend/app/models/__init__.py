"""
数据模型包
"""
from .session import Session, SessionCreate
from .topic import TopicThread, TopicCreate, ArtifactVersion
from .message import Message, MessageCreate, MessageRole
from .graph import MemoryNode, MemoryEdge, GraphData
from .graph_schema import NodeType, RelationType
from .activation import SpreadingActivationConfig
from .flash import EventIngestRequest, EventIngestResponse, RecallRequest, RecallResponse
from .character_profile import CharacterProfile
from .event import (
    Event,
    EventType,
    EventContent,
    EventVisibility,
    EventDispatchOverride,
    GMEventIngestRequest,
    GMEventIngestResponse,
)
from .game import (
    ChatMode,
    SceneState,
    CombatContext,
    GameSessionState,
    CreateSessionRequest,
    CreateSessionResponse,
    UpdateSceneRequest,
    CombatStartRequest,
    CombatStartResponse,
    CombatResolveRequest,
    CombatResolveResponse,
)
from .npc_instance import (
    NPCConfig,
    NPCInstanceState,
    NPCInstanceInfo,
    GraphizeTrigger,
    MemoryInjection,
    QueryUnderstanding,
)
from .context_window import (
    WindowMessage,
    ContextWindowState,
    ContextWindowSnapshot,
    AddMessageResult,
    GraphizeRequest,
    RemoveGraphizedResult,
)
from .graph_elements import (
    TranscriptMessage,
    TranscriptRange,
    EventGroupNode,
    EventNode,
    GraphEdgeSpec,
    ExtractedElements,
    GraphizeResult,
    MergeResult,
    MemoryWithContext,
)
from .graph_nodes import (
    GraphScope,
    CRPGNodeType,
    CRPGRelationType,
    RELATION_BASE_WEIGHT,
    default_importance,
    ChapterNode,
    AreaNode,
    LocationNode,
    CharacterNode,
    EventNode2,
    ChoiceNode,
    QuestNode,
)

__all__ = [
    "Session",
    "SessionCreate",
    "TopicThread",
    "TopicCreate",
    "ArtifactVersion",
    "Message",
    "MessageCreate",
    "MessageRole",
    "MemoryNode",
    "MemoryEdge",
    "GraphData",
    "NodeType",
    "RelationType",
    "EventIngestRequest",
    "EventIngestResponse",
    "RecallRequest",
    "RecallResponse",
    "SpreadingActivationConfig",
    "CharacterProfile",
    "Event",
    "EventType",
    "EventContent",
    "EventVisibility",
    "EventDispatchOverride",
    "GMEventIngestRequest",
    "GMEventIngestResponse",
    "SceneState",
    "CombatContext",
    "ChatMode",
    "GameSessionState",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "UpdateSceneRequest",
    "CombatStartRequest",
    "CombatStartResponse",
    "CombatResolveRequest",
    "CombatResolveResponse",
    # NPC Instance Pool models
    "NPCConfig",
    "NPCInstanceState",
    "NPCInstanceInfo",
    "GraphizeTrigger",
    "MemoryInjection",
    "QueryUnderstanding",
    # Context Window models
    "WindowMessage",
    "ContextWindowState",
    "ContextWindowSnapshot",
    "AddMessageResult",
    "GraphizeRequest",
    "RemoveGraphizedResult",
    # Graph Elements models
    "TranscriptMessage",
    "TranscriptRange",
    "EventGroupNode",
    "EventNode",
    "GraphEdgeSpec",
    "ExtractedElements",
    "GraphizeResult",
    "MergeResult",
    "MemoryWithContext",
    # CRPG Graph Nodes v2
    "GraphScope",
    "CRPGNodeType",
    "CRPGRelationType",
    "RELATION_BASE_WEIGHT",
    "default_importance",
    "ChapterNode",
    "AreaNode",
    "LocationNode",
    "CharacterNode",
    "EventNode2",
    "ChoiceNode",
    "QuestNode",
]
