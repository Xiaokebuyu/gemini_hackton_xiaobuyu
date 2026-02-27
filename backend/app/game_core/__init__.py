"""New game-core kernel, isolated from the legacy runtime stack."""

from app.game_core.content import ContentRegistry, WorldInstance
from app.game_core.state import StateChange, StateContainer, StateDelta, StateSlice

__all__ = [
    "ContentRegistry",
    "StateChange",
    "WorldInstance",
    "StateContainer",
    "StateDelta",
    "StateSlice",
]
