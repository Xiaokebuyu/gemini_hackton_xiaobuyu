"""
战斗单位数据模型
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CombatantType(str, Enum):
    """战斗单位类型"""

    PLAYER = "player"
    ENEMY = "enemy"
    ALLY = "ally"  # 预留：队友


class StatusEffect(str, Enum):
    """状态效果（预留扩展）"""

    POISONED = "poisoned"
    STUNNED = "stunned"
    DEFENDING = "defending"
    BURNING = "burning"
    PRONE = "prone"
    FRIGHTENED = "frightened"
    BLINDED = "blinded"
    RESTRAINED = "restrained"
    DISENGAGED = "disengaged"
    HIDDEN = "hidden"


@dataclass
class StatusEffectInstance:
    """状态效果实例"""

    effect: StatusEffect
    duration: int  # 剩余回合数
    source: str = ""  # 来源（谁施加的）

    def tick(self) -> bool:
        """
        回合结束时调用，减少持续时间

        Returns:
            bool: 是否已过期
        """
        self.duration -= 1
        return self.duration <= 0


@dataclass
class Combatant:
    """
    战斗单位

    设计原则：
    - 必需字段放前面
    - 预留字段用Optional
    - 提供便捷方法
    """

    # ===== 基础信息 =====
    id: str  # 唯一ID（如 "player", "goblin_1"）
    name: str  # 显示名称
    combatant_type: CombatantType  # 类型

    # ===== 生命值 =====
    hp: int  # 当前HP
    max_hp: int  # 最大HP

    # ===== 防御 =====
    ac: int  # 护甲等级（Armor Class）

    # ===== 攻击属性 =====
    attack_bonus: int  # 攻击加值（影响命中）
    damage_dice: str  # 伤害骰子（如 "1d6"）
    damage_bonus: int  # 伤害加值
    damage_type: str = "slashing"  # 伤害类型

    # ===== 先攻 =====
    initiative_bonus: int = 0  # 先攻加值
    initiative_roll: int = 0  # 实际骰出的先攻值（战斗时填充）

    # ===== 状态 =====
    is_alive: bool = True
    status_effects: List[StatusEffectInstance] = field(default_factory=list)

    # ===== 行动经济 =====
    action_available: bool = True
    bonus_action_available: bool = True
    reaction_available: bool = True
    speed: int = 1
    movement_points: int = 1

    # ===== 预留：进阶属性（6大属性） =====
    abilities: Optional[Dict[str, int]] = None
    # 示例：{"strength": 12, "dexterity": 14, "constitution": 10, ...}

    # ===== 预留：装备 =====
    weapon_id: Optional[str] = None
    armor_id: Optional[str] = None
    offhand_damage_dice: Optional[str] = None
    offhand_damage_bonus: int = 0

    # ===== 预留：AI配置 =====
    ai_personality: Optional[str] = None  # 敌人AI性格

    # ===== 预留：法术配置 =====
    spells_known: List[str] = field(default_factory=list)
    spell_slots: Dict[int, int] = field(default_factory=dict)
    spell_attack_bonus: int = 0
    spell_save_dc: int = 10

    # ===== 预留：抗性 =====
    resistances: List[str] = field(default_factory=list)
    vulnerabilities: List[str] = field(default_factory=list)
    immunities: List[str] = field(default_factory=list)

    # ===== 便捷方法 =====

    def is_player(self) -> bool:
        """是否是玩家"""
        return self.combatant_type == CombatantType.PLAYER

    def is_enemy(self) -> bool:
        """是否是敌人"""
        return self.combatant_type == CombatantType.ENEMY

    def is_ally(self) -> bool:
        """是否是队友（预留）"""
        return self.combatant_type == CombatantType.ALLY

    def take_damage(self, amount: int) -> int:
        """
        受到伤害

        Args:
            amount: 伤害值

        Returns:
            int: 实际受到的伤害（不会为负）
        """
        actual_damage = min(amount, self.hp)
        self.hp -= actual_damage

        if self.hp <= 0:
            self.hp = 0
            self.is_alive = False

        return actual_damage

    def heal(self, amount: int) -> int:
        """
        恢复生命值

        Args:
            amount: 恢复量

        Returns:
            int: 实际恢复的量
        """
        actual_heal = min(amount, self.max_hp - self.hp)
        self.hp += actual_heal
        return actual_heal

    def add_status_effect(self, effect: StatusEffect, duration: int, source: str = ""):
        """添加状态效果"""
        self.status_effects.append(
            StatusEffectInstance(effect=effect, duration=duration, source=source)
        )

    def has_status_effect(self, effect: StatusEffect) -> bool:
        """检查是否有某状态"""
        return any(se.effect == effect for se in self.status_effects)

    def remove_expired_effects(self):
        """移除过期的状态效果（回合结束时调用）"""
        self.status_effects = [se for se in self.status_effects if not se.tick()]

    def reset_turn_resources(self):
        """回合开始时重置行动经济"""
        self.action_available = True
        self.bonus_action_available = True
        self.reaction_available = True
        self.movement_points = self.speed

    def has_available_actions(self) -> bool:
        """是否还有可用行动（行动/附赠/移动）"""
        return (
            self.action_available
            or self.bonus_action_available
            or self.movement_points > 0
        )

    def consume_action(self, cost_type: str) -> bool:
        """消耗行动资源"""
        if cost_type == "action":
            if not self.action_available:
                return False
            self.action_available = False
            return True
        if cost_type == "bonus":
            if not self.bonus_action_available:
                return False
            self.bonus_action_available = False
            return True
        if cost_type == "reaction":
            if not self.reaction_available:
                return False
            self.reaction_available = False
            return True
        if cost_type == "movement":
            if self.movement_points <= 0:
                return False
            self.movement_points -= 1
            return True
        return False

    def ability_modifier(self, ability: str) -> int:
        """获取能力值修正"""
        if not self.abilities or ability not in self.abilities:
            return 0
        return (self.abilities[ability] - 10) // 2

    def get_effective_ac(self) -> int:
        """
        获取有效AC（考虑状态效果）

        预留扩展：防御姿态 +2 AC
        """
        ac = self.ac
        if self.has_status_effect(StatusEffect.DEFENDING):
            ac += 2
        return ac

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.combatant_type.value,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "ac": self.ac,
            "is_alive": self.is_alive,
            "action_available": self.action_available,
            "bonus_action_available": self.bonus_action_available,
            "reaction_available": self.reaction_available,
            "movement_points": self.movement_points,
            "speed": self.speed,
            "resistances": self.resistances,
            "vulnerabilities": self.vulnerabilities,
            "immunities": self.immunities,
            "status_effects": [
                {"effect": se.effect.value, "duration": se.duration}
                for se in self.status_effects
            ],
        }
