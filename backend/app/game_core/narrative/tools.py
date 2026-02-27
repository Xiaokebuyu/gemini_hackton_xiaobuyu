"""Narrative agent tool abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult


class AgentTool(ABC):
    """Command-construction tool contract for agent layers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool identifier exposed to the agent."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the agent."""

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON-schema-like parameter definition."""

    @property
    @abstractmethod
    def allowed_roles(self) -> list[str]:
        """Roles allowed to invoke this tool."""

    @abstractmethod
    async def execute(self, params: dict[str, Any], context: AgentContext) -> ToolResult:
        """Execute the tool."""
