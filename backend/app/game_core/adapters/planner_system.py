"""Planner system ports and assembly models.

The planner runtime is composed of:
- one central blackboard that maintains long-term strategy notes / story facts
- multiple independent subsystem agents that emit business directives

All concrete implementations live in app/ and are injected through these ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PlannerAgentPort(Protocol):
    """Independent subsystem-planning agent."""

    @property
    def history_key(self) -> str:
        """Stable persistence slot for this agent's history."""
        ...

    async def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return a planner-style JSON payload for one subsystem turn."""
        ...

    def export_history(self) -> list[dict[str, Any]]:
        """Serialize agent conversation history."""
        ...

    def import_history(self, data: list[dict[str, Any]]) -> None:
        """Restore agent conversation history."""
        ...


@runtime_checkable
class PlannerBlackboardPort(Protocol):
    """Central strategy blackboard coordinator."""

    @property
    def history_key(self) -> str:
        """Stable persistence slot for blackboard history."""
        ...

    async def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return strategy notes / story facts / scheduling hints."""
        ...

    def export_history(self) -> list[dict[str, Any]]:
        """Serialize blackboard conversation history."""
        ...

    def import_history(self, data: list[dict[str, Any]]) -> None:
        """Restore blackboard conversation history."""
        ...


@dataclass(slots=True)
class PlannerSystemAssembly:
    """All planner-system participants wired for one runtime."""

    blackboard: PlannerBlackboardPort | None = None
    quest_manager_agent: PlannerAgentPort | None = None
    npc_director_agent: PlannerAgentPort | None = None
    world_builder_agent: PlannerAgentPort | None = None
    narrative_weaver_agent: PlannerAgentPort | None = None
    outline_generator: Any = None  # MilestoneOutlineGeneratorPort
