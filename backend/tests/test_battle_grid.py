"""Tests for BattleGrid — spatial model for SRPG combat.

All tests are synchronous (pure computation, no async needed).
"""

from __future__ import annotations

import pytest

from app.game_core.rules.battle_grid import (
    BattleGrid,
    EnvironmentModifiers,
    TerrainType,
    TERRAIN_REGISTRY,
    compute_environment_modifiers,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_grid(terrain_strings: list[str]) -> BattleGrid:
    """Construct a BattleGrid from a list of row strings.

    Each string is one row; each character is a terrain code.
    """
    height = len(terrain_strings)
    width = max(len(s) for s in terrain_strings) if terrain_strings else 0
    terrain = [list(row) for row in terrain_strings]
    return BattleGrid(width=width, height=height, terrain=terrain)


def _make_unit(
    unit_id: str,
    col: int,
    row: int,
    side: str = "enemy",
    alive: bool = True,
) -> dict:
    """Build a minimal unit dict compatible with BattleGrid queries."""
    return {
        "id": unit_id,
        "position": [col, row],
        "side": side,
        "alive": alive,
    }


# ---------------------------------------------------------------------------
# Terrain registry tests
# ---------------------------------------------------------------------------

def test_registry_has_all_nine_types():
    codes = set(TERRAIN_REGISTRY.keys())
    assert codes == {"G", "F", "H", "S", "W", "R", "B", "M", "D"}


def test_terrain_type_is_frozen():
    t = TERRAIN_REGISTRY["G"]
    with pytest.raises((AttributeError, TypeError)):
        t.move_cost = 99  # type: ignore[misc]


def test_impassable_terrains_zero_move_cost():
    for code in ("W", "B", "M"):
        assert TERRAIN_REGISTRY[code].move_cost == 0, f"{code} should be impassable"


# ---------------------------------------------------------------------------
# BattleGrid — basic accessors
# ---------------------------------------------------------------------------

def test_is_in_bounds_valid():
    grid = _make_grid(["GGG", "GGG"])
    assert grid.is_in_bounds(0, 0)
    assert grid.is_in_bounds(2, 1)


def test_is_in_bounds_invalid():
    grid = _make_grid(["GGG", "GGG"])
    assert not grid.is_in_bounds(-1, 0)
    assert not grid.is_in_bounds(3, 0)
    assert not grid.is_in_bounds(0, 2)
    assert not grid.is_in_bounds(0, -1)


def test_at_returns_correct_terrain():
    grid = _make_grid(["GFG", "GHW"])
    assert grid.at(0, 0).code == "G"
    assert grid.at(1, 0).code == "F"
    assert grid.at(1, 1).code == "H"
    assert grid.at(2, 1).code == "W"


def test_at_unknown_code_fallback():
    grid = BattleGrid(width=1, height=1, terrain=[["Z"]])
    t = grid.at(0, 0)
    assert t.code == "G", "Unknown code should fall back to grass"


def test_is_passable_and_move_cost():
    grid = _make_grid(["GW", "FG"])
    # G is passable, move_cost=1
    assert grid.is_passable(0, 0)
    assert grid.move_cost(0, 0) == 1
    # W is impassable
    assert not grid.is_passable(1, 0)
    assert grid.move_cost(1, 0) == 0
    # F move_cost=2
    assert grid.is_passable(0, 1)
    assert grid.move_cost(0, 1) == 2
    # Out-of-bounds
    assert not grid.is_passable(5, 5)
    assert grid.move_cost(5, 5) == 0


def test_occupied_by():
    grid = _make_grid(["GGG"])
    units = [
        _make_unit("a", 0, 0, alive=True),
        _make_unit("b", 1, 0, alive=False),
        _make_unit("c", 2, 0, alive=True),
    ]
    assert grid.occupied_by(0, 0, units)["id"] == "a"
    # Dead unit at (1,0) should not be returned
    assert grid.occupied_by(1, 0, units) is None
    assert grid.occupied_by(2, 0, units)["id"] == "c"
    # Empty cell
    assert grid.occupied_by(0, 0, []) is None


# ---------------------------------------------------------------------------
# Distance & range
# ---------------------------------------------------------------------------

def test_manhattan_distance():
    grid = _make_grid(["GGGGG"] * 5)
    assert grid.distance((0, 0), (0, 0)) == 0
    assert grid.distance((0, 0), (3, 0)) == 3
    assert grid.distance((0, 0), (0, 4)) == 4
    assert grid.distance((1, 1), (3, 3)) == 4
    assert grid.distance((2, 2), (0, 0)) == 4


def test_cells_in_range():
    grid = _make_grid(["GGG"] * 3)
    cells = grid.cells_in_range((1, 1), 1)
    # Center + 4 cardinal neighbors = 5 cells on 3x3 grid
    assert (1, 1) in cells
    assert (0, 1) in cells
    assert (2, 1) in cells
    assert (1, 0) in cells
    assert (1, 2) in cells
    assert len(cells) == 5


def test_cells_in_range_near_border():
    grid = _make_grid(["GGG"] * 3)
    cells = grid.cells_in_range((0, 0), 2)
    # Out-of-bounds cells are excluded
    for col, row in cells:
        assert grid.is_in_bounds(col, row)
    # (0,0), (1,0), (0,1), (2,0), (1,1), (0,2) → 6 cells
    assert (0, 0) in cells
    assert (2, 0) in cells
    assert (0, 2) in cells


# ---------------------------------------------------------------------------
# BFS — reachable_cells
# ---------------------------------------------------------------------------

def test_reachable_open_grass():
    # 5x5 all-grass, move_points=4; starting at (2,2)
    grid = _make_grid(["GGGGG"] * 5)
    reachable = grid.reachable_cells((2, 2), 4)
    # Starting cell always included
    assert (2, 2) in reachable
    assert reachable[(2, 2)] == 0
    # Corners are distance 4 away
    assert (0, 2) in reachable  # left 4 steps
    assert (4, 2) in reachable  # right 4 steps
    assert (2, 0) in reachable  # up 4 steps
    assert (2, 4) in reachable  # down 4 steps
    # All cells with Manhattan distance <= 4 from (2,2) and in bounds
    for (c, r), cost in reachable.items():
        assert cost <= 4


def test_reachable_terrain_cost():
    # Row of cells: G F G, move_points=2
    # Cost to reach (1,0) from (0,0): forest costs 2, so barely reachable
    # (2,0) would cost 3 (G→F→G = 1+2+1), exceeds move_points
    grid = _make_grid(["GFG"])
    reachable = grid.reachable_cells((0, 0), 2)
    assert (0, 0) in reachable
    assert (1, 0) in reachable
    assert reachable[(1, 0)] == 2
    assert (2, 0) not in reachable


def test_reachable_impassable_blocks():
    # G W G — water in middle
    grid = _make_grid(["GWG"])
    reachable = grid.reachable_cells((0, 0), 10)
    assert (0, 0) in reachable
    assert (1, 0) not in reachable   # water
    assert (2, 0) not in reachable   # behind water, unreachable


def test_reachable_enemy_blocks():
    grid = _make_grid(["GGG"])
    enemy = _make_unit("e1", 1, 0, side="enemy", alive=True)
    reachable = grid.reachable_cells((0, 0), 5, units=[enemy], side="ally")
    assert (0, 0) in reachable
    # Enemy at (1,0) blocks passage; (2,0) becomes unreachable
    assert (1, 0) not in reachable
    assert (2, 0) not in reachable


def test_reachable_ally_does_not_block():
    grid = _make_grid(["GGG"])
    ally = _make_unit("a1", 1, 0, side="ally", alive=True)
    reachable = grid.reachable_cells((0, 0), 5, units=[ally], side="ally")
    # Ally does not block
    assert (1, 0) in reachable
    assert (2, 0) in reachable


def test_reachable_dead_enemy_does_not_block():
    grid = _make_grid(["GGG"])
    dead_enemy = _make_unit("e1", 1, 0, side="enemy", alive=False)
    reachable = grid.reachable_cells((0, 0), 5, units=[dead_enemy], side="ally")
    # Dead enemy does not block
    assert (1, 0) in reachable
    assert (2, 0) in reachable


# ---------------------------------------------------------------------------
# A* — shortest_path
# ---------------------------------------------------------------------------

def test_shortest_path_straight_line():
    grid = _make_grid(["GGGGG"])
    path = grid.shortest_path((0, 0), (4, 0))
    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (4, 0)
    assert len(path) == 5


def test_shortest_path_around_wall():
    # Layout (3 rows x 5 cols):
    # G G G G G
    # G B B B G
    # G G G G G
    # Start (0,1), goal (4,1). Must go around the wall row.
    grid = _make_grid([
        "GGGGG",
        "GBBBG",
        "GGGGG",
    ])
    path = grid.shortest_path((0, 1), (4, 1))
    assert path is not None
    assert path[0] == (0, 1)
    assert path[-1] == (4, 1)
    # Path must go through row 0 or row 2 to bypass the wall
    mid_cells = set(path[1:-1])
    has_detour = any(r != 1 for _, r in mid_cells)
    assert has_detour, "Path must detour around the wall"


def test_shortest_path_no_route():
    # Completely walled off
    grid = _make_grid([
        "GBG",
        "BBB",
        "GBG",
    ])
    path = grid.shortest_path((0, 0), (2, 2))
    assert path is None


def test_shortest_path_prefers_low_cost():
    # Two routes from (0,0) to (2,0):
    # Direct:  G F G  cost = 1 + 2 = 3 (through forest)
    # Detour:  via (0,1),(1,1),(2,1),(2,0) = 1+1+1+1 = 4 on all-grass lower row
    # Wait — 2 rows:
    # Row 0: G F G
    # Row 1: G G G
    # Direct route through forest: cost = move_cost(1,0)=2 + move_cost(2,0)=1 → total 3
    # Detour (0,1)→(1,1)→(2,1)→(2,0): cost = 1+1+1+1 → total 4
    # A* should pick the direct path (cost 3).
    grid = _make_grid([
        "GFG",
        "GGG",
    ])
    path = grid.shortest_path((0, 0), (2, 0))
    assert path is not None
    # Verify path goes directly through (1,0) — the lower-cost route
    assert (1, 0) in path


def test_shortest_path_enemy_blocks():
    grid = _make_grid(["GGGGG"])
    enemy = _make_unit("e1", 2, 0, side="enemy", alive=True)
    # Enemy at (2,0) blocks the straight line; no detour possible on 1 row
    path = grid.shortest_path((0, 0), (4, 0), units=[enemy], side="ally")
    # With only 1 row and an enemy blocking at (2,0), no path exists
    assert path is None


# ---------------------------------------------------------------------------
# Line of sight (Bresenham)
# ---------------------------------------------------------------------------

def test_los_clear():
    grid = _make_grid(["GGGGG"])
    assert grid.line_of_sight((0, 0), (4, 0))


def test_los_blocked_by_wall():
    # G B G
    grid = _make_grid(["GBG"])
    assert not grid.line_of_sight((0, 0), (2, 0))


def test_los_blocked_by_mountain():
    # G M G
    grid = _make_grid(["GMG"])
    assert not grid.line_of_sight((0, 0), (2, 0))


def test_los_same_cell():
    grid = _make_grid(["GGG"])
    assert grid.line_of_sight((1, 0), (1, 0))


def test_los_start_and_end_not_blocked():
    # Start and end cells are walls — LoS checks only intermediate cells
    grid = _make_grid(["BGG", "GGG", "GGB"])
    # No intermediate wall between (0,0) and (2,2); diagonal path via Bresenham
    # The start (0,0)=B and end (2,2)=B are excluded from check
    # This verifies that start/end terrain does not affect LoS
    assert grid.line_of_sight((0, 0), (2, 2))


# ---------------------------------------------------------------------------
# from_map_data
# ---------------------------------------------------------------------------

def test_from_map_data_valid():
    data = {
        "width": 3,
        "height": 2,
        "terrain": ["GFG", "GHW"],
    }
    grid = BattleGrid.from_map_data(data)
    assert grid.width == 3
    assert grid.height == 2
    assert grid.at(1, 0).code == "F"
    assert grid.at(1, 1).code == "H"
    assert grid.at(2, 1).code == "W"


def test_from_map_data_dimension_mismatch():
    # Row count doesn't match height
    data = {
        "width": 3,
        "height": 4,
        "terrain": ["GGG", "GGG"],  # only 2 rows, height says 4
    }
    with pytest.raises(ValueError):
        BattleGrid.from_map_data(data)


def test_from_map_data_column_mismatch():
    # A row has wrong number of columns
    data = {
        "width": 3,
        "height": 2,
        "terrain": ["GGG", "GG"],  # second row only 2 chars
    }
    with pytest.raises(ValueError):
        BattleGrid.from_map_data(data)


def test_from_map_data_preserves_terrain_codes():
    codes = "GFHSWRBMD"
    data = {
        "width": len(codes),
        "height": 1,
        "terrain": [codes],
    }
    grid = BattleGrid.from_map_data(data)
    for col, code in enumerate(codes):
        assert grid.at(col, 0).code == code


# ---------------------------------------------------------------------------
# F-2: speed_penalty consumed in reachable_cells
# ---------------------------------------------------------------------------

def test_speed_penalty_reduces_reachable_range():
    """Swamp (move_cost=3, speed_penalty=2) should cost 5 total per step."""
    # Row: G S G — entering swamp at (1,0) from (0,0) should cost 3+2=5
    grid = _make_grid(["GSG"])
    # With move_points=4: cannot reach swamp cell (cost 5 > 4)
    reachable = grid.reachable_cells((0, 0), 4)
    assert (0, 0) in reachable
    assert (1, 0) not in reachable, "Swamp at cost 5 should be out of range with 4 move_points"
    assert (2, 0) not in reachable

def test_speed_penalty_reachable_with_sufficient_points():
    """With 5 move_points, the swamp cell should be reachable."""
    grid = _make_grid(["GSG"])
    reachable = grid.reachable_cells((0, 0), 5)
    assert (1, 0) in reachable
    assert reachable[(1, 0)] == 5  # 3 move_cost + 2 speed_penalty


# ---------------------------------------------------------------------------
# F-3: shallow_water terrain (D)
# ---------------------------------------------------------------------------

def test_shallow_water_is_passable():
    """Shallow water (D) is passable with move_cost=2."""
    t = TERRAIN_REGISTRY["D"]
    assert t.move_cost == 2
    assert t.name == "shallow_water"
    assert not t.blocks_los


def test_shallow_water_fire_immune():
    """Shallow water terrain grants fire immunity."""
    t = TERRAIN_REGISTRY["D"]
    assert "fire" in t.damage_immunities


def test_shallow_water_no_thunder_immunity():
    """Shallow water does NOT grant immunity to non-fire damage types."""
    t = TERRAIN_REGISTRY["D"]
    assert "thunder" not in t.damage_immunities
    assert "physical" not in t.damage_immunities


def test_terrain_type_damage_immunities_default_empty():
    """All non-shallow-water terrains default to empty immunities."""
    for code, t in TERRAIN_REGISTRY.items():
        if code != "D":
            assert t.damage_immunities == frozenset(), (
                f"{code} should have no immunities by default"
            )


def test_terrain_type_frozen_immunities():
    """TerrainType is frozen; damage_immunities is a frozenset (immutable)."""
    t = TERRAIN_REGISTRY["D"]
    assert isinstance(t.damage_immunities, frozenset)
    with pytest.raises((AttributeError, TypeError)):
        t.damage_immunities = frozenset()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# F-4: night max_visibility = 4
# ---------------------------------------------------------------------------

def test_night_sets_max_visibility_4():
    """Night time should produce max_visibility=4."""
    mods = compute_environment_modifiers(weather="clear", time_of_day="night")
    assert mods.max_visibility == 4


def test_night_also_sets_ranged_hit_penalty():
    """Night still applies ranged_hit_modifier=-2."""
    mods = compute_environment_modifiers(weather="clear", time_of_day="night")
    assert mods.ranged_hit_modifier == -2


def test_fog_max_visibility_tighter_than_night():
    """Fog (max_visibility=3) should override night (4) since 3 < 4."""
    mods = compute_environment_modifiers(weather="fog", time_of_day="night")
    assert mods.max_visibility == 3  # fog is tighter


def test_day_no_max_visibility():
    """Daytime with no weather should have no visibility cap."""
    mods = compute_environment_modifiers(weather="clear", time_of_day="day")
    assert mods.max_visibility is None


# ---------------------------------------------------------------------------
# F-5: damage_type_modifiers in EnvironmentModifiers
# ---------------------------------------------------------------------------

def test_rain_fire_damage_half():
    """Rain reduces fire damage by 50%."""
    mods = compute_environment_modifiers(weather="rain", time_of_day="day")
    assert mods.get_damage_modifier("fire") == 0.5


def test_rain_thunder_damage_boosted():
    """Rain boosts thunder damage by 150%."""
    mods = compute_environment_modifiers(weather="rain", time_of_day="day")
    assert mods.get_damage_modifier("thunder") == 1.5


def test_no_rain_no_damage_modifier():
    """Without rain, get_damage_modifier returns 1.0 for all types."""
    mods = compute_environment_modifiers(weather="clear", time_of_day="day")
    assert mods.get_damage_modifier("fire") == 1.0
    assert mods.get_damage_modifier("thunder") == 1.0
    assert mods.get_damage_modifier("physical") == 1.0


def test_environment_modifiers_is_frozen():
    """EnvironmentModifiers is a frozen dataclass."""
    mods = EnvironmentModifiers()
    with pytest.raises((AttributeError, TypeError)):
        mods.hit_modifier = 5  # type: ignore[misc]


def test_damage_type_modifiers_default_empty():
    """Default EnvironmentModifiers has no damage type modifiers."""
    mods = EnvironmentModifiers()
    assert mods.damage_type_modifiers == ()
    assert mods.get_damage_modifier("fire") == 1.0


def test_night_rain_stacks_modifiers():
    """Night + rain should stack both ranged penalty and fire/thunder modifiers."""
    mods = compute_environment_modifiers(weather="rain", time_of_day="night")
    assert mods.hit_modifier == -1
    assert mods.ranged_hit_modifier == -2
    assert mods.get_damage_modifier("fire") == 0.5
    assert mods.get_damage_modifier("thunder") == 1.5
    assert mods.max_visibility == 4  # night sets it since rain doesn't
