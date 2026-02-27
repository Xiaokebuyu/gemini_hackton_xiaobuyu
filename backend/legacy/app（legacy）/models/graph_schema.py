"""
Graph schema definitions.
"""
from enum import Enum
from typing import Set


class NodeType(str, Enum):
    """Graph node types."""

    IDENTITY = "identity"
    PERSON = "person"
    LOCATION = "location"
    EVENT = "event"
    RUMOR = "rumor"
    KNOWLEDGE = "knowledge"
    ITEM = "item"
    ORGANIZATION = "organization"
    GOAL = "goal"
    EMOTION = "emotion"
    LOCATION_REF = "location_ref"
    PERSON_REF = "person_ref"
    ITEM_REF = "item_ref"


class RelationType(str, Enum):
    """Graph edge relation types."""

    LOCATED_IN = "located_in"
    PART_OF = "part_of"
    OWNS = "owns"
    WORKS_AT = "works_at"
    FAMILY = "family"
    FRIEND = "friend"
    ENEMY = "enemy"
    COLLEAGUE = "colleague"
    KNOWS = "knows"
    PARTICIPATED = "participated"
    WITNESSED = "witnessed"
    HEARD_ABOUT = "heard_about"
    CAUSED = "caused"
    BELIEVES = "believes"
    SUSPECTS = "suspects"
    KNOWS_THAT = "knows_that"
    LIKES = "likes"
    FEARS = "fears"
    TRUSTS = "trusts"
    HATES = "hates"
    REFERS_TO = "refers_to"


GRAPH_NODE_TYPES: Set[str] = {item.value for item in NodeType}
GRAPH_RELATIONS: Set[str] = {item.value for item in RelationType}
EVENT_REQUIRED_PROPERTIES: Set[str] = {"day", "summary"}
