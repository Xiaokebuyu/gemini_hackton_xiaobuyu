"""NarrativeOps — 叙事操作（从 SessionRuntime 提取，P7 瘦身）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)


class NarrativeOps:
    """章节切换 / 目标完成 / NPC 好感度操作。"""

    def __init__(self, session: 'SessionRuntime') -> None:
        self._s = session

    def advance_chapter(
        self, target_chapter_id: str, transition_type: str = "normal"
    ) -> Dict[str, Any]:
        """切换章节（纯内存操作，persist() 统一持久化）。"""
        from datetime import datetime

        narrative = self._s.narrative
        if not narrative:
            return {"success": False, "error": "narrative not loaded"}

        old_chapter = getattr(narrative, "current_chapter", None)

        # 验证目标章节存在
        world = self._s.world
        if world and hasattr(world, "chapter_registry"):
            if target_chapter_id not in world.chapter_registry:
                return {
                    "success": False,
                    "error": f"unknown chapter: {target_chapter_id}",
                    "available_chapters": list(world.chapter_registry.keys()),
                }

        # 记录旧章节完成
        if old_chapter and old_chapter != target_chapter_id:
            if old_chapter not in narrative.chapters_completed:
                narrative.chapters_completed.append(old_chapter)

        # 切换章节 + 重置计数器
        narrative.current_chapter = target_chapter_id
        narrative.events_triggered = []
        narrative.chapter_started_at = datetime.now()
        narrative.rounds_in_chapter = 0
        narrative.rounds_since_last_progress = 0

        # 分支历史
        if old_chapter and old_chapter != target_chapter_id:
            narrative.branch_history.append({
                "from": old_chapter,
                "to": target_chapter_id,
                "type": transition_type or "normal",
                "at": datetime.now().isoformat(),
            })

        # 清理 active_chapters
        if narrative.active_chapters:
            narrative.active_chapters = [
                cid for cid in narrative.active_chapters
                if cid and cid != old_chapter
            ]

        self._s.mark_narrative_dirty()

        # 同步 game_state
        if self._s.game_state:
            self._s.game_state.chapter_id = target_chapter_id
            self._s.mark_game_state_dirty()

        # 收集新章节解锁的地图
        new_maps: list = []
        if world and target_chapter_id in world.chapter_registry:
            chapter_data = world.chapter_registry[target_chapter_id]
            if isinstance(chapter_data, dict):
                new_maps = chapter_data.get("available_maps", [])
            elif hasattr(chapter_data, "available_maps"):
                new_maps = chapter_data.available_maps or []

        return {
            "success": True,
            "previous_chapter": old_chapter,
            "new_chapter": target_chapter_id,
            "transition_type": transition_type,
            "new_maps_unlocked": new_maps,
        }

    def complete_objective(self, objective_id: str) -> Dict[str, Any]:
        """标记章节目标完成（纯内存操作）。"""
        narrative = self._s.narrative
        if not narrative:
            return {"success": False, "error": "narrative not loaded"}

        # 获取章节数据用于验证
        chapter_id = getattr(narrative, "current_chapter", None)
        world = self._s.world
        chapter_data = None
        if world and hasattr(world, "chapter_registry") and chapter_id:
            chapter_data = world.chapter_registry.get(chapter_id)

        # 查找目标
        obj_description = ""
        if chapter_data:
            objectives = chapter_data.get("objectives", []) if isinstance(chapter_data, dict) else getattr(chapter_data, "objectives", [])
            for obj in objectives:
                obj_id = obj.get("id", "") if isinstance(obj, dict) else getattr(obj, "id", "")
                if obj_id == objective_id:
                    obj_description = obj.get("description", "") if isinstance(obj, dict) else getattr(obj, "description", "")
                    break
            else:
                return {
                    "success": False,
                    "error": f"objective not found: {objective_id}",
                    "available_objectives": [
                        (obj.get("id", "") if isinstance(obj, dict) else getattr(obj, "id", ""))
                        for obj in objectives
                    ],
                }

        # 检查是否已完成
        completed = getattr(narrative, "objectives_completed", []) or []
        if objective_id in completed:
            return {"success": False, "error": f"objective already completed: {objective_id}"}

        # 标记完成
        narrative.objectives_completed.append(objective_id)
        self._s.mark_narrative_dirty()

        return {
            "success": True,
            "objective_id": objective_id,
            "description": obj_description,
            "total_completed": len(narrative.objectives_completed),
        }

    def update_disposition(
        self,
        npc_id: str,
        deltas: Dict[str, int],
        reason: str = "",
    ) -> Dict[str, Any]:
        """更新 NPC 好感度（纯内存操作，通过 WorldGraph 存储）。"""
        valid_dims = {"approval", "trust", "fear", "romance"}
        cleaned: Dict[str, int] = {}
        for dim, val in (deltas or {}).items():
            if dim not in valid_dims:
                continue
            clamped = max(-20, min(20, int(val)))
            if clamped != 0:
                cleaned[dim] = clamped

        if not cleaned:
            return {"success": False, "error": "no valid disposition deltas"}

        wg = self._s.world_graph
        if not wg or self._s._world_graph_failed:
            return {"success": False, "error": "WorldGraph not available"}

        node = wg.get_node(npc_id)
        if not node:
            return {"success": False, "error": f"NPC node not found: {npc_id}"}

        # 读取当前好感度
        dispositions = node.state.get("dispositions", {})
        current = dispositions.get("player", {
            "approval": 0, "trust": 0, "fear": 0, "romance": 0, "history": [],
        })

        # 应用 deltas + clamp
        clamp_ranges = {
            "approval": (-100, 100),
            "trust": (-100, 100),
            "fear": (0, 100),
            "romance": (0, 100),
        }
        game_day = getattr(self._s.time, "day", None) if self._s.time else None
        history_entry: Dict[str, Any] = {"reason": reason, "day": game_day}
        for dim, delta in cleaned.items():
            lo, hi = clamp_ranges.get(dim, (-100, 100))
            old_val = current.get(dim, 0)
            current[dim] = max(lo, min(hi, old_val + delta))
            history_entry[f"delta_{dim}"] = delta

        # 追加历史（保留最近 50 条）
        history = current.get("history", [])
        if not isinstance(history, list):
            history = []
        history.append(history_entry)
        if len(history) > 50:
            history = history[-50:]
        current["history"] = history

        # 写回图节点
        wg.merge_state(npc_id, {"dispositions": {"player": current}})

        # 返回不含 history 的精简视图
        result_view = {dim: current.get(dim, 0) for dim in ("approval", "trust", "fear", "romance")}
        return {"success": True, "npc_id": npc_id, "applied_deltas": cleaned, "current": result_view}
