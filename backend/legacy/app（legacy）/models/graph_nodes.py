"""
CRPG graph node/edge type definitions and typed node wrappers (v2).

Defines:
- GraphScope: unified scope addressing for multi-level graph storage
- CRPGNodeType / CRPGRelationType: v2 enums aligned with graph_architecture_v2
- RELATION_BASE_WEIGHT: canonical weight lookup for each relation
- Typed node wrappers with to_memory_node() serialization
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.graph import MemoryNode


# ==================== GraphScope ====================


class GraphScope(BaseModel):
    """Unified scope addressing for hierarchical graph storage.

    Maps to Firestore paths:
        world    -> worlds/{wid}/graphs/world/
        chapter  -> worlds/{wid}/chapters/{cid}/graph/
        area     -> worlds/{wid}/chapters/{cid}/areas/{aid}/graph/
        location -> worlds/{wid}/chapters/{cid}/areas/{aid}/locations/{lid}/graph/
        character-> worlds/{wid}/characters/{char_id}/
        camp     -> worlds/{wid}/camp/graph/
    """

    scope_type: str  # "world" | "chapter" | "area" | "location" | "character" | "camp"
    chapter_id: Optional[str] = None
    area_id: Optional[str] = None
    location_id: Optional[str] = None
    character_id: Optional[str] = None

    def firestore_prefix(self, world_id: str) -> str:
        """Return the Firestore collection prefix for this scope."""
        base = f"worlds/{world_id}"
        if self.scope_type == "world":
            return f"{base}/graphs/world"
        if self.scope_type == "chapter":
            return f"{base}/chapters/{self.chapter_id}/graph"
        if self.scope_type == "area":
            return (
                f"{base}/chapters/{self.chapter_id}"
                f"/areas/{self.area_id}/graph"
            )
        if self.scope_type == "location":
            return (
                f"{base}/chapters/{self.chapter_id}"
                f"/areas/{self.area_id}"
                f"/locations/{self.location_id}/graph"
            )
        if self.scope_type == "character":
            return f"{base}/characters/{self.character_id}"
        if self.scope_type == "camp":
            return f"{base}/camp/graph"
        raise ValueError(f"Unknown scope_type: {self.scope_type}")


# ==================== Node Type Enum (v2) ====================


class CRPGNodeType(str, Enum):
    """CRPG node types (v2 architecture)."""

    # Structural
    CHAPTER = "chapter"
    AREA = "area"
    LOCATION = "location"
    CHARACTER = "character"

    # Content
    EVENT = "event"
    CHOICE = "choice"
    QUEST = "quest"
    FACTION = "faction"
    DEITY = "deity"
    RACE = "race"
    MONSTER = "monster"
    ITEM = "item"
    CONCEPT = "concept"
    KNOWLEDGE = "knowledge"


# ==================== Relation Type Enum (v2) ====================


class CRPGRelationType(str, Enum):
    """CRPG edge relation types (v2 architecture)."""

    # Structural (worldbook, no decay)
    OPENS_AREA = "opens_area"
    HAS_LOCATION = "has_location"
    CONNECTS_TO = "connects_to"
    HOSTS_NPC = "hosts_npc"
    DEFAULT_AREA = "default_area"

    # World-lore (worldbook, no decay)
    COMPANION_OF = "companion_of"
    ENEMY_OF = "enemy_of"
    MEMBER_OF = "member_of"
    WORSHIPS = "worships"
    ALLY_OF = "ally_of"
    RULES = "rules"
    NATIVE_TO = "native_to"
    LOCATED_AT = "located_at"
    KNOWS = "knows"

    # Social (runtime, dynamic weight)
    APPROVES = "approves"
    TRUSTS = "trusts"
    RESPECTS = "respects"
    FEARS = "fears"
    RIVALS = "rivals"
    ROMANTIC = "romantic"

    # Combat outcome
    FOUGHT_IN = "fought_in"
    DEFEATED = "defeated"
    DEFEATED_BY = "defeated_by"
    PROTECTED = "protected"
    HEALED = "healed"

    # Causal / quest
    CAUSED = "caused"
    LED_TO = "led_to"
    RESULTED_FROM = "resulted_from"
    ADVANCES = "advances"
    PERSPECTIVE_OF = "perspective_of"


# ==================== Relation Base Weights ====================


RELATION_BASE_WEIGHT: Dict[str, float] = {
    # Structural
    CRPGRelationType.OPENS_AREA: 1.0,
    CRPGRelationType.HAS_LOCATION: 1.0,
    CRPGRelationType.CONNECTS_TO: 0.8,
    CRPGRelationType.HOSTS_NPC: 0.7,
    CRPGRelationType.DEFAULT_AREA: 0.6,

    # World-lore
    CRPGRelationType.COMPANION_OF: 0.9,
    CRPGRelationType.ENEMY_OF: 0.8,
    CRPGRelationType.MEMBER_OF: 0.7,
    CRPGRelationType.WORSHIPS: 0.6,
    CRPGRelationType.ALLY_OF: 0.6,
    CRPGRelationType.RULES: 0.7,
    CRPGRelationType.NATIVE_TO: 0.5,
    CRPGRelationType.LOCATED_AT: 0.6,
    CRPGRelationType.KNOWS: 0.5,

    # Social — values here are defaults; actual weight derived at runtime
    CRPGRelationType.APPROVES: 0.5,
    CRPGRelationType.TRUSTS: 0.5,
    CRPGRelationType.RESPECTS: 0.5,
    CRPGRelationType.FEARS: 0.5,
    CRPGRelationType.RIVALS: 0.7,
    CRPGRelationType.ROMANTIC: 0.0,

    # Combat
    CRPGRelationType.FOUGHT_IN: 0.8,
    CRPGRelationType.DEFEATED: 0.7,
    CRPGRelationType.DEFEATED_BY: 0.6,
    CRPGRelationType.PROTECTED: 0.8,
    CRPGRelationType.HEALED: 0.7,

    # Causal / quest
    CRPGRelationType.CAUSED: 0.9,
    CRPGRelationType.LED_TO: 0.9,
    CRPGRelationType.RESULTED_FROM: 0.9,
    CRPGRelationType.ADVANCES: 0.8,
    CRPGRelationType.PERSPECTIVE_OF: 1.0,
}


# ==================== Importance Defaults ====================


# Maps (node_type, optional sub_type) -> default importance
_IMPORTANCE_DEFAULTS: Dict[str, float] = {
    CRPGNodeType.CHAPTER: 0.95,
    CRPGNodeType.AREA: 0.8,
    CRPGNodeType.LOCATION: 0.6,
    CRPGNodeType.FACTION: 0.6,
    CRPGNodeType.DEITY: 0.7,
    CRPGNodeType.RACE: 0.4,
    CRPGNodeType.MONSTER: 0.4,
    CRPGNodeType.ITEM: 0.3,
    CRPGNodeType.CONCEPT: 0.3,
    CRPGNodeType.KNOWLEDGE: 0.4,
    CRPGNodeType.CHOICE: 0.9,
}

# Character importance by role
CHARACTER_IMPORTANCE: Dict[str, float] = {
    "main": 0.95,
    "secondary": 0.7,
    "passerby": 0.3,
}

# Event importance by sub-type
EVENT_IMPORTANCE: Dict[str, float] = {
    "combat": 0.7,
    "quest": 0.8,
    "social": 0.4,
    "choice": 0.9,
}

# Quest importance by status
QUEST_IMPORTANCE: Dict[str, float] = {
    "active": 0.8,
    "locked": 0.4,
    "completed": 0.5,
}


def default_importance(node_type: str, sub_type: Optional[str] = None) -> float:
    """Compute deterministic importance for a given node type and sub-type."""
    if node_type == CRPGNodeType.CHARACTER and sub_type:
        return CHARACTER_IMPORTANCE.get(sub_type, 0.5)
    if node_type == CRPGNodeType.EVENT and sub_type:
        return EVENT_IMPORTANCE.get(sub_type, 0.5)
    if node_type == CRPGNodeType.QUEST and sub_type:
        return QUEST_IMPORTANCE.get(sub_type, 0.5)
    return _IMPORTANCE_DEFAULTS.get(node_type, 0.5)


# ==================== Scope Properties Mixin ====================


class _ScopePropertiesMixin(BaseModel):
    """Common scope-tracking properties embedded in node.properties."""

    scope_type: Optional[str] = None
    chapter_id: Optional[str] = None
    area_id: Optional[str] = None
    location_id: Optional[str] = None
    perspective: Optional[str] = None  # "narrative" | "personal"
    character_id: Optional[str] = None

    def scope_dict(self) -> Dict:
        """Return non-None scope fields as a dict (for embedding in properties)."""
        return {k: v for k, v in {
            "scope_type": self.scope_type,
            "chapter_id": self.chapter_id,
            "area_id": self.area_id,
            "location_id": self.location_id,
            "perspective": self.perspective,
            "character_id": self.character_id,
        }.items() if v is not None}


# ==================== Typed Node Wrappers ====================


class ChapterNode(_ScopePropertiesMixin):
    """Chapter arc node."""

    id: str
    name: str
    order: int = 0
    status: str = "locked"  # "locked" | "active" | "completed"
    started_at: Optional[datetime] = None
    description: Optional[str] = None

    def to_memory_node(self) -> MemoryNode:
        props = self.scope_dict()
        props.update({
            "order": self.order,
            "status": self.status,
        })
        if self.started_at:
            props["started_at"] = self.started_at.isoformat()
        if self.description:
            props["description"] = self.description
        return MemoryNode(
            id=self.id,
            type=CRPGNodeType.CHAPTER,
            name=self.name,
            importance=default_importance(CRPGNodeType.CHAPTER),
            properties=props,
        )


class AreaNode(_ScopePropertiesMixin):
    """Map area node."""

    id: str
    name: str
    danger_level: Optional[int] = None
    atmosphere: Optional[str] = None
    description: Optional[str] = None

    def to_memory_node(self) -> MemoryNode:
        props = self.scope_dict()
        if self.danger_level is not None:
            props["danger_level"] = self.danger_level
        if self.atmosphere:
            props["atmosphere"] = self.atmosphere
        if self.description:
            props["description"] = self.description
        return MemoryNode(
            id=self.id,
            type=CRPGNodeType.AREA,
            name=self.name,
            importance=default_importance(CRPGNodeType.AREA),
            properties=props,
        )


class LocationNode(_ScopePropertiesMixin):
    """Sub-location node."""

    id: str
    name: str
    description: Optional[str] = None
    resident_npcs: List[str] = Field(default_factory=list)

    def to_memory_node(self) -> MemoryNode:
        props = self.scope_dict()
        if self.description:
            props["description"] = self.description
        if self.resident_npcs:
            props["resident_npcs"] = self.resident_npcs
        return MemoryNode(
            id=self.id,
            type=CRPGNodeType.LOCATION,
            name=self.name,
            importance=default_importance(CRPGNodeType.LOCATION),
            properties=props,
        )


class CharacterNode(_ScopePropertiesMixin):
    """Character node (cross-chapter, memory accumulates)."""

    id: str
    name: str
    role: str = "secondary"  # "main" | "secondary" | "passerby"
    personality: Optional[str] = None
    background: Optional[str] = None

    def to_memory_node(self) -> MemoryNode:
        props = self.scope_dict()
        props["role"] = self.role
        if self.personality:
            props["personality"] = self.personality
        if self.background:
            props["background"] = self.background
        return MemoryNode(
            id=self.id,
            type=CRPGNodeType.CHARACTER,
            name=self.name,
            importance=default_importance(CRPGNodeType.CHARACTER, self.role),
            properties=props,
        )


class EventNode2(_ScopePropertiesMixin):
    """Narrative event node (dual-perspective).

    Named EventNode2 to avoid collision with graph_elements.EventNode.
    """

    id: str
    name: str
    sub_type: str = "social"  # "combat" | "quest" | "social" | "choice"
    day: Optional[int] = None
    summary: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    evidence_text: Optional[str] = None

    def to_memory_node(self) -> MemoryNode:
        props = self.scope_dict()
        props["sub_type"] = self.sub_type
        if self.day is not None:
            props["day"] = self.day
        if self.summary:
            props["summary"] = self.summary
        if self.participants:
            props["participants"] = self.participants
        if self.evidence_text:
            props["evidence_text"] = self.evidence_text
        return MemoryNode(
            id=self.id,
            type=CRPGNodeType.EVENT,
            name=self.name,
            importance=default_importance(CRPGNodeType.EVENT, self.sub_type),
            properties=props,
        )


class ChoiceNode(_ScopePropertiesMixin):
    """Player choice node."""

    id: str
    name: str
    description: Optional[str] = None
    consequences: List[str] = Field(default_factory=list)
    resolved: bool = False

    def to_memory_node(self) -> MemoryNode:
        props = self.scope_dict()
        if self.description:
            props["description"] = self.description
        if self.consequences:
            props["consequences"] = self.consequences
        props["resolved"] = self.resolved
        return MemoryNode(
            id=self.id,
            type=CRPGNodeType.CHOICE,
            name=self.name,
            importance=default_importance(CRPGNodeType.CHOICE),
            properties=props,
        )


class QuestNode(_ScopePropertiesMixin):
    """Quest / loyalty mission node."""

    id: str
    name: str
    status: str = "locked"  # "active" | "locked" | "completed"
    owner_character_id: Optional[str] = None
    description: Optional[str] = None
    objectives: List[str] = Field(default_factory=list)

    def to_memory_node(self) -> MemoryNode:
        props = self.scope_dict()
        props["status"] = self.status
        if self.owner_character_id:
            props["owner_character_id"] = self.owner_character_id
        if self.description:
            props["description"] = self.description
        if self.objectives:
            props["objectives"] = self.objectives
        return MemoryNode(
            id=self.id,
            type=CRPGNodeType.QUEST,
            name=self.name,
            importance=default_importance(CRPGNodeType.QUEST, self.status),
            properties=props,
        )
