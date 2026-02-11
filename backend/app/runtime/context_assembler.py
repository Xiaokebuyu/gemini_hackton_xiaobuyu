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

    chapter_registry = getattr(world, "chapter_registry", {})
    chapter_data = chapter_registry.get(current_chapter_id)
    if not chapter_data:
        return ctx

    # chapter_data 可能是 dict（从 Firestore 加载）
    if isinstance(chapter_data, dict):
        ctx["name"] = chapter_data.get("name", "")
        ctx["description"] = chapter_data.get("description", "")

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

        return LayeredContext(
            world=(
                world.world_constants.to_context()
                if world and getattr(world, "world_constants", None)
                else {}
            ),
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

        # 玩家摘要
        player = getattr(session, "player", None)
        if player and hasattr(player, "to_summary_text"):
            state["player_summary"] = player.to_summary_text()

        # 队伍成员
        party = getattr(session, "party", None)
        if party and hasattr(party, "get_active_members"):
            state["party_members"] = [
                {
                    "character_id": m.character_id,
                    "name": m.name,
                    "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                    "is_active": m.is_active,
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

        # 好感度（Phase 5 预留）
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
