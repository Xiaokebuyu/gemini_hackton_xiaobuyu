"""纯函数式 Stats 操作模块 — 无 I/O，无 LLM。

所有 XP/Gold/HP 修改必须通过此模块，确保校验一致性。
PlayerNodeView setter 已有非负约束作为安全网，本模块做业务校验。
"""
from __future__ import annotations

import math
from typing import Any, Dict

from app.world.constants import (
    CASTER_TYPE_BY_CLASS,
    FULL_CASTER_SPELL_SLOTS,
    HALF_CASTER_SPELL_SLOTS,
    HIT_DIE_BY_CLASS,
    PROFICIENCY_BY_LEVEL,
    XP_BY_LEVEL,
)


# ---------------------------------------------------------------------------
# XP
# ---------------------------------------------------------------------------

def add_xp(player: Any, amount: int, *, class_name: str = "") -> Dict[str, Any]:
    """Add XP with level-up check (supports multi-level jumps).

    Args:
        player: PlayerNodeView or PlayerCharacter (duck-typed).
        amount: XP to grant (must be > 0).
        class_name: D&D class for hit die lookup; auto-detected from player if empty.

    Returns:
        dict with old_xp, new_xp, xp_to_next, leveled_up, new_level, hp_gained.
    """
    if amount <= 0:
        return {"success": False, "error": "amount must be positive"}

    if not class_name:
        cc = getattr(player, "character_class", "")
        class_name = cc.value if hasattr(cc, "value") else str(cc or "")

    old_xp = player.xp
    old_level = player.level
    player.xp = old_xp + amount

    hp_gained = 0
    while True:
        next_level = player.level + 1
        threshold = XP_BY_LEVEL.get(next_level)
        if threshold is None or player.xp < threshold:
            break

        player.level = next_level
        player.proficiency_bonus = PROFICIENCY_BY_LEVEL.get(next_level, 2)

        hit_die = HIT_DIE_BY_CLASS.get(class_name.lower(), 8)
        con_mod = player.ability_modifier("con") if hasattr(player, "ability_modifier") else 0
        hp_gain = max(1, math.ceil(hit_die / 2) + 1 + con_mod)
        player.max_hp += hp_gain
        player.current_hp += hp_gain
        hp_gained += hp_gain

        next_threshold = XP_BY_LEVEL.get(next_level + 1)
        if next_threshold is not None:
            player.xp_to_next_level = next_threshold

        _update_spell_slots_on_level_up(player, next_level, class_name)

    return {
        "old_xp": old_xp,
        "new_xp": player.xp,
        "xp_to_next": player.xp_to_next_level,
        "leveled_up": player.level > old_level,
        "new_level": player.level,
        "hp_gained": hp_gained,
    }


# ---------------------------------------------------------------------------
# Gold
# ---------------------------------------------------------------------------

def add_gold(player: Any, amount: int) -> Dict[str, Any]:
    """Add gold (amount must be > 0)."""
    if amount <= 0:
        return {"success": False, "error": "amount must be positive"}
    old = player.gold
    player.gold = (old or 0) + amount
    return {"success": True, "old_gold": old, "new_gold": player.gold}


def remove_gold(player: Any, amount: int) -> Dict[str, Any]:
    """Remove gold (checks sufficient balance)."""
    if amount <= 0:
        return {"success": False, "error": "amount must be positive"}
    old = player.gold or 0
    if old < amount:
        return {"success": False, "error": "insufficient gold", "current": old, "requested": amount}
    player.gold = old - amount
    return {"success": True, "old_gold": old, "new_gold": player.gold}


# ---------------------------------------------------------------------------
# HP
# ---------------------------------------------------------------------------

def add_hp(player: Any, amount: int) -> Dict[str, Any]:
    """Heal HP (clamped to max_hp)."""
    if amount <= 0:
        return {"success": False, "error": "amount must be positive"}
    old = player.current_hp
    player.current_hp = min(player.max_hp, old + amount)
    return {"old_hp": old, "new_hp": player.current_hp, "max_hp": player.max_hp}


def remove_hp(player: Any, amount: int) -> Dict[str, Any]:
    """Damage HP (clamped to 0)."""
    if amount <= 0:
        return {"success": False, "error": "amount must be positive"}
    old = player.current_hp
    player.current_hp = max(0, old - amount)
    return {"old_hp": old, "new_hp": player.current_hp, "max_hp": player.max_hp}


def set_hp(player: Any, hp: int) -> Dict[str, Any]:
    """Set HP to exact value (clamped to [0, max_hp]). For combat sync."""
    old = player.current_hp
    player.current_hp = max(0, min(int(hp), player.max_hp))
    return {"old_hp": old, "new_hp": player.current_hp, "max_hp": player.max_hp}


# ---------------------------------------------------------------------------
# Combat rewards sync
# ---------------------------------------------------------------------------

def sync_combat_rewards(player: Any, combat_payload: dict) -> Dict[str, Any]:
    """Sync HP/XP/Gold/Items from combat payload to player.

    Extracts player_state.hp_remaining and final_result.rewards,
    applies them via set_hp/add_xp/add_gold/add_item.

    Returns: {"hp_set": bool, "xp_added": int, "gold_added": int, "items_added": list}
    """
    result: Dict[str, Any] = {"hp_set": False, "xp_added": 0, "gold_added": 0, "items_added": []}

    # HP
    player_state = combat_payload.get("player_state") or {}
    hp_remaining = player_state.get("hp_remaining")
    if hp_remaining is not None:
        set_hp(player, int(hp_remaining))
        result["hp_set"] = True

    # Rewards (victory only)
    final_result = combat_payload.get("final_result") or combat_payload.get("result") or {}
    if not isinstance(final_result, dict):
        return result
    if final_result.get("result") != "victory":
        return result

    rewards = final_result.get("rewards") or {}
    xp = int(rewards.get("xp", 0))
    if xp > 0:
        add_xp(player, xp)
        result["xp_added"] = xp
    gold = int(rewards.get("gold", 0))
    if gold > 0:
        add_gold(player, gold)
        result["gold_added"] = gold
    for item_id in rewards.get("items", []):
        player.add_item(item_id, item_id, 1)
        result["items_added"].append(item_id)

    return result


# ---------------------------------------------------------------------------
# Internal: spell slot update on level-up
# ---------------------------------------------------------------------------

def _update_spell_slots_on_level_up(player: Any, new_level: int, class_name: str) -> None:
    """Update spell slots when leveling up (if caster class)."""
    caster_type = CASTER_TYPE_BY_CLASS.get(class_name.lower(), "")
    if not caster_type:
        return

    table = FULL_CASTER_SPELL_SLOTS if caster_type == "full" else HALF_CASTER_SPELL_SLOTS
    slots = table.get(new_level)
    if slots is None:
        return

    if hasattr(player, "spell_slots"):
        player.spell_slots = slots
