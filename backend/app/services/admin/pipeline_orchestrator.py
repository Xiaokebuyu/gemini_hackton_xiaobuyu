"""PipelineOrchestrator — V4 薄编排层。

替代 AdminCoordinator.process_player_input_v3 的核心逻辑，
通过 ContextAssembler + SessionRuntime + FlashCPUService.agentic_process_v4 实现。

V4 关键变化:
- 使用 V4AgenticToolRegistry（通过 SessionRuntime 操作）
- 不使用 StoryDirector / ConditionEngine / AgenticEnforcement
- 事件状态机由 BehaviorEngine.tick() 驱动（C8: 唯一事件系统）
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
        memory_graphizer: Any = None,
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
        self.memory_graphizer = memory_graphizer

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

        # 降级状态诊断
        if session._world_graph_failed:
            logger.warning("[v4] WorldGraph 构建失败，降级运行（事件引擎不可用）")
        if not session.party:
            logger.info("[v4] party=None，队友阶段将跳过")

        # A4: BehaviorEngine pre-tick (C8: 唯一事件系统)
        pre_tick_result = session.run_behavior_tick("pre")

        context = ContextAssembler.assemble(world, session)

        # =====================================================================
        # B 阶段: Agentic 会话
        # =====================================================================
        context_dict = context.to_flat_dict()

        # C7b + 世界状态回注：构建 world_state_update 注入 LLM 上下文
        # 1. 读取上一轮 post-tick 存在 GameState.metadata 里的遗留变更（跨回合持久化）
        prev_summary: Dict[str, Any] = {}
        if session.game_state and isinstance(getattr(session.game_state, "metadata", None), dict):
            prev_summary = session.game_state.metadata.pop("_world_state_summary", {}) or {}
            if prev_summary:
                session.mark_game_state_dirty()  # metadata 被修改，persist 时写入 Firestore

        # 2. 本轮 pre-tick 新变更
        pre_update: Dict[str, Any] = {}
        if pre_tick_result:
            pre_update = PipelineOrchestrator._build_world_state_update(session, pre_tick_result)

        # 3. 合并（去重），注入 context_dict
        world_state_update = PipelineOrchestrator._merge_world_state_updates(prev_summary, pre_update)
        if world_state_update:
            context_dict["world_state_update"] = world_state_update
            logger.info(
                "[v4] world_state_update: available=%d completed=%d hints=%d",
                len(world_state_update.get("events_newly_available", [])),
                len(world_state_update.get("events_auto_completed", [])),
                len(world_state_update.get("narrative_hints", [])),
            )

        # E3: 注入 pending_flash 语义条件，LLM 应调用 report_flash_evaluation 回报结果
        if pre_tick_result and pre_tick_result.pending_flash:
            pending = [
                {
                    "prompt": cond.params.get("prompt", ""),
                    "context": cond.params.get("context", ""),
                }
                for cond in pre_tick_result.pending_flash
                if cond.params.get("prompt")
            ]
            if pending:
                context_dict["pending_flash_evaluations"] = pending

        # --- 玩家主动掷骰预处理 ---
        player_roll_result = None
        if player_input.strip().lower().startswith("/roll"):
            player_roll_result = await self._handle_player_roll(
                world_id, session_id, player_input,
            )
            if player_roll_result and "error" not in player_roll_result:
                context_dict["player_roll_result"] = player_roll_result
                if event_queue:
                    await event_queue.put({
                        "type": "dice_roll",
                        "source": "player",
                        "result": player_roll_result,
                    })

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

        # 注入好感度数据（P2: 从 WorldGraph 图节点读取，不再走 Firestore）
        self._inject_dispositions_from_graph(session, context_dict)

        # ===== SceneBus: 写入玩家输入 + pre-tick hints =====
        # 对齐 Direction A 手册顺序：玩家输入先写总线，再做引擎前置执行。
        if session.scene_bus:
            from app.world.scene_bus import BusEntry, BusEntryType
            _vis = f"private:{private_target}" if is_private and private_target else "public"
            session.scene_bus.publish(BusEntry(
                actor="player",
                actor_name="player",
                type=BusEntryType.ACTION if not is_private else BusEntryType.SPEECH,
                content=player_input,
                visibility=_vis,
            ), event_queue)
            if pre_tick_result and pre_tick_result.narrative_hints:
                for hint in pre_tick_result.narrative_hints:
                    session.scene_bus.publish(BusEntry(
                        actor="engine",
                        type=BusEntryType.SYSTEM,
                        content=hint,
                    ), event_queue)

        # ===== A.2: 引擎前置执行（高置信度机械意图）=====
        if session.world_graph:
            from app.world.intent_resolver import IntentResolver
            from app.world.intent_executor import IntentExecutor
            resolver = IntentResolver(session.world_graph, session)
            intent = resolver.resolve(player_input)
            if intent is not None:
                executor = IntentExecutor(
                    session,
                    session.scene_bus,
                    recall_orchestrator=self.recall_orchestrator,
                    flash_cpu=self.flash_cpu,
                )
                engine_result = await executor.dispatch(intent)
                if engine_result.success:
                    if session.scene_bus:
                        for entry in engine_result.bus_entries:
                            session.scene_bus.publish(entry, event_queue)
                    context_dict["engine_executed"] = {
                        "type": engine_result.intent_type,
                        "target": engine_result.target,
                        "hints": engine_result.narrative_hints,
                    }
                    logger.info(
                        "[v4] engine pre-executed: %s → %s",
                        engine_result.intent_type, engine_result.target,
                    )
                else:
                    # 引擎执行失败 — 记录原因，GM 走完整旧路径
                    logger.warning(
                        "[v4] engine pre-exec FAILED: type=%s target=%s error=%s",
                        intent.type.value, intent.target, engine_result.error,
                    )
                    context_dict["engine_attempted"] = {
                        "type": intent.type.value,
                        "target": intent.target,
                        "error": engine_result.error,
                    }

        # 注入总线摘要到 context_dict（B 阶段前）
        if session.scene_bus:
            bus_summary = session.scene_bus.get_round_summary()
            if bus_summary:
                context_dict["scene_bus_summary"] = bus_summary

        # ===== A.3: NPC autonomous reactions (before GM, so GM can consume them) =====
        npc_autonomous_responses: List[Dict[str, Any]] = []
        if session.scene_bus:
            try:
                from app.world.npc_reactor import NPCReactor
                wg = getattr(session, "world_graph", None)
                reactor = NPCReactor(
                    instance_manager=None,
                    world_graph=wg,
                    use_llm=True,
                    llm_service=self.flash_cpu.llm_service,
                )
                npc_reactions = await reactor.collect_reactions(
                    session.scene_bus, session, context_dict,
                )
                from app.world.scene_bus import BusEntry as _BE
                for r in npc_reactions:
                    session.scene_bus.publish(r, event_queue)
                    npc_autonomous_responses.append({
                        "character_id": r.actor,
                        "name": r.actor_name,
                        "dialogue": r.content,
                    })
                # Refresh bus_summary so GM sees NPC reactions
                if npc_reactions:
                    bus_summary = session.scene_bus.get_round_summary()
                    if bus_summary:
                        context_dict["scene_bus_summary"] = bus_summary
            except Exception as exc:
                logger.warning("[v4] NPC reactor failed: %s", exc)

        # ===== B 阶段: Agentic 会话 =====
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

        # ===== SceneBus: B 阶段后写入工具结果 + NPC 响应 + GM 叙述 =====
        if session.scene_bus:
            from app.world.scene_bus import BusEntry, BusEntryType
            for tc in agentic_result.tool_calls:
                if tc.success:
                    session.scene_bus.publish(BusEntry(
                        actor="engine",
                        type=BusEntryType.ENGINE_RESULT,
                        content=f"[{tc.name}] completed",
                        data={"tool": tc.name, "args": tc.args},
                    ))
            for tc in agentic_result.tool_calls:
                if tc.name == "npc_dialogue" and tc.success and tc.result.get("response"):
                    session.scene_bus.publish(BusEntry(
                        actor=tc.args.get("npc_id", "npc"),
                        actor_name=tc.result.get("npc_name", ""),
                        type=BusEntryType.SPEECH,
                        content=tc.result["response"],
                    ))
            if gm_narration:
                session.scene_bus.publish(BusEntry(
                    actor="gm",
                    type=BusEntryType.NARRATIVE,
                    content=gm_narration[:500],
                ))

        # =====================================================================
        # C 阶段: 后处理
        # =====================================================================

        # C0: 递增叙事回合计数器
        if session.narrative:
            session.narrative.rounds_in_chapter += 1
            progress_tools = {
                "complete_event", "complete_objective",
                "activate_event", "advance_chapter",
                "advance_stage", "complete_event_objective",
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

        # C1: BehaviorEngine post-tick (C8: 唯一事件系统)
        # E3: post-tick 时 flash_results 已由 LLM 写入，TickContext 会携带它们
        post_tick_result = session.run_behavior_tick("post")
        # E3: post-tick 完成后清空缓存，避免跨回合污染
        session.flash_results.clear()

        # 世界状态回注：将 post-tick 产生的状态变更存入 GameState.metadata，供下一回合注入 LLM
        if post_tick_result and session.game_state:
            post_summary = PipelineOrchestrator._build_world_state_update(session, post_tick_result)
            if post_summary:
                if session.game_state.metadata is None:
                    session.game_state.metadata = {}
                session.game_state.metadata["_world_state_summary"] = post_summary
                session.mark_game_state_dirty()
                logger.info(
                    "[v4] post-tick summary stored: available=%d completed=%d",
                    len(post_summary.get("events_newly_available", [])),
                    len(post_summary.get("events_auto_completed", [])),
                )

        # C1b: 章节转换（从 WorldGraph GATE 边）
        chapter_transition = session.check_chapter_transitions()

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

        # 队友响应（流式推送到 event_queue）
        # A.3: 注入总线摘要到队友上下文（排除 player/gm 条目，避免与模板变量重复）
        teammate_context = self._build_teammate_context(
            session=session,
            base_context=context_dict,
            player_input=player_input,
            gm_narration=gm_narration,
            world_state_update=world_state_update,
            agentic_result=agentic_result,
        )
        if session.scene_bus:
            bus_summary_for_teammates = session.scene_bus.get_round_summary(
                exclude_actors={"player", "gm"}
            )
            if bus_summary_for_teammates:
                teammate_context["scene_bus_summary"] = bus_summary_for_teammates
        party = session.party
        teammate_responses: List[Dict[str, Any]] = []
        if not party or not party.get_active_members():
            logger.info("[v4] 队友阶段跳过: party=%s members=%d",
                        bool(party), len(party.get_active_members()) if party else 0)
        if party and party.get_active_members():
            async for tm_event in self.teammate_response_service.process_round_stream(
                party=party,
                player_input=player_input,
                gm_response=gm_narration,
                context=teammate_context,
            ):
                # 实时推送到前端
                if event_queue:
                    await event_queue.put(tm_event)
                # 从 teammate_end 事件收集结果（用于历史记录 + CoordinatorResponse）
                if tm_event.get("type") == "teammate_end" and tm_event.get("response"):
                    teammate_responses.append({
                        "character_id": tm_event["character_id"],
                        "name": tm_event["name"],
                        "response": tm_event["response"],
                        "reaction": tm_event.get("reaction", ""),
                    })

        # ===== SceneBus: C 阶段写入队友响应 + clear =====
        if session.scene_bus:
            from app.world.scene_bus import BusEntry, BusEntryType as _BET
            for t in teammate_responses:
                session.scene_bus.publish(BusEntry(
                    actor=t["character_id"],
                    actor_name=t.get("name", ""),
                    type=_BET.REACTION,
                    content=t["response"],
                ))

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
            # A.3: NPC 自主反应也写入历史
            for r in npc_autonomous_responses:
                session.history.record_npc_response(
                    character_id=r["character_id"],
                    name=r["name"],
                    dialogue=r["dialogue"],
                )

        # SceneBus: 回合末图谱化（F18）
        if session.scene_bus and self.memory_graphizer:
            try:
                from app.services.scene_bus_graphizer import graphize_scene_bus_round

                graphize_result = await graphize_scene_bus_round(
                    scene_bus=session.scene_bus,
                    session=session,
                    memory_graphizer=self.memory_graphizer,
                )
                if not graphize_result.success:
                    logger.warning(
                        "[v4] scene bus graphize skipped/failed: %s",
                        graphize_result.error,
                    )
            except Exception as exc:
                logger.warning("[v4] scene bus graphize raised: %s", exc)

        # SceneBus: persist 前 clear
        if session.scene_bus:
            session.scene_bus.clear()

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

        # ── 收集所有已触发事件 ──
        all_event_ids: List[str] = list(
            session.narrative.events_triggered
        ) if session.narrative else []
        if post_tick_result:
            for nid, changes in post_tick_result.state_changes.items():
                if changes.get("status") == "completed" and nid not in all_event_ids:
                    node = session.world_graph.get_node(nid) if session.world_graph else None
                    if node and node.type == "event_def":
                        all_event_ids.append(nid)

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
                    "tool_calls": [
                        tc.model_dump(
                            exclude={"result"} if tc.name in {
                                "recall_memory", "npc_dialogue",
                                "get_combat_options", "choose_combat_action",
                                "generate_scene_image",
                            } else set()
                        )
                        for tc in agentic_result.tool_calls
                    ],
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

    @staticmethod
    def _build_world_state_update(session: Any, tick_result: Any) -> Dict[str, Any]:
        """从 TickResult 构建结构化世界状态公报，注入 LLM 上下文。

        来源：
          - state_changes: 节点状态变更（事件解锁/完成、世界标记、声望）
          - all_events: WorldEvent 列表（XP/物品/声望/世界标记等副作用）
          - narrative_hints: 引擎行为产生的叙事文本
        """
        from app.world.models import WorldNodeType
        update: Dict[str, Any] = {}
        newly_available: List[Dict] = []
        auto_completed: List[Dict] = []
        failed: List[Dict] = []
        world_flags_changed: Dict[str, Any] = {}
        faction_rep_changed: Dict[str, int] = {}
        npc_state_changes: List[Dict] = []

        wg = getattr(session, "world_graph", None)

        # ── 1. 节点状态变更（state_changes） ──
        for nid, changes in tick_result.state_changes.items():
            node = wg.get_node(nid) if wg else None

            if node and node.type == WorldNodeType.EVENT_DEF:
                # 事件状态变更
                status = changes.get("status")
                entry = {"id": nid, "name": node.name}
                if status == "available":
                    newly_available.append(entry)
                elif status == "completed":
                    auto_completed.append(entry)
                elif status == "failed":
                    entry["reason"] = node.state.get("failure_reason", "")
                    failed.append(entry)

            elif nid == "world_root" and node:
                # 世界标记变更
                if "world_flags" in changes:
                    world_flags_changed.update(changes["world_flags"])
                if "faction_reputations" in changes:
                    faction_rep_changed.update(changes["faction_reputations"])

            elif node and node.type == WorldNodeType.NPC:
                # NPC 关键状态变更（存活/位置）
                significant = {}
                if "is_alive" in changes:
                    significant["is_alive"] = changes["is_alive"]
                if "current_location" in changes:
                    significant["moved_to"] = changes["current_location"]
                if significant:
                    npc_state_changes.append({"id": nid, "name": node.name, **significant})

        # ── 2. WorldEvent 副作用摘要（all_events） ──
        # 按事件类型分类，过滤内部机制事件
        _MEANINGFUL_EVENT_TYPES = {
            "xp_awarded", "item_granted", "gold_awarded",
            "reputation_changed", "world_flag_set",
            "event_completed", "event_activated", "event_unlocked",
            "event_failed", "combat_ended", "npc_killed",
        }
        world_events_summary: List[Dict] = []
        for evt in tick_result.all_events:
            if evt.event_type not in _MEANINGFUL_EVENT_TYPES:
                continue
            entry = {"type": evt.event_type, "origin": evt.origin_node}
            # 提取关键数据字段
            if evt.data:
                for key in ("amount", "event_id", "item_id", "faction", "delta", "key", "value", "result"):
                    if key in evt.data:
                        entry[key] = evt.data[key]
            world_events_summary.append(entry)

        # ── 组装输出 ──
        if newly_available:
            update["events_newly_available"] = newly_available
        if auto_completed:
            update["events_auto_completed"] = auto_completed
        if failed:
            update["events_failed"] = failed
        if world_flags_changed:
            update["world_flags_changed"] = world_flags_changed
        if faction_rep_changed:
            update["faction_reputations_changed"] = faction_rep_changed
        if npc_state_changes:
            update["npc_state_changes"] = npc_state_changes
        if world_events_summary:
            update["world_events"] = world_events_summary
        if tick_result.narrative_hints:
            update["narrative_hints"] = tick_result.narrative_hints

        return update

    @staticmethod
    def _merge_world_state_updates(*updates: Dict[str, Any]) -> Dict[str, Any]:
        """合并多个 world_state_update，按 event_id 去重。"""
        merged: Dict[str, Any] = {}
        for upd in updates:
            for key in ("events_newly_available", "events_auto_completed", "events_failed"):
                existing_ids = {e["id"] for e in merged.get(key, [])}
                for entry in upd.get(key, []):
                    if entry["id"] not in existing_ids:
                        merged.setdefault(key, []).append(entry)
                        existing_ids.add(entry["id"])
            for hint in upd.get("narrative_hints", []):
                merged.setdefault("narrative_hints", []).append(hint)
        return merged

    def _build_teammate_context(
        self,
        *,
        session: SessionRuntime,
        base_context: Dict[str, Any],
        player_input: str,
        gm_narration: str,
        world_state_update: Optional[Dict[str, Any]],
        agentic_result: AgenticResult,
    ) -> Dict[str, Any]:
        """构建队友阶段专用上下文，避免直接复用 GM 阶段原始字典。"""
        teammate_context = dict(base_context)
        teammate_context["world_id"] = session.world_id
        teammate_context["chapter_id"] = session.chapter_id
        teammate_context["area_id"] = session.area_id
        teammate_context["location"] = (
            base_context.get("location")
            or base_context.get("location_context")
            or {}
        )
        teammate_context["player_input"] = player_input
        teammate_context["gm_narration_full"] = gm_narration
        teammate_context["world_state_update"] = world_state_update or base_context.get("world_state_update", {})
        teammate_context["combat_active"] = bool(
            session.game_state and session.game_state.combat_id
        )
        teammate_context["_runtime_session"] = session

        if session.history and hasattr(session.history, "get_last_teammate_responses"):
            teammate_context["last_teammate_responses"] = (
                session.history.get_last_teammate_responses()
            )

        tool_summaries: List[Dict[str, Any]] = []
        for call in getattr(agentic_result, "tool_calls", []) or []:
            name = getattr(call, "name", "")
            if name in {"recall_memory", "generate_scene_image"}:
                continue
            tool_summaries.append(
                {
                    "name": name,
                    "success": bool(getattr(call, "success", False)),
                    "args": getattr(call, "args", {}) or {},
                }
            )
        if tool_summaries:
            teammate_context["this_round_tools"] = tool_summaries

        return teammate_context

    def _inject_dispositions_from_graph(
        self,
        session: SessionRuntime,
        context_dict: Dict[str, Any],
    ) -> None:
        """P2: 从 WorldGraph NPC 节点读取好感度注入 context_dict。"""
        wg = getattr(session, "world_graph", None)
        if not wg or getattr(session, "_world_graph_failed", False):
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

        affinity: Dict[str, Any] = {}
        for cid in npc_ids:
            node = wg.get_node(cid)
            if not node:
                continue
            disp = node.state.get("dispositions", {}).get("player", {})
            if not disp:
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
            logger.info("[v4] injected dispositions for %d NPCs (from graph)", len(affinity))

    async def _handle_player_roll(
        self,
        world_id: str,
        session_id: str,
        raw_input: str,
    ) -> Optional[Dict[str, Any]]:
        """解析 /roll 命令并执行检定。

        Format: /roll <skill_or_ability> [DC]
        Examples: /roll stealth 15, /roll dex, /roll perception
        """
        from app.services.ability_check_service import (
            AbilityCheckService,
            SKILL_ABILITY_MAP,
            VALID_ABILITIES,
        )

        parts = raw_input.strip().split()
        if len(parts) < 2:
            return {"error": "用法: /roll <技能或属性> [DC]", "success": False}

        target = parts[1].lower().replace(" ", "_")
        dc = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 10

        skill = target if target in SKILL_ABILITY_MAP else ""
        ability = target if target in VALID_ABILITIES else ""
        if not skill and not ability:
            skill = target  # Let AbilityCheckService validate

        turn_key = f"{session_id}:{id(self)}"

        svc = AbilityCheckService(store=self.character_store)
        return await svc.perform_check(
            world_id=world_id,
            session_id=session_id,
            skill=skill or None,
            ability=ability or None,
            dc=dc,
            source="player",
            turn_key=turn_key,
        )
