"""Narrative-layer data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game_core.rules.models import Command


@dataclass(slots=True)
class ToolResult:
    """Result returned by one agent tool invocation."""

    ok: bool
    message: str = ""
    commands: list[Command] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def noop(cls, message: str = "no-op") -> "ToolResult":
        return cls(ok=True, message=message, metadata={"status": "noop"})


@dataclass(slots=True)
class AgentResult:
    """Result of a multi-turn agentic loop."""

    text: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    turns_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    last_model_parts: list[dict[str, Any]] | None = None
    """Structured Gemini parts from the final model response turn.

    Contains function_call and text parts (thought_signature filtered out).
    Stored so that callers can persist this in ContextWindow.parts and
    replay the tool-call structure in subsequent conversation turns.
    Populated by AgenticExecutor.run_agentic() on every successful return.
    """
