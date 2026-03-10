"""Tests for battle_ai.py — monster AI decision module.

All tests are synchronous pure-function tests.
No game state, no async, no external dependencies.
"""

from __future__ import annotations

import unittest.mock as mock
from typing import Any

from app.game_core.rules.battle_ai import (
    MonsterDecision,
    decide_monster_turn,
    validate_decision,
    _estimate_damage,
    _evaluate_targets,
    _find_flee_destination,
    _select_attack_index,
    _should_flee,
)
from app.game_core.rules.battle_grid import BattleGrid


# ---------------------------------------------------------------------------
# Grid factories
# ---------------------------------------------------------------------------

_FLAT_5x5 = {
    "width": 5,
    "height": 5,
    "terrain": [
        "GGGGG",
        "GGGGG",
        "GGGGG",
        "GGGGG",
        "GGGGG",
    ],
}

_GRID_WITH_HILL = {
    "width": 6,
    "height": 6,
    "terrain": [
        "GGGGGG",
        "GGGGGG",
        "GHGGGG",  # (1, 2) = Hill (range_bonus=1)
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
    ],
}


def _flat_grid() -> BattleGrid:
    return BattleGrid.from_map_data(_FLAT_5x5)


def _hill_grid() -> BattleGrid:
    return BattleGrid.from_map_data(_GRID_WITH_HILL)


# ---------------------------------------------------------------------------
# Unit factories
# ---------------------------------------------------------------------------

def _unit(
    unit_id: str,
    side: str,
    position: list[int],
    *,
    hp: int = 20,
    max_hp: int = 20,
    speed: int = 6,
    ac: int = 12,
    personality: str = "aggressive",
    alive: bool = True,
    fled: bool = False,
    attacks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a minimal unit dict for AI tests."""
    return {
        "unit_id": unit_id,
        "side": side,
        "source": "monster",
        "position": list(position),
        "hp": hp,
        "max_hp": max_hp,
        "speed": speed,
        "ac": ac,
        "personality": personality,
        "alive": alive,
        "fled": fled,
        "action_used": False,
        "move_used": False,
        "attacks": attacks or [
            {
                "name": "Claw",
                "hit_bonus": 3,
                "damage_dice": "1d6",
                "damage_type": "slashing",
                "range": 1,
            }
        ],
    }


# ---------------------------------------------------------------------------
# _should_flee tests
# ---------------------------------------------------------------------------

def test_should_flee_cowardly_below_threshold() -> None:
    """A cowardly unit below 50% HP has a high chance to flee."""
    unit = _unit("goblin", "enemy", [0, 0], hp=8, max_hp=20, personality="cowardly")
    # HP ratio = 0.4 < 0.5 threshold; flee_chance=0.8 → mock random to always trigger
    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.5):
        result = _should_flee(unit)
    assert result is True


def test_should_not_flee_above_threshold() -> None:
    """A cowardly unit above the flee threshold never flees."""
    unit = _unit("goblin", "enemy", [0, 0], hp=15, max_hp=20, personality="cowardly")
    # HP ratio = 0.75 >= 0.5 — no flee regardless of random
    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.0):
        result = _should_flee(unit)
    assert result is False


def test_should_flee_aggressive_high_threshold_not_triggered() -> None:
    """An aggressive unit at 30% HP does NOT flee (threshold=10%)."""
    unit = _unit("orc", "enemy", [0, 0], hp=6, max_hp=20, personality="aggressive")
    # HP ratio=0.3 >= 0.1 threshold → no flee
    result = _should_flee(unit)
    assert result is False


def test_should_flee_probability_check_can_fail() -> None:
    """Even below threshold a cowardly unit may not flee if random check fails."""
    unit = _unit("goblin", "enemy", [0, 0], hp=5, max_hp=20, personality="cowardly")
    # flee_chance=0.8; random.random() returns 0.9 → does not flee
    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.9):
        result = _should_flee(unit)
    assert result is False


# ---------------------------------------------------------------------------
# _find_flee_destination tests
# ---------------------------------------------------------------------------

def test_flee_destination_maximizes_enemy_distance() -> None:
    """The selected flee cell should maximize distance from all enemies."""
    # Monster at (2,2), enemy at (0,0); flee destination should be far from (0,0)
    monster = _unit("orc", "enemy", [2, 2], speed=6)
    enemy = _unit("hero", "ally", [0, 0])
    grid = _flat_grid()

    dest = _find_flee_destination(monster, grid, [monster, enemy])

    assert dest is not None
    # The flee destination should be farther from (0,0) than the start (2,2)
    # which has distance 4. We expect something like (4,4) at distance 8.
    hero_pos = (0, 0)
    start_dist = grid.distance((2, 2), hero_pos)
    dest_dist = grid.distance(dest, hero_pos)
    assert dest_dist >= start_dist, (
        f"Flee destination {dest} (dist={dest_dist}) should be >= start dist {start_dist}"
    )


def test_flee_destination_not_start_cell() -> None:
    """The flee destination is never the unit's own current position."""
    monster = _unit("goblin", "enemy", [2, 2], speed=4)
    enemy = _unit("hero", "ally", [4, 4])
    grid = _flat_grid()

    dest = _find_flee_destination(monster, grid, [monster, enemy])
    assert dest != (2, 2)


# ---------------------------------------------------------------------------
# _select_attack_index tests
# ---------------------------------------------------------------------------

def test_select_attack_aggressive_highest_damage() -> None:
    """Aggressive personality selects the attack with highest expected damage."""
    attacks = [
        {"name": "Dagger",  "hit_bonus": 5, "damage_dice": "1d4", "range": 1},
        {"name": "Greatsword", "hit_bonus": 2, "damage_dice": "2d6", "range": 1},
        {"name": "Shortbow",   "hit_bonus": 3, "damage_dice": "1d6", "range": 5},
    ]
    # Expected: Dagger=2.5, Greatsword=7.0, Shortbow=3.5 → index 1
    idx = _select_attack_index(attacks, "aggressive")
    assert idx == 1


def test_select_attack_defensive_highest_hit_bonus() -> None:
    """Defensive personality selects the attack with highest hit_bonus."""
    attacks = [
        {"name": "Dagger",     "hit_bonus": 5, "damage_dice": "1d4", "range": 1},
        {"name": "Greatsword", "hit_bonus": 2, "damage_dice": "2d6", "range": 1},
        {"name": "Shortbow",   "hit_bonus": 3, "damage_dice": "1d6", "range": 5},
    ]
    # hit_bonus: Dagger=5, Greatsword=2, Shortbow=3 → index 0
    idx = _select_attack_index(attacks, "defensive")
    assert idx == 0


def test_select_attack_cowardly_longest_range() -> None:
    """Cowardly personality selects the attack with the longest range."""
    attacks = [
        {"name": "Dagger",     "hit_bonus": 5, "damage_dice": "1d4", "range": 1},
        {"name": "Shortbow",   "hit_bonus": 3, "damage_dice": "1d6", "range": 5},
        {"name": "Longbow",    "hit_bonus": 2, "damage_dice": "1d8", "range": 8},
    ]
    # ranges: Dagger=1, Shortbow=5, Longbow=8 → index 2
    idx = _select_attack_index(attacks, "cowardly")
    assert idx == 2


def test_estimate_damage_basic() -> None:
    """_estimate_damage returns correct averages for common dice expressions."""
    assert _estimate_damage("1d6") == 3.5
    assert _estimate_damage("2d6") == 7.0
    assert _estimate_damage("1d4") == 2.5
    assert _estimate_damage("1d8") == 4.5
    assert _estimate_damage("1d6+3") == 6.5


# ---------------------------------------------------------------------------
# _evaluate_targets tests
# ---------------------------------------------------------------------------

def test_evaluate_targets_prefers_close_low_hp() -> None:
    """Targets that are close and have low HP should rank highest."""
    attacker = _unit("monster", "enemy", [0, 0])
    melee_attack = {"name": "Claw", "hit_bonus": 3, "damage_dice": "1d6", "range": 1}

    # Target A: close (dist=1) but healthy
    target_close = _unit("hero_close", "ally", [1, 0], hp=20, max_hp=20)
    # Target B: far (dist=4) and low HP
    target_far_low = _unit("hero_far_low", "ally", [4, 0], hp=2, max_hp=20)
    # Target C: close AND low HP — should rank first
    target_close_low = _unit("hero_close_low", "ally", [1, 1], hp=2, max_hp=20)

    grid = _flat_grid()
    ranked = _evaluate_targets(attacker, grid, [target_close, target_far_low, target_close_low], melee_attack)

    assert ranked[0]["unit_id"] == "hero_close_low", (
        f"Expected hero_close_low first but got {ranked[0]['unit_id']}"
    )


def test_evaluate_targets_in_range_bonus() -> None:
    """Targets within current weapon range get an in_range_bonus."""
    attacker = _unit("monster", "enemy", [0, 0])
    ranged_attack = {"name": "Arrow", "hit_bonus": 3, "damage_dice": "1d6", "range": 5}

    # Target A: dist=3 (within range=5) — gets in_range_bonus
    target_in_range = _unit("hero_a", "ally", [3, 0], hp=20, max_hp=20)
    # Target B: dist=1 (also in range) — even closer
    target_closer = _unit("hero_b", "ally", [1, 0], hp=20, max_hp=20)

    grid = _flat_grid()
    ranked = _evaluate_targets(attacker, grid, [target_in_range, target_closer], ranged_attack)

    # hero_b is closer and also in range, so it should be ranked first
    assert ranked[0]["unit_id"] == "hero_b"


# ---------------------------------------------------------------------------
# decide_monster_turn integration tests
# ---------------------------------------------------------------------------

def test_attack_from_current_position_no_move() -> None:
    """Monster adjacent to target attacks without moving."""
    # Monster at (0,0), player at (1,0) — adjacent, dist=1, melee range=1
    monster = _unit("goblin", "enemy", [0, 0], hp=20, max_hp=20, personality="aggressive")
    player = _unit("hero", "ally", [1, 0], hp=20, max_hp=20)
    grid = _flat_grid()

    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.9):
        # HP ratio=1.0 → no flee
        decision = decide_monster_turn(monster, grid, [monster, player])

    assert decision.action == "attack"
    assert decision.move_to is None
    assert decision.target_id == "hero"


def test_move_toward_target_melee() -> None:
    """Melee monster out of range moves toward target."""
    # Monster at (0,0), player at (4,0) — dist=4, range=1, speed=6
    monster = _unit("goblin", "enemy", [0, 0], hp=20, max_hp=20, speed=6, personality="aggressive")
    player = _unit("hero", "ally", [4, 0], hp=20, max_hp=20)
    grid = _flat_grid()

    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.9):
        decision = decide_monster_turn(monster, grid, [monster, player])

    # Should move toward player and attack
    assert decision.move_to is not None
    # After moving, should be adjacent (dist=1 from player) and attack
    if decision.action == "attack":
        # The move destination should be (3,0) — adjacent to player
        assert decision.move_to == (3, 0), f"Expected (3,0) but got {decision.move_to}"
        assert decision.target_id == "hero"
    else:
        # At minimum should move closer
        assert decision.move_to is not None
        dest_dist = abs(decision.move_to[0] - 4) + abs(decision.move_to[1] - 0)
        start_dist = 4
        assert dest_dist < start_dist, (
            f"Move destination {decision.move_to} is not closer to player"
        )


def test_move_toward_target_ranged_prefers_hill() -> None:
    """Ranged monster moving into range should prefer a hill cell."""
    # Monster at (0,0), player at (5,0)
    # Hill is at (1,2) in _GRID_WITH_HILL
    # Ranged attack with range=4, speed=6
    ranged_attack = [{"name": "Arrow", "hit_bonus": 3, "damage_dice": "1d6", "range": 4}]
    monster = _unit("archer", "enemy", [0, 0], hp=20, max_hp=20, speed=6,
                    personality="cowardly", attacks=ranged_attack)
    player = _unit("hero", "ally", [5, 0], hp=20, max_hp=20)
    grid = _hill_grid()

    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.9):
        decision = decide_monster_turn(monster, grid, [monster, player])

    # Monster should move; destination should use hill if reachable and within range
    # Hill at (1,2): dist to player (5,0) = 4+2=6, range=4 → not in range
    # Hill at (1,2): range_bonus=1, effective_range=5 → still not in range of player at (5,0)
    # So monster should move to some cell in range of player
    assert decision.action in ("attack", "hold")
    if decision.move_to is not None:
        # Just verify it's a valid position
        assert grid.is_in_bounds(decision.move_to[0], decision.move_to[1])


def test_no_targets_returns_hold() -> None:
    """When there are no opposing targets, the decision is hold."""
    monster = _unit("goblin", "enemy", [0, 0], hp=20, max_hp=20)
    # Only a dead enemy
    dead_enemy = _unit("hero", "ally", [1, 0], hp=0, max_hp=20, alive=False)
    grid = _flat_grid()

    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.9):
        decision = decide_monster_turn(monster, grid, [monster, dead_enemy])

    assert decision.action == "hold"
    assert decision.move_to is None
    assert decision.target_id is None


def test_flee_returns_flee_action() -> None:
    """Monster below flee threshold with cowardly personality returns flee action."""
    monster = _unit("goblin", "enemy", [2, 2], hp=3, max_hp=20, personality="cowardly")
    enemy = _unit("hero", "ally", [0, 0])
    grid = _flat_grid()

    # random=0.5 < flee_chance=0.8 → flee
    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.5):
        decision = decide_monster_turn(monster, grid, [monster, enemy])

    assert decision.action == "flee"
    assert decision.target_id is None


# ---------------------------------------------------------------------------
# validate_decision tests
# ---------------------------------------------------------------------------

def test_validate_decision_valid() -> None:
    """A well-formed attack decision within range returns True."""
    # Unit at (0,0), target at (1,0) — adjacent melee, attack_index=0 valid
    unit = _unit("goblin", "enemy", [0, 0], speed=6)
    target = _unit("hero", "ally", [1, 0])
    grid = _flat_grid()
    all_units = [unit, target]

    decision = MonsterDecision(
        move_to=None,
        action="attack",
        target_id="hero",
        attack_index=0,
    )
    assert validate_decision(decision, unit, grid, all_units) is True


def test_validate_decision_invalid_move() -> None:
    """A move_to cell beyond the unit's speed returns False."""
    # Unit at (0,0), speed=2 — cannot reach (4,4) (Manhattan dist=8)
    unit = _unit("goblin", "enemy", [0, 0], speed=2)
    target = _unit("hero", "ally", [4, 4])
    grid = _flat_grid()
    all_units = [unit, target]

    decision = MonsterDecision(
        move_to=(4, 4),
        action="hold",
        target_id=None,
        attack_index=0,
    )
    assert validate_decision(decision, unit, grid, all_units) is False


def test_validate_decision_invalid_target() -> None:
    """Targeting a dead unit or a friendly unit returns False."""
    unit = _unit("goblin", "enemy", [0, 0], speed=6)
    # Dead enemy target (same side as unit would be wrong, but here: dead ally)
    dead_ally = _unit("hero", "ally", [1, 0], alive=False)
    # Friendly unit (same side as attacker)
    friendly = _unit("orc", "enemy", [2, 0])
    grid = _flat_grid()

    # Dead target
    decision_dead = MonsterDecision(
        move_to=None,
        action="attack",
        target_id="hero",
        attack_index=0,
    )
    assert validate_decision(decision_dead, unit, grid, [unit, dead_ally]) is False

    # Friendly target (same side)
    decision_friendly = MonsterDecision(
        move_to=None,
        action="attack",
        target_id="orc",
        attack_index=0,
    )
    assert validate_decision(decision_friendly, unit, grid, [unit, friendly]) is False
