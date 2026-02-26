"""
Player operations — WorldGraph 原生的角色业务操作。

函数式 API，无外部服务依赖。所有操作通过 PlayerNodeView setter 自动触发脏标记。
迁移自旧 CharacterService (character.py)。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, TYPE_CHECKING

from app.models.character_creation import CharacterCreationRequest
from app.models.player_character import CharacterClass, CharacterRace
from app.world.player.constants import default_player_state

if TYPE_CHECKING:
    from app.world.graph.world_graph import WorldGraph
    from app.world.player.node_view import PlayerNodeView

logger = logging.getLogger(__name__)


# =============================================================================
# 角色创建
# =============================================================================


def create_player_node(
    wg: "WorldGraph",
    creation_config: Dict[str, Any],
    request: CharacterCreationRequest,
    player_location: Optional[str] = None,
) -> "PlayerNodeView":
    """校验 + 种族加成 + 衍生计算 → 构建 WorldNode → 写入 WorldGraph → 返回 PlayerNodeView。

    Args:
        wg: 当前 SessionRuntime 的 WorldGraph。
        creation_config: 角色创建配置（races/classes/backgrounds/point_buy/leveling）。
        request: 前端提交的角色创建请求。
        player_location: 可选，当前玩家位置 ID（用于建立 HOSTS 边）。

    Returns:
        PlayerNodeView 适配器。

    Raises:
        ValueError: 校验失败（种族/职业/点数购买不合法）。
    """
    from app.world.graph.models import WorldNode, WorldNodeType, WorldEdgeType
    from app.world.player.node_view import PlayerNodeView

    # --- Validate enums ---
    race = CharacterRace(request.race)
    char_class = CharacterClass(request.character_class)

    race_cfg = creation_config["races"].get(race.value)
    if not race_cfg:
        raise ValueError(f"Unknown race: {race.value}")
    class_cfg = creation_config["classes"].get(char_class.value)
    if not class_cfg:
        raise ValueError(f"Unknown class: {char_class.value}")

    # --- Validate point buy ---
    if not validate_point_buy(request.ability_scores, creation_config["point_buy"]):
        raise ValueError("Invalid point-buy allocation")

    # --- Apply racial bonuses ---
    abilities = dict(request.ability_scores)
    for ability, bonus in race_cfg.get("ability_bonuses", {}).items():
        abilities[ability] = abilities.get(ability, 10) + bonus

    # --- Derived stats ---
    con_mod = (abilities.get("con", 10) - 10) // 2
    dex_mod = (abilities.get("dex", 10) - 10) // 2
    hit_die = class_cfg["hit_die"]
    max_hp = hit_die + con_mod
    ac = _calculate_starting_ac(char_class.value, dex_mod, abilities, race_cfg, class_cfg)
    initiative_bonus = dex_mod
    speed = race_cfg.get("speed", 30)

    # --- Proficiencies ---
    skill_profs = list(request.skill_proficiencies)
    bg_cfg = creation_config["backgrounds"].get(request.background, {})
    for sk in bg_cfg.get("skill_proficiencies", []):
        if sk not in skill_profs:
            skill_profs.append(sk)
    for sk in race_cfg.get("free_proficiencies", []):
        if sk not in skill_profs:
            skill_profs.append(sk)

    saving_throw_profs = list(class_cfg.get("saving_throws", []))
    weapon_profs = list(class_cfg.get("weapon_proficiencies", []))
    armor_profs = list(class_cfg.get("armor_proficiencies", []))

    # --- Features ---
    class_features = list(class_cfg.get("class_features", []))
    racial_traits = list(race_cfg.get("racial_traits", []))

    # --- Equipment ---
    equipment: Dict[str, Optional[str]] = {}
    for item in class_cfg.get("starting_equipment", []):
        equipment[item["slot"]] = item["item_id"]

    # --- Gold ---
    gold = class_cfg.get("starting_gold", 0) + bg_cfg.get("starting_gold_bonus", 0)

    # --- Spellcasting ---
    spell_slots: Dict[str, int] = {}
    spell_slots_used: Dict[str, int] = {}
    if class_cfg.get("spellcasting"):
        raw_slots = class_cfg.get("spell_slots_at_level_1", {})
        spell_slots = {str(k): int(v) for k, v in raw_slots.items()}
        spell_slots_used = {k: 0 for k in spell_slots}

    # --- XP thresholds ---
    leveling = creation_config.get("leveling", {})
    xp_thresholds = leveling.get("xp_thresholds", {})
    xp_to_next = xp_thresholds.get("2", 300)

    # --- Build state + properties ---
    state = default_player_state()
    state.update({
        "level": 1,
        "xp": 0,
        "xp_to_next_level": xp_to_next,
        "abilities": abilities,
        "hp": max_hp,
        "max_hp": max_hp,
        "ac": ac,
        "initiative_bonus": initiative_bonus,
        "speed": speed,
        "proficiency_bonus": 2,
        "skill_proficiencies": skill_profs,
        "saving_throw_proficiencies": saving_throw_profs,
        "weapon_proficiencies": weapon_profs,
        "armor_proficiencies": armor_profs,
        "class_features": class_features,
        "racial_traits": racial_traits,
        "equipment": equipment,
        "gold": gold,
        "spell_slots_max": spell_slots,
        "spell_slots_used": spell_slots_used,
        "inventory": [],
    })
    if player_location:
        state["current_location"] = player_location

    properties = {
        "race": race.value,
        "character_class": char_class.value,
        "background": request.background,
        "backstory": request.backstory or "",
    }

    # --- Write to WorldGraph ---
    wg.add_node(WorldNode(
        id="player",
        type=WorldNodeType.PLAYER,
        name=request.name,
        properties=properties,
        state=state,
    ))
    wg.add_edge("player", "camp", WorldEdgeType.MEMBER_OF.value, key="member_player")

    if player_location and wg.has_node(player_location):
        wg.add_edge(player_location, "player", WorldEdgeType.HOSTS.value, key="hosts_player")

    logger.info(
        "Created player node: %s (%s %s)",
        request.name, race.value, char_class.value,
    )
    return PlayerNodeView(wg.get_node("player"), wg)


# =============================================================================
# 升级
# =============================================================================


def level_up(player: "PlayerNodeView", creation_config: Dict[str, Any], xp_amount: int) -> Dict[str, Any]:
    """给玩家加经验并检查升级（支持多级跳）。

    通过 PlayerNodeView setter 自动标记脏节点。

    Returns:
        {xp_gained, new_xp, leveled_up, new_level, hp_gained}
    """
    leveling = creation_config.get("leveling", {})
    xp_thresholds = leveling.get("xp_thresholds", {})
    proficiency_by_level = leveling.get("proficiency_by_level", {})

    player.xp = player.xp + xp_amount
    result: Dict[str, Any] = {
        "xp_gained": xp_amount,
        "new_xp": player.xp,
        "leveled_up": False,
        "new_level": player.level,
        "hp_gained": 0,
    }

    while True:
        next_level = player.level + 1
        threshold = xp_thresholds.get(str(next_level))
        if threshold is None or player.xp < threshold:
            break

        player.level = next_level
        result["leveled_up"] = True
        result["new_level"] = next_level

        prof = proficiency_by_level.get(str(next_level))
        if prof:
            player.proficiency_bonus = prof

        # HP gain: average hit die + CON mod
        char_class_val = player.character_class
        if hasattr(char_class_val, "value"):
            char_class_val = char_class_val.value
        class_cfg = creation_config["classes"].get(str(char_class_val), {})
        hit_die = class_cfg.get("hit_die", 8)
        con_mod = player.ability_modifier("con")
        hp_gain = max(1, math.ceil(hit_die / 2) + 1 + con_mod)
        player.max_hp = player.max_hp + hp_gain
        player.current_hp = player.current_hp + hp_gain
        result["hp_gained"] += hp_gain

        # xp_to_next_level
        next_next = str(next_level + 1)
        if next_next in xp_thresholds:
            player.xp_to_next_level = xp_thresholds[next_next]

        # Spell slots
        if class_cfg.get("spellcasting"):
            _update_spell_slots_for_level(player, next_level)

    return result


# =============================================================================
# 装备管理
# =============================================================================


def equip_item(player: "PlayerNodeView", item_id: str, slot: str) -> Dict[str, Any]:
    """装备物品到指定槽位，返回 {slot, item_id, previous}。"""
    if not player.has_item(item_id):
        raise ValueError(f"Item {item_id} not in inventory")

    old_item_id = player.equipment.get(slot)
    if old_item_id:
        player.add_item(old_item_id, old_item_id, 1)

    player.remove_item(item_id, 1)
    equipment = player.equipment
    equipment[slot] = item_id
    player._state["equipment"] = equipment
    player._mark_dirty()

    if slot in ("armor", "off_hand"):
        recalculate_ac(player)

    return {"slot": slot, "item_id": item_id, "previous": old_item_id}


def unequip_item(player: "PlayerNodeView", slot: str) -> Dict[str, Any]:
    """卸下指定槽位装备，放回背包。"""
    item_id = player.equipment.get(slot)
    if not item_id:
        raise ValueError(f"No item equipped in slot {slot}")

    equipment = player.equipment
    equipment[slot] = None
    player._state["equipment"] = equipment
    player._mark_dirty()
    player.add_item(item_id, item_id, 1)

    if slot in ("armor", "off_hand"):
        recalculate_ac(player)

    return {"slot": slot, "unequipped": item_id}


# =============================================================================
# 校验与计算
# =============================================================================


def validate_point_buy(scores: Dict[str, int], config: Dict[str, Any]) -> bool:
    """校验点数购买是否合法。"""
    total_points = config.get("total_points", 27)
    min_score = config.get("min_score", 8)
    max_score = config.get("max_score", 15)
    cost_table = config.get("cost_table", {})
    required_abilities = config.get("abilities", ["str", "dex", "con", "int", "wis", "cha"])

    if set(scores.keys()) != set(required_abilities):
        return False

    spent = 0
    for ability, score in scores.items():
        if score < min_score or score > max_score:
            return False
        cost = cost_table.get(str(score))
        if cost is None:
            return False
        spent += cost

    return spent == total_points


def recalculate_ac(player: "PlayerNodeView") -> None:
    """根据当前装备重算 AC。"""
    from app.world.item.registry import get_item

    dex_mod = player.ability_modifier("dex")
    armor_id = player.equipment.get("armor")
    shield_id = player.equipment.get("off_hand")

    base_ac = 10 + dex_mod
    if armor_id:
        armor_data = get_item(armor_id)
        if armor_data:
            subtype = armor_data.get("subtype", "")
            ac_bonus = armor_data.get("properties", {}).get("ac_bonus", 0)
            if subtype == "heavy":
                base_ac = 10 + ac_bonus
            elif subtype in ("light", "clothing"):
                base_ac = 10 + ac_bonus + dex_mod
            else:
                base_ac = 10 + ac_bonus + min(dex_mod, 2)

    shield_bonus = 0
    if shield_id:
        shield_data = get_item(shield_id)
        if shield_data and shield_data.get("subtype") == "shield":
            shield_bonus = shield_data.get("properties", {}).get("ac_bonus", 0)

    char_class = player.character_class
    class_val = char_class.value if hasattr(char_class, "value") else str(char_class)
    if class_val == "monk" and not armor_id:
        wis_mod = player.ability_modifier("wis")
        base_ac = max(base_ac, 10 + dex_mod + wis_mod)

    if "natural_armor" in player.racial_traits:
        base_ac = max(base_ac, 13 + dex_mod)

    player.ac = base_ac + shield_bonus


def _calculate_starting_ac(
    class_name: str,
    dex_mod: int,
    abilities: Dict[str, int],
    race_cfg: Dict[str, Any],
    class_cfg: Dict[str, Any],
) -> int:
    """计算初始 AC（创建时用，基于起始装备）。"""
    if class_name == "monk":
        wis_mod = (abilities.get("wis", 10) - 10) // 2
        return 10 + dex_mod + wis_mod

    has_natural_armor = "natural_armor" in race_cfg.get("racial_traits", [])

    equipment = class_cfg.get("starting_equipment", [])
    armor_id = None
    has_shield = False
    for item in equipment:
        if item["slot"] == "armor":
            armor_id = item["item_id"]
        if item["slot"] == "off_hand" and "shield" in item.get("item_id", ""):
            has_shield = True

    shield_bonus = 2 if has_shield else 0

    if armor_id == "armor_chain_mail":
        base_ac = 16
    elif armor_id == "armor_chain_shirt":
        base_ac = 13 + min(dex_mod, 2)
    elif armor_id == "armor_leather":
        base_ac = 11 + dex_mod
    elif armor_id == "armor_basic_clothing":
        base_ac = 10 + dex_mod
    else:
        base_ac = 10 + dex_mod

    equipped_ac = base_ac + shield_bonus

    if has_natural_armor:
        natural_ac = 13 + dex_mod
        return max(equipped_ac, natural_ac)

    return equipped_ac


def _update_spell_slots_for_level(player: "PlayerNodeView", level: int) -> None:
    """升级时更新法术位。"""
    slot_progression = {
        2: {1: 3},
        3: {1: 4, 2: 2},
        4: {1: 4, 2: 3},
        5: {1: 4, 2: 3, 3: 2},
    }
    if level in slot_progression:
        slots = player.spell_slots
        slots_used = player.spell_slots_used
        for slot_level, count in slot_progression[level].items():
            slots[slot_level] = count
            if slot_level not in slots_used:
                slots_used[slot_level] = 0
        player.spell_slots = slots
        player.spell_slots_used = slots_used
