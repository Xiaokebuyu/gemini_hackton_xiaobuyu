"""Character profile model."""
from typing import Dict, Optional

from pydantic import BaseModel, Field


class CharacterProfile(BaseModel):
    """Character profile data."""

    name: str = ""
    occupation: Optional[str] = None
    age: Optional[int] = None
    personality: Optional[str] = None
    speech_pattern: Optional[str] = None
    example_dialogue: Optional[str] = None
    system_prompt: Optional[str] = None
    metadata: Dict = Field(default_factory=dict)
