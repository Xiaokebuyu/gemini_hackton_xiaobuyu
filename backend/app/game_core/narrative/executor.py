"""Unified agent executor — single-pass + multi-turn agentic loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.narrative.tools import AgentTool

if TYPE_CHECKING:
    from app.game_core.adapters.llm import LlmPort, LlmResponse


class AgenticExecutor:
    """Thin orchestration wrapper around RoleToolRegistry.

    Supports two modes:
    - ``run()``: single-pass tool execution (pre-built tool_calls list)
    - ``run_agentic()``: multi-turn LLM-driven loop (requires LlmPort)
    """

    def __init__(
        self,
        tool_registry: RoleToolRegistry | None = None,
        llm: LlmPort | None = None,
    ) -> None:
        self.tool_registry = tool_registry or RoleToolRegistry()
        self._llm = llm

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

    # ------------------------------------------------------------------
    # Multi-turn agentic loop
    # ------------------------------------------------------------------

    async def run_agentic(
        self,
        role: str,
        context: AgentContext,
        *,
        system_prompt: str = "",
        user_message: str = "",
        max_turns: int = 5,
    ) -> AgentResult:
        """Multi-turn agentic loop driven by an LLM.

        Each turn: LLM generates → parse tool_calls → execute tools → feed
        results back as history → repeat until LLM returns text-only or
        *max_turns* is reached.
        """
        if self._llm is None:
            return AgentResult(metadata={"status": "no_llm"})

        tools = self.tool_registry.get_tools_for(role)
        declarations = self._build_declarations(tools)
        history = self._build_initial_history(context, user_message)
        all_results: list[ToolResult] = []

        for turn in range(max_turns):
            response = await self._llm.generate(system_prompt, history, declarations)

            if not response.tool_calls:
                return AgentResult(
                    text=response.text,
                    tool_results=all_results,
                    turns_used=turn + 1,
                    metadata={
                        "status": "completed",
                        "finish_reason": response.finish_reason,
                    },
                )

            # Append model response to history
            history.append(self._model_turn(response))

            # Execute tool calls via existing single-pass run()
            turn_results = await self.run(
                role,
                context,
                tool_calls=[
                    {"name": tc["name"], "params": tc.get("args", {})}
                    for tc in response.tool_calls
                ],
            )
            all_results.extend(turn_results)

            # Append tool results to history
            history.append(
                self._tool_response_turn(response.tool_calls, turn_results)
            )

        return AgentResult(
            text="",
            tool_results=all_results,
            turns_used=max_turns,
            metadata={"status": "max_turns_reached"},
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    @staticmethod
    def _build_declarations(tools: list[AgentTool]) -> list[dict[str, Any]]:
        """Convert AgentTool schemas to function declarations."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

    @staticmethod
    def _build_initial_history(
        context: AgentContext, user_message: str,
    ) -> list[dict[str, Any]]:
        """Build initial conversation history from context."""
        parts: list[dict[str, Any]] = []
        if context.scene_entries:
            parts.append({"text": f"Scene context: {context.scene_entries}"})
        if user_message:
            parts.append({"text": user_message})
        if not parts:
            parts.append({"text": "Proceed with your role."})
        return [{"role": "user", "parts": parts}]

    @staticmethod
    def _model_turn(response: LlmResponse) -> dict[str, Any]:
        """Format model response as history entry."""
        parts: list[dict[str, Any]] = []
        if response.text:
            parts.append({"text": response.text})
        for tc in response.tool_calls:
            parts.append({
                "function_call": {
                    "name": tc["name"],
                    "args": tc.get("args", {}),
                },
            })
        return {"role": "model", "parts": parts}

    @staticmethod
    def _tool_response_turn(
        tool_calls: list[dict[str, Any]],
        results: list[ToolResult],
    ) -> dict[str, Any]:
        """Format tool execution results as history entry."""
        parts: list[dict[str, Any]] = []
        for tc, result in zip(tool_calls, results):
            parts.append({
                "function_response": {
                    "name": tc["name"],
                    "response": {
                        "success": result.success,
                        "message": result.message,
                        **result.metadata,
                    },
                },
            })
        return {"role": "user", "parts": parts}

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
