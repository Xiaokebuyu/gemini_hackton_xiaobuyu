"""Character creation API request/response models."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CharacterCreationRequest(BaseModel):
    """Request to create a player character."""

    name: str
    race: str
    character_class: str
    background: str = ""
    ability_scores: Dict[str, int]  # str/dex/con/int/wis/cha (pre-racial)
    skill_proficiencies: List[str] = Field(default_factory=list)
    backstory: str = ""


class CharacterCreationResponse(BaseModel):
    """Response after character creation."""

    character: Dict[str, Any]
    opening_narration: str
    phase: str = "active"


class CharacterCreationOptions(BaseModel):
    """Available options for character creation."""

    races: Dict[str, Any]
    classes: Dict[str, Any]
    backgrounds: Dict[str, Any]
    skills: Dict[str, Any]
    point_buy: Dict[str, Any]
