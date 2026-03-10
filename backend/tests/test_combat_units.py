"""Tests for combat_units.py — pure synchronous tests (no async needed)."""

from __future__ import annotations

import random
from typing import Any

import pytest

from app.game_core.content.registries.characters import CharacterTemplate, NpcAttack
from app.game_core.content.registries.monsters import MonsterAttack, MonsterTemplate
from app.game_core.rules.combat_units import (
    assign_positions,
    assign_positions_from_spawns,
    build_companion_unit,
    build_default_grid,
    build_monster_unit,
    build_player_unit,
    build_turn_order,
    resolve_surprise,
    roll_initiative,
)
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, TimeSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_player_state(
    hp: int = 10,
    stats: dict[str, int] | None = None,
    equipment: dict[str, Any] | None = None,
) -> StateContainer:
    """Build a minimal StateContainer with player + area + time slices."""
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "character_id": "hero_1",
        "character_name": "Hero",
        "hp": hp,
        "max_hp": max(hp, 10),
        "ac": 14,
        "stats": stats or {
            "str": 14,
            "dex": 12,
            "con": 12,
            "int": 10,
            "wis": 10,
            "cha": 10,
        },
        "proficiency_bonus": 2,
        "equipment": equipment or {slot: None for slot in [
            "head", "chest", "gloves", "boots", "cloak", "amulet",
            "ring_l", "ring_r", "main_hand", "off_hand", "ranged", "ammo", "belt",
        ]},
    })
    state.register(player)

    areas = AreaSlice()
    areas.restore({"areas": {}})
    state.register(areas)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 6})
    state.register(time_slice)

    return state


def _make_monster_template(
    monster_id: str,
    abilities: dict[str, int] | None = None,
    speed: int = 30,
    attacks: list[MonsterAttack] | None = None,
    hp: int = 10,
    ac: int = 12,
) -> MonsterTemplate:
    return MonsterTemplate(
        id=monster_id,
        name=monster_id.capitalize(),
        hp=hp,
        max_hp=hp,
        ac=ac,
        abilities=abilities or {},
        speed=speed,
        attacks=attacks or [],
    )


def _make_character_template(
    char_id: str,
    combat_capable: bool = True,
    base_hp: int | None = 15,
    stats: dict[str, int] | None = None,
    attacks: list[NpcAttack] | None = None,
) -> CharacterTemplate:
    return CharacterTemplate(
        id=char_id,
        name=char_id.capitalize(),
        combat_capable=combat_capable,
        base_hp=base_hp,
        base_ac=12,
        stats=stats or {"str": 12, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        attacks=attacks or [],
        proficiency_bonus=2,
        level=1,
    )


# ---------------------------------------------------------------------------
# Player unit tests
# ---------------------------------------------------------------------------

class TestBuildPlayerUnit:
    def test_build_player_unit_basic_fields(self) -> None:
        state = _make_player_state()
        unit = build_player_unit(state)

        assert unit["unit_id"] == "player"
        assert unit["side"] == "ally"
        assert unit["source"] == "player"
        assert unit["monster_id"] is None
        assert unit["character_id"] == "hero_1"
        assert unit["name"] == "Hero"
        assert unit["hp"] == 10
        assert unit["max_hp"] == 10
        assert unit["ac"] == 14
        assert unit["speed"] == 3
        assert unit["position"] is None
        assert unit["alive"] is True
        assert unit["fled"] is False
        assert unit["active_effects"] == []
        assert unit["action_used"] is False
        assert unit["move_used"] is False
        assert unit["surprised"] is False
        assert unit["proficiency_bonus"] == 2
        assert isinstance(unit["stats"], dict)
        assert unit["stats"]["str"] == 14

    def test_build_player_unit_unarmed(self) -> None:
        state = _make_player_state()  # no main_hand set
        unit = build_player_unit(state)

        assert len(unit["attacks"]) == 1
        atk = unit["attacks"][0]
        assert atk["name"] == "Unarmed Strike"
        assert atk["damage_dice"] == "1d1"
        assert "UNARMED" in atk["tags"]

    def test_build_player_unit_weapon(self) -> None:
        weapon_slot = {slot: None for slot in [
            "head", "chest", "gloves", "boots", "cloak", "amulet",
            "ring_l", "ring_r", "main_hand", "off_hand", "ranged", "ammo", "belt",
        ]}
        weapon_slot["main_hand"] = {
            "item_id": "longsword",
            "name": "Longsword",
            "hit_bonus": 3,
            "damage_dice": "1d8",
            "damage_type": "slashing",
            "range": 1,
            "tags": ["MELEE", "SLASHING"],
        }
        state = _make_player_state(equipment=weapon_slot)
        unit = build_player_unit(state)

        assert len(unit["attacks"]) == 1
        atk = unit["attacks"][0]
        assert atk["name"] == "Longsword"
        assert atk["hit_bonus"] == 3
        assert atk["damage_dice"] == "1d8"
        assert atk["damage_type"] == "slashing"
        assert "MELEE" in atk["tags"]


# ---------------------------------------------------------------------------
# Companion unit tests
# ---------------------------------------------------------------------------

class TestBuildCompanionUnit:
    def test_build_companion_unit_basic(self) -> None:
        template = _make_character_template("lyra", combat_capable=True, base_hp=18)
        member_data: dict[str, Any] = {"hp": 15, "max_hp": 18}

        unit = build_companion_unit("lyra", member_data, template)

        assert unit is not None
        assert unit["unit_id"] == "lyra"
        assert unit["side"] == "ally"
        assert unit["source"] == "companion"
        assert unit["character_id"] == "lyra"
        assert unit["monster_id"] is None
        assert unit["hp"] == 15
        assert unit["max_hp"] == 18
        assert unit["ac"] == 12

    def test_build_companion_non_combat_returns_none(self) -> None:
        template = _make_character_template("merchant", combat_capable=False)
        unit = build_companion_unit("merchant", {}, template)
        assert unit is None

    def test_build_companion_hp_fallback(self) -> None:
        # member_data has hp → use it
        template = _make_character_template("anya", base_hp=20)
        member_data: dict[str, Any] = {"hp": 12}
        unit = build_companion_unit("anya", member_data, template)
        assert unit is not None
        assert unit["hp"] == 12

        # member_data has no hp → use template.base_hp
        member_data2: dict[str, Any] = {}
        unit2 = build_companion_unit("anya", member_data2, template)
        assert unit2 is not None
        assert unit2["hp"] == 20

        # template.base_hp is None → use fallback 10
        template3 = _make_character_template("rogue", base_hp=None)
        unit3 = build_companion_unit("rogue", {}, template3)
        assert unit3 is not None
        assert unit3["hp"] == 10

    def test_build_companion_attacks_from_template(self) -> None:
        attacks = [NpcAttack(name="Short Sword", hit_bonus=3, damage_dice="1d6",
                             damage_type="piercing", range=1, tags=["MELEE"])]
        template = _make_character_template("fighter", attacks=attacks)
        unit = build_companion_unit("fighter", {}, template)

        assert unit is not None
        assert len(unit["attacks"]) == 1
        atk = unit["attacks"][0]
        assert atk["name"] == "Short Sword"
        assert atk["hit_bonus"] == 3
        assert atk["damage_dice"] == "1d6"


# ---------------------------------------------------------------------------
# Monster unit tests
# ---------------------------------------------------------------------------

class TestBuildMonsterUnit:
    def test_build_monster_unit_basic(self) -> None:
        tmpl = _make_monster_template("goblin", hp=7, ac=13)
        unit = build_monster_unit("goblin", tmpl, 1)

        assert unit["unit_id"] == "goblin_1"
        assert unit["side"] == "enemy"
        assert unit["source"] == "monster"
        assert unit["monster_id"] == "goblin"
        assert unit["character_id"] is None
        assert unit["name"] == "Goblin"
        assert unit["hp"] == 7
        assert unit["max_hp"] == 7
        assert unit["ac"] == 13
        assert unit["alive"] is True
        assert unit["fled"] is False

    def test_build_monster_speed_conversion(self) -> None:
        # 30 ft → 3 grid cells
        tmpl30 = _make_monster_template("wolf", speed=30)
        unit30 = build_monster_unit("wolf", tmpl30, 1)
        assert unit30["speed"] == 3

        # 60 ft → 6 grid cells
        tmpl60 = _make_monster_template("horse", speed=60)
        unit60 = build_monster_unit("horse", tmpl60, 1)
        assert unit60["speed"] == 6

        # 10 ft → would be 1, but min is 2
        tmpl10 = _make_monster_template("slow", speed=10)
        unit10 = build_monster_unit("slow", tmpl10, 1)
        assert unit10["speed"] == 2

    def test_build_monster_default_attack(self) -> None:
        # No attacks in template → gets default Slam attack
        tmpl = _make_monster_template("ooze", attacks=[])
        unit = build_monster_unit("ooze", tmpl, 1)

        assert len(unit["attacks"]) == 1
        atk = unit["attacks"][0]
        assert atk["name"] == "Slam"
        assert atk["damage_dice"] == "1d4"
        assert atk["damage_type"] == "bludgeoning"

    def test_build_monster_unit_with_attacks(self) -> None:
        attacks = [MonsterAttack(name="Bite", hit_bonus=4, damage_dice="2d6",
                                 damage_type="piercing", range=1, tags=["MELEE"])]
        tmpl = _make_monster_template("wolf", attacks=attacks)
        unit = build_monster_unit("wolf", tmpl, 2)

        assert unit["unit_id"] == "wolf_2"
        assert len(unit["attacks"]) == 1
        assert unit["attacks"][0]["name"] == "Bite"
        assert unit["attacks"][0]["hit_bonus"] == 4


# ---------------------------------------------------------------------------
# Surprise resolution tests
# ---------------------------------------------------------------------------

class TestResolveSurprise:
    def _make_units(self) -> list[dict[str, Any]]:
        return [
            {"unit_id": "player", "side": "ally", "stats": {"wis": 10}, "surprised": False},
            {"unit_id": "goblin_1", "side": "enemy", "stats": {"wis": 8}, "surprised": False},
            {"unit_id": "wolf_1", "side": "enemy", "stats": {"wis": 14}, "surprised": False},
        ]

    def test_resolve_surprise_player_surprise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        units = self._make_units()
        # goblin WIS=8 → mod=-1; wolf WIS=14 → mod=+2
        # stealth_total=25 means even wolf (max d20=20 + 2 = 22) < 25 → both surprised
        resolve_surprise(units, "player_surprise", stealth_total=25)
        player = next(u for u in units if u["unit_id"] == "player")
        goblin = next(u for u in units if u["unit_id"] == "goblin_1")
        wolf = next(u for u in units if u["unit_id"] == "wolf_1")
        assert player["surprised"] is False
        assert goblin["surprised"] is True
        assert wolf["surprised"] is True

    def test_resolve_surprise_player_surprise_high_wis_not_surprised(self) -> None:
        units = self._make_units()
        # stealth_total=0 means d20 + mod >= 0 always → nobody surprised
        resolve_surprise(units, "player_surprise", stealth_total=0)
        for unit in units:
            assert unit["surprised"] is False

    def test_resolve_surprise_enemy_surprise(self) -> None:
        units = self._make_units()
        resolve_surprise(units, "enemy_surprise", stealth_total=0)
        player = next(u for u in units if u["unit_id"] == "player")
        goblin = next(u for u in units if u["unit_id"] == "goblin_1")
        assert player["surprised"] is True   # ally
        assert goblin["surprised"] is False  # enemy

    def test_resolve_surprise_none(self) -> None:
        units = self._make_units()
        resolve_surprise(units, "none", stealth_total=999)
        for unit in units:
            assert unit["surprised"] is False


# ---------------------------------------------------------------------------
# Initiative and turn order tests
# ---------------------------------------------------------------------------

class TestBuildTurnOrder:
    def _make_unit(self, unit_id: str, side: str, dex: int = 10) -> dict[str, Any]:
        return {
            "unit_id": unit_id,
            "side": side,
            "stats": {"dex": dex},
            "alive": True,
            "fled": False,
        }

    def test_build_turn_order_descending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        units = [
            self._make_unit("player", "ally", dex=10),
            self._make_unit("goblin_1", "enemy", dex=10),
        ]
        rolls = iter([5, 18])   # player rolls 5, goblin rolls 18
        monkeypatch.setattr("app.game_core.rules.combat_units.random.randint",
                            lambda a, b: next(rolls))

        turn_order, initiative_rolls = build_turn_order(units)
        assert turn_order[0] == "goblin_1"
        assert turn_order[1] == "player"
        assert initiative_rolls["goblin_1"] > initiative_rolls["player"]

    def test_build_turn_order_dex_tiebreak(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Both roll the same d20 result, but rogue has higher DEX → goes first
        units = [
            self._make_unit("fighter", "ally", dex=10),
            self._make_unit("rogue", "ally", dex=16),
        ]
        rolls = iter([10, 10])  # both roll 10 on d20
        monkeypatch.setattr("app.game_core.rules.combat_units.random.randint",
                            lambda a, b: next(rolls))

        turn_order, _ = build_turn_order(units)
        # rogue: 10 + (16-10)//2 = 10+3=13; fighter: 10 + (10-10)//2 = 10+0=10
        assert turn_order[0] == "rogue"
        assert turn_order[1] == "fighter"

    def test_build_turn_order_excludes_dead_and_fled(self) -> None:
        units = [
            self._make_unit("player", "ally", dex=10),
            {**self._make_unit("goblin_1", "enemy", dex=10), "alive": False},
            {**self._make_unit("wolf_1", "enemy", dex=10), "fled": True},
        ]
        turn_order, _ = build_turn_order(units)
        assert len(turn_order) == 1
        assert turn_order[0] == "player"


# ---------------------------------------------------------------------------
# Position assignment tests
# ---------------------------------------------------------------------------

class TestAssignPositions:
    def _make_units(self, n_allies: int, n_enemies: int) -> list[dict[str, Any]]:
        units: list[dict[str, Any]] = []
        for i in range(n_allies):
            units.append({"unit_id": f"ally_{i}", "side": "ally", "position": None})
        for i in range(n_enemies):
            units.append({"unit_id": f"enemy_{i}", "side": "enemy", "position": None})
        return units

    def test_assign_positions_sides(self) -> None:
        units = self._make_units(2, 2)
        assign_positions(units, grid_width=8, grid_height=6)

        for unit in units:
            assert unit["position"] is not None
            col, row = unit["position"]
            assert 0 <= col < 8
            assert 0 <= row < 6
            if unit["side"] == "ally":
                assert col <= 1, f"ally should be in col 0-1, got {col}"
            else:
                assert col >= 6, f"enemy should be in col 6-7, got {col}"

    def test_assign_positions_all_have_positions(self) -> None:
        units = self._make_units(3, 3)
        assign_positions(units, grid_width=8, grid_height=6)
        for unit in units:
            assert unit["position"] is not None
            assert len(unit["position"]) == 2

    def test_assign_positions_single_units(self) -> None:
        units = [
            {"unit_id": "player", "side": "ally", "position": None},
            {"unit_id": "boss", "side": "enemy", "position": None},
        ]
        assign_positions(units, grid_width=8, grid_height=6)
        ally = units[0]
        enemy = units[1]
        assert ally["position"][0] == 0    # first ally: col 0
        assert enemy["position"][0] == 7   # first enemy: col 7 (w-1)


# ---------------------------------------------------------------------------
# build_default_grid test
# ---------------------------------------------------------------------------

class TestBuildDefaultGrid:
    def test_default_grid_dimensions(self) -> None:
        grid = build_default_grid()
        assert grid["width"] == 8
        assert grid["height"] == 6
        assert len(grid["terrain"]) == 6
        assert all(len(row) == 8 for row in grid["terrain"])

    def test_default_grid_all_grass(self) -> None:
        grid = build_default_grid(width=4, height=3)
        for row in grid["terrain"]:
            assert row == "GGGG"

    def test_default_grid_custom_size(self) -> None:
        grid = build_default_grid(width=10, height=5)
        assert grid["width"] == 10
        assert grid["height"] == 5
        assert len(grid["terrain"]) == 5
        assert all(len(row) == 10 for row in grid["terrain"])


# ---------------------------------------------------------------------------
# assign_positions_from_spawns tests
# ---------------------------------------------------------------------------

class TestAssignPositionsFromSpawns:
    def _make_units(self, n_allies: int, n_enemies: int) -> list[dict[str, Any]]:
        units: list[dict[str, Any]] = []
        for i in range(n_allies):
            units.append({"unit_id": f"ally_{i}", "side": "ally", "position": None})
        for i in range(n_enemies):
            units.append({"unit_id": f"enemy_{i}", "side": "enemy", "position": None})
        return units

    def test_assign_positions_from_spawns_basic(self) -> None:
        """Spawn points are assigned in order to allies and enemies."""
        player_spawns = [(0, 2), (0, 3), (1, 2)]
        enemy_spawns = [(6, 2), (7, 2), (7, 3)]
        units = self._make_units(3, 3)

        assign_positions_from_spawns(units, player_spawns, enemy_spawns)

        allies = [u for u in units if u["side"] == "ally"]
        enemies = [u for u in units if u["side"] == "enemy"]

        for idx, unit in enumerate(allies):
            assert unit["position"] == list(player_spawns[idx]), (
                f"ally[{idx}] position mismatch: {unit['position']}"
            )
        for idx, unit in enumerate(enemies):
            assert unit["position"] == list(enemy_spawns[idx]), (
                f"enemy[{idx}] position mismatch: {unit['position']}"
            )

    def test_assign_positions_from_spawns_wraps(self) -> None:
        """When units outnumber spawn points, positions wrap around (modulo)."""
        player_spawns = [(0, 2), (0, 3)]   # only 2 spawn points
        enemy_spawns = [(6, 2), (6, 3)]     # only 2 spawn points
        units = self._make_units(4, 4)      # but 4 units each side

        assign_positions_from_spawns(units, player_spawns, enemy_spawns)

        allies = [u for u in units if u["side"] == "ally"]
        enemies = [u for u in units if u["side"] == "enemy"]

        # idx 0 → spawn[0], idx 1 → spawn[1], idx 2 → spawn[0] (wrap), idx 3 → spawn[1]
        assert allies[0]["position"] == [0, 2]
        assert allies[1]["position"] == [0, 3]
        assert allies[2]["position"] == [0, 2]   # wraps back to spawn[0]
        assert allies[3]["position"] == [0, 3]   # wraps back to spawn[1]

        assert enemies[0]["position"] == [6, 2]
        assert enemies[1]["position"] == [6, 3]
        assert enemies[2]["position"] == [6, 2]   # wraps
        assert enemies[3]["position"] == [6, 3]   # wraps

    def test_assign_positions_from_spawns_modifies_in_place(self) -> None:
        """Function modifies units list in-place; return value is None."""
        units = self._make_units(1, 1)
        result = assign_positions_from_spawns(units, [(1, 1)], [(5, 4)])
        assert result is None
        assert units[0]["position"] == [1, 1]
        assert units[1]["position"] == [5, 4]

    def test_assign_positions_from_spawns_tuple_input(self) -> None:
        """Accepts tuple inputs (as returned by BattleMapVariant.player_spawn)."""
        player_spawns: tuple[tuple[int, int], ...] = ((0, 2), (1, 2))
        enemy_spawns: tuple[tuple[int, int], ...] = ((6, 3), (7, 3))
        units = self._make_units(2, 2)

        assign_positions_from_spawns(units, player_spawns, enemy_spawns)

        allies = [u for u in units if u["side"] == "ally"]
        enemies = [u for u in units if u["side"] == "enemy"]
        assert allies[0]["position"] == [0, 2]
        assert allies[1]["position"] == [1, 2]
        assert enemies[0]["position"] == [6, 3]
        assert enemies[1]["position"] == [7, 3]
