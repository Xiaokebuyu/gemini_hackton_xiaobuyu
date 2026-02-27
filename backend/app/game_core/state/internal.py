"""Internal state-layer protocols.

These are intentionally not part of the public API surface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.game_core.state.delta import StateChange


@runtime_checkable
class SupportsStateChange(Protocol):
    """Internal protocol used by StateContainer.apply()."""

    def apply_state_change(self, change: StateChange) -> None:
        """Apply one state change to the implementing slice."""
