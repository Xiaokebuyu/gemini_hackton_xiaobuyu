"""Tests for P18 Phase 2 — NarrativePlanner LLM 化.

Covers:
- no_planner_returns_noop: planner=None → HookResult noop with reason="no_planner"
- bootstrap quest-agent flow (seed, skip existing, bulletin)
- story_facts from plan saved to NarrativePlanSlice
- invalid story_facts filtered before saving
- _format_planner_context includes story_facts section
- _format_planner_context includes danger_level
- AgenticNarrativePlanner noop on parse failure
- system prompt does not contain spawn_quest_npc
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.game_core.content import WorldInstance
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.planning.pacing_controller import PacingControllerSubSystem
from app.game_core.planning.quest_manager import QuestManagerSubSystem
from app.game_core.planning.subsystem import PlannerDispatcher
from app.game_core.planning.world_builder import WorldBuilderSubSystem
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import (
    AreaSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)
from app.narrators import AgenticNarrativePlanner, _format_planner_context


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_context(
    *,
    narrative_plan_payload: dict[str, object] | None = None,
    quest_payload: dict[str, object] | None = None,
    area_payload: dict[str, object] | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
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
                            "tags": ["quest_source"],
                        }
                    ],
                }
            },
        }
    })
    world.register(maps)
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": None})
    state.register(player)

    quests = QuestSlice()
    base_quest_payload: dict[str, object] = {
        "milestone_states": {"ms_1": {"state": "AVAILABLE"}},
        "dynamic_quests": {},
    }
    if quest_payload is not None:
        base_quest_payload.update(quest_payload)
    quests.restore(base_quest_payload)
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    base_np_payload: dict[str, object] = {"last_run_tick": 9}
    if narrative_plan_payload is not None:
        base_np_payload.update(narrative_plan_payload)
    narrative_plan.restore(base_np_payload)
    state.register(narrative_plan)

    area_slice = AreaSlice()
    base_area: dict[str, object] = {"areas": {"forest": {}}}
    if area_payload is not None:
        base_area.update(area_payload)
    area_slice.restore(base_area)
    state.register(area_slice)

    scene = SceneSlice()
    state.register(scene)

    rules_engine = RulesEngine()

    def _apply(delta: Any) -> None:
        state.apply(delta)

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(scene),
        _rules_engine=rules_engine,
        _apply_delta=_apply,
    )


def _make_llm_response(text: str) -> Any:
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = []
    return resp


def _make_llm(text: str) -> Any:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=_make_llm_response(text))
    return llm


def _make_full_hook_p18(planner: Any = None) -> NarrativePlannerHook:
    """Build a NarrativePlannerHook with dispatcher wiring and explicit blackboard/agent ports."""
    hook = NarrativePlannerHook(blackboard=planner)
    dispatcher = PlannerDispatcher()
    quest_manager = QuestManagerSubSystem(dispatcher=dispatcher)
    dispatcher.register(quest_manager)
    dispatcher.register(NpcDirectorSubSystem())
    dispatcher.register(WorldBuilderSubSystem(sse_collector=hook._pending_sse))
    dispatcher.register(PacingControllerSubSystem())
    hook._dispatcher = dispatcher
    return hook


class _StaticBlackboard:
    async def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        del context
        return {
            "directives": [],
            "story_facts": [],
            "strategy_notes": "",
            "metadata": {"status": "noop", "reason": "stable", "provider": "test_blackboard"},
        }


class _BootstrapQuestAgent:
    def __init__(self, *, seeded: bool) -> None:
        self._seeded = seeded

    async def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        del context
        if not self._seeded:
            return {"directives": [], "story_facts": [], "metadata": {}}
        return {
            "directives": [
                {
                    "kind": "create_quest",
                    "payload": {
                        "quest_id": "dq_ms_1",
                        "title": "Lead: Ms 1",
                        "summary": "Follow the new lead tied to ms_1.",
                        "metadata": {"source_milestone": "ms_1", "urgency": "medium"},
                    },
                },
                {
                    "kind": "publish_bulletin",
                    "payload": {
                        "board_id": "board",
                        "title": "New Lead Posted",
                        "content": "A fresh lead: Ms 1.",
                        "tags": ["quest", "planner"],
                        "urgency": "medium",
                        "posted_by": "narrative_planner",
                        "metadata": {"quest_id": "dq_ms_1", "source_milestone": "ms_1"},
                    },
                },
            ],
            "story_facts": [],
            "metadata": {"status": "quest_seeded", "provider": "bootstrap_agent"},
        }


def _make_bootstrap_hook_p18(*, seeded: bool) -> NarrativePlannerHook:
    hook = NarrativePlannerHook(blackboard=_StaticBlackboard())
    dispatcher = PlannerDispatcher()
    quest_manager = QuestManagerSubSystem(
        dispatcher=dispatcher,
        agent=_BootstrapQuestAgent(seeded=seeded),
    )
    dispatcher.register(quest_manager)
    dispatcher.register(NpcDirectorSubSystem())
    dispatcher.register(WorldBuilderSubSystem(sse_collector=hook._pending_sse))
    dispatcher.register(PacingControllerSubSystem())
    hook._dispatcher = dispatcher
    return hook


# ------------------------------------------------------------------
# 2.3c: no-LLM 降级
# ------------------------------------------------------------------


def test_no_planner_returns_noop() -> None:
    """NarrativePlannerHook with no blackboard/dispatcher returns noop with reason=no_planner."""
    context = _make_context(narrative_plan_payload={"last_run_tick": 3})

    result = asyncio.run(NarrativePlannerHook(blackboard=None).execute(context))

    assert result.metadata["status"] == "noop"
    assert result.metadata["reason"] == "no_planner"
    assert result.metadata["applied_count"] == 0
    assert result.sse_events == []


# ------------------------------------------------------------------
# 2.1d: bootstrap quest-agent 路由
# ------------------------------------------------------------------


def test_bootstrap_seeds_first_milestone() -> None:
    """Bootstrap quest agent seeds the first available milestone as a dynamic quest."""
    context = _make_context(
        narrative_plan_payload={"last_run_tick": 9},
        quest_payload={"dynamic_quests": {}},
    )

    result = asyncio.run(_make_bootstrap_hook_p18(seeded=True).bootstrap(context))

    assert result.metadata["status"] == "updated"
    assert result.metadata["reason"] == "bootstrap"
    assert "create_quest" in result.metadata["applied_kinds"]
    dq = context.state.quests.get_dynamic_quest("dq_ms_1")
    assert dq is not None
    assert dq["status"] == "available"
    assert dq["metadata"]["source_milestone"] == "ms_1"


def test_bootstrap_skips_existing_quest() -> None:
    """Bootstrap quest agent can noop when the milestone quest already exists."""
    context = _make_context(
        narrative_plan_payload={"last_run_tick": 9},
        quest_payload={
            "dynamic_quests": {
                "dq_ms_1": {
                    "status": "available",
                    "title": "Already seeded",
                    "summary": "Exists",
                }
            }
        },
    )

    result = asyncio.run(_make_bootstrap_hook_p18(seeded=False).bootstrap(context))

    assert result.metadata["status"] == "noop"
    assert result.metadata["applied_count"] == 0


def test_bootstrap_appends_bulletin_when_board_exists() -> None:
    """Bootstrap quest agent can emit publish_bulletin when a quest_source board exists."""
    context = _make_context(
        narrative_plan_payload={"last_run_tick": 9},
        quest_payload={"dynamic_quests": {}},
    )

    result = asyncio.run(_make_bootstrap_hook_p18(seeded=True).bootstrap(context))

    # Both create_quest and publish_bulletin should be applied
    assert "create_quest" in result.metadata["applied_kinds"]
    assert "publish_bulletin" in result.metadata["applied_kinds"]
    assert result.metadata["applied_count"] == 3
    assert result.metadata["replay_round_count"] == 2


# ------------------------------------------------------------------
# 2.3b: story_facts 写入持久化
# ------------------------------------------------------------------


def test_story_facts_from_plan_saved_to_slice() -> None:
    """Planner returning story_facts → facts are saved to NarrativePlanSlice.story_facts."""
    story_facts = [
        {"subject": "guild", "relation": "warns_about", "object": "raiders"},
        {"subject": "mayor", "relation": "knows", "object": "secret_path"},
    ]

    class StoryFactPlanner:
        async def plan(self, context: dict[str, Any]) -> dict[str, Any]:
            return {
                "directives": [],
                "story_facts": story_facts,
                "strategy_notes": "",
                "metadata": {"status": "noop", "provider": "test"},
            }

    context = _make_context(narrative_plan_payload={"last_run_tick": 3})
    # Add a trigger change so the planner runs
    from app.game_core.state import StateChange
    context.change_log.append(StateChange("flags", "set", "flags.trigger", True))

    asyncio.run(NarrativePlannerHook(blackboard=StoryFactPlanner()).execute(context))

    saved = context.state.narrative_plan.story_facts
    assert len(saved) == 2
    assert saved[0]["subject"] == "guild"
    assert saved[1]["subject"] == "mayor"


def test_invalid_story_facts_filtered() -> None:
    """Facts missing subject/relation/object are silently filtered."""
    invalid_facts = [
        {"subject": "guild"},                        # missing relation + object
        {"relation": "knows", "object": "secret"},   # missing subject
        {"subject": "a", "relation": "b", "object": "c"},  # valid
    ]

    class BadFactPlanner:
        async def plan(self, context: dict[str, Any]) -> dict[str, Any]:
            return {
                "directives": [],
                "story_facts": invalid_facts,
                "strategy_notes": "",
                "metadata": {"status": "noop", "provider": "test"},
            }

    context = _make_context(narrative_plan_payload={"last_run_tick": 3})
    from app.game_core.state import StateChange
    context.change_log.append(StateChange("flags", "set", "flags.trigger", True))

    asyncio.run(NarrativePlannerHook(blackboard=BadFactPlanner()).execute(context))

    saved = context.state.narrative_plan.story_facts
    # Only the valid fact should be saved
    assert len(saved) == 1
    assert saved[0] == {"subject": "a", "relation": "b", "object": "c"}


# ------------------------------------------------------------------
# 2.2d: _format_planner_context 包含 story_facts + danger_level
# ------------------------------------------------------------------


def test_format_planner_context_includes_story_facts() -> None:
    """story_facts in context appear in the formatted output under 世界中已确立的事实."""
    ctx: dict[str, Any] = {
        "narrative_plan": {
            "current_chapter": "ch1",
            "escalation_level": 0,
            "ticks_since_milestone_progress": 0,
            "current_target_milestone": None,
            "pacing_frozen": False,
        },
        "quests": {"available_milestones": [], "dynamic_quests": {}},
        "current_tick": 5,
        "story_facts": [
            {"subject": "guild", "relation": "warns_about", "object": "raiders"},
            {"subject": "elder", "relation": "hides", "object": "relic"},
        ],
    }

    result = _format_planner_context(ctx)

    assert "世界中已确立的事实" in result
    assert "guild --[warns_about]--> raiders" in result
    assert "elder --[hides]--> relic" in result


def test_format_planner_context_includes_danger_level() -> None:
    """danger_level > 0.0 appears in the formatted output."""
    ctx: dict[str, Any] = {
        "narrative_plan": {
            "current_chapter": "ch1",
            "escalation_level": 0,
            "ticks_since_milestone_progress": 0,
            "current_target_milestone": None,
            "pacing_frozen": False,
        },
        "quests": {"available_milestones": [], "dynamic_quests": {}},
        "current_tick": 5,
        "danger_level": 2.5,
    }

    result = _format_planner_context(ctx)

    assert "Danger level: 2.5" in result


def test_format_planner_context_omits_danger_level_when_zero() -> None:
    """danger_level == 0.0 is omitted from the formatted output."""
    ctx: dict[str, Any] = {
        "narrative_plan": {
            "current_chapter": "ch1",
            "escalation_level": 0,
            "ticks_since_milestone_progress": 0,
            "current_target_milestone": None,
            "pacing_frozen": False,
        },
        "quests": {"available_milestones": [], "dynamic_quests": {}},
        "current_tick": 5,
        "danger_level": 0.0,
    }

    result = _format_planner_context(ctx)

    assert "Danger level" not in result


def test_build_planner_context_includes_story_facts() -> None:
    """_build_planner_context injects story_facts from NarrativePlanSlice."""
    context = _make_context()
    context.state.narrative_plan.add_story_facts([
        {"subject": "hero", "relation": "saved", "object": "village"},
    ])

    result = NarrativePlannerHook()._build_planner_context(context, current_tick=1)

    assert result["story_facts"] == [{"subject": "hero", "relation": "saved", "object": "village"}]


def test_build_planner_context_includes_danger_level() -> None:
    """_build_planner_context injects danger_level from current area."""
    context = _make_context(area_payload={"areas": {"forest": {"danger_level": 3.0}}})
    # danger_level defaults to 1.0 if none specified, but explicit 3.0 overrides

    result = NarrativePlannerHook()._build_planner_context(context, current_tick=1)

    assert result["danger_level"] == 3.0


# ------------------------------------------------------------------
# 2.2c: AgenticNarrativePlanner noop on failure
# ------------------------------------------------------------------


def test_agentic_planner_noop_on_failure() -> None:
    """LLM failure → AgenticNarrativePlanner returns empty directives without crashing."""
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan({"narrative_plan": {}, "quests": {}, "current_tick": 1}))

    assert isinstance(result, dict)
    assert result["directives"] == []
    assert result.get("metadata", {}).get("reason") == "parse_failed"


def test_story_facts_in_output_preserved() -> None:
    """LLM returns story_facts in JSON → they appear in the result dict."""
    plan_json = json.dumps({
        "directives": [],
        "story_facts": [{"subject": "x", "relation": "r", "object": "y"}],
        "strategy_notes": "test",
    })
    llm = _make_llm(plan_json)
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan({"narrative_plan": {}, "quests": {}, "current_tick": 1}))

    assert result.get("story_facts") == [{"subject": "x", "relation": "r", "object": "y"}]


def test_markdown_code_block_stripped() -> None:
    """LLM wrapping response in markdown code fences is handled correctly."""
    plan_dict = {
        "directives": [{"kind": "escalate", "payload": {"delta": 1}}],
        "story_facts": [],
    }
    wrapped = "```json\n" + json.dumps(plan_dict) + "\n```"
    llm = _make_llm(wrapped)
    planner = AgenticNarrativePlanner(llm=llm)

    result = asyncio.run(planner.plan({"narrative_plan": {}, "quests": {}, "current_tick": 1}))

    assert "directives" in result
    assert result["directives"][0]["kind"] == "escalate"


# ------------------------------------------------------------------
# 2.2b: system prompt contents
# ------------------------------------------------------------------


def test_system_prompt_no_spawn_quest_npc() -> None:
    """New system prompt must not reference spawn_quest_npc (it was removed)."""
    prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
    assert "spawn_quest_npc" not in prompt
