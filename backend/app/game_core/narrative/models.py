"""Narrative-layer data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game_core.rules.models import Command


@dataclass(slots=True)
class ToolResult:
    """Result returned by one agent tool invocation."""

    success: bool
    message: str = ""
    commands: list[Command] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def noop(cls, message: str = "no-op") -> "ToolResult":
        return cls(success=True, message=message, metadata={"status": "noop"})


@dataclass(slots=True)
class AgentResult:
    """Result of a multi-turn agentic loop."""

    text: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    turns_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
