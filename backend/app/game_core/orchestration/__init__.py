"""Lazy exports for orchestration package helpers."""

from __future__ import annotations

from importlib import import_module
from typing import Any

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

_EXPORTS = {
    "ActionDispatcher": ("app.game_core.orchestration.action_dispatcher", "ActionDispatcher"),
    "ContextAssembler": ("app.game_core.orchestration.context_assembler", "ContextAssembler"),
    "DEFAULT_ACTION_COMMAND_TYPES": ("app.game_core.orchestration.defaults", "DEFAULT_ACTION_COMMAND_TYPES"),
    "DEFAULT_SETTLEMENT_HOOK_TYPES": ("app.game_core.orchestration.defaults", "DEFAULT_SETTLEMENT_HOOK_TYPES"),
    "build_default_action_dispatcher": ("app.game_core.orchestration.defaults", "build_default_action_dispatcher"),
    "register_default_action_mappings": ("app.game_core.orchestration.defaults", "register_default_action_mappings"),
    "build_default_settlement_hooks": ("app.game_core.orchestration.defaults", "build_default_settlement_hooks"),
    "register_default_settlement_hooks": ("app.game_core.orchestration.defaults", "register_default_settlement_hooks"),
    "PipelineHook": ("app.game_core.orchestration.pipeline", "PipelineHook"),
    "PipelineOrchestrator": ("app.game_core.orchestration.pipeline", "PipelineOrchestrator"),
    "SceneBus": ("app.game_core.orchestration.scene_bus", "SceneBus"),
    "SettlementContext": ("app.game_core.orchestration.settlement", "SettlementContext"),
    "SharedContext": ("app.game_core.orchestration.shared_context", "SharedContext"),
    "NpcInteractionCoordinator": ("app.game_core.orchestration.npc_interaction", "NpcInteractionCoordinator"),
    "NpcInteractionResult": ("app.game_core.orchestration.npc_interaction", "NpcInteractionResult"),
    "TickCoordinator": ("app.game_core.orchestration.tick_coordinator", "TickCoordinator"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
