"""Unified agent executor skeleton."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult
from app.game_core.narrative.registry import RoleToolRegistry


class AgenticExecutor:
    """Thin orchestration wrapper around RoleToolRegistry."""

    def __init__(self, tool_registry: RoleToolRegistry | None = None) -> None:
        self.tool_registry = tool_registry or RoleToolRegistry()

    async def run(
        self,
        role: str,
        context: AgentContext,
        tool_calls: list[Any] | None = None,
    ) -> list[ToolResult]:
        if not tool_calls:
            return []

        available = {
            tool.name: tool
            for tool in self.tool_registry.get_tools_for(role)
        }
        results: list[ToolResult] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, Mapping):
                results.append(
                    self._error_result(
                        status="invalid_params",
                        tool_name="",
                        message="invalid tool call",
                    )
                )
                continue
            tool_name = str(tool_call.get("name", ""))
            params = tool_call.get("params", {})
            tool = available.get(tool_name)
            if tool is None:
                results.append(
                    self._error_result(
                        status="unknown_tool",
                        tool_name=tool_name,
                        message=f"unknown tool: {tool_name}",
                    )
                )
                continue
            if not isinstance(params, dict):
                results.append(
                    self._error_result(
                        status="invalid_params",
                        tool_name=tool_name,
                        message=f"invalid params: {tool_name}",
                    )
                )
                continue
            results.append(await tool.execute(params, context))
        return results

    @staticmethod
    def _error_result(
        *,
        status: str,
        tool_name: str,
        message: str,
    ) -> ToolResult:
        return ToolResult(
            success=False,
            message=message,
            metadata={
                "status": status,
                "tool_name": tool_name,
            },
        )
