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


# ===========================================================================
# CombatHandler: 怪物反击 + 逃跑 + XP 分发（F-C）
# ===========================================================================

from app.game_core.content.registries.monsters import MonsterRegistry
from app.game_core.rules.handlers.combat import CombatHandler
from app.game_core.state.slices import PlayerSlice
from app.game_core.state.slices.area import AreaSlice


def _make_combat_world(
    *,
    ai_personality: str = "aggressive",
    flee_threshold: float = 0.0,
    xp_reward: int = 25,
    damage_dice: str = "1d4",
    hit_bonus: int = 99,   # 默认设为极高，使怪物必定命中，方便测试
    hp: int = 10,
    ac: int = 8,
) -> "WorldInstance":
    from app.game_core.content import WorldInstance
    world = WorldInstance("combat_test")
    monsters = MonsterRegistry()
    monsters.load({
        "goblin": {
            "id": "goblin",
            "name": "Goblin",
            "hp": hp,
            "ac": ac,
            "ai_personality": ai_personality,
            "flee_threshold": flee_threshold,
            "xp_reward": xp_reward,
            "attacks": [{"name": "short_sword", "damage_dice": damage_dice, "hit_bonus": hit_bonus}],
        }
    })
    world.register(monsters)
    return world


def _make_combat_state(
    *,
    player_hp: int = 20,
    player_max_hp: int = 20,
    monster_hp: int = 10,
    monster_max_hp: int = 10,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({
        "character_id": "pc_1",
        "character_class": "fighter",
        "level": 1,
        "xp": 0,
        "hp": player_hp,
        "max_hp": player_max_hp,
        "proficiency_bonus": 2,
        "current_area": "dungeon",
        "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
    })
    state.register(player)
    area = AreaSlice()
    # AreaSlice.restore() 期望 {"areas": {area_id: {hostile_tracking: {...}, ...}}}
    area.restore({
        "areas": {
            "dungeon": {
                "hostile_tracking": {
                    "_combat_dungeon_1": {
                        "area_id": "dungeon",
                        "sub_area_id": "_combat_dungeon_1",
                        "blocking": True,
                        "combat_active": True,
                        "combat_round": 1,
                        "surprise_state": "none",
                        "combat_started_at_tick": None,
                        "monster_ids": ["goblin"],
                        "participants": [
                            {
                                "monster_id": "goblin",
                                "name": "Goblin",
                                "hp": monster_hp,
                                "max_hp": monster_max_hp,
                                "ac": 8,
                                "alive": True,
                            }
                        ],
                        "status": "active",
                        "cleared": False,
                        "player_flags": {
                            "defending": False,
                            "disengaged": False,
                            "dashed": False,
                        },
                    }
                }
            }
        }
    })
    state.register(area)
    return state


def _attack_goblin(
    state: StateContainer,
    world: "WorldInstance",
    *,
    always_hit: bool = True,
) -> "ExecuteResult":
    """Execute attack command. With always_hit=True the test uses a fixed-outcome stub."""
    from app.game_core.rules import RulesEngine, Command
    engine = RulesEngine()
    engine.register(CombatHandler())

    if always_hit:
        # Patch resolve_roll so player always hits (roll=20)
        import app.game_core.rules.handlers.combat as combat_mod
        original_resolve_roll = combat_mod.resolve_roll

        def _always_hit_resolve_roll(*args, **kwargs):
            return 20, [20], "1d20"

        combat_mod.resolve_roll = _always_hit_resolve_roll
        try:
            results = engine.batch_execute(
                [Command(type="attack", params={"target": "goblin"})],
                state, world,
            )
        finally:
            combat_mod.resolve_roll = original_resolve_roll
    else:
        results = engine.batch_execute(
            [Command(type="attack", params={"target": "goblin"})],
            state, world,
        )
    return results[0]


def test_monster_counterattacks_after_player_attack() -> None:
    """攻击命中后，存活怪物应在 monster_responses 中出现一次行动记录。"""
    world = _make_combat_world(hp=30, hit_bonus=99)  # hit_bonus=99 确保怪物必中
    state = _make_combat_state(monster_hp=30, monster_max_hp=30)

    result = _attack_goblin(state, world)

    assert result.executed, f"attack failed: {result.errors}"
    responses = result.metadata.get("monster_responses", [])
    assert len(responses) == 1
    assert responses[0]["monster_id"] == "goblin"
    assert responses[0]["action"] == "attack"


def test_player_takes_damage_from_monster_counterattack() -> None:
    """怪物命中玩家时，玩家 HP 应减少。"""
    world = _make_combat_world(hp=30, hit_bonus=99, damage_dice="1d4")
    state = _make_combat_state(player_hp=20, player_max_hp=20, monster_hp=30, monster_max_hp=30)
    initial_player_hp = 20

    result = _attack_goblin(state, world)

    assert result.executed
    assert state.player.hp < initial_player_hp, (
        f"Player HP should decrease after monster hit; was {initial_player_hp}, now {state.player.hp}"
    )


def test_dead_monsters_do_not_counterattack() -> None:
    """击杀最后一只怪物后，monster_responses 列表应为空（战斗已结束）。"""
    world = _make_combat_world(hp=1, hit_bonus=99)   # 1 HP goblin → 必死
    state = _make_combat_state(monster_hp=1, monster_max_hp=1)

    result = _attack_goblin(state, world)

    assert result.executed
    assert result.metadata.get("combat_cleared") is True
    # 怪物已死亡，不应有反击
    responses = result.metadata.get("monster_responses", [])
    assert responses == [], f"Dead monsters should not counterattack, got: {responses}"


def test_xp_awarded_when_combat_cleared() -> None:
    """击杀最后怪物时，应自动分发 xp_reward。"""
    world = _make_combat_world(hp=1, xp_reward=25)
    state = _make_combat_state(monster_hp=1, monster_max_hp=1)
    assert state.player.xp == 0

    result = _attack_goblin(state, world)

    assert result.executed
    assert result.metadata.get("combat_cleared") is True
    assert result.metadata.get("xp_awarded") == 25
    assert state.player.xp == 25


def test_monster_flees_when_hp_below_threshold() -> None:
    """flee_threshold=0.5 + cowardly 时，HP 低于 50% 的怪物应逃跑。"""
    world = _make_combat_world(
        hp=10,
        ai_personality="cowardly",
        flee_threshold=0.5,
        hit_bonus=99,
    )
    # 怪物 HP=3/10=30% < 50% 阈值 → 应逃跑
    state = _make_combat_state(monster_hp=3, monster_max_hp=10)

    # 不需要玩家实际命中，直接读初始 hostile state 就有低HP怪物
    # 但为了触发怪物回合，需要至少一次玩家行动
    # 玩家以 always_hit=False 攻击（结果不重要，怪物回合仍会执行）
    result = _attack_goblin(state, world, always_hit=False)

    assert result.executed
    responses = result.metadata.get("monster_responses", [])
    assert len(responses) == 1
    assert responses[0]["action"] == "flee"


def test_combat_cleared_no_monster_response() -> None:
    """combat_cleared=True 时（玩家一击必杀），不应再有怪物反击。"""
    world = _make_combat_world(hp=1, hit_bonus=99)
    state = _make_combat_state(monster_hp=1, monster_max_hp=1)

    result = _attack_goblin(state, world)

    assert result.executed
    assert result.metadata["combat_cleared"] is True
    responses = result.metadata.get("monster_responses", [])
    assert responses == []
