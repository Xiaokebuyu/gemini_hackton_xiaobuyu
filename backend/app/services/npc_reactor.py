"""NPCReactor — NPC 相关度推荐 + 模板反应收集器。

Direction A.3 实现（4b 简化版）。
遍历在场 NPC，按相关度排序，返回模板反应。

约束:
- 每轮最多 2 个 NPC 反应
- 纯模板，无 LLM 调用（NPC 深度交互走 /interact 端点）
- 私密对话时，非目标 NPC 不反应
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.world.scene_bus import BusEntry, BusEntryType, SceneBus

if TYPE_CHECKING:
    from app.runtime.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)

MAX_REACTIONS_PER_ROUND = 2


class NPCReactor:
    """NPC 相关度推荐 + 模板反应收集器。"""

    def __init__(self, world_graph: Optional[Any] = None) -> None:
        self.wg = world_graph

    def get_relevant_npcs(
        self,
        scene_bus: SceneBus,
        session: "SessionRuntime",
        max_count: int = MAX_REACTIONS_PER_ROUND,
    ) -> List[Dict[str, Any]]:
        """返回当前场景中相关 NPC 列表（按相关度排序）。

        Returns:
            [{"npc_id", "npc_name", "relevance", "node"}, ...]
        """
        if not self.wg:
            return []

        current_area = session.player_location
        if not current_area:
            return []

        npc_ids = self._get_area_npcs(current_area, session.sub_location)
        if not npc_ids:
            return []

        entries = scene_bus.entries
        if not entries:
            return []

        is_private = any(e.visibility.startswith("private:") for e in entries)

        candidates: List[Dict[str, Any]] = []
        for npc_id in npc_ids:
            node = self.wg.get_node(npc_id)
            if not node:
                continue
            if not node.state.get("is_alive", True):
                continue

            npc_name = node.name

            if is_private:
                private_entries = [
                    e for e in entries if e.visibility.startswith("private:")
                ]
                if private_entries:
                    targets = {e.visibility.split(":", 1)[1] for e in private_entries}
                    if npc_id not in targets:
                        continue

            relevance = self._calculate_relevance(npc_id, npc_name, entries)
            if relevance > 0:
                candidates.append({
                    "npc_id": npc_id,
                    "npc_name": npc_name,
                    "relevance": relevance,
                    "node": node,
                })

        candidates.sort(key=lambda c: c["relevance"], reverse=True)
        return candidates[:max_count]

    async def collect_reactions(
        self,
        scene_bus: SceneBus,
        session: "SessionRuntime",
        context: Dict[str, Any],
    ) -> List[BusEntry]:
        """返回模板反应 BusEntry 列表（最多 MAX_REACTIONS_PER_ROUND 个）。"""
        selected = self.get_relevant_npcs(scene_bus, session)
        return [
            BusEntry(
                actor=c["npc_id"],
                actor_name=c["npc_name"],
                type=BusEntryType.REACTION,
                content=(
                    f"{c['npc_name']}注意到了你的呼唤。"
                    if c["relevance"] >= 10
                    else f"{c['npc_name']}注意到了你的到来。"
                ),
            )
            for c in selected
        ]

    def _get_area_npcs(self, area_id: str, sub_location: Optional[str] = None) -> List[str]:
        """获取当前区域在场的 NPC ID 列表。"""
        if not self.wg:
            return []

        from app.world.models import WorldNodeType
        npc_ids: List[str] = []
        seen = set()

        scope_nodes = [area_id]
        if sub_location:
            scope_nodes.append(sub_location)
        for child_id in self.wg.get_children(area_id, WorldNodeType.LOCATION.value):
            scope_nodes.append(child_id)

        for scope_id in scope_nodes:
            for entity_id in self.wg.get_entities_at(scope_id):
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                node = self.wg.get_node(entity_id)
                if node and node.type == WorldNodeType.NPC.value:
                    npc_ids.append(entity_id)

        return npc_ids

    @staticmethod
    def _calculate_relevance(
        npc_id: str,
        npc_name: str,
        entries: List[BusEntry],
    ) -> float:
        """计算 NPC 与总线内容的关联度。

        点名 → +10
        话题匹配 → +5
        引擎事件（ON_ENTER 等）→ +1
        """
        score = 0.0
        for entry in entries:
            content_lower = entry.content.lower()
            if npc_id.lower() in content_lower or npc_name.lower() in content_lower:
                score += 10.0
            for topic in entry.topics:
                if npc_id in topic or npc_name in topic:
                    score += 5.0
            if entry.type == BusEntryType.ENGINE_RESULT:
                if entry.data.get("tool") == "navigate":
                    score += 1.0
        return score
