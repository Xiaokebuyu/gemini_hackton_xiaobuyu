"""State layer for the new game-core kernel."""

from app.game_core.state.base import StateContainer, StateSlice
from app.game_core.state.delta import StateChange, StateDelta

__all__ = [
    "StateContainer",
    "StateChange",
    "StateDelta",
    "StateSlice",
]
