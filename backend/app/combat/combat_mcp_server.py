"""
战斗系统 MCP 服务器

暴露标准 MCP 工具接口
"""
import argparse
import json
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .combat_engine import CombatEngine
from .enemy_registry import (
    list_templates,
    load_world_templates,
    register_archetype,
    register_template,
)
from .data_repository import CombatDataRepository
from .template_mapper import skill_to_spell_template, slugify
from .models.combat_session import CombatState
from app.models.event import Event, EventContent, EventType, GMEventIngestRequest
from app.models.game import CombatContext
from app.services.game_session_store import GameSessionStore
from app.services.admin.event_service import AdminEventService


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
session_store = GameSessionStore()
event_service = AdminEventService()


def _normalize_enemy_specs(
    enemies: List[Any],
    *,
    world_id: Optional[str] = None,
    template_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Normalize mixed enemy inputs into engine-friendly enemy entries."""
    normalized: List[Dict[str, Any]] = []

    for raw in enemies or []:
        if isinstance(raw, str):
            spec: Dict[str, Any] = {"enemy_id": raw, "count": 1, "level": 1}
        elif isinstance(raw, dict):
            spec = dict(raw)
        else:
            continue

        enemy_id = str(spec.get("enemy_id") or spec.get("type") or "").strip()
        if not enemy_id:
            continue

        try:
            count = int(spec.get("count", 1) or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, 20))

        try:
            level = int(spec.get("level", 1) or 1)
        except (TypeError, ValueError):
            level = 1
        level = max(1, min(level, 20))

        for _ in range(count):
            normalized.append(
                {
                    "type": enemy_id,
                    "enemy_id": enemy_id,
                    "level": level,
                    "variant": spec.get("variant"),
                    "template_version": spec.get("template_version") or template_version,
                    "world_id": spec.get("world_id") or world_id,
                    "tags": list(spec.get("tags", []) or []),
                    "overrides": dict(spec.get("overrides", {}) or {}),
                }
            )

    return normalized


def _build_world_skill_templates(
    world_id: str,
    template_version: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build session-level skill templates from world skills."""
    repository = CombatDataRepository(world_id=world_id, template_version=template_version)
    templates: Dict[str, Dict[str, Any]] = {}

    for skill in repository.list_skills():
        mapped = skill_to_spell_template(skill)
        if not mapped:
            continue

        mapped_id = str(mapped.get("id") or "").strip()
        payload = dict(mapped)
        payload.pop("id", None)
        if mapped_id:
            templates[mapped_id] = dict(payload)

        # Also register aliases to improve spell_id compatibility.
        source_id = str(skill.get("id") or "").strip()
        if source_id:
            templates[source_id] = dict(payload)

        name_alias = slugify(str(skill.get("name") or ""))
        if name_alias:
            templates[name_alias] = dict(payload)

    return templates


def _to_action_v3(action: Dict[str, Any]) -> Dict[str, Any]:
    """Extend legacy action payload into v3-friendly option shape."""
    return {
        "action_id": action.get("action_id"),
        "action_type": action.get("type"),
        "display_name": action.get("display_name", ""),
        "description": action.get("description", ""),
        "target_id": action.get("target_id"),
        "requirements": {},
        "resource_cost": {"type": action.get("cost_type")},
        "hit_formula": None,
        "damage_formula": action.get("damage_type"),
        "effect_refs": [],
        "metadata": {
            "range_band": action.get("range_band"),
            "success_rate": action.get("success_rate"),
        },
    }


# ============================================
# MCP 工具定义
# ============================================


@combat_mcp.tool()
async def start_combat(
    enemies: List[Dict[str, Any]],
    player_state: Dict[str, Any],
    environment: Optional[Dict[str, Any]] = None,
    allies: Optional[List[Dict[str, Any]]] = None,
    template_version: Optional[str] = None,
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
    normalized_enemies = _normalize_enemy_specs(
        enemies,
        template_version=template_version,
    )
    session = combat_engine.start_combat(
        enemies=normalized_enemies,
        player_state=player_state,
        environment=environment,
        allies=allies,
        template_version=template_version,
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
async def start_combat_session(
    world_id: str,
    session_id: str,
    enemies: List[Dict[str, Any]],
    player_state: Dict[str, Any],
    environment: Optional[Dict[str, Any]] = None,
    allies: Optional[List[Dict[str, Any]]] = None,
    combat_context: Optional[Dict[str, Any]] = None,
    template_version: Optional[str] = None,
    use_world_templates: bool = True,
    include_skill_templates: bool = True,
) -> str:
    """
    开始战斗并写入会话状态
    """
    existing_session = await session_store.get_session(world_id, session_id)
    if not existing_session:
        return json.dumps({"error": "session not found"}, ensure_ascii=False)

    normalized_enemies = _normalize_enemy_specs(
        enemies,
        world_id=world_id,
        template_version=template_version,
    )
    if use_world_templates:
        load_world_templates(world_id, template_version=template_version)

    session_env = dict(environment or {})
    if include_skill_templates:
        skill_templates = _build_world_skill_templates(
            world_id=world_id,
            template_version=template_version,
        )
        if skill_templates:
            session_env["skill_templates"] = skill_templates

    session = combat_engine.start_combat(
        enemies=normalized_enemies,
        player_state=player_state,
        environment=session_env,
        allies=allies,
        world_id=world_id,
        template_version=template_version,
    )
    context = CombatContext(**(combat_context or {}))
    await session_store.set_combat(world_id, session_id, session.combat_id, context)
    state = await session_store.get_session(world_id, session_id)

    return json.dumps(
        {
            "combat_id": session.combat_id,
            "combat_state": {
                "combat_id": session.combat_id,
                "state": session.state.value,
                "turn_order": session.turn_order,
                "current_round": session.current_round,
            },
            "session": state.model_dump() if state else None,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@combat_mcp.tool()
async def resolve_combat_session(
    world_id: str,
    session_id: str,
    combat_id: Optional[str] = None,
    use_engine: bool = True,
    result_override: Optional[Dict[str, Any]] = None,
    summary_override: Optional[str] = None,
    dispatch: bool = True,
    recipients: Optional[List[str]] = None,
    per_character: Optional[Dict[str, Any]] = None,
    write_indexes: bool = False,
    validate: bool = False,
    strict: bool = False,
) -> str:
    """
    结算战斗并写入事件（GM图谱），同时清理会话战斗状态。
    """
    session_state = await session_store.get_session(world_id, session_id)
    if not session_state:
        return json.dumps({"error": "session not found"}, ensure_ascii=False)

    combat_id = combat_id or session_state.active_combat_id
    if not combat_id:
        return json.dumps({"error": "combat_id is required"}, ensure_ascii=False)

    if use_engine:
        try:
            result = combat_engine.get_combat_result(combat_id)
            result_payload = result.to_dict()
            summary = summary_override or result.summary
        except ValueError:
            if result_override:
                result_payload = result_override
                summary = summary_override or result_payload.get("summary", "")
            else:
                summary = summary_override or "combat resolved (manual)"
                result_payload = {"summary": summary, "result": "special"}
    else:
        if not result_override:
            return json.dumps({"error": "result_override is required when use_engine=false"}, ensure_ascii=False)
        result_payload = result_override
        summary = summary_override or result_payload.get("summary", "")

    combat_context = session_state.combat_context
    location = combat_context.location if combat_context else None
    participants = combat_context.participants if combat_context else []
    witnesses = combat_context.witnesses if combat_context else []

    event = Event(
        type=EventType.COMBAT,
        game_day=None,
        location=location,
        participants=participants,
        witnesses=witnesses,
        content=EventContent(raw=summary, structured=result_payload),
    )

    dispatch_request = GMEventIngestRequest(
        event=event,
        distribute=dispatch,
        recipients=recipients,
        known_characters=combat_context.known_characters if combat_context else [],
        character_locations=combat_context.character_locations if combat_context else {},
        per_character=per_character or {},
        write_indexes=write_indexes,
        validate_input=validate,
        strict=strict,
    )

    response = await event_service.ingest_event(world_id, dispatch_request)
    await session_store.clear_combat(world_id, session_id)

    return json.dumps(
        {
            "combat_id": combat_id,
            "event_id": response.event_id,
            "dispatched": response.dispatched,
            "player_state": result_payload.get("player_state"),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@combat_mcp.tool()
async def start_combat_v3(
    world_id: str,
    session_id: str,
    enemies: List[Dict[str, Any]],
    player_state: Dict[str, Any],
    environment: Optional[Dict[str, Any]] = None,
    allies: Optional[List[Dict[str, Any]]] = None,
    combat_context: Optional[Dict[str, Any]] = None,
    template_version: Optional[str] = None,
) -> str:
    """Start combat using v3 enemy spec + world templates."""
    raw = await start_combat_session(
        world_id=world_id,
        session_id=session_id,
        enemies=enemies,
        player_state=player_state,
        environment=environment,
        allies=allies,
        combat_context=combat_context,
        template_version=template_version,
        use_world_templates=True,
        include_skill_templates=True,
    )
    payload = json.loads(raw)
    combat_id = payload.get("combat_id")
    if combat_id:
        actions_raw = await get_available_actions(combat_id=combat_id)
        actions_payload = json.loads(actions_raw)
        actions = actions_payload.get("actions", [])
        payload["available_actions_v3"] = [_to_action_v3(action) for action in actions]
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@combat_mcp.tool()
async def get_available_actions_v3(
    combat_id: str,
    actor_id: Optional[str] = None,
) -> str:
    """Get v3 action options for current/selected actor."""
    if actor_id:
        raw = await get_available_actions_for_actor(combat_id=combat_id, actor_id=actor_id)
    else:
        raw = await get_available_actions(combat_id=combat_id)
    payload = json.loads(raw)
    actions = payload.get("actions", [])
    payload["actions_v3"] = [_to_action_v3(action) for action in actions]
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@combat_mcp.tool()
async def execute_action_v3(
    combat_id: str,
    action_id: str,
    actor_id: Optional[str] = None,
) -> str:
    """Execute action via actor-aware v3 wrapper."""
    if actor_id:
        raw = await execute_action_for_actor(
            combat_id=combat_id,
            actor_id=actor_id,
            action_id=action_id,
        )
    else:
        raw = await execute_action(combat_id=combat_id, action_id=action_id)
    payload = json.loads(raw)
    if payload.get("combat_state", {}).get("is_ended"):
        final_result = payload.get("final_result", {})
        if isinstance(final_result, dict):
            payload["resolution_v3"] = {
                "combat_id": combat_id,
                "result": final_result.get("result"),
                "summary": final_result.get("summary", ""),
                "rewards": final_result.get("rewards", {}),
                "player_state": final_result.get("player_state", {}),
                "character_deltas": {},
                "loot_events": [],
                "relationship_impacts": [],
                "event_payload_ref": None,
            }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@combat_mcp.tool()
async def resolve_combat_session_v3(
    world_id: str,
    session_id: str,
    combat_id: Optional[str] = None,
    use_engine: bool = True,
    result_override: Optional[Dict[str, Any]] = None,
    summary_override: Optional[str] = None,
    dispatch: bool = True,
    recipients: Optional[List[str]] = None,
    per_character: Optional[Dict[str, Any]] = None,
    write_indexes: bool = False,
    validate: bool = False,
    strict: bool = False,
) -> str:
    """Resolve combat and return v3-shaped resolution payload."""
    raw = await resolve_combat_session(
        world_id=world_id,
        session_id=session_id,
        combat_id=combat_id,
        use_engine=use_engine,
        result_override=result_override,
        summary_override=summary_override,
        dispatch=dispatch,
        recipients=recipients,
        per_character=per_character,
        write_indexes=write_indexes,
        validate=validate,
        strict=strict,
    )
    payload = json.loads(raw)
    payload["resolution_v3"] = {
        "combat_id": payload.get("combat_id"),
        "result": (result_override or {}).get("result") if isinstance(result_override, dict) else None,
        "summary": summary_override or "",
        "rewards": (result_override or {}).get("rewards", {}) if isinstance(result_override, dict) else {},
        "player_state": payload.get("player_state", {}),
        "character_deltas": {},
        "loot_events": [],
        "relationship_impacts": [],
        "event_payload_ref": payload.get("event_id"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


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
    scene: Optional[str] = None,
    tags: Optional[List[str]] = None,
    world_id: Optional[str] = None,
    template_version: Optional[str] = None,
) -> str:
    """
    列出敌人模板（可按场景/标签过滤）
    """
    templates = list_templates(
        tags=tags,
        scene=scene,
        world_id=world_id,
        template_version=template_version,
    )
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
    parser = argparse.ArgumentParser(description="Combat MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="Transport protocol",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "127.0.0.1"),
        help="Bind host for HTTP transports",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "9102")),
        help="Bind port for HTTP transports",
    )
    args = parser.parse_args()

    combat_mcp.settings.host = args.host
    combat_mcp.settings.port = args.port
    run_combat_mcp_server(args.transport)
