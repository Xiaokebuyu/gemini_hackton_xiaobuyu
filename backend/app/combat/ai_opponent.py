"""
敌人AI系统

实现敌人的决策逻辑
"""
import random
from typing import Optional

from .models.combatant import Combatant
from .models.action import ActionType, ActionOption
from .models.combat_session import CombatSession
from .rules import AI_PERSONALITIES


class OpponentAI:
    """
    敌人AI

    设计原则：
    - 简单规则树（MVP阶段）
    - 预留扩展接口
    """

    def __init__(self, session: CombatSession):
        """
        初始化AI

        Args:
            session: 当前战斗会话
        """
        self.session = session

    def decide_action(self, enemy: Combatant) -> ActionOption:
        """
        为敌人决定行动

        Args:
            enemy: 敌人战斗单位

        Returns:
            ActionOption: 选择的行动
        """
        # 获取AI性格配置
        personality_name = enemy.ai_personality or "aggressive"
        personality = AI_PERSONALITIES.get(
            personality_name, AI_PERSONALITIES["aggressive"]
        )

        # 1. 检查是否应该逃跑
        if self._should_flee(enemy, personality):
            return self._create_flee_action(enemy)

        # 2. 检查是否应该防御
        if self._should_defend(enemy, personality):
            return self._create_defend_action(enemy)

        # 3. 选择攻击目标
        target = self._select_target(enemy, personality)

        if target:
            return self._create_attack_action(enemy, target)

        # 4. 无法行动，防御
        return self._create_defend_action(enemy)

    # ===== 私有方法 =====

    def _should_flee(self, enemy: Combatant, personality: dict) -> bool:
        """
        判断是否应该逃跑

        Args:
            enemy: 敌人
            personality: 性格配置

        Returns:
            bool: 是否应该逃跑
        """
        flee_threshold = personality.get("flee_threshold", 0.0)

        if flee_threshold == 0:
            return False  # 永不逃跑

        hp_ratio = enemy.hp / enemy.max_hp

        # HP低于阈值时有50%几率逃跑
        if hp_ratio < flee_threshold:
            return random.random() < 0.5

        return False

    def _should_defend(self, enemy: Combatant, personality: dict) -> bool:
        """
        判断是否应该防御

        Args:
            enemy: 敌人
            personality: 性格配置

        Returns:
            bool: 是否应该防御
        """
        # 如果性格偏好防御
        if personality.get("prefer_defend", False):
            hp_ratio = enemy.hp / enemy.max_hp
            # HP低于50%时有30%几率防御
            if hp_ratio < 0.5:
                return random.random() < 0.3

        return False

    def _select_target(self, enemy: Combatant, personality: dict) -> Optional[Combatant]:
        """
        选择攻击目标

        Args:
            enemy: 敌人
            personality: 性格配置

        Returns:
            Optional[Combatant]: 目标（可能为None）
        """
        # 获取所有可攻击的目标（玩家 + 队友）
        targets = []
        for combatant in self.session.get_alive_combatants():
            if combatant.is_player() or combatant.is_ally():
                targets.append(combatant)

        if not targets:
            return None

        # 根据性格选择目标
        if personality.get("prefer_weaker_targets", False):
            # 优先攻击血量最低的
            return min(targets, key=lambda target: target.hp)

        if personality.get("prefer_wounded_targets", False):
            # 优先攻击受伤的（血量比例低的）
            wounded = [t for t in targets if t.hp < t.max_hp]
            if wounded:
                return min(wounded, key=lambda t: t.hp / t.max_hp)

        # 默认：随机选择
        return random.choice(targets)

    # ===== 创建行动选项 =====

    def _create_attack_action(self, enemy: Combatant, target: Combatant) -> ActionOption:
        """创建攻击行动"""
        return ActionOption(
            action_id=f"ai_attack_{enemy.id}_{target.id}",
            action_type=ActionType.ATTACK,
            display_name=f"{enemy.name}攻击{target.name}",
            description=f"{enemy.name}挥舞武器攻击{target.name}",
            target_id=target.id,
        )

    def _create_defend_action(self, enemy: Combatant) -> ActionOption:
        """创建防御行动"""
        return ActionOption(
            action_id=f"ai_defend_{enemy.id}",
            action_type=ActionType.DEFEND,
            display_name=f"{enemy.name}防御",
            description=f"{enemy.name}进入防御姿态",
        )

    def _create_flee_action(self, enemy: Combatant) -> ActionOption:
        """创建逃跑行动"""
        return ActionOption(
            action_id=f"ai_flee_{enemy.id}",
            action_type=ActionType.FLEE,
            display_name=f"{enemy.name}逃跑",
            description=f"{enemy.name}试图逃离战斗",
        )
