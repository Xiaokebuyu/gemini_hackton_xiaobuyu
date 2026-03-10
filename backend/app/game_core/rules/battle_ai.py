"""Monster AI decision module for SRPG combat.

Pure computation module — no state, no side effects.
Only imports: stdlib + battle_grid.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from app.game_core.rules.battle_grid import BattleGrid, TERRAIN_REGISTRY


# ---------------------------------------------------------------------------
# MonsterDecision
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MonsterDecision:
    """Encapsulates the AI decision for a single monster turn."""

    move_to: tuple[int, int] | None  # movement target cell, None = stay
    action: str                       # "attack" | "flee" | "defend" | "hold"
    target_id: str | None             # unit_id of attack target (if action="attack")
    attack_index: int                 # index into unit["attacks"] list


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_decision(
    decision: MonsterDecision,
    unit: dict[str, Any],
    grid: BattleGrid,
    all_units: list[dict[str, Any]],
) -> bool:
    """Validate an externally-supplied MonsterDecision against current grid state.

    Returns True only when all the following hold:
    - action is one of {"attack", "flee", "defend", "hold"}
    - move_to (if set) is within the unit's reachable cells this turn
    - for action="attack": target exists and is alive on opposing side,
      attack_index is valid, and the attack position is in range (+LoS for ranged)
    """
    # action must be a known value
    valid_actions = {"attack", "flee", "defend", "hold"}
    if decision.action not in valid_actions:
        return False

    my_pos: tuple[int, int] = tuple(unit["position"])  # type: ignore[assignment]
    my_side = str(unit.get("side", "enemy"))

    # --- move_to validation ---
    effective_pos = my_pos
    if decision.move_to is not None:
        # Compute reachable cells accounting for dash
        base_speed = int(unit.get("speed", 6))
        effective_speed = base_speed * (2 if unit.get("dashed") else 1)
        reachable = grid.reachable_cells(
            start=my_pos,
            move_points=effective_speed,
            units=all_units,
            side=my_side,
        )
        if decision.move_to not in reachable:
            return False
        effective_pos = decision.move_to

    # --- attack validation ---
    if decision.action == "attack":
        if decision.target_id is None:
            return False

        # Locate target
        target = _find_unit(all_units, decision.target_id)
        if target is None:
            return False

        # Target must be alive and on the opposing side
        if not _is_active(target):
            return False
        opposing_side = "ally" if my_side == "enemy" else "enemy"
        if target.get("side") != opposing_side:
            return False

        # attack_index must be within bounds
        attacks = unit.get("attacks", [])
        if decision.attack_index < 0 or decision.attack_index >= len(attacks):
            return False

        atk = attacks[decision.attack_index]
        weapon_range = int(atk.get("range", 1))
        t_pos: tuple[int, int] = tuple(target["position"])  # type: ignore[assignment]

        # Distance from effective position (after move) to target
        dist = grid.distance(effective_pos, t_pos)
        pos_terrain = grid.at(effective_pos[0], effective_pos[1])
        in_range = dist <= weapon_range + pos_terrain.range_bonus
        if not in_range:
            return False

        # Ranged attacks require line-of-sight
        if weapon_range > 1 and not grid.line_of_sight(effective_pos, t_pos):
            return False

    return True


def decide_monster_turn(
    unit: dict[str, Any],
    grid: BattleGrid,
    all_units: list[dict[str, Any]],
) -> MonsterDecision:
    """Compute an AI decision for the given monster unit.

    Decision tree:
    1. Should flee? → move away + action="flee"
    2. No valid targets? → hold
    3. Pick best attack via personality
    4. Evaluate and sort targets
    5. For each target (priority order):
       a. Already in range + LoS? → attack without moving
       b. Can move into range? → move + attack
       c. Can move closer? → move + hold
    6. Fallback → hold
    """
    personality = str(unit.get("personality", "aggressive"))

    # Step 1: flee check
    if _should_flee(unit):
        flee_dest = _find_flee_destination(unit, grid, all_units)
        return MonsterDecision(
            move_to=flee_dest,
            action="flee",
            target_id=None,
            attack_index=0,
        )

    # Step 2: find opposing side targets
    my_side = str(unit.get("side", "enemy"))
    opposing_side = "ally" if my_side == "enemy" else "enemy"
    targets = [
        u for u in all_units
        if u.get("side") == opposing_side and _is_active(u)
    ]
    if not targets:
        return MonsterDecision(move_to=None, action="hold", target_id=None, attack_index=0)

    # Step 3: choose attack
    attacks = unit.get("attacks", [])
    if not attacks:
        return MonsterDecision(move_to=None, action="hold", target_id=None, attack_index=0)
    attack_idx = _select_attack_index(attacks, personality)
    chosen_attack = attacks[attack_idx]
    weapon_range = int(chosen_attack.get("range", 1))

    # Step 4: evaluate and sort targets
    ranked_targets = _evaluate_targets(unit, grid, targets, chosen_attack)

    # Step 5: iterate targets by priority
    my_pos = tuple(unit["position"])
    my_speed = int(unit.get("speed", 6))
    reachable = grid.reachable_cells(
        start=my_pos,  # type: ignore[arg-type]
        move_points=my_speed,
        units=all_units,
        side=my_side,
    )

    for target in ranked_targets:
        t_pos: tuple[int, int] = tuple(target["position"])  # type: ignore[assignment]
        dist = grid.distance(my_pos, t_pos)  # type: ignore[arg-type]
        pos_terrain = grid.at(my_pos[0], my_pos[1])
        effective_range = weapon_range + pos_terrain.range_bonus
        has_los = weapon_range <= 1 or grid.line_of_sight(my_pos, t_pos)

        # 5a: already in range and has LoS
        if dist <= effective_range and has_los:
            return MonsterDecision(
                move_to=None,
                action="attack",
                target_id=str(target["unit_id"]),
                attack_index=attack_idx,
            )

        # 5b/c: try to move
        move_dest = _find_move_toward_target(
            unit, grid, all_units, target, weapon_range
        )
        if move_dest is not None:
            # Check if after moving we can attack
            dest_terrain = grid.at(move_dest[0], move_dest[1])
            effective_range_at_dest = weapon_range + dest_terrain.range_bonus
            dist_after_move = grid.distance(move_dest, t_pos)
            has_los_after = weapon_range <= 1 or grid.line_of_sight(move_dest, t_pos)

            if dist_after_move <= effective_range_at_dest and has_los_after:
                # 5b: move + attack
                return MonsterDecision(
                    move_to=move_dest,
                    action="attack",
                    target_id=str(target["unit_id"]),
                    attack_index=attack_idx,
                )
            else:
                # 5c: move closer + hold (try next target first, but default to this)
                # For now, move toward highest-priority target and hold
                return MonsterDecision(
                    move_to=move_dest,
                    action="hold",
                    target_id=None,
                    attack_index=attack_idx,
                )

    # Step 6: fallback
    return MonsterDecision(move_to=None, action="hold", target_id=None, attack_index=0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_active(unit: dict[str, Any]) -> bool:
    """Return True when a unit is alive and not fled."""
    return bool(unit.get("alive", True)) and not bool(unit.get("fled", False))


def _find_unit(units: list[dict[str, Any]], uid: str) -> dict[str, Any] | None:
    """Return the first unit matching uid, or None."""
    for u in units:
        if u.get("unit_id") == uid:
            return u
    return None


def _should_flee(unit: dict[str, Any]) -> bool:
    """Return True when the unit should attempt to flee.

    Flee is triggered when HP ratio falls below the personality-specific
    threshold AND a probability check passes (cowardly monsters flee more
    readily than aggressive ones).
    """
    personality = str(unit.get("personality", "aggressive"))
    hp = int(unit.get("hp", 1))
    max_hp = int(unit.get("max_hp", 1))
    if max_hp <= 0:
        return False
    hp_ratio = hp / max_hp

    flee_threshold: float
    flee_chance: float
    if personality == "cowardly":
        flee_threshold = 0.5
        flee_chance = 0.8
    elif personality == "defensive":
        flee_threshold = 0.25
        flee_chance = 0.5
    else:
        # aggressive (default) — rarely flees
        flee_threshold = 0.1
        flee_chance = 0.2

    if hp_ratio >= flee_threshold:
        return False

    return random.random() < flee_chance


def _find_flee_destination(
    unit: dict[str, Any],
    grid: BattleGrid,
    all_units: list[dict[str, Any]],
) -> tuple[int, int] | None:
    """Return the reachable cell that maximizes distance from all enemies.

    Returns None when no movement is available.
    """
    my_side = str(unit.get("side", "enemy"))
    opposing_side = "ally" if my_side == "enemy" else "enemy"
    my_pos: tuple[int, int] = tuple(unit["position"])  # type: ignore[assignment]
    my_speed = int(unit.get("speed", 6))

    enemies = [u for u in all_units if u.get("side") == opposing_side and _is_active(u)]
    reachable = grid.reachable_cells(
        start=my_pos,
        move_points=my_speed,
        units=all_units,
        side=my_side,
    )

    if not reachable or not enemies:
        return None

    # Find cell that maximizes minimum distance to all enemies
    best_cell: tuple[int, int] | None = None
    best_min_dist = -1

    for cell in reachable:
        if cell == my_pos:
            continue
        min_dist_to_enemy = min(
            grid.distance(cell, tuple(e["position"]))  # type: ignore[arg-type]
            for e in enemies
        )
        if min_dist_to_enemy > best_min_dist:
            best_min_dist = min_dist_to_enemy
            best_cell = cell

    return best_cell


def _select_attack_index(attacks: list[dict[str, Any]], personality: str) -> int:
    """Choose the attack index based on personality.

    - aggressive: maximize expected damage
    - defensive:  maximize hit_bonus (most reliable hit)
    - cowardly:   maximize range (stay as far as possible)
    """
    if not attacks:
        return 0

    if personality == "aggressive":
        best = max(range(len(attacks)), key=lambda i: _estimate_damage(attacks[i].get("damage_dice", "1d4")))
    elif personality == "defensive":
        best = max(range(len(attacks)), key=lambda i: int(attacks[i].get("hit_bonus", 0)))
    elif personality == "cowardly":
        best = max(range(len(attacks)), key=lambda i: int(attacks[i].get("range", 1)))
    else:
        # aggressive is the default
        best = max(range(len(attacks)), key=lambda i: _estimate_damage(attacks[i].get("damage_dice", "1d4")))

    return best


def _estimate_damage(dice_str: str) -> float:
    """Return the expected average damage for a dice expression like '2d6'.

    Supports optional bonus, e.g. '1d6+3'. Returns 0.0 on parse error.
    """
    try:
        # Strip spaces
        s = str(dice_str).replace(" ", "").lower()
        # Split off bonus
        bonus = 0
        if "+" in s:
            parts = s.split("+", 1)
            s = parts[0]
            bonus = int(parts[1])
        elif "-" in s:
            parts = s.split("-", 1)
            s = parts[0]
            bonus = -int(parts[1])

        if "d" not in s:
            return float(int(s))

        count_str, sides_str = s.split("d", 1)
        count = int(count_str) if count_str else 1
        sides = int(sides_str)
        # Expected value of NdX = N * (X+1) / 2
        return count * (sides + 1) / 2.0 + bonus
    except (ValueError, AttributeError):
        return 0.0


def _evaluate_targets(
    unit: dict[str, Any],
    grid: BattleGrid,
    targets: list[dict[str, Any]],
    attack: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return targets sorted by attack priority (highest score first).

    Score = distance_score + low_hp_bonus + in_range_bonus

    - distance_score: 10 / (distance + 1)  — closer is better
    - low_hp_bonus: 5 if target HP < 30% max_hp — focus low-HP targets
    - in_range_bonus: 3 if already in weapon range — prefer targets we can
      attack immediately
    """
    my_pos: tuple[int, int] = tuple(unit["position"])  # type: ignore[assignment]
    weapon_range = int(attack.get("range", 1))
    pos_terrain = grid.at(my_pos[0], my_pos[1])
    effective_range = weapon_range + pos_terrain.range_bonus

    def _score(t: dict[str, Any]) -> float:
        t_pos: tuple[int, int] = tuple(t["position"])  # type: ignore[assignment]
        dist = grid.distance(my_pos, t_pos)
        distance_score = 10.0 / (dist + 1)

        hp = int(t.get("hp", 1))
        max_hp = int(t.get("max_hp", 1))
        hp_ratio = hp / max_hp if max_hp > 0 else 1.0
        low_hp_bonus = 5.0 if hp_ratio < 0.3 else 0.0

        in_range_bonus = 3.0 if dist <= effective_range else 0.0

        return distance_score + low_hp_bonus + in_range_bonus

    return sorted(targets, key=_score, reverse=True)


def _find_move_toward_target(
    unit: dict[str, Any],
    grid: BattleGrid,
    all_units: list[dict[str, Any]],
    target: dict[str, Any],
    weapon_range: int,
) -> tuple[int, int] | None:
    """Return the best reachable cell to move toward a target.

    For melee attacks (range <= 1): move to the cell adjacent to the target
    that is reachable within the unit's speed.

    For ranged attacks (range > 1): move to a cell within weapon range of the
    target; among those cells, prefer ones with a hill terrain (range_bonus).

    Returns None if no suitable cell is reachable.
    """
    my_side = str(unit.get("side", "enemy"))
    my_pos: tuple[int, int] = tuple(unit["position"])  # type: ignore[assignment]
    my_speed = int(unit.get("speed", 6))
    t_pos: tuple[int, int] = tuple(target["position"])  # type: ignore[assignment]

    reachable = grid.reachable_cells(
        start=my_pos,
        move_points=my_speed,
        units=all_units,
        side=my_side,
    )

    if weapon_range <= 1:
        # Melee: find adjacent cells of the target that are passable + reachable
        candidates: list[tuple[int, int, int]] = []  # (dist_to_my_pos, col, row)
        for dc, dr in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nc, nr = t_pos[0] + dc, t_pos[1] + dr
            cell = (nc, nr)
            if cell == my_pos:
                # Already adjacent — no need to move
                return None
            if cell in reachable and grid.is_passable(nc, nr):
                move_cost = reachable[cell]
                candidates.append((move_cost, nc, nr))

        if not candidates:
            # Not reachable this turn — find closest reachable cell toward target
            return _move_closer(my_pos, t_pos, reachable, grid)

        # Pick the adjacent cell with the lowest movement cost (closest)
        candidates.sort()
        return (candidates[0][1], candidates[0][2])

    else:
        # Ranged: find cells in weapon range of target that are reachable
        attack_cells: list[tuple[float, int, int]] = []
        for cell, cost in reachable.items():
            if cell == my_pos:
                continue
            dist_to_target = grid.distance(cell, t_pos)
            cell_terrain = grid.at(cell[0], cell[1])
            effective = weapon_range + cell_terrain.range_bonus
            if dist_to_target <= effective:
                # Prefer hill terrain (range_bonus > 0), then prefer lower cost
                hill_bonus = -1.0 if cell_terrain.range_bonus > 0 else 0.0
                attack_cells.append((hill_bonus + cost, cell[0], cell[1]))

        if not attack_cells:
            return _move_closer(my_pos, t_pos, reachable, grid)

        attack_cells.sort()
        return (attack_cells[0][1], attack_cells[0][2])


def _move_closer(
    my_pos: tuple[int, int],
    t_pos: tuple[int, int],
    reachable: dict[tuple[int, int], int],
    grid: BattleGrid,
) -> tuple[int, int] | None:
    """Return the reachable cell that minimizes distance to t_pos.

    Used as fallback when the ideal destination is not reachable this turn.
    Returns None if reachable is empty or only contains my_pos.
    """
    best_cell: tuple[int, int] | None = None
    best_dist = grid.distance(my_pos, t_pos)

    for cell in reachable:
        if cell == my_pos:
            continue
        d = grid.distance(cell, t_pos)
        if d < best_dist:
            best_dist = d
            best_cell = cell

    return best_cell
