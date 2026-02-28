"""Settlement context passed into hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.game_core.content import WorldInstance
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.rules import RulesEngine
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateChange, StateContainer, StateDelta


@dataclass(slots=True)
class SettlementContext:
    """Controlled access surface for settlement hooks."""

    change_log: list[StateChange]
    state: StateContainer
    world: WorldInstance
    scene_bus: SceneBus
    _rules_engine: RulesEngine = field(repr=False)
    _apply_delta: Callable[[StateDelta | None], None] = field(repr=False)

    def execute_command(self, cmd: Command) -> ExecuteResult:
        result = self._rules_engine.execute(cmd, self.state, self.world)
        if result.success and result.delta is not None:
            self._apply_delta(result.delta)
        return result
