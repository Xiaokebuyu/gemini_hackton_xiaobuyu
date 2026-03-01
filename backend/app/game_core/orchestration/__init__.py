"""Orchestration layer package for coordinators, buses, and hooks."""

from app.game_core.orchestration.action_dispatcher import ActionDispatcher
from app.game_core.orchestration.context_assembler import ContextAssembler
from app.game_core.orchestration.defaults import (
    DEFAULT_ACTION_COMMAND_TYPES,
    DEFAULT_SETTLEMENT_HOOK_TYPES,
    build_default_action_dispatcher,
    register_default_action_mappings,
    build_default_settlement_hooks,
    register_default_settlement_hooks,
)
from app.game_core.orchestration.pipeline import PipelineHook, PipelineOrchestrator
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.orchestration.npc_interaction import (
    NpcInteractionCoordinator,
    NpcInteractionResult,
)
from app.game_core.orchestration.tick_coordinator import TickCoordinator

__all__ = [
    "ActionDispatcher",
    "ContextAssembler",
    "DEFAULT_ACTION_COMMAND_TYPES",
    "DEFAULT_SETTLEMENT_HOOK_TYPES",
    "NpcInteractionCoordinator",
    "NpcInteractionResult",
    "PipelineHook",
    "PipelineOrchestrator",
    "SceneBus",
    "SettlementContext",
    "SharedContext",
    "TickCoordinator",
    "build_default_action_dispatcher",
    "register_default_action_mappings",
    "build_default_settlement_hooks",
    "register_default_settlement_hooks",
]
