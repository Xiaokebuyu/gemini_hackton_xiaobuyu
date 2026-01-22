"""
Flash service request/response models.
"""
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.services.spreading_activation import SpreadingActivationConfig


class EventIngestRequest(BaseModel):
    """Event ingestion request."""
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
    validate: bool = False
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
