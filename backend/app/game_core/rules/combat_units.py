"""Combat unit construction factory for the v2 SRPG combat system.

❷ Rules engine layer pure utility module.
Only imports stdlib + game_core internal types.
No application layer imports.
"""

from __future__ import annotations

import random
from typing import Any

from app.game_core.content.registries.characters import CharacterTemplate, NpcAttack
from app.game_core.content.registries.monsters import MonsterAttack, MonsterTemplate
from app.game_core.state import StateContainer


# ---------------------------------------------------------------------------
# Stat helpers
# ---------------------------------------------------------------------------

def _ability_modifier(score: int) -> int:
    """Standard D&D ability modifier: (score - 10) // 2."""
    return (score - 10) // 2


def _get_stat(stats: dict[str, int], name: str, default: int = 10) -> int:
    """Return stat value with fallback to default."""
    return int(stats.get(name, default))


# ---------------------------------------------------------------------------
# Unit dict template
# ---------------------------------------------------------------------------

def _base_unit(
    unit_id: str,
    side: str,
    source: str,
    name: str,
    hp: int,
    max_hp: int,
    ac: int,
    stats: dict[str, int],
    speed: int,
    attacks: list[dict[str, Any]],
    ai_personality: str | None,
    flee_threshold: float,
    flee_chance: float,
    proficiency_bonus: int,
    monster_id: str | None = None,
    character_id: str | None = None,
) -> dict[str, Any]:
    """Build the canonical unit dict shared by all unit types."""
    return {
        "unit_id": unit_id,
        "side": side,
        "source": source,
        "monster_id": monster_id,
        "character_id": character_id,
        "name": name,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "stats": dict(stats),
        "speed": speed,
        "position": None,
        "alive": True,
        "fled": False,
        "active_effects": [],
        "attacks": attacks,
        "ai_personality": ai_personality,
        "flee_threshold": flee_threshold,
        "flee_chance": flee_chance,
        "action_used": False,
        "move_used": False,
        "disengaged": False,
        "dashed": False,
        "defending": False,
        "reaction_used": False,
        "surprised": False,
        "proficiency_bonus": proficiency_bonus,
    }


# ---------------------------------------------------------------------------
# Attack dict conversion
# ---------------------------------------------------------------------------

def _monster_attack_to_dict(atk: MonsterAttack) -> dict[str, Any]:
    return {
        "name": atk.name,
        "hit_bonus": atk.hit_bonus,
        "damage_dice": atk.damage_dice,
        "damage_type": atk.damage_type,
        "range": atk.range,
        "tags": list(atk.tags),
    }


def _npc_attack_to_dict(atk: NpcAttack) -> dict[str, Any]:
    return {
        "name": atk.name,
        "hit_bonus": atk.hit_bonus,
        "damage_dice": atk.damage_dice,
        "damage_type": atk.damage_type,
        "range": atk.range,
        "tags": list(atk.tags),
    }


def _unarmed_attack() -> dict[str, Any]:
    return {
        "name": "Unarmed Strike",
        "hit_bonus": 0,
        "damage_dice": "1d1",
        "damage_type": "bludgeoning",
        "range": 1,
        "tags": ["MELEE", "UNARMED"],
    }


def _default_monster_attack() -> dict[str, Any]:
    return {
        "name": "Slam",
        "hit_bonus": 0,
        "damage_dice": "1d4",
        "damage_type": "bludgeoning",
        "range": 1,
        "tags": ["MELEE"],
    }


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_player_unit(state: StateContainer) -> dict[str, Any]:
    """Build the player unit dict from live state."""
    player = state.player
    stats = {
        "str": _get_stat(player.stats, "str"),
        "dex": _get_stat(player.stats, "dex"),
        "con": _get_stat(player.stats, "con"),
        "int": _get_stat(player.stats, "int"),
        "wis": _get_stat(player.stats, "wis"),
        "cha": _get_stat(player.stats, "cha"),
    }

    # Build attack from main_hand equipment, or fall back to unarmed
    main_hand = player.equipment.get("main_hand")
    if isinstance(main_hand, dict) and main_hand.get("item_id"):
        weapon_attack: dict[str, Any] = {
            "name": str(main_hand.get("name", main_hand["item_id"])),
            "hit_bonus": int(main_hand.get("hit_bonus", 0)),
            "damage_dice": str(main_hand.get("damage_dice", "1d6")),
            "damage_type": str(main_hand.get("damage_type", "physical")),
            "range": int(main_hand.get("range", 1)),
            "tags": list(main_hand.get("tags", [])),
        }
        attacks = [weapon_attack]
    else:
        attacks = [_unarmed_attack()]

    return _base_unit(
        unit_id="player",
        side="ally",
        source="player",
        name=player.character_name or "Player",
        hp=player.hp,
        max_hp=player.max_hp,
        ac=player.ac,
        stats=stats,
        speed=3,
        attacks=attacks,
        ai_personality=None,
        flee_threshold=0.0,
        flee_chance=0.0,
        proficiency_bonus=player.proficiency_bonus,
        monster_id=None,
        character_id=player.character_id or None,
    )


def build_companion_unit(
    character_id: str,
    member_data: dict[str, Any],
    template: CharacterTemplate,
) -> dict[str, Any] | None:
    """Build a companion unit dict.

    Returns None if the character template is not combat_capable.
    """
    if not template.combat_capable:
        return None

    # HP: member_data first, then template.base_hp, then 10
    hp: int
    raw_hp = member_data.get("hp")
    if raw_hp is not None:
        try:
            hp = max(1, int(raw_hp))
        except (TypeError, ValueError):
            hp = int(template.base_hp) if template.base_hp is not None else 10
    elif template.base_hp is not None:
        hp = max(1, int(template.base_hp))
    else:
        hp = 10

    max_hp: int
    raw_max_hp = member_data.get("max_hp")
    if raw_max_hp is not None:
        try:
            max_hp = max(1, int(raw_max_hp))
        except (TypeError, ValueError):
            max_hp = hp
    else:
        max_hp = max(hp, int(template.base_hp) if template.base_hp is not None else hp)

    ac: int = int(template.base_ac) if template.base_ac is not None else 10

    stats = {
        "str": _get_stat(template.stats, "str"),
        "dex": _get_stat(template.stats, "dex"),
        "con": _get_stat(template.stats, "con"),
        "int": _get_stat(template.stats, "int"),
        "wis": _get_stat(template.stats, "wis"),
        "cha": _get_stat(template.stats, "cha"),
    }

    attacks = [_npc_attack_to_dict(atk) for atk in template.attacks]

    return _base_unit(
        unit_id=character_id,
        side="ally",
        source="companion",
        name=template.name or character_id,
        hp=hp,
        max_hp=max_hp,
        ac=ac,
        stats=stats,
        speed=3,
        attacks=attacks,
        ai_personality=None,
        flee_threshold=0.0,
        flee_chance=0.0,
        proficiency_bonus=template.proficiency_bonus,
        monster_id=None,
        character_id=character_id,
    )


def build_monster_unit(
    monster_id: str,
    template: MonsterTemplate,
    index: int,
) -> dict[str, Any]:
    """Build a monster unit dict.

    speed conversion: feet → grid cells (max(2, template.speed // 10)).
    """
    max_hp = template.hp or template.max_hp
    if max_hp is None or max_hp < 1:
        max_hp = 10
    hp = max_hp

    ac: int = template.ac if template.ac is not None and template.ac >= 1 else 10

    stats = {
        "str": _get_stat(template.abilities, "str"),
        "dex": _get_stat(template.abilities, "dex"),
        "con": _get_stat(template.abilities, "con"),
        "int": _get_stat(template.abilities, "int"),
        "wis": _get_stat(template.abilities, "wis"),
        "cha": _get_stat(template.abilities, "cha"),
    }

    # Convert feet to grid cells, minimum 2
    speed = max(2, template.speed // 10)

    attacks = [_monster_attack_to_dict(atk) for atk in template.attacks]
    if not attacks:
        attacks = [_default_monster_attack()]

    return _base_unit(
        unit_id=f"{monster_id}_{index}",
        side="enemy",
        source="monster",
        name=template.name or monster_id,
        hp=hp,
        max_hp=max_hp,
        ac=ac,
        stats=stats,
        speed=speed,
        attacks=attacks,
        ai_personality=template.ai_personality,
        flee_threshold=template.flee_threshold,
        flee_chance=template.flee_chance,
        proficiency_bonus=2,
        monster_id=monster_id,
        character_id=None,
    )


# ---------------------------------------------------------------------------
# Surprise resolution
# ---------------------------------------------------------------------------

def resolve_surprise(
    units: list[dict[str, Any]],
    base_surprise_state: str,
    stealth_total: int,
) -> list[dict[str, Any]]:
    """Assign surprised=True/False on each unit in-place, then return units.

    - "player_surprise": each enemy rolls d20 + WIS_mod; surprised if total < stealth_total
    - "enemy_surprise": all ally units are surprised
    - "none": nobody is surprised
    """
    for unit in units:
        unit["surprised"] = False

    if base_surprise_state == "player_surprise":
        for unit in units:
            if unit["side"] != "enemy":
                continue
            wis_mod = _ability_modifier(_get_stat(unit["stats"], "wis"))
            perception_roll = random.randint(1, 20) + wis_mod
            if perception_roll < stealth_total:
                unit["surprised"] = True

    elif base_surprise_state == "enemy_surprise":
        for unit in units:
            if unit["side"] == "ally":
                unit["surprised"] = True

    return units


# ---------------------------------------------------------------------------
# Initiative rolling
# ---------------------------------------------------------------------------

def roll_initiative(unit: dict[str, Any]) -> tuple[int, int]:
    """Roll initiative for a unit.

    Returns (d20 + DEX_mod, DEX) for sorting and tiebreaking.
    """
    dex = _get_stat(unit["stats"], "dex")
    dex_mod = _ability_modifier(dex)
    initiative = random.randint(1, 20) + dex_mod
    return (initiative, dex)


# ---------------------------------------------------------------------------
# Turn order
# ---------------------------------------------------------------------------

def build_turn_order(
    units: list[dict[str, Any]],
) -> tuple[list[str], dict[str, int]]:
    """Build initiative-ordered turn list for alive, non-fled units.

    Returns:
        - ordered list of unit_id strings (descending initiative, DEX tiebreak)
        - dict mapping unit_id -> initiative roll
    """
    eligible = [u for u in units if u.get("alive") and not u.get("fled")]

    rolls: dict[str, tuple[int, int]] = {}
    for unit in eligible:
        rolls[unit["unit_id"]] = roll_initiative(unit)

    sorted_units = sorted(
        eligible,
        key=lambda u: rolls[u["unit_id"]],
        reverse=True,
    )

    turn_order = [u["unit_id"] for u in sorted_units]
    initiative_rolls = {uid: rolls[uid][0] for uid in turn_order}

    return turn_order, initiative_rolls


# ---------------------------------------------------------------------------
# Position assignment
# ---------------------------------------------------------------------------

def assign_positions(
    units: list[dict[str, Any]],
    grid_width: int,
    grid_height: int,
) -> None:
    """Assign starting grid positions in-place.

    Allies start on the left (col 0-1), enemies on the right (col w-2 ~ w-1).
    Rows are distributed top to bottom, wrapping if needed.
    """
    allies = [u for u in units if u["side"] == "ally"]
    enemies = [u for u in units if u["side"] == "enemy"]

    center_row = grid_height // 2

    for idx, unit in enumerate(allies):
        col = idx % 2                          # col 0 or 1
        row = (center_row + (idx // 2)) % grid_height
        unit["position"] = [col, row]

    for idx, unit in enumerate(enemies):
        col = (grid_width - 1) - (idx % 2)    # col w-1 or w-2
        row = (center_row + (idx // 2)) % grid_height
        unit["position"] = [col, row]


# ---------------------------------------------------------------------------
# Spawn-point position assignment
# ---------------------------------------------------------------------------

def assign_positions_from_spawns(
    units: list[dict[str, Any]],
    player_spawns: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    enemy_spawns: list[tuple[int, int]] | tuple[tuple[int, int], ...],
) -> None:
    """Assign grid positions from pre-defined spawn points in-place.

    Allies are assigned to player_spawns in order; if there are more allies
    than spawn points, positions wrap around (modulo).  The same applies to
    enemies and enemy_spawns.
    """
    allies = [u for u in units if u["side"] == "ally"]
    enemies = [u for u in units if u["side"] == "enemy"]

    ps = list(player_spawns)
    es = list(enemy_spawns)

    for idx, unit in enumerate(allies):
        col, row = ps[idx % len(ps)]
        unit["position"] = [col, row]

    for idx, unit in enumerate(enemies):
        col, row = es[idx % len(es)]
        unit["position"] = [col, row]


# ---------------------------------------------------------------------------
# Default grid
# ---------------------------------------------------------------------------

def build_default_grid(width: int = 8, height: int = 6) -> dict[str, Any]:
    """Return a default all-grass grid dict.

    Format: {"width": w, "height": h, "terrain": [row_string, ...]}
    """
    row = "G" * width
    return {
        "width": width,
        "height": height,
        "terrain": [row] * height,
    }
