"""共享图谱化辅助函数 — PipelineOrchestrator + NPCInteractionCoordinator 共用。"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

from app.exceptions import LLMServiceError

if TYPE_CHECKING:
    from app.runtime.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)


async def maybe_graphize_session_history(
    session: SessionRuntime,
    *,
    source: str,
) -> None:
    """尝试触发 SessionHistory 图谱化；失败只记日志，不中断主流程。"""
    history = getattr(session, "history", None)
    if not history:
        return

    maybe_graphize = getattr(history, "maybe_graphize", None)
    # 测试里常用 MagicMock；只调用真实 async 方法/AsyncMock。
    if not maybe_graphize or not inspect.iscoroutinefunction(maybe_graphize):
        return

    if not session.world_graph:
        return

    try:
        from app.agentic.memory_graphizer import MemoryGraphizer

        result = await maybe_graphize(
            graphizer=MemoryGraphizer(),
            world_graph=session.world_graph,
            game_day=session.time.day if session.time else 1,
            current_scene=session.player_location,
        )
        if result:
            logger.info(
                "[v4] SessionHistory 图谱化(%s): nodes=%d edges=%d removed=%d",
                source,
                result.get("nodes_added", 0),
                result.get("edges_added", 0),
                result.get("messages_removed", 0),
            )
    except (LLMServiceError, KeyError) as exc:
        logger.warning("[v4] SessionHistory 图谱化失败(%s): %s", source, exc)
