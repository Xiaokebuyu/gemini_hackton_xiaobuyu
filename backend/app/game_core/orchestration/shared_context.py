"""Shared orchestration context."""

from __future__ import annotations

from dataclasses import dataclass

from app.game_core.content import WorldInstance
from app.game_core.narrative.companion_runtime import CompanionRuntimeManager
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer


@dataclass(slots=True)
class SharedContext:
    """Core objects shared by pipeline and settlement layers."""

    world: WorldInstance
    state: StateContainer
    rules_engine: RulesEngine
    scene_bus: SceneBus
    companion_manager: CompanionRuntimeManager | None = None
