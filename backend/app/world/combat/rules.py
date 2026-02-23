"""
战斗规则（DND简化版）

定义所有战斗相关的常量和规则
"""
from typing import Any, Dict


# ============================================
# 常量定义
# ============================================

# 逃跑判定DC（Difficulty Class）
FLEE_DC = 10

# 防御姿态AC加值
DEFEND_AC_BONUS = 2

# 暴击判定（预留）
CRITICAL_HIT_ROLL = 20
CRITICAL_MISS_ROLL = 1


# ============================================
# 敌人模板
# ============================================

ENEMY_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "goblin": {
        "name": "哥布林",
        "max_hp": 15,
        "ac": 13,
        "attack_bonus": 3,
        "damage_dice": "1d6",
        "damage_bonus": 1,
        "initiative_bonus": 2,
        "ai_personality": "cowardly",  # 胆小型
        "xp_reward": 50,
        "gold_reward": 5,
    },
    "orc": {
        "name": "兽人战士",
        "max_hp": 30,
        "ac": 15,
        "attack_bonus": 5,
        "damage_dice": "1d8",
        "damage_bonus": 3,
        "initiative_bonus": 0,
        "ai_personality": "aggressive",  # 攻击型
        "xp_reward": 100,
        "gold_reward": 15,
    },
    "wolf": {
        "name": "野狼",
        "max_hp": 20,
        "ac": 14,
        "attack_bonus": 4,
        "damage_dice": "1d6",
        "damage_bonus": 2,
        "initiative_bonus": 3,
        "ai_personality": "pack_hunter",  # 群猎型
        "xp_reward": 75,
        "gold_reward": 0,
    },
    # 预留：更多敌人类型
}


# ============================================
# 物品效果（治疗药水等）
# ============================================

ITEM_EFFECTS: Dict[str, Dict[str, Any]] = {
    "healing_potion": {
        "name": "治疗药水",
        "effect_type": "heal",
        "heal_amount": "2d4+2",  # 恢复2d4+2 HP
        "description": "恢复少量生命值",
    },
    "greater_healing_potion": {
        "name": "强效治疗药水",
        "effect_type": "heal",
        "heal_amount": "4d4+4",
        "description": "恢复中等生命值",
    },
    # 预留：其他物品
}


# ============================================
# AI性格配置
# ============================================

AI_PERSONALITIES: Dict[str, Dict[str, Any]] = {
    "cowardly": {
        "flee_threshold": 0.3,  # HP低于30%时尝试逃跑
        "aggression": 0.5,  # 攻击倾向（0-1）
        "prefer_weaker_targets": True,
    },
    "aggressive": {
        "flee_threshold": 0.0,  # 永不逃跑
        "aggression": 1.0,
        "prefer_weaker_targets": False,
    },
    "pack_hunter": {
        "flee_threshold": 0.2,
        "aggression": 0.8,
        "prefer_wounded_targets": True,  # 优先攻击受伤目标
    },
    "defensive": {
        "flee_threshold": 0.4,
        "aggression": 0.3,
        "prefer_defend": True,  # 优先防御
    },
}


# ============================================
# 规则函数
# ============================================


def calculate_hit_chance(attack_bonus: int, target_ac: int) -> float:
    """
    计算命中概率

    Args:
        attack_bonus: 攻击加值
        target_ac: 目标AC

    Returns:
        float: 命中概率（0-1）
    """
    # d20 + attack_bonus >= target_ac
    # 需要骰出：target_ac - attack_bonus 或更高
    required_roll = target_ac - attack_bonus

    if required_roll <= 1:
        return 0.95  # 几乎必中（只有骰1才会失手）
    if required_roll >= 20:
        return 0.05  # 几乎不中（只有骰20才会命中）
    return (21 - required_roll) / 20


def get_flee_difficulty() -> int:
    """获取逃跑判定的DC"""
    return FLEE_DC


def get_defeat_penalty(player_gold: int) -> Dict[str, Any]:
    """
    计算战斗失败的惩罚

    Args:
        player_gold: 玩家当前金币

    Returns:
        Dict: 惩罚内容
    """
    gold_lost = max(int(player_gold * 0.5), 0)  # 失去50%金币

    return {
        "gold_lost": gold_lost,
        "items_lost": [],  # MVP阶段不掉落物品
        "respawn_location": "village_temple",
    }
