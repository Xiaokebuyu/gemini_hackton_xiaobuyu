"""Tests for P28 Track A — task auto-completion monitor.

Covers:
- A-1: level_reached condition in EventEngine
- A-2: set_task_monitor directive contract validation
- A-3: PlannerQuestHandler for planner_set_task_monitor command
- A-4: QuestManagerSubSystem.apply_directive("set_task_monitor")
- A-5: TaskMonitorHook auto-complete / notify / reward logic

Decision record: P28-动态能力系统与综合体验修复.md §6
"""

from __future__ import annotations

import asyncio
from typing import Any
import unittest.mock as mock

from app.game_core.content import WorldInstance
from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
from app.game_core.orchestration.hooks.task_monitor import TaskMonitorHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.directive_contracts import validate_planner_directive
from app.game_core.planning.quest_manager import QuestManagerSubSystem
from app.game_core.planning.subsystem import PlannerDispatcher
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_state(
    *,
    dynamic_quests: dict[str, Any] | None = None,
    flags: dict[str, Any] | None = None,
    player_data: dict[str, Any] | None = None,
) -> StateContainer:
    state = StateContainer()

    quests = QuestSlice()
    quests.restore({
        "dynamic_quests": dynamic_quests or {},
        "milestone_states": {},
        "chapter_completion": {},
    })
    state.register(quests)

    flag_slice = FlagSlice()
    flag_slice.restore({"flags": flags or {}})
    state.register(flag_slice)

    if player_data is not None:
        player = PlayerSlice()
        player.restore(player_data)
        state.register(player)

    return state


def _make_context(
    *,
    dynamic_quests: dict[str, Any] | None = None,
    flags: dict[str, Any] | None = None,
    player_data: dict[str, Any] | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
    state = _make_state(
        dynamic_quests=dynamic_quests,
        flags=flags,
        player_data=player_data,
    )

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    if not state.has_slice("narrative_plan"):
        narrative_plan = NarrativePlanSlice()
        narrative_plan.restore({"current_chapter": "ch1"})
        state.register(narrative_plan)

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


def _active_quest(
    *,
    title: str = "Test Quest",
    rewards: dict[str, Any] | None = None,
    task_monitor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quest: dict[str, Any] = {
        "title": title,
        "summary": "A test quest.",
        "status": "active",
        "objectives": [],
    }
    if rewards is not None:
        quest["rewards"] = rewards
    if task_monitor is not None:
        quest["task_monitor"] = task_monitor
    return quest


# ===========================================================================
# TestLevelReached — A-1
# ===========================================================================


class TestLevelReached:
    def test_level_met(self) -> None:
        """level_reached returns True when player.level >= required."""
        state = _make_state(player_data={"level": 5, "name": "Hero"})
        evaluator = BasicEventConditionEvaluator()
        result, _ = evaluator._condition_met(
            state,
            {"type": "level_reached", "params": {"level": 5}},
        )
        assert result is True

    def test_level_exceeds_required(self) -> None:
        """level_reached returns True when player.level exceeds required."""
        state = _make_state(player_data={"level": 7, "name": "Hero"})
        evaluator = BasicEventConditionEvaluator()
        result, _ = evaluator._condition_met(
            state,
            {"type": "level_reached", "params": {"level": 3}},
        )
        assert result is True

    def test_level_not_met(self) -> None:
        """level_reached returns False when player.level < required."""
        state = _make_state(player_data={"level": 2, "name": "Hero"})
        evaluator = BasicEventConditionEvaluator()
        result, _ = evaluator._condition_met(
            state,
            {"type": "level_reached", "params": {"level": 5}},
        )
        assert result is False

    def test_no_player_slice(self) -> None:
        """level_reached returns False when player slice is absent."""
        state = StateContainer()
        evaluator = BasicEventConditionEvaluator()
        result, _ = evaluator._condition_met(
            state,
            {"type": "level_reached", "params": {"level": 1}},
        )
        assert result is False


# ===========================================================================
# TestSetTaskMonitorContract — A-2
# ===========================================================================


class TestSetTaskMonitorContract:
    def test_valid_auto(self) -> None:
        """Valid set_task_monitor directive with on_complete=auto passes."""
        result = validate_planner_directive({
            "kind": "set_task_monitor",
            "payload": {
                "quest_id": "dq_patrol",
                "conditions": [{"type": "kill_count", "params": {"monster_type": "goblin", "count": 3}}],
                "on_complete": "auto",
            },
        })
        assert result.ok
        assert result.kind == "set_task_monitor"

    def test_valid_notify(self) -> None:
        """Valid set_task_monitor directive with on_complete=notify passes."""
        result = validate_planner_directive({
            "kind": "set_task_monitor",
            "payload": {
                "quest_id": "dq_patrol",
                "conditions": [{"type": "npc_talked", "params": {"npc_id": "guild_girl"}}],
                "on_complete": "notify",
            },
        })
        assert result.ok
        assert result.payload["on_complete"] == "notify"

    def test_default_on_complete_is_auto(self) -> None:
        """on_complete defaults to 'auto' when omitted."""
        result = validate_planner_directive({
            "kind": "set_task_monitor",
            "payload": {
                "quest_id": "dq_patrol",
                "conditions": [{"type": "kill_count", "params": {"monster_type": "goblin", "count": 1}}],
            },
        })
        assert result.ok
        assert result.payload["on_complete"] == "auto"

    def test_missing_quest_id(self) -> None:
        """Missing quest_id produces missing_quest_id reason code."""
        result = validate_planner_directive({
            "kind": "set_task_monitor",
            "payload": {
                "conditions": [{"type": "kill_count"}],
                "on_complete": "auto",
            },
        })
        assert not result.ok
        assert result.reason_code == "missing_quest_id"

    def test_empty_conditions(self) -> None:
        """Empty conditions list produces missing_conditions reason code."""
        result = validate_planner_directive({
            "kind": "set_task_monitor",
            "payload": {
                "quest_id": "dq_patrol",
                "conditions": [],
                "on_complete": "auto",
            },
        })
        assert not result.ok
        assert result.reason_code == "missing_conditions"

    def test_invalid_on_complete(self) -> None:
        """on_complete with unsupported value produces invalid_on_complete."""
        result = validate_planner_directive({
            "kind": "set_task_monitor",
            "payload": {
                "quest_id": "dq_patrol",
                "conditions": [{"type": "kill_count"}],
                "on_complete": "immediate",
            },
        })
        assert not result.ok
        assert result.reason_code == "invalid_on_complete"


# ===========================================================================
# TestSetTaskMonitorHandler — A-3
# ===========================================================================


class TestSetTaskMonitorHandler:
    def test_success(self) -> None:
        """planner_set_task_monitor persists task_monitor on active quest."""
        context = _make_context(dynamic_quests={"dq_1": _active_quest()})
        manager = QuestManagerSubSystem(PlannerDispatcher())

        result = manager.apply_directive(
            "set_task_monitor",
            {
                "quest_id": "dq_1",
                "conditions": [{"type": "kill_count", "params": {"monster_type": "goblin", "count": 3}}],
                "on_complete": "auto",
            },
            context,
            current_tick=5,
        )

        assert result is True
        quest = context.state.quests.dynamic_quests["dq_1"]
        assert "task_monitor" in quest
        assert quest["task_monitor"]["on_complete"] == "auto"
        assert len(quest["task_monitor"]["conditions"]) == 1

    def test_quest_not_found(self) -> None:
        """set_task_monitor fails when quest_id doesn't exist."""
        context = _make_context()
        manager = QuestManagerSubSystem(PlannerDispatcher())

        result = manager.apply_directive(
            "set_task_monitor",
            {
                "quest_id": "nonexistent",
                "conditions": [{"type": "kill_count"}],
                "on_complete": "auto",
            },
            context,
            current_tick=1,
        )

        assert result is not True  # Error string or False

    def test_quest_not_active(self) -> None:
        """set_task_monitor fails when quest is not active."""
        context = _make_context(dynamic_quests={
            "dq_2": {
                "title": "Available Quest",
                "status": "available",
                "objectives": [],
            }
        })
        manager = QuestManagerSubSystem(PlannerDispatcher())

        result = manager.apply_directive(
            "set_task_monitor",
            {
                "quest_id": "dq_2",
                "conditions": [{"type": "kill_count"}],
                "on_complete": "auto",
            },
            context,
            current_tick=1,
        )

        assert result is not True


# ===========================================================================
# TestTaskMonitorHook — A-5
# ===========================================================================


class TestTaskMonitorHook:
    def test_auto_complete_triggers(self) -> None:
        """When all conditions met and on_complete=auto, quest is completed."""
        context = _make_context(
            dynamic_quests={
                "dq_patrol": _active_quest(task_monitor={
                    "conditions": [
                        {"type": "kill_count", "params": {"monster_type": "goblin", "count": 2}},
                    ],
                    "on_complete": "auto",
                }),
            },
            flags={"kill_count_goblin": 3},
        )

        async def _run() -> None:
            hook = TaskMonitorHook()
            result = await hook.execute(context)
            assert result.metadata["completed_count"] == 1
            assert "dq_patrol" in result.metadata["completed"]
            completed_sse = [e for e in result.sse_events if e.event_type == "task_monitor_completed"]
            assert len(completed_sse) == 1
            assert completed_sse[0].payload["quest_id"] == "dq_patrol"
            # Quest status should be completed
            quest = context.state.quests.dynamic_quests["dq_patrol"]
            assert quest["status"] == "completed"
            assert quest["rewards_claimed"] is True

        asyncio.run(_run())

    def test_notify_emits_sse(self) -> None:
        """When conditions met and on_complete=notify, SSE is emitted but quest not completed."""
        context = _make_context(
            dynamic_quests={
                "dq_watch": _active_quest(task_monitor={
                    "conditions": [
                        {"type": "kill_count", "params": {"monster_type": "wolf", "count": 1}},
                    ],
                    "on_complete": "notify",
                }),
            },
            flags={"kill_count_wolf": 2},
        )

        async def _run() -> None:
            hook = TaskMonitorHook()
            result = await hook.execute(context)
            assert result.metadata["triggered_count"] == 1
            assert "dq_watch" in result.metadata["triggered"]
            triggered_sse = [e for e in result.sse_events if e.event_type == "task_monitor_triggered"]
            assert len(triggered_sse) == 1
            # Quest should NOT be completed
            quest = context.state.quests.dynamic_quests["dq_watch"]
            assert quest["status"] == "active"

        asyncio.run(_run())

    def test_conditions_not_met(self) -> None:
        """When conditions are NOT met, hook returns noop."""
        context = _make_context(
            dynamic_quests={
                "dq_patrol": _active_quest(task_monitor={
                    "conditions": [
                        {"type": "kill_count", "params": {"monster_type": "goblin", "count": 5}},
                    ],
                    "on_complete": "auto",
                }),
            },
            flags={"kill_count_goblin": 2},  # Only 2, need 5
        )

        async def _run() -> None:
            hook = TaskMonitorHook()
            result = await hook.execute(context)
            assert result.metadata["status"] == "noop"
            assert result.metadata["completed_count"] == 0
            quest = context.state.quests.dynamic_quests["dq_patrol"]
            assert quest["status"] == "active"

        asyncio.run(_run())

    def test_no_monitor_field(self) -> None:
        """Quests without task_monitor field are skipped."""
        context = _make_context(
            dynamic_quests={"dq_normal": _active_quest()},
        )

        async def _run() -> None:
            hook = TaskMonitorHook()
            result = await hook.execute(context)
            assert result.metadata["status"] == "noop"
            assert result.metadata["completed_count"] == 0

        asyncio.run(_run())

    def test_reward_applied(self) -> None:
        """Auto-complete applies gold/xp rewards to player."""
        context = _make_context(
            dynamic_quests={
                "dq_reward": _active_quest(
                    rewards={"gold": 100, "xp": 300},
                    task_monitor={
                        "conditions": [
                            {"type": "flag_set", "params": {"key": "patrol_done", "value": True}},
                        ],
                        "on_complete": "auto",
                    },
                ),
            },
            flags={"patrol_done": True},
            player_data={"name": "Hero", "gold": 50, "xp": 0, "level": 1},
        )

        async def _run() -> None:
            hook = TaskMonitorHook()
            await hook.execute(context)
            player_snap = context.state.player.snapshot()
            assert player_snap.get("gold") == 150
            assert player_snap.get("xp") == 300

        asyncio.run(_run())

    def test_auto_complete_routes_through_advance_quest_command(self) -> None:
        context = _make_context(
            dynamic_quests={
                "dq_patrol": _active_quest(task_monitor={
                    "conditions": [
                        {"type": "kill_count", "params": {"monster_type": "goblin", "count": 1}},
                    ],
                    "on_complete": "auto",
                }),
            },
            flags={"kill_count_goblin": 1},
        )

        async def _run() -> None:
            hook = TaskMonitorHook()
            with mock.patch.object(
                context._rules_engine,
                "execute",
                wraps=context._rules_engine.execute,
            ) as execute_spy:
                result = await hook.execute(context)
            assert result.metadata["completed_count"] == 1
            commands = [call.args[0] for call in execute_spy.call_args_list]
            assert any(
                command.type == "advance_quest"
                and command.params.get("quest_id") == "dq_patrol"
                and command.params.get("claim_rewards") is True
                for command in commands
            )

        asyncio.run(_run())

    def test_no_quests_slice(self) -> None:
        """Hook returns noop gracefully when quests slice is absent."""
        world = WorldInstance("test_world")
        state = StateContainer()
        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)
        scene_bus = SceneBus(scene_slice)

        def _apply_delta(delta: StateDelta | None) -> None:
            if delta:
                state.apply(delta)

        context = SettlementContext(
            change_log=[],
            state=state,
            world=world,
            scene_bus=scene_bus,
            _rules_engine=RulesEngine(),
            _apply_delta=_apply_delta,
        )

        async def _run() -> None:
            hook = TaskMonitorHook()
            result = await hook.execute(context)
            assert result.metadata["status"] == "noop"
            assert result.metadata["reason"] == "missing_quests_slice"

        asyncio.run(_run())
