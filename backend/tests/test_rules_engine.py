"""Tests for RulesEngine core behaviour."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import ClassRegistry
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import GrowthHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PlayerSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    classes = ClassRegistry()
    classes.load(
        {
            "classes": {
                "fighter": {
                    "id": "fighter",
                    "hit_die": 10,
                    "hp_per_level": 6,
                    "subclass_level": 6,
                    "starting_gold": 10,
                    "level_features": {"1": ["Second Wind"], "2": ["Action Surge"]},
                }
            },
            "subclasses": {},
            "races": {},
            "backgrounds": {},
            "xp_curve": [0, 1000, 2000, 3000],
        }
    )
    world.register(classes)
    return world


def _make_state(*, level: int = 1, xp: int = 0) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "character_id": "pc_1",
            "character_class": "fighter",
            "level": level,
            "xp": xp,
            "hp": 12,
            "max_hp": 12,
            "proficiency_bonus": 2,
            "class_features": ["Second Wind"],
            "stats": {
                "str": 10, "dex": 10, "con": 10,
                "int": 10, "wis": 10, "cha": 10,
            },
        }
    )
    state.register(player)
    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(GrowthHandler())
    return engine


# ---------------------------------------------------------------------------
# batch_execute: delta accumulation
# ---------------------------------------------------------------------------

def test_batch_execute_accumulates_delta() -> None:
    """batch_execute must apply each delta before running the next command.

    Scenario: add_xp gives the player enough XP to reach level 2; the
    immediately following level_up must see that updated XP in state.
    Without accumulation level_up would see XP=0 and return an error.
    """
    engine = _make_engine()
    world = _make_world()
    state = _make_state(level=1, xp=0)

    commands = [
        Command(type="add_xp", params={"character_id": "pc_1", "amount": 1000}),
        Command(type="level_up", params={"character_id": "pc_1", "target_level": 2}),
    ]
    results = engine.batch_execute(commands, state, world)

    assert len(results) == 2
    add_xp_result, level_up_result = results
    assert add_xp_result.executed, f"add_xp failed: {add_xp_result.errors}"
    assert level_up_result.executed, (
        "level_up should succeed after add_xp applied its delta; "
        f"errors: {level_up_result.errors}"
    )
    # State should reflect both changes
    assert state.player.xp >= 1000
    assert state.player.level == 2


def test_batch_execute_second_command_fails_without_prior_delta() -> None:
    """Verify that level_up genuinely requires the XP set by add_xp.

    Running level_up *alone* with XP=0 should fail, confirming the
    accumulation test above is a meaningful correctness check.
    """
    engine = _make_engine()
    world = _make_world()
    state = _make_state(level=1, xp=0)

    result = engine.execute(
        Command(type="level_up", params={"character_id": "pc_1", "target_level": 2}),
        state,
        world,
    )
    assert not result.executed, "level_up should fail when XP is insufficient"


def test_batch_execute_failed_command_does_not_accumulate() -> None:
    """A failed command must NOT apply its delta (which is None) to state."""
    engine = _make_engine()
    world = _make_world()
    state = _make_state(level=1, xp=0)

    commands = [
        # Invalid: amount=0 fails validation
        Command(type="add_xp", params={"character_id": "pc_1", "amount": 0}),
        # This should still execute against original state (XP=0)
        Command(type="add_xp", params={"character_id": "pc_1", "amount": 500}),
    ]
    results = engine.batch_execute(commands, state, world)

    assert not results[0].executed, "first add_xp (amount=0) should fail"
    assert results[1].executed, "second add_xp should still execute and succeed"
    assert state.player.xp == 500


# ===========================================================================
# NavigationHandler: connection validation + travel_slots (F-D)
# ===========================================================================

from app.game_core.content.registries.maps import MapRegistry
from app.game_core.rules.handlers.navigation import NavigationHandler


def _make_nav_world() -> WorldInstance:
    world = WorldInstance("nav_test")
    maps = MapRegistry()
    maps.load({
        "town": {
            "id": "town",
            "connections": [
                {"target_map_id": "forest", "travel_time": "30分钟"},
                {"target_map_id": "castle", "travel_time": "2小时"},
            ],
        },
        "forest": {"id": "forest"},
        "castle": {"id": "castle"},
        "cave": {"id": "cave"},  # no connection from town
    })
    world.register(maps)
    return world


def _make_nav_state(current_area: str = "town") -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"character_id": "pc_1", "current_area": current_area})
    state.register(player)
    return state


def test_navigation_validate_rejects_disconnected_area() -> None:
    """validate must fail if there is no connection from current area to target."""
    handler = NavigationHandler()
    world = _make_nav_world()
    state = _make_nav_state("town")

    result = handler.validate(
        Command(type="move_area", params={"area_id": "cave"}),
        state,
        world,
    )
    assert not result.ok
    assert "no connection" in result.reason


def test_navigation_validate_accepts_connected_area() -> None:
    """validate must succeed when a connection exists from current to target."""
    handler = NavigationHandler()
    world = _make_nav_world()
    state = _make_nav_state("town")

    result = handler.validate(
        Command(type="move_area", params={"area_id": "forest"}),
        state,
        world,
    )
    assert result.ok


def test_navigation_execute_uses_travel_slots() -> None:
    """execute must use Connection.travel_slots (not hardcode 1.0) as time_cost."""
    handler = NavigationHandler()
    world = _make_nav_world()

    # forest: 30分钟 → 1 slot
    state = _make_nav_state("town")
    result_forest = handler.compute(
        Command(type="move_area", params={"area_id": "forest"}),
        state,
        world,
    )
    assert result_forest.executed
    assert result_forest.time_cost == 1.0  # ceil(30/60)=1

    # castle: 2小时 → 2 slots
    state2 = _make_nav_state("town")
    result_castle = handler.compute(
        Command(type="move_area", params={"area_id": "castle"}),
        state2,
        world,
    )
    assert result_castle.executed
    assert result_castle.time_cost == 2.0  # ceil(120/60)=2


def test_navigation_no_connection_check_when_no_current_area() -> None:
    """If player has no current_area, move_area should skip the connection check."""
    handler = NavigationHandler()
    world = _make_nav_world()
    state = _make_nav_state("")  # empty current_area

    result = handler.validate(
        Command(type="move_area", params={"area_id": "cave"}),
        state,
        world,
    )
    assert result.ok  # no current_area → no connection check, cave is valid target


# ===========================================================================
# GrowthHandler: class_resources initialization on level_up (F-E)
# ===========================================================================

def _make_world_with_resources() -> WorldInstance:
    world = WorldInstance("growth_test")
    classes = ClassRegistry()
    classes.load(
        {
            "classes": {
                "fighter": {
                    "id": "fighter",
                    "hit_die": 10,
                    "hp_per_level": 6,
                    "level_features": {
                        "1": ["Second Wind"],
                        "2": ["Action Surge"],
                    },
                    "class_resources_schema": {
                        "second_wind": {
                            "max_at_level": {"1": 1},
                            "recovery": "short_rest",
                        },
                        "action_surge": {
                            "max_at_level": {"2": 1, "17": 2},
                            "recovery": "short_rest",
                        },
                    },
                }
            },
            "subclasses": {},
            "races": {},
            "backgrounds": {},
            "xp_curve": [0, 1000, 2000, 3000, 5000],
        }
    )
    world.register(classes)
    return world


def _make_state_with_resources(*, level: int = 1, xp: int = 0) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "character_id": "pc_1",
            "character_class": "fighter",
            "level": level,
            "xp": xp,
            "hp": 12,
            "max_hp": 12,
            "proficiency_bonus": 2,
            "class_features": ["Second Wind"],
            "stats": {
                "str": 10, "dex": 10, "con": 10,
                "int": 10, "wis": 10, "cha": 10,
            },
        }
    )
    state.register(player)
    return state


def test_growth_level_up_initializes_class_resources() -> None:
    """Level-up to level 2 must initialize action_surge resource (first unlock)."""
    engine = RulesEngine()
    engine.register(GrowthHandler())
    world = _make_world_with_resources()
    state = _make_state_with_resources(level=1, xp=1000)

    results = engine.batch_execute(
        [Command(type="level_up", params={"character_id": "pc_1", "target_level": 2})],
        state,
        world,
    )
    result = results[0]

    assert result.executed, f"level_up failed: {result.errors}"
    assert state.player.level == 2

    # action_surge unlocks at level 2 → should now be initialized
    action_surge = state.player.get_resource("action_surge")
    assert action_surge is not None
    assert action_surge["max"] == 1
    assert action_surge["current"] == 1
    assert action_surge["recovery"] == "short_rest"


def test_growth_level_up_increments_resource_max() -> None:
    """Level-up to level 17 must increase action_surge max from 1 to 2."""
    engine = RulesEngine()
    engine.register(GrowthHandler())
    world = _make_world_with_resources()

    # Setup: fighter at level 16, action_surge already unlocked at max=1
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "character_id": "pc_1",
            "character_class": "fighter",
            "level": 16,
            "xp": 100000,  # enough for any level
            "hp": 100,
            "max_hp": 100,
            "proficiency_bonus": 5,
            "class_features": ["Second Wind", "Action Surge"],
            "class_resources": {
                "second_wind": {"current": 0, "max": 1, "recovery": "short_rest"},
                "action_surge": {"current": 1, "max": 1, "recovery": "short_rest"},
            },
            "stats": {
                "str": 10, "dex": 10, "con": 10,
                "int": 10, "wis": 10, "cha": 10,
            },
        }
    )
    state.register(player)

    results = engine.batch_execute(
        [Command(type="level_up", params={"character_id": "pc_1", "target_level": 17})],
        state,
        world,
    )
    result = results[0]

    assert result.executed, f"level_up failed: {result.errors}"
    assert state.player.level == 17

    action_surge = state.player.get_resource("action_surge")
    assert action_surge["max"] == 2
    assert action_surge["current"] == 2  # gained 1 from the max increase


def test_growth_level_up_no_resource_change_at_max() -> None:
    """Level-up at the same or lower max tier must NOT produce resource StateChanges."""
    engine = RulesEngine()
    engine.register(GrowthHandler())
    world = _make_world_with_resources()

    # Fighter at level 2, action_surge already at max=1
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "character_id": "pc_1",
            "character_class": "fighter",
            "level": 2,
            "xp": 2000,
            "hp": 18,
            "max_hp": 18,
            "proficiency_bonus": 2,
            "class_features": ["Second Wind", "Action Surge"],
            "class_resources": {
                # Both resources already initialized from level 1/2 level-up
                "second_wind": {"current": 1, "max": 1, "recovery": "short_rest"},
                "action_surge": {"current": 1, "max": 1, "recovery": "short_rest"},
            },
            "stats": {
                "str": 10, "dex": 10, "con": 10,
                "int": 10, "wis": 10, "cha": 10,
            },
        }
    )
    state.register(player)

    results = engine.batch_execute(
        [Command(type="level_up", params={"character_id": "pc_1", "target_level": 3})],
        state,
        world,
    )
    result = results[0]

    assert result.executed
    # action_surge max is still 1 at level 3 (next tier is level 17)
    action_surge = state.player.get_resource("action_surge")
    assert action_surge["max"] == 1  # unchanged
    assert result.metadata.get("updated_resources") == []

