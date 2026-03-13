"""BattleGrid: square-grid spatial model for SRPG combat.

Pure computation module — no state, no imports from application layer.
API parameter order: (col, row) matching unit position [col, row].
Internal storage: terrain[row][col] (row-major).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Environment modifiers (weather / time-of-day)
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class EnvironmentModifiers:
    """Compiled attack modifiers from current weather and time-of-day.

    All fields are additive modifiers applied to the attacker's attack total
    or used as constraints on line-of-sight distance.

    Attributes:
        hit_modifier:           Flat bonus/penalty applied to *all* attack rolls.
        ranged_hit_modifier:    Additional penalty applied only to ranged attacks
                                (stacked on top of hit_modifier).
        max_visibility:         Maximum LoS distance (cells).  None means no cap.
        damage_type_modifiers:  Tuple of (damage_type, multiplier) pairs that
                                scale outgoing damage by type.  E.g. rain reduces
                                fire damage to 0.5× and boosts thunder to 1.5×.
    """

    hit_modifier: int = 0
    ranged_hit_modifier: int = 0
    max_visibility: int | None = None
    damage_type_modifiers: tuple[tuple[str, float], ...] = ()

    def get_damage_modifier(self, damage_type: str) -> float:
        """Return the damage multiplier for the given damage type.

        Returns 1.0 when no modifier is registered for the type.
        """
        for dt, mod in self.damage_type_modifiers:
            if dt == damage_type:
                return mod
        return 1.0


def compute_environment_modifiers(
    weather: str,
    time_of_day: str,
) -> EnvironmentModifiers:
    """Compute attack modifiers from weather and time-of-day strings.

    Conditions and effects (stackable):

    | Condition          | Effect                                                  |
    |--------------------|---------------------------------------------------------|
    | weather="rain"     | hit_modifier=-1; fire×0.5, thunder×1.5                 |
    | weather="fog"      | hit_modifier=-2, max_visibility=3                       |
    | time_of_day="night"| ranged_hit_modifier=-2, max_visibility=4 (if not fog)  |
    | otherwise          | no modifier                                             |

    Multiple conditions stack independently.  For example, night + rain
    produces hit_modifier=-1, ranged_hit_modifier=-2, fire damage halved.
    """
    hit_mod = 0
    ranged_hit_mod = 0
    max_vis: int | None = None
    damage_mods: list[tuple[str, float]] = []

    if weather == "rain":
        hit_mod += -1
        damage_mods.extend([("fire", 0.5), ("thunder", 1.5)])
    elif weather == "fog":
        hit_mod += -2
        max_vis = 3

    if time_of_day == "night":
        ranged_hit_mod += -2
        # Night reduces visibility to 4 cells unless fog already set a tighter cap
        if max_vis is None or max_vis > 4:
            max_vis = 4

    return EnvironmentModifiers(
        hit_modifier=hit_mod,
        ranged_hit_modifier=ranged_hit_mod,
        max_visibility=max_vis,
        damage_type_modifiers=tuple(damage_mods),
    )


# ---------------------------------------------------------------------------
# Terrain types
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class TerrainType:
    """Immutable descriptor for a single terrain type."""

    code: str
    name: str
    move_cost: int      # 0 = impassable
    ac_bonus: int
    range_bonus: int
    blocks_los: bool
    speed_penalty: int
    damage_immunities: frozenset[str] = frozenset()  # damage types immune to on this terrain


#: Registry of all 9 terrain types, keyed by single-character code.
TERRAIN_REGISTRY: dict[str, TerrainType] = {
    "G": TerrainType(code="G", name="grass",         move_cost=1, ac_bonus=0, range_bonus=0, blocks_los=False, speed_penalty=0),
    "F": TerrainType(code="F", name="forest",        move_cost=2, ac_bonus=2, range_bonus=0, blocks_los=False, speed_penalty=0),
    "H": TerrainType(code="H", name="hill",          move_cost=2, ac_bonus=0, range_bonus=1, blocks_los=False, speed_penalty=0),
    "S": TerrainType(code="S", name="swamp",         move_cost=3, ac_bonus=0, range_bonus=0, blocks_los=False, speed_penalty=2),
    "W": TerrainType(code="W", name="water",         move_cost=0, ac_bonus=0, range_bonus=0, blocks_los=False, speed_penalty=0),
    "R": TerrainType(code="R", name="stone",         move_cost=1, ac_bonus=1, range_bonus=0, blocks_los=False, speed_penalty=0),
    "B": TerrainType(code="B", name="wall",          move_cost=0, ac_bonus=0, range_bonus=0, blocks_los=True,  speed_penalty=0),
    "M": TerrainType(code="M", name="mountain",      move_cost=0, ac_bonus=0, range_bonus=0, blocks_los=True,  speed_penalty=0),
    "D": TerrainType(code="D", name="shallow_water", move_cost=2, ac_bonus=0, range_bonus=0, blocks_los=False, speed_penalty=0,
                     damage_immunities=frozenset({"fire"})),
}

_FALLBACK_TERRAIN = TERRAIN_REGISTRY["G"]


# ---------------------------------------------------------------------------
# Grid coordinate alias
# ---------------------------------------------------------------------------

# A cell is (col, row) — matches unit position convention [col, row].
_Cell = tuple[int, int]


# ---------------------------------------------------------------------------
# BattleGrid
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BattleGrid:
    """Mutable square-grid spatial model.

    ``terrain`` is stored row-major: ``terrain[row][col]`` holds the terrain
    code string.  All public API methods accept ``(col, row)`` parameter order.
    """

    width: int
    height: int
    terrain: list[list[str]]  # [row][col] → terrain code

    # ------------------------------------------------------------------
    # Bounds & basic accessors
    # ------------------------------------------------------------------

    def is_in_bounds(self, col: int, row: int) -> bool:
        """Return True when (col, row) is within the grid."""
        return 0 <= col < self.width and 0 <= row < self.height

    def at(self, col: int, row: int) -> TerrainType:
        """Return the TerrainType at (col, row).

        Falls back to grass for unknown codes.  Out-of-bounds coordinates
        also return the fallback rather than raising.
        """
        if not self.is_in_bounds(col, row):
            return _FALLBACK_TERRAIN
        code = self.terrain[row][col]
        return TERRAIN_REGISTRY.get(code, _FALLBACK_TERRAIN)

    def is_passable(self, col: int, row: int) -> bool:
        """Return True when (col, row) is within bounds and passable."""
        if not self.is_in_bounds(col, row):
            return False
        return self.at(col, row).move_cost > 0

    def move_cost(self, col: int, row: int) -> int:
        """Return movement cost for (col, row), or 0 when out of bounds."""
        if not self.is_in_bounds(col, row):
            return 0
        return self.at(col, row).move_cost

    # ------------------------------------------------------------------
    # Unit queries
    # ------------------------------------------------------------------

    def occupied_by(
        self,
        col: int,
        row: int,
        units: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return the first alive unit at (col, row), or None.

        ``units`` is a list of dicts with keys ``position`` ([col, row])
        and ``alive`` (bool).  Dead units are ignored.
        """
        for unit in units:
            if not unit.get("alive", True):
                continue
            pos = unit.get("position", [])
            if len(pos) >= 2 and pos[0] == col and pos[1] == row:
                return unit
        return None

    # ------------------------------------------------------------------
    # Distance & range
    # ------------------------------------------------------------------

    def distance(self, a: _Cell, b: _Cell) -> int:
        """Manhattan distance between two cells."""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def cells_in_range(self, center: _Cell, range_: int) -> list[_Cell]:
        """Return all in-bounds cells within Manhattan distance *range_* of *center*.

        The center cell itself is included (distance 0).
        """
        cx, cy = center
        result: list[_Cell] = []
        for dc in range(-range_, range_ + 1):
            for dr in range(-range_, range_ + 1):
                if abs(dc) + abs(dr) <= range_:
                    col, row = cx + dc, cy + dr
                    if self.is_in_bounds(col, row):
                        result.append((col, row))
        return result

    # ------------------------------------------------------------------
    # Pathfinding helpers
    # ------------------------------------------------------------------

    def _is_blocked_by_unit(
        self,
        col: int,
        row: int,
        units: list[dict[str, Any]],
        side: str,
    ) -> bool:
        """Return True when an alive opposing-side unit occupies (col, row).

        ``side`` is the moving side ("ally" or "enemy").  The opposing side
        is "enemy" when side="ally", and "ally" when side="enemy".
        """
        opposing = "enemy" if side == "ally" else "ally"
        occupant = self.occupied_by(col, row, units)
        if occupant is None:
            return False
        return occupant.get("side") == opposing

    # ------------------------------------------------------------------
    # Reachable cells (Dijkstra / weighted BFS)
    # ------------------------------------------------------------------

    def reachable_cells(
        self,
        start: _Cell,
        move_points: int,
        units: list[dict[str, Any]] | None = None,
        side: str = "ally",
    ) -> dict[_Cell, int]:
        """Return all cells reachable within *move_points* movement points.

        The result maps each reachable cell to its minimum movement cost.
        The start cell is always included (cost 0).

        Enemy units (opposing side) block passage; friendly units do not.
        Dead units never block.
        """
        units = units or []
        dist: dict[_Cell, int] = {start: 0}
        # heap: (cost, col, row)
        heap: list[tuple[int, int, int]] = [(0, start[0], start[1])]

        while heap:
            cost, col, row = heapq.heappop(heap)
            if cost > dist.get((col, row), move_points + 1):
                continue
            for nc, nr in _cardinal_neighbors(col, row):
                if not self.is_in_bounds(nc, nr):
                    continue
                if not self.is_passable(nc, nr):
                    continue
                if self._is_blocked_by_unit(nc, nr, units, side):
                    continue
                terrain = self.at(nc, nr)
                new_cost = cost + terrain.move_cost + terrain.speed_penalty
                if new_cost > move_points:
                    continue
                if new_cost < dist.get((nc, nr), move_points + 1):
                    dist[(nc, nr)] = new_cost
                    heapq.heappush(heap, (new_cost, nc, nr))

        return dist

    # ------------------------------------------------------------------
    # Shortest path (A*)
    # ------------------------------------------------------------------

    def shortest_path(
        self,
        start: _Cell,
        goal: _Cell,
        units: list[dict[str, Any]] | None = None,
        side: str = "ally",
    ) -> list[_Cell] | None:
        """Return the shortest path from *start* to *goal* (inclusive), or None.

        Uses A* with Manhattan-distance heuristic.  Enemy units block; friendly
        units do not.  Returns None when no path exists.
        """
        units = units or []
        if not self.is_in_bounds(goal[0], goal[1]):
            return None
        if not self.is_passable(goal[0], goal[1]):
            return None

        # g_score: cost from start to cell
        g: dict[_Cell, int] = {start: 0}
        # came_from for path reconstruction
        came_from: dict[_Cell, _Cell] = {}
        # heap: (f, col, row)
        open_heap: list[tuple[int, int, int]] = [
            (self.distance(start, goal), start[0], start[1])
        ]

        while open_heap:
            _f, col, row = heapq.heappop(open_heap)
            current: _Cell = (col, row)

            if current == goal:
                return _reconstruct_path(came_from, goal)

            current_g = g.get(current, 10**9)

            for nc, nr in _cardinal_neighbors(col, row):
                neighbor: _Cell = (nc, nr)
                if not self.is_in_bounds(nc, nr):
                    continue
                if not self.is_passable(nc, nr):
                    continue
                # Blocking: only opposing units block intermediate cells.
                # The goal cell itself may be the destination even if it
                # contains a unit (e.g., to attack), so we only check
                # blocking for non-goal intermediate cells.
                if neighbor != goal and self._is_blocked_by_unit(nc, nr, units, side):
                    continue
                tentative_g = current_g + self.move_cost(nc, nr)
                if tentative_g < g.get(neighbor, 10**9):
                    g[neighbor] = tentative_g
                    came_from[neighbor] = current
                    f = tentative_g + self.distance(neighbor, goal)
                    heapq.heappush(open_heap, (f, nc, nr))

        return None

    # ------------------------------------------------------------------
    # Line of sight (Bresenham)
    # ------------------------------------------------------------------

    def line_of_sight(self, start: _Cell, end: _Cell) -> bool:
        """Return True when there is unobstructed line of sight from *start* to *end*.

        Uses Bresenham's line algorithm.  The start and end cells themselves
        are never treated as obstructions; only intermediate cells are checked
        for ``blocks_los``.
        """
        if start == end:
            return True

        x0, y0 = start
        x1, y1 = end
        cells = _bresenham_cells(x0, y0, x1, y1)

        # Exclude start and end; check only intermediate cells.
        for col, row in cells[1:-1]:
            if not self.is_in_bounds(col, row):
                return False
            if self.at(col, row).blocks_los:
                return False
        return True

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_map_data(cls, map_data: dict[str, Any]) -> BattleGrid:
        """Construct a BattleGrid from a JSON-compatible map descriptor.

        Expected format::

            {
                "width": 5,
                "height": 4,
                "terrain": [
                    "GGGGG",
                    "GFGGG",
                    "GGGGG",
                    "GGGWG",
                ]
            }

        Each string in ``terrain`` represents one row, with each character
        being a terrain code.  Raises ``ValueError`` on dimension mismatch.
        """
        width: int = int(map_data["width"])
        height: int = int(map_data["height"])
        raw_rows: list[str] = list(map_data["terrain"])

        if len(raw_rows) != height:
            raise ValueError(
                f"terrain row count {len(raw_rows)} does not match height {height}"
            )
        terrain: list[list[str]] = []
        for idx, row_str in enumerate(raw_rows):
            if len(row_str) != width:
                raise ValueError(
                    f"terrain row {idx} has {len(row_str)} columns, expected {width}"
                )
            terrain.append(list(row_str))

        return cls(width=width, height=height, terrain=terrain)


# ---------------------------------------------------------------------------
# Module-level helpers (private)
# ---------------------------------------------------------------------------

def _cardinal_neighbors(col: int, row: int) -> list[tuple[int, int]]:
    """Return the four cardinal neighbors of (col, row)."""
    return [
        (col - 1, row),
        (col + 1, row),
        (col, row - 1),
        (col, row + 1),
    ]


def _reconstruct_path(
    came_from: dict[_Cell, _Cell],
    goal: _Cell,
) -> list[_Cell]:
    """Reconstruct a path by following came_from back to origin."""
    path: list[_Cell] = [goal]
    current = goal
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def _bresenham_cells(x0: int, y0: int, x1: int, y1: int) -> list[_Cell]:
    """Return all cells on the Bresenham line from (x0,y0) to (x1,y1)."""
    cells: list[_Cell] = []
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        cells.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
    return cells
