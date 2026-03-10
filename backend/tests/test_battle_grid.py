"""Tests for BattleGrid — spatial model for SRPG combat.

All tests are synchronous (pure computation, no async needed).
"""

from __future__ import annotations

import pytest

from app.game_core.rules.battle_grid import (
    BattleGrid,
    TerrainType,
    TERRAIN_REGISTRY,
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

def test_registry_has_all_eight_types():
    codes = set(TERRAIN_REGISTRY.keys())
    assert codes == {"G", "F", "H", "S", "W", "R", "B", "M"}


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
    codes = "GFHSWRBM"
    data = {
        "width": len(codes),
        "height": 1,
        "terrain": [codes],
    }
    grid = BattleGrid.from_map_data(data)
    for col, code in enumerate(codes):
        assert grid.at(col, 0).code == code
