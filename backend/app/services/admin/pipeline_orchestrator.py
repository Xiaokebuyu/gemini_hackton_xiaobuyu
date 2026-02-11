"""PipelineOrchestrator — V4 薄编排层。

替代 AdminCoordinator.process_player_input_v3 的核心逻辑，
通过 ContextAssembler + SessionRuntime + FlashCPUService.agentic_process_v4 实现。

V4 关键变化:
- 使用 V4AgenticToolRegistry（通过 SessionRuntime/AreaRuntime 操作）
- 不使用 StoryDirector / ConditionEngine / AgenticEnforcement
- 事件状态机由 AreaRuntime.check_events() 驱动
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.admin_protocol import AgenticResult, CoordinatorResponse
from app.runtime.context_assembler import ContextAssembler
from app.runtime.game_runtime import GameRuntime
from app.runtime.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """V4 管线编排器 — 比 V3 更简洁的三阶段流程。

    A 阶段: 上下文组装（ContextAssembler + SessionRuntime）
    B 阶段: Agentic 会话（FlashCPUService.agentic_process）
    C 阶段: 后处理（队友响应 + 历史记录 + 持久化）
    """

    def __init__(
        self,
        flash_cpu: Any,
        party_service: Any,
        narrative_service: Any,
        graph_store: Any,
        teammate_response_service: Any,
        session_history_manager: Any,
        character_store: Any,
        state_manager: Any,
        world_runtime: Any,
        recall_orchestrator: Any = None,
    ) -> None:
        self.flash_cpu = flash_cpu
        self.party_service = party_service
        self.narrative_service = narrative_service
        self.graph_store = graph_store
        self.teammate_response_service = teammate_response_service
        self.session_history_manager = session_history_manager
        self.character_store = character_store
        self.state_manager = state_manager
        self.world_runtime = world_runtime
        self.recall_orchestrator = recall_orchestrator

    async def process(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: Optional[str] = None,
    ) -> CoordinatorResponse:
        """V4 管线主入口。"""

        # =====================================================================
        # A 阶段: 上下文组装
        # =====================================================================
        rt = await GameRuntime.get_instance()
        world = await rt.get_world(world_id)

        session = SessionRuntime(
            world_id=world_id,
            session_id=session_id,
            world=world,
            state_manager=self.state_manager,
            party_service=self.party_service,
            narrative_service=self.narrative_service,
            session_history_manager=self.session_history_manager,
            character_store=self.character_store,
            world_runtime=self.world_runtime,
        )
        await session.restore()

        if not session.player:
            raise ValueError("请先创建角色后再开始冒险。")

        context = ContextAssembler.assemble(world, session)

        # 事件预检查（机械条件驱动状态机）
        event_updates = (
            session.current_area.check_events(session)
            if session.current_area
            else []
        )

        # =====================================================================
        # B 阶段: Agentic 会话
        # =====================================================================
        context_dict = context.to_flat_dict()

        # 注入事件更新
        if event_updates:
            context_dict["event_updates"] = [
                {
                    "event_id": u.event.id,
                    "transition": u.transition,
                    "event_name": u.event.name,
                }
                for u in event_updates
            ]

        # 注入章节转换可用性（AreaRuntime 条件评估结果）
        if session.current_area:
            transition = session.current_area.check_chapter_transition(session)
            if transition:
                chapter_ctx = context_dict.get("chapter_context", {})
                chapter_ctx["chapter_transition_available"] = transition
                context_dict["chapter_context"] = chapter_ctx

        # 注入对话历史
        if session.history:
            conversation_history = session.history.get_recent_history(
                max_tokens=settings.admin_agentic_history_max_chars
            )
            if conversation_history:
                context_dict["conversation_history"] = conversation_history

        # 注入私密对话标记
        if is_private:
            context_dict["is_private"] = True
            context_dict["private_target"] = private_target

        agentic_result: AgenticResult = await self.flash_cpu.agentic_process_v4(
            session=session,
            player_input=player_input,
            context=context_dict,
            graph_store=self.graph_store,
            recall_orchestrator=self.recall_orchestrator,
        )
        gm_narration = agentic_result.narration

        logger.info(
            "[v4] agentic 完成: tools=%d narration_len=%d",
            len(agentic_result.tool_calls),
            len(gm_narration),
        )

        # =====================================================================
        # C 阶段: 后处理
        # =====================================================================

        # 后检查事件（agentic 操作可能改变了状态）
        post_updates = (
            session.current_area.check_events(session)
            if session.current_area
            else []
        )

        # Phase 5C: 分发已完成事件到同伴
        if session.companions:
            from app.runtime.models.companion_state import CompactEvent

            game_day = 1
            if session.time:
                game_day = getattr(session.time, "day", 1) or 1

            for u in post_updates:
                if u.transition.endswith("completed"):
                    compact = CompactEvent(
                        event_id=u.event.id,
                        event_name=u.event.name,
                        summary=u.event.description or u.event.name,
                        area_id=u.event.area_id,
                        game_day=game_day,
                        importance=u.event.importance or "side",
                    )
                    for companion in session.companions.values():
                        if hasattr(companion, "add_event"):
                            companion.add_event(compact)

        # 记录行动到区域访问日志
        if session.current_area:
            session.current_area.record_action(player_input)

        # 队友响应
        party = session.party
        teammate_responses: List[Dict[str, Any]] = []
        if party and party.get_active_members():
            teammate_result = await self.teammate_response_service.process_round(
                party=party,
                player_input=player_input,
                gm_response=gm_narration,
                context=context_dict,
            )
            for r in teammate_result.responses:
                if r.response:
                    teammate_responses.append({
                        "character_id": r.character_id,
                        "name": r.name,
                        "response": r.response,
                        "reaction": r.reaction,
                    })

        # 历史记录
        if session.history:
            session.history.record_round(
                player_input=player_input,
                gm_response=gm_narration,
                metadata={
                    "source": "v4_pipeline",
                    "visibility": "private" if is_private else "public",
                    "private_target": private_target if is_private else None,
                },
            )
            for t in teammate_responses:
                session.history.record_teammate_response(
                    character_id=t["character_id"],
                    name=t["name"],
                    response=t["response"],
                )

        # 统一持久化
        await session.persist()

        # 合并所有事件 ID
        all_event_ids: List[str] = []
        seen: set = set()
        for u in event_updates + post_updates:
            if u.event.id not in seen:
                all_event_ids.append(u.event.id)
                seen.add(u.event.id)

        return CoordinatorResponse(
            narration=gm_narration,
            speaker="GM",
            teammate_responses=teammate_responses,
            state_delta=None,
            metadata={
                "source": "v4_pipeline",
                "model": settings.admin_agentic_model,
                "agentic_tool_calls": len(agentic_result.tool_calls),
                "agentic_usage": agentic_result.usage,
                "agentic_finish_reason": agentic_result.finish_reason,
                "teammate_count": len(teammate_responses),
                "is_private": is_private,
                "private_target": private_target if is_private else None,
            },
            story_events=all_event_ids,
            image_data=agentic_result.image_data,
        )
