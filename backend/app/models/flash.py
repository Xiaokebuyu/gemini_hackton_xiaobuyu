"""
Flash service request/response models.
"""
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from app.models.activation import SpreadingActivationConfig
from app.models.graph import GraphData, MemoryEdge, MemoryNode


class EventIngestRequest(BaseModel):
    """Event ingestion request."""
    model_config = ConfigDict(populate_by_name=True)

    event_id: Optional[str] = None
    description: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    game_day: Optional[int] = None
    location: Optional[str] = None
    perspective: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    witnesses: List[str] = Field(default_factory=list)
    visibility_public: bool = False
    nodes: List[MemoryNode] = Field(default_factory=list)
    edges: List[MemoryEdge] = Field(default_factory=list)
    state_updates: Dict = Field(default_factory=dict)
    write_indexes: bool = False
    validate_input: bool = Field(default=False, alias="validate")
    strict: bool = False


class EventIngestResponse(BaseModel):
    """Event ingestion response."""
    event_id: str
    node_count: int
    edge_count: int
    state_updated: bool
    note: Optional[str] = None


class RecallRequest(BaseModel):
    """Recall request."""
    seed_nodes: List[str]
    config: Optional[SpreadingActivationConfig] = None
    include_subgraph: bool = True
    resolve_refs: bool = False
    use_subgraph: bool = False
    subgraph_depth: int = 2
    subgraph_direction: str = "both"


class RecallResponse(BaseModel):
    """Recall response."""
    seed_nodes: List[str]
    activated_nodes: Dict[str, float]
    subgraph: Optional[GraphData] = None
    used_subgraph: bool = False
    translated_memory: Optional[str] = None


# ==================== LLM增强的请求/响应模型 ====================


class NaturalEventIngestRequest(BaseModel):
    """自然语言事件摄入请求"""
    event_description: str
    game_day: int
    location: Optional[str] = None
    perspective: Optional[str] = None
    write_indexes: bool = False


class NaturalEventIngestResponse(BaseModel):
    """自然语言事件摄入响应"""
    event_id: str
    node_count: int
    edge_count: int
    state_updated: bool
    encoded_nodes: List[MemoryNode] = Field(default_factory=list)
    encoded_edges: List[MemoryEdge] = Field(default_factory=list)
    note: Optional[str] = None


class NaturalRecallRequest(BaseModel):
    """自然语言记忆检索请求"""
    query: str
    recent_conversation: Optional[str] = None
    translate: bool = True
    include_subgraph: bool = True
    resolve_refs: bool = False
    use_subgraph: bool = False
    subgraph_depth: int = 2


class NaturalRecallResponse(BaseModel):
    """自然语言记忆检索响应"""
    query: str
    search_intent: Optional[str] = None
    seed_nodes: List[str] = Field(default_factory=list)
    activated_nodes: Dict[str, float] = Field(default_factory=dict)
    subgraph: Optional[GraphData] = None
    translated_memory: Optional[str] = None
    note: Optional[str] = None
