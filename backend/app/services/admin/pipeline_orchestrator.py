"""PipelineOrchestrator — V4 薄编排层。

替代 AdminCoordinator.process_player_input_v3 的核心逻辑，
通过 ContextAssembler + SessionRuntime + FlashCPUService.agentic_process_v4 实现。

V4 关键变化:
- 使用 V4AgenticToolRegistry（通过 SessionRuntime/AreaRuntime 操作）
- 不使用 StoryDirector / ConditionEngine / AgenticEnforcement
- 事件状态机由 AreaRuntime.check_events() 驱动
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.admin_protocol import AgenticResult, CoordinatorResponse
from app.models.state_delta import StateDelta
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
        event_queue: Optional[asyncio.Queue] = None,
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
            graph_store=self.graph_store,
        )
        await session.restore()

        if not session.player:
            raise ValueError("请先创建角色后再开始冒险。")

        context = ContextAssembler.assemble(world, session)

        # =====================================================================
        # B 阶段: Agentic 会话
        # =====================================================================
        context_dict = context.to_flat_dict()

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

        # 注入好感度数据（数据包⑥）
        await self._inject_dispositions(world_id, context_dict)

        agentic_result: AgenticResult = await self.flash_cpu.agentic_process_v4(
            session=session,
            player_input=player_input,
            context=context_dict,
            graph_store=self.graph_store,
            recall_orchestrator=self.recall_orchestrator,
            event_queue=event_queue,
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

        # C0: 递增叙事回合计数器
        if session.narrative:
            session.narrative.rounds_in_chapter += 1
            progress_tools = {
                "complete_event", "complete_objective",
                "activate_event", "advance_chapter",
            }
            made_progress = any(
                tc.success and tc.name in progress_tools
                for tc in agentic_result.tool_calls
            )
            if made_progress:
                session.narrative.rounds_since_last_progress = 0
            else:
                session.narrative.rounds_since_last_progress += 1
            session.mark_narrative_dirty()

        # C1: 后检查事件（agentic 操作可能改变了状态）
        post_updates = (
            session.current_area.check_events(session)
            if session.current_area
            else []
        )

        # C1b: 后置章节转换重评估
        chapter_transition = (
            session.current_area.check_chapter_transition(session)
            if session.current_area
            else None
        )

        # 提取 NPC 对话响应
        npc_responses: List[Dict[str, Any]] = []
        for tc in agentic_result.tool_calls:
            if tc.name == "npc_dialogue" and tc.success and tc.result.get("response"):
                npc_responses.append({
                    "character_id": tc.args.get("npc_id", ""),
                    "name": tc.result.get("npc_name", tc.args.get("npc_id", "")),
                    "dialogue": tc.result["response"],
                    "message": tc.args.get("message", ""),
                })

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
            for npc_r in npc_responses:
                session.history.record_npc_response(
                    character_id=npc_r["character_id"],
                    name=npc_r["name"],
                    dialogue=npc_r["dialogue"],
                )
            for t in teammate_responses:
                session.history.record_teammate_response(
                    character_id=t["character_id"],
                    name=t["name"],
                    response=t["response"],
                )

        # 统一持久化
        await session.persist()

        # ── 构建 state_delta（从 SessionRuntime 当前状态） ──
        state_changes: Dict[str, Any] = {}

        # 时间
        if session.time:
            state_changes["game_time"] = {
                "day": session.time.day,
                "hour": session.time.hour,
                "minute": session.time.minute,
                "period": session.time.period,
                "formatted": session.time.formatted,
            }

        # 子地点
        state_changes["sub_location"] = session.sub_location

        # 对话 / 战斗
        gs = session.game_state
        if gs:
            state_changes["active_dialogue_npc"] = gs.active_dialogue_npc
            state_changes["combat_id"] = gs.combat_id

        # 队伍
        if session.party:
            state_changes["has_party"] = True
            state_changes["party_id"] = session.party.party_id
            state_changes["party_members"] = [
                m.model_dump(mode="json") for m in session.party.members
            ]
        else:
            state_changes["has_party"] = False

        # 玩家 HP
        if session.player:
            state_changes["player_hp"] = {
                "current": session.player.current_hp,
                "max": session.player.max_hp,
            }

        # 好感度快照（合并 pre-agentic 快照 + 本轮 update_disposition 结果）
        dispositions: Dict[str, Dict[str, Any]] = {
            k: dict(v) for k, v in
            context_dict.get("dynamic_state", {}).get("affinity", {}).items()
        }
        for tc in agentic_result.tool_calls:
            if tc.name == "update_disposition" and tc.success and tc.result:
                npc_id = tc.result.get("npc_id") or tc.args.get("npc_id")
                current = tc.result.get("current")
                if npc_id and isinstance(current, dict):
                    entry = dispositions.get(npc_id, {})
                    for dim in ("approval", "trust", "fear", "romance"):
                        if dim in current:
                            entry[dim] = current[dim]
                    dispositions[npc_id] = entry
        if dispositions:
            state_changes["dispositions"] = dispositions

        state_delta = StateDelta(
            delta_id=str(uuid.uuid4())[:8],
            timestamp=datetime.utcnow(),
            operation="v4_pipeline",
            changes=state_changes,
            previous_values={},
        )

        # ── 收集所有已触发事件（不仅是 post-check 变更） ──
        all_event_ids: List[str] = list(
            session.narrative.events_triggered
        ) if session.narrative else []
        for u in post_updates:
            if u.event.id not in all_event_ids:
                all_event_ids.append(u.event.id)

        # ── 构建完整 chapter_info ──
        chapter_info_payload: Optional[Dict[str, Any]] = None
        try:
            chapter_plan = await self.narrative_service.get_current_chapter_plan(
                world_id, session_id,
            )
            if chapter_plan and chapter_plan.get("chapter"):
                ch = chapter_plan["chapter"]
                chapter_info_payload = {
                    "id": ch["id"],
                    "name": ch.get("name"),
                    "description": ch.get("description"),
                    "goals": chapter_plan.get("goals", []),
                    "required_events": chapter_plan.get("required_events", []),
                    "required_event_summaries": chapter_plan.get("required_event_summaries", []),
                    "pending_required_events": chapter_plan.get("pending_required_events", []),
                    "events_triggered": chapter_plan.get("events_triggered", []),
                    "event_total": chapter_plan.get("event_total", 0),
                    "event_completed": chapter_plan.get("event_completed", 0),
                    "event_completion_pct": chapter_plan.get("event_completion_pct", 0),
                    "all_required_events_completed": chapter_plan.get("all_required_events_completed", False),
                    "waiting_transition": chapter_plan.get("waiting_transition", False),
                    "suggested_maps": chapter_plan.get("suggested_maps", []),
                    "event_directives": chapter_plan.get("event_directives", []),
                    "current_event": chapter_plan.get("current_event"),
                    "next_chapter": chapter_plan.get("next_chapter"),
                }
                if chapter_transition:
                    chapter_info_payload["transition"] = chapter_transition.get("target_chapter_id")
        except Exception as exc:
            logger.warning("[v4] chapter_info 构建失败: %s", exc)

        return CoordinatorResponse(
            narration=gm_narration,
            speaker="GM",
            npc_responses=npc_responses,
            teammate_responses=teammate_responses,
            state_delta=state_delta,
            metadata={
                "source": "v4_pipeline",
                "model": settings.admin_agentic_model,
                "agentic_tool_calls": len(agentic_result.tool_calls),
                "agentic_usage": agentic_result.usage,
                "agentic_finish_reason": agentic_result.finish_reason,
                "teammate_count": len(teammate_responses),
                "is_private": is_private,
                "private_target": private_target if is_private else None,
                "agentic_trace": {
                    "thinking": {
                        "summary": agentic_result.thinking_summary,
                        "thoughts_token_count": agentic_result.usage.get("thoughts_token_count", 0),
                        "output_token_count": agentic_result.usage.get("output_token_count", 0),
                        "total_token_count": agentic_result.usage.get("total_token_count", 0),
                        "finish_reason": agentic_result.finish_reason,
                    },
                    "tool_calls": [tc.model_dump(exclude={"result"}) for tc in agentic_result.tool_calls],
                    "stats": {
                        "count": len(agentic_result.tool_calls),
                        "success": sum(1 for tc in agentic_result.tool_calls if tc.success),
                        "failed": sum(1 for tc in agentic_result.tool_calls if not tc.success),
                    },
                },
            },
            story_events=all_event_ids,
            chapter_info=chapter_info_payload,
            image_data=agentic_result.image_data,
        )

    async def _inject_dispositions(
        self,
        world_id: str,
        context_dict: Dict[str, Any],
    ) -> None:
        """注入在场 NPC + 队友对玩家的好感度到 context_dict。"""
        if not self.graph_store:
            return

        # 收集在场 NPC ID
        area_npcs = context_dict.get("area_context", {}).get("npcs", [])
        npc_ids = {npc.get("id") for npc in area_npcs if npc.get("id")}

        # 收集队友 ID
        party_members = context_dict.get("dynamic_state", {}).get("party_members", [])
        for member in party_members:
            cid = member.get("character_id")
            if cid:
                npc_ids.add(cid)

        if not npc_ids:
            return

        # 并行获取（单个失败不影响其他）
        async def _fetch_one(cid: str):
            try:
                return cid, await self.graph_store.get_disposition(world_id, cid, "player")
            except Exception as exc:
                logger.warning("disposition fetch failed for %s: %s", cid, exc)
                return cid, None

        results = await asyncio.gather(*[_fetch_one(cid) for cid in npc_ids])

        # 过滤 + 格式化
        affinity: Dict[str, Any] = {}
        for cid, disp in results:
            if disp is None:
                continue
            approval = disp.get("approval", 0)
            trust = disp.get("trust", 0)
            fear = disp.get("fear", 0)
            romance = disp.get("romance", 0)
            if approval == 0 and trust == 0 and fear == 0 and romance == 0:
                continue
            history = disp.get("history", [])
            if not isinstance(history, list):
                history = []
            affinity[cid] = {
                "approval": approval,
                "trust": trust,
                "fear": fear,
                "romance": romance,
                "recent_changes": history[-5:],
            }

        if affinity:
            context_dict.setdefault("dynamic_state", {})["affinity"] = affinity
            logger.info("[v4] injected dispositions for %d NPCs", len(affinity))
