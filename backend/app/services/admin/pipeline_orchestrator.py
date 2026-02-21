"""PipelineOrchestrator — V4 薄编排层。

替代 AdminCoordinator.process_player_input_v3 的核心逻辑，
通过 ContextAssembler + SessionRuntime + AgenticExecutor 实现。

V4 关键变化:
- B 阶段使用 AgenticExecutor + RoleRegistry 沉浸式工具 + GM extra_tools
- 事件状态机由 BehaviorEngine.tick() 驱动（C8: 唯一事件系统）
"""

from __future__ import annotations

import asyncio
import json
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
        session_store: Any = None,
        recall_orchestrator: Any = None,
        memory_graphizer: Any = None,
        instance_manager: Any = None,
    ) -> None:
        self.flash_cpu = flash_cpu
        self.party_service = party_service
        self.narrative_service = narrative_service
        self.graph_store = graph_store
        self.teammate_response_service = teammate_response_service
        self.session_history_manager = session_history_manager
        self.character_store = character_store
        self.state_manager = state_manager
        self.session_store = session_store
        self.recall_orchestrator = recall_orchestrator
        self.memory_graphizer = memory_graphizer
        self.instance_manager = instance_manager

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
            session_store=self.session_store,
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
                )
                engine_result = await executor.dispatch(intent)
                if engine_result.success:
                    if session.scene_bus:
                        for entry in engine_result.bus_entries:
                            session.scene_bus.publish(entry, event_queue)

                    # Pipeline 层处理 talk_pending: 生成 NPC 对话，成功则升级为 "talk"
                    final_intent_type = engine_result.intent_type
                    if engine_result.intent_type == "talk_pending":
                        npc_talk_result = await self._pipeline_npc_dialogue(
                            session, world, engine_result.target, player_input, event_queue,
                        )
                        if npc_talk_result:
                            final_intent_type = "talk"
                            engine_result.narrative_hints.append(
                                f"{npc_talk_result['name']}已在总线中回复，请勿再调用 npc_dialogue 重复对话"
                            )

                    context_dict["engine_executed"] = {
                        "type": final_intent_type,
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
                from app.services.npc_reactor import NPCReactor
                wg = getattr(session, "world_graph", None)
                reactor = NPCReactor(world_graph=wg)
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
                logger.error("[v4] NPC reactor failed: %s", exc, exc_info=True)

        # ===== B 阶段: Agentic 会话（AgenticExecutor + RoleRegistry + extra_tools）=====
        from app.world.agentic_executor import AgenticExecutor
        from app.world.immersive_tools import AgenticContext
        from app.world.gm_extra_tools import build_gm_extra_tools, ENGINE_TOOL_EXCLUSIONS

        gm_ctx = AgenticContext(
            session=session,
            agent_id="gm",
            role="gm",
            scene_bus=session.scene_bus,
            world_id=world_id,
            chapter_id=session.chapter_id,
            area_id=session.area_id,
            location_id=session.sub_location or "",
            recall_orchestrator=self.recall_orchestrator,
            graph_store=self.graph_store,
            image_service=getattr(self.flash_cpu, "image_service", None),
            flash_cpu=self.flash_cpu,
        )

        extra_tools = build_gm_extra_tools(
            session=session,
            flash_cpu=self.flash_cpu,
            graph_store=self.graph_store,
            event_queue=event_queue,
            engine_executed=context_dict.get("engine_executed"),
        )

        # 引擎排除（同时影响 immersive 工具）
        excluded: set = set()
        engine_exec = context_dict.get("engine_executed")
        if engine_exec:
            excluded = ENGINE_TOOL_EXCLUSIONS.get(engine_exec.get("type", ""), set())

        context_json = json.dumps(context_dict, ensure_ascii=False, default=str)
        user_prompt = (
            f"以下是分层上下文(JSON)：\n{context_json}\n\n"
            f"玩家输入：{player_input}\n\n"
            "请先调用必要工具，再输出最终 GM 叙述。"
        )

        executor = AgenticExecutor(self.flash_cpu.llm_service)
        agentic_result: AgenticResult = await executor.run(
            ctx=gm_ctx,
            system_prompt=self.flash_cpu._load_agentic_prompt(),
            user_prompt=user_prompt,
            extra_tools=extra_tools,
            exclude_tools=excluded or None,
            event_queue=event_queue,
            model_override=settings.admin_agentic_model or settings.admin_flash_model,
            thinking_level=settings.admin_flash_thinking_level,
            max_tool_rounds=settings.admin_agentic_max_remote_calls,
        )

        # 空叙述兜底
        if not agentic_result.narration:
            agentic_result.narration = "（你短暂沉默，观察着周围的动静。）"

        # image_data 提取（从 immersive generate_scene_image 工具结果中）
        for tc in agentic_result.tool_calls:
            if tc.name == "generate_scene_image" and tc.success:
                img = tc.result
                if isinstance(img, dict):
                    if img.get("image_data"):
                        agentic_result.image_data = {"generated": True, **(img["image_data"] if isinstance(img["image_data"], dict) else {})}
                        break
                    if img.get("generated"):
                        agentic_result.image_data = img
                        break

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

        # C1c: 自动时间推进（每轮 +10 分钟）
        engine_exec = context_dict.get("engine_executed")
        engine_already_advanced = engine_exec and engine_exec.get("type") in ("move_area", "rest")
        if not engine_already_advanced:
            try:
                session.advance_time(10)
            except Exception as exc:
                logger.warning("[v4] auto time advance failed: %s", exc)

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
                logger.error("[v4] scene bus graphize raised: %s", exc, exc_info=True)

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
            logger.error("[v4] chapter_info 构建失败: %s", exc)

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

    # =================================================================
    # NPC /interact 交互流
    # =================================================================

    async def process_interact_stream(
        self,
        world_id: str,
        session_id: str,
        npc_id: str,
        player_input: str,
    ):
        """NPC 直接交互 SSE 流。

        流程: Setup → NPC 回复 → GM 观察(可 PASS) → 队友观察 → 对话选项 → 持久化
        """
        import json as _json

        # ── A: Setup ──
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
            session_store=self.session_store,
            graph_store=self.graph_store,
        )
        await session.restore()

        if not session.player:
            yield {"type": "error", "error": "请先创建角色后再开始冒险。"}
            return

        wg = getattr(session, "world_graph", None)
        if not wg:
            yield {"type": "error", "error": "世界图谱不可用。"}
            return

        npc_node = wg.get_node(npc_id)
        if not npc_node:
            yield {"type": "error", "error": f"NPC '{npc_id}' 不存在。"}
            return

        npc_name = npc_node.name or npc_id

        # SceneBus: contact + 写入玩家发言
        if session.scene_bus:
            from app.world.scene_bus import BusEntry, BusEntryType
            session.scene_bus.contact(npc_id)
            session.scene_bus.publish(BusEntry(
                actor="player",
                actor_name="player",
                type=BusEntryType.SPEECH,
                content=player_input,
            ))

        yield {"type": "interact_start", "npc_id": npc_id, "npc_name": npc_name}

        # ── B1: NPC Agentic Response ──
        try:
            from app.world.agentic_executor import AgenticExecutor
            from app.world.immersive_tools import AgenticContext

            npc_traits = set(npc_node.properties.get("traits", []))
            event_queue: asyncio.Queue = asyncio.Queue()

            npc_ctx = AgenticContext(
                session=session,
                agent_id=npc_id,
                role="npc",
                scene_bus=session.scene_bus,
                world_id=world_id,
                chapter_id=getattr(session, "chapter_id", ""),
                area_id=getattr(session, "area_id", ""),
                location_id=getattr(session, "sub_location", ""),
                recall_orchestrator=self.recall_orchestrator,
                graph_store=self.graph_store,
            )

            npc_system_prompt = self._build_npc_system_prompt(npc_node, session)
            npc_model, npc_thinking = self._select_npc_model(npc_node)

            bus_summary = ""
            if session.scene_bus:
                bus_summary = session.scene_bus.get_round_summary(viewer_id=npc_id) or ""

            npc_user_prompt = bus_summary or f"玩家对你说：{player_input}"

            executor = AgenticExecutor(self.flash_cpu.llm_service)
            npc_result = await executor.run(
                ctx=npc_ctx,
                system_prompt=npc_system_prompt,
                user_prompt=npc_user_prompt,
                traits=npc_traits,
                event_queue=event_queue,
                model_override=npc_model,
                thinking_level=npc_thinking,
            )

            # Drain NPC tool events
            while not event_queue.empty():
                evt = event_queue.get_nowait()
                evt["character_id"] = npc_id
                yield evt

            npc_response_text = npc_result.narration or ""
            yield {
                "type": "npc_response",
                "npc_id": npc_id,
                "npc_name": npc_name,
                "text": npc_response_text,
            }

            # 写入总线
            if session.scene_bus and npc_response_text:
                from app.world.scene_bus import BusEntry, BusEntryType
                session.scene_bus.publish(BusEntry(
                    actor=npc_id,
                    actor_name=npc_name,
                    type=BusEntryType.SPEECH,
                    content=npc_response_text,
                ))
        except Exception as exc:
            logger.error("[interact] NPC response failed: %s", exc, exc_info=True)
            npc_response_text = ""
            yield {"type": "error", "error": f"NPC 回复失败: {exc}"}

        # ── B2: GM Observer (可 [PASS]) ──
        try:
            gm_ctx = AgenticContext(
                session=session,
                agent_id="gm",
                role="gm",
                scene_bus=session.scene_bus,
                world_id=world_id,
                chapter_id=getattr(session, "chapter_id", ""),
                area_id=getattr(session, "area_id", ""),
                location_id=getattr(session, "sub_location", ""),
                recall_orchestrator=self.recall_orchestrator,
                graph_store=self.graph_store,
            )
            gm_prompt = (
                f"玩家正在与{npc_name}对话。作为 GM，观察这次交互。\n"
                f"如果场景需要环境描述、气氛渲染或重要事件提示，请简短叙述。\n"
                f"如果不需要介入，直接输出 [PASS]。\n\n"
            )
            if session.scene_bus:
                gm_bus = session.scene_bus.get_round_summary() or ""
                if gm_bus:
                    gm_prompt += f"场景总线:\n{gm_bus}"

            gm_executor = AgenticExecutor(self.flash_cpu.llm_service)
            gm_result = await gm_executor.run(
                ctx=gm_ctx,
                system_prompt="你是游戏 GM。简洁观察，必要时渲染氛围。不必要时输出[PASS]。",
                user_prompt=gm_prompt,
                model_override=settings.admin_agentic_model,
                thinking_level=settings.admin_agentic_thinking,
            )
            gm_narration = gm_result.narration or ""
            if gm_narration:
                yield {"type": "gm_observation", "text": gm_narration}
        except Exception as exc:
            logger.warning("[interact] GM observer failed: %s", exc)
            gm_narration = ""

        # ── B3: Teammate Observer ──
        teammate_responses: List[Dict[str, Any]] = []
        party = session.party
        if party and party.get_active_members():
            context_dict = {"_runtime_session": session}
            if session.scene_bus:
                bus_for_tm = session.scene_bus.get_round_summary(
                    exclude_actors={"player", "gm"},
                ) or ""
                if bus_for_tm:
                    context_dict["scene_bus_summary"] = bus_for_tm
            try:
                async for tm_event in self.teammate_response_service.process_round_stream(
                    party=party,
                    player_input=player_input,
                    gm_response=gm_narration or npc_response_text,
                    context=context_dict,
                ):
                    yield tm_event
                    if tm_event.get("type") == "teammate_end" and tm_event.get("response"):
                        teammate_responses.append({
                            "character_id": tm_event["character_id"],
                            "name": tm_event["name"],
                            "response": tm_event["response"],
                        })
            except Exception as exc:
                logger.warning("[interact] teammate observer failed: %s", exc)

        # ── C: Dialogue Options ──
        dialogue_options = await self._generate_dialogue_options(
            npc_name, npc_node, player_input, npc_response_text, session,
        )
        yield {
            "type": "dialogue_options",
            "options": [opt.model_dump() for opt in dialogue_options],
        }

        # ── D: Persist ──
        if session.history:
            session.history.record_round(
                player_input=player_input,
                gm_response=gm_narration or "",
                metadata={"source": "interact", "npc_id": npc_id},
            )
            session.history.record_npc_response(
                character_id=npc_id,
                name=npc_name,
                dialogue=npc_response_text,
            )
            for t in teammate_responses:
                session.history.record_teammate_response(
                    character_id=t["character_id"],
                    name=t["name"],
                    response=t["response"],
                )

        # 叙事计数
        if session.narrative:
            npc_interactions = getattr(session.narrative, "npc_interactions", None)
            if isinstance(npc_interactions, dict):
                npc_interactions[npc_id] = npc_interactions.get(npc_id, 0) + 1
            session.mark_narrative_dirty()

        # SceneBus 图谱化 + clear
        if session.scene_bus and self.memory_graphizer:
            try:
                from app.services.scene_bus_graphizer import graphize_scene_bus_round
                await graphize_scene_bus_round(
                    scene_bus=session.scene_bus,
                    session=session,
                    memory_graphizer=self.memory_graphizer,
                )
            except Exception as exc:
                logger.warning("[interact] graphize failed: %s", exc)
        if session.scene_bus:
            session.scene_bus.clear()

        await session.persist()

        yield {
            "type": "complete",
            "npc_id": npc_id,
            "npc_name": npc_name,
            "npc_response": npc_response_text,
            "gm_observation": gm_narration,
            "teammate_responses": teammate_responses,
        }

    # =================================================================
    # 私聊 /private-chat 交互流
    # =================================================================

    async def process_private_chat_stream(
        self,
        world_id: str,
        session_id: str,
        npc_id: str,
        player_input: str,
    ):
        """私聊 SSE 流 — 完整 Pipeline，GM/队友观察跳过。

        流程: Setup → NPC Agentic Response → 对话选项 → 持久化
        与 process_interact_stream 对齐，但跳过 GM 观察和队友旁观（私密模式）。
        使用 InstanceManager 双层认知（上下文窗口 + 记忆图谱化）。
        """
        import json as _json

        # ── A: Setup ──
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
            session_store=self.session_store,
            graph_store=self.graph_store,
        )
        await session.restore()

        if not session.player:
            yield {"type": "error", "error": "请先创建角色后再开始冒险。"}
            return

        wg = getattr(session, "world_graph", None)
        if not wg:
            yield {"type": "error", "error": "世界图谱不可用。"}
            return

        npc_node = wg.get_node(npc_id)
        if not npc_node:
            yield {"type": "error", "error": f"NPC '{npc_id}' 不存在。"}
            return

        npc_name = npc_node.name or npc_id

        # InstanceManager 双层认知
        if not self.instance_manager:
            yield {"type": "error", "error": "InstanceManager 不可用。"}
            return

        instance = await self.instance_manager.get_or_create(npc_id, world_id)
        instance.context_window.add_message("user", player_input)

        # SceneBus: contact + 写入玩家发言
        if session.scene_bus:
            from app.world.scene_bus import BusEntry, BusEntryType
            session.scene_bus.contact(npc_id)
            session.scene_bus.publish(BusEntry(
                actor="player",
                actor_name="player",
                type=BusEntryType.SPEECH,
                content=player_input,
            ))

        yield {"type": "interact_start", "npc_id": npc_id, "npc_name": npc_name}

        # ── B: NPC Agentic Response ──
        npc_response_text = ""
        try:
            from app.world.agentic_executor import AgenticExecutor
            from app.world.immersive_tools import AgenticContext

            npc_traits = set(npc_node.properties.get("traits", []))
            event_queue: asyncio.Queue = asyncio.Queue()

            npc_ctx = AgenticContext(
                session=session,
                agent_id=npc_id,
                role="npc",
                scene_bus=session.scene_bus,
                world_id=world_id,
                chapter_id=getattr(session, "chapter_id", ""),
                area_id=getattr(session, "area_id", ""),
                location_id=getattr(session, "sub_location", ""),
                recall_orchestrator=self.recall_orchestrator,
                graph_store=self.graph_store,
            )

            # 系统提示来自 InstanceManager（保留双层认知 + 记忆注入）
            npc_system_prompt = instance.context_window.get_system_prompt()
            npc_model, npc_thinking = self._select_npc_model(npc_node)

            # 构建 user_prompt：最近对话历史
            character_name = instance.config.name if instance.config else npc_name
            recent_messages = instance.context_window.get_all_messages()
            conversation_lines = []
            for msg in recent_messages[-20:]:
                role_label = "玩家" if msg.role == "user" else character_name
                conversation_lines.append(f"{role_label}: {msg.content}")
            npc_user_prompt = "\n".join(conversation_lines)

            executor = AgenticExecutor(self.flash_cpu.llm_service)
            npc_result = await executor.run(
                ctx=npc_ctx,
                system_prompt=npc_system_prompt,
                user_prompt=npc_user_prompt,
                traits=npc_traits,
                event_queue=event_queue,
                model_override=npc_model,
                thinking_level=npc_thinking,
            )

            # Drain NPC tool events
            while not event_queue.empty():
                evt = event_queue.get_nowait()
                evt["character_id"] = npc_id
                yield evt

            npc_response_text = npc_result.narration or ""
            yield {
                "type": "npc_response",
                "npc_id": npc_id,
                "npc_name": npc_name,
                "text": npc_response_text,
            }

            # 写回 InstanceManager 上下文 + SceneBus
            instance.context_window.add_message("assistant", npc_response_text)
            if session.scene_bus and npc_response_text:
                from app.world.scene_bus import BusEntry, BusEntryType
                session.scene_bus.publish(BusEntry(
                    actor=npc_id,
                    actor_name=npc_name,
                    type=BusEntryType.SPEECH,
                    content=npc_response_text,
                ))
        except Exception as exc:
            logger.error("[private_chat] NPC response failed: %s", exc, exc_info=True)
            yield {"type": "error", "error": f"NPC 回复失败: {exc}"}

        # ── C: 后处理（私密模式 — 跳过 GM/队友） ──

        # C1: 对话选项
        dialogue_options = await self._generate_dialogue_options(
            npc_name, npc_node, player_input, npc_response_text, session,
        )
        yield {
            "type": "dialogue_options",
            "options": [opt.model_dump() for opt in dialogue_options],
        }

        # C2: 历史记录
        if session.history:
            session.history.record_round(
                player_input=player_input,
                gm_response="",
                metadata={"source": "private_chat", "npc_id": npc_id},
            )
            session.history.record_npc_response(
                character_id=npc_id,
                name=npc_name,
                dialogue=npc_response_text,
            )

        # C3: 叙事计数
        if session.narrative:
            npc_interactions = getattr(session.narrative, "npc_interactions", None)
            if isinstance(npc_interactions, dict):
                npc_interactions[npc_id] = npc_interactions.get(npc_id, 0) + 1
            session.mark_narrative_dirty()

        # C4: SceneBus 图谱化 + clear
        if session.scene_bus and self.memory_graphizer:
            try:
                from app.services.scene_bus_graphizer import graphize_scene_bus_round
                await graphize_scene_bus_round(
                    scene_bus=session.scene_bus,
                    session=session,
                    memory_graphizer=self.memory_graphizer,
                )
            except Exception as exc:
                logger.warning("[private_chat] graphize failed: %s", exc)
        if session.scene_bus:
            session.scene_bus.clear()

        # C5: InstanceManager 图谱化检查
        try:
            await self.instance_manager.maybe_graphize_instance(world_id, npc_id)
        except Exception as exc:
            logger.debug("[private_chat] instance graphize check failed: %s", exc)

        # C6: 统一持久化
        await session.persist()

        yield {
            "type": "complete",
            "npc_id": npc_id,
            "npc_name": npc_name,
            "npc_response": npc_response_text,
            "gm_observation": "",
            "teammate_responses": [],
        }

    def _build_npc_system_prompt(self, npc_node: Any, session: Any) -> str:
        """从 WorldGraph 节点属性构建 NPC 系统提示。"""
        props = npc_node.properties
        if props.get("system_prompt"):
            return props["system_prompt"]

        parts = [f"你是{npc_node.name}。"]
        if props.get("occupation"):
            parts.append(f"职业：{props['occupation']}。")
        if props.get("personality"):
            parts.append(f"性格：{props['personality']}。")
        if props.get("speech_pattern"):
            parts.append(f"说话风格：{props['speech_pattern']}。")
        if props.get("background"):
            parts.append(f"背景：{props['background'][:200]}。")
        if props.get("example_dialogue"):
            parts.append(f"\n示例对话：\n{props['example_dialogue']}")

        # 好感度上下文
        disps = npc_node.state.get("dispositions", {}).get("player", {})
        if disps:
            a, t = disps.get("approval", 0), disps.get("trust", 0)
            if a or t:
                parts.append(f"\n你对冒险者的态度：好感{a:+d}，信任{t:+d}。")

        parts.append("\n以第一人称回应，保持角色一致性。直接输出对话。")
        return "\n".join(parts)

    @staticmethod
    def _select_npc_model(npc_node: Any):
        """根据 NPC 层级选择模型。"""
        tier = npc_node.properties.get("tier", "secondary")
        is_essential = npc_node.state.get("is_essential", False)
        cfg = settings.npc_tier_config
        if tier == "main" or is_essential:
            return cfg.main_model, cfg.main_thinking
        return cfg.secondary_model, cfg.secondary_thinking

    async def _generate_dialogue_options(
        self,
        npc_name: str,
        npc_node: Any,
        player_input: str,
        npc_response: str,
        session: Any,
    ):
        """生成 4 个对话选项。"""
        import json as _json
        from app.models.admin_protocol import DialogueOption

        prompt = (
            f"你在与{npc_name}对话。\n你刚说：{player_input}\n{npc_name}回复：{npc_response}\n\n"
            "生成4个简短对话选项（15字以内），覆盖不同态度。\n"
            '[{"text":"...","intent":"...","tone":"curious"},...]'
        )
        try:
            raw = await self.flash_cpu.llm_service.generate_simple(
                prompt,
                model_override=settings.npc_tier_config.passerby_model,
            )
            stripped = getattr(self.flash_cpu.llm_service, "_strip_code_block", lambda x: x)(raw or "[]")
            parsed = _json.loads(stripped)
            if isinstance(parsed, list):
                return [
                    DialogueOption(
                        text=o["text"],
                        intent=o.get("intent", ""),
                        tone=o.get("tone", "neutral"),
                    )
                    for o in parsed[:4]
                    if o.get("text")
                ]
        except Exception as exc:
            logger.warning("[interact] dialogue options failed: %s", exc)

        return [
            DialogueOption(text="继续询问", intent="continue", tone="curious"),
            DialogueOption(text="表示感谢", intent="thank", tone="friendly"),
            DialogueOption(text="告辞离开", intent="leave", tone="neutral"),
            DialogueOption(text="追问细节", intent="dig_deeper", tone="curious"),
        ]

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

    async def _pipeline_npc_dialogue(
        self,
        session: SessionRuntime,
        world: Any,
        npc_id: str,
        player_input: str,
        event_queue: Optional[asyncio.Queue],
    ) -> Optional[Dict[str, str]]:
        """Pipeline 层 NPC 对话生成 — 从 IntentExecutor 上移。

        Returns {"name": display_name, "response": text} on success, None on failure.
        """
        try:
            from app.models.admin_protocol import FlashOperation, FlashRequest
            req = FlashRequest(
                operation=FlashOperation.NPC_DIALOGUE,
                parameters={"npc_id": npc_id, "message": player_input or "你好"},
            )
            resp = await self.flash_cpu.execute_request(
                world_id=session.world_id,
                session_id=session.session_id,
                request=req,
                generate_narration=False,
            )
            if resp.success and isinstance(resp.result, dict):
                response_text = resp.result.get("response", "")
                if response_text:
                    char_data = world.get_character(npc_id) if world else None
                    display_name = (
                        (char_data or {}).get("name", npc_id)
                        if isinstance(char_data, dict)
                        else npc_id
                    )
                    if session.scene_bus:
                        from app.world.scene_bus import BusEntry, BusEntryType
                        session.scene_bus.publish(
                            BusEntry(
                                actor=npc_id,
                                actor_name=display_name,
                                type=BusEntryType.SPEECH,
                                content=response_text,
                            ),
                            event_queue,
                        )
                    return {"name": display_name, "response": response_text}
        except Exception as exc:
            logger.warning("[v4] Pipeline NPC dialogue failed for %s, GM will handle: %s", npc_id, exc)
        return None

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
