"""Tests for combat environment modifiers (Phase 7 — D-R40).

Covers:
  - EnvironmentModifiers dataclass and compute_environment_modifiers()
  - Rain / fog / night conditions (and combinations)
  - combat_attack: rain penalty lowers effective atk_total

All tests are synchronous.
"""

from __future__ import annotations

import random
import unittest.mock as mock
from typing import Any

from app.game_core.rules import Command
from app.game_core.rules.battle_grid import (
    EnvironmentModifiers,
    compute_environment_modifiers,
)
from app.game_core.rules.handlers import CombatHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice


# ---------------------------------------------------------------------------
# Helpers (copied from test_combat_v2_attack to keep independence)
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


def _unit(
    unit_id: str,
    side: str,
    position: list[int],
    *,
    hp: int = 20,
    max_hp: int = 20,
    ac: int = 12,
    alive: bool = True,
    action_used: bool = False,
    attacks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "side": side,
        "source": "monster",
        "position": list(position),
        "speed": 6,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "alive": alive,
        "fled": False,
        "action_used": action_used,
        "move_used": False,
        "disengaged": False,
        "dashed": False,
        "defending": False,
        "reaction_used": False,
        "surprised": False,
        "attacks": attacks or [
            {"name": "Sword", "hit_bonus": 2, "damage_dice": "1d6",
             "damage_type": "slashing", "range": 1}
        ],
        "stats": {"dex": 10, "wis": 10},
    }


def _make_v2_payload(
    units: list[dict[str, Any]],
    turn_order: list[str],
    current_turn_index: int = 0,
    grid: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_unit_id = turn_order[current_turn_index] if turn_order else ""
    return {
        "area_id": "dungeon",
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
        "combat_round": 1,
        "initiative_rolls": {},
        "environment": environment or {"weather": "clear", "time_of_day": "day"},
    }


def _make_state(
    payload: dict[str, Any],
    sub_area_id: str = "room1",
    area_id: str = "dungeon",
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "character_id": "hero",
        "hp": 20,
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
    return Command(type=cmd_type, params=params, source="player")


def _seq_rolls(*values: int):
    """Return a randint replacement that yields successive values."""
    it = iter(values)

    def _roll(a: int, b: int) -> int:
        return next(it, values[-1])

    return _roll


# ---------------------------------------------------------------------------
# Unit tests: EnvironmentModifiers + compute_environment_modifiers
# ---------------------------------------------------------------------------

def test_environment_modifiers_defaults() -> None:
    """Default (clear / day) yields zero modifiers and no visibility cap."""
    mods = compute_environment_modifiers("clear", "day")
    assert mods.hit_modifier == 0
    assert mods.ranged_hit_modifier == 0
    assert mods.max_visibility is None


def test_environment_modifiers_rain() -> None:
    """Rain applies hit_modifier=-1, no ranged penalty, no visibility cap."""
    mods = compute_environment_modifiers("rain", "day")
    assert mods.hit_modifier == -1
    assert mods.ranged_hit_modifier == 0
    assert mods.max_visibility is None


def test_environment_modifiers_fog_visibility() -> None:
    """Fog applies hit_modifier=-2 and max_visibility=3."""
    mods = compute_environment_modifiers("fog", "day")
    assert mods.hit_modifier == -2
    assert mods.ranged_hit_modifier == 0
    assert mods.max_visibility == 3


def test_environment_modifiers_night_ranged() -> None:
    """Night applies ranged_hit_modifier=-2 and max_visibility=4 (F-4)."""
    mods = compute_environment_modifiers("clear", "night")
    assert mods.hit_modifier == 0
    assert mods.ranged_hit_modifier == -2
    assert mods.max_visibility == 4  # night limits vision to 4 cells (F-4)


def test_environment_modifiers_night_and_rain_stack() -> None:
    """Night + rain stack: hit_modifier=-1, ranged_hit_modifier=-2, max_visibility=4 (F-4)."""
    mods = compute_environment_modifiers("rain", "night")
    assert mods.hit_modifier == -1
    assert mods.ranged_hit_modifier == -2
    assert mods.max_visibility == 4  # rain doesn't set visibility, night does (F-4)


def test_environment_modifiers_night_and_fog_stack() -> None:
    """Night + fog stack: hit_modifier=-2, ranged_hit_modifier=-2, max_visibility=3."""
    mods = compute_environment_modifiers("fog", "night")
    assert mods.hit_modifier == -2
    assert mods.ranged_hit_modifier == -2
    assert mods.max_visibility == 3


def test_environment_modifiers_is_frozen() -> None:
    """EnvironmentModifiers is immutable (frozen dataclass)."""
    mods = EnvironmentModifiers(hit_modifier=-1, ranged_hit_modifier=0, max_visibility=None)
    try:
        mods.hit_modifier = 0  # type: ignore[misc]
        assert False, "should have raised FrozenInstanceError"
    except Exception:
        pass  # expected — frozen dataclass prevents mutation


# ---------------------------------------------------------------------------
# Integration: combat_attack with environment penalties
# ---------------------------------------------------------------------------

def test_combat_attack_with_rain_penalty() -> None:
    """Rain (-1 hit) causes a borderline attack to miss.

    Without rain: atk_roll=10, hit_bonus=2 → atk_total=12 ≥ ac=12 → hit.
    With rain:    atk_total=12-1=11 < ac=12 → miss.
    """
    player = _unit("player", "ally", [0, 0], ac=12, attacks=[
        {"name": "Sword", "hit_bonus": 2, "damage_dice": "1d6",
         "damage_type": "slashing", "range": 1}
    ])
    player["source"] = "player"
    enemy = _unit("goblin", "enemy", [1, 0], ac=12)

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "rain", "time_of_day": "day"},
    )
    state = _make_state(payload)

    cmd = _make_cmd("combat_attack", {
        "sub_area_id": "room1",
        "target": "goblin",
        "attack_index": 0,
    })

    handler = CombatHandler()
    # d20=10 → atk_total = 10+2 - 1(rain) = 11 < ac=12 → miss
    with mock.patch.object(random, "randint", _seq_rolls(10)):
        result = handler.compute(cmd, state, None)

    assert result.executed, f"unexpected failure: {result.errors}"
    updated_payload = None
    for change in result.delta.changes:
        if "hostile_tracking" in change.path:
            updated_payload = change.value
            break
    assert updated_payload is not None
    goblin = next(u for u in updated_payload["units"] if u["unit_id"] == "goblin")
    assert goblin["hp"] == 20, "rain penalty should have caused a miss; hp should be unchanged"


def test_combat_attack_without_rain_penalty_hits() -> None:
    """Control: same borderline roll succeeds when weather is clear."""
    player = _unit("player", "ally", [0, 0], ac=12, attacks=[
        {"name": "Sword", "hit_bonus": 2, "damage_dice": "1d4",
         "damage_type": "slashing", "range": 1}
    ])
    player["source"] = "player"
    enemy = _unit("goblin", "enemy", [1, 0], ac=12)

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "clear", "time_of_day": "day"},
    )
    state = _make_state(payload)

    cmd = _make_cmd("combat_attack", {
        "sub_area_id": "room1",
        "target": "goblin",
        "attack_index": 0,
    })

    handler = CombatHandler()
    # d20=10, hit_bonus=2 → atk_total=12 = ac=12 → hit; damage=1d4 → mock 2
    with mock.patch.object(random, "randint", _seq_rolls(10, 2)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    updated_payload = None
    for change in result.delta.changes:
        if "hostile_tracking" in change.path:
            updated_payload = change.value
            break
    assert updated_payload is not None
    goblin = next(u for u in updated_payload["units"] if u["unit_id"] == "goblin")
    assert goblin["hp"] < 20, "clear weather borderline hit should deal damage"


def test_combat_attack_ranged_night_penalty() -> None:
    """Night ranged attack: atk_total gets -2 additional penalty for range > 1.

    Without night: atk_roll=12, hit_bonus=2 → atk_total=14 ≥ ac=13 → hit.
    With night:    atk_total = 12+2 + 0(hit) + (-2)(ranged_night) = 12 < ac=13 → miss.
    """
    player = _unit("player", "ally", [0, 0], attacks=[
        {"name": "Bow", "hit_bonus": 2, "damage_dice": "1d6",
         "damage_type": "piercing", "range": 4}
    ])
    player["source"] = "player"
    enemy = _unit("goblin", "enemy", [2, 0], ac=13)

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "clear", "time_of_day": "night"},
    )
    state = _make_state(payload)

    cmd = _make_cmd("combat_attack", {
        "sub_area_id": "room1",
        "target": "goblin",
        "attack_index": 0,
    })

    handler = CombatHandler()
    # d20=12 → atk_total = 12+2+0(hit_mod)+(-2)(ranged_night) = 12 < ac=13 → miss
    with mock.patch.object(random, "randint", _seq_rolls(12)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    updated_payload = None
    for change in result.delta.changes:
        if "hostile_tracking" in change.path:
            updated_payload = change.value
            break
    assert updated_payload is not None
    goblin = next(u for u in updated_payload["units"] if u["unit_id"] == "goblin")
    assert goblin["hp"] == 20, "night ranged penalty should cause miss"


def test_combat_attack_fog_blocks_distant_ranged() -> None:
    """Fog (max_visibility=3) blocks a ranged attack at distance 4.

    Even with a natural 20, fog-blocked attack should miss (fog_blocked gate).
    """
    player = _unit("player", "ally", [0, 0], attacks=[
        {"name": "Bow", "hit_bonus": 5, "damage_dice": "1d8",
         "damage_type": "piercing", "range": 6}
    ])
    player["source"] = "player"
    enemy = _unit("goblin", "enemy", [4, 0], ac=10)  # distance = 4 > max_visibility=3

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "fog", "time_of_day": "day"},
    )
    state = _make_state(payload)

    cmd = _make_cmd("combat_attack", {
        "sub_area_id": "room1",
        "target": "goblin",
        "attack_index": 0,
    })

    handler = CombatHandler()
    # Even nat 20 should be fog-blocked (distance 4 > max_visibility 3)
    with mock.patch.object(random, "randint", _seq_rolls(20)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    updated_payload = None
    for change in result.delta.changes:
        if "hostile_tracking" in change.path:
            updated_payload = change.value
            break
    assert updated_payload is not None
    goblin = next(u for u in updated_payload["units"] if u["unit_id"] == "goblin")
    assert goblin["hp"] == 20, "fog should block attack beyond max_visibility"
