"""MemoryOps — 记忆系统门面（从 SessionRuntime 提取，P7 瘦身）。"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)


class MemoryOps:
    """记忆召回 / 写入 / 图谱化，委托到 memory 子系统。"""

    def __init__(self, session: 'SessionRuntime') -> None:
        self._s = session

    async def recall(
        self,
        role: str,
        actor_id: str,
        seeds: List[str],
        intent_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """统一记忆读取门面。"""
        start = time.perf_counter()

        if not self._s.world_graph or self._s._world_graph_failed:
            return []

        from app.world.memory.recall import WorldGraphRecallOrchestrator

        orchestrator = WorldGraphRecallOrchestrator(self._s.world_graph)
        response = await orchestrator.recall_for_role(
            role=role,
            character_id=actor_id,
            seed_nodes=seeds,
            intent_type=intent_type,
        )

        activated = response.activated_nodes or {}
        ranked = sorted(
            activated.items(),
            key=lambda item: item[1],
            reverse=True,
        )[: max(1, int(limit))]

        memories: List[Dict[str, Any]] = []
        for node_id, score in ranked:
            node = self._s.world_graph.get_node(node_id)
            if not node:
                continue
            memories.append(
                {
                    "node_id": node_id,
                    "name": node.name or node_id,
                    "summary": node.properties.get("summary", ""),
                    "type": node.type,
                    "relevance": round(float(score), 4),
                }
            )

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "[recall] role=%s actor=%s seeds=%s activated=%d returned=%d elapsed=%.1fms",
            role, actor_id, seeds, len(activated), len(memories), elapsed_ms,
        )
        return memories

    def _check_memory_write_permission(self, role: str, memory_type: str) -> None:
        """角色级写入权限校验 — 委托到 memory/recorder。"""
        from app.world.memory.recorder import check_memory_write_permission
        check_memory_write_permission(role, memory_type)

    def record_memory(
        self,
        owner_id: str,
        memory_type: str,
        name: str,
        summary: str,
        importance: float,
        role: str,
        **props: Any,
    ) -> str:
        """统一记忆写入门面 — 委托到 memory/recorder。"""
        if not self._s.world_graph or self._s._world_graph_failed:
            raise RuntimeError("WorldGraph unavailable for record_memory")
        from app.world.memory.recorder import record_memory as _record
        return _record(
            self._s.world_graph, owner_id, memory_type, name, summary,
            importance, role, **props,
        )

    async def graphize_messages(
        self,
        owner_id: str,
        messages: List[Any],
        current_scene: Optional[str] = None,
        game_day: int = 1,
    ) -> Dict[str, Any]:
        """统一图谱化入口（WorldGraph-only）。"""
        if not self._s.world_graph or self._s._world_graph_failed:
            return {"success": False, "error": "WorldGraph unavailable"}

        from app.models.context_window import GraphizeRequest, WindowMessage
        from app.services.memory_graphizer import MemoryGraphizer

        normalized_messages: List[WindowMessage] = []
        for idx, raw in enumerate(messages):
            if isinstance(raw, WindowMessage):
                normalized_messages.append(raw)
                continue
            if isinstance(raw, dict):
                normalized_messages.append(WindowMessage(**raw))
                continue
            if hasattr(raw, "model_dump"):
                normalized_messages.append(WindowMessage(**raw.model_dump()))
                continue
            raise TypeError(f"unsupported message type at index {idx}: {type(raw)}")

        request = GraphizeRequest(
            npc_id=owner_id,
            world_id=self._s.world_id,
            messages=normalized_messages,
            current_scene=current_scene or self._s.player_location,
            game_day=game_day,
        )
        graphizer = MemoryGraphizer()
        result = await graphizer.graphize(
            request=request,
            world_graph=self._s.world_graph,
        )
        return result.model_dump()
