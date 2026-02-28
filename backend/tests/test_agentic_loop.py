"""Tests for AgenticExecutor multi-turn agentic loop."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.adapters.llm import LlmResponse
from app.game_core.content import WorldInstance
from app.game_core.narrative import (
    AgentResult,
    AgenticExecutor,
    AgentTool,
    RoleToolRegistry,
    ToolResult,
)
from app.game_core.narrative.context import AgentContext
from app.game_core.state import StateContainer


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


class RecordingLlmProvider:
    """Pre-configured LLM that returns scripted responses in order."""

    def __init__(self, responses: list[LlmResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        self.calls.append({
            "system_prompt": system_prompt,
            "history": history,
            "tool_declarations": tool_declarations,
        })
        return self._responses.pop(0) if self._responses else LlmResponse()


class EchoTool(AgentTool):
    """Minimal tool that echoes its params — for testing."""

    name = "echo"
    description = "Echo input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    allowed_roles = ["gm"]

    async def execute(self, params: dict[str, Any], context: AgentContext) -> ToolResult:
        text = params.get("text", "")
        return ToolResult(
            success=True,
            message=text,
            metadata={"event_type": "echo", "echoed": text},
        )


class CounterTool(AgentTool):
    """Tool that increments a counter in context metadata — for testing."""

    name = "increment"
    description = "Increment counter"
    parameters = {"type": "object", "properties": {}}
    allowed_roles = ["gm"]

    async def execute(self, params: dict[str, Any], context: AgentContext) -> ToolResult:
        context.metadata.setdefault("counter", 0)
        context.metadata["counter"] += 1
        return ToolResult(
            success=True,
            message=f"counter={context.metadata['counter']}",
            metadata={"counter": context.metadata["counter"]},
        )


def _registry() -> RoleToolRegistry:
    reg = RoleToolRegistry()
    reg.register(EchoTool())
    reg.register(CounterTool())
    return reg


def _ctx(**overrides: Any) -> AgentContext:
    return AgentContext(
        role="gm",
        world=WorldInstance("test"),
        state=StateContainer(),
        **overrides,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_run_agentic_no_llm_returns_no_llm() -> None:
    """Without an LLM, run_agentic returns immediately with no_llm status."""
    executor = AgenticExecutor(tool_registry=_registry(), llm=None)
    result = asyncio.run(executor.run_agentic("gm", _ctx()))

    assert isinstance(result, AgentResult)
    assert result.metadata["status"] == "no_llm"
    assert result.turns_used == 0
    assert result.tool_results == []


def test_run_agentic_no_tool_calls_returns_text() -> None:
    """LLM returns text-only on first turn — loop exits with text."""
    llm = RecordingLlmProvider([
        LlmResponse(text="All is calm.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    result = asyncio.run(executor.run_agentic("gm", _ctx()))

    assert result.text == "All is calm."
    assert result.turns_used == 1
    assert result.tool_results == []
    assert result.metadata["status"] == "completed"
    assert result.metadata["finish_reason"] == "stop"
    assert len(llm.calls) == 1


def test_run_agentic_single_turn_tool_call() -> None:
    """LLM returns a tool call, then text — 2-turn loop."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "echo", "args": {"text": "hello"}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(text="Done echoing.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    result = asyncio.run(executor.run_agentic("gm", _ctx()))

    assert result.text == "Done echoing."
    assert result.turns_used == 2
    assert len(result.tool_results) == 1
    assert result.tool_results[0].success is True
    assert result.tool_results[0].metadata["echoed"] == "hello"
    assert result.metadata["status"] == "completed"


def test_run_agentic_multi_turn() -> None:
    """LLM makes tool calls across two turns before returning text."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(text="Counter is 2.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    ctx = _ctx()
    result = asyncio.run(executor.run_agentic("gm", ctx))

    assert result.turns_used == 3
    assert len(result.tool_results) == 2
    assert ctx.metadata["counter"] == 2
    assert result.text == "Counter is 2."
    assert result.metadata["status"] == "completed"


def test_run_agentic_max_turns_reached() -> None:
    """Loop stops when max_turns is reached — status reflects this."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    result = asyncio.run(executor.run_agentic("gm", _ctx(), max_turns=2))

    assert result.metadata["status"] == "max_turns_reached"
    assert result.turns_used == 2
    assert len(result.tool_results) == 2
    assert result.text == ""


def test_run_agentic_parallel_tool_calls() -> None:
    """LLM returns multiple tool calls in a single turn — all executed."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[
                {"name": "echo", "args": {"text": "first"}},
                {"name": "echo", "args": {"text": "second"}},
                {"name": "increment", "args": {}},
            ],
            finish_reason="tool_calls",
        ),
        LlmResponse(text="All done.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    result = asyncio.run(executor.run_agentic("gm", _ctx()))

    assert result.turns_used == 2
    assert len(result.tool_results) == 3
    assert result.tool_results[0].metadata["echoed"] == "first"
    assert result.tool_results[1].metadata["echoed"] == "second"
    assert result.tool_results[2].metadata["counter"] == 1


def test_build_declarations_from_tools() -> None:
    """_build_declarations converts AgentTool list to function declarations."""
    tools = [EchoTool(), CounterTool()]
    declarations = AgenticExecutor._build_declarations(tools)

    assert len(declarations) == 2
    assert declarations[0]["name"] == "echo"
    assert declarations[0]["description"] == "Echo input"
    assert declarations[0]["parameters"]["type"] == "object"
    assert declarations[1]["name"] == "increment"


def test_tool_response_fed_back_to_history() -> None:
    """Verify that tool results are correctly fed back into LLM history."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "echo", "args": {"text": "ping"}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(text="pong", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    asyncio.run(executor.run_agentic("gm", _ctx(), system_prompt="Be helpful."))

    # Second LLM call should have the tool result in history
    assert len(llm.calls) == 2
    second_call = llm.calls[1]

    assert second_call["system_prompt"] == "Be helpful."

    # History should have: initial user msg + model tool_call + user tool_response
    history = second_call["history"]
    assert len(history) == 3

    # First entry: initial user message
    assert history[0]["role"] == "user"

    # Second entry: model's function call
    assert history[1]["role"] == "model"
    fc_part = history[1]["parts"][0]
    assert "function_call" in fc_part
    assert fc_part["function_call"]["name"] == "echo"

    # Third entry: tool response
    assert history[2]["role"] == "user"
    fr_part = history[2]["parts"][0]
    assert "function_response" in fr_part
    assert fr_part["function_response"]["name"] == "echo"
    assert fr_part["function_response"]["response"]["success"] is True
    assert fr_part["function_response"]["response"]["echoed"] == "ping"
