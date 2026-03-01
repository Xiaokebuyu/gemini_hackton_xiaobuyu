"""Narrative-layer execution context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from app.game_core.content import WorldInstance
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer

if TYPE_CHECKING:
    from app.game_core.narrative.role_proxy import RoleStateProxy


@dataclass(slots=True)
class AgentContext:
    """Execution context passed to agent tools."""

    role: str
    world: WorldInstance
    state: StateContainer | RoleStateProxy
    scene_entries: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    execute_command: Callable[[Command], ExecuteResult] | None = None

    def run_command(self, command: Command) -> ExecuteResult:
        if self.execute_command is None:
            return ExecuteResult.error("command execution unavailable")
        return self.execute_command(command)
