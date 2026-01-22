"""
Graph data models.
"""
from datetime import datetime
from typing import Dict, List
from pydantic import BaseModel, Field, ConfigDict


class MemoryNode(BaseModel):
    """Graph node model."""
    id: str
    type: str
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    importance: float = 0.0
    properties: Dict = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class MemoryEdge(BaseModel):
    """Graph edge model."""
    id: str
    source: str
    target: str
    relation: str
    weight: float = 1.0
    created_at: datetime = Field(default_factory=datetime.now)
    properties: Dict = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class GraphData(BaseModel):
    """Serializable graph container."""
    nodes: List[MemoryNode] = Field(default_factory=list)
    edges: List[MemoryEdge] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
