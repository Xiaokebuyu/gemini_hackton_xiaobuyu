"""Phase 3a tests:
- visited_area flag written on move_area
- EventEngine location_visited uses persistent flag
- MilestoneCompletionHook: single condition, multiple conditions, cascade with MilestoneUnlockHook
"""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry, QuestRegistry
from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
from app.game_core.orchestration.hooks.milestone_completion import MilestoneCompletionHook
from app.game_core.orchestration.hooks.milestone_unlock import MilestoneUnlockHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import NavigationHandler
from app.game_core.rules.handlers.world_state import WorldStateHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    FlagSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_nav_world() -> WorldInstance:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "town": {
                "id": "town",
                "name": "Town",
                "sub_locations": {"square": {"id": "square", "name": "Square"}},
                "default_sub_location": "square",
                "connections": [{"target_map_id": "forest"}],
            },
            "forest": {
                "id": "forest",
                "name": "Forest",
                "sub_locations": {
                    "camp": {
                        "id": "camp",
                        "name": "Camp",
                        "rooms": {"tent": {"id": "tent", "name": "Tent"}},
                    }
                },
                "default_sub_location": "camp",
                "connections": [{"target_map_id": "town"}],
            },
        }
    )
    world.register(maps)
    return world


def _make_full_state(
    *,
    area: str = "town",
    location: str | None = None,
    room: str | None = None,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": area, "current_location": location, "current_room": room})
    state.register(player)
    flags = FlagSlice()
    flags.restore({"flags": {}})
    state.register(flags)
    return state


def _make_settlement_context(
    state: StateContainer,
    world: WorldInstance,
) -> SettlementContext:
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
# 3a-1: visited_area flag on move_area
# ------------------------------------------------------------------


class TestVisitedAreaFlag:
    def test_move_area_writes_visited_area_flag(self) -> None:
        state = _make_full_state(area="town")
        world = _make_nav_world()

        engine = RulesEngine()
        engine.register(NavigationHandler())
        result = engine.execute(
            Command(type="move_area", params={"area_id": "forest"}),
            state,
            world,
        )

        assert result.executed is True
        assert result.delta is not None
        state.apply(result.delta)

        assert state.flags.get("visited_area_forest") is True

    def test_move_area_without_flags_slice_still_executes(self) -> None:
        """move_area succeeds even when flags slice is absent."""
        state = StateContainer()
        player = PlayerSlice()
        player.restore({"current_area": "town"})
        state.register(player)

        world = _make_nav_world()
        engine = RulesEngine()
        engine.register(NavigationHandler())

        result = engine.execute(
            Command(type="move_area", params={"area_id": "forest"}),
            state,
            world,
        )
        assert result.executed is True
        assert result.delta is not None
        # No KeyError — flag change is omitted when slice is absent
        state.apply(result.delta)
        assert state.player.current_area == "forest"

    def test_visited_area_flag_persists_across_return(self) -> None:
        """Flag stays True after player moves away."""
        state = _make_full_state(area="town")
        world = _make_nav_world()
        engine = RulesEngine()
        engine.register(NavigationHandler())

        # Go to forest
        r1 = engine.execute(Command(type="move_area", params={"area_id": "forest"}), state, world)
        state.apply(r1.delta)
        assert state.flags.get("visited_area_forest") is True

        # Return to town
        r2 = engine.execute(Command(type="move_area", params={"area_id": "town"}), state, world)
        state.apply(r2.delta)

        # flag for forest still True
        assert state.flags.get("visited_area_forest") is True
        assert state.flags.get("visited_area_town") is True

    def test_move_area_writes_visited_location_flag_for_auto_sub_location(self) -> None:
        state = _make_full_state(area="town", location="square")
        world = _make_nav_world()

        engine = RulesEngine()
        engine.register(NavigationHandler())
        result = engine.execute(
            Command(type="move_area", params={"area_id": "forest"}),
            state,
            world,
        )

        assert result.executed is True
        assert result.delta is not None
        state.apply(result.delta)

        assert state.flags.get("visited_location_forest__camp") is True

    def test_enter_room_writes_visited_room_flag(self) -> None:
        state = _make_full_state(area="forest", location="camp")
        world = _make_nav_world()

        engine = RulesEngine()
        engine.register(NavigationHandler())
        result = engine.execute(
            Command(type="enter_room", params={"room_id": "tent"}),
            state,
            world,
        )

        assert result.executed is True
        assert result.delta is not None
        state.apply(result.delta)

        assert state.flags.get("visited_room_forest__camp__tent") is True


# ------------------------------------------------------------------
# 3a-2: EventEngine location_visited uses persistent flag
# ------------------------------------------------------------------


class TestLocationVisitedFlag:
    def test_location_visited_checks_persistent_flag(self) -> None:
        """location_visited returns True when visited_area_X flag is set,
        even if player is not currently there."""
        state = StateContainer()
        player = PlayerSlice()
        player.restore({"current_area": "town"})  # currently in town
        state.register(player)
        flags = FlagSlice()
        flags.restore({"flags": {"visited_area_forest": True}})  # visited forest before
        state.register(flags)

        evaluator = BasicEventConditionEvaluator()
        condition = {"type": "location_visited", "area_id": "forest"}
        met, _ = evaluator._condition_met(state, condition)
        assert met is True

    def test_location_visited_falls_back_to_realtime_if_no_flag(self) -> None:
        """Without persistent flag, falls back to current_area check."""
        state = StateContainer()
        player = PlayerSlice()
        player.restore({"current_area": "forest"})
        state.register(player)
        flags = FlagSlice()
        flags.restore({"flags": {}})
        state.register(flags)

        evaluator = BasicEventConditionEvaluator()
        condition = {"type": "location_visited", "area_id": "forest"}
        met, _ = evaluator._condition_met(state, condition)
        assert met is True

    def test_location_visited_returns_false_when_neither(self) -> None:
        """Returns False when not currently there and no flag."""
        state = StateContainer()
        player = PlayerSlice()
        player.restore({"current_area": "town"})
        state.register(player)
        flags = FlagSlice()
        flags.restore({"flags": {}})
        state.register(flags)

        evaluator = BasicEventConditionEvaluator()
        condition = {"type": "location_visited", "area_id": "ancient_ruins"}
        met, _ = evaluator._condition_met(state, condition)
        assert met is False

    def test_location_visited_checks_persistent_sub_location_flag(self) -> None:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({"current_area": "town", "current_location": "square"})
        state.register(player)
        flags = FlagSlice()
        flags.restore({"flags": {"visited_location_forest__camp": True}})
        state.register(flags)

        evaluator = BasicEventConditionEvaluator()
        condition = {"type": "location_visited", "area_id": "forest", "location_id": "camp"}
        met, _ = evaluator._condition_met(state, condition)
        assert met is True

    def test_location_visited_checks_persistent_room_flag(self) -> None:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({"current_area": "town", "current_location": "square", "current_room": None})
        state.register(player)
        flags = FlagSlice()
        flags.restore({"flags": {"visited_room_forest__camp__tent": True}})
        state.register(flags)

        evaluator = BasicEventConditionEvaluator()
        condition = {
            "type": "location_visited",
            "area_id": "forest",
            "location_id": "camp",
            "room_id": "tent",
        }
        met, _ = evaluator._condition_met(state, condition)
        assert met is True


# ------------------------------------------------------------------
# 3a-3/4: MilestoneCompletionHook
# ------------------------------------------------------------------


def _make_quest_world_with_milestones(milestones_data: dict) -> WorldInstance:
    world = WorldInstance("test_world")
    quests = QuestRegistry()
    quests.load({"milestones": milestones_data})
    world.register(quests)
    return world


def _make_quest_state(milestone_states: dict) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": "town"})
    state.register(player)
    flags = FlagSlice()
    flags.restore({"flags": {}})
    state.register(flags)
    quests = QuestSlice()
    quests.restore({"milestone_states": milestone_states})
    state.register(quests)
    return state


class TestMilestoneCompletionHook:
    def test_noop_when_no_quests_slice(self) -> None:
        state = StateContainer()
        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)
        world = WorldInstance("test_world")

        rules_engine = RulesEngine()
        rules_engine.register(WorldStateHandler())
        ctx = SettlementContext(
            change_log=[],
            state=state,
            world=world,
            scene_bus=SceneBus(scene_slice),
            _rules_engine=rules_engine,
            _apply_delta=lambda d: state.apply(d) if d else None,
        )
        hook = MilestoneCompletionHook()

        def _run():
            return hook.execute(ctx)

        result = asyncio.run(_run())
        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "missing_quests_slice"

    def test_noop_when_no_quests_registry(self) -> None:
        state = _make_quest_state({"ms_1": {"state": "ACTIVE"}})
        world = WorldInstance("test_world")  # no quests registry
        ctx = _make_settlement_context(state, world)
        hook = MilestoneCompletionHook()

        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "missing_quests_registry"

    def test_single_flag_condition_met_completes_milestone(self) -> None:
        """ACTIVE milestone with flag_set condition: flag present → COMPLETED."""
        state = _make_quest_state({"ms_first": {"state": "ACTIVE"}})
        # Set the flag
        state.flags.set("talked_to_guild_girl", True)

        world = _make_quest_world_with_milestones({
            "ms_first": {
                "id": "ms_first",
                "title": "Test milestone",
                "chapter_id": "ch1",
                "success_conditions": [
                    {"type": "flag_set", "params": {"key": "talked_to_guild_girl"}}
                ],
            }
        })
        ctx = _make_settlement_context(state, world)
        hook = MilestoneCompletionHook()

        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["status"] == "applied"
        assert "ms_first" in result.metadata["completed"]
        assert state.quests.get_milestone_state("ms_first") == "COMPLETED"
        # SSE event emitted
        sse_types = [e.event_type for e in result.sse_events]
        assert "milestone_completed" in sse_types

    def test_completed_milestone_grants_rewards(self) -> None:
        state = _make_quest_state({"ms_reward": {"state": "ACTIVE"}})
        state.flags.set("talked_to_guild_girl", True)

        world = _make_quest_world_with_milestones({
            "ms_reward": {
                "id": "ms_reward",
                "title": "Reward milestone",
                "chapter_id": "ch1",
                "success_conditions": [
                    {"type": "flag_set", "params": {"key": "talked_to_guild_girl"}}
                ],
                "rewards": {
                    "gold": 40,
                    "xp": 120,
                    "items": [{"item_id": "healing_herb", "count": 2}],
                },
            }
        })
        ctx = _make_settlement_context(state, world)
        hook = MilestoneCompletionHook()

        result = asyncio.run(hook.execute(ctx))

        assert result.metadata["status"] == "applied"
        assert state.quests.get_milestone_state("ms_reward") == "COMPLETED"
        assert state.player.gold == 40
        assert state.player.xp == 120
        assert state.player.get_item_count("healing_herb") == 2

    def test_single_condition_not_met_does_not_complete(self) -> None:
        """Flag absent → milestone stays ACTIVE."""
        state = _make_quest_state({"ms_first": {"state": "ACTIVE"}})
        # Flag NOT set

        world = _make_quest_world_with_milestones({
            "ms_first": {
                "id": "ms_first",
                "title": "Test milestone",
                "chapter_id": "ch1",
                "success_conditions": [
                    {"type": "flag_set", "params": {"key": "talked_to_guild_girl"}}
                ],
            }
        })
        ctx = _make_settlement_context(state, world)
        hook = MilestoneCompletionHook()

        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["status"] == "noop"
        assert result.metadata["completed"] == []
        assert state.quests.get_milestone_state("ms_first") == "ACTIVE"

    def test_multiple_conditions_all_met_completes(self) -> None:
        """All conditions satisfied → COMPLETED."""
        state = _make_quest_state({"ms_multi": {"state": "ACTIVE"}})
        # Both conditions met
        state.flags.set("talked_to_guild_girl", True)
        state.flags.set("talked_to_goblin_slayer", True)

        world = _make_quest_world_with_milestones({
            "ms_multi": {
                "id": "ms_multi",
                "title": "Multi-condition milestone",
                "chapter_id": "ch1",
                "success_conditions": [
                    {"type": "flag_set", "params": {"key": "talked_to_guild_girl"}},
                    {"type": "flag_set", "params": {"key": "talked_to_goblin_slayer"}},
                ],
            }
        })
        ctx = _make_settlement_context(state, world)
        hook = MilestoneCompletionHook()

        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["status"] == "applied"
        assert "ms_multi" in result.metadata["completed"]

    def test_multiple_conditions_partial_does_not_complete(self) -> None:
        """Only one of two conditions met → still ACTIVE."""
        state = _make_quest_state({"ms_multi": {"state": "ACTIVE"}})
        state.flags.set("talked_to_guild_girl", True)
        # talked_to_goblin_slayer NOT set

        world = _make_quest_world_with_milestones({
            "ms_multi": {
                "id": "ms_multi",
                "title": "Multi-condition milestone",
                "chapter_id": "ch1",
                "success_conditions": [
                    {"type": "flag_set", "params": {"key": "talked_to_guild_girl"}},
                    {"type": "flag_set", "params": {"key": "talked_to_goblin_slayer"}},
                ],
            }
        })
        ctx = _make_settlement_context(state, world)
        hook = MilestoneCompletionHook()

        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["status"] == "noop"
        assert state.quests.get_milestone_state("ms_multi") == "ACTIVE"

    def test_no_conditions_on_template_skipped(self) -> None:
        """Milestones without success_conditions are not auto-completed."""
        state = _make_quest_state({"ms_no_cond": {"state": "ACTIVE"}})

        world = _make_quest_world_with_milestones({
            "ms_no_cond": {
                "id": "ms_no_cond",
                "title": "No conditions",
                "chapter_id": "ch1",
                # no success_conditions
            }
        })
        ctx = _make_settlement_context(state, world)
        hook = MilestoneCompletionHook()

        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["status"] == "noop"
        assert state.quests.get_milestone_state("ms_no_cond") == "ACTIVE"

    def test_completed_milestone_not_reprocessed(self) -> None:
        """Already COMPLETED milestones are ignored by the hook."""
        state = _make_quest_state({"ms_done": {"state": "COMPLETED"}})
        state.flags.set("some_flag", True)

        world = _make_quest_world_with_milestones({
            "ms_done": {
                "id": "ms_done",
                "title": "Already done",
                "chapter_id": "ch1",
                "success_conditions": [
                    {"type": "flag_set", "params": {"key": "some_flag"}}
                ],
            }
        })
        ctx = _make_settlement_context(state, world)
        hook = MilestoneCompletionHook()

        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["status"] == "noop"
        assert result.metadata["completed"] == []


class TestMilestoneCompletionAndUnlockCascade:
    """Test that MilestoneCompletionHook (54) + MilestoneUnlockHook (55) work in sequence:
    completing ms_parent → unlocking ms_child in the same settlement."""

    def test_completion_then_unlock_cascade(self) -> None:
        # ms_parent: ACTIVE, condition met
        # ms_child: LOCKED, prerequisite = ms_parent
        state = _make_quest_state({
            "ms_parent": {"state": "ACTIVE"},
            "ms_child": {"state": "LOCKED"},
        })
        state.flags.set("talked_to_guild_girl", True)

        world = _make_quest_world_with_milestones({
            "ms_parent": {
                "id": "ms_parent",
                "title": "Parent milestone",
                "chapter_id": "ch1",
                "next_milestones": ["ms_child"],
                "success_conditions": [
                    {"type": "flag_set", "params": {"key": "talked_to_guild_girl"}}
                ],
            },
            "ms_child": {
                "id": "ms_child",
                "title": "Child milestone",
                "chapter_id": "ch1",
                "prerequisites": ["ms_parent"],
            }
        })
        ctx = _make_settlement_context(state, world)

        completion_hook = MilestoneCompletionHook()
        unlock_hook = MilestoneUnlockHook()

        def _run():
            async def _body():
                r1 = await completion_hook.execute(ctx)
                r2 = await unlock_hook.execute(ctx)
                return r1, r2
            return asyncio.run(_body())

        r_complete, r_unlock = _run()

        # Completion hook completed ms_parent
        assert "ms_parent" in r_complete.metadata["completed"]
        assert state.quests.get_milestone_state("ms_parent") == "COMPLETED"

        # Unlock hook then unlocked ms_child
        assert "ms_child" in r_unlock.metadata["unlocked"]
        assert state.quests.get_milestone_state("ms_child") == "AVAILABLE"

    def test_hook_priority_ordering(self) -> None:
        """MilestoneCompletionHook (54) must sort before MilestoneUnlockHook (55)."""
        completion_hook = MilestoneCompletionHook()
        unlock_hook = MilestoneUnlockHook()
        assert completion_hook.priority < unlock_hook.priority
