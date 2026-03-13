"""Phase 5a + 5b tests.

5a-1: Quest panel hides milestone_states from the public API and filters
retired/expired quests.
5a-2: completion_hint field appears when all objectives are completed.
5a-3: Skills prompt contains mandatory design template directive.
5b-1: objectives condition schema preserved in create_quest normalization.
5b-2: QuestObjectiveTrackingHook auto-marks objectives with satisfied conditions.
5b-3: Hook advances quest status when all objectives are completed.
5b-4: Objectives without a condition field are not auto-marked.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
from app.game_core.orchestration.hooks.quest_objective_tracking import (
    QuestObjectiveTrackingHook,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers.world_state import WorldStateHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import FlagSlice, PlayerSlice, QuestSlice, SceneSlice
from app.quest_views import normalize_dynamic_quest_view


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def _make_state_with_quests(dynamic_quests: dict[str, Any]) -> StateContainer:
    """Build a minimal StateContainer with a QuestSlice seeded with dynamic_quests."""
    state = StateContainer()
    quests = QuestSlice()
    quests.restore({"dynamic_quests": dynamic_quests})
    state.register(quests)

    player = PlayerSlice()
    player.restore({"current_area": "town"})
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {}})
    state.register(flags)

    return state


def _make_context(state: StateContainer) -> SettlementContext:
    world = WorldInstance("test_world")
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    rules_engine.register(WorldStateHandler())

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


# ------------------------------------------------------------------
# 5a-1: Quest panel hides milestone_states from the public API and filters
# retired/expired quests.
# Note: panels.py imports FastAPI at module level, so we test the logic
# directly via the underlying quest_views helpers and snapshot inspection.
# ------------------------------------------------------------------


class TestQuestPanelFiltering:
    """Test that the panel quest filtering logic is correct."""

    def test_milestone_states_not_in_public_schema(self) -> None:
        """QuestPanelResponse must not expose milestone_states to frontend callers."""
        from app.quest_views import normalize_dynamic_quest_panel

        from app.api_models import QuestPanelResponse

        response = QuestPanelResponse(
            dynamic_quests=normalize_dynamic_quest_panel({
                "dq_active": {"status": "active", "title": "Active Quest"},
            }),
            chapter_completion={},
        )
        assert "milestone_states" not in response.model_dump(), (
            "milestone_states must not be exposed via QuestPanelResponse"
        )
        assert "dq_active" in response.dynamic_quests

    def test_retired_expired_filtered_from_visible_quests(self) -> None:
        """Retired and expired quests must be filtered before normalize."""
        from app.quest_views import normalize_dynamic_quest_panel

        # Simulate the filtering logic from _quest_response
        raw_dynamic = {
            "dq_active": {"status": "active", "title": "Active"},
            "dq_retired": {"status": "retired", "title": "Retired"},
            "dq_expired": {"status": "expired", "title": "Expired"},
            "dq_completed": {"status": "completed", "title": "Completed"},
        }

        # Apply the filter from _quest_response
        visible_quests = {
            qid: quest
            for qid, quest in raw_dynamic.items()
            if str(quest.get("status", "")).strip().lower() not in {"retired", "expired"}
        }

        result = normalize_dynamic_quest_panel(visible_quests)
        assert "dq_active" in result
        assert "dq_completed" in result
        assert "dq_retired" not in result, "retired quests must be hidden"
        assert "dq_expired" not in result, "expired quests must be hidden"


# ------------------------------------------------------------------
# 5a-2: completion_hint field
# ------------------------------------------------------------------


class TestCompletionHint:
    def test_completion_hint_appears_when_all_objectives_completed(self) -> None:
        quest = normalize_dynamic_quest_view(
            "dq_done",
            {
                "status": "active",
                "title": "All Done",
                "summary": "Complete everything.",
                "objectives": [
                    {"description": "Kill the goblin", "completed": True},
                    {"description": "Return to guild", "completed": True},
                ],
            },
        )
        assert "completion_hint" in quest
        assert quest["completion_hint"] == "所有目标已完成"

    def test_completion_hint_with_requires_report(self) -> None:
        quest = normalize_dynamic_quest_view(
            "dq_report",
            {
                "status": "active",
                "title": "Report Quest",
                "summary": "Return and report.",
                "requires_report": True,
                "objectives": [
                    {"description": "Kill goblins", "completed": True},
                ],
            },
        )
        assert "completion_hint" in quest
        assert quest["completion_hint"] == "所有目标已完成，请返回汇报"

    def test_no_completion_hint_when_some_incomplete(self) -> None:
        quest = normalize_dynamic_quest_view(
            "dq_partial",
            {
                "status": "active",
                "title": "Partial",
                "summary": "Not done yet.",
                "objectives": [
                    {"description": "Kill goblin", "completed": True},
                    {"description": "Find artifact", "completed": False},
                ],
            },
        )
        assert "completion_hint" not in quest

    def test_no_completion_hint_for_completed_status(self) -> None:
        """completion_hint only applies to status=active quests."""
        quest = normalize_dynamic_quest_view(
            "dq_already_done",
            {
                "status": "completed",
                "title": "Already Done",
                "summary": "Done.",
                "objectives": [
                    {"description": "Kill goblin", "completed": True},
                ],
            },
        )
        assert "completion_hint" not in quest

    def test_no_completion_hint_when_no_objectives(self) -> None:
        quest = normalize_dynamic_quest_view(
            "dq_no_obj",
            {
                "status": "active",
                "title": "No Objectives",
                "summary": "Open-ended task.",
                "objectives": [],
            },
        )
        assert "completion_hint" not in quest


# ------------------------------------------------------------------
# 5a-3: Skills prompt contains mandatory directive
# ------------------------------------------------------------------


class TestSkillsPromptMandatory:
    def test_system_prompt_contains_mandatory_design_template_rule(self) -> None:
        from app.narrators import AgenticNarrativePlanner

        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        assert "设计模板使用规则" in prompt, "Should have 设计模板使用规则 section"
        assert "强制要求" in prompt, "Should have 强制要求 (mandatory) clause"
        assert "create_quest" in prompt, "Mandatory rule should mention create_quest"
        assert "plant_encounter" in prompt, "Mandatory rule should mention plant_encounter"

    def test_subsystem_prompts_updated_from_optional_to_recommended(self) -> None:
        from app.narrators import (
            QUEST_MANAGER_AGENT_PROMPT,
            NPC_DIRECTOR_AGENT_PROMPT,
            WORLD_BUILDER_AGENT_PROMPT,
            NARRATIVE_WEAVER_AGENT_PROMPT,
            ITEM_DESIGNER_AGENT_PROMPT,
        )
        for name, prompt in [
            ("QUEST_MANAGER", QUEST_MANAGER_AGENT_PROMPT),
            ("NPC_DIRECTOR", NPC_DIRECTOR_AGENT_PROMPT),
            ("WORLD_BUILDER", WORLD_BUILDER_AGENT_PROMPT),
            ("NARRATIVE_WEAVER", NARRATIVE_WEAVER_AGENT_PROMPT),
            ("ITEM_DESIGNER", ITEM_DESIGNER_AGENT_PROMPT),
        ]:
            assert "（可选）" not in prompt, f"{name}: old '（可选）' text should be replaced"
            assert "建议先通过 list_design_skills" in prompt, f"{name}: should have recommended language"


# ------------------------------------------------------------------
# 5b-1: objectives condition schema preserved in create_quest
# ------------------------------------------------------------------


def _make_planner_engine() -> RulesEngine:
    """Build a RulesEngine with PlannerQuestHandler registered."""
    from app.game_core.rules.handlers.planner import PlannerQuestHandler
    engine = RulesEngine()
    engine.register(PlannerQuestHandler())
    return engine


def _make_state_for_planner() -> StateContainer:
    """Build a StateContainer suitable for planner handler tests."""
    from app.game_core.state.slices import NarrativePlanSlice, EventSlice
    state = _make_state_with_quests({})
    narrative = NarrativePlanSlice()
    narrative.restore({})
    state.register(narrative)
    events = EventSlice()
    events.restore({})
    state.register(events)
    return state


def _planner_create_quest_cmd(quest_id: str, **kwargs) -> Any:
    """Build a planner_create_quest Command (source must be narrative_planner)."""
    from app.game_core.rules.models import Command
    params = {"quest_id": quest_id}
    params.update(kwargs)
    return Command(type="planner_create_quest", params=params, source="narrative_planner")


class TestObjectiveConditionSchema:
    def test_condition_field_preserved_in_create_quest(self) -> None:
        state = _make_state_for_planner()
        world = WorldInstance("test_world")
        engine = _make_planner_engine()

        result = engine.execute(
            _planner_create_quest_cmd(
                "dq_cond_test",
                title="Condition Test",
                summary="Kill 3 goblins.",
                objectives=[
                    {
                        "description": "Kill 3 goblins",
                        "condition": {
                            "type": "kill_count",
                            "params": {"monster_type": "goblin", "count": 3},
                        },
                    },
                    {
                        "description": "Return to guild",
                    },
                ],
            ),
            state,
            world,
        )

        assert result.executed is True
        state.apply(result.delta)
        quest = state.quests.get_dynamic_quest("dq_cond_test")
        assert quest is not None
        objs = quest["objectives"]
        assert len(objs) == 2
        # First objective has condition
        assert objs[0]["description"] == "Kill 3 goblins"
        assert objs[0]["completed"] is False
        assert "condition" in objs[0]
        assert objs[0]["condition"]["type"] == "kill_count"
        # Second objective has no condition
        assert objs[1]["description"] == "Return to guild"
        assert "condition" not in objs[1]

    def test_invalid_condition_stripped(self) -> None:
        """Condition dicts without 'type' are stripped."""
        state = _make_state_for_planner()
        world = WorldInstance("test_world")
        engine = _make_planner_engine()

        result = engine.execute(
            _planner_create_quest_cmd(
                "dq_bad_cond",
                title="Bad Condition",
                summary="Invalid condition dict.",
                objectives=[
                    {
                        "description": "Do something",
                        "condition": {"no_type_key": "value"},
                    },
                ],
            ),
            state,
            world,
        )

        assert result.executed is True
        state.apply(result.delta)
        quest = state.quests.get_dynamic_quest("dq_bad_cond")
        assert quest is not None
        assert "condition" not in quest["objectives"][0]

    def test_string_objective_normalized_to_dict(self) -> None:
        """String objectives are converted to {description, completed} dicts."""
        state = _make_state_for_planner()
        world = WorldInstance("test_world")
        engine = _make_planner_engine()

        result = engine.execute(
            _planner_create_quest_cmd(
                "dq_str_obj",
                title="String Objectives",
                summary="Old format.",
                objectives=["Find the cave", "Defeat the boss"],
            ),
            state,
            world,
        )

        assert result.executed is True
        state.apply(result.delta)
        quest = state.quests.get_dynamic_quest("dq_str_obj")
        assert quest is not None
        objs = quest["objectives"]
        assert len(objs) == 2
        assert objs[0] == {"description": "Find the cave", "completed": False}
        assert objs[1] == {"description": "Defeat the boss", "completed": False}


# ------------------------------------------------------------------
# 5b-2/3/4: QuestObjectiveTrackingHook
# ------------------------------------------------------------------


class TestQuestObjectiveTrackingHook:
    def test_objective_with_satisfied_condition_is_marked_complete(self) -> None:
        """A kill_count condition that is satisfied marks the objective completed."""
        state = _make_state_with_quests({
            "dq_kill": {
                "status": "active",
                "title": "Kill Quest",
                "objectives": [
                    {
                        "description": "Kill 3 goblins",
                        "completed": False,
                        "condition": {
                            "type": "kill_count",
                            "params": {"monster_type": "goblin", "count": 3},
                        },
                    },
                ],
            },
        })
        # Seed kill count flag
        state.flags.restore({"flags": {"kill_count_goblin": 5}})
        context = _make_context(state)

        def _run():
            return QuestObjectiveTrackingHook().execute(context)

        result = asyncio.run(_run())

        assert result.metadata["status"] == "applied"
        assert result.metadata["updated_quest_count"] == 1
        assert result.metadata["completed_objective_count"] == 1
        # Check in-place mutation
        quest = context.state.quests.dynamic_quests["dq_kill"]
        assert quest["objectives"][0]["completed"] is True
        # SSE event emitted
        sse_types = [e.event_type for e in result.sse_events]
        assert "quest_objective_updated" in sse_types

    def test_unsatisfied_condition_leaves_objective_incomplete(self) -> None:
        """An objective with unmet condition is not marked complete."""
        state = _make_state_with_quests({
            "dq_not_yet": {
                "status": "active",
                "title": "Not Done",
                "objectives": [
                    {
                        "description": "Kill 10 goblins",
                        "completed": False,
                        "condition": {
                            "type": "kill_count",
                            "params": {"monster_type": "goblin", "count": 10},
                        },
                    },
                ],
            },
        })
        state.flags.restore({"flags": {"kill_count_goblin": 2}})
        context = _make_context(state)

        def _run():
            return QuestObjectiveTrackingHook().execute(context)

        result = asyncio.run(_run())

        assert result.metadata["status"] == "noop"
        quest = context.state.quests.dynamic_quests["dq_not_yet"]
        assert quest["objectives"][0]["completed"] is False

    def test_objective_without_condition_not_auto_marked(self) -> None:
        """Objectives without a condition field are not touched."""
        state = _make_state_with_quests({
            "dq_manual": {
                "status": "active",
                "title": "Manual Quest",
                "objectives": [
                    {"description": "Talk to innkeeper", "completed": False},
                ],
            },
        })
        context = _make_context(state)

        def _run():
            return QuestObjectiveTrackingHook().execute(context)

        result = asyncio.run(_run())

        assert result.metadata["status"] == "noop"
        quest = context.state.quests.dynamic_quests["dq_manual"]
        assert quest["objectives"][0]["completed"] is False

    def test_all_objectives_completed_advances_quest_to_completed(self) -> None:
        """When all objectives are completed, quest status becomes 'completed'."""
        state = _make_state_with_quests({
            "dq_finish": {
                "status": "active",
                "title": "Finish Quest",
                "requires_report": False,
                "objectives": [
                    {
                        "description": "Kill 1 goblin",
                        "completed": False,
                        "condition": {
                            "type": "kill_count",
                            "params": {"monster_type": "goblin", "count": 1},
                        },
                    },
                ],
            },
        })
        state.flags.restore({"flags": {"kill_count_goblin": 3}})
        context = _make_context(state)

        def _run():
            return QuestObjectiveTrackingHook().execute(context)

        result = asyncio.run(_run())

        quest = context.state.quests.dynamic_quests["dq_finish"]
        assert quest["status"] == "completed"
        sse_types = [e.event_type for e in result.sse_events]
        assert "quest_status_changed" in sse_types
        status_event = next(e for e in result.sse_events if e.event_type == "quest_status_changed")
        assert status_event.payload["new_status"] == "completed"

    def test_all_objectives_completed_with_requires_report(self) -> None:
        """When requires_report=True, status becomes 'ready_to_report'."""
        state = _make_state_with_quests({
            "dq_report": {
                "status": "active",
                "title": "Report Quest",
                "requires_report": True,
                "objectives": [
                    {
                        "description": "Kill 1 goblin",
                        "completed": False,
                        "condition": {
                            "type": "kill_count",
                            "params": {"monster_type": "goblin", "count": 1},
                        },
                    },
                ],
            },
        })
        state.flags.restore({"flags": {"kill_count_goblin": 5}})
        context = _make_context(state)

        def _run():
            return QuestObjectiveTrackingHook().execute(context)

        asyncio.run(_run())

        quest = context.state.quests.dynamic_quests["dq_report"]
        assert quest["status"] == "ready_to_report"

    def test_mixed_objectives_partial_auto_track(self) -> None:
        """Only conditioned objectives are auto-tracked; manual ones are skipped."""
        state = _make_state_with_quests({
            "dq_mixed": {
                "status": "active",
                "title": "Mixed Quest",
                "objectives": [
                    {
                        "description": "Kill 1 goblin",
                        "completed": False,
                        "condition": {
                            "type": "kill_count",
                            "params": {"monster_type": "goblin", "count": 1},
                        },
                    },
                    {"description": "Talk to innkeeper", "completed": False},
                ],
            },
        })
        state.flags.restore({"flags": {"kill_count_goblin": 2}})
        context = _make_context(state)

        def _run():
            return QuestObjectiveTrackingHook().execute(context)

        result = asyncio.run(_run())

        quest = context.state.quests.dynamic_quests["dq_mixed"]
        # First objective auto-marked
        assert quest["objectives"][0]["completed"] is True
        # Second (no condition) untouched
        assert quest["objectives"][1]["completed"] is False
        # Quest should NOT advance because manual obj is still pending
        assert quest["status"] == "active"
        # Hook updated the quest (one objective changed)
        assert result.metadata["updated_quest_count"] == 1

    def test_skip_non_active_quests(self) -> None:
        """Hook skips quests that are not in 'active' status."""
        state = _make_state_with_quests({
            "dq_done": {
                "status": "completed",
                "objectives": [
                    {
                        "description": "Kill goblin",
                        "completed": False,
                        "condition": {"type": "kill_count", "params": {"monster_type": "goblin", "count": 1}},
                    },
                ],
            },
        })
        state.flags.restore({"flags": {"kill_count_goblin": 5}})
        context = _make_context(state)

        def _run():
            return QuestObjectiveTrackingHook().execute(context)

        result = asyncio.run(_run())

        assert result.metadata["status"] == "noop"
        # Objective NOT changed on a non-active quest
        quest = context.state.quests.dynamic_quests["dq_done"]
        assert quest["objectives"][0]["completed"] is False

    def test_no_quests_slice_returns_noop(self) -> None:
        """If quests slice is absent, hook returns noop without error."""
        state = StateContainer()
        world = WorldInstance("test_world")
        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)
        scene_bus = SceneBus(scene_slice)
        rules_engine = RulesEngine()
        change_log: list[StateChange] = []

        def _apply_delta(delta: StateDelta | None) -> None:
            pass

        context = SettlementContext(
            change_log=change_log,
            state=state,
            world=world,
            scene_bus=scene_bus,
            _rules_engine=rules_engine,
            _apply_delta=_apply_delta,
        )

        def _run():
            return QuestObjectiveTrackingHook().execute(context)

        result = asyncio.run(_run())
        assert result.metadata["status"] == "noop"
