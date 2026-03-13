"""Tests for Phase 3.3 quest completion feedback loop.

Tests cover:
- Reward dispatch (gold, xp, items) on dynamic quest auto-complete
- quest_completed SSE emission via run_inline_event_check
- Milestone COMPLETED detection: milestone_completed SSE
- Milestone cascade unlock of next_milestones
- Chapter completion update
- quests.json data integrity
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest

from app.game_core.content import WorldInstance
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.registries.quests import QuestRegistry
from app.game_core.orchestration.event_engine import (
    _apply_complete_objective,
    _apply_quest_rewards,
    run_inline_event_check,
)
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.models import Command
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_with_player(
    *,
    gold: int = 100,
    xp: int = 0,
    level: int = 1,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "gold": gold,
            "xp": xp,
            "level": level,
            "current_area": "frontier_town",
            "current_location": None,
            "inventory": [],
        }
    )
    state.register(player)
    quests = QuestSlice()
    quests.restore({})
    state.register(quests)
    return state


def _make_dynamic_quest(
    *,
    objectives: list[dict[str, Any]],
    rewards: dict[str, Any] | None = None,
    title: str = "Test Quest",
) -> dict[str, Any]:
    quest: dict[str, Any] = {
        "title": title,
        "summary": "A test quest",
        "status": "active",
        "objectives": objectives,
    }
    if rewards is not None:
        quest["rewards"] = rewards
    return quest


def _make_event_check_context(
    state: StateContainer,
    *,
    events_payload: dict[str, Any] | None = None,
) -> tuple[RulesEngine, SceneBus, list[StateChange]]:
    """Return (rules_engine, scene_bus, change_log) ready for run_inline_event_check."""
    events = EventSlice()
    events.restore(events_payload or {})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    change_log: list[StateChange] = []
    return rules_engine, scene_bus, change_log


def _apply_delta_fn(state: StateContainer, change_log: list[StateChange]) -> Any:
    def _apply(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    return _apply


# ---------------------------------------------------------------------------
# Phase 2: _apply_quest_rewards tests
# ---------------------------------------------------------------------------


class TestApplyQuestRewards:
    def test_applies_gold_reward(self) -> None:
        state = _make_state_with_player(gold=50)
        _apply_quest_rewards(state, {"gold": 200})
        assert state.player.gold == 250

    def test_applies_xp_reward(self) -> None:
        state = _make_state_with_player(xp=0, level=1)
        _apply_quest_rewards(state, {"xp": 500})
        assert state.player.xp == 500

    def test_xp_reward_triggers_levelup(self) -> None:
        state = _make_state_with_player(xp=900, level=1)
        # Level 1 threshold = 1 * 1000 = 1000; after +500 xp=1400 -> level 2
        _apply_quest_rewards(state, {"xp": 500})
        assert state.player.level == 2

    def test_applies_item_reward(self) -> None:
        state = _make_state_with_player()
        _apply_quest_rewards(
            state,
            {"items": [{"item_id": "sword_of_testing", "count": 1}]},
        )
        inv = state.player.snapshot()["inventory"]
        assert any(item["item_id"] == "sword_of_testing" for item in inv)

    def test_applies_multiple_items(self) -> None:
        state = _make_state_with_player()
        _apply_quest_rewards(
            state,
            {
                "items": [
                    {"item_id": "potion", "count": 3},
                    {"item_id": "scroll", "count": 1},
                ]
            },
        )
        inv = {item["item_id"]: item["count"] for item in state.player.snapshot()["inventory"]}
        assert inv.get("potion") == 3
        assert inv.get("scroll") == 1

    def test_empty_rewards_no_error(self) -> None:
        state = _make_state_with_player(gold=100)
        _apply_quest_rewards(state, {})
        assert state.player.gold == 100  # unchanged

    def test_no_player_slice_no_error(self) -> None:
        state = StateContainer()
        # No player slice registered — must not raise
        _apply_quest_rewards(state, {"gold": 100, "xp": 50})

    def test_non_mapping_rewards_no_error(self) -> None:
        state = _make_state_with_player(gold=100)
        # Passing None / non-mapping — must not raise
        _apply_quest_rewards(state, None)  # type: ignore[arg-type]
        assert state.player.gold == 100


# ---------------------------------------------------------------------------
# Phase 2: _apply_complete_objective + reward integration
# ---------------------------------------------------------------------------


class TestApplyCompleteObjective:
    def test_complete_all_required_triggers_reward_gold(self) -> None:
        state = _make_state_with_player(gold=0)
        state.quests.add_dynamic_quest(
            "q1",
            _make_dynamic_quest(
                objectives=[{"description": "Kill goblins", "required": True}],
                rewards={"gold": 300},
            ),
        )
        cmd = Command(
            type="complete_objective",
            params={"quest_id": "q1", "objective_index": 0},
            source="system",
        )
        result = _apply_complete_objective(state, cmd)
        assert result is not None
        assert result["quest_id"] == "q1"
        assert state.player.gold == 300
        assert state.quests.dynamic_quests["q1"]["status"] == "completed"

    def test_partial_completion_no_reward(self) -> None:
        state = _make_state_with_player(gold=0)
        state.quests.add_dynamic_quest(
            "q2",
            _make_dynamic_quest(
                objectives=[
                    {"description": "Step 1"},
                    {"description": "Step 2"},
                ],
                rewards={"gold": 100},
            ),
        )
        # Complete only first objective
        cmd = Command(
            type="complete_objective",
            params={"quest_id": "q2", "objective_index": 0},
            source="system",
        )
        result = _apply_complete_objective(state, cmd)
        # Only one of two done — should NOT complete or reward
        assert result is None
        assert state.player.gold == 0
        assert state.quests.dynamic_quests["q2"]["status"] == "active"

    def test_complete_with_empty_rewards_no_error(self) -> None:
        state = _make_state_with_player(gold=50)
        state.quests.add_dynamic_quest(
            "q3",
            _make_dynamic_quest(
                objectives=[{"description": "Only step"}],
                rewards={},
            ),
        )
        cmd = Command(
            type="complete_objective",
            params={"quest_id": "q3", "objective_index": 0},
            source="system",
        )
        result = _apply_complete_objective(state, cmd)
        assert result is not None
        assert state.player.gold == 50  # unchanged

    def test_complete_objective_records_change_log_when_provided(self) -> None:
        state = _make_state_with_player(gold=0)
        state.quests.add_dynamic_quest(
            "q4",
            _make_dynamic_quest(
                objectives=[{"description": "Only step"}],
                rewards={"gold": 10},
            ),
        )
        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)
        scene_bus = SceneBus(scene_slice)
        change_log: list[StateChange] = []

        cmd = Command(
            type="complete_objective",
            params={"quest_id": "q4", "objective_index": 0},
            source="system",
        )
        _apply_complete_objective(
            state,
            cmd,
            change_log=change_log,
            scene_bus=scene_bus,
        )

        assert [
            (change.slice, change.path, change.value)
            for change in change_log
        ] == [
            ("quests", "dynamic_quests.q4.objectives.0.completed", True),
            ("quests", "dynamic_quests.q4.status", "completed"),
        ]
        assert len(scene_bus.snapshot()["state_changes"]) == 2


# ---------------------------------------------------------------------------
# Phase 2c: quest_completed SSE via run_inline_event_check
# ---------------------------------------------------------------------------


class TestQuestCompletedSSE:
    def test_quest_completed_sse_emitted_on_event_trigger(self) -> None:
        """When an on_trigger command completes a quest, quest_completed SSE is collected."""

        state = _make_state_with_player(gold=0)
        # Register a flag
        flags = FlagSlice()
        flags.restore({})
        state.register(flags)

        # Register the quest
        state.quests.add_dynamic_quest(
            "q_event",
            _make_dynamic_quest(
                objectives=[{"description": "Triggered objective"}],
                rewards={"gold": 100},
            ),
        )

        rules_engine, scene_bus, change_log = _make_event_check_context(
            state,
            events_payload={
                "active_events": {
                    "ev_complete": {
                        "state": "locked",
                        "conditions": [{"type": "flag_set", "params": {"key": "trigger_me"}}],
                        "on_trigger": [
                            {
                                "type": "complete_objective",
                                "params": {"quest_id": "q_event", "objective_index": 0},
                            }
                        ],
                    }
                }
            },
        )

        # Set the flag to trigger the event
        state.flags.set("trigger_me", True)

        collected: list[SSEEvent] = []
        run_inline_event_check(
            state=state,
            world=WorldInstance("test"),
            rules_engine=rules_engine,
            apply_delta=_apply_delta_fn(state, change_log),
            change_log=change_log,
            scene_bus=scene_bus,
            label="test",
            sse_collector=collected,
        )

        quest_completed_events = [e for e in collected if e.event_type == "quest_completed"]
        assert len(quest_completed_events) == 1
        payload = quest_completed_events[0].payload
        assert payload["quest_id"] == "q_event"
        assert payload["rewards"]["gold"] == 100

    def test_no_quest_completed_sse_when_no_completion(self) -> None:
        """Partial objective completion does not emit quest_completed SSE."""

        state = _make_state_with_player()
        flags = FlagSlice()
        flags.restore({})
        state.register(flags)

        state.quests.add_dynamic_quest(
            "q_partial",
            _make_dynamic_quest(
                objectives=[{"description": "A"}, {"description": "B"}],
            ),
        )

        rules_engine, scene_bus, change_log = _make_event_check_context(
            state,
            events_payload={
                "active_events": {
                    "ev_partial": {
                        "state": "locked",
                        "conditions": [{"type": "flag_set", "params": {"key": "go_partial"}}],
                        "on_trigger": [
                            {
                                "type": "complete_objective",
                                "params": {"quest_id": "q_partial", "objective_index": 0},
                            }
                        ],
                    }
                }
            },
        )
        state.flags.set("go_partial", True)

        collected: list[SSEEvent] = []
        run_inline_event_check(
            state=state,
            world=WorldInstance("test"),
            rules_engine=rules_engine,
            apply_delta=_apply_delta_fn(state, change_log),
            change_log=change_log,
            scene_bus=scene_bus,
            label="test",
            sse_collector=collected,
        )

        quest_completed_events = [e for e in collected if e.event_type == "quest_completed"]
        assert len(quest_completed_events) == 0


# ---------------------------------------------------------------------------
# Phase 3: NarrativePlannerHook milestone cascade tests
# ---------------------------------------------------------------------------


def _make_quest_world() -> WorldInstance:
    """Build a WorldInstance with a quests registry for milestone tests."""
    quest_data = {
        "chapters": [{"id": "ch1", "title": "Chapter 1"}],
        "milestones": {
            "ms_first": {
                "id": "ms_first",
                "title": "First Milestone",
                "chapter_id": "ch1",
                "completion_value": 20,
                "next_milestones": ["ms_second"],
            },
            "ms_second": {
                "id": "ms_second",
                "title": "Second Milestone",
                "chapter_id": "ch1",
                "completion_value": 30,
                "next_milestones": [],
            },
        },
    }
    world = WorldInstance("test_world")
    quest_reg = QuestRegistry()
    quest_reg.load(quest_data)
    world.register(quest_reg)

    maps = MapRegistry()
    maps.load({})
    world.register(maps)
    return world


def _make_planner_context(
    *,
    milestone_states: dict[str, Any] | None = None,
    chapter_completion: dict[str, float] | None = None,
    world: WorldInstance | None = None,
) -> SettlementContext:
    """Build a SettlementContext with qnp+time+areas slices."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    quests = QuestSlice()
    quests.restore(
        {
            "milestone_states": milestone_states or {},
            "dynamic_quests": {},
            "chapter_completion": chapter_completion or {},
        }
    )
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "ch1", "ticks_since_milestone_progress": 0})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {}})
    state.register(areas)

    events = EventSlice()
    events.restore({})
    state.register(events)

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
        for change in delta.changes:
            scene_bus.record_state_change(change)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world or _make_quest_world(),
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


class TestMilestoneCascadeAndSSE:
    def test_milestone_completed_sse_emitted(self) -> None:
        """COMPLETED milestone transition → milestone_completed SSE in HookResult."""

        def _run() -> None:
            context = _make_planner_context(
                milestone_states={"ms_first": {"state": "ACTIVE"}, "ms_second": {"state": "LOCKED"}},
            )
            # Simulate a milestone being advanced to COMPLETED via execute_command
            context.execute_command(Command(
                type="advance_quest",
                params={"quest_id": "ms_first", "to_state": "COMPLETED", "quest_kind": "milestone"},
                source="system",
            ))

            hook = NarrativePlannerHook()
            result = asyncio.run(hook.execute(context))

            completed_events = [e for e in result.sse_events if e.event_type == "milestone_completed"]
            assert len(completed_events) == 1
            payload = completed_events[0].payload
            assert payload["milestone_id"] == "ms_first"
            assert payload["title"] == "First Milestone"
            assert payload["completion_value"] == 20

        _run()

    def test_milestone_cascade_unlocks_next(self) -> None:
        """Completing ms_first cascades unlock to ms_second (LOCKED → AVAILABLE)."""

        def _run() -> None:
            context = _make_planner_context(
                milestone_states={
                    "ms_first": {"state": "ACTIVE"},
                    "ms_second": {"state": "LOCKED"},
                },
            )
            # Advance ms_first to COMPLETED
            context.execute_command(Command(
                type="advance_quest",
                params={"quest_id": "ms_first", "to_state": "COMPLETED", "quest_kind": "milestone"},
                source="system",
            ))

            hook = NarrativePlannerHook()
            asyncio.run(hook.execute(context))

            # ms_second should now be AVAILABLE
            ms2 = context.state.quests.get_milestone("ms_second")
            assert ms2 is not None
            assert ms2.state == "AVAILABLE"

        _run()

    def test_cascade_skips_non_locked(self) -> None:
        """If ms_second is already AVAILABLE, cascade should NOT downgrade it."""

        def _run() -> None:
            context = _make_planner_context(
                milestone_states={
                    "ms_first": {"state": "ACTIVE"},
                    "ms_second": {"state": "AVAILABLE"},  # already unlocked
                },
            )
            context.execute_command(Command(
                type="advance_quest",
                params={"quest_id": "ms_first", "to_state": "COMPLETED", "quest_kind": "milestone"},
                source="system",
            ))

            hook = NarrativePlannerHook()
            asyncio.run(hook.execute(context))

            # ms_second should still be AVAILABLE (not changed)
            ms2 = context.state.quests.get_milestone("ms_second")
            assert ms2 is not None
            assert ms2.state == "AVAILABLE"

        _run()

    def test_milestone_chapter_completion_updated(self) -> None:
        """Completing ms_first (completion_value=20) adds 0.20 to ch1 chapter_completion."""

        def _run() -> None:
            context = _make_planner_context(
                milestone_states={
                    "ms_first": {"state": "ACTIVE"},
                    "ms_second": {"state": "LOCKED"},
                },
                chapter_completion={"ch1": 0.0},
            )
            context.execute_command(Command(
                type="advance_quest",
                params={"quest_id": "ms_first", "to_state": "COMPLETED", "quest_kind": "milestone"},
                source="system",
            ))

            hook = NarrativePlannerHook()
            asyncio.run(hook.execute(context))

            completion = context.state.quests.get_completion("ch1")
            assert abs(completion - 0.20) < 1e-9

        _run()

    def test_no_cascade_without_quest_registry(self) -> None:
        """Without a quests registry, no cascade happens but no crash either."""

        def _run() -> None:
            world = WorldInstance("bare_world")  # no quest registry
            context = _make_planner_context(
                milestone_states={"ms_first": {"state": "ACTIVE"}},
                world=world,
            )
            context.execute_command(Command(
                type="advance_quest",
                params={"quest_id": "ms_first", "to_state": "COMPLETED", "quest_kind": "milestone"},
                source="system",
            ))

            hook = NarrativePlannerHook()
            result = asyncio.run(hook.execute(context))

            # Should still emit the SSE (with milestone_id as title fallback)
            completed_events = [e for e in result.sse_events if e.event_type == "milestone_completed"]
            assert len(completed_events) == 1
            payload = completed_events[0].payload
            assert payload["title"] == "ms_first"  # fallback: milestone_id
            assert payload["completion_value"] == 0

        _run()


# ---------------------------------------------------------------------------
# Phase 1: quests.json data integrity
# ---------------------------------------------------------------------------


class TestQuestsJsonData:
    def test_success_conditions_loaded_for_all_milestones(self) -> None:
        """All 7 milestones in goblin_slayer quests.json should have success_conditions."""
        quests_path = (
            pathlib.Path(__file__).parent.parent
            / "data"
            / "goblin_slayer"
            / "v2"
            / "quests.json"
        )
        assert quests_path.exists(), f"quests.json not found at {quests_path}"
        data = json.loads(quests_path.read_text(encoding="utf-8"))

        reg = QuestRegistry()
        reg.load(data)
        issues = reg.validate()
        assert not issues, f"QuestRegistry validation errors: {issues}"

        milestones = {ms.id: ms for ms in reg.list_all()}
        expected_ids = {
            "ms_arrival", "ms_town_life", "ms_party_encounter",
            "ms_growing_shadow", "ms_into_the_wilds", "ms_the_hive",
            "ms_water_capital_call",
        }
        assert expected_ids.issubset(milestones.keys())

        for ms_id in expected_ids:
            ms = milestones[ms_id]
            assert len(ms.success_conditions) >= 1, (
                f"Milestone {ms_id} has no success_conditions"
            )

    def test_arrival_conditions(self) -> None:
        """ms_arrival should have npc_talked condition for guild_girl."""
        quests_path = (
            pathlib.Path(__file__).parent.parent
            / "data"
            / "goblin_slayer"
            / "v2"
            / "quests.json"
        )
        data = json.loads(quests_path.read_text(encoding="utf-8"))
        reg = QuestRegistry()
        reg.load(data)

        ms = reg.get_milestone("ms_arrival")
        assert ms is not None
        cond_types = {c.type for c in ms.success_conditions}
        assert "npc_talked" in cond_types

    def test_the_hive_conditions_types(self) -> None:
        """ms_the_hive should have kill_count and location_visited conditions."""
        quests_path = (
            pathlib.Path(__file__).parent.parent
            / "data"
            / "goblin_slayer"
            / "v2"
            / "quests.json"
        )
        data = json.loads(quests_path.read_text(encoding="utf-8"))
        reg = QuestRegistry()
        reg.load(data)

        ms = reg.get_milestone("ms_the_hive")
        assert ms is not None
        cond_types = {c.type for c in ms.success_conditions}
        assert "kill_count" in cond_types
        assert "location_visited" in cond_types

    def test_growing_shadow_conditions_types(self) -> None:
        """ms_growing_shadow should have npc_talked conditions."""
        quests_path = (
            pathlib.Path(__file__).parent.parent
            / "data"
            / "goblin_slayer"
            / "v2"
            / "quests.json"
        )
        data = json.loads(quests_path.read_text(encoding="utf-8"))
        reg = QuestRegistry()
        reg.load(data)

        ms = reg.get_milestone("ms_growing_shadow")
        assert ms is not None
        cond_types = {c.type for c in ms.success_conditions}
        assert "npc_talked" in cond_types

    def test_next_milestones_chain_intact(self) -> None:
        """Full 7-milestone chain: arrival → town_life → encounter → shadow → wilds → hive → water_capital."""
        quests_path = (
            pathlib.Path(__file__).parent.parent
            / "data"
            / "goblin_slayer"
            / "v2"
            / "quests.json"
        )
        data = json.loads(quests_path.read_text(encoding="utf-8"))
        reg = QuestRegistry()
        reg.load(data)

        assert "ms_town_life" in reg.get_milestone("ms_arrival").next_milestones
        assert "ms_party_encounter" in reg.get_milestone("ms_town_life").next_milestones
        assert "ms_growing_shadow" in reg.get_milestone("ms_party_encounter").next_milestones
        assert "ms_into_the_wilds" in reg.get_milestone("ms_growing_shadow").next_milestones
        assert "ms_the_hive" in reg.get_milestone("ms_into_the_wilds").next_milestones
        assert "ms_water_capital_call" in reg.get_milestone("ms_the_hive").next_milestones
        assert reg.get_milestone("ms_water_capital_call").next_milestones == []
