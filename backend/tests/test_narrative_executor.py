from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative import (
    AgentContext,
    AgentTool,
    AgenticExecutor,
    RoleToolRegistry,
    ToolResult,
)
from app.game_core.state import StateContainer


class DummyTool(AgentTool):
    def __init__(self, name: str, *, roles: list[str], marker: str) -> None:
        self._name = name
        self._roles = list(roles)
        self._marker = marker
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"tool:{self._name}"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object"}

    @property
    def allowed_roles(self) -> list[str]:
        return list(self._roles)

    async def execute(self, params: dict[str, Any], context: AgentContext) -> ToolResult:
        self.calls.append({"params": dict(params), "role": context.role})
        return ToolResult(
            ok=True,
            message=f"ran:{self._marker}",
            metadata={"status": "ok", "marker": self._marker},
        )


def _context() -> AgentContext:
    return AgentContext(
        role="gm",
        world=WorldInstance("test_world"),
        state=StateContainer(),
    )


def test_role_tool_registry_replaces_same_name_in_place_and_returns_empty_for_unknown_role() -> None:
    registry = RoleToolRegistry()
    alpha_v1 = DummyTool("alpha", roles=["gm"], marker="v1")
    beta = DummyTool("beta", roles=["gm"], marker="beta")
    alpha_v2 = DummyTool("alpha", roles=["gm"], marker="v2")

    registry.register(alpha_v1)
    registry.register(beta)
    registry.register(alpha_v2)

    tools = registry.get_tools_for("gm")

    assert tools == [alpha_v2, beta]
    assert registry.get_tools_for("unknown") == []


def test_agentic_executor_handles_invalid_calls_and_executes_known_tools() -> None:
    registry = RoleToolRegistry()
    alpha = DummyTool("alpha", roles=["gm"], marker="alpha")
    registry.register(alpha)
    executor = AgenticExecutor(tool_registry=registry)

    results = asyncio.run(
        executor.run(
            "gm",
            _context(),
            tool_calls=[
                "bad-call",
                {"name": "", "params": {}},
                {"name": "alpha", "params": []},
                {"name": "alpha", "params": {"x": 1}},
            ],
        )
    )

    assert len(results) == 4
    assert results[0].ok is False
    assert results[0].metadata == {"status": "invalid_params", "tool_name": ""}
    assert results[1].ok is False
    assert results[1].metadata == {"status": "unknown_tool", "tool_name": ""}
    assert results[2].ok is False
    assert results[2].metadata == {"status": "invalid_params", "tool_name": "alpha"}
    assert results[3].ok is True
    assert results[3].metadata["marker"] == "alpha"
    assert alpha.calls == [{"params": {"x": 1}, "role": "gm"}]


