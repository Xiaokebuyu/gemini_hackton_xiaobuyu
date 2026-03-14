"""Tests for Round 4 planner capabilities:
R4-A: advance_milestone directive (contract validation + handler)
R4-B: outline_updates output field (slice method + decision parsing)
R4-C: quest completion auto-marks outline step (already implemented; one smoke test)
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries import QuestRegistry
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.orchestration.hooks.quest_objective_tracking import QuestObjectiveTrackingHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.directive_contracts import validate_planner_directive
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.engine import RulesEngine
from app.game_core.rules.handlers.planner import PlannerRuntimeHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateDelta, StateChange, StateContainer
from app.game_core.state.slices import (
    AreaSlice,
    FlagSlice,
    NarrativePlanSlice,
    QuestSlice,
    SceneSlice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_quest_registry_with_milestone(
    milestone_id: str = "ms_test",
    success_conditions: list[dict[str, Any]] | None = None,
) -> QuestRegistry:
    """Build a minimal QuestRegistry with one milestone."""
    if success_conditions is None:
        success_conditions = [
            {"type": "npc_talked", "params": {"npc_id": "npc_a"}},
            {"type": "npc_talked", "params": {"npc_id": "npc_b"}},
        ]
    registry = QuestRegistry()
    registry.load({
        "milestones": {
            milestone_id: {
                "id": milestone_id,
                "title": "Test Milestone",
                "chapter_id": "ch1",
                "completion_value": 50,
                "success_conditions": success_conditions,
            }
        }
    })
    return registry


def _make_world(
    milestone_id: str = "ms_test",
    success_conditions: list[dict[str, Any]] | None = None,
) -> WorldInstance:
    world = WorldInstance("test")
    registry = _make_quest_registry_with_milestone(milestone_id, success_conditions)
    world.register(registry)
    return world


def _make_state(
    *,
    milestone_id: str = "ms_test",
    milestone_state: str = "AVAILABLE",
    talked_to_npcs: list[str] | None = None,
) -> StateContainer:
    state = StateContainer()

    quests = QuestSlice()
    quests.restore({
        "milestone_states": {
            milestone_id: {"state": milestone_state, "tick": 0},
        },
        "dynamic_quests": {},
        "chapter_completion": {},
    })
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    flags = FlagSlice()
    flags_data: dict[str, Any] = {}
    if talked_to_npcs:
        for npc_id in talked_to_npcs:
            # FlagSlice stores flags at "talked_to_{npc_id}" (underscore separator)
            flags_data[f"talked_to_{npc_id}"] = True
    flags.restore({"flags": flags_data})
    state.register(flags)

    return state


# ---------------------------------------------------------------------------
# R4-A: advance_milestone directive contract validation
# ---------------------------------------------------------------------------

def test_advance_milestone_contract_valid() -> None:
    result = validate_planner_directive({
        "kind": "advance_milestone",
        "payload": {"milestone_id": "ms_test", "to_state": "COMPLETED"},
    })
    assert result.ok is True
    assert result.reason_code is None
    assert result.payload["milestone_id"] == "ms_test"
    assert result.payload["to_state"] == "COMPLETED"


def test_advance_milestone_contract_default_to_state() -> None:
    """to_state is optional and defaults to COMPLETED."""
    result = validate_planner_directive({
        "kind": "advance_milestone",
        "payload": {"milestone_id": "ms_test"},
    })
    assert result.ok is True
    assert result.payload["to_state"] == "COMPLETED"


def test_advance_milestone_contract_missing_milestone_id() -> None:
    result = validate_planner_directive({
        "kind": "advance_milestone",
        "payload": {},
    })
    assert result.ok is False
    assert result.reason_code == "missing_milestone_id"


def test_advance_milestone_contract_to_state_uppercased() -> None:
    result = validate_planner_directive({
        "kind": "advance_milestone",
        "payload": {"milestone_id": "ms_test", "to_state": "completed"},
    })
    assert result.ok is True
    assert result.payload["to_state"] == "COMPLETED"


# ---------------------------------------------------------------------------
# R4-A: advance_milestone handler — 80% threshold
# ---------------------------------------------------------------------------

def test_advance_milestone_succeeds_at_80_percent() -> None:
    """When >= 80% of conditions are met, the handler advances the milestone."""
    # 2 conditions: npc_a + npc_b.  Talk to npc_a only = 1/2 = 50% (should fail).
    # Talk to both = 2/2 = 100% (should succeed).
    world = _make_world(success_conditions=[
        {"type": "npc_talked", "params": {"npc_id": "npc_a"}},
        {"type": "npc_talked", "params": {"npc_id": "npc_b"}},
    ])
    state = _make_state(talked_to_npcs=["npc_a", "npc_b"])

    handler = PlannerRuntimeHandler()
    result = handler.compute(
        Command(
            type="planner_advance_milestone",
            params={"milestone_id": "ms_test", "to_state": "COMPLETED", "current_tick": 10},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True, result.errors
    # Verify the change is a milestone state advancement
    assert len(result.delta.changes) >= 1
    milestone_change = next(
        (c for c in result.delta.changes if c.path == "milestone_states.ms_test"),
        None,
    )
    assert milestone_change is not None
    assert milestone_change.value["state"] == "COMPLETED"


def test_advance_milestone_rejected_below_80_percent() -> None:
    """When < 80% of conditions are met, the handler rejects."""
    world = _make_world(success_conditions=[
        {"type": "npc_talked", "params": {"npc_id": "npc_a"}},
        {"type": "npc_talked", "params": {"npc_id": "npc_b"}},
    ])
    # Only npc_a talked to: 1/2 = 50% < 80%
    state = _make_state(talked_to_npcs=["npc_a"])

    handler = PlannerRuntimeHandler()
    result = handler.compute(
        Command(
            type="planner_advance_milestone",
            params={"milestone_id": "ms_test", "to_state": "COMPLETED"},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is False
    assert any("milestone_conditions_insufficient" in e for e in (result.errors or []))


def test_advance_milestone_with_no_conditions_succeeds() -> None:
    """A milestone with no success_conditions always passes the threshold check."""
    world = _make_world(success_conditions=[])
    state = _make_state()

    handler = PlannerRuntimeHandler()
    result = handler.compute(
        Command(
            type="planner_advance_milestone",
            params={"milestone_id": "ms_test", "to_state": "COMPLETED"},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True


def test_advance_milestone_with_three_conditions_two_met_passes() -> None:
    """3 conditions, 2 met = 66.7% < 80%; must fail."""
    world = _make_world(success_conditions=[
        {"type": "npc_talked", "params": {"npc_id": "npc_a"}},
        {"type": "npc_talked", "params": {"npc_id": "npc_b"}},
        {"type": "npc_talked", "params": {"npc_id": "npc_c"}},
    ])
    state = _make_state(talked_to_npcs=["npc_a", "npc_b"])  # 2/3 = 66.7%

    handler = PlannerRuntimeHandler()
    result = handler.compute(
        Command(
            type="planner_advance_milestone",
            params={"milestone_id": "ms_test", "to_state": "COMPLETED"},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is False


def test_advance_milestone_with_five_conditions_four_met_passes() -> None:
    """5 conditions, 4 met = 80%; should succeed."""
    world = _make_world(success_conditions=[
        {"type": "npc_talked", "params": {"npc_id": f"npc_{i}"}}
        for i in range(5)
    ])
    state = _make_state(talked_to_npcs=[f"npc_{i}" for i in range(4)])  # 4/5 = 80%

    handler = PlannerRuntimeHandler()
    result = handler.compute(
        Command(
            type="planner_advance_milestone",
            params={"milestone_id": "ms_test", "to_state": "COMPLETED"},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True


# ---------------------------------------------------------------------------
# R4-B: update_milestone_outline slice method
# ---------------------------------------------------------------------------

def test_outline_updates_mark_steps_completed() -> None:
    """completed_steps marks matching steps."""
    s = NarrativePlanSlice()
    s.milestone_outline = {
        "steps": [
            {"index": 0, "description": "step 0", "completed": False},
            {"index": 1, "description": "step 1", "completed": False},
        ]
    }
    s.update_milestone_outline({"completed_steps": [0]})
    steps = s.milestone_outline["steps"]
    assert steps[0]["completed"] is True
    assert steps[1]["completed"] is False
    assert s._dirty is True


def test_outline_updates_add_new_steps() -> None:
    """new_steps appends entries with auto-assigned index."""
    s = NarrativePlanSlice()
    s.milestone_outline = {
        "steps": [
            {"index": 0, "description": "existing", "completed": False},
        ]
    }
    s.update_milestone_outline({
        "new_steps": [{"description": "added step", "type": "dialogue"}]
    })
    steps = s.milestone_outline["steps"]
    assert len(steps) == 2
    assert steps[1]["description"] == "added step"
    assert steps[1]["type"] == "dialogue"
    assert steps[1]["completed"] is False
    assert steps[1]["index"] == 1  # auto-assigned


def test_outline_updates_remove_steps() -> None:
    """remove_steps removes steps by index value."""
    s = NarrativePlanSlice()
    s.milestone_outline = {
        "steps": [
            {"index": 0, "description": "step 0", "completed": False},
            {"index": 1, "description": "step 1", "completed": False},
            {"index": 2, "description": "step 2", "completed": False},
        ]
    }
    s.update_milestone_outline({"remove_steps": [1]})
    steps = s.milestone_outline["steps"]
    assert len(steps) == 2
    remaining_indices = [s["index"] for s in steps]
    assert 1 not in remaining_indices
    assert 0 in remaining_indices
    assert 2 in remaining_indices


def test_outline_updates_combined_operations() -> None:
    """All three update operations applied together."""
    s = NarrativePlanSlice()
    s.milestone_outline = {
        "steps": [
            {"index": 0, "description": "first", "completed": False},
            {"index": 1, "description": "second", "completed": False},
            {"index": 2, "description": "third", "completed": False},
        ]
    }
    s.update_milestone_outline({
        "completed_steps": [0, 1],
        "remove_steps": [2],
        "new_steps": [{"description": "new finale", "type": "exploration"}],
    })
    steps = s.milestone_outline["steps"]
    # step 2 removed, step 0 and 1 completed, new step appended
    assert len(steps) == 3
    assert steps[0]["completed"] is True
    assert steps[1]["completed"] is True
    # The new step should be at the end
    assert steps[2]["description"] == "new finale"
    assert steps[2]["completed"] is False


def test_outline_updates_noop_when_empty_dict() -> None:
    """Empty updates dict doesn't change anything or mark dirty."""
    s = NarrativePlanSlice()
    s.milestone_outline = {
        "steps": [{"index": 0, "description": "x", "completed": False}]
    }
    # Reset dirty manually (it was set by the milestone_outline assignment above)
    s.clear_dirty()
    s.update_milestone_outline({})
    assert s._dirty is False


def test_outline_updates_no_steps_yet_creates_steps_list() -> None:
    """new_steps creates the steps list if not already present."""
    s = NarrativePlanSlice()
    s.milestone_outline = {"target_milestone_id": "ms_test"}
    s.update_milestone_outline({
        "new_steps": [{"description": "first", "type": "dialogue"}]
    })
    assert "steps" in s.milestone_outline
    assert len(s.milestone_outline["steps"]) == 1


# ---------------------------------------------------------------------------
# R4-B: NarrativePlannerDecision includes outline_updates
# ---------------------------------------------------------------------------

def test_normalize_decision_extracts_outline_updates() -> None:
    """_normalize_decision extracts outline_updates from raw dict."""
    raw = {
        "directives": [],
        "story_facts": [],
        "strategy_notes": "",
        "outline_updates": {
            "completed_steps": [0],
            "new_steps": [],
            "remove_steps": [],
        },
    }
    decision = NarrativePlannerHook._normalize_decision(raw)
    assert isinstance(decision.outline_updates, dict)
    assert decision.outline_updates == {
        "completed_steps": [0],
        "new_steps": [],
        "remove_steps": [],
    }


def test_normalize_decision_outline_updates_defaults_to_empty() -> None:
    """When outline_updates is absent from raw, it defaults to empty dict."""
    raw = {"directives": [], "story_facts": [], "strategy_notes": ""}
    decision = NarrativePlannerHook._normalize_decision(raw)
    assert decision.outline_updates == {}


def test_normalize_decision_outline_updates_non_mapping_ignored() -> None:
    """Non-mapping outline_updates is silently ignored (defaults to empty)."""
    raw = {"directives": [], "outline_updates": [1, 2, 3]}
    decision = NarrativePlannerHook._normalize_decision(raw)
    assert decision.outline_updates == {}


# ---------------------------------------------------------------------------
# R4-C: Quest completion auto-marks outline step (smoke test)
# ---------------------------------------------------------------------------

def test_quest_completion_auto_marks_outline_step() -> None:
    """When a quest with metadata.step_index completes, outline step is marked done."""
    async def _run() -> None:
        state = StateContainer()

        quests = QuestSlice()
        quests.restore({
            "milestone_states": {},
            "dynamic_quests": {
                "dq_step_quest": {
                    "quest_id": "dq_step_quest",
                    "status": "active",
                    "title": "Step Quest",
                    "objectives": [
                        {
                            "description": "Talk to NPC",
                            "completed": False,
                            "condition": {"type": "npc_talked", "params": {"npc_id": "npc_x"}},
                        }
                    ],
                    "requires_report": False,
                    "metadata": {"step_index": 2},
                }
            },
            "chapter_completion": {},
        })
        state.register(quests)

        narrative_plan = NarrativePlanSlice()
        narrative_plan.restore({})
        narrative_plan.milestone_outline = {
            "steps": [
                {"index": 0, "description": "s0", "completed": False},
                {"index": 1, "description": "s1", "completed": False},
                {"index": 2, "description": "s2", "completed": False},
            ]
        }
        state.register(narrative_plan)

        flags = FlagSlice()
        flags.restore({"flags": {"talked_to_npc_x": True}})
        state.register(flags)

        world = WorldInstance("test")
        engine = RulesEngine()
        register_default_rules_handlers(engine)

        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)
        scene_bus = SceneBus(scene_slice)

        change_log: list[StateChange] = []

        def _apply_delta(delta: StateDelta | None) -> None:
            if delta is None:
                return
            state.apply(delta)
            change_log.extend(delta.changes)

        context = SettlementContext(
            change_log=change_log,
            state=state,
            world=world,
            scene_bus=scene_bus,
            _rules_engine=engine,
            _apply_delta=_apply_delta,
        )

        hook = QuestObjectiveTrackingHook()
        result = await hook.execute(context)

        assert result.metadata["status"] == "applied"
        # The outline step at index 2 should now be completed
        steps = state.narrative_plan.milestone_outline["steps"]
        step_2 = next(s for s in steps if s["index"] == 2)
        assert step_2["completed"] is True
        # Step 0 and 1 should remain incomplete
        step_0 = next(s for s in steps if s["index"] == 0)
        assert step_0["completed"] is False

    asyncio.run(_run())
