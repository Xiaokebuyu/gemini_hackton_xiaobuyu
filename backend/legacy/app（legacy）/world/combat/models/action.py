"""
战斗行动数据模型
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class ActionType(str, Enum):
    """行动类型"""

    ATTACK = "attack"
    DEFEND = "defend"
    USE_ITEM = "use_item"
    FLEE = "flee"
    SPELL = "spell"  # 预留
    MOVE = "move"
    DASH = "dash"
    DISENGAGE = "disengage"
    SHOVE = "shove"
    THROW = "throw"
    OFFHAND_ATTACK = "offhand_attack"
    END_TURN = "end_turn"


@dataclass
class ActionOption:
    """
    可用行动选项（给玩家选择）
    """

    action_id: str  # 唯一ID（如 "attack_goblin_1"）
    action_type: ActionType  # 类型
    display_name: str  # 显示名称
    description: str  # 描述
    target_id: Optional[str] = None  # 目标ID（攻击时需要）
    item_id: Optional[str] = None  # 物品ID（使用物品时需要）
    success_rate: Optional[float] = None  # 预计成功率（可选显示）
    cost_type: Optional[str] = None  # action/bonus/reaction/movement
    range_band: Optional[str] = None  # 距离段位
    damage_type: Optional[str] = None  # 伤害类型

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "action_id": self.action_id,
            "type": self.action_type.value,
            "display_name": self.display_name,
            "description": self.description,
            "target_id": self.target_id,
            "item_id": self.item_id,
            "success_rate": self.success_rate,
            "cost_type": self.cost_type,
            "range_band": self.range_band,
            "damage_type": self.damage_type,
        }


@dataclass
class DiceRoll:
    """骰子结果"""

    dice_notation: str  # 骰子记号（如 "1d20"）
    roll_result: int  # 骰出的值
    modifier: int  # 修正值
    total: int  # 总值

    def __str__(self) -> str:
        if self.modifier >= 0:
            return f"{self.dice_notation} ({self.roll_result}) + {self.modifier} = {self.total}"
        return f"{self.dice_notation} ({self.roll_result}) - {abs(self.modifier)} = {self.total}"


@dataclass
class AttackRoll:
    """攻击判定结果"""

    hit_roll: DiceRoll  # 命中判定骰
    target_ac: int  # 目标AC
    is_hit: bool  # 是否命中
    is_critical: bool = False  # 是否暴击（预留）

    def to_display_text(self) -> str:
        """转换为可读文本"""
        hit_text = str(self.hit_roll)
        result_text = f"vs AC {self.target_ac} → {'命中！' if self.is_hit else '未命中'}"

        if self.is_critical:
            result_text = f"暴击！{result_text}"

        return f"{hit_text} {result_text}"


@dataclass
class DamageRoll:
    """伤害判定结果"""

    damage_roll: DiceRoll  # 伤害骰
    actual_damage: int  # 实际造成的伤害

    def to_display_text(self) -> str:
        """转换为可读文本"""
        return f"{self.damage_roll} → 造成 {self.actual_damage} 点伤害"


@dataclass
class ActionResult:
    """
    行动执行结果
    """

    action_id: str
    action_type: ActionType
    actor_id: str
    target_id: Optional[str] = None

    # 结果
    success: bool = True

    # 攻击相关
    attack_roll: Optional[AttackRoll] = None
    damage_roll: Optional[DamageRoll] = None

    # 逃跑相关
    flee_roll: Optional[DiceRoll] = None

    # 消息（给UI显示）
    messages: list[str] = None

    def __post_init__(self):
        if self.messages is None:
            self.messages = []

    def add_message(self, message: str):
        """添加消息"""
        self.messages.append(message)

    def to_display_text(self) -> str:
        """
        转换为格式化文本（给UI显示）

        Returns:
            str: 多行文本
        """
        return "\n".join(self.messages)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "action_id": self.action_id,
            "type": self.action_type.value,
            "actor": self.actor_id,
            "target": self.target_id,
            "success": self.success,
            "display_text": self.to_display_text(),
        }
