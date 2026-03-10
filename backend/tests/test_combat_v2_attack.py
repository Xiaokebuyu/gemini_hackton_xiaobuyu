"""Tests for CombatHandler v2 attack and defend commands (Phase 4).

Covers: combat_attack, combat_defend.
Tests build v2 payloads directly (no start_combat flow needed).
All tests are synchronous.
"""

from __future__ import annotations

import random
import unittest.mock as mock
from typing import Any

from app.game_core.rules import Command
from app.game_core.rules.handlers import CombatHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice


# ---------------------------------------------------------------------------
# Helpers (independent from test_combat_v2_turn.py — no cross-test imports)
# ---------------------------------------------------------------------------

_DEFAULT_GRID = {
    "width": 6,
    "height": 6,
    "terrain": [
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
    ],
}

_GRID_WITH_FOREST_AT_3_0 = {
    "width": 6,
    "height": 6,
    "terrain": [
        "GGGFGG",  # row 0: col 3 = Forest (ac_bonus=2)
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
    ],
}

_GRID_WITH_HILL_AT_0_0 = {
    "width": 6,
    "height": 6,
    "terrain": [
        "HGGGGG",  # row 0: col 0 = Hill (range_bonus=1)
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
    ],
}

# Wall at col=2, row=0 — blocks LoS between (0,0) and (4,0)
_GRID_WITH_WALL_COL2 = {
    "width": 6,
    "height": 6,
    "terrain": [
        "GGBGGG",  # row 0: col 2 = Wall (blocks_los=True)
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
    ],
}


def _unit(
    unit_id: str,
    side: str,
    position: list[int],
    *,
    speed: int = 6,
    hp: int = 20,
    max_hp: int = 20,
    ac: int = 12,
    alive: bool = True,
    fled: bool = False,
    action_used: bool = False,
    move_used: bool = False,
    disengaged: bool = False,
    dashed: bool = False,
    defending: bool = False,
    reaction_used: bool = False,
    surprised: bool = False,
    attacks: list[dict[str, Any]] | None = None,
    monster_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal v2 unit dict."""
    u: dict[str, Any] = {
        "unit_id": unit_id,
        "side": side,
        "source": "monster",
        "position": list(position),
        "speed": speed,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "alive": alive,
        "fled": fled,
        "action_used": action_used,
        "move_used": move_used,
        "disengaged": disengaged,
        "dashed": dashed,
        "defending": defending,
        "reaction_used": reaction_used,
        "surprised": surprised,
        "attacks": attacks or [
            {"name": "Claw", "hit_bonus": 3, "damage_dice": "1d6",
             "damage_type": "slashing", "range": 1}
        ],
        "stats": {"dex": 10, "wis": 10},
    }
    if monster_id is not None:
        u["monster_id"] = monster_id
    return u


def _make_v2_payload(
    units: list[dict[str, Any]],
    turn_order: list[str],
    current_turn_index: int = 0,
    combat_round: int = 1,
    area_id: str = "dungeon",
    sub_area_id: str = "room1",
    grid: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a v2 hostile payload dict."""
    current_unit_id = turn_order[current_turn_index] if turn_order else ""
    return {
        "area_id": area_id,
        "version": 2,
        "combat_active": True,
        "cleared": False,
        "blocking": True,
        "status": "engaged",
        "units": [dict(u) for u in units],
        "grid": grid or _DEFAULT_GRID,
        "turn_order": list(turn_order),
        "current_turn_index": current_turn_index,
        "current_unit_id": current_unit_id,
        "combat_round": combat_round,
        "initiative_rolls": {},
    }


def _make_state(
    payload: dict[str, Any],
    sub_area_id: str = "room1",
    area_id: str = "dungeon",
    player_hp: int = 20,
) -> StateContainer:
    """Build a StateContainer with the given v2 combat payload registered."""
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "character_id": "hero",
        "hp": player_hp,
        "max_hp": 20,
        "ac": 14,
        "current_area": area_id,
        "xp": 0,
        "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
    })
    state.register(player)

    areas = AreaSlice()
    areas.restore({
        "areas": {
            area_id: {
                "danger_level": 1.0,
                "npc_locations": {},
                "hostile_tracking": {},
            }
        }
    })
    areas.register_hostile(sub_area_id, payload)
    state.register(areas)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    return state


def _make_cmd(cmd_type: str, params: dict[str, Any]) -> Command:
    """Build a Command with the given type and params."""
    return Command(type=cmd_type, params=params, source="player")


def _apply(cmd: Command, state: StateContainer) -> Any:
    """Run a command through CombatHandler.compute() directly."""
    handler = CombatHandler()
    return handler.compute(cmd, state, None)


def _get_updated_payload(result: Any, sub_area_id: str = "room1") -> dict[str, Any]:
    """Extract the updated payload from an ExecuteResult delta."""
    assert result.executed, f"command failed: {result.errors}"
    assert result.delta is not None
    for change in result.delta.changes:
        if "hostile_tracking" in change.path:
            return change.value
    raise AssertionError("No hostile_tracking change found in delta")


def _get_unit(payload: dict[str, Any], unit_id: str) -> dict[str, Any]:
    """Find a unit in payload by unit_id."""
    for u in payload["units"]:
        if u["unit_id"] == unit_id:
            return u
    raise AssertionError(f"Unit {unit_id!r} not found in payload")


def _seq_rolls(*values: int):
    """Return a randint replacement that yields successive values.

    Both combat.py (d20) and handler_utils.py (damage dice) import the same
    ``random`` module object, so one patch on ``random.randint`` intercepts all
    dice calls in the correct order.
    """
    it = iter(values)

    def _roll(a: int, b: int) -> int:
        return next(it, values[-1])  # repeat last if exhausted

    return _roll


# ---------------------------------------------------------------------------
# combat_attack tests
# ---------------------------------------------------------------------------

def test_combat_attack_melee_hit() -> None:
    """Melee hit applies damage and sets action_used=True."""
    attacker = _unit("ally_1", "ally", [0, 0])
    target = _unit("goblin", "enemy", [1, 0], hp=10, ac=12)
    payload = _make_v2_payload([attacker, target], ["ally_1"])
    state = _make_state(payload)

    # Rolls in order: d20=15 → 15+3=18 > AC12 → hit; then 1d6 damage die=4
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    with mock.patch.object(random, "randint", _seq_rolls(15, 4)):
        result = _apply(cmd, state)

    assert result.executed, f"Expected success, got errors: {result.errors}"
    assert result.metadata["hit"] is True
    assert result.metadata["damage"] == 4
    assert result.metadata["attacker_id"] == "ally_1"
    assert result.metadata["target_id"] == "goblin"

    updated = _get_updated_payload(result)
    ally = _get_unit(updated, "ally_1")
    assert ally["action_used"] is True


def test_combat_attack_melee_miss() -> None:
    """When attack roll+hit_bonus is below target AC, no damage is dealt."""
    attacker = _unit("ally_1", "ally", [0, 0],
                     attacks=[{"name": "Punch", "hit_bonus": 0,
                               "damage_dice": "1d4", "damage_type": "bludgeoning", "range": 1}])
    target = _unit("tank", "enemy", [1, 0], hp=20, ac=15)
    payload = _make_v2_payload([attacker, target], ["ally_1"])
    state = _make_state(payload)

    # d20=2, total=2+0=2 < AC15 → miss (not auto-miss since 2≠1, but below AC)
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "tank"})
    with mock.patch.object(random, "randint", _seq_rolls(2)):
        result = _apply(cmd, state)

    assert result.executed
    assert result.metadata["hit"] is False
    assert result.metadata["damage"] == 0


def test_combat_attack_melee_out_of_range() -> None:
    """Target beyond weapon range returns error."""
    attacker = _unit("ally_1", "ally", [0, 0],
                     attacks=[{"name": "Claw", "hit_bonus": 3,
                               "damage_dice": "1d6", "damage_type": "slashing", "range": 1}])
    target = _unit("goblin", "enemy", [3, 0])  # distance=3, range=1
    payload = _make_v2_payload([attacker, target], ["ally_1"])
    state = _make_state(payload)

    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    result = _apply(cmd, state)

    assert not result.executed
    assert "out of range" in (result.errors[0] if result.errors else "")


def test_combat_attack_ranged_hit() -> None:
    """Ranged attack (range=4) at distance=3 succeeds."""
    attacker = _unit("archer", "ally", [0, 0],
                     attacks=[{"name": "Arrow", "hit_bonus": 5,
                               "damage_dice": "1d8", "damage_type": "piercing", "range": 4}])
    target = _unit("goblin", "enemy", [3, 0])  # distance=3, range=4 → in range
    payload = _make_v2_payload([attacker, target], ["archer"])
    state = _make_state(payload)

    # d20=15 → 15+5=20 > AC12 → hit; 1d8 damage=5
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    with mock.patch.object(random, "randint", _seq_rolls(15, 5)):
        result = _apply(cmd, state)

    assert result.executed
    assert result.metadata["hit"] is True
    assert result.metadata["damage"] == 5


def test_combat_attack_ranged_blocked_los() -> None:
    """Ranged attack blocked by wall returns 'no line of sight' error."""
    # attacker at (0,0), target at (4,0), wall at (2,0)
    attacker = _unit("archer", "ally", [0, 0],
                     attacks=[{"name": "Arrow", "hit_bonus": 5,
                               "damage_dice": "1d8", "damage_type": "piercing", "range": 6}])
    target = _unit("goblin", "enemy", [4, 0])
    payload = _make_v2_payload([attacker, target], ["archer"],
                               grid=_GRID_WITH_WALL_COL2)
    state = _make_state(payload)

    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    result = _apply(cmd, state)

    assert not result.executed
    assert "line of sight" in (result.errors[0] if result.errors else "")


def test_combat_attack_terrain_ac_bonus() -> None:
    """Target in forest terrain gets +2 AC bonus applied to effective AC."""
    # Target at (3,0) — Forest in _GRID_WITH_FOREST_AT_3_0 → terrain ac_bonus=2
    # Use AC=10 so base AC=10, forest gives +2 → effective AC=12
    # d20=11: total=11+0=11 < effective_ac=12 → miss (would hit AC10 without forest)
    attacker = _unit("ally_1", "ally", [2, 0],
                     attacks=[{"name": "Claw", "hit_bonus": 0,
                               "damage_dice": "1d4", "damage_type": "slashing", "range": 1}])
    target = _unit("goblin", "enemy", [3, 0], ac=10)
    payload = _make_v2_payload([attacker, target], ["ally_1"],
                               grid=_GRID_WITH_FOREST_AT_3_0)
    state = _make_state(payload)

    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    with mock.patch.object(random, "randint", _seq_rolls(11)):
        result = _apply(cmd, state)

    assert result.executed
    assert result.metadata["terrain_ac_bonus"] == 2
    assert result.metadata["target_ac"] == 12  # base_ac=10 + forest=2
    assert result.metadata["hit"] is False  # 11 < 12


def test_combat_attack_range_bonus_from_hill() -> None:
    """Attacker on hill terrain gets +1 effective range bonus."""
    # Attacker at (0,0) which is Hill → range_bonus=1
    # weapon range=1, effective_range=2
    # Target at (2,0) — distance=2, out of range normally (1), but hill gives +1 → reachable
    attacker = _unit("archer", "ally", [0, 0],
                     attacks=[{"name": "Stab", "hit_bonus": 5,
                               "damage_dice": "1d6", "damage_type": "piercing", "range": 1}])
    target = _unit("goblin", "enemy", [2, 0])
    payload = _make_v2_payload([attacker, target], ["archer"],
                               grid=_GRID_WITH_HILL_AT_0_0)
    state = _make_state(payload)

    # d20=15 → 15+5=20 > AC12 → hit; 1d6 damage=4
    # Note: weapon_range=1 so NOT considered ranged — no LoS check
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    with mock.patch.object(random, "randint", _seq_rolls(15, 4)):
        result = _apply(cmd, state)

    assert result.executed
    assert result.metadata["hit"] is True


def test_combat_attack_defending_target_ac_bonus() -> None:
    """Target with defending=True gets +2 AC bonus."""
    attacker = _unit("ally_1", "ally", [0, 0],
                     attacks=[{"name": "Claw", "hit_bonus": 0,
                               "damage_dice": "1d4", "damage_type": "slashing", "range": 1}])
    target = _unit("goblin", "enemy", [1, 0], ac=10, defending=True)
    payload = _make_v2_payload([attacker, target], ["ally_1"])
    state = _make_state(payload)

    # d20=11: total=11+0=11, effective_ac=10+2(defending)=12 → miss
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    with mock.patch.object(random, "randint", _seq_rolls(11)):
        result = _apply(cmd, state)

    assert result.executed
    assert result.metadata["target_ac"] == 12  # base=10 + defending=2
    assert result.metadata["hit"] is False


def test_combat_attack_action_already_used_fails() -> None:
    """Validation fails when current unit's action_used=True."""
    attacker = _unit("ally_1", "ally", [0, 0], action_used=True)
    target = _unit("goblin", "enemy", [1, 0])
    payload = _make_v2_payload([attacker, target], ["ally_1"])
    state = _make_state(payload)

    handler = CombatHandler()
    from app.game_core.content import WorldInstance
    world = WorldInstance("test_world")
    validation = handler.validate(
        _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"}),
        state,
        world,
    )
    assert not validation.ok
    assert "already used action" in validation.reason


def test_combat_attack_friendly_fire_fails() -> None:
    """Attacking a unit on the same side returns error (not a validation error, compute error)."""
    attacker = _unit("ally_1", "ally", [0, 0])
    friendly = _unit("ally_2", "ally", [1, 0])
    payload = _make_v2_payload([attacker, friendly], ["ally_1"])
    state = _make_state(payload)

    # d20=15 → passes hit check, but friendly fire check fires first
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "ally_2"})
    with mock.patch.object(random, "randint", _seq_rolls(15)):
        result = _apply(cmd, state)

    assert not result.executed
    assert "friendly" in (result.errors[0] if result.errors else "")


def test_combat_attack_kills_last_enemy_clears_combat() -> None:
    """Killing the last enemy triggers combat_cleared=True."""
    attacker = _unit("ally_1", "ally", [0, 0],
                     attacks=[{"name": "Claw", "hit_bonus": 10,
                               "damage_dice": "1d4", "damage_type": "slashing", "range": 1}])
    target = _unit("goblin", "enemy", [1, 0], hp=1, ac=1)  # 1 HP, AC=1 → trivially killed
    payload = _make_v2_payload([attacker, target], ["ally_1"])
    state = _make_state(payload)

    # d20=10 → total=20 (hit vs AC=1); 1d4 damage=3 → kills 1 HP target
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    with mock.patch.object(random, "randint", _seq_rolls(10, 3)):
        result = _apply(cmd, state)

    assert result.executed
    assert result.metadata["hit"] is True
    assert result.metadata["combat_cleared"] is True

    updated = _get_updated_payload(result)
    assert updated["combat_active"] is False
    assert updated["cleared"] is True


def test_combat_attack_player_hp_sync() -> None:
    """When player unit is attacked and hit, PlayerSlice HP is synced via StateChange."""
    # unit_id="player" on "enemy" side (being attacked by "goblin" ally)
    player_unit = _unit("player", "enemy", [1, 0], hp=20, ac=5)
    attacker = _unit("goblin", "ally", [0, 0],
                     attacks=[{"name": "Claw", "hit_bonus": 10,
                               "damage_dice": "1d6", "damage_type": "slashing", "range": 1}])
    payload = _make_v2_payload([attacker, player_unit], ["goblin"])
    state = _make_state(payload, player_hp=20)

    # d20=15 → 15+10=25 > AC5 → hit; 1d6 damage=5
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "player"})
    with mock.patch.object(random, "randint", _seq_rolls(15, 5)):
        result = _apply(cmd, state)

    assert result.executed
    assert result.metadata["hit"] is True
    # Check that a PlayerSlice HP sync change was added
    player_hp_changes = [
        ch for ch in result.delta.changes
        if ch.slice == "player" and ch.path == "hp"
    ]
    assert len(player_hp_changes) == 1
    assert player_hp_changes[0].value == 15  # 20 - 5 = 15


def test_combat_attack_critical_double_damage_dice() -> None:
    """d20=20 (critical) results in double damage dice rolls."""
    attacker = _unit("ally_1", "ally", [0, 0],
                     attacks=[{"name": "Claw", "hit_bonus": 3,
                               "damage_dice": "1d6", "damage_type": "slashing", "range": 1}])
    target = _unit("goblin", "enemy", [1, 0], hp=100, ac=12)
    payload = _make_v2_payload([attacker, target], ["ally_1"])
    state = _make_state(payload)

    # Roll order: d20=20 (critical), then two 1d6 dice each returning 4 → total=8
    cmd = _make_cmd("combat_attack", {"sub_area_id": "room1", "target": "goblin"})
    with mock.patch.object(random, "randint", _seq_rolls(20, 4, 4)):
        result = _apply(cmd, state)

    assert result.executed
    assert result.metadata["critical"] is True
    # Critical: 2 × 1d6 both returning 4 → total damage = 8
    assert result.metadata["damage"] == 8


# ---------------------------------------------------------------------------
# combat_defend tests
# ---------------------------------------------------------------------------

def test_combat_defend_sets_flags() -> None:
    """combat_defend sets defending=True and action_used=True."""
    ally = _unit("ally_1", "ally", [0, 0])
    enemy = _unit("goblin", "enemy", [3, 3])
    payload = _make_v2_payload([ally, enemy], ["ally_1"])
    state = _make_state(payload)

    cmd = _make_cmd("combat_defend", {"sub_area_id": "room1"})
    result = _apply(cmd, state)

    assert result.executed, f"Expected success, got errors: {result.errors}"
    assert result.metadata["unit_id"] == "ally_1"
    assert result.metadata["status"] == "defending"

    updated = _get_updated_payload(result)
    unit = _get_unit(updated, "ally_1")
    assert unit["defending"] is True
    assert unit["action_used"] is True
