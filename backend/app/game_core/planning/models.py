"""Narrative planning output models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PlanningDirective:
    """Base planning directive."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreateQuestPlan:
    quest_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SpawnQuestNpcPlan:
    npc_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DirectNpcPlan:
    npc_id: str
    directive: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlantEnvironmentalPlan:
    area_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PublishBulletinPlan:
    board_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EscalatePlan:
    delta: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AdjustPacingPlan:
    frozen: bool
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RetireQuestPlan:
    quest_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FillAreaPlan:
    area_id: str
    payload: dict[str, Any] = field(default_factory=dict)
