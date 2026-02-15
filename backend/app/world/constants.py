"""
D&D 5e Reference Constants -- Step C1

参考常量表 + 节点默认值模板。
具体数值表（职业模板等）待后续按需扩充。
"""
from __future__ import annotations

from typing import Any


# =============================================================================
# Ability Scores
# =============================================================================

ABILITY_SCORES: list[str] = ["str", "dex", "con", "int", "wis", "cha"]

ABILITY_NAMES: dict[str, str] = {
    "str": "力量", "dex": "敏捷", "con": "体质",
    "int": "智力", "wis": "感知", "cha": "魅力",
}


# =============================================================================
# Skills -- 18 skills mapped to governing ability
# =============================================================================

SKILLS: dict[str, str] = {
    # STR
    "athletics": "str",
    # DEX
    "acrobatics": "dex",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    # INT
    "arcana": "int",
    "history": "int",
    "investigation": "int",
    "nature": "int",
    "religion": "int",
    # WIS
    "animal_handling": "wis",
    "insight": "wis",
    "medicine": "wis",
    "perception": "wis",
    "survival": "wis",
    # CHA
    "deception": "cha",
    "intimidation": "cha",
    "performance": "cha",
    "persuasion": "cha",
}

SKILL_NAMES: dict[str, str] = {
    "athletics": "运动",
    "acrobatics": "体操",
    "sleight_of_hand": "巧手",
    "stealth": "隐匿",
    "arcana": "奥秘",
    "history": "历史",
    "investigation": "调查",
    "nature": "自然",
    "religion": "宗教",
    "animal_handling": "驯兽",
    "insight": "洞悉",
    "medicine": "医药",
    "perception": "感知",
    "survival": "求生",
    "deception": "欺瞒",
    "intimidation": "威吓",
    "performance": "表演",
    "persuasion": "说服",
}


# =============================================================================
# Damage Types
# =============================================================================

DAMAGE_TYPES: list[str] = [
    # Physical
    "slashing", "piercing", "bludgeoning",
    # Elemental
    "fire", "cold", "lightning", "thunder", "acid", "poison",
    # Magical
    "radiant", "necrotic", "force", "psychic",
]


# =============================================================================
# Conditions (D&D 5e PHB + common buffs)
# =============================================================================

CONDITIONS: list[str] = [
    # Debuffs (PHB standard)
    "blinded", "charmed", "deafened", "frightened",
    "grappled", "incapacitated", "invisible", "paralyzed",
    "petrified", "poisoned", "prone", "restrained",
    "stunned", "unconscious",
    # Common buffs
    "blessed", "hasted", "inspired", "shielded",
    "raging", "concentrating",
]


# =============================================================================
# Alignments
# =============================================================================

ALIGNMENTS: list[str] = [
    "lawful_good", "neutral_good", "chaotic_good",
    "lawful_neutral", "true_neutral", "chaotic_neutral",
    "lawful_evil", "neutral_evil", "chaotic_evil",
]


# =============================================================================
# Equipment Slots (BG3 standard, 10 slots)
# =============================================================================

EQUIPMENT_SLOTS: list[str] = [
    "main_hand", "off_hand", "armor", "helmet", "cloak",
    "gloves", "boots", "amulet", "ring_1", "ring_2",
]


# =============================================================================
# Proficiency Bonus by Level (PHB p.15)
# =============================================================================

PROFICIENCY_BY_LEVEL: dict[int, int] = {
    1: 2, 2: 2, 3: 2, 4: 2,
    5: 3, 6: 3, 7: 3, 8: 3,
    9: 4, 10: 4, 11: 4, 12: 4,
    13: 5, 14: 5, 15: 5, 16: 5,
    17: 6, 18: 6, 19: 6, 20: 6,
}


# =============================================================================
# XP Thresholds (PHB p.15)
# =============================================================================

XP_BY_LEVEL: dict[int, int] = {
    1: 0, 2: 300, 3: 900, 4: 2700,
    5: 6500, 6: 14000, 7: 23000, 8: 34000,
    9: 48000, 10: 64000, 11: 85000, 12: 100000,
    13: 120000, 14: 140000, 15: 165000, 16: 195000,
    17: 225000, 18: 265000, 19: 305000, 20: 355000,
}


# =============================================================================
# Spell Slots by Level -- Full Caster (Wizard/Cleric/Druid/Sorcerer/Bard)
# =============================================================================

FULL_CASTER_SPELL_SLOTS: dict[int, dict[int, int]] = {
    # character_level: {spell_level: num_slots}
    1:  {1: 2},
    2:  {1: 3},
    3:  {1: 4, 2: 2},
    4:  {1: 4, 2: 3},
    5:  {1: 4, 2: 3, 3: 2},
    6:  {1: 4, 2: 3, 3: 3},
    7:  {1: 4, 2: 3, 3: 3, 4: 1},
    8:  {1: 4, 2: 3, 3: 3, 4: 2},
    9:  {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    10: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    11: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    12: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    13: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    16: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 1, 7: 1, 8: 1, 9: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 1, 8: 1, 9: 1},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1},
}

# Half Caster (Paladin / Ranger): 2 级开始获得法术位
HALF_CASTER_SPELL_SLOTS: dict[int, dict[int, int]] = {
    1:  {},
    2:  {1: 2},
    3:  {1: 3},
    4:  {1: 3},
    5:  {1: 4, 2: 2},
    6:  {1: 4, 2: 2},
    7:  {1: 4, 2: 3},
    8:  {1: 4, 2: 3},
    9:  {1: 4, 2: 3, 3: 2},
    10: {1: 4, 2: 3, 3: 2},
    11: {1: 4, 2: 3, 3: 3},
    12: {1: 4, 2: 3, 3: 3},
    13: {1: 4, 2: 3, 3: 3, 4: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 2},
    16: {1: 4, 2: 3, 3: 3, 4: 2},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
}


# =============================================================================
# Exhaustion Levels (D&D 5e PHB p.291)
# =============================================================================

EXHAUSTION_EFFECTS: dict[int, str] = {
    0: "正常",
    1: "能力检定劣势",
    2: "速度减半",
    3: "攻击骰和豁免骰劣势",
    4: "生命值上限减半",
    5: "速度降为 0",
    6: "死亡",
}


# =============================================================================
# Node Default State Templates
# =============================================================================


def default_character_state() -> dict[str, Any]:
    """角色共用 state 默认值（player + npc 共享的 D&D 5e 完整角色卡）"""
    return {
        # --- 等级与经验 ---
        "level": 1,
        "xp": 0,
        # --- 属性值 ---
        "abilities": {
            "str": 10, "dex": 10, "con": 10,
            "int": 10, "wis": 10, "cha": 10,
        },
        # --- 生命值 ---
        "hp": 10,
        "max_hp": 10,
        "temp_hp": 0,
        "hit_dice_remaining": 1,
        # --- 防御 ---
        "ac": 10,
        "speed": 30,
        "proficiency_bonus": 2,
        "initiative_bonus": 0,
        # --- 熟练 ---
        "skill_proficiencies": [],
        "skill_expertise": [],
        "saving_throw_proficiencies": [],
        # --- 抗性 ---
        "resistances": [],
        "vulnerabilities": [],
        "immunities": [],
        "condition_immunities": [],
        # --- 装备与物品 ---
        "equipment": {slot: None for slot in EQUIPMENT_SLOTS},
        "inventory": [],        # [{item_id, quantity}]
        "attunement_slots": [],  # 已协调魔法物品 (max 3)
        # --- 法术系统 ---
        "spells_known": [],
        "spells_prepared": [],
        "cantrips": [],
        "spell_slots_max": {},   # {spell_level: max_slots}
        "spell_slots_used": {},  # {spell_level: used_slots}
        "spell_save_dc": 0,
        "spell_attack_bonus": 0,
        "concentration": None,
        # --- 职业特性 ---
        "class_features": [],
        "class_resources": {},   # {"rage": {"max": 3, "used": 1}}
        # --- 状态效果 ---
        "conditions": [],
        "exhaustion_level": 0,
        # --- 存活 ---
        "is_alive": True,
        "death_saves": {"successes": 0, "failures": 0},
        "is_conscious": True,
        # --- 位置 ---
        "current_location": "",
    }


def default_npc_state() -> dict[str, Any]:
    """NPC 节点 state 默认值 = 角色共用 + NPC 专有"""
    base = default_character_state()
    base.update({
        # --- 情绪与作息 ---
        "mood": "neutral",
        "schedule": "morning",
        # --- 好感度 ---
        "dispositions": {},
        # dispositions 格式:
        #   {"player": {"approval": 0, "trust": 0, "fear": 0, "respect": 0, "affection": 0}}
        "romance_stage": None,      # none/interested/active/committed/rejected
        # --- 敌对与保护 ---
        "is_hostile": False,
        "is_essential": False,
        # --- 对话标记 ---
        "dialogue_flags": {},       # {"introduced": true, "knows_secret": false}
    })
    return base


def default_player_state() -> dict[str, Any]:
    """玩家节点 state 默认值 = 角色共用 + 玩家专有"""
    base = default_character_state()
    base.update({
        "inspiration": False,
        # --- 任务追踪 ---
        "active_quests": [],
        "completed_quests": [],
        "failed_quests": [],
        # --- 探索 ---
        "discovered_locations": [],
        "known_recipes": [],
        # --- 休息 ---
        "rest_state": {
            "last_long_rest_day": 0,
            "last_short_rest_day": 0,
            "short_rests_today": 0,
        },
    })
    return base
