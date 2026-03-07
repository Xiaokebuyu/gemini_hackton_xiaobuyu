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
from app.game_core.planning import NarrativePlanner
from app.game_core.state import StateContainer
from app.game_core.content.registries.maps import MapRegistry


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
            success=True,
            message=f"ran:{self._marker}",
            metadata={"status": "ok", "marker": self._marker},
        )


def _context() -> AgentContext:
    return AgentContext(
        role="gm",
        world=WorldInstance("test_world"),
        state=StateContainer(),
    )


def _map_context_with_board(area_id: str = "forest", board_id: str = "board") -> dict[str, Any]:
    maps = MapRegistry()
    maps.load({
        area_id: {
            "id": area_id,
            "sub_locations": {
                "quest_hub": {
                    "id": "quest_hub",
                    "name": "Quest Hub",
                    "interactables": [
                        {
                            "id": board_id,
                            "name": "Quest Board",
                            "type": "inspect",
                            "tags": ["quest_source"],
                        }
                    ],
                }
            },
        }
    })
    return {"maps": maps, "location": {"area_id": area_id, "location_id": None}}


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
    assert results[0].success is False
    assert results[0].metadata == {"status": "invalid_params", "tool_name": ""}
    assert results[1].success is False
    assert results[1].metadata == {"status": "unknown_tool", "tool_name": ""}
    assert results[2].success is False
    assert results[2].metadata == {"status": "invalid_params", "tool_name": "alpha"}
    assert results[3].success is True
    assert results[3].metadata["marker"] == "alpha"
    assert alpha.calls == [{"params": {"x": 1}, "role": "gm"}]


def test_default_narrative_planner_seeds_first_available_milestone() -> None:
    result = asyncio.run(NarrativePlanner().plan(
        {
            **_map_context_with_board(),
            "current_tick": 9,
            "quests": {
                "available_milestones": ["report_in"],
                "dynamic_quests": {},
            },
            "narrative_plan": {
                "escalation_level": 0,
                "ticks_since_milestone_progress": 0,
                "pacing_frozen": False,
            },
        }
    ))

    assert result["metadata"]["status"] == "quest_seeded"
    assert result["metadata"]["provider"] == "default_planner"
    assert result["next_scheduled_tick"] == 15
    directives = result["directives"]
    assert len(directives) == 2
    assert directives[0]["kind"] == "create_quest"
    assert directives[0]["payload"]["quest_id"] == "dq_report_in"
    assert directives[1]["kind"] == "publish_bulletin"
    assert directives[1]["payload"]["board_id"] == "board"


def test_default_narrative_planner_uses_resolved_board_id_from_map() -> None:
    result = asyncio.run(NarrativePlanner().plan(
        {
            **_map_context_with_board(area_id="frontier", board_id="board_2"),
            "current_tick": 9,
            "quests": {
                "available_milestones": ["report_in"],
                "dynamic_quests": {},
            },
            "narrative_plan": {
                "escalation_level": 0,
                "ticks_since_milestone_progress": 0,
                "pacing_frozen": False,
            },
        }
    ))

    assert result["metadata"]["status"] == "quest_seeded"
    assert result["directives"][1]["kind"] == "publish_bulletin"
    assert result["directives"][1]["payload"]["board_id"] == "board_2"


def test_default_narrative_planner_seed_payload_includes_extended_fields() -> None:
    result = asyncio.run(NarrativePlanner().plan(
        {
            **_map_context_with_board(),
            "current_tick": 9,
            "quests": {
                "available_milestones": ["report_in"],
                "dynamic_quests": {},
            },
            "narrative_plan": {
                "escalation_level": 0,
                "ticks_since_milestone_progress": 0,
                "pacing_frozen": False,
            },
        }
    ))

    assert result["metadata"]["status"] == "quest_seeded"
    payload = result["directives"][0]["payload"]
    assert payload["metadata"]["source_milestone"] == "report_in"
    assert payload["metadata"]["urgency"] == "medium"
    assert payload["objectives"] == []
    assert payload["rewards"] == {}
    assert payload["delivery_method"] == "board"
    assert payload["expiry_ticks"] is None
    assert payload["on_expire"] == "ignore"
    assert payload["generated_by_escalation"] == 0
    assert payload["planner_reasoning"] == "Seeded from report_in availability."


def test_default_narrative_planner_l3_payload_includes_extended_fields() -> None:
    result = asyncio.run(NarrativePlanner().plan(
        {
            "current_tick": 9,
            "quests": {
                "available_milestones": [],
                "active_milestones": ["ms_a"],
                "dynamic_quests": {},
            },
            "narrative_plan": {
                "escalation_level": 0,
                "ticks_since_milestone_progress": 12,
                "pacing_frozen": False,
            },
        }
    ))

    assert result["metadata"]["status"] == "escalation_l3"
    payload = result["directives"][0]["payload"]
    assert payload["metadata"]["source_milestone"] == "ms_a"
    assert payload["metadata"]["urgency"] == "high"
    assert payload["metadata"]["generated_by_escalation"] == 3
    assert payload["objectives"] == []
    assert payload["rewards"] == {}
    assert payload["delivery_method"] == "board"
    assert payload["expiry_ticks"] == 12
    assert payload["on_expire"] == "escalate"
    assert payload["generated_by_escalation"] == 3
    assert payload["planner_reasoning"] == "L3 urgent escalation for ms_a."

def test_default_narrative_planner_skips_seed_when_board_unresolved() -> None:
    maps = MapRegistry()
    maps.load({
        "forest": {
            "id": "forest",
            "sub_locations": {
                "quest_hub": {
                    "id": "quest_hub",
                    "name": "Quest Hub",
                    "interactables": [
                        {
                            "id": "board",
                            "name": "Quest Board",
                            "type": "inspect",
                            "tags": ["utility"],
                        }
                    ],
                }
            },
        }
    })
    result = asyncio.run(NarrativePlanner().plan(
        {
            "maps": maps,
            "location": {"area_id": "forest", "location_id": None},
            "current_tick": 9,
            "quests": {
                "available_milestones": ["report_in"],
                "dynamic_quests": {},
            },
            "narrative_plan": {
                "escalation_level": 0,
                "ticks_since_milestone_progress": 0,
                "pacing_frozen": False,
            },
        }
    ))

    assert result["metadata"]["status"] == "quest_seeded"
    assert len(result["directives"]) == 1
    assert result["directives"][0]["kind"] == "create_quest"


def test_default_narrative_planner_skips_existing_seeded_quest_and_returns_noop() -> None:
    result = asyncio.run(NarrativePlanner().plan(
        {
            "current_tick": 9,
            "quests": {
                "available_milestones": ["report_in"],
                "dynamic_quests": {"dq_report_in": {"status": "available"}},
            },
            "narrative_plan": {
                "escalation_level": 0,
                "ticks_since_milestone_progress": 0,
                "pacing_frozen": False,
            },
        }
    ))

    assert result["directives"] == []
    assert result["metadata"] == {
        "status": "noop",
        "provider": "default_planner",
        "reason": "stable",
    }


def test_default_narrative_planner_generates_escalation_l1() -> None:
    result = asyncio.run(NarrativePlanner().plan(
        {
            **_map_context_with_board(),
            "current_tick": 9,
            "quests": {
                "available_milestones": [],
                "active_milestones": ["ms_a"],
                "dynamic_quests": {
                    "dq_ms_a": {"status": "active"},
                },
            },
            "narrative_plan": {
                "escalation_level": 0,
                "ticks_since_milestone_progress": 6,
                "pacing_frozen": False,
            },
        }
    ))

    assert result["metadata"]["status"] == "escalation_l1"
    assert result["next_scheduled_tick"] == 12
    assert len(result["directives"]) == 2
    assert result["directives"][0]["kind"] == "publish_bulletin"
    assert result["directives"][1]["kind"] == "escalate"


def test_default_narrative_planner_thaws_when_progress_resumes() -> None:
    result = asyncio.run(NarrativePlanner().plan(
        {
            "current_tick": 9,
            "quests": {
                "available_milestones": [],
                "dynamic_quests": {},
            },
            "narrative_plan": {
                "escalation_level": 1,
                "ticks_since_milestone_progress": 1,
                "pacing_frozen": True,
            },
        }
    ))

    assert result["metadata"]["status"] == "thaw"
    assert result["directives"] == [
        {"kind": "adjust_pacing", "payload": {"frozen": False}}
    ]
