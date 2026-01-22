"""
战斗引擎

核心战斗逻辑实现
"""
import uuid
from typing import List, Optional, Tuple

from .ai_opponent import OpponentAI
from .dice import DiceRoller, d20
from .enemy_registry import get_template as get_enemy_template
from .effects import apply_start_of_turn_effects, attack_advantage_state, is_incapacitated
from .models.action import (
    ActionOption,
    ActionResult,
    ActionType,
    AttackRoll,
    DamageRoll,
    DiceRoll,
)
from .models.combatant import Combatant, CombatantType, StatusEffect
from .models.combat_result import CombatPenalty, CombatResult, CombatRewards
from .models.combat_session import CombatEndReason, CombatSession, CombatState
from .rules import ITEM_EFFECTS, calculate_hit_chance, get_defeat_penalty, get_flee_difficulty
from .spatial import DistanceBand, SimpleDistanceProvider
from .spells import SPELL_TEMPLATES

ACTION_COSTS = {
    ActionType.ATTACK: "action",
    ActionType.DEFEND: "action",
    ActionType.USE_ITEM: "bonus",
    ActionType.FLEE: "action",
    ActionType.SPELL: "action",
    ActionType.MOVE: "movement",
    ActionType.DASH: "action",
    ActionType.DISENGAGE: "action",
    ActionType.SHOVE: "bonus",
    ActionType.THROW: "action",
    ActionType.OFFHAND_ATTACK: "bonus",
}

RANGE_BANDS = [DistanceBand.ENGAGED, DistanceBand.CLOSE, DistanceBand.NEAR, DistanceBand.FAR, DistanceBand.DISTANT]


class CombatEngine:
    """
    战斗引擎

    职责：
    - 初始化战斗
    - 执行行动
    - 管理回合流程
    - 判定胜负
    """

    def __init__(self):
        """初始化引擎"""
        self.sessions: dict[str, CombatSession] = {}

    # ============================================
    # 公共接口
    # ============================================

    def start_combat(
        self,
        enemies: List[dict],
        player_state: dict,
        environment: Optional[dict] = None,
        allies: Optional[List[dict]] = None,
    ) -> CombatSession:
        """
        开始战斗

        Args:
            enemies: 敌人列表
                示例：[{"type": "goblin", "level": 1}, ...]
            player_state: 玩家战斗状态
                示例：{"hp": 50, "max_hp": 50, "ac": 15, ...}
            environment: 环境配置（可选）

        Returns:
            CombatSession: 战斗会话

        流程：
        1. 创建战斗会话
        2. 创建战斗单位（玩家+敌人）
        3. 骰先攻
        4. 排序行动顺序
        5. 返回会话
        """
        combat_id = f"combat_{uuid.uuid4().hex[:8]}"
        session = CombatSession(combat_id=combat_id, environment=environment)

        # 1. 创建玩家
        player = self._create_player_combatant(player_state)
        session.combatants.append(player)

        # 2. 创建队友（可选）
        if allies:
            for index, ally_state in enumerate(allies):
                ally = self._create_ally_combatant(ally_state, index=index + 1)
                session.combatants.append(ally)

        # 3. 创建敌人
        for index, enemy_data in enumerate(enemies):
            enemy = self._create_enemy_combatant(enemy_data, index=index + 1)
            session.combatants.append(enemy)

        # 初始化距离系统
        session.spatial = SimpleDistanceProvider()
        ally_ids = [
            combatant.id
            for combatant in session.combatants
            if combatant.is_player() or combatant.is_ally()
        ]
        session.spatial.initialize([c.id for c in session.combatants], allies=ally_ids)

        # 4. 骰先攻
        self._roll_initiative(session)

        # 5. 排序行动顺序
        session.turn_order = sorted(
            [combatant.id for combatant in session.combatants],
            key=lambda combatant_id: session.get_combatant(combatant_id).initiative_roll,
            reverse=True,
        )

        # 6. 设置状态
        session.state = CombatState.INITIALIZED

        # 7. 添加初始日志
        session.add_log(
            "system",
            f"战斗开始！行动顺序：{', '.join(session.turn_order)}",
            event_type="system",
        )

        # 8. 检查第一个行动者
        first_actor = session.get_current_actor()
        if first_actor and (first_actor.is_player() or first_actor.is_ally()):
            self._set_waiting_player_input(session)
        else:
            session.state = CombatState.IN_PROGRESS
            self._begin_turn(session)
            self._run_enemy_turns_until_player(session)

        # 9. 保存会话
        self.sessions[combat_id] = session

        return session

    def get_available_actions(self, combat_id: str) -> List[ActionOption]:
        """
        获取玩家当前可用的行动选项

        Args:
            combat_id: 战斗ID

        Returns:
            List[ActionOption]: 行动选项列表

        流程：
        1. 检查是否轮到玩家
        2. 生成攻击选项（所有存活敌人）
        3. 生成防御选项
        4. 生成物品选项（从玩家背包读取）
        5. 生成逃跑选项
        """
        return self.get_available_actions_for_actor(combat_id, "player")

    def get_available_actions_for_actor(
        self, combat_id: str, actor_id: str
    ) -> List[ActionOption]:
        """获取指定角色当前可用行动选项"""
        session = self.sessions.get(combat_id)
        if not session:
            return []

        current_actor = session.get_current_actor()
        if not current_actor or current_actor.id != actor_id:
            return []

        if not (current_actor.is_player() or current_actor.is_ally()):
            return []

        actions = []

        if is_incapacitated(current_actor):
            actions.append(
                ActionOption(
                    action_id="end_turn",
                    action_type=ActionType.END_TURN,
                    display_name="结束回合",
                    description="你处于无法行动的状态，结束回合",
                )
            )
            return actions

        # 1. 移动选项
        if current_actor.movement_points > 0:
            actions.append(
                ActionOption(
                    action_id="move_closer",
                    action_type=ActionType.MOVE,
                    display_name="移动靠近",
                    description="向敌人靠近一段距离",
                    cost_type="movement",
                )
            )
            actions.append(
                ActionOption(
                    action_id="move_away",
                    action_type=ActionType.MOVE,
                    display_name="后撤",
                    description="与敌人拉开一段距离",
                    cost_type="movement",
                )
            )

        # 2. 攻击选项（近战）
        if current_actor.action_available:
            for enemy in session.get_enemies():
                distance = session.spatial.get_distance(current_actor.id, enemy.id)
                if distance not in (DistanceBand.ENGAGED, DistanceBand.CLOSE):
                    continue
                hit_chance = calculate_hit_chance(current_actor.attack_bonus, enemy.ac)
                actions.append(
                    ActionOption(
                        action_id=f"attack_{enemy.id}",
                        action_type=ActionType.ATTACK,
                        display_name=f"攻击 {enemy.name}",
                        description=f"用{current_actor.weapon_id or '武器'}攻击{enemy.name}",
                        target_id=enemy.id,
                        success_rate=hit_chance,
                        cost_type="action",
                        range_band=distance.value,
                        damage_type=current_actor.damage_type,
                    )
                )

        # 3. 投掷攻击
        if current_actor.action_available:
            for enemy in session.get_enemies():
                distance = session.spatial.get_distance(current_actor.id, enemy.id)
                if distance not in (DistanceBand.NEAR, DistanceBand.FAR, DistanceBand.CLOSE):
                    continue
                actions.append(
                    ActionOption(
                        action_id=f"throw_{enemy.id}",
                        action_type=ActionType.THROW,
                        display_name=f"投掷攻击 {enemy.name}",
                        description=f"投掷物品攻击{enemy.name}",
                        target_id=enemy.id,
                        cost_type="action",
                        range_band=distance.value,
                        damage_type="bludgeoning",
                    )
                )

        # 4. 副手攻击
        if current_actor.bonus_action_available and current_actor.offhand_damage_dice:
            for enemy in session.get_enemies():
                distance = session.spatial.get_distance(current_actor.id, enemy.id)
                if distance not in (DistanceBand.ENGAGED, DistanceBand.CLOSE):
                    continue
                actions.append(
                    ActionOption(
                        action_id=f"offhand_{enemy.id}",
                        action_type=ActionType.OFFHAND_ATTACK,
                        display_name=f"副手攻击 {enemy.name}",
                        description=f"用副手攻击{enemy.name}",
                        target_id=enemy.id,
                        cost_type="bonus",
                        range_band=distance.value,
                        damage_type=current_actor.damage_type,
                    )
                )

        # 5. 推撞
        if current_actor.bonus_action_available:
            for enemy in session.get_enemies():
                distance = session.spatial.get_distance(current_actor.id, enemy.id)
                if distance != DistanceBand.ENGAGED:
                    continue
                actions.append(
                    ActionOption(
                        action_id=f"shove_{enemy.id}",
                        action_type=ActionType.SHOVE,
                        display_name=f"推撞 {enemy.name}",
                        description=f"尝试推倒并击退{enemy.name}",
                        target_id=enemy.id,
                        cost_type="bonus",
                        range_band=distance.value,
                    )
                )

        # 6. 防御
        if current_actor.action_available:
            actions.append(
                ActionOption(
                    action_id="defend",
                    action_type=ActionType.DEFEND,
                    display_name="防御",
                    description="进入防御姿态，AC+2直到下回合",
                    cost_type="action",
                )
            )

        # 7. 脱离
        if current_actor.action_available:
            engaged = any(
                session.spatial.get_distance(current_actor.id, enemy.id)
                == DistanceBand.ENGAGED
                for enemy in session.get_enemies()
            )
            if engaged:
                actions.append(
                    ActionOption(
                        action_id="disengage",
                        action_type=ActionType.DISENGAGE,
                        display_name="脱离",
                        description="本回合离开近战不触发借机攻击",
                        cost_type="action",
                    )
                )

        # 8. 疾跑
        if current_actor.action_available:
            actions.append(
                ActionOption(
                    action_id="dash",
                    action_type=ActionType.DASH,
                    display_name="疾跑",
                    description="获得额外移动",
                    cost_type="action",
                )
            )

        # 9. 物品选项（硬编码治疗药水）
        if current_actor.bonus_action_available:
            actions.append(
                ActionOption(
                    action_id="use_healing_potion",
                    action_type=ActionType.USE_ITEM,
                    display_name="使用治疗药水",
                    description="恢复2d4+2生命值",
                    item_id="healing_potion",
                    cost_type="bonus",
                )
            )

        # 10. 法术选项
        for spell_id in current_actor.spells_known:
            template = SPELL_TEMPLATES.get(spell_id)
            if not template:
                continue
            cost_type = "bonus" if template.get("bonus_action") else "action"
            if cost_type == "bonus" and not current_actor.bonus_action_available:
                continue
            if cost_type == "action" and not current_actor.action_available:
                continue
            level = template.get("level", 0)
            if level and current_actor.spell_slots.get(level, 0) <= 0:
                continue
            range_band = template.get("range", "near")

            if template.get("type") == "heal":
                targets = [
                    current_actor,
                    *[c for c in session.get_alive_combatants() if c.is_ally()],
                ]
            else:
                targets = session.get_enemies()

            for target in targets:
                distance = session.spatial.get_distance(current_actor.id, target.id)
                if not self._distance_in_range(distance, range_band):
                    continue
                actions.append(
                    ActionOption(
                        action_id=f"spell_{spell_id}_{target.id}",
                        action_type=ActionType.SPELL,
                        display_name=f"施放 {template['name']} -> {target.name}",
                        description=f"施放{template['name']}",
                        target_id=target.id,
                        cost_type=cost_type,
                        range_band=distance.value,
                        damage_type=template.get("damage_type"),
                    )
                )

        # 11. 逃跑选项
        if current_actor.action_available:
            actions.append(
                ActionOption(
                    action_id="flee",
                    action_type=ActionType.FLEE,
                    display_name="逃跑",
                    description="尝试逃离战斗（50%成功率）",
                    cost_type="action",
                )
            )

        # 12. 结束回合
        actions.append(
            ActionOption(
                action_id="end_turn",
                action_type=ActionType.END_TURN,
                display_name="结束回合",
                description="结束当前回合",
            )
        )

        return actions

    def execute_action_for_actor(
        self, combat_id: str, actor_id: str, action_id: str
    ) -> ActionResult:
        """指定角色执行行动（用于多LLM协作）"""
        session = self.sessions.get(combat_id)
        if not session:
            raise ValueError(f"Combat session not found: {combat_id}")

        current_actor = session.get_current_actor()
        if not current_actor or current_actor.id != actor_id:
            raise ValueError("It is not this actor's turn")

        return self.execute_action(combat_id, action_id)

    def execute_action(self, combat_id: str, action_id: str) -> ActionResult:
        """
        执行行动

        Args:
            combat_id: 战斗ID
            action_id: 行动ID

        Returns:
            ActionResult: 行动结果

        流程：
        1. 解析action_id，确定行动类型
        2. 执行对应的行动逻辑
        3. 记录战斗日志
        4. 检查战斗是否结束
        5. 如果未结束，推进回合
        6. 如果下一个是敌人，自动执行敌人回合
        """
        session = self.sessions.get(combat_id)
        if not session:
            raise ValueError(f"Combat session not found: {combat_id}")

        current_actor = session.get_current_actor()
        if not current_actor:
            raise ValueError("No current actor")

        self._begin_turn(session)

        # 处理回合请求
        if current_actor.is_player() or current_actor.is_ally():
            session.mark_turn_request_handled(current_actor.id)

        # 无法行动
        if is_incapacitated(current_actor) and action_id != "end_turn":
            result = ActionResult(
                action_id="end_turn",
                action_type=ActionType.END_TURN,
                actor_id=current_actor.id,
            )
            result.success = False
            result.add_message(f"{current_actor.name}无法行动")
            self._record_action_logs(session, current_actor, result)
            self._end_turn(session, current_actor)
            return result

        # 解析行动
        if action_id == "end_turn":
            result = ActionResult(
                action_id="end_turn",
                action_type=ActionType.END_TURN,
                actor_id=current_actor.id,
            )
            result.add_message(f"{current_actor.name}结束回合")
            self._record_action_logs(session, current_actor, result)
            self._end_turn(session, current_actor)
            self._run_enemy_turns_until_player(session)
            return result

        if action_id.startswith("move_"):
            direction = action_id.replace("move_", "")
            result = self._execute_move(session, current_actor, direction)
        elif action_id.startswith("attack_"):
            if not current_actor.consume_action(ACTION_COSTS[ActionType.ATTACK]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.ATTACK
                )
            else:
                target_id = action_id.replace("attack_", "")
                result = self._execute_attack(session, current_actor, target_id)
        elif action_id.startswith("offhand_"):
            if not current_actor.consume_action(ACTION_COSTS[ActionType.OFFHAND_ATTACK]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.OFFHAND_ATTACK
                )
            else:
                target_id = action_id.replace("offhand_", "")
                result = self._execute_offhand_attack(session, current_actor, target_id)
        elif action_id.startswith("throw_"):
            if not current_actor.consume_action(ACTION_COSTS[ActionType.THROW]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.THROW
                )
            else:
                target_id = action_id.replace("throw_", "")
                result = self._execute_throw(session, current_actor, target_id)
        elif action_id.startswith("shove_"):
            if not current_actor.consume_action(ACTION_COSTS[ActionType.SHOVE]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.SHOVE
                )
            else:
                target_id = action_id.replace("shove_", "")
                result = self._execute_shove(session, current_actor, target_id)
        elif action_id == "defend":
            if not current_actor.consume_action(ACTION_COSTS[ActionType.DEFEND]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.DEFEND
                )
            else:
                result = self._execute_defend(session, current_actor)
        elif action_id == "dash":
            if not current_actor.consume_action(ACTION_COSTS[ActionType.DASH]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.DASH
                )
            else:
                result = self._execute_dash(session, current_actor)
        elif action_id == "disengage":
            if not current_actor.consume_action(ACTION_COSTS[ActionType.DISENGAGE]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.DISENGAGE
                )
            else:
                result = self._execute_disengage(session, current_actor)
        elif action_id.startswith("spell_"):
            parts = action_id.split("_", 2)
            if len(parts) < 3:
                raise ValueError("Invalid spell action id")
            spell_id = parts[1]
            target_id = parts[2]
            result = self._execute_spell(session, current_actor, spell_id, target_id)
        elif action_id.startswith("use_"):
            if not current_actor.consume_action(ACTION_COSTS[ActionType.USE_ITEM]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.USE_ITEM
                )
            else:
                item_id = action_id.replace("use_", "")
                result = self._execute_use_item(session, current_actor, item_id)
        elif action_id == "flee":
            if not current_actor.consume_action(ACTION_COSTS[ActionType.FLEE]):
                result = self._build_cost_failed_result(
                    current_actor, action_id, ActionType.FLEE
                )
            else:
                result = self._execute_flee(session, current_actor)
        else:
            raise ValueError(f"Unknown action: {action_id}")

        self._record_action_logs(session, current_actor, result)

        if session.state == CombatState.ENDED:
            return result

        end_reason = session.check_combat_end()
        if end_reason:
            session.state = CombatState.ENDED
            session.end_reason = end_reason
            return result

        if current_actor.is_enemy():
            self._end_turn(session, current_actor)
            self._run_enemy_turns_until_player(session)
            return result

        if current_actor.has_available_actions():
            self._set_waiting_player_input(session)
            return result

        self._end_turn(session, current_actor)
        self._run_enemy_turns_until_player(session)
        return result

    def get_combat_state(self, combat_id: str) -> Optional[CombatSession]:
        """获取战斗状态"""
        return self.sessions.get(combat_id)

    def get_combat_result(self, combat_id: str) -> CombatResult:
        """
        获取战斗结果

        Args:
            combat_id: 战斗ID

        Returns:
            CombatResult: 战斗结果

        流程：
        1. 检查战斗是否已结束
        2. 根据结束原因生成摘要
        3. 计算奖励/惩罚
        4. 返回结果
        """
        session = self.sessions.get(combat_id)
        if not session:
            raise ValueError(f"Combat session not found: {combat_id}")

        if session.state != CombatState.ENDED:
            raise ValueError("Combat is not ended")

        player = session.get_player()

        # 生成摘要
        if session.end_reason == CombatEndReason.VICTORY:
            summary = f"你在{session.current_round}回合内击败了敌人"
            rewards = self._calculate_rewards(session)
            penalty = None

        elif session.end_reason == CombatEndReason.DEFEAT:
            summary = "你被敌人击败了"
            rewards = None
            # TODO: 从玩家状态读取金币
            penalty = CombatPenalty(**get_defeat_penalty(player_gold=100))

        elif session.end_reason == CombatEndReason.FLED:
            summary = "你成功逃离了战斗"
            rewards = None
            penalty = None

        else:
            summary = "战斗以特殊方式结束"
            rewards = None
            penalty = None

        # 生成完整日志
        full_log = [entry.message for entry in session.combat_log]

        return CombatResult(
            combat_id=combat_id,
            result=session.end_reason,
            summary=summary,
            rewards=rewards,
            penalty=penalty,
            player_hp_remaining=player.hp if player else 0,
            player_max_hp=player.max_hp if player else 0,
            items_used=[],  # TODO: 跟踪使用的物品
            full_log=full_log,
            total_rounds=session.current_round,
        )

    # ============================================
    # 私有方法 - 行动执行
    # ============================================

    def _execute_attack(
        self, session: CombatSession, attacker: Combatant, target_id: str
    ) -> ActionResult:
        """
        执行攻击

        流程：
        1. 获取目标
        2. 骰命中判定（d20 + 攻击加值 vs 目标AC）
        3. 如果命中，骰伤害（武器骰子 + 伤害加值）
        4. 目标受伤
        5. 生成结果
        """
        target = session.get_combatant(target_id)
        if not target:
            raise ValueError(f"Target not found: {target_id}")

        result = ActionResult(
            action_id=f"attack_{target_id}",
            action_type=ActionType.ATTACK,
            actor_id=attacker.id,
            target_id=target_id,
        )

        result.add_message(f"{attacker.name}攻击{target.name}")

        distance = session.spatial.get_distance(attacker.id, target.id)
        if distance not in (DistanceBand.ENGAGED, DistanceBand.CLOSE):
            result.success = False
            result.add_message("距离过远，无法近战攻击")
            return result

        # 1. 命中判定
        advantage_state = attack_advantage_state(
            attacker, target, distance=distance, is_ranged=False
        )
        hit_roll_value, roll_note = self._roll_d20_with_advantage(advantage_state)
        if roll_note:
            result.add_message(roll_note)
        hit_total = hit_roll_value + attacker.attack_bonus
        target_effective_ac = target.get_effective_ac()
        is_hit = hit_total >= target_effective_ac
        is_critical = hit_roll_value == 20

        attack_roll = AttackRoll(
            hit_roll=DiceRoll(
                dice_notation="1d20",
                roll_result=hit_roll_value,
                modifier=attacker.attack_bonus,
                total=hit_total,
            ),
            target_ac=target_effective_ac,
            is_hit=is_hit,
            is_critical=is_critical,
        )
        result.attack_roll = attack_roll
        result.add_message(attack_roll.to_display_text())

        # 2. 如果命中，计算伤害
        if is_hit:
            damage_total, _ = DiceRoller.roll(attacker.damage_dice)
            if is_critical:
                extra_damage, _ = DiceRoller.roll(attacker.damage_dice)
                damage_total += extra_damage
            damage_total += attacker.damage_bonus

            modified_damage = self._apply_damage_modifiers(
                target, damage_total, attacker.damage_type
            )
            actual_damage = target.take_damage(modified_damage)

            damage_roll = DamageRoll(
                damage_roll=DiceRoll(
                    dice_notation=attacker.damage_dice,
                    roll_result=damage_total - attacker.damage_bonus,
                    modifier=attacker.damage_bonus,
                    total=damage_total,
                ),
                actual_damage=actual_damage,
            )
            result.damage_roll = damage_roll
            result.add_message(damage_roll.to_display_text())

            if not target.is_alive:
                result.add_message(f"{target.name}被击败了！")
        else:
            result.add_message("攻击未命中")
            result.success = False

        return result

    def _execute_defend(self, session: CombatSession, defender: Combatant) -> ActionResult:
        """执行防御"""
        result = ActionResult(
            action_id="defend", action_type=ActionType.DEFEND, actor_id=defender.id
        )

        # 添加防御状态效果（持续1回合）
        defender.add_status_effect(StatusEffect.DEFENDING, duration=1)

        result.add_message(f"{defender.name}进入防御姿态（AC+2直到下回合）")

        return result

    def _execute_use_item(
        self, session: CombatSession, user: Combatant, item_id: str
    ) -> ActionResult:
        """使用物品"""
        result = ActionResult(
            action_id=f"use_{item_id}",
            action_type=ActionType.USE_ITEM,
            actor_id=user.id,
        )

        item_config = ITEM_EFFECTS.get(item_id)
        if not item_config:
            result.success = False
            result.add_message(f"未知物品：{item_id}")
            return result

        # 执行物品效果
        if item_config["effect_type"] == "heal":
            heal_amount, _ = DiceRoller.roll(item_config["heal_amount"])
            actual_heal = user.heal(heal_amount)

            result.add_message(f"{user.name}使用了{item_config['name']}")
            result.add_message(
                f"恢复了{actual_heal}点生命值（当前HP: {user.hp}/{user.max_hp}）"
            )

        return result

    def _execute_flee(self, session: CombatSession, fleer: Combatant) -> ActionResult:
        """执行逃跑"""
        result = ActionResult(
            action_id="flee", action_type=ActionType.FLEE, actor_id=fleer.id
        )

        # 骰d20判定
        flee_roll_value = d20()
        flee_dc = get_flee_difficulty()
        success = flee_roll_value >= flee_dc

        flee_roll = DiceRoll(
            dice_notation="1d20",
            roll_result=flee_roll_value,
            modifier=0,
            total=flee_roll_value,
        )
        result.flee_roll = flee_roll
        result.success = success

        result.add_message(f"{fleer.name}试图逃跑")
        result.add_message(f"逃跑判定：{flee_roll} vs DC {flee_dc}")

        if success:
            result.add_message("成功逃离！")
            session.state = CombatState.ENDED
            session.end_reason = CombatEndReason.FLED
        else:
            result.add_message("逃跑失败，浪费了回合")

        return result

    def _execute_move(self, session: CombatSession, mover: Combatant, direction: str) -> ActionResult:
        """执行移动（抽象距离）"""
        result = ActionResult(
            action_id=f"move_{direction}",
            action_type=ActionType.MOVE,
            actor_id=mover.id,
        )

        delta = -1 if direction == "closer" else 1
        if not mover.consume_action("movement"):
            result.success = False
            result.add_message("移动次数不足")
            return result

        opponents = [
            c
            for c in session.get_alive_combatants()
            if c.id != mover.id and self._is_opponent(mover, c)
        ]

        if delta > 0 and not mover.has_status_effect(StatusEffect.DISENGAGED):
            for opponent in opponents:
                distance = session.spatial.get_distance(mover.id, opponent.id)
                if distance == DistanceBand.ENGAGED:
                    self._execute_reaction_attack(session, opponent, mover)

        for combatant in session.get_alive_combatants():
            if combatant.id == mover.id:
                continue
            session.spatial.adjust_distance(mover.id, combatant.id, delta)

        result.add_message(f"{mover.name}移动{'靠近' if delta < 0 else '后撤'}")
        return result

    def _execute_dash(self, session: CombatSession, runner: Combatant) -> ActionResult:
        """执行疾跑"""
        result = ActionResult(
            action_id="dash", action_type=ActionType.DASH, actor_id=runner.id
        )
        runner.movement_points += runner.speed
        result.add_message(f"{runner.name}疾跑，获得额外移动")
        return result

    def _execute_disengage(self, session: CombatSession, actor: Combatant) -> ActionResult:
        """执行脱离"""
        result = ActionResult(
            action_id="disengage",
            action_type=ActionType.DISENGAGE,
            actor_id=actor.id,
        )
        actor.add_status_effect(StatusEffect.DISENGAGED, duration=1)
        result.add_message(f"{actor.name}脱离战斗（本回合不触发借机）")
        return result

    def _execute_shove(
        self, session: CombatSession, actor: Combatant, target_id: str
    ) -> ActionResult:
        """执行推撞"""
        target = session.get_combatant(target_id)
        result = ActionResult(
            action_id=f"shove_{target_id}",
            action_type=ActionType.SHOVE,
            actor_id=actor.id,
            target_id=target_id,
        )
        if not target:
            result.success = False
            result.add_message("目标不存在")
            return result

        distance = session.spatial.get_distance(actor.id, target.id)
        if distance != DistanceBand.ENGAGED:
            result.success = False
            result.add_message("距离过远，无法推撞")
            return result

        attack_roll = d20() + actor.ability_modifier("strength")
        defense_roll = d20() + max(
            target.ability_modifier("strength"), target.ability_modifier("dexterity")
        )

        if attack_roll >= defense_roll:
            target.add_status_effect(StatusEffect.PRONE, duration=1)
            session.spatial.adjust_distance(actor.id, target.id, 1)
            result.add_message(f"{actor.name}成功推倒{target.name}")
        else:
            result.success = False
            result.add_message(f"{actor.name}推撞失败")

        return result

    def _execute_throw(
        self, session: CombatSession, attacker: Combatant, target_id: str
    ) -> ActionResult:
        """执行投掷攻击"""
        target = session.get_combatant(target_id)
        result = ActionResult(
            action_id=f"throw_{target_id}",
            action_type=ActionType.THROW,
            actor_id=attacker.id,
            target_id=target_id,
        )
        if not target:
            result.success = False
            result.add_message("目标不存在")
            return result

        distance = session.spatial.get_distance(attacker.id, target.id)
        if not self._distance_in_range(distance, "far"):
            result.success = False
            result.add_message("目标太远，无法投掷")
            return result

        advantage_state = attack_advantage_state(
            attacker, target, distance=distance, is_ranged=True
        )
        hit_roll_value, roll_note = self._roll_d20_with_advantage(advantage_state)
        if roll_note:
            result.add_message(roll_note)
        hit_total = hit_roll_value + attacker.attack_bonus
        target_effective_ac = target.get_effective_ac()
        is_hit = hit_total >= target_effective_ac

        attack_roll = AttackRoll(
            hit_roll=DiceRoll(
                dice_notation="1d20",
                roll_result=hit_roll_value,
                modifier=attacker.attack_bonus,
                total=hit_total,
            ),
            target_ac=target_effective_ac,
            is_hit=is_hit,
        )
        result.attack_roll = attack_roll
        result.add_message(attack_roll.to_display_text())

        if is_hit:
            damage_dice = attacker.offhand_damage_dice or "1d4"
            damage_total, _ = DiceRoller.roll(damage_dice)
            damage_total += attacker.damage_bonus
            modified_damage = self._apply_damage_modifiers(
                target, damage_total, "bludgeoning"
            )
            actual_damage = target.take_damage(modified_damage)
            damage_roll = DamageRoll(
                damage_roll=DiceRoll(
                    dice_notation=damage_dice,
                    roll_result=damage_total - attacker.damage_bonus,
                    modifier=attacker.damage_bonus,
                    total=damage_total,
                ),
                actual_damage=actual_damage,
            )
            result.damage_roll = damage_roll
            result.add_message(damage_roll.to_display_text())
        else:
            result.success = False
            result.add_message("投掷未命中")

        return result

    def _execute_offhand_attack(
        self, session: CombatSession, attacker: Combatant, target_id: str
    ) -> ActionResult:
        """执行副手攻击"""
        target = session.get_combatant(target_id)
        result = ActionResult(
            action_id=f"offhand_{target_id}",
            action_type=ActionType.OFFHAND_ATTACK,
            actor_id=attacker.id,
            target_id=target_id,
        )
        if not target:
            result.success = False
            result.add_message("目标不存在")
            return result
        if not attacker.offhand_damage_dice:
            result.success = False
            result.add_message("没有可用的副手武器")
            return result

        distance = session.spatial.get_distance(attacker.id, target.id)
        if distance not in (DistanceBand.ENGAGED, DistanceBand.CLOSE):
            result.success = False
            result.add_message("距离过远，无法副手攻击")
            return result

        advantage_state = attack_advantage_state(
            attacker, target, distance=distance, is_ranged=False
        )
        hit_roll_value, roll_note = self._roll_d20_with_advantage(advantage_state)
        if roll_note:
            result.add_message(roll_note)
        hit_total = hit_roll_value + attacker.attack_bonus
        target_effective_ac = target.get_effective_ac()
        is_hit = hit_total >= target_effective_ac

        attack_roll = AttackRoll(
            hit_roll=DiceRoll(
                dice_notation="1d20",
                roll_result=hit_roll_value,
                modifier=attacker.attack_bonus,
                total=hit_total,
            ),
            target_ac=target_effective_ac,
            is_hit=is_hit,
        )
        result.attack_roll = attack_roll
        result.add_message(attack_roll.to_display_text())

        if is_hit:
            damage_total, _ = DiceRoller.roll(attacker.offhand_damage_dice)
            damage_total += attacker.offhand_damage_bonus
            modified_damage = self._apply_damage_modifiers(
                target, damage_total, attacker.damage_type
            )
            actual_damage = target.take_damage(modified_damage)
            damage_roll = DamageRoll(
                damage_roll=DiceRoll(
                    dice_notation=attacker.offhand_damage_dice,
                    roll_result=damage_total - attacker.offhand_damage_bonus,
                    modifier=attacker.offhand_damage_bonus,
                    total=damage_total,
                ),
                actual_damage=actual_damage,
            )
            result.damage_roll = damage_roll
            result.add_message(damage_roll.to_display_text())
        else:
            result.success = False
            result.add_message("副手攻击未命中")

        return result

    def _execute_spell(
        self, session: CombatSession, caster: Combatant, spell_id: str, target_id: str
    ) -> ActionResult:
        """执行法术"""
        template = SPELL_TEMPLATES.get(spell_id)
        result = ActionResult(
            action_id=f"spell_{spell_id}_{target_id}",
            action_type=ActionType.SPELL,
            actor_id=caster.id,
            target_id=target_id,
        )
        if not template:
            result.success = False
            result.add_message("未知法术")
            return result

        target = session.get_combatant(target_id)
        if not target:
            result.success = False
            result.add_message("目标不存在")
            return result

        distance = session.spatial.get_distance(caster.id, target.id)
        if not self._distance_in_range(distance, template.get("range", "near")):
            result.success = False
            result.add_message("距离过远，无法施法")
            return result

        level = template.get("level", 0)
        if level and caster.spell_slots.get(level, 0) <= 0:
            result.success = False
            result.add_message("法术位不足")
            return result

        cost_type = "bonus" if template.get("bonus_action") else "action"
        if not caster.consume_action(cost_type):
            result.success = False
            result.add_message("行动资源不足，无法施法")
            return result

        if level:
            caster.spell_slots[level] = caster.spell_slots.get(level, 0) - 1

        result.add_message(f"{caster.name}施放{template['name']}")

        if template.get("type") == "heal":
            heal_amount, _ = DiceRoller.roll(template["heal_amount"])
            actual_heal = target.heal(heal_amount)
            result.add_message(
                f"{target.name}恢复了{actual_heal}点生命值（当前HP: {target.hp}/{target.max_hp}）"
            )
            return result

        if template.get("type") == "auto_hit":
            damage_total, _ = DiceRoller.roll(template["damage_dice"])
            modified_damage = self._apply_damage_modifiers(
                target, damage_total, template.get("damage_type", "force")
            )
            actual_damage = target.take_damage(modified_damage)
            result.add_message(f"造成{actual_damage}点伤害")
            return result

        # attack spell
        advantage_state = attack_advantage_state(
            caster, target, distance=distance, is_ranged=True
        )
        hit_roll_value, roll_note = self._roll_d20_with_advantage(advantage_state)
        if roll_note:
            result.add_message(roll_note)
        hit_total = hit_roll_value + caster.spell_attack_bonus
        target_effective_ac = target.get_effective_ac()
        is_hit = hit_total >= target_effective_ac
        attack_roll = AttackRoll(
            hit_roll=DiceRoll(
                dice_notation="1d20",
                roll_result=hit_roll_value,
                modifier=caster.spell_attack_bonus,
                total=hit_total,
            ),
            target_ac=target_effective_ac,
            is_hit=is_hit,
        )
        result.attack_roll = attack_roll
        result.add_message(attack_roll.to_display_text())

        if is_hit:
            damage_total, _ = DiceRoller.roll(template["damage_dice"])
            modified_damage = self._apply_damage_modifiers(
                target, damage_total, template.get("damage_type", "force")
            )
            actual_damage = target.take_damage(modified_damage)
            result.add_message(f"造成{actual_damage}点伤害")

            if template.get("apply_effect"):
                effect_payload = template["apply_effect"]
                try:
                    target.add_status_effect(
                        StatusEffect(effect_payload["effect"]),
                        duration=effect_payload.get("duration", 1),
                    )
                except ValueError:
                    pass
        else:
            result.success = False
            result.add_message("法术未命中")

        return result

    # ============================================
    # 私有方法 - 敌人回合
    # ============================================

    def _execute_enemy_turn(self, session: CombatSession):
        """
        执行敌人回合

        流程：
        1. 调用AI决策
        2. 执行行动
        3. 记录日志
        """
        current_enemy = session.get_current_actor()
        if not current_enemy or not current_enemy.is_enemy():
            return

        if is_incapacitated(current_enemy):
            skip_result = ActionResult(
                action_id="end_turn",
                action_type=ActionType.END_TURN,
                actor_id=current_enemy.id,
            )
            skip_result.add_message(f"{current_enemy.name}无法行动")
            self._record_action_logs(session, current_enemy, skip_result)
            return

        # AI决策
        ai = OpponentAI(session)
        action = ai.decide_action(current_enemy)

        # 执行行动
        if action.action_type == ActionType.ATTACK:
            if not current_enemy.consume_action("action"):
                return
            result = self._execute_attack(session, current_enemy, action.target_id)
        elif action.action_type == ActionType.DEFEND:
            if not current_enemy.consume_action("action"):
                return
            result = self._execute_defend(session, current_enemy)
        elif action.action_type == ActionType.FLEE:
            if not current_enemy.consume_action("action"):
                return
            result = self._execute_flee(session, current_enemy)
        else:
            # 未知行动，跳过
            return

        # 记录日志
        self._record_action_logs(session, current_enemy, result)

    # ============================================
    # 私有方法 - 辅助功能
    # ============================================

    def _create_player_combatant(self, player_state: dict) -> Combatant:
        """从玩家状态创建战斗单位"""
        return Combatant(
            id="player",
            name=player_state.get("name", "玩家"),
            combatant_type=CombatantType.PLAYER,
            hp=player_state["hp"],
            max_hp=player_state["max_hp"],
            ac=player_state.get("ac", 15),
            attack_bonus=player_state.get("attack_bonus", 3),
            damage_dice=player_state.get("damage_dice", "1d6"),
            damage_bonus=player_state.get("damage_bonus", 2),
            damage_type=player_state.get("damage_type", "slashing"),
            initiative_bonus=player_state.get("initiative_bonus", 2),
            weapon_id=player_state.get("weapon_id"),
            armor_id=player_state.get("armor_id"),
            offhand_damage_dice=player_state.get("offhand_damage_dice"),
            offhand_damage_bonus=player_state.get("offhand_damage_bonus", 0),
            abilities=player_state.get("abilities"),
            spells_known=player_state.get("spells_known", []),
            spell_slots=player_state.get("spell_slots", {}),
            spell_attack_bonus=player_state.get("spell_attack_bonus", 0),
            spell_save_dc=player_state.get("spell_save_dc", 10),
            resistances=player_state.get("resistances", []),
            vulnerabilities=player_state.get("vulnerabilities", []),
            immunities=player_state.get("immunities", []),
            speed=player_state.get("speed", 1),
        )

    def _create_enemy_combatant(self, enemy_data: dict, index: int) -> Combatant:
        """从敌人配置创建战斗单位"""
        enemy_type = enemy_data["type"]
        template = get_enemy_template(enemy_type)

        if not template:
            raise ValueError(f"Unknown enemy type: {enemy_type}")

        return Combatant(
            id=f"{enemy_type}_{index}",
            name=f"{template['name']}{index}",
            combatant_type=CombatantType.ENEMY,
            hp=template["max_hp"],
            max_hp=template["max_hp"],
            ac=template["ac"],
            attack_bonus=template["attack_bonus"],
            damage_dice=template["damage_dice"],
            damage_bonus=template["damage_bonus"],
            damage_type=template.get("damage_type", "slashing"),
            initiative_bonus=template["initiative_bonus"],
            ai_personality=template.get("ai_personality", "aggressive"),
            resistances=template.get("resistances", []),
            vulnerabilities=template.get("vulnerabilities", []),
            immunities=template.get("immunities", []),
        )

    def _create_ally_combatant(self, ally_state: dict, index: int) -> Combatant:
        """从队友状态创建战斗单位"""
        ally_id = ally_state.get("id", f"ally_{index}")
        return Combatant(
            id=ally_id,
            name=ally_state.get("name", f"队友{index}"),
            combatant_type=CombatantType.ALLY,
            hp=ally_state["hp"],
            max_hp=ally_state["max_hp"],
            ac=ally_state.get("ac", 13),
            attack_bonus=ally_state.get("attack_bonus", 2),
            damage_dice=ally_state.get("damage_dice", "1d6"),
            damage_bonus=ally_state.get("damage_bonus", 1),
            damage_type=ally_state.get("damage_type", "slashing"),
            initiative_bonus=ally_state.get("initiative_bonus", 1),
            weapon_id=ally_state.get("weapon_id"),
            armor_id=ally_state.get("armor_id"),
            offhand_damage_dice=ally_state.get("offhand_damage_dice"),
            offhand_damage_bonus=ally_state.get("offhand_damage_bonus", 0),
            abilities=ally_state.get("abilities"),
            spells_known=ally_state.get("spells_known", []),
            spell_slots=ally_state.get("spell_slots", {}),
            spell_attack_bonus=ally_state.get("spell_attack_bonus", 0),
            spell_save_dc=ally_state.get("spell_save_dc", 10),
            resistances=ally_state.get("resistances", []),
            vulnerabilities=ally_state.get("vulnerabilities", []),
            immunities=ally_state.get("immunities", []),
            speed=ally_state.get("speed", 1),
        )

    def _roll_initiative(self, session: CombatSession):
        """为所有战斗单位骰先攻"""
        for combatant in session.combatants:
            roll = d20() + combatant.initiative_bonus
            combatant.initiative_roll = roll

    def _calculate_rewards(self, session: CombatSession) -> CombatRewards:
        """计算战斗奖励"""
        total_xp = 0
        total_gold = 0
        items: list[str] = []

        # 遍历所有被击败的敌人
        for combatant in session.combatants:
            if combatant.is_enemy() and not combatant.is_alive:
                # 从模板读取奖励
                enemy_type = combatant.id.rsplit("_", 1)[0]  # 去掉数字后缀
                template = get_enemy_template(enemy_type)

                if template:
                    total_xp += template.get("xp_reward", 0)
                    total_gold += template.get("gold_reward", 0)

        # TODO: 物品掉落逻辑

        return CombatRewards(xp=total_xp, gold=total_gold, items=items)

    def _record_action_logs(
        self, session: CombatSession, actor: Combatant, result: ActionResult
    ):
        """记录行动日志"""
        for msg in result.messages:
            session.add_log(
                actor.id,
                msg,
                event_type=result.action_type.value,
                payload=result.to_dict(),
                action_id=result.action_id,
            )

    def _build_cost_failed_result(
        self, actor: Combatant, action_id: str, action_type: ActionType
    ) -> ActionResult:
        result = ActionResult(
            action_id=action_id, action_type=action_type, actor_id=actor.id
        )
        result.success = False
        result.add_message("行动资源不足")
        return result

    def _apply_damage_modifiers(
        self, target: Combatant, damage: int, damage_type: str
    ) -> int:
        """应用抗性/易伤/免疫"""
        if damage_type in target.immunities:
            return 0
        if damage_type in target.vulnerabilities:
            return damage * 2
        if damage_type in target.resistances:
            return max(1, damage // 2)
        return damage

    def _roll_d20_with_advantage(self, state: str) -> Tuple[int, Optional[str]]:
        """根据优势/劣势骰d20"""
        if state == "advantage":
            roll1 = d20()
            roll2 = d20()
            return max(roll1, roll2), f"优势掷骰：{roll1} / {roll2}"
        if state == "disadvantage":
            roll1 = d20()
            roll2 = d20()
            return min(roll1, roll2), f"劣势掷骰：{roll1} / {roll2}"
        return d20(), None

    def _distance_in_range(self, distance: DistanceBand, range_band: str) -> bool:
        """判断距离是否在范围内"""
        if not distance:
            return True
        try:
            max_band = DistanceBand(range_band)
        except ValueError:
            max_band = DistanceBand.NEAR
        return RANGE_BANDS.index(distance) <= RANGE_BANDS.index(max_band)

    def _is_opponent(self, source: Combatant, target: Combatant) -> bool:
        if source.is_enemy() and (target.is_player() or target.is_ally()):
            return True
        if target.is_enemy() and (source.is_player() or source.is_ally()):
            return True
        return False

    def _execute_reaction_attack(
        self, session: CombatSession, attacker: Combatant, target: Combatant
    ):
        if not attacker.reaction_available:
            return
        attacker.consume_action("reaction")
        result = self._execute_attack(session, attacker, target.id)
        result.add_message(f"{attacker.name}借机攻击{target.name}")
        self._record_action_logs(session, attacker, result)

    def _begin_turn(self, session: CombatSession):
        """开始新回合"""
        current_actor = session.get_current_actor()
        if not current_actor:
            return
        if session.turn_actor_id == current_actor.id:
            return
        session.turn_actor_id = current_actor.id
        current_actor.reset_turn_resources()

        # 回合开始效果
        for message, damage, damage_type in apply_start_of_turn_effects(current_actor):
            modified = self._apply_damage_modifiers(current_actor, damage, damage_type)
            actual = current_actor.take_damage(modified)
            session.add_log(
                "system",
                f"{message}，造成{actual}点{damage_type}伤害",
                event_type="effect",
                payload={"damage": actual, "type": damage_type, "target": current_actor.id},
            )

    def _end_turn(self, session: CombatSession, actor: Combatant):
        """结束当前角色回合"""
        actor.remove_expired_effects()
        session.turn_actor_id = None
        session.advance_turn()
        self._begin_turn(session)

    def _run_enemy_turns_until_player(self, session: CombatSession):
        """连贯执行敌人回合，直到轮到玩家/队友"""
        while session.state != CombatState.ENDED:
            current = session.get_current_actor()
            if not current:
                return
            if current.is_enemy():
                self._begin_turn(session)
                self._execute_enemy_turn(session)
                if session.state == CombatState.ENDED:
                    return
                end_reason = session.check_combat_end()
                if end_reason:
                    session.state = CombatState.ENDED
                    session.end_reason = end_reason
                    return
                self._end_turn(session, current)
                continue
            self._set_waiting_player_input(session)
            return

    def _set_waiting_player_input(self, session: CombatSession):
        """进入等待玩家/队友输入的状态并发出回合请求"""
        session.state = CombatState.WAITING_PLAYER_INPUT
        current_actor = session.get_current_actor()
        if current_actor and (current_actor.is_player() or current_actor.is_ally()):
            self._begin_turn(session)
            session.enqueue_turn_request(current_actor.id)
