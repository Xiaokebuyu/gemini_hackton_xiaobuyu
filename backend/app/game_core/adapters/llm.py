"""LLM adapter port — abstract interface for language model integration.

game_core 不得 import 任何具体 LLM SDK（如 google.genai）。
具体实现放在 app/ 层，通过本 Protocol 注入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class LlmResponse:
    """Abstract LLM response — SDK-agnostic."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # [{"name": "tool_name", "args": {"key": "val"}}]
    finish_reason: str = "stop"  # "stop" | "tool_calls" | "error"
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LlmPort(Protocol):
    """Protocol for LLM generation — injectable into AgenticExecutor."""

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse: ...


class NullLlmProvider:
    """Safe default — returns empty response, no external calls."""

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        return LlmResponse()
