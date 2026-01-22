"""
战斗结果数据模型
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .combat_session import CombatEndReason


@dataclass
class CombatRewards:
    """战斗奖励"""

    xp: int = 0
    gold: int = 0
    items: List[str] = field(default_factory=list)  # 物品ID列表

    def to_dict(self) -> Dict[str, Any]:
        return {"xp": self.xp, "gold": self.gold, "items": self.items}


@dataclass
class CombatPenalty:
    """战斗惩罚（失败时）"""

    gold_lost: int = 0
    items_lost: List[str] = field(default_factory=list)
    respawn_location: str = "village_temple"  # 复活地点

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gold_lost": self.gold_lost,
            "items_lost": self.items_lost,
            "respawn_location": self.respawn_location,
        }


@dataclass
class CombatResult:
    """
    战斗最终结果

    用于返回给Game Master Loop和LLM
    """

    # ===== 基础信息 =====
    combat_id: str
    result: CombatEndReason

    # ===== 摘要（LLM叙事用） =====
    summary: str  # 如 "你在3回合内击败了3只哥布林"

    # ===== 奖励/惩罚 =====
    rewards: Optional[CombatRewards] = None
    penalty: Optional[CombatPenalty] = None

    # ===== 玩家状态 =====
    player_hp_remaining: int = 0
    player_max_hp: int = 0
    items_used: List[str] = field(default_factory=list)  # 消耗的物品

    # ===== 完整日志（可选，给UI详细显示） =====
    full_log: List[str] = field(default_factory=list)

    # ===== 统计数据（可选） =====
    total_rounds: int = 0
    total_damage_dealt: int = 0
    total_damage_taken: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result_dict = {
            "combat_id": self.combat_id,
            "result": self.result.value,
            "summary": self.summary,
            "player_state": {
                "hp_remaining": self.player_hp_remaining,
                "max_hp": self.player_max_hp,
                "items_used": self.items_used,
            },
            "statistics": {
                "total_rounds": self.total_rounds,
                "damage_dealt": self.total_damage_dealt,
                "damage_taken": self.total_damage_taken,
            },
        }

        if self.rewards:
            result_dict["rewards"] = self.rewards.to_dict()

        if self.penalty:
            result_dict["penalty"] = self.penalty.to_dict()

        if self.full_log:
            result_dict["full_log"] = self.full_log

        return result_dict

    def to_llm_summary(self) -> str:
        """
        生成给LLM的简短摘要

        Returns:
            str: 适合LLM继续叙事的文本
        """
        lines = [self.summary]

        if self.result == CombatEndReason.VICTORY and self.rewards:
            reward_parts = []
            if self.rewards.xp > 0:
                reward_parts.append(f"获得了{self.rewards.xp}点经验值")
            if self.rewards.gold > 0:
                reward_parts.append(f"{self.rewards.gold}金币")
            if self.rewards.items:
                items_text = "、".join(self.rewards.items)
                reward_parts.append(f"物品：{items_text}")

            if reward_parts:
                lines.append("你" + "、".join(reward_parts) + "。")

        elif self.result == CombatEndReason.DEFEAT and self.penalty:
            lines.append(
                f"你失去了{self.penalty.gold_lost}金币，醒来时发现自己在{self.penalty.respawn_location}。"
            )

        lines.append(f"你当前生命值：{self.player_hp_remaining}/{self.player_max_hp}")

        return "\n".join(lines)
