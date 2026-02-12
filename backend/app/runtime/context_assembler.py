"""ContextAssembler — 分层上下文组装器（Phase 3 实现）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.runtime.models.layered_context import LayeredContext

logger = logging.getLogger(__name__)


def _get_chapter_context(session: Any) -> Dict[str, Any]:
    """从 session.narrative + session.world.chapter_registry 提取章节上下文。

    包含：当前章节名称、描述、目标、事件列表及状态、转换条件状态。
    """
    narrative = getattr(session, "narrative", None)
    if narrative is None:
        return {}

    current_chapter_id = getattr(narrative, "current_chapter", None)
    if not current_chapter_id:
        return {}

    ctx: Dict[str, Any] = {
        "chapter_id": current_chapter_id,
        "mainline_id": getattr(narrative, "current_mainline", ""),
        "rounds_in_chapter": getattr(narrative, "rounds_in_chapter", 0),
        "rounds_since_last_progress": getattr(narrative, "rounds_since_last_progress", 0),
    }

    # 从 WorldInstance.chapter_registry 获取章节定义
    world = getattr(session, "world", None)
    if world is None:
        return ctx

    # 卷级叙事概述 — 从 mainline_registry 注入主线名称和描述
    mainline_id = ctx.get("mainline_id", "")
    mainline_registry = getattr(world, "mainline_registry", {})
    mainline_data = mainline_registry.get(mainline_id)
    if mainline_data:
        if isinstance(mainline_data, dict):
            ctx["current_mainline"] = {
                "id": mainline_id,
                "name": mainline_data.get("name", ""),
                "description": mainline_data.get("description", ""),
            }
        else:
            ctx["current_mainline"] = {
                "id": mainline_id,
                "name": getattr(mainline_data, "name", ""),
                "description": getattr(mainline_data, "description", ""),
            }

    chapter_registry = getattr(world, "chapter_registry", {})
    chapter_data = chapter_registry.get(current_chapter_id)
    if not chapter_data:
        return ctx

    # chapter_data 可能是 dict（从 Firestore 加载）
    if isinstance(chapter_data, dict):
        ctx["name"] = chapter_data.get("name", "")
        ctx["description"] = chapter_data.get("description", "")

        # 注入 pacing 配置
        pacing_raw = chapter_data.get("pacing", {})
        if pacing_raw:
            ctx["pacing"] = {
                "stall_threshold": pacing_raw.get("stall_threshold", 5),
                "hint_escalation": pacing_raw.get("hint_escalation", []),
            }

        # 目标
        objectives = chapter_data.get("objectives", [])
        completed_objectives = getattr(narrative, "objectives_completed", []) or []
        ctx["objectives"] = [
            {
                "id": obj.get("id", "") if isinstance(obj, dict) else getattr(obj, "id", ""),
                "description": obj.get("description", "") if isinstance(obj, dict) else getattr(obj, "description", ""),
                "completed": (
                    (obj.get("id", "") if isinstance(obj, dict) else getattr(obj, "id", ""))
                    in completed_objectives
                ),
            }
            for obj in objectives
        ]

        # 事件列表及状态
        events = chapter_data.get("events", [])
        events_triggered = getattr(narrative, "events_triggered", []) or []
        ctx["events"] = [
            {
                "id": ev.get("id", "") if isinstance(ev, dict) else getattr(ev, "id", ""),
                "name": ev.get("name", "") if isinstance(ev, dict) else getattr(ev, "name", ""),
                "triggered": (
                    (ev.get("id", "") if isinstance(ev, dict) else getattr(ev, "id", ""))
                    in events_triggered
                ),
            }
            for ev in events
        ]

        # 转换条件（简要信息）
        transitions = chapter_data.get("transitions", [])
        if transitions:
            ctx["transitions"] = [
                {
                    "target": t.get("target_chapter_id", "") if isinstance(t, dict) else getattr(t, "target_chapter_id", ""),
                    "type": t.get("transition_type", "normal") if isinstance(t, dict) else getattr(t, "transition_type", "normal"),
                }
                for t in transitions
            ]
    else:
        # Chapter 模型对象
        ctx["name"] = getattr(chapter_data, "name", "")
        ctx["description"] = getattr(chapter_data, "description", "")

        # 注入 pacing 配置
        pacing = getattr(chapter_data, "pacing", None)
        if pacing:
            ctx["pacing"] = {
                "stall_threshold": getattr(pacing, "stall_threshold", 5),
                "hint_escalation": getattr(pacing, "hint_escalation", []),
            }

        completed_objectives = getattr(narrative, "objectives_completed", []) or []
        ctx["objectives"] = [
            {
                "id": obj.id,
                "description": obj.description,
                "completed": obj.id in completed_objectives,
            }
            for obj in getattr(chapter_data, "objectives", [])
        ]

        events_triggered = getattr(narrative, "events_triggered", []) or []
        ctx["events"] = [
            {
                "id": ev.id,
                "name": ev.name,
                "triggered": ev.id in events_triggered,
            }
            for ev in getattr(chapter_data, "events", [])
        ]

        transitions = getattr(chapter_data, "transitions", [])
        if transitions:
            ctx["transitions"] = [
                {
                    "target": t.target_chapter_id,
                    "type": t.transition_type,
                }
                for t in transitions
            ]

    # 章节转换就绪评估（机械条件检查）
    area = getattr(session, "current_area", None)
    if area and hasattr(area, "check_chapter_transition"):
        transition_ready = area.check_chapter_transition(session)
        if transition_ready:
            ctx["chapter_transition_available"] = transition_ready

    # 下一章预览 — 从第一个有效转换的目标章节提取
    _raw_transitions = (
        chapter_data.get("transitions", [])
        if isinstance(chapter_data, dict)
        else getattr(chapter_data, "transitions", [])
    )
    for _t in _raw_transitions:
        if isinstance(_t, dict):
            _target_id = _t.get("target_chapter_id", "")
            _hint = _t.get("narrative_hint", "")
        else:
            _target_id = getattr(_t, "target_chapter_id", "")
            _hint = getattr(_t, "narrative_hint", "")
        if not _target_id:
            continue
        _target_ch = chapter_registry.get(_target_id)
        if _target_ch:
            if isinstance(_target_ch, dict):
                ctx["next_chapter_preview"] = {
                    "id": _target_id,
                    "name": _target_ch.get("name", ""),
                    "narrative_hint": _hint,
                    "available_areas": _target_ch.get("available_maps", []),
                }
            else:
                ctx["next_chapter_preview"] = {
                    "id": _target_id,
                    "name": getattr(_target_ch, "name", ""),
                    "narrative_hint": _hint,
                    "available_areas": getattr(_target_ch, "available_maps", []),
                }
            break

    return ctx


class ContextAssembler:
    """纯机械上下文组装 — 零逻辑，只做数据映射。

    6 个数据包映射:
    ① 地点信息 → Layer 2 + 3
    ② NPC 信息 → Layer 2
    ③ 战斗实体 → Layer 2
    ④ 章节/事件 → Layer 1
    ⑤ 基础状态 → Layer 4
    ⑥ 好感度   → Layer 4

    Phase 3 完整实现。
    """

    @staticmethod
    def assemble(world: Any, session: Any) -> LayeredContext:
        """组装分层上下文。

        Args:
            world: WorldInstance 实例。
            session: SessionRuntime 实例。

        Returns:
            LayeredContext 分层上下文。
        """
        area = getattr(session, "current_area", None)

        # Layer 0: 世界常量 + 角色花名册
        world_ctx: Dict[str, Any] = (
            world.world_constants.to_context()
            if world and getattr(world, "world_constants", None)
            else {}
        )
        if world and getattr(world, "character_registry", None):
            world_ctx["character_roster"] = [
                {
                    "id": cid,
                    "name": cdata.get("name", cid),
                    "description": cdata.get("description", ""),
                    "location": (
                        cdata.get("profile", {}).get("metadata", {}).get("default_map", "")
                        if isinstance(cdata.get("profile"), dict)
                        else ""
                    ),
                }
                for cid, cdata in world.character_registry.items()
            ]

        return LayeredContext(
            world=world_ctx,
            chapter=_get_chapter_context(session),
            area=(
                area.get_area_context(world, session)
                if area
                else {}
            ),
            location=(
                area.get_location_context(session.sub_location)
                if area and getattr(session, "sub_location", None)
                else None
            ),
            dynamic=ContextAssembler._build_dynamic_state(session),
            memory=None,  # 由 PipelineOrchestrator 后续填充
        )

    @staticmethod
    def _build_dynamic_state(session: Any) -> Dict[str, Any]:
        """构建 Layer 4 动态状态。

        从 session 提取：
        - player 摘要
        - party 成员列表
        - 时间
        - 好感度（预留，Phase 5 填充）
        - 对话历史
        """
        state: Dict[str, Any] = {}

        # 玩家角色全量结构
        player = getattr(session, "player", None)
        if player and hasattr(player, "model_dump"):
            state["player_character"] = player.model_dump(
                exclude={"created_at", "updated_at"}
            )

        # 队伍成员
        party = getattr(session, "party", None)
        if party and hasattr(party, "get_active_members"):
            state["party_members"] = [
                {
                    "character_id": m.character_id,
                    "name": m.name,
                    "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                    "is_active": m.is_active,
                    "personality": m.personality,
                    "current_mood": m.current_mood,
                }
                for m in party.get_active_members()
            ]
        else:
            state["party_members"] = []

        # 时间
        time_state = getattr(session, "time", None)
        if time_state and hasattr(time_state, "model_dump"):
            state["game_time"] = time_state.model_dump()
        elif time_state and isinstance(time_state, dict):
            state["game_time"] = time_state

        # 好感度（由 PipelineOrchestrator._inject_dispositions 填充，此处提供空 fallback）
        state["affinity"] = {}

        # 对话历史（短摘要，完整历史在 PipelineOrchestrator 注入）
        history = getattr(session, "history", None)
        if history and hasattr(history, "get_recent_history"):
            state["recent_history_preview"] = history.get_recent_history(
                max_tokens=4000
            )

        # 游戏状态元信息
        game_state = getattr(session, "game_state", None)
        if game_state:
            state["chat_mode"] = getattr(game_state, "chat_mode", None)
            state["active_dialogue_npc"] = getattr(game_state, "active_dialogue_npc", None)
            state["combat_id"] = getattr(game_state, "combat_id", None)

        return state
