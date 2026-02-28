"""New game-core kernel, isolated from the legacy runtime stack."""

from app.game_core.bootstrap import (
    DEFAULT_CONTENT_REGISTRY_TYPES,
    DEFAULT_STATE_SLICE_TYPES,
    DefaultRuntime,
    build_default_state,
    build_default_runtime,
    build_default_world,
    build_restored_runtime,
)
from app.game_core.content import ContentRegistry, WorldInstance
from app.game_core.runtime import (
    CharacterCreationOptions,
    CharacterCreationResult,
    CharacterCreationSpec,
    GameRuntime,
    ManagedSession,
    SavedSessionInfo,
    SessionSummary,
)
from app.game_core.state import StateChange, StateContainer, StateDelta, StateSlice

__all__ = [
    "CharacterCreationOptions",
    "CharacterCreationResult",
    "CharacterCreationSpec",
    "ContentRegistry",
    "DEFAULT_CONTENT_REGISTRY_TYPES",
    "DEFAULT_STATE_SLICE_TYPES",
    "DefaultRuntime",
    "GameRuntime",
    "ManagedSession",
    "SavedSessionInfo",
    "SessionSummary",
    "StateChange",
    "WorldInstance",
    "build_default_state",
    "build_default_runtime",
    "build_restored_runtime",
    "build_default_world",
    "StateContainer",
    "StateDelta",
    "StateSlice",
]
