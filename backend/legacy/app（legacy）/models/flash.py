"""
Flash service request/response models.
"""
from typing import Dict, List, Optional
from pydantic import BaseModel

from app.models.graph import GraphData


class RecallResponse(BaseModel):
    """Recall response."""
    seed_nodes: List[str]
    activated_nodes: Dict[str, float]
    subgraph: Optional[GraphData] = None
    used_subgraph: bool = False
    translated_memory: Optional[str] = None
