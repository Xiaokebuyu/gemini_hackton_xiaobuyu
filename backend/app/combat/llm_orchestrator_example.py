"""
多LLM回合调度示例（本地伪实现）

说明：
- 这里用简单规则代替真实LLM调用
- 真实场景下，将 decide_action_for_actor 替换为 LLM 工具调用逻辑
"""
import random
from typing import Callable, Dict, List

from app.combat.combat_engine import CombatEngine
from app.combat.models.action import ActionOption, ActionType


class FakeLLM:
    """伪LLM：用简单规则选择行动"""

    def __init__(self, name: str, policy: Callable[[List[ActionOption]], ActionOption]):
        self.name = name
        self.policy = policy

    def choose_action(self, actions: List[ActionOption]) -> ActionOption:
        return self.policy(actions)


def choose_first_attack(actions: List[ActionOption]) -> ActionOption:
    for action in actions:
        if action.action_type == ActionType.ATTACK:
            return action
    for action in actions:
        if action.action_type == ActionType.END_TURN:
            return action
    return actions[0]


def choose_random(actions: List[ActionOption]) -> ActionOption:
    return random.choice(actions)


def run_orchestrator_demo():
    random.seed(7)

    engine = CombatEngine()

    # 准备主角 + 2个同伴
    player_state = {
        "name": "主角",
        "hp": 40,
        "max_hp": 40,
        "ac": 14,
        "attack_bonus": 3,
        "damage_dice": "1d6",
        "damage_bonus": 2,
        "initiative_bonus": 2,
    }
    allies = [
        {
            "id": "ally_mage",
            "name": "法师同伴",
            "hp": 26,
            "max_hp": 26,
            "ac": 12,
            "attack_bonus": 4,
            "damage_dice": "1d6",
            "damage_bonus": 1,
            "initiative_bonus": 3,
        },
        {
            "id": "ally_guard",
            "name": "卫士同伴",
            "hp": 55,
            "max_hp": 55,
            "ac": 16,
            "attack_bonus": 2,
            "damage_dice": "1d6",
            "damage_bonus": 2,
            "initiative_bonus": 1,
        },
    ]

    # 启动战斗
    session = engine.start_combat(
        enemies=[{"type": "goblin", "level": 1}, {"type": "goblin", "level": 1}],
        player_state=player_state,
        allies=allies,
    )

    llm_agents: Dict[str, FakeLLM] = {
        "player": FakeLLM("主角LLM", choose_first_attack),
        "ally_mage": FakeLLM("法师LLM", choose_random),
        "ally_guard": FakeLLM("卫士LLM", choose_first_attack),
    }

    print("=== 战斗开始 ===")
    print(f"Combat ID: {session.combat_id}")
    print(f"Turn Order: {session.turn_order}")

    # 调度循环：依赖回合请求队列
    while session.state.value != "ended":
        pending = session.get_pending_turn_requests()
        if not pending:
            # 没有待处理回合时刷新 session
            session = engine.get_combat_state(session.combat_id)
            continue

        for request in pending:
            actor_id = request.actor_id
            llm = llm_agents.get(actor_id)
            if not llm:
                print(f"No LLM assigned for actor: {actor_id}")
                continue

            actions = engine.get_available_actions_for_actor(session.combat_id, actor_id)
            if not actions:
                continue

            chosen = llm.choose_action(actions)
            result = engine.execute_action_for_actor(
                session.combat_id, actor_id, chosen.action_id
            )
            print(f"[{llm.name}] {result.to_display_text()}")

            session = engine.get_combat_state(session.combat_id)
            if session.state.value == "ended":
                break

    print("=== 战斗结束 ===")
    final_result = engine.get_combat_result(session.combat_id)
    print(final_result.to_llm_summary())


if __name__ == "__main__":
    run_orchestrator_demo()
