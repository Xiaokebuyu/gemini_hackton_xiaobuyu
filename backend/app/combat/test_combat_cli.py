"""
战斗系统命令行测试脚本
"""
import random

from app.combat.combat_engine import CombatEngine
from app.combat.models.action import ActionType


def test_simple_combat():
    """测试简单战斗"""
    random.seed(42)
    engine = CombatEngine()

    # 1. 初始化战斗
    print("=== 初始化战斗 ===")
    session = engine.start_combat(
        enemies=[{"type": "goblin", "level": 1}, {"type": "goblin", "level": 1}],
        player_state={
            "name": "勇者艾伦",
            "hp": 50,
            "max_hp": 50,
            "ac": 15,
            "attack_bonus": 3,
            "damage_dice": "1d6",
            "damage_bonus": 2,
            "initiative_bonus": 100,
        },
    )
    print(f"Combat ID: {session.combat_id}")
    print(f"Turn Order: {session.turn_order}")

    # 2. 战斗循环
    while session.state.value != "ended":
        current_actor = session.get_current_actor()
        print(f"\n=== Round {session.current_round}, {current_actor.name}'s Turn ===")

        if current_actor.is_player():
            # 玩家回合：获取选项
            actions = engine.get_available_actions(session.combat_id)
            print("Available Actions:")
            for i, action in enumerate(actions):
                print(f"  {i+1}. {action.display_name}: {action.description}")

            # 选择第一个攻击行动，否则结束回合
            attack_actions = [a for a in actions if a.action_type == ActionType.ATTACK]
            end_turn_actions = [a for a in actions if a.action_type == ActionType.END_TURN]
            if attack_actions:
                selected_action = attack_actions[0]
            elif end_turn_actions:
                selected_action = end_turn_actions[0]
            else:
                selected_action = actions[0]

            print(f"\n选择：{selected_action.display_name}")
            result = engine.execute_action(
                session.combat_id, selected_action.action_id
            )
            print(result.to_display_text())
        else:
            # 如果先攻不是玩家，防御以推进流程
            result = engine.execute_action(session.combat_id, "defend")
            print(result.to_display_text())

        # 刷新session状态
        session = engine.get_combat_state(session.combat_id)

    # 3. 获取结果
    print("\n=== 战斗结束 ===")
    final_result = engine.get_combat_result(session.combat_id)
    print(final_result.to_llm_summary())


if __name__ == "__main__":
    test_simple_combat()
