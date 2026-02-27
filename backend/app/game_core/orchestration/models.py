"""Orchestration-layer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game_core.rules.models import Command


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

    success: bool
    response_text: str = ""
    commands: list[Command] = field(default_factory=list)
    time_cost: float = 0.0
    action_type: str = "noop"
    narrative_hints: list[str] = field(default_factory=list)
    sse_events: list[SSEEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def noop(cls) -> "PipelineResult":
        return cls(success=True, metadata={"status": "stub"})
