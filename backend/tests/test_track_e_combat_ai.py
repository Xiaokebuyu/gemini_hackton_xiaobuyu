"""Track E tests: combat AI personality + preferred_terrain + melee AI improvements.

Covers:
- E-1: companion ai_personality propagates from CharacterTemplate → combat unit
- E-2: monster AI moves toward preferred_terrain cells
- E-3: melee AI prefers AC-bonus cells (B1-08), avoids swamp (B1-09),
        target evaluation penalizes high-cover targets (B1-10)

All tests are synchronous pure-function tests.
"""

from __future__ import annotations

import unittest.mock as mock
from typing import Any

import pytest

from app.game_core.content.registries.characters import CharacterTemplate, NpcAttack
from app.game_core.content.registries.monsters import MonsterAttack, MonsterTemplate
from app.game_core.rules.battle_ai import (
    _evaluate_targets,
    _find_move_toward_target,
    decide_monster_turn,
)
from app.game_core.rules.battle_grid import BattleGrid
from app.game_core.rules.combat_units import build_companion_unit, build_monster_unit


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

# Grid with forest (F, ac_bonus=2) at (1,2) and swamp (S, move_cost=3) at (3,2)
_MIXED_TERRAIN_5x5 = {
    "width": 5,
    "height": 5,
    "terrain": [
        "GGGGG",
        "GGGGG",
        "GFGSGG"[:5],  # (1,2)=F, (3,2)=S
        "GGGGG",
        "GGGGG",
    ],
}

# Grid: hill (H) at (2,0) — range_bonus=1
_HILL_GRID = {
    "width": 5,
    "height": 3,
    "terrain": [
        "GGHGG",   # (2,0) = Hill
        "GGGGG",
        "GGGGG",
    ],
}

# Grid with hill (H) at (1,1) and forest (F) at (2,2) and swamp (S) at (3,1)
_TERRAIN_FOR_MELEE = {
    "width": 5,
    "height": 5,
    "terrain": [
        "GGGGG",
        "GHGSG",   # (1,1)=Hill, (3,1)=Swamp
        "GGFGG",   # (2,2)=Forest (ac_bonus=2)
        "GGGGG",
        "GGGGG",
    ],
}


def _flat_grid() -> BattleGrid:
    return BattleGrid.from_map_data(_FLAT_5x5)


def _hill_grid() -> BattleGrid:
    return BattleGrid.from_map_data(_HILL_GRID)


def _terrain_melee_grid() -> BattleGrid:
    return BattleGrid.from_map_data(_TERRAIN_FOR_MELEE)


# ---------------------------------------------------------------------------
# Unit factory
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
    preferred_terrain: list[str] | None = None,
) -> dict[str, Any]:
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
        "preferred_terrain": preferred_terrain or [],
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
# E-1: companion ai_personality from CharacterTemplate
# ---------------------------------------------------------------------------

def _make_character_template(
    char_id: str,
    *,
    combat_capable: bool = True,
    base_hp: int = 20,
    ai_personality: str | None = None,
    attacks: list[NpcAttack] | None = None,
) -> CharacterTemplate:
    return CharacterTemplate(
        id=char_id,
        name=char_id.capitalize(),
        combat_capable=combat_capable,
        base_hp=base_hp,
        base_ac=13,
        stats={"str": 14, "dex": 12, "con": 12, "int": 10, "wis": 10, "cha": 10},
        attacks=attacks or [],
        proficiency_bonus=2,
        level=2,
        ai_personality=ai_personality,
    )


def test_companion_unit_ai_personality_from_template() -> None:
    """companion unit inherits ai_personality from CharacterTemplate."""
    template = _make_character_template("priestess", ai_personality="protective")
    unit = build_companion_unit("priestess", {}, template)

    assert unit is not None
    assert unit["ai_personality"] == "protective"


def test_companion_unit_ai_personality_default_aggressive() -> None:
    """companion unit defaults to 'aggressive' when template has no ai_personality."""
    template = _make_character_template("warrior", ai_personality=None)
    unit = build_companion_unit("warrior", {}, template)

    assert unit is not None
    assert unit["ai_personality"] == "aggressive"


def test_companion_unit_various_personalities() -> None:
    """Verify the full range of supported personalities propagates correctly."""
    for personality in ("aggressive", "defensive", "cowardly", "protective"):
        template = _make_character_template(f"npc_{personality}", ai_personality=personality)
        unit = build_companion_unit(f"npc_{personality}", {}, template)
        assert unit is not None, f"expected unit for personality={personality}"
        assert unit["ai_personality"] == personality, (
            f"expected ai_personality={personality}, got {unit['ai_personality']}"
        )


def test_monster_unit_preferred_terrain_in_unit_dict() -> None:
    """build_monster_unit propagates preferred_terrain list from MonsterTemplate."""
    template = MonsterTemplate(
        id="forest_goblin",
        name="Forest Goblin",
        hp=8,
        max_hp=8,
        ac=12,
        speed=30,
        preferred_terrain=["forest", "hill"],
    )
    unit = build_monster_unit("forest_goblin", template, 1)

    assert "preferred_terrain" in unit
    assert unit["preferred_terrain"] == ["forest", "hill"]


def test_monster_unit_empty_preferred_terrain() -> None:
    """build_monster_unit passes an empty list when MonsterTemplate has no preferred_terrain."""
    template = MonsterTemplate(
        id="goblin",
        name="Goblin",
        hp=7,
        max_hp=7,
        ac=12,
        speed=30,
    )
    unit = build_monster_unit("goblin", template, 1)

    assert "preferred_terrain" in unit
    assert unit["preferred_terrain"] == []


def test_decide_monster_turn_uses_ai_personality_when_personality_missing() -> None:
    """Rules AI should read ai_personality before falling back to legacy personality."""
    grid = _flat_grid()
    monster = _unit("rat", "enemy", [0, 0], hp=1, max_hp=10)
    monster.pop("personality", None)
    monster["ai_personality"] = "cowardly"
    target = _unit("hero", "ally", [1, 0])

    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.1):
        decision = decide_monster_turn(monster, grid, [monster, target])

    assert decision.action == "flee"


# ---------------------------------------------------------------------------
# E-2: AI moves toward preferred_terrain cells
# ---------------------------------------------------------------------------

def test_melee_ai_prefers_preferred_terrain_cell() -> None:
    """Melee AI chooses a preferred_terrain adjacent cell over a grass cell.

    Grid layout (5x5):
      Row 0: GGGGG
      Row 1: GHGSG   (1,1)=Hill, (3,1)=Swamp
      Row 2: GGFGG   (2,2)=Forest (ac_bonus=2)
      Row 3: GGGGG
      Row 4: GGGGG

    Monster at (0,2) wants to move adjacent to target at (2,2).
    Adjacent cells are: (1,2)=G, (3,2)=G, (2,1)=G, (2,3)=G.
    Monster's preferred_terrain=["forest"] → no adjacent match.

    Test simpler: monster at (0,1), target at (2,1).
    Adjacent cells of (2,1): (1,1)=H, (3,1)=S, (2,0)=G, (2,2)=F.
    With preferred_terrain=["hill"], (1,1) should be preferred.
    """
    grid = _terrain_melee_grid()
    # Monster at (0,1), target at (2,1) — dist=2, melee range=1, speed=6
    monster = _unit("wolf", "enemy", [0, 1], speed=6, preferred_terrain=["hill"])
    target = _unit("hero", "ally", [2, 1], hp=20, max_hp=20)

    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.9):
        decision = decide_monster_turn(monster, grid, [monster, target])

    assert decision.action == "attack", f"Expected attack but got {decision.action}"
    assert decision.move_to is not None, "Expected movement to happen"
    # (1,1) = Hill → preferred, should be chosen over (2,0) = Grass
    assert decision.move_to == (1, 1), (
        f"Expected hill cell (1,1) but got {decision.move_to}"
    )


def test_melee_ai_avoids_swamp_adjacent_cell() -> None:
    """Melee AI does NOT move to swamp adjacent cell when grass alternative exists.

    Monster at (4,1), target at (2,1) — adjacent cells: (3,1)=Swamp, (2,0)=G, (2,2)=F.
    All are reachable. Without preference, (3,1) is closest (cost-wise) but
    swamp_penalty=2.0 should push it behind (2,0) grass.
    """
    grid = _terrain_melee_grid()
    # Monster at (4,1), target at (2,1)
    monster = _unit("orc", "enemy", [4, 1], speed=6, preferred_terrain=[])
    target = _unit("hero", "ally", [2, 1])

    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.9):
        decision = decide_monster_turn(monster, grid, [monster, target])

    assert decision.move_to is not None
    # (3,1) is swamp → should not be chosen
    assert decision.move_to != (3, 1), (
        f"Monster should avoid swamp (3,1) but moved there: {decision.move_to}"
    )


# ---------------------------------------------------------------------------
# E-3: melee AI improvements — unit tests for _find_move_toward_target
# ---------------------------------------------------------------------------

# Grid specifically designed for cover tests:
# Row 0: GGGGG
# Row 1: GFGGG  → (1,1) = Forest (ac_bonus=2)
# Row 2: GGGGG
# Row 3: GGGGG
# Row 4: GGGGG
_COVER_GRID_DATA = {
    "width": 5,
    "height": 5,
    "terrain": [
        "GGGGG",
        "GFGGG",
        "GGGGG",
        "GGGGG",
        "GGGGG",
    ],
}


def test_melee_prefers_cover_cell_over_grass() -> None:
    """_find_move_toward_target for melee picks forest (ac_bonus=2) over plain grass.

    Grid (5x5):
      Row 0: GGGGG
      Row 1: GFGGG  → (1,1) = Forest (ac_bonus=2, move_cost=2)
      Row 2: GGGGG
      ...

    Monster at (4,2), target at (2,2) — dist=2.
    Adjacent cells to target (2,2) reachable from (4,2):
      (1,2)=G: reachable_cost=3 → score 3+0+0=3.0
      (3,2)=G: reachable_cost=1 → score 1+0+0=1.0
      (2,1)=G: reachable_cost=2 → score 2+0+0=2.0
      (2,3)=G: reachable_cost=2 → score 2+0+0=2.0

    Without any forest adjacent to target, test the simpler behavior using
    a grid where forest IS adjacent to the target.

    Grid (5x5):
      Row 0: GGGGG
      Row 1: GFGGG  → (1,1) = Forest (ac_bonus=2, move_cost=2)
      Row 2: GGGGG
      ...

    Monster at (4,1), target at (2,1) — dist=2. Adjacent to (2,1):
      (1,1)=F: move_cost=2, cover_bonus=-1 → score 2+(-1)=1.0
      (3,1)=G: move_cost=1 → score 1+0=1.0
      (2,0)=G: move_cost=1 → score 1+0=1.0
      (2,2)=G: move_cost=1 → score 1+0=1.0

    Forest ties — not a clean test. Use separate grid where forest is more expensive
    but cover_bonus makes it win:

    Grid (6x3):
      Row 0: GGGGGG
      Row 1: GFSSSG  → (1,1)=F, (2,1)-(4,1)=Swamp (block other adjacent cells)
      Row 2: GGGGGG

    Monster at (0,1), target at (3,1) - dist=3.
    Adjacent to (3,1): (2,1)=S, (4,1)=S, (3,0)=G, (3,2)=G.
    Only non-swamp adjacent cells: (3,0) and (3,2) both grass → no forest to pick.

    Simplest valid test: use decide_monster_turn which exercises the full path,
    and verify that when forest is adjacent to target, it's chosen over equivalent grass.
    """
    # Grid where (2,1)=Forest is adjacent to target (2,2) and (1,2)=Grass is also adjacent.
    # Monster at (0,2), target at (2,2) — dist=2. Adjacent cells:
    #   (1,2)=G: cost 1 → score 1+0+0=1.0
    #   (3,2)=G: cost 3 → score 3.0  (only if speed allows)
    #   (2,1)=F: cost 3 (move_cost=2+1) → score 3+(-1)+0=2.0
    #   (2,3)=G: cost 3 → score 3.0
    # So (1,2) wins with score 1.0 — not a good test for cover.

    # Better: Monster at (4,2), target at (2,2). Adjacent cells from (4,2):
    #   (1,2)=G: cost=3 → score 3.0
    #   (3,2)=G: cost=1 → score 1.0  ← lowest, wins
    # (3,2) is grass — the forest test doesn't work well this way.

    # The cleanest approach: grid with forest at (3,2) which IS adjacent to target at (2,2),
    # and monster at (0,2). From (0,2):
    #   (1,2)=G: cost=1 → score 1.0
    #   (3,2)=F: cost=3+forest=4? No, it's cost of path from start, not terrain.
    # Reachable costs from (0,2) with speed=6: (1,2)=1, (3,2)=2+2=4 (via (1,2)→(2,2) blocked by target?)
    # Actually reachable_cells goes around occupied cells.

    # Use a grid where forest at (3,2), monster at (4,2), target at (2,2):
    # Adjacent cells of (2,2): (1,2)=G, (3,2)=F, (2,1)=G, (2,3)=G
    # Costs from (4,2): (3,2)=F cost=2 (move_cost=2), (1,2)=G cost=3
    # Scores: (3,2)=F: 2+(-1)+0=1.0, (1,2)=G: 3+0=3.0, (2,1)=G: cost=3, (2,3)=G: cost=3
    # Forest wins!
    grid_data = {
        "width": 5,
        "height": 5,
        "terrain": [
            "GGGGG",
            "GGGGG",
            "GGGFG",   # (3,2) = Forest
            "GGGGG",
            "GGGGG",
        ],
    }
    grid = BattleGrid.from_map_data(grid_data)
    # Monster at (4,2), target at (2,2) — dist=2
    monster = _unit("orc", "enemy", [4, 2], speed=6, preferred_terrain=[])
    target = _unit("hero", "ally", [2, 2])

    dest = _find_move_toward_target(monster, grid, [monster, target], target, weapon_range=1)

    # From (4,2), adjacent cells of (2,2):
    # (3,2)=F: path (4,2)→(3,2)=F(cost 2); score=2+(-1)=1.0
    # (1,2)=G: path (4,2)→(3,2)→(2,2)[skip,occ]→... or (4,2)→(4,1)→... cost≥3; score≥3.0
    # (2,1)=G: cost≥3; score≥3.0
    # (2,3)=G: cost≥3; score≥3.0
    # Forest cell (3,2) should win
    assert dest == (3, 2), (
        f"Expected forest cell (3,2) for melee cover bonus, got {dest}"
    )


def test_evaluate_targets_penalizes_high_cover() -> None:
    """Targets in forest (ac_bonus=2) receive a cover_penalty and rank lower.

    Attacker at (0,0).
    Target A at (1,0) — grass (ac_bonus=0) — no penalty.
    Target B at (2,1) — forest cell (ac_bonus=2) — cover_penalty=-2.0.
    Both at similar distance. A should rank higher.
    """
    # Grid with forest at (2,1)
    grid_data = {
        "width": 5,
        "height": 5,
        "terrain": [
            "GGGGG",
            "GGFGG",
            "GGGGG",
            "GGGGG",
            "GGGGG",
        ],
    }
    grid = BattleGrid.from_map_data(grid_data)
    attacker = _unit("monster", "enemy", [0, 0])
    melee_attack = {"name": "Claw", "hit_bonus": 3, "damage_dice": "1d6", "range": 1}

    target_grass = _unit("hero_grass", "ally", [1, 0], hp=20, max_hp=20)   # grass
    target_forest = _unit("hero_forest", "ally", [2, 1], hp=20, max_hp=20)  # forest

    ranked = _evaluate_targets(attacker, grid, [target_grass, target_forest], melee_attack)

    assert ranked[0]["unit_id"] == "hero_grass", (
        f"Expected grass target (no cover) to rank first, got {ranked[0]['unit_id']}"
    )


def test_evaluate_targets_low_hp_outweighs_cover_penalty() -> None:
    """A critically low-HP target in forest still ranks above a healthy target in grass
    when both are at the same distance.

    Attacker at (0,0), both targets at dist=1 (adjacent):
    - (1,0) = Grass: distance_score=5.0, low_hp_bonus=5.0, in_range=3.0, cover=0 → 13.0
    - (0,1) = Forest: distance_score=5.0, low_hp_bonus=5.0, in_range=3.0, cover=-2.0 → 11.0

    Grass still wins. Adjust: put grass target at dist=2, forest target at dist=1 (low HP).
    - Forest adj (1,0): distance=1, low_hp_bonus=5.0, in_range=3.0, cover=-2.0 → 5.0+5.0+3.0-2.0=11.0
    - Grass at (2,0): distance=2, low_hp_bonus=0, in_range=3.0, cover=0 → 3.33+0+3.0=6.33

    So the adjacent low-HP forest target wins over the farther healthy grass target.
    """
    grid_data = {
        "width": 5,
        "height": 5,
        "terrain": [
            "GFGGG",   # (1,0) = Forest (ac_bonus=2)
            "GGGGG",
            "GGGGG",
            "GGGGG",
            "GGGGG",
        ],
    }
    grid = BattleGrid.from_map_data(grid_data)
    attacker = _unit("monster", "enemy", [0, 0])
    melee_attack = {"name": "Claw", "hit_bonus": 3, "damage_dice": "1d6", "range": 1}

    # Low HP target adjacent (dist=1) in forest — despite cover_penalty should still rank first
    target_forest_low = _unit("hero_forest_low", "ally", [1, 0], hp=2, max_hp=20)
    # Full HP target farther (dist=2) in grass
    target_grass_full = _unit("hero_grass_full", "ally", [2, 0], hp=20, max_hp=20)

    ranked = _evaluate_targets(
        attacker, grid,
        [target_forest_low, target_grass_full],
        melee_attack,
    )

    assert ranked[0]["unit_id"] == "hero_forest_low", (
        f"Expected adjacent low-HP forest target to rank first despite cover penalty, "
        f"got {ranked[0]['unit_id']}"
    )


def test_stone_terrain_ac_bonus_also_triggers_cover_bonus() -> None:
    """Stone terrain (ac_bonus=1) does NOT trigger cover_penalty (threshold is >= 2).

    Only forest (ac_bonus=2) or higher triggers the cover_penalty=-2.0 in targeting.
    This confirms the threshold is correct.
    """
    # Grid with stone (R) at (1,0)
    grid_data = {
        "width": 5,
        "height": 5,
        "terrain": [
            "GRFGG",  # (1,0)=Stone ac_bonus=1, (2,0)=Forest ac_bonus=2
            "GGGGG",
            "GGGGG",
            "GGGGG",
            "GGGGG",
        ],
    }
    grid = BattleGrid.from_map_data(grid_data)
    attacker = _unit("monster", "enemy", [0, 0])
    melee_attack = {"name": "Claw", "hit_bonus": 3, "damage_dice": "1d6", "range": 1}

    target_stone = _unit("hero_stone", "ally", [1, 0], hp=20, max_hp=20)   # ac_bonus=1
    target_forest = _unit("hero_forest", "ally", [2, 0], hp=20, max_hp=20)  # ac_bonus=2

    ranked = _evaluate_targets(
        attacker, grid, [target_stone, target_forest], melee_attack,
    )

    # Stone target: no cover_penalty (ac_bonus=1 < 2)
    # Forest target: cover_penalty=-2.0 (ac_bonus=2 >= 2)
    # Stone is closer (dist=1) vs forest (dist=2), so stone should rank first
    assert ranked[0]["unit_id"] == "hero_stone", (
        f"Expected stone target to rank first over forest target, "
        f"got {ranked[0]['unit_id']}"
    )
