"""Tests for kill_count flag writing on monster defeat via v2 combat_attack."""

from __future__ import annotations

import random
import unittest.mock as mock
from typing import Any

from app.game_core.rules import Command
from app.game_core.rules.handlers import CombatHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice


# ------------------------------------------------------------------
# Helpers (v2 combat payload setup)
# ------------------------------------------------------------------

_DEFAULT_GRID = {
    "width": 6,
    "height": 6,
    "terrain": ["GGGGGG"] * 6,
}


def _unit(
    unit_id: str,
    side: str,
    position: list[int],
    *,
    hp: int = 10,
    max_hp: int = 10,
    ac: int = 5,
    alive: bool = True,
    fled: bool = False,
    action_used: bool = False,
    move_used: bool = False,
    monster_id: str | None = None,
    attacks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    u: dict[str, Any] = {
        "unit_id": unit_id,
        "side": side,
        "source": "monster",
        "position": list(position),
        "speed": 6,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "alive": alive,
        "fled": fled,
        "action_used": action_used,
        "move_used": move_used,
        "disengaged": False,
        "dashed": False,
        "defending": False,
        "reaction_used": False,
        "surprised": False,
        "attacks": attacks or [
            {"name": "Claw", "hit_bonus": 10, "damage_dice": "1d4",
             "damage_type": "slashing", "range": 1},
        ],
        "stats": {"dex": 10, "wis": 10},
    }
    if monster_id is not None:
        u["monster_id"] = monster_id
    return u


def _make_v2_payload(
    units: list[dict[str, Any]],
    turn_order: list[str],
    area_id: str = "forest",
    sub_area_id: str = "combat_1",
) -> dict[str, Any]:
    return {
        "area_id": area_id,
        "version": 2,
        "combat_active": True,
        "cleared": False,
        "blocking": True,
        "status": "engaged",
        "units": [dict(u) for u in units],
        "grid": _DEFAULT_GRID,
        "turn_order": list(turn_order),
        "current_turn_index": 0,
        "current_unit_id": turn_order[0] if turn_order else "",
        "combat_round": 1,
        "initiative_rolls": {},
        "monster_ids": [u["monster_id"] for u in units if u.get("monster_id")],
    }


def _make_state(
    payload: dict[str, Any],
    sub_area_id: str = "combat_1",
    area_id: str = "forest",
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
        "stats": {"str": 14, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
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
    flags.restore({"flags": {}})
    state.register(flags)

    return state


def _seq_rolls(*values: int):
    it = iter(values)
    def _roll(a: int, b: int) -> int:
        return next(it, values[-1])
    return _roll


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestKillCountFlag:
    def test_kill_count_incremented_on_defeat(self) -> None:
        """When a monster is killed via combat_attack, kill_count flag should increment."""
        attacker = _unit("player", "ally", [0, 0])
        target = _unit("goblin_1", "enemy", [1, 0], hp=1, ac=1,
                        monster_id="goblin")
        payload = _make_v2_payload([attacker, target], ["player"])
        state = _make_state(payload)

        cmd = Command(type="combat_attack",
                      params={"sub_area_id": "combat_1", "target": "goblin_1"},
                      source="player")
        handler = CombatHandler()
        # d20=15 → total=15+10=25 > AC=1 → hit; 1d4=1 → kills 1 HP goblin
        with mock.patch.object(random, "randint", _seq_rolls(15, 1)):
            result = handler.compute(cmd, state, None)

        assert result.executed is True
        assert result.metadata["combat_cleared"] is True
        _apply(result, state)
        assert state.flags.get("kill_count_goblin", 0) == 1

    def test_kill_count_not_incremented_on_flee(self) -> None:
        """When a monster flees (fled=True), kill_count should NOT increment."""
        attacker = _unit("player", "ally", [0, 0])
        # Monster already fled
        target = _unit("goblin_1", "enemy", [1, 0], hp=0, ac=1,
                        alive=False, fled=True, monster_id="goblin")
        payload = _make_v2_payload([attacker, target], ["player"])
        # Manually set combat inactive (already fled)
        payload["combat_active"] = False
        payload["cleared"] = True
        state = _make_state(payload)

        # No kill_count should have been incremented (monster fled, not killed)
        assert state.flags.get("kill_count_goblin", 0) == 0

    def test_kill_count_accumulates(self) -> None:
        """Multiple kills in separate combats should accumulate the count."""
        for expected_count in (1, 2):
            attacker = _unit("player", "ally", [0, 0])
            target = _unit("goblin_1", "enemy", [1, 0], hp=1, ac=1,
                            monster_id="goblin")
            payload = _make_v2_payload([attacker, target], ["player"])
            state = _make_state(payload)
            # Restore existing kill count before second combat
            if expected_count == 2:
                state.flags.restore({"flags": {"kill_count_goblin": 1}})

            cmd = Command(type="combat_attack",
                          params={"sub_area_id": "combat_1", "target": "goblin_1"},
                          source="player")
            handler = CombatHandler()
            with mock.patch.object(random, "randint", _seq_rolls(15, 1)):
                result = handler.compute(cmd, state, None)

            assert result.executed and result.metadata["combat_cleared"]
            _apply(result, state)
            assert state.flags.get("kill_count_goblin", 0) == expected_count

    def test_kill_count_different_monster_types(self) -> None:
        """Different monster types get separate kill_count flags."""
        # Kill a goblin
        attacker = _unit("player", "ally", [0, 0])
        goblin = _unit("goblin_1", "enemy", [1, 0], hp=1, ac=1, monster_id="goblin")
        payload = _make_v2_payload([attacker, goblin], ["player"])
        state = _make_state(payload)

        cmd = Command(type="combat_attack",
                      params={"sub_area_id": "combat_1", "target": "goblin_1"},
                      source="player")
        handler = CombatHandler()
        with mock.patch.object(random, "randint", _seq_rolls(15, 1)):
            result = handler.compute(cmd, state, None)
        _apply(result, state)
        assert state.flags.get("kill_count_goblin", 0) == 1

        # Kill a wolf in a new combat
        wolf = _unit("wolf_1", "enemy", [1, 0], hp=1, ac=1, monster_id="wolf")
        payload2 = _make_v2_payload([attacker, wolf], ["player"],
                                    sub_area_id="combat_2")
        state2 = _make_state(payload2, sub_area_id="combat_2")
        state2.flags.restore({"flags": {"kill_count_goblin": 1}})

        cmd2 = Command(type="combat_attack",
                       params={"sub_area_id": "combat_2", "target": "wolf_1"},
                       source="player")
        with mock.patch.object(random, "randint", _seq_rolls(15, 1)):
            result2 = handler.compute(cmd2, state2, None)
        _apply(result2, state2)
        assert state2.flags.get("kill_count_goblin", 0) == 1
        assert state2.flags.get("kill_count_wolf", 0) == 1
