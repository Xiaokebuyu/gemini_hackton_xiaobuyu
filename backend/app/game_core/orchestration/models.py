"""Orchestration-layer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game_core.result_semantics import normalize_outcome
from app.game_core.rules.models import Command, DiceRoll
from app.game_core.state import StateDelta


@dataclass(slots=True)
class SSEEvent:
    """Serializable event for streaming presentation layers."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HookResult:
    """Result from one settlement hook execution."""

    commands: list[Command] = field(default_factory=list)
    sse_events: list[SSEEvent] = field(default_factory=list)
    skip_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StructuredAction:
    """Normalized structured action sent by UI-like callers."""

    action_type: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "player"
    context: dict[str, Any] | None = None


@dataclass(slots=True)
class PipelineResult:
    """Output of one pipeline pass."""

    executed: bool
    response_text: str = ""
    commands: list[Command] = field(default_factory=list)
    delta: StateDelta | None = None
    time_cost: float = 0.0
    action_type: str = "noop"
    errors: list[str] = field(default_factory=list)
    narrative_hints: list[str] = field(default_factory=list)
    rolls: list[DiceRoll] = field(default_factory=list)
    sse_events: list[SSEEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, dict):
            self.metadata = {}
        self.metadata.setdefault("executed", self.executed)
        command_type = self.action_type or str(self.metadata.get("command") or "")
        outcome = normalize_outcome(command_type, self.metadata)
        if outcome is not None:
            self.metadata["outcome"] = outcome

    @classmethod
    def noop(cls) -> "PipelineResult":
        return cls(executed=True, metadata={"status": "stub"})
