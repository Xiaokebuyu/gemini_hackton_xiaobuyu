"""
战斗系统 MCP 服务器

暴露标准 MCP 工具接口
"""
import json
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .combat_engine import CombatEngine
from .enemy_registry import list_templates, register_archetype, register_template
from .models.combat_session import CombatState


# 初始化 FastMCP
combat_mcp = FastMCP(
    name="Combat System MCP",
    instructions="""
战斗系统 MCP 服务器

提供程序化的DND风格战斗系统，完全不使用LLM。

核心功能：
- 回合制战斗
- 自动敌人AI
- 骰子判定
- 战斗奖励/惩罚

使用流程：
1. start_combat - 初始化战斗
2. get_available_actions - 获取玩家可用行动
3. execute_action - 执行玩家选择的行动
4. 重复2-3直到战斗结束
5. get_combat_result - 获取最终结果
""",
)

# 全局战斗引擎实例
combat_engine = CombatEngine()


# ============================================
# MCP 工具定义
# ============================================


@combat_mcp.tool()
async def start_combat(
    enemies: List[Dict[str, Any]],
    player_state: Dict[str, Any],
    environment: Optional[Dict[str, Any]] = None,
    allies: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    开始战斗

    Args:
        enemies: 敌人列表，每个元素包含 type 和 level
            示例：[{"type": "goblin", "level": 1}, {"type": "goblin", "level": 1}]
        player_state: 玩家战斗状态
            必需字段：hp, max_hp, ac, attack_bonus, damage_dice, damage_bonus
        environment: 环境配置（可选）

    Returns:
        str: JSON字符串，包含combat_id和初始状态
    """
    session = combat_engine.start_combat(
        enemies=enemies,
        player_state=player_state,
        environment=environment,
        allies=allies,
    )

    return json.dumps(
        {
            "combat_id": session.combat_id,
            "state": session.state.value,
            "turn_order": session.turn_order,
            "current_turn": session.get_current_actor().name
            if session.get_current_actor()
            else None,
            "combatants": session.to_dict()["combatants"],
        },
        ensure_ascii=False,
        indent=2,
    )


@combat_mcp.tool()
async def get_available_actions(combat_id: str) -> str:
    """
    获取玩家当前可用的行动选项

    Args:
        combat_id: 战斗ID

    Returns:
        str: JSON字符串，包含行动选项列表
    """
    actions = combat_engine.get_available_actions(combat_id)

    return json.dumps(
        {"combat_id": combat_id, "actions": [action.to_dict() for action in actions]},
        ensure_ascii=False,
        indent=2,
    )


@combat_mcp.tool()
async def get_available_actions_for_actor(combat_id: str, actor_id: str) -> str:
    """
    获取指定角色可用行动选项
    """
    actions = combat_engine.get_available_actions_for_actor(combat_id, actor_id)

    return json.dumps(
        {
            "combat_id": combat_id,
            "actor_id": actor_id,
            "actions": [action.to_dict() for action in actions],
        },
        ensure_ascii=False,
        indent=2,
    )


@combat_mcp.tool()
async def execute_action(combat_id: str, action_id: str) -> str:
    """
    执行玩家选择的行动

    Args:
        combat_id: 战斗ID
        action_id: 行动ID（从 get_available_actions 获取）

    Returns:
        str: JSON字符串，包含行动结果和当前战斗状态
    """
    result = combat_engine.execute_action(combat_id, action_id)
    session = combat_engine.get_combat_state(combat_id)

    response = {
        "combat_id": combat_id,
        "action_result": {
            "display_text": result.to_display_text(),
            "success": result.success,
        },
        "combat_state": {
            "state": session.state.value,
            "round": session.current_round,
            "is_ended": session.state == CombatState.ENDED,
            "combatants": session.to_dict()["combatants"],
        },
    }

    # 如果战斗已结束，附加结果
    if session.state == CombatState.ENDED:
        combat_result = combat_engine.get_combat_result(combat_id)
        response["final_result"] = {
            "result": combat_result.result.value,
            "summary": combat_result.to_llm_summary(),
        }

    return json.dumps(response, ensure_ascii=False, indent=2)


@combat_mcp.tool()
async def execute_action_for_actor(
    combat_id: str, actor_id: str, action_id: str
) -> str:
    """
    指定角色执行行动
    """
    result = combat_engine.execute_action_for_actor(combat_id, actor_id, action_id)
    session = combat_engine.get_combat_state(combat_id)

    response = {
        "combat_id": combat_id,
        "actor_id": actor_id,
        "action_result": {
            "display_text": result.to_display_text(),
            "success": result.success,
        },
        "combat_state": {
            "state": session.state.value,
            "round": session.current_round,
            "is_ended": session.state == CombatState.ENDED,
            "combatants": session.to_dict()["combatants"],
        },
    }

    if session.state == CombatState.ENDED:
        combat_result = combat_engine.get_combat_result(combat_id)
        response["final_result"] = {
            "result": combat_result.result.value,
            "summary": combat_result.to_llm_summary(),
        }

    return json.dumps(response, ensure_ascii=False, indent=2)


@combat_mcp.tool()
async def get_combat_state(combat_id: str) -> str:
    """
    获取当前战斗状态

    Args:
        combat_id: 战斗ID

    Returns:
        str: JSON字符串，包含完整战斗状态
    """
    session = combat_engine.get_combat_state(combat_id)

    if not session:
        return json.dumps({"error": "Combat session not found"})

    return json.dumps(session.to_dict(), ensure_ascii=False, indent=2, default=str)


@combat_mcp.tool()
async def get_combat_events(
    combat_id: str, since_seq: int = 0, limit: int = 100
) -> str:
    """
    获取结构化战斗事件（用于前端推送/轮询）
    """
    session = combat_engine.get_combat_state(combat_id)

    if not session:
        return json.dumps({"error": "Combat session not found"})

    events = session.get_event_log_since(since_seq=since_seq, limit=limit)
    return json.dumps(
        {
            "combat_id": combat_id,
            "events": [event.to_dict() for event in events],
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@combat_mcp.tool()
async def get_pending_turn_requests(
    combat_id: str, since_seq: int = 0
) -> str:
    """
    获取未处理的回合请求队列
    """
    session = combat_engine.get_combat_state(combat_id)

    if not session:
        return json.dumps({"error": "Combat session not found"})

    requests = session.get_pending_turn_requests(since_seq=since_seq)
    return json.dumps(
        {
            "combat_id": combat_id,
            "turn_requests": [request.to_dict() for request in requests],
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@combat_mcp.tool()
async def get_combat_result(combat_id: str) -> str:
    """
    获取战斗最终结果

    仅在战斗结束后调用

    Args:
        combat_id: 战斗ID

    Returns:
        str: JSON字符串，包含战斗结果、奖励和摘要
    """
    try:
        result = combat_engine.get_combat_result(combat_id)
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@combat_mcp.tool()
async def register_enemy_template(template: Dict[str, Any]) -> str:
    """
    注册完整敌人模板
    """
    try:
        registered = register_template(template)
        return json.dumps(
            {
                "status": "ok",
                "enemy_type": registered["enemy_type"],
                "template": registered,
            },
            ensure_ascii=False,
            indent=2,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)


@combat_mcp.tool()
async def register_enemy_archetype(spec: Dict[str, Any]) -> str:
    """
    注册敌人范式（role/tier 自动生成属性）
    """
    try:
        registered = register_archetype(spec)
        return json.dumps(
            {
                "status": "ok",
                "enemy_type": registered["enemy_type"],
                "template": registered,
            },
            ensure_ascii=False,
            indent=2,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)


@combat_mcp.tool()
async def list_enemy_templates(
    scene: Optional[str] = None, tags: Optional[List[str]] = None
) -> str:
    """
    列出敌人模板（可按场景/标签过滤）
    """
    templates = list_templates(tags=tags, scene=scene)
    return json.dumps(
        {"templates": templates},
        ensure_ascii=False,
        indent=2,
    )


# ============================================
# 服务器启动
# ============================================


def run_combat_mcp_server(transport: str = "stdio"):
    """
    启动战斗MCP服务器

    Args:
        transport: 传输方式（stdio/streamable-http/sse）
    """
    combat_mcp.run(transport=transport)


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    run_combat_mcp_server(transport)
