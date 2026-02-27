"""State delta contract for the new state container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StateChange:
    """One targeted state mutation produced by the rules layer."""

    slice: str
    operation: str
    path: str
    value: Any


@dataclass(slots=True)
class StateDelta:
    """State mutation set produced by the rules layer.

    The public shape follows the design docs: a linear list of state changes
    that the container dispatches to the target slice.
    """

    changes: list[StateChange] = field(default_factory=list)
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
