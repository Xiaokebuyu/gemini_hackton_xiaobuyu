"""Rules-layer data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game_core.state import StateDelta


@dataclass(slots=True)
class Command:
    """One normalized game command."""

    type: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    context: dict[str, Any] | None = None


@dataclass(slots=True)
class ValidationResult:
    """Pure validation result."""

    ok: bool
    reason: str = ""


@dataclass(slots=True)
class DiceRoll:
    """Dice roll trace."""

    purpose: str
    dice: str
    result: int
    modifiers: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    critical: bool | None = None


@dataclass(slots=True)
class ExecuteResult:
    """Structured command execution result."""

    success: bool
    delta: StateDelta | None = None
    narrative_hints: list[str] = field(default_factory=list)
    rolls: list[DiceRoll] = field(default_factory=list)
    time_cost: float = 0.0
    errors: list[str] = field(default_factory=list)
    is_dry_run: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def error(cls, reason: str) -> "ExecuteResult":
        return cls(success=False, errors=[reason])

    @classmethod
    def not_implemented(cls, name: str) -> "ExecuteResult":
        return cls(
            success=False,
            errors=[f"{name} not implemented"],
            metadata={"status": "stub"},
        )
