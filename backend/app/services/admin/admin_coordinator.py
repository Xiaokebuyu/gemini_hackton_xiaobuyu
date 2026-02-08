"""
Admin coordinator - entrypoint for centralized admin layer.

Flash-Only 架构：
1. Flash 一次性分析 (analyze_and_plan)
2. Flash 执行操作 (execute_request)
3. Flash 生成叙述 (GM + 队友)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Any, List, Optional, Dict

from app.config import settings
from app.services.admin.flash_cpu_service import FlashCPUService
from app.services.admin.state_manager import StateManager
from app.services.memory_graphizer import MemoryGraphizer
from app.services.session_history import SessionHistoryManager
from app.models.game import (
    CombatResolveRequest,
    CombatResolveResponse,
    CombatStartRequest,
    CombatStartResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    GameSessionState,
    GamePhase,
    SceneState,
    UpdateSceneRequest,
)
from app.models.admin_protocol import (
    CoordinatorResponse,
    FlashOperation,
    FlashRequest,
    FlashResponse,
    IntentType,
)
from app.services.game_session_store import GameSessionStore
from app.services.flash_service import FlashService
from app.services.admin.event_service import AdminEventService
from app.services.graph_store import GraphStore
from app.services.narrative_service import NarrativeService
from app.services.passerby_service import PasserbyService
from app.services.admin.world_runtime import AdminWorldRuntime
from app.services.party_service import PartyService
from app.services.party_store import PartyStore
from app.services.teammate_response_service import TeammateResponseService
from app.services.teammate_visibility_manager import TeammateVisibilityManager
from app.models.graph_scope import GraphScope


class AdminCoordinator:
    """Core coordinator for admin-layer game flow."""

    _instance: Optional["AdminCoordinator"] = None

    @classmethod
    def get_instance(cls) -> "AdminCoordinator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def __init__(
        self,
        session_store: Optional[GameSessionStore] = None,
        state_manager: Optional[StateManager] = None,
        event_service: Optional[AdminEventService] = None,
        graph_store: Optional[GraphStore] = None,
        flash_service: Optional[FlashService] = None,
        narrative_service: Optional[NarrativeService] = None,
        passerby_service: Optional[PasserbyService] = None,
        world_runtime: Optional[AdminWorldRuntime] = None,
        flash_cpu: Optional[FlashCPUService] = None,
        party_service: Optional[PartyService] = None,
        teammate_response_service: Optional[TeammateResponseService] = None,
        visibility_manager: Optional[TeammateVisibilityManager] = None,
    ) -> None:
        self._session_store = session_store or GameSessionStore()
        self._state_manager = state_manager or StateManager()
        self.event_service = event_service or AdminEventService()
        self.graph_store = graph_store or GraphStore()
        self.flash_service = flash_service or FlashService(self.graph_store)
        self.narrative_service = narrative_service or NarrativeService(self._session_store)
        from app.services.admin.story_director import StoryDirector
        self.story_director = StoryDirector(self.narrative_service)
        self.passerby_service = passerby_service or PasserbyService()
        self._world_runtime = world_runtime or AdminWorldRuntime(
            state_manager=self._state_manager,
            session_store=self._session_store,
            narrative_service=self.narrative_service,
            event_service=self.event_service,
        )

        # 共享 InstanceManager（队友 + NPC 实例化）
        from app.services.instance_manager import InstanceManager
        self.instance_manager = InstanceManager(
            max_instances=settings.instance_pool_max_instances,
            context_window_size=settings.instance_pool_context_window_size,
            graphize_threshold=settings.instance_pool_graphize_threshold,
            keep_recent_tokens=settings.instance_pool_keep_recent_tokens,
            graph_store=self.graph_store,
        )

        self.flash_cpu = flash_cpu or FlashCPUService(
            state_manager=self._state_manager,
            world_runtime=self._world_runtime,
            session_store=self._session_store,
            event_service=self.event_service,
            narrative_service=self.narrative_service,
            passerby_service=self.passerby_service,
            instance_manager=self.instance_manager,
        )

        # 队友系统
        self.party_store = PartyStore()
        self.party_service = party_service or PartyService(self.graph_store, self.party_store)
        # 补充注入 party_service 到 flash_cpu（flash_cpu 创建早于 party_service）
        self.flash_cpu.party_service = self.party_service
        self.teammate_response_service = teammate_response_service or TeammateResponseService(
            instance_manager=self.instance_manager,
        )
        self.visibility_manager = visibility_manager or TeammateVisibilityManager()

        # 会话历史 + 图谱化
        self.session_history_manager = SessionHistoryManager(
            firestore_db=self.graph_store.db,
            max_tokens=settings.session_history_max_tokens,
            graphize_threshold=settings.session_history_graphize_threshold,
            keep_recent_tokens=settings.session_history_keep_recent_tokens,
        )
        self.memory_graphizer = MemoryGraphizer(graph_store=self.graph_store)

        # 世界背景 / 角色花名册缓存
        self._world_background_cache: Dict[str, str] = {}
        self._character_roster_cache: Dict[str, str] = {}

    @dataclass
    class AdminContextView:
        world_id: str
        session_id: str
        phase: GamePhase
        game_day: int
        current_scene: Any = None
        current_npc: Optional[str] = None
        known_characters: list = None

    # ==================== World listing ====================

    async def list_worlds(self) -> list[dict]:
        """列出 Firestore 中所有已初始化的世界。

        使用 list_documents() 而非 stream()，因为世界初始化器只创建
        子集合（meta/info, maps/, characters/ 等）而不一定创建根文档。
        list_documents() 能发现这些"虚拟"父文档。
        """
        worlds_ref = self.graph_store.db.collection("worlds")
        worlds = []
        for doc_ref in worlds_ref.list_documents():
            world_id = doc_ref.id
            meta_doc = doc_ref.collection("meta").document("info").get()
            meta = meta_doc.to_dict() if meta_doc.exists else {}
            if not meta:
                continue
            worlds.append({
                "id": world_id,
                "name": meta.get("name") or meta.get("title") or world_id,
                "description": meta.get("description") or meta.get("overview") or "",
            })
        return worlds

    # ==================== Session history ====================

    async def get_session_history(
        self,
        world_id: str,
        session_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """从 Firestore 获取会话聊天历史"""
        return await self.session_history_manager.load_history_from_firestore(
            world_id, session_id, limit, self.graph_store.db,
        )

    # ==================== GameLoop compatible methods ====================

    async def create_session(self, world_id: str, request: CreateSessionRequest) -> CreateSessionResponse:
        state = await self._session_store.create_session(world_id, request.session_id, request.participants)
        return CreateSessionResponse(session=state)

    async def get_session(self, world_id: str, session_id: str) -> GameSessionState | None:
        return await self._session_store.get_session(world_id, session_id)

    async def list_recoverable_sessions(
        self,
        world_id: str,
        user_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        列出当前用户在某世界可恢复的会话。

        仅返回可用于 v2 继续游玩的会话：
        - 非 demo 会话
        - 包含 admin_state
        """
        sessions = await self._session_store.list_sessions(
            world_id=world_id,
            user_id=user_id,
            limit=limit,
        )

        recoverable: List[Dict[str, Any]] = []
        for session in sessions:
            if session.session_id.startswith("demo-") or session.session_id == "demo-session-001":
                continue

            metadata = session.metadata or {}
            admin_state = metadata.get("admin_state")
            if not isinstance(admin_state, dict):
                continue

            # 加载队伍信息
            party_member_count = 0
            party_members_names: List[str] = []
            try:
                party_obj = await self.party_store.get_party(world_id, session.session_id)
                if party_obj:
                    for m in party_obj.get_active_members():
                        party_member_count += 1
                        party_members_names.append(m.name)
            except Exception:
                pass

            recoverable.append(
                {
                    "session_id": session.session_id,
                    "world_id": session.world_id,
                    "status": session.status,
                    "updated_at": session.updated_at,
                    "participants": session.participants,
                    "player_location": admin_state.get("player_location"),
                    "chapter_id": admin_state.get("chapter_id"),
                    "sub_location": admin_state.get("sub_location"),
                    "party_member_count": party_member_count,
                    "party_members": party_members_names,
                }
            )

        return recoverable

    async def update_scene(self, world_id: str, session_id: str, request: UpdateSceneRequest) -> GameSessionState:
        await self._session_store.set_scene(world_id, session_id, request.scene)
        state = await self._session_store.get_session(world_id, session_id)
        if not state:
            raise ValueError("session not found")
        return state

    async def start_combat(
        self,
        world_id: str,
        session_id: str,
        request: CombatStartRequest,
    ) -> CombatStartResponse:
        payload = await self.flash_cpu.call_combat_tool(
            "start_combat_session",
            {
                "world_id": world_id,
                "session_id": session_id,
                "enemies": request.enemies,
                "player_state": request.player_state,
                "environment": request.environment,
                "allies": request.allies,
                "combat_context": request.combat_context.model_dump(),
            },
        )
        if payload.get("error"):
            raise ValueError(payload["error"])

        session_data = payload.get("session")
        session_state = GameSessionState(**session_data) if session_data else await self._session_store.get_session(world_id, session_id)
        combat_state = payload.get("combat_state", {})
        return CombatStartResponse(
            combat_id=payload.get("combat_id", ""),
            combat_state=combat_state,
            session=session_state,
        )

    async def resolve_combat(
        self,
        world_id: str,
        session_id: str,
        request: CombatResolveRequest,
    ) -> CombatResolveResponse:
        payload = await self.flash_cpu.call_combat_tool(
            "resolve_combat_session",
            {
                "world_id": world_id,
                "session_id": session_id,
                "combat_id": request.combat_id,
                "use_engine": request.use_engine,
                "result_override": request.result_override,
                "summary_override": request.summary_override,
                "dispatch": request.dispatch,
                "recipients": request.recipients,
                "per_character": request.per_character,
                "write_indexes": request.write_indexes,
                "validate": request.validate_input,
                "strict": request.strict,
            },
        )
        if payload.get("error"):
            raise ValueError(payload["error"])
        return CombatResolveResponse(
            combat_id=payload.get("combat_id", ""),
            event_id=payload.get("event_id"),
            dispatched=payload.get("dispatched", False),
        )

    # ==================== Flash-Only 架构 (v2) ====================

    async def process_player_input_v2(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
    ) -> CoordinatorResponse:
        """
        Flash-Only 架构的玩家输入处理流程（两轮 Flash）。

        流程：
        1. 收集基础上下文
        2. Flash 第一轮分析 (intent + operations + memory seeds)
        3. 并行召回记忆 + 顺序执行操作
        4. 组装完整上下文
        4.5 Flash 第二轮：基于扩散激活结果编排 context_package
        5. 导航后同步队友位置
        6. Flash 生成叙述
        7. 队友响应
        8. 分发事件到队友图谱

        Args:
            world_id: 世界ID
            session_id: 会话ID
            player_input: 玩家输入

        Returns:
            CoordinatorResponse: 完整响应
        """
        # 预检查：位置上下文必须可用，避免进入"未知地点"伪成功叙述
        location_check = await self._world_runtime.get_current_location(world_id, session_id)
        if location_check.get("error"):
            raise ValueError(location_check["error"])

        # 0. 获取队伍信息 + 会话历史
        party = await self.party_service.get_party(world_id, session_id)
        history = self.session_history_manager.get_or_create(world_id, session_id)

        # 1. 收集上下文
        base_context = await self._build_context(world_id, session_id)

        # 1.5 注入对话历史到上下文（上下文连续性）
        # Flash 侧优先使用大上下文窗口（1M）
        conversation_history = history.get_recent_history(max_tokens=settings.session_history_max_tokens)
        if conversation_history:
            base_context["conversation_history"] = conversation_history

        # 1.8 StoryDirector Pre-Flash 评估（纯机械，< 1ms）
        progress = await self.narrative_service.get_progress(world_id, session_id)
        active_story_chapters = self._resolve_story_chapters(world_id, progress)
        current_chapter = active_story_chapters[0] if active_story_chapters else None
        game_ctx = self._build_game_context(base_context, progress, session_id, player_input)

        if len(active_story_chapters) > 1:
            pre_directive = self.story_director.pre_evaluate_multi(
                game_ctx, active_story_chapters
            )
        else:
            pre_directive = self.story_director.pre_evaluate(game_ctx, current_chapter)

        # 处理纯机械触发的事件
        pre_side_effects: List[Dict[str, Any]] = []
        for event in pre_directive.auto_fired_events:
            await self.narrative_service.trigger_event(world_id, session_id, event.id)
            logger.info("[v2] StoryDirector pre-fire: %s", event.id)
            if event.side_effects:
                pre_side_effects.extend(event.side_effects)
        if pre_side_effects:
            await self._execute_side_effects(world_id, session_id, pre_side_effects)

        # 注入到 base_context 供 Flash 感知
        base_context["story_directives"] = pre_directive.narrative_injections
        base_context["pending_flash_conditions"] = [
            {"id": c.condition_id, "prompt": c.condition_prompt, "event_id": c.event_id}
            for c in pre_directive.pending_flash_conditions
        ]

        # 2. Flash 一次性分析
        logger.info("[v2] 步骤2: Flash 分析")
        analysis = await self.flash_cpu.analyze_and_plan(player_input, base_context)
        intent = analysis.intent
        logger.info("[v2] 意图=%s 目标=%s seeds=%d", intent.intent_type.value, intent.target, len(analysis.memory_seeds or []))

        # 3. 并行召回记忆 + 顺序执行操作
        logger.info("[v2] 步骤3: 执行操作")
        # 获取当前 chapter/area 上下文用于多图谱合并扩散
        state = await self._state_manager.get_state(world_id, session_id)
        current_chapter_id = getattr(state, "chapter_id", None) if state else None
        current_area_id = getattr(state, "area_id", None) if state else None

        # 总是构建基线 seeds（保证最小世界感知）
        effective_seeds = self._build_effective_seeds(
            analysis.memory_seeds,
            base_context,
            character_id="player",
        )

        memory_task = asyncio.create_task(
            self._recall_memory(
                world_id, "player", effective_seeds,
                intent_type=intent.intent_type.value,
                chapter_id=current_chapter_id,
                area_id=current_area_id,
            )
        )

        flash_requests = analysis.operations or self._generate_default_flash_requests(intent, base_context)
        flash_results = await self._execute_flash_requests(
            world_id, session_id, flash_requests, generate_narration=False
        )
        logger.info("[v2] Flash 结果: %d 个操作", len(flash_results))
        for r in flash_results:
            logger.debug("[v2]   操作=%s 成功=%s 错误=%s", r.operation, r.success, r.error)

        # 3.5 LEAVE_SUB_LOCATION 直接处理（无对应 FlashOperation）
        if intent.intent_type == IntentType.LEAVE_SUB_LOCATION:
            try:
                leave_result = await self._world_runtime.leave_sub_location(world_id, session_id)
                flash_results.append(FlashResponse(
                    success=leave_result.get("success", False),
                    operation=FlashOperation.ENTER_SUBLOCATION,
                    result=leave_result,
                    error=leave_result.get("error") if not leave_result.get("success") else None,
                ))
            except Exception as e:
                logger.error("[v2] 离开子地点失败: %s", e)

        memory_result = await memory_task if memory_task else None

        # 4. 组装完整上下文
        context = await self._assemble_context(
            base_context, memory_result, flash_results, world_id, session_id
        )

        # 保留第一轮分析产出的结构化编排建议（第二轮可能覆盖 context_package）。
        if isinstance(analysis.context_package, dict) and analysis.context_package:
            context["context_package"] = dict(analysis.context_package)
        if isinstance(analysis.story_progression, dict):
            context["story_progression"] = analysis.story_progression

        # 4.5 第二轮 Flash：基于扩散激活结果编排 context_package
        if memory_result and getattr(memory_result, "activated_nodes", None):
            logger.info("[v2] 步骤4.5: Flash 上下文编排（第二轮）")
            curated_package = await self.flash_cpu.curate_context(
                player_input=player_input,
                intent=intent,
                memory_result=memory_result,
                flash_results=flash_results,
                context=context,
            )
            if curated_package:
                merged_package = {}
                if isinstance(context.get("context_package"), dict):
                    merged_package.update(context["context_package"])
                merged_package.update(curated_package)
                context["context_package"] = merged_package
                if isinstance(curated_package.get("story_progression"), dict):
                    context["story_progression"] = curated_package["story_progression"]
                logger.info("[v2] context_package 已生成（%d 个字段）", len(merged_package))
            else:
                logger.error("[v2] context_package 编排失败，curate_context 返回 None")

        # 4.8 StoryDirector Post-Flash 评估
        flash_condition_results: Dict[str, bool] = {}
        story_prog = context.get("story_progression")
        if isinstance(story_prog, dict):
            for eval_item in story_prog.get("condition_evaluations", []):
                if isinstance(eval_item, dict) and "id" in eval_item:
                    flash_condition_results[str(eval_item["id"])] = bool(eval_item.get("result", False))

        # 刷新进度（Pre-Flash 可能触发了事件）
        progress = await self.narrative_service.get_progress(world_id, session_id)
        active_story_chapters = self._resolve_story_chapters(world_id, progress)
        current_chapter = active_story_chapters[0] if active_story_chapters else None
        game_ctx_updated = self._build_game_context(context, progress, session_id, player_input)

        if len(active_story_chapters) > 1:
            final_directive = self.story_director.post_evaluate_multi(
                game_ctx_updated,
                chapters=active_story_chapters,
                flash_condition_results=flash_condition_results,
                pre_auto_fired_ids=[e.id for e in pre_directive.auto_fired_events],
            )
        else:
            final_directive = self.story_director.post_evaluate(
                game_ctx_updated,
                chapter=current_chapter,
                flash_condition_results=flash_condition_results,
                pre_auto_fired_ids=[e.id for e in pre_directive.auto_fired_events],
            )

        # 执行副作用
        if final_directive.side_effects:
            await self._execute_side_effects(world_id, session_id, final_directive.side_effects)

        turn_story_events = list(pre_directive.auto_fired_events) + list(final_directive.fired_events)

        # 落地 fired_events
        for event in final_directive.fired_events:
            await self.narrative_service.trigger_event(world_id, session_id, event.id)
            logger.info("[v2] StoryDirector post-fire: %s", event.id)

        # 注入叙述指令到 context 供 GM 叙述使用
        all_narrative_directives = pre_directive.narrative_injections + final_directive.narrative_injections
        if all_narrative_directives:
            context["story_narrative_directives"] = all_narrative_directives

        # 更新回合计数
        progress.rounds_in_chapter += 1
        has_progress = bool(final_directive.fired_events or pre_directive.auto_fired_events)
        if has_progress:
            progress.rounds_since_last_progress = 0
        else:
            progress.rounds_since_last_progress += 1
        # NPC 交互计数
        for req, res in zip(flash_requests, flash_results):
            if res.success and res.operation == FlashOperation.NPC_DIALOGUE:
                npc_id = req.parameters.get("npc_id")
                if npc_id:
                    progress.npc_interactions[npc_id] = progress.npc_interactions.get(npc_id, 0) + 1
        # 冷却时间记录（以章节内回合作为基准）
        for event in turn_story_events:
            progress.event_cooldowns[event.id] = progress.rounds_in_chapter
        await self.narrative_service.save_progress(world_id, session_id, progress)

        # 处理章节转换（v2 StoryDirector 评估的 transitions）
        v2_transition_result: Optional[Dict[str, Any]] = None
        if final_directive.chapter_transition:
            trans = final_directive.chapter_transition
            logger.info(
                "[v2] StoryDirector 章节转换: → %s (%s)",
                trans.target_chapter_id,
                trans.transition_type,
            )
            # narrative_hint 注入叙述
            if trans.narrative_hint:
                context.setdefault("story_narrative_directives", []).append(trans.narrative_hint)

            v2_transition_result = await self.narrative_service.transition_to_chapter(
                world_id=world_id,
                session_id=session_id,
                target_chapter_id=trans.target_chapter_id,
                transition_type=trans.transition_type,
            )
            if state and v2_transition_result.get("new_chapter"):
                state.chapter_id = v2_transition_result["new_chapter"]
                await self._state_manager.set_state(world_id, session_id, state)

        # StoryDirector 同回合强一致写图（失败阻断回合）
        if settings.story_event_graph_sync and (turn_story_events or final_directive.chapter_transition):
            await self._sync_story_director_graph(
                world_id=world_id,
                session_id=session_id,
                events=turn_story_events,
                chapter_transition=final_directive.chapter_transition,
                context=context,
                player_input=player_input,
                party=party,
                progress=progress,
            )

        # 5. 导航后同步队友位置
        if intent.intent_type == IntentType.NAVIGATION and party:
            logger.info("[v2] 步骤5: 同步队友位置")
            new_location = context.get("location", {}).get("location_id")
            if new_location:
                await self.party_service.sync_locations(
                    world_id, session_id, new_location
                )

        # 6. 生成叙述和角色回复（Flash 并发）
        logger.info("[v2] 步骤6: Flash 叙述与角色回复")
        execution_summary = self._build_execution_summary(flash_results)

        # 为每个队友构建图谱上下文（Flash 第二轮编排）
        if party and party.get_active_members():
            teammate_context_packages, teammate_memory_summaries = await self._build_teammate_context_packages(
                world_id=world_id,
                party=party,
                player_input=player_input,
                intent=intent,
                flash_results=flash_results,
                base_context=context,
                base_seeds=analysis.memory_seeds,
                chapter_id=current_chapter_id,
                area_id=current_area_id,
            )
            if teammate_context_packages:
                context["teammate_context_packages"] = teammate_context_packages
            if teammate_memory_summaries:
                context["teammate_memory_summaries"] = teammate_memory_summaries

        gm_task = asyncio.create_task(
            self.flash_cpu.generate_gm_narration(
                player_input=player_input,
                execution_summary=execution_summary,
                context=context,
            )
        )

        teammate_task = None
        if party and party.get_active_members():
            logger.info("[v2] 步骤7: 队友响应（%d 个队友）", len(party.get_active_members()))
            # 与 GM 并发：队友基于同轮执行摘要生成
            teammate_task = asyncio.create_task(
                self.teammate_response_service.process_round(
                    party=party,
                    player_input=player_input,
                    gm_response=execution_summary,
                    context=context,
                )
            )
        else:
            logger.debug("[v2] 步骤7: 跳过（无队友）")

        gm_narration = await gm_task
        logger.info("[v2] 叙述完成: %s...", gm_narration[:50])

        if v2_transition_result and v2_transition_result.get("new_chapter"):
            transition_text = await self._generate_chapter_transition(
                world_id,
                session_id,
                v2_transition_result.get("new_chapter"),
                v2_transition_result.get("new_maps_unlocked", []),
            )
            if transition_text:
                gm_narration += f"\n\n{transition_text}"

        teammate_responses = []
        if teammate_task:
            teammate_result = await teammate_task
            logger.info("[v2] 队友响应完成: %d 个响应", len(teammate_result.responses))
            for resp in teammate_result.responses:
                if resp.response:  # 只包含实际回复的
                    teammate_responses.append({
                        "character_id": resp.character_id,
                        "name": resp.name,
                        "response": resp.response,
                        "reaction": resp.reaction,
                        "model_used": resp.model_used,
                        "thinking_level": resp.thinking_level,
                        "latency_ms": resp.latency_ms,
                    })

        # 8. 分发事件到队友图谱（如果有队友且启用事件共享）
        if party and party.share_events:
            logger.info("[v2] 步骤8: 分发事件到队友图谱")
            await self._distribute_event_to_party(
                world_id=world_id,
                party=party,
                player_input=player_input,
                gm_response=gm_narration,
                context=context,
            )
            logger.info("[v2] 事件分发完成")

        # 8.5 strict-v2: 仅记录 Flash 自报事件用于调试（不再走 legacy fallback）
        story_prog = context.get("story_progression")
        if not isinstance(story_prog, dict):
            story_prog = (context.get("context_package") or {}).get("story_progression", {})
        flash_story_events: List[str] = []
        story_events_raw = story_prog.get("story_events", []) if isinstance(story_prog, dict) else []
        if isinstance(story_events_raw, list):
            for event_id in story_events_raw:
                if isinstance(event_id, str) and event_id.strip():
                    flash_story_events.append(event_id.strip())
        if flash_story_events:
            logger.debug("[v2] Flash self-reported story events: %s", flash_story_events)

        # 9. 记录对话到 SessionHistory + 触发图谱化
        round_stats = history.record_round(
            player_input=player_input,
            gm_response=gm_narration,
            metadata={
                "intent_type": intent.intent_type.value,
                "story_events": [event.id for event in turn_story_events],
                "transition": (
                    final_directive.chapter_transition.target_chapter_id
                    if final_directive.chapter_transition else ""
                ),
                "chapter_id": (
                    v2_transition_result.get("new_chapter")
                    if v2_transition_result and v2_transition_result.get("new_chapter")
                    else progress.current_chapter
                ),
            },
        )
        for tr in teammate_responses:
            history.record_teammate_response(
                character_id=tr["character_id"],
                name=tr["name"],
                response=tr["response"],
            )
        logger.info(
            "[v2] 步骤9: 历史记录 msgs=%d tokens=%d usage=%.1f%%",
            round_stats["message_count"],
            round_stats["total_tokens"],
            round_stats["usage_ratio"] * 100,
        )

        # 图谱化检查（fire-and-forget 模式不阻塞响应）
        if round_stats["should_graphize"]:
            game_day = context.get("time", {}).get("day", 1)
            location_id = context.get("location", {}).get("location_id")
            asyncio.create_task(
                self._run_graphization(history, game_day, location_id)
            )

        # 10. 构建最终响应
        state_delta = self._merge_state_deltas(flash_results)

        available_actions = await self._get_available_actions(world_id, session_id, context)

        return CoordinatorResponse(
            narration=gm_narration,
            speaker="GM",
            teammate_responses=teammate_responses,
            available_actions=available_actions,
            state_delta=state_delta,
            metadata={
                "intent_type": intent.intent_type.value,
                "confidence": intent.confidence,
                "teammate_count": len(teammate_responses),
                "analysis_reasoning": analysis.reasoning,
                "session_history": round_stats,
                "source": "flash_gm",
                "model": "flash",
            },
            story_events=[e.id for e in turn_story_events] if turn_story_events else [],
            pacing_action=final_directive.pacing_action if final_directive else None,
            chapter_info={
                "id": progress.current_chapter,
                "transition": final_directive.chapter_transition.target_chapter_id
            } if (final_directive and final_directive.chapter_transition) else None,
        )

    async def _distribute_event_to_party(
        self,
        world_id: str,
        party: "Party",
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
    ) -> None:
        """分发事件到队友图谱（v2 双视角写入）。

        双视角写入：
        1. 叙述 event 写入区域叙述图谱（narrative perspective）
        2. 个人 event 写入各角色图谱（personal perspective）
        3. perspective_of 边链接两个 event 节点
        """
        import uuid as _uuid
        from app.models.party import Party
        from app.models.graph import MemoryEdge, MemoryNode

        def _format_context_snippet(filtered: Dict[str, Any]) -> str:
            lines = []
            location = filtered.get("location") or {}
            if location:
                loc_name = location.get("location_name") or location.get("location_id")
                if loc_name:
                    lines.append(f"地点: {loc_name}")
            time_info_inner = filtered.get("time") or {}
            if time_info_inner:
                time_text = time_info_inner.get("formatted")
                if not time_text:
                    d = time_info_inner.get("day")
                    h = time_info_inner.get("hour")
                    m = time_info_inner.get("minute")
                    if d is not None and h is not None and m is not None:
                        time_text = f"第{d}天 {h:02d}:{m:02d}"
                if time_text:
                    lines.append(f"时间: {time_text}")
            return "\n".join(lines)

        gm_excerpt = gm_response.strip()
        if len(gm_excerpt) > 400:
            gm_excerpt = f"{gm_excerpt[:400]}..."

        base_description = f"玩家: {player_input}\nGM: {gm_excerpt}"

        location_info = context.get("location") or {}
        location_name = location_info.get("location_name") or location_info.get("location_id")
        if location_name:
            base_description = f"地点: {location_name}\n{base_description}"

        time_info = context.get("time") or {}
        if time_info:
            time_text = time_info.get("formatted")
            if not time_text:
                day = time_info.get("day")
                hour = time_info.get("hour")
                minute = time_info.get("minute")
                if day is not None and hour is not None and minute is not None:
                    time_text = f"第{day}天 {hour:02d}:{minute:02d}"
            if time_text:
                base_description = f"时间: {time_text}\n{base_description}"

        known_characters = [m.character_id for m in party.get_active_members()]
        if "player" not in known_characters:
            known_characters.append("player")
        known_locations = [location_name] if location_name else []

        parsed_event = await self.event_service.llm_service.parse_event(
            event_description=base_description,
            known_characters=known_characters,
            known_locations=known_locations,
        )

        event = {
            "event_type": "player_action",
            "description": base_description,
            "visibility": "party",
            "participants": parsed_event.get("participants") or ["player"],
            "witnesses": parsed_event.get("witnesses") or [m.character_id for m in party.get_active_members()],
            "location": parsed_event.get("location") or location_name,
        }

        game_day = time_info.get("day") if isinstance(time_info, dict) else None
        if game_day is None:
            game_day = 1

        # 获取当前 chapter/area 上下文
        party_session_id = getattr(party, "session_id", None)
        state = (
            await self._state_manager.get_state(world_id, party_session_id)
            if party_session_id
            else None
        )
        chapter_id = getattr(state, "chapter_id", None) if state else None
        area_id = getattr(state, "area_id", None) if state else None

        # --- 双视角写入 ---
        narrative_event_id = f"event_narr_{_uuid.uuid4().hex[:12]}"
        narrative_event_written = False

        # 1. 叙述 event 写入区域叙述图谱（如果有 chapter/area 上下文）
        if chapter_id and area_id:
            narrative_node = MemoryNode(
                id=narrative_event_id,
                type="event",
                name=parsed_event.get("summary", "事件")[:60],
                importance=max(0.3, min(0.9, parsed_event.get("importance", 0.5))),
                properties={
                    "perspective": "narrative",
                    "scope_type": "area",
                    "chapter_id": chapter_id,
                    "area_id": area_id,
                    "day": int(game_day),
                    "summary": base_description[:300],
                    "participants": event.get("participants", []),
                    "witnesses": event.get("witnesses", []),
                    "location": location_name,
                    "sub_type": parsed_event.get("event_type", "social"),
                },
            )
            area_scope = GraphScope(
                scope_type="area", chapter_id=chapter_id, area_id=area_id,
            )
            await self.graph_store.upsert_node_v2(world_id, area_scope, narrative_node)
            logger.debug("[事件分发] 叙述 event 已写入区域图谱: %s", narrative_event_id)
            narrative_event_written = True

        # 2. 个人 event 写入各角色图谱 + perspective_of 边
        tasks = []
        for member in party.get_active_members():
            if not self.visibility_manager.should_teammate_know(member, event, party):
                continue

            filtered_context = self.visibility_manager.filter_context_for_teammate(
                teammate=member,
                full_context={
                    "player_input": player_input,
                    "gm_response": gm_response,
                    **context,
                },
                event=event,
            )

            perspective = self._determine_teammate_perspective(member, event)
            snippet = _format_context_snippet(filtered_context)
            teammate_description = (
                f"{base_description}\n\n补充信息:\n{snippet}"
                if snippet
                else base_description
            )

            async def _write_personal_event(
                char_id: str,
                desc: str,
                persp: str,
            ) -> None:
                # 写入个人视角 event 到角色图谱
                result = await self.event_service.ingest_for_character(
                    world_id=world_id,
                    character_id=char_id,
                    event_description=desc,
                    parsed_event=parsed_event,
                    perspective=persp,
                    game_day=int(game_day),
                )
                # 如果叙述 event 存在，写入 perspective_of 边链接
                if (
                    narrative_event_written
                    and chapter_id
                    and area_id
                    and result
                    and result.event_node_ids
                ):
                    personal_event_id = result.event_node_ids[0]
                    perspective_edge = MemoryEdge(
                        id=f"edge_perspof_{char_id}_{narrative_event_id[:20]}",
                        source=personal_event_id,
                        target=narrative_event_id,
                        relation="perspective_of",
                        weight=1.0,
                        properties={
                            "perspective": "personal",
                            "character_id": char_id,
                            "created_by": "game_event",
                            "game_day": int(game_day),
                        },
                    )
                    char_scope = GraphScope(scope_type="character", character_id=char_id)
                    await self.graph_store.upsert_edge_v2(
                        world_id, char_scope, perspective_edge
                    )

            tasks.append(
                _write_personal_event(member.character_id, teammate_description, perspective)
            )

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error("[事件分发] 队友事件写入失败: %s", result)

    def _determine_teammate_perspective(
        self,
        member: "PartyMember",
        event: Dict[str, Any],
    ) -> str:
        """确定队友视角类型"""
        participants = set(event.get("participants") or [])
        witnesses = set(event.get("witnesses") or [])
        if member.character_id in participants:
            return "participant"
        if member.character_id in witnesses:
            return "witness"
        visibility = event.get("visibility")
        if visibility in ("party", "public"):
            return "bystander"
        return "rumor"

    async def _run_graphization(
        self,
        history: "SessionHistory",
        game_day: int,
        current_scene: Optional[str],
        retry: int = 0,
    ) -> None:
        """Fire-and-forget graphization task with one retry."""
        try:
            result = await history.maybe_graphize(
                graphizer=self.memory_graphizer,
                graph_store=self.graph_store,
                game_day=game_day,
                current_scene=current_scene,
            )
            if result:
                logger.info(
                    "[图谱化] 完成: nodes=%d edges=%d freed=%d tokens",
                    result["nodes_added"], result["edges_added"], result["tokens_freed"],
                )
            else:
                logger.info("[图谱化] 跳过：无需图谱化")
        except Exception as exc:
            logger.error("[图谱化] 异常 (retry=%d): %s", retry, exc, exc_info=True)
            if retry < 1:
                await asyncio.sleep(2)
                await self._run_graphization(history, game_day, current_scene, retry=retry + 1)

    def _build_effective_seeds(
        self,
        seeds: Optional[List[str]],
        context: Dict[str, Any],
        character_id: Optional[str] = None,
    ) -> List[str]:
        """构建基础召回 seeds（含兜底，且去重保序）。"""
        effective_seeds = list(seeds or [])
        location_id = (context.get("location") or {}).get("location_id")
        if location_id:
            effective_seeds.append(location_id)
        active_npc = context.get("active_npc")
        if active_npc:
            effective_seeds.append(active_npc)
        if character_id:
            effective_seeds.append(character_id)
        if not effective_seeds:
            effective_seeds.append("player")
        return list(dict.fromkeys(s for s in effective_seeds if s))

    async def _build_teammate_context_packages(
        self,
        world_id: str,
        party: "Party",
        player_input: str,
        intent: Any,
        flash_results: List[FlashResponse],
        base_context: Dict[str, Any],
        base_seeds: Optional[List[str]],
        chapter_id: Optional[str],
        area_id: Optional[str],
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        """为每个队友生成基于图谱召回的 Flash 上下文包。"""
        packages: Dict[str, Dict[str, Any]] = {}
        memory_summaries: Dict[str, str] = {}
        members = party.get_active_members()
        if not members:
            return packages, memory_summaries

        async def _build_for_member(member) -> tuple[str, Optional[Dict[str, Any]], str]:
            char_id = member.character_id
            seeds = self._build_effective_seeds(base_seeds, base_context, character_id=char_id)
            memory_result = await self._recall_memory(
                world_id=world_id,
                character_id=char_id,
                seed_nodes=seeds,
                intent_type=intent.intent_type.value,
                chapter_id=chapter_id,
                area_id=area_id,
            )
            if not memory_result or not getattr(memory_result, "activated_nodes", None):
                return char_id, None, ""

            teammate_view = dict(base_context)
            teammate_view["active_character_id"] = char_id
            curated = await self.flash_cpu.curate_context(
                player_input=player_input,
                intent=intent,
                memory_result=memory_result,
                flash_results=flash_results,
                context=teammate_view,
            )
            summary = self._summarize_memory(memory_result)
            return char_id, curated, summary

        results = await asyncio.gather(*(_build_for_member(m) for m in members), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error("[v2] 队友上下文编排失败: %s", result, exc_info=True)
                continue
            char_id, curated, summary = result
            if curated:
                packages[char_id] = curated
            if summary:
                memory_summaries[char_id] = summary

        return packages, memory_summaries

    # ==================== 游戏状态恢复 ====================

    async def resume_session(
        self,
        world_id: str,
        session_id: str,
        generate_narration: bool = True,
    ) -> Dict[str, Any]:
        """完整恢复游戏会话状态。

        加载 admin_state、队伍、对话历史，预热队友实例，
        并可选生成恢复叙述帮助玩家回忆进度。
        """
        # 1. 加载 admin_state（从 Firestore 恢复到 StateManager）
        state = await self._world_runtime.get_state(world_id, session_id)
        if not state:
            raise ValueError(f"session {session_id} not found or has no admin_state")

        # 2. 加载队伍（从 Firestore 加载并缓存）
        party = await self.party_service.get_party(world_id, session_id)

        # 3. 加载对话历史（自动从 Firestore 恢复）
        history = self.session_history_manager.get_or_create(world_id, session_id)

        # 4. 预热队友实例（恢复 state metadata）
        prewarmed_members = []
        if party:
            for member in party.get_active_members():
                try:
                    await self.instance_manager.get_or_create(
                        member.character_id, world_id,
                    )
                    prewarmed_members.append(member.name)
                except Exception as exc:
                    logger.warning(
                        "[resume] 队友实例预热失败 (%s): %s",
                        member.character_id, exc,
                    )

        # 5. 获取当前位置信息
        location = await self._world_runtime.get_current_location(world_id, session_id)

        # 6. 构建恢复数据
        history_stats = {
            "message_count": history._window.message_count,
            "total_tokens": history._window.current_tokens,
        }

        party_info: Dict[str, Any] = {"has_party": False, "members": []}
        if party:
            party_info = {
                "has_party": True,
                "party_id": party.party_id,
                "members": [
                    {
                        "character_id": m.character_id,
                        "name": m.name,
                        "role": m.role.value,
                        "is_active": m.is_active,
                    }
                    for m in party.members
                ],
            }

        state_dict = {
            "player_location": state.player_location,
            "sub_location": state.sub_location,
            "chapter_id": getattr(state, "chapter_id", None),
            "game_time": state.game_time.model_dump() if state.game_time else None,
        }

        # 7. 可选生成恢复叙述
        resume_narration = ""
        if generate_narration:
            resume_narration = await self._generate_resume_narration(
                world_id=world_id,
                location=location,
                state=state,
                party=party,
                history=history,
            )

        return {
            "session_id": session_id,
            "restored": True,
            "state": state_dict,
            "location": location,
            "party": party_info,
            "history": history_stats,
            "resume_narration": resume_narration,
            "prewarmed_members": prewarmed_members,
        }

    async def _generate_resume_narration(
        self,
        world_id: str,
        location: Dict[str, Any],
        state: Any,
        party: Any,
        history: Any,
    ) -> str:
        """生成恢复叙述，帮助玩家回忆进度。"""
        from app.services.llm_service import LLMService

        location_name = location.get("location_name") or location.get("location_id") or "未知地点"
        time_info = state.game_time
        time_text = ""
        if time_info:
            day = getattr(time_info, "day", None)
            hour = getattr(time_info, "hour", None)
            minute = getattr(time_info, "minute", None)
            if day is not None and hour is not None and minute is not None:
                time_text = f"第{day}天 {hour:02d}:{minute:02d}"

        teammate_names = []
        if party:
            teammate_names = [m.name for m in party.get_active_members()]

        recent_history = history.get_recent_history(max_tokens=2000) if history else ""

        prompt_path = Path("app/prompts/session_resume.md")
        if prompt_path.exists():
            prompt_template = prompt_path.read_text(encoding="utf-8")
            prompt = prompt_template.format(
                location_name=location_name,
                time=time_text or "未知",
                teammates=", ".join(teammate_names) if teammate_names else "无",
                recent_history=recent_history or "无",
            )
        else:
            prompt = (
                f"你是游戏 GM。玩家正在恢复之前的游戏会话。请根据以下信息生成2-3句沉浸式中文叙述，"
                f"帮助玩家回忆他们上次游戏的进度和状态。不要输出JSON。\n\n"
                f"当前位置: {location_name}\n"
                f"时间: {time_text or '未知'}\n"
                f"队友: {', '.join(teammate_names) if teammate_names else '无'}\n"
                f"近期对话:\n{recent_history or '无'}\n"
            )

        try:
            llm_service = self.flash_cpu.llm_service
            result = await llm_service.generate_simple(
                prompt,
                model_override=settings.admin_flash_model,
                thinking_level=settings.admin_flash_thinking_level,
            )
            narration = (result or "").strip()
            if narration:
                return narration
        except Exception as exc:
            logger.error("[resume_narration] LLM 生成失败: %s", exc)

        # fallback
        parts = [f"你回到了{location_name}。"]
        if time_text:
            parts.append(f"现在是{time_text}。")
        if teammate_names:
            parts.append(f"{', '.join(teammate_names)}还在你身边。")
        return "".join(parts)

    async def generate_opening_narration(
        self,
        world_id: str,
        session_id: str,
    ) -> str:
        """生成开场叙述"""
        try:
            # 构建上下文
            location = await self._world_runtime.get_current_location(world_id, session_id)
            location_name = location.get("location_name") or location.get("location_id") or "未知地点"
            location_atmosphere = location.get("atmosphere") or ""
            npcs_present = ", ".join(location.get("npcs_present", [])) or "无"

            time_info = await self._world_runtime.get_game_time(world_id, session_id)
            time_text = time_info.get("formatted") or time_info.get("formatted_time") or "未知"

            world_background = await self._get_world_background(world_id, session_id)

            # 章节信息
            chapter_plan = await self.narrative_service.get_current_chapter_plan(world_id, session_id)
            chapter_obj = chapter_plan.get("chapter") or {}
            chapter_name = chapter_obj.get("name", "序章")
            chapter_description = chapter_obj.get("description", "")
            chapter_goals = chapter_plan.get("goals", [])

            # 队友
            party = await self.party_service.get_party(world_id, session_id)
            teammates = "无"
            if party:
                names = [m.name for m in party.get_active_members()]
                if names:
                    teammates = ", ".join(names)

            # 加载 prompt
            prompt_path = Path("app/prompts/opening_narration.md")
            if prompt_path.exists():
                prompt_template = prompt_path.read_text(encoding="utf-8")
                prompt = prompt_template.format(
                    world_background=world_background or "未知世界",
                    chapter_name=chapter_name,
                    chapter_description=chapter_description[:300] or "冒险的开始",
                    chapter_goals="、".join(chapter_goals[:3]) if chapter_goals else "探索世界",
                    location_name=location_name,
                    location_atmosphere=location_atmosphere or "平静",
                    npcs_present=npcs_present,
                    teammates=teammates,
                    time=time_text,
                )
            else:
                prompt = (
                    f"你是TRPG游戏GM。玩家刚开始冒险，请生成3-5句开场叙述。\n\n"
                    f"位置：{location_name}\n时间：{time_text}\n"
                    f"章节：{chapter_name}\n不要输出JSON。"
                )

            result = await self.flash_cpu.llm_service.generate_simple(
                prompt,
                model_override=settings.admin_flash_model,
                thinking_level=settings.admin_flash_thinking_level,
            )
            narration = (result or "").strip()
            if narration:
                return narration
        except Exception as exc:
            logger.error("[opening_narration] 生成失败: %s", exc)

        # fallback
        fallback_name = location_name if "location_name" in locals() else "未知地点"
        return f"冒险在{fallback_name}开始了。"

    async def _generate_chapter_transition(
        self,
        world_id: str,
        session_id: str,
        new_chapter_id: Optional[str],
        new_maps_unlocked: List[str],
    ) -> str:
        """生成章节过渡叙述"""
        if not new_chapter_id:
            return ""

        chapter_info = self.narrative_service.get_chapter_info(world_id, new_chapter_id)
        chapter_name = chapter_info.get("name", new_chapter_id) if chapter_info else new_chapter_id
        chapter_desc = chapter_info.get("description", "") if chapter_info else ""

        maps_text = ""
        if new_maps_unlocked and "*" not in new_maps_unlocked:
            maps_text = f"\n新区域已解锁：{'、'.join(new_maps_unlocked)}"

        prompt = (
            f"你是TRPG游戏GM。当前章节已完成，故事推进到新章节。"
            f"请用2-3句中文叙述章节转换，营造过渡感。不要输出JSON。\n\n"
            f"新章节：{chapter_name}\n"
            f"章节描述：{chapter_desc}\n"
            f"{maps_text}"
        )

        try:
            result = await self.flash_cpu.llm_service.generate_simple(
                prompt,
                model_override=settings.admin_flash_model,
                thinking_level=settings.admin_flash_thinking_level,
            )
            return (result or "").strip() or f"故事进入了新篇章——{chapter_name}。"
        except Exception as exc:
            logger.error("[chapter_transition] LLM 生成失败: %s", exc)
            return f"故事进入了新篇章——{chapter_name}。{maps_text}"

    async def _get_world_background(self, world_id: str, session_id: Optional[str] = None) -> str:
        """获取世界背景描述（缓存）"""
        if world_id in self._world_background_cache:
            base_text = self._world_background_cache[world_id]
        else:
            parts = []
            try:
                world_ref = self.graph_store.db.collection("worlds").document(world_id)
                # 优先读取初始化器写入的 worlds/{world_id}/meta/info
                meta_doc = world_ref.collection("meta").document("info").get()
                data = meta_doc.to_dict() if meta_doc.exists else {}
                # 兼容旧数据结构（世界根文档）
                if not data:
                    root_doc = world_ref.get()
                    if root_doc.exists:
                        data = root_doc.to_dict() or {}

                if data:
                    desc = data.get("description") or data.get("overview") or ""
                    if desc:
                        parts.append(desc)
                    name = data.get("name") or data.get("title") or ""
                    if name and not desc.startswith(name):
                        parts.insert(0, f"世界: {name}")
            except Exception as exc:
                logger.debug("[world_background] Firestore 读取失败: %s", exc)

            base_text = "\n".join(parts) if parts else ""
            self._world_background_cache[world_id] = base_text

        chapter_block = ""
        if session_id:
            try:
                progress = await self.narrative_service.get_progress(world_id, session_id)
                chapter_id = getattr(progress, "current_chapter", None)
                if chapter_id:
                    chapter_info = self.narrative_service.get_chapter_info(world_id, chapter_id) or {}
                    chapter_name = chapter_info.get("name") or chapter_id
                    chapter_desc = chapter_info.get("description") or ""
                    chapter_block = f"当前章节: {chapter_name}"
                    if chapter_desc:
                        chapter_block += f"\n{chapter_desc}"
                    try:
                        chapter_plan = await self.narrative_service.get_current_chapter_plan(
                            world_id=world_id,
                            session_id=session_id,
                        )
                        goals = chapter_plan.get("goals", []) if isinstance(chapter_plan, dict) else []
                        if goals:
                            goal_lines = "\n".join(f"- {goal}" for goal in goals[:3])
                            chapter_block += f"\n当前推进目标:\n{goal_lines}"
                    except Exception as inner_exc:
                        logger.debug("[world_background] chapter_plan 读取失败: %s", inner_exc)
            except Exception as exc:
                logger.debug("[world_background] chapter 读取失败: %s", exc)

        blocks = [b for b in (base_text, chapter_block) if b]
        return "\n".join(blocks)

    async def _get_character_roster(self, world_id: str) -> str:
        """获取世界角色花名册（缓存）"""
        if world_id in self._character_roster_cache:
            return self._character_roster_cache[world_id]

        entries = []
        try:
            chars_ref = self.graph_store.db.collection("worlds").document(world_id).collection("characters")
            docs = chars_ref.stream()
            for doc in docs:
                data = doc.to_dict() or {}
                profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
                metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
                state = data.get("state") if isinstance(data.get("state"), dict) else {}
                char_id = doc.id
                name = profile.get("name") or data.get("name") or char_id
                occupation = (
                    profile.get("occupation")
                    or data.get("occupation")
                    or profile.get("role")
                    or data.get("role")
                    or ""
                )
                default_map = (
                    metadata.get("default_map")
                    or profile.get("default_map")
                    or data.get("default_map")
                    or state.get("current_map")
                    or data.get("default_location")
                    or ""
                )
                default_sub = metadata.get("default_sub_location") or profile.get("default_sub_location") or ""
                parts = [f"{name}(id={char_id}"]
                if occupation:
                    parts[0] += f", {occupation}"
                if default_map:
                    parts[0] += f", 常驻:{default_map}"
                if default_sub:
                    parts[0] += f"/{default_sub}"
                parts[0] += ")"
                entries.append(parts[0])
        except Exception as exc:
            logger.debug("[character_roster] Firestore 读取失败: %s", exc)

        result = ", ".join(entries) if entries else ""
        self._character_roster_cache[world_id] = result
        return result

    async def _build_context(self, world_id: str, session_id: str) -> Dict[str, Any]:
        """构建当前上下文"""
        context = {"world_id": world_id}

        # 获取位置信息
        location = await self._world_runtime.get_current_location(world_id, session_id)
        context["location"] = location
        context["available_destinations"] = location.get("available_destinations", [])
        # 添加子地点信息（字段名是 available_sub_locations）
        context["sub_locations"] = location.get("available_sub_locations", [])

        # 获取时间
        time_info = await self._world_runtime.get_game_time(world_id, session_id)
        context["time"] = time_info

        # 获取状态
        state = await self._state_manager.get_state(world_id, session_id)
        if state:
            context["state"] = "in_dialogue" if state.active_dialogue_npc else (
                "combat" if state.combat_id else "exploring"
            )
            context["active_npc"] = state.active_dialogue_npc
        else:
            context["state"] = "exploring"
            context["active_npc"] = None

        # 队友信息
        party = await self.party_service.get_party(world_id, session_id)
        if party:
            context["party"] = party
            context["teammates"] = [
                {
                    "character_id": m.character_id,
                    "name": m.name,
                    "role": m.role.value,
                    "personality": m.personality,
                    "current_mood": m.current_mood,
                }
                for m in party.get_active_members()
            ]
        else:
            context["teammates"] = []

        # 世界背景 + 角色花名册
        context["world_background"] = await self._get_world_background(world_id, session_id)
        context["character_roster"] = await self._get_character_roster(world_id)

        # 章节信息
        try:
            chapter_plan = await self.narrative_service.get_current_chapter_plan(world_id, session_id)
            context["chapter_info"] = chapter_plan
        except Exception as exc:
            logger.debug("[_build_context] 章节信息获取失败: %s", exc)
            context["chapter_info"] = {}

        return context

    def _resolve_story_chapters(self, world_id: str, progress: Any) -> List[Any]:
        """解析当前激活章节（主章节 + 并行章节）。"""
        chapters_by_id = self.narrative_service._world_chapters(world_id)
        chapter_ids: List[str] = []

        current_chapter_id = getattr(progress, "current_chapter", "")
        if isinstance(current_chapter_id, str) and current_chapter_id.strip():
            chapter_ids.append(current_chapter_id.strip())

        for chapter_id in getattr(progress, "active_chapters", []) or []:
            if not isinstance(chapter_id, str):
                continue
            chapter_id = chapter_id.strip()
            if chapter_id and chapter_id not in chapter_ids:
                chapter_ids.append(chapter_id)

        chapters: List[Any] = []
        for chapter_id in chapter_ids:
            chapter = chapters_by_id.get(chapter_id)
            if chapter:
                chapters.append(chapter)
        return chapters

    def _build_game_context(
        self,
        base_context: Dict[str, Any],
        progress: Any,
        session_id: str,
        player_input: str = "",
    ) -> "GameContext":
        """从 base_context + progress 构建 GameContext 快照。"""
        from app.services.admin.condition_engine import GameContext

        location = base_context.get("location") or {}
        time_info = base_context.get("time") or {}
        party = base_context.get("party")
        teammates = base_context.get("teammates") or []

        party_member_ids: List[str] = []
        if party and hasattr(party, "get_active_members"):
            party_member_ids = [m.character_id for m in party.get_active_members()]
        elif teammates:
            party_member_ids = [
                t.get("character_id", "") for t in teammates if isinstance(t, dict)
            ]

        return GameContext(
            session_id=session_id,
            area_id=location.get("location_id") or location.get("area_id") or "",
            sub_location=location.get("sub_location_id"),
            game_day=int(time_info.get("day", 0)),
            game_hour=int(time_info.get("hour", 0)),
            game_minute=int(time_info.get("minute", 0)),
            game_state=base_context.get("state", "exploring"),
            active_npc=base_context.get("active_npc"),
            party_member_ids=party_member_ids,
            events_triggered=list(progress.events_triggered),
            objectives_completed=list(progress.objectives_completed),
            rounds_in_chapter=progress.rounds_in_chapter,
            npc_interactions=dict(progress.npc_interactions),
            event_cooldowns=dict(progress.event_cooldowns),
            rounds_since_last_progress=progress.rounds_since_last_progress,
            player_input=player_input,
            conversation_history=base_context.get("conversation_history", ""),
        )

    async def _sync_story_director_graph(
        self,
        world_id: str,
        session_id: str,
        events: List[Any],
        chapter_transition: Optional[Any],
        context: Dict[str, Any],
        player_input: str,
        party: Optional[Any],
        progress: Any,
    ) -> None:
        """将 StoryDirector 本回合结果同步写入图谱（失败即抛错阻断回合）。"""
        from app.models.graph import MemoryEdge, MemoryNode

        location = context.get("location") or {}
        time_info = context.get("time") or {}
        chapter_id = getattr(progress, "current_chapter", "") or ""
        area_id = location.get("location_id") or location.get("area_id") or ""
        game_day = int(time_info.get("day", 0))
        round_idx = int(getattr(progress, "rounds_in_chapter", 0))
        session_key = str(session_id).replace("-", "_")

        character_ids = ["player"]
        if party and hasattr(party, "get_active_members"):
            for member in party.get_active_members():
                char_id = getattr(member, "character_id", "")
                if isinstance(char_id, str) and char_id and char_id not in character_ids:
                    character_ids.append(char_id)

        async def _write_story_event(
            story_event_id: str,
            name: str,
            summary: str,
            event_type: str,
            transition_target: str = "",
        ) -> None:
            safe_event = str(story_event_id).replace("/", "_").replace(" ", "_")
            narrative_id = f"story_narr_{session_key}_{safe_event}_{game_day}_{round_idx}"
            narrative_props = {
                "perspective": "narrative",
                "scope_type": "area" if (chapter_id and area_id) else "world",
                "source": "story_director_v2",
                "story_event_id": story_event_id,
                "event_type": event_type,
                "chapter_id": chapter_id,
                "area_id": area_id,
                "transition_target": transition_target,
                "day": game_day,
                "round": round_idx,
                "summary": summary[:500],
            }
            narrative_node = MemoryNode(
                id=narrative_id,
                type="event",
                name=(name or story_event_id)[:80],
                importance=0.85,
                properties=narrative_props,
            )

            # world 视图必写
            await self.graph_store.upsert_node_v2(
                world_id=world_id,
                scope=GraphScope.world(),
                node=narrative_node,
            )
            # area 视图可选写
            if chapter_id and area_id:
                await self.graph_store.upsert_node_v2(
                    world_id=world_id,
                    scope=GraphScope.area(chapter_id=chapter_id, area_id=area_id),
                    node=narrative_node,
                )

            for char_id in character_ids:
                personal_id = f"story_personal_{char_id}_{safe_event}_{game_day}_{round_idx}"
                personal_node = MemoryNode(
                    id=personal_id,
                    type="event",
                    name=(name or story_event_id)[:80],
                    importance=0.8,
                    properties={
                        "perspective": "personal",
                        "source": "story_director_v2",
                        "story_event_id": story_event_id,
                        "event_type": event_type,
                        "character_id": char_id,
                        "chapter_id": chapter_id,
                        "area_id": area_id,
                        "transition_target": transition_target,
                        "day": game_day,
                        "round": round_idx,
                        "summary": summary[:500],
                    },
                )
                char_scope = GraphScope.character(char_id)
                await self.graph_store.upsert_node_v2(
                    world_id=world_id,
                    scope=char_scope,
                    node=personal_node,
                )
                await self.graph_store.upsert_edge_v2(
                    world_id=world_id,
                    scope=char_scope,
                    edge=MemoryEdge(
                        id=f"edge_story_perspective_{char_id}_{safe_event}_{game_day}_{round_idx}",
                        source=personal_id,
                        target=narrative_id,
                        relation="perspective_of",
                        weight=1.0,
                        properties={
                            "source": "story_director_v2",
                            "chapter_id": chapter_id,
                            "area_id": area_id,
                            "day": game_day,
                            "round": round_idx,
                        },
                    ),
                )

        for event in events:
            summary = getattr(event, "description", "") or getattr(event, "narrative_directive", "") or player_input
            await _write_story_event(
                story_event_id=str(getattr(event, "id", "")),
                name=str(getattr(event, "name", "")),
                summary=summary,
                event_type="story_event",
            )

        if chapter_transition:
            target = str(getattr(chapter_transition, "target_chapter_id", "")).strip()
            if target:
                await _write_story_event(
                    story_event_id=f"transition_{target}",
                    name=f"章节转换:{target}",
                    summary=f"章节转换至 {target}",
                    event_type="chapter_transition",
                    transition_target=target,
                )

    async def _execute_side_effects(
        self,
        world_id: str,
        session_id: str,
        effects: List[Dict[str, Any]],
    ) -> None:
        """执行 StoryDirector 副作用（地图解锁、队伍变化等）。"""
        for effect in effects:
            effect_type = effect.get("type", "")
            try:
                if effect_type == "unlock_map":
                    map_id = effect.get("map_id")
                    if map_id:
                        logger.info("[v2] 副作用: 解锁地图 %s", map_id)
                        # NarrativeService 没有动态添加地图的 API，记录日志
                        # 地图可用性由当前章节的 available_maps 控制
                        # 章节转换时会自动解锁下一章的地图
                elif effect_type == "add_teammate":
                    character_id = effect.get("character_id")
                    if character_id:
                        logger.info("[v2] 副作用: 添加队友 %s", character_id)
                        name = effect.get("name", character_id)
                        try:
                            await self.party_service.add_member(
                                world_id=world_id,
                                session_id=session_id,
                                character_id=character_id,
                                name=name,
                            )
                            logger.info("[v2] 队友已添加: %s (%s)", name, character_id)
                        except Exception as add_exc:
                            logger.warning("[v2] 添加队友失败 %s: %s", character_id, add_exc)
                elif effect_type == "remove_teammate":
                    character_id = effect.get("character_id")
                    if character_id:
                        logger.info("[v2] 副作用: 移除队友 %s", character_id)
                        try:
                            removed = await self.party_service.remove_member(
                                world_id=world_id,
                                session_id=session_id,
                                character_id=character_id,
                            )
                            if removed:
                                logger.info("[v2] 队友已移除: %s", character_id)
                            else:
                                logger.debug("[v2] 队友不在队伍中: %s", character_id)
                        except Exception as rm_exc:
                            logger.warning("[v2] 移除队友失败 %s: %s", character_id, rm_exc)
                else:
                    logger.debug("[v2] 未知副作用类型: %s", effect_type)
            except Exception as exc:
                logger.error("[v2] 副作用执行失败 (%s): %s", effect_type, exc)

    # 意图→召回配置映射
    _RECALL_CONFIGS: Dict[str, Dict[str, Any]] = {
        "exploration": {"depth": 1, "output_threshold": 0.3},
        "dialogue": {"depth": 2, "output_threshold": 0.2},
        "npc_interaction": {"depth": 2, "output_threshold": 0.2},
        "recall": {"depth": 3, "output_threshold": 0.1},
        "lore": {"depth": 3, "output_threshold": 0.1},
        "combat": {"depth": 1, "output_threshold": 0.4},
        "start_combat": {"depth": 1, "output_threshold": 0.4},
        "navigation": {"depth": 1, "output_threshold": 0.3},
        "team_interaction": {"depth": 2, "output_threshold": 0.2},
        "roleplay": {"depth": 2, "output_threshold": 0.2},
        "enter_sub_location": {"depth": 1, "output_threshold": 0.3},
        "leave_sub_location": {"depth": 1, "output_threshold": 0.3},
        "wait": {"depth": 1, "output_threshold": 0.3},
    }

    async def _recall_memory(
        self,
        world_id: str,
        character_id: str,
        seed_nodes: List[str],
        intent_type: Optional[str] = None,
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
    ):
        """召回记忆（异步），合并多层图谱后统一扩散激活。

        仅 v2: 加载角色图谱 + 营地图谱，并按上下文可选叠加章节/区域图谱。
        """
        from app.models.flash import RecallResponse
        from app.models.activation import SpreadingActivationConfig
        from app.services.memory_graph import MemoryGraph
        from app.services.spreading_activation import spread_activation, extract_subgraph

        # 根据意图选择召回配置
        recall_cfg = self._RECALL_CONFIGS.get(intent_type or "", {})
        output_threshold = recall_cfg.get("output_threshold", 0.15)

        config = SpreadingActivationConfig(
            output_threshold=output_threshold,
            current_chapter_id=chapter_id,
        )

        # 角色（必选）+ 营地（必选）+ 章节/区域（可选）
        scoped_data = []

        # 1) 角色个人图谱（必选）
        char_scope = GraphScope(scope_type="character", character_id=character_id)
        char_data = await self.graph_store.load_graph_v2(world_id, char_scope)
        scoped_data.append((char_scope, char_data))

        # 2) 当前区域叙述图谱（可选）
        if chapter_id and area_id:
            area_scope = GraphScope(
                scope_type="area", chapter_id=chapter_id, area_id=area_id,
            )
            area_data = await self.graph_store.load_graph_v2(world_id, area_scope)
            scoped_data.append((area_scope, area_data))

        # 3) 章节叙述图谱（可选）
        if chapter_id:
            chapter_scope = GraphScope(scope_type="chapter", chapter_id=chapter_id)
            chapter_data = await self.graph_store.load_graph_v2(world_id, chapter_scope)
            scoped_data.append((chapter_scope, chapter_data))

        # 4) 营地图谱（必选）
        camp_scope = GraphScope(scope_type="camp")
        camp_data = await self.graph_store.load_graph_v2(world_id, camp_scope)
        scoped_data.append((camp_scope, camp_data))

        # 5) 世界图谱（让世界书跨 scope 边参与扩散）
        world_scope = GraphScope(scope_type="world")
        world_data = await self.graph_store.load_graph_v2(world_id, world_scope)
        scoped_data.append((world_scope, world_data))

        # 7) 注入好感度动态边权重
        merged = MemoryGraph.from_multi_scope(scoped_data)
        await self._inject_disposition_edges(world_id, character_id, merged)

        # 8) 扩展 seeds 为多候选（兼容 prefill 无前缀 + LLM 有前缀）
        expanded_seeds = []
        for seed in seed_nodes:
            expanded_seeds.append(seed)
            for prefix in ("person_", "character_", "location_", "area_"):
                if seed.startswith(prefix):
                    expanded_seeds.append(seed[len(prefix):])
                else:
                    expanded_seeds.append(f"{prefix}{seed}")
        valid_seeds = [s for s in expanded_seeds if merged.has_node(s)]
        if not valid_seeds:
            logger.error(
                "[v2] 记忆召回跳过：无有效种子 (character=%s, original=%s, expanded=%d)",
                character_id,
                seed_nodes,
                len(expanded_seeds),
            )
            return RecallResponse(
                seed_nodes=seed_nodes,
                activated_nodes={},
                subgraph=None,
                used_subgraph=False,
            )

        # 9) 运行扩散激活
        activated = spread_activation(merged, valid_seeds, config)

        subgraph_graph = extract_subgraph(merged, activated)
        subgraph = subgraph_graph.to_graph_data()
        subgraph.nodes = [
            n for n in subgraph.nodes
            if not (n.properties or {}).get("placeholder", False)
        ]

        return RecallResponse(
            seed_nodes=seed_nodes,
            activated_nodes=activated,
            subgraph=subgraph,
            used_subgraph=True,
        )

    async def _inject_disposition_edges(
        self,
        world_id: str,
        character_id: str,
        graph: "MemoryGraph",
    ) -> None:
        """注入好感度动态边到合并图谱中。

        从 Firestore 加载角色的所有 dispositions，为图谱中存在的
        目标角色创建 approves 边，权重 = (approval+100)/200。
        """
        from app.models.graph import MemoryEdge

        dispositions = await self.graph_store.get_all_dispositions(
            world_id, character_id
        )

        char_node_id = f"character_{character_id}" if not character_id.startswith("character_") else character_id

        for target_id, disp_data in dispositions.items():
            target_node_id = f"character_{target_id}" if not target_id.startswith("character_") else target_id
            if not graph.has_node(char_node_id) or not graph.has_node(target_node_id):
                continue
            approval = disp_data.get("approval", 0)
            weight = (approval + 100) / 200.0
            weight = max(0.0, min(1.0, weight))
            edge_id = f"disposition_{character_id}_{target_id}_approves"
            if not graph.has_edge(edge_id):
                graph.add_edge(MemoryEdge(
                    id=edge_id,
                    source=char_node_id,
                    target=target_node_id,
                    relation="approves",
                    weight=weight,
                    properties={
                        "created_by": "disposition",
                        "approval": approval,
                        "trust": disp_data.get("trust", 0),
                    },
                ))

    async def _assemble_context(
        self,
        base_context: Dict[str, Any],
        memory_result: Any,
        flash_results: List[FlashResponse],
        world_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """组装完整上下文（基础 + 按需刷新变更字段 + 记忆摘要）"""
        context = dict(base_context or {})

        # 根据 Flash 操作类型判断需要刷新的字段，避免全量重查
        ops = {r.operation for r in flash_results if r.success} if flash_results else set()

        refresh_location = ops & {
            FlashOperation.NAVIGATE,
            FlashOperation.ENTER_SUBLOCATION,
        }
        refresh_time = FlashOperation.UPDATE_TIME in ops
        refresh_state = ops & {
            FlashOperation.NPC_DIALOGUE,
            FlashOperation.START_COMBAT,
        }

        if refresh_location:
            location = await self._world_runtime.get_current_location(world_id, session_id)
            context["location"] = location
            context["available_destinations"] = location.get("available_destinations", [])
            context["sub_locations"] = location.get("available_sub_locations", [])

        if refresh_time:
            context["time"] = await self._world_runtime.get_game_time(world_id, session_id)

        if refresh_state:
            state = await self._state_manager.get_state(world_id, session_id)
            if state:
                context["state"] = "in_dialogue" if state.active_dialogue_npc else (
                    "combat" if state.combat_id else "exploring"
                )
                context["active_npc"] = state.active_dialogue_npc

        # 队伍变更后刷新队伍信息
        refresh_party = ops & {
            FlashOperation.ADD_TEAMMATE,
            FlashOperation.REMOVE_TEAMMATE,
            FlashOperation.DISBAND_PARTY,
        }
        if refresh_party:
            party = await self.party_service.get_party(world_id, session_id)
            if party:
                context["party"] = party
                context["teammates"] = [
                    {
                        "character_id": m.character_id,
                        "name": m.name,
                        "role": m.role.value,
                        "personality": m.personality,
                        "current_mood": m.current_mood,
                    }
                    for m in party.get_active_members()
                ]
            else:
                context["party"] = None
                context["teammates"] = []

        if memory_result and getattr(memory_result, "activated_nodes", None):
            context["memory_summary"] = self._summarize_memory(memory_result)
            if hasattr(memory_result, "model_dump"):
                context["recalled_memory"] = memory_result.model_dump()
            else:
                context["recalled_memory"] = memory_result

        if flash_results:
            context["flash_results"] = [
                r.model_dump() if hasattr(r, "model_dump") else r for r in flash_results
            ]

        return context

    def _summarize_memory(self, memory_result) -> str:
        """将记忆结果转为自然语言摘要（含属性、关系和时间信息）"""
        activated = getattr(memory_result, "activated_nodes", {}) or {}
        if not activated:
            return ""

        subgraph = getattr(memory_result, "subgraph", None)
        node_lookup: Dict[str, Any] = {}
        edges: list = []
        if subgraph:
            if getattr(subgraph, "nodes", None):
                node_lookup = {node.id: node for node in subgraph.nodes}
            if getattr(subgraph, "edges", None):
                edges = list(subgraph.edges)

        # 按激活分数排序，过滤占位节点
        scored = sorted(activated.items(), key=lambda x: x[1], reverse=True)
        valid_nodes: List[Any] = []
        for node_id, score in scored:
            node = node_lookup.get(node_id)
            if not node:
                continue
            props = getattr(node, "properties", {}) or {}
            if props.get("placeholder"):
                continue
            valid_nodes.append((node, score))

        # 按类型分组，每类最多3个，总计最多12个
        from collections import defaultdict
        by_type: Dict[str, list] = defaultdict(list)
        for node, score in valid_nodes:
            by_type[node.type].append((node, score))

        selected: List[Any] = []
        for node_type in by_type:
            selected.extend(by_type[node_type][:3])
        # 按分数重排，截断到12个
        selected.sort(key=lambda x: x[1], reverse=True)
        selected = selected[:12]

        selected_ids = {node.id for node, _ in selected}

        # 格式化节点摘要
        summaries = []
        for node, score in selected:
            props = getattr(node, "properties", {}) or {}
            parts = [f"[{node.type}] {node.name}"]
            # 添加关键属性
            detail_keys = ["occupation", "personality", "summary", "description", "status"]
            details = []
            for key in detail_keys:
                val = props.get(key)
                if val and isinstance(val, str):
                    details.append(f"{key}={val}")
            if details:
                parts.append(f"({', '.join(details[:3])})")
            # 添加时间信息
            game_day = props.get("game_day")
            if game_day is not None:
                parts.append(f"[day {game_day}]")
            summaries.append(" ".join(parts))

        # 格式化关系（仅涉及已选节点的边）
        relation_lines = []
        for edge in edges:
            src = getattr(edge, "source", None)
            tgt = getattr(edge, "target", None)
            if src in selected_ids and tgt in selected_ids:
                src_name = node_lookup[src].name if src in node_lookup else src
                tgt_name = node_lookup[tgt].name if tgt in node_lookup else tgt
                relation = getattr(edge, "relation", "related")
                relation_lines.append(f"关系: {src_name} --[{relation}]--> {tgt_name}")

        result_parts = summaries
        if relation_lines:
            result_parts.append("")  # 空行分隔
            result_parts.extend(relation_lines[:8])

        return "\n".join(result_parts)

    @staticmethod
    def _merge_state_deltas(flash_results: List[FlashResponse]) -> Optional["StateDelta"]:
        """合并多个 Flash 结果中的 state_delta。

        后者的 changes 覆盖前者，operation 字符串拼接。
        单个 delta 直接返回，无 delta 返回 None。
        """
        from app.models.state_delta import StateDelta

        deltas = [r.state_delta for r in flash_results if r.state_delta]
        if not deltas:
            return None
        if len(deltas) == 1:
            return deltas[0]

        merged_changes: Dict[str, Any] = {}
        operations: List[str] = []
        for d in deltas:
            merged_changes.update(d.changes)
            operations.append(d.operation)

        return StateDelta(
            delta_id=deltas[-1].delta_id,
            timestamp=deltas[-1].timestamp,
            operation=" + ".join(operations),
            changes=merged_changes,
        )

    def _build_execution_summary(self, flash_results: List[FlashResponse]) -> str:
        """构建执行结果摘要"""
        if not flash_results:
            return "无系统操作执行"

        summaries = []
        for result in flash_results:
            op = result.operation.value if result.operation else "unknown"
            if result.success:
                # 查询类操作需要格式化完整数据
                if result.operation == FlashOperation.GET_STATUS:
                    summaries.append(self._format_status_summary(result.result))
                    continue
                if result.operation == FlashOperation.GET_PROGRESS:
                    summaries.append(self._format_progress_summary(result.result))
                    continue
                detail = (
                    result.result.get("summary")
                    or result.result.get("narration")
                    or result.result.get("description")
                    or ""
                )
                detail = detail.strip() if isinstance(detail, str) else str(detail)
                summaries.append(f"[{op}] 成功: {detail or '执行成功'}")
            else:
                summaries.append(f"[{op}] 失败: {result.error or '未知错误'}")

        return "\n".join(summaries)

    @staticmethod
    def _format_status_summary(data: Dict[str, Any]) -> str:
        """格式化 GET_STATUS 查询结果供 GM 叙述"""
        lines = ["[系统状态查询结果]"]
        loc = data.get("location") or {}
        if loc and not loc.get("error"):
            loc_name = loc.get("location_name") or loc.get("location_id") or "未知"
            lines.append(f"- 当前位置: {loc_name}")
            desc = loc.get("description")
            if desc:
                lines.append(f"  描述: {desc[:100]}")
            npcs = loc.get("npcs_present")
            if npcs:
                lines.append(f"  NPC: {', '.join(npcs)}")
        time_info = data.get("time") or {}
        if time_info and not time_info.get("error"):
            formatted = time_info.get("formatted") or time_info.get("formatted_time")
            if not formatted:
                d = time_info.get("day")
                h = time_info.get("hour")
                m = time_info.get("minute")
                if d is not None and h is not None and m is not None:
                    formatted = f"第{d}天 {h:02d}:{m:02d}"
            if formatted:
                lines.append(f"- 时间: {formatted}")
        party = data.get("party") or {}
        if party.get("has_party") is not False:
            members = party.get("members") or []
            if members:
                member_strs = [f"{m.get('name', m.get('character_id'))}({m.get('role', '?')})" for m in members]
                lines.append(f"- 队伍: {', '.join(member_strs)}")
            else:
                lines.append("- 队伍: 无队友")
        else:
            lines.append("- 队伍: 未组队")
        return "\n".join(lines)

    @staticmethod
    def _format_progress_summary(data: Dict[str, Any]) -> str:
        """格式化 GET_PROGRESS 查询结果供 GM 叙述"""
        import json as _json
        lines = ["[任务进度查询结果]"]
        # get_progress 返回的数据结构取决于 NarrativeService
        if isinstance(data, dict):
            chapter = data.get("current_chapter") or data.get("chapter_id")
            if chapter:
                lines.append(f"- 当前章节: {chapter}")
            quests = data.get("active_quests") or data.get("quests") or data.get("objectives") or []
            if quests:
                lines.append("- 活跃任务:")
                for q in quests[:5]:
                    if isinstance(q, dict):
                        name = q.get("name") or q.get("title") or q.get("id", "未知")
                        status = q.get("status", "进行中")
                        lines.append(f"  * {name} [{status}]")
                    else:
                        lines.append(f"  * {q}")
            completed = data.get("completed_quests") or data.get("completed") or []
            if completed:
                lines.append(f"- 已完成任务: {len(completed)} 个")
            # 如果没有结构化字段，输出原始 JSON 摘要
            if len(lines) == 1:
                raw = _json.dumps(data, ensure_ascii=False, default=str)
                if len(raw) > 300:
                    raw = raw[:300] + "..."
                lines.append(raw)
        return "\n".join(lines)

    async def narrate_state_change(
        self,
        world_id: str,
        session_id: str,
        change_type: str,
        change_details: Dict[str, Any],
    ) -> str:
        """状态变更后的统一叙述（供 navigate/time/dialogue 等端点使用）"""
        context = await self._build_context(world_id, session_id)
        history = self.session_history_manager.get_or_create(world_id, session_id)
        conversation_history = history.get_recent_history(max_tokens=settings.session_history_max_tokens)
        if conversation_history:
            context["conversation_history"] = conversation_history
        summary = self._build_change_summary(change_type, change_details)
        return await self.flash_cpu.generate_gm_narration(
            player_input=summary,
            execution_summary=summary,
            context=context,
        )

    def _build_change_summary(self, change_type: str, details: Dict[str, Any]) -> str:
        """构建状态变更摘要"""
        builders = {
            "navigation": lambda d: f"玩家到达了{d.get('to', '新地点')}",
            "time_advance": lambda d: f"时间过去了{d.get('minutes', 0)}分钟",
            "dialogue_start": lambda d: f"玩家开始与{d.get('npc_name', 'NPC')}对话",
            "sub_location_enter": lambda d: f"玩家进入了{d.get('name', '某处')}",
            "day_advance": lambda d: f"新的一天（第{d.get('day', 1)}天）开始了",
        }
        return builders.get(change_type, lambda d: str(d))(details)

    async def _execute_flash_requests(
        self,
        world_id: str,
        session_id: str,
        flash_requests: List[FlashRequest],
        generate_narration: bool = True,
    ) -> List[FlashResponse]:
        """执行意图中的 Flash 请求"""
        results = []

        logger.info("Flash 请求数量: %d 个", len(flash_requests))
        if not flash_requests:
            return results

        # 执行每个请求
        for i, request in enumerate(flash_requests):
            logger.info("Flash 执行请求 %d: %s params=%s", i+1, request.operation.value, request.parameters)
            try:
                result = await self.flash_cpu.execute_request(
                    world_id, session_id, request, generate_narration=generate_narration
                )
                logger.info("Flash 请求 %d 完成: success=%s", i+1, result.success)
                results.append(result)
            except Exception as e:
                logger.error("Flash 请求 %d 异常: %s", i+1, e, exc_info=True)
                results.append(FlashResponse(
                    success=False,
                    operation=request.operation,
                    error=str(e),
                ))

        return results

    def _generate_default_flash_requests(
        self,
        intent: "ParsedIntent",
        context: Dict[str, Any] = None,
    ) -> List[FlashRequest]:
        """根据意图类型生成默认的 Flash 请求"""
        from app.models.admin_protocol import ParsedIntent

        if not isinstance(intent, ParsedIntent):
            return []

        intent_type = intent.intent_type

        if intent_type == IntentType.NAVIGATION and intent.target:
            return [FlashRequest(
                operation=FlashOperation.NAVIGATE,
                parameters={"destination": intent.target},
            )]

        if intent_type == IntentType.ENTER_SUB_LOCATION and intent.target:
            # 尝试将中文名称映射到 sub_location_id（同步快速匹配）
            sub_loc_id = intent.target
            sub_locations = (context or {}).get("sub_locations", [])
            target_lower = intent.target.lower()
            for sub_loc in sub_locations:
                if isinstance(sub_loc, dict):
                    loc_id = sub_loc.get("id", "")
                    loc_name = sub_loc.get("name", "")
                    if target_lower == loc_id.lower() or intent.target == loc_name:
                        sub_loc_id = loc_id
                        break
            return [FlashRequest(
                operation=FlashOperation.ENTER_SUBLOCATION,
                parameters={"sub_location_id": sub_loc_id},
            )]

        if intent_type == IntentType.NPC_INTERACTION and intent.target:
            return [FlashRequest(
                operation=FlashOperation.NPC_DIALOGUE,
                parameters={
                    "npc_id": intent.target,
                    "message": intent.raw_input,
                },
            )]

        if intent_type == IntentType.LEAVE_SUB_LOCATION:
            return []  # 由 v2 流程直接处理，不生成 Flash 请求

        if intent_type == IntentType.WAIT:
            minutes = intent.parameters.get("minutes", 30)
            return [FlashRequest(
                operation=FlashOperation.UPDATE_TIME,
                parameters={"minutes": minutes},
            )]

        if intent_type == IntentType.SYSTEM_COMMAND:
            target = (intent.target or "").lower()
            command = (intent.parameters.get("command") or "").lower()
            keyword = target or command

            # 任务/进度查询
            progress_keywords = {"任务", "进度", "quest", "progress", "任务列表", "quests", "目标"}
            if any(k in keyword for k in progress_keywords):
                return [FlashRequest(
                    operation=FlashOperation.GET_PROGRESS,
                    parameters={},
                )]

            # 状态/信息查询（聚合位置+时间+队伍）
            status_keywords = {
                "状态", "status", "player_status", "info",
                "信息", "队伍", "party", "位置", "where", "时间", "time",
            }
            if any(k in keyword for k in status_keywords):
                return [FlashRequest(
                    operation=FlashOperation.GET_STATUS,
                    parameters={},
                )]

            # 未识别的 system_command，默认走 GET_STATUS
            return [FlashRequest(
                operation=FlashOperation.GET_STATUS,
                parameters={},
            )]

        # 其他类型不需要默认请求
        return []

    async def _get_available_actions(
        self,
        world_id: str,
        session_id: str,
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """获取当前可用操作"""
        actions = []
        hotkey = 1

        # 从位置信息获取可用操作
        location = context.get("location") or {}

        # 子地点选项（优先显示）
        for sub in location.get("sub_locations", []):
            if isinstance(sub, dict):
                sub_id = sub.get("id", sub.get("name", "unknown"))
                sub_name = sub.get("name", sub.get("id", "未知"))
                actions.append({
                    "action_id": f"enter_{sub_id}",
                    "category": "movement",
                    "display_name": f"进入 {sub_name}",
                    "description": sub.get("description", "")[:50],
                    "hotkey": str(hotkey) if hotkey <= 9 else None,
                })
                hotkey += 1

        # 导航选项
        for dest in location.get("available_destinations", []):
            if isinstance(dest, dict):
                dest_id = dest.get("id", dest.get("name", "unknown"))
                dest_name = dest.get("name", dest.get("id", "未知"))
                actions.append({
                    "action_id": f"go_{dest_id}",
                    "category": "movement",
                    "display_name": f"前往 {dest_name}",
                    "description": dest.get("description", ""),
                    "hotkey": str(hotkey) if hotkey <= 9 else None,
                })
                hotkey += 1
            else:
                actions.append({
                    "action_id": f"go_{dest}",
                    "category": "movement",
                    "display_name": f"前往 {dest}",
                    "description": "",
                    "hotkey": str(hotkey) if hotkey <= 9 else None,
                })
                hotkey += 1

        # NPC 交互选项
        for npc_id in location.get("npcs_present", []):
            actions.append({
                "action_id": f"talk_{npc_id}",
                "category": "interaction",
                "display_name": f"与 {npc_id} 交谈",
                "description": "开始对话",
                "hotkey": str(hotkey) if hotkey <= 9 else None,
            })
            hotkey += 1

        # 观察选项
        actions.append({
            "action_id": "look_around",
            "category": "observation",
            "display_name": "观察周围",
            "description": "仔细观察当前环境",
            "hotkey": "0",
        })

        return actions

    async def execute_action(
        self,
        world_id: str,
        session_id: str,
        action_id: str,
    ) -> CoordinatorResponse:
        """
        直接执行操作（复用 v2 流程）。

        将 action_id 转换为自然语言后调用 process_player_input_v2，
        保证按钮操作与自然语言输入有一致的体验。

        Args:
            world_id: 世界ID
            session_id: 会话ID
            action_id: 操作ID (如 "enter_tavern", "go_forest", "talk_npc_id")

        Returns:
            CoordinatorResponse: 响应
        """
        player_input = self._action_id_to_input(action_id)
        return await self.process_player_input_v2(world_id, session_id, player_input)

    @staticmethod
    def _action_id_to_input(action_id: str) -> str:
        """将 action_id 转换为自然语言输入。"""
        parts = action_id.split("_", 1)
        action_type = parts[0] if parts else ""
        target = parts[1] if len(parts) > 1 else ""

        if action_type == "enter" and target:
            return f"进入{target}"
        if action_type == "go" and target:
            return f"前往{target}"
        if action_type == "talk" and target:
            return f"与{target}交谈"
        if action_id == "look_around":
            return "观察周围"

        return action_id.replace("_", " ")

    async def enter_scene(
        self,
        world_id: str,
        session_id: str,
        scene: SceneState,
        generate_description: bool = True,
    ) -> Dict[str, Any]:
        await self._session_store.set_scene(world_id, session_id, scene)
        description = scene.description or ""
        if generate_description and not description:
            context = await self._build_context(world_id, session_id)
            history = self.session_history_manager.get_or_create(world_id, session_id)
            conversation_history = history.get_recent_history(max_tokens=settings.session_history_max_tokens)
            if conversation_history:
                context["conversation_history"] = conversation_history
            summary = f"进入场景：{scene.location or scene.scene_id}"
            description = await self.flash_cpu.generate_gm_narration(
                player_input=summary,
                execution_summary=summary,
                context=context,
            )
        return {
            "scene": scene,
            "description": description,
            "npc_memories": {},
        }

    async def start_dialogue(self, world_id: str, session_id: str, npc_id: str) -> Dict[str, Any]:
        delta = self.flash_cpu._build_state_delta("dialogue_start", {"active_dialogue_npc": npc_id})
        await self.flash_cpu._apply_delta(world_id, session_id, delta)
        request = FlashRequest(
            operation=FlashOperation.NPC_DIALOGUE,
            parameters={"npc_id": npc_id, "message": "你好"},
        )
        result = await self.flash_cpu.execute_request(world_id, session_id, request)
        response_text = result.result.get("response") if isinstance(result.result, dict) else ""
        return {
            "type": "dialogue",
            "response": response_text or "……",
            "speaker": npc_id,
            "npc_id": npc_id,
        }

    async def end_dialogue(self, world_id: str, session_id: str) -> Dict[str, Any]:
        delta = self.flash_cpu._build_state_delta("dialogue_end", {"active_dialogue_npc": None})
        await self.flash_cpu._apply_delta(world_id, session_id, delta)
        return {"type": "system", "response": "结束对话。", "speaker": "系统"}

    async def trigger_combat(
        self,
        world_id: str,
        session_id: str,
        enemies: list,
        player_state: dict,
        combat_description: str = "",
        environment: Optional[dict] = None,
    ) -> Dict[str, Any]:
        payload = await self.flash_cpu.call_combat_tool(
            "start_combat_session",
            {
                "world_id": world_id,
                "session_id": session_id,
                "enemies": enemies,
                "player_state": player_state,
                "environment": environment,
                "combat_context": CombatStartRequest(player_state=player_state, enemies=enemies).combat_context.model_dump(),
            },
        )
        if payload.get("error"):
            return {"type": "error", "response": payload["error"]}

        combat_id = payload.get("combat_id", "")
        await self.flash_cpu._apply_delta(world_id, session_id, self.flash_cpu._build_state_delta("start_combat", {"combat_id": combat_id}))
        actions_payload = await self.flash_cpu.call_combat_tool("get_available_actions", {"combat_id": combat_id})
        actions = actions_payload.get("actions", [])
        narration = combat_description or "战斗开始！"
        return {
            "type": "combat",
            "phase": "start",
            "combat_id": combat_id,
            "narration": narration,
            "combat_state": payload.get("combat_state", {}),
            "available_actions": actions,
        }

    async def execute_combat_action(self, world_id: str, session_id: str, action_id: str) -> Dict[str, Any]:
        session_state = await self._session_store.get_session(world_id, session_id)
        if not session_state or not session_state.active_combat_id:
            return {"type": "error", "response": "没有活跃的战斗"}

        combat_id = session_state.active_combat_id
        payload = await self.flash_cpu.call_combat_tool(
            "execute_action",
            {"combat_id": combat_id, "action_id": action_id},
        )
        if payload.get("error"):
            return {"type": "error", "response": payload["error"]}

        combat_state = payload.get("combat_state", {})
        if combat_state.get("is_ended"):
            await self.resolve_combat(
                world_id,
                session_id,
                CombatResolveRequest(combat_id=combat_id, use_engine=True, dispatch=True),
            )
            await self.flash_cpu._apply_delta(world_id, session_id, self.flash_cpu._build_state_delta("end_combat", {"combat_id": None}))
            return {
                "type": "combat",
                "phase": "end",
                "result": payload.get("final_result"),
                "narration": payload.get("final_result", {}).get("summary", "战斗结束。"),
            }

        actions_payload = await self.flash_cpu.call_combat_tool("get_available_actions", {"combat_id": combat_id})
        return {
            "type": "combat",
            "phase": "action",
            "action_result": payload.get("action_result"),
            "narration": payload.get("action_result", {}).get("display_text", ""),
            "available_actions": actions_payload.get("actions", []),
        }

    async def advance_day(self, world_id: str, session_id: str) -> Dict[str, Any]:
        return await self._world_runtime.advance_day(world_id, session_id)

    async def start_session(
        self,
        world_id: str,
        session_id: Optional[str] = None,
        participants: Optional[list] = None,
        known_characters: Optional[list] = None,
        character_locations: Optional[dict] = None,
        starting_location: Optional[str] = None,
        starting_time: Optional[dict] = None,
    ):
        return await self._world_runtime.start_session(
            world_id=world_id,
            session_id=session_id,
            participants=participants,
            known_characters=known_characters,
            character_locations=character_locations,
            starting_location=starting_location,
            starting_time=starting_time,
        )

    def get_context(self, world_id: str, session_id: str):
        # Prefer in-memory admin state
        state = self._state_manager._states.get(f"{world_id}:{session_id}")
        if state:
            known_chars = state.metadata.get("known_characters", []) if state.metadata else []
            return self.AdminContextView(
                world_id=world_id,
                session_id=session_id,
                phase=GamePhase.IDLE,
                game_day=state.game_time.day,
                current_scene=None,
                current_npc=None,
                known_characters=known_chars,
            )
        return None

    async def get_context_async(self, world_id: str, session_id: str):
        state = await self._world_runtime.get_state(world_id, session_id)
        if state:
            known_chars = state.metadata.get("known_characters", []) if state.metadata else []
            return self.AdminContextView(
                world_id=world_id,
                session_id=session_id,
                phase=GamePhase.IDLE,
                game_day=state.game_time.day,
                current_scene=None,
                current_npc=state.active_dialogue_npc,
                known_characters=known_chars,
            )
        return None

    async def get_current_location(self, world_id: str, session_id: str):
        return await self._world_runtime.get_current_location(world_id, session_id)

    async def navigate(
        self,
        world_id: str,
        session_id: str,
        destination: Optional[str] = None,
        direction: Optional[str] = None,
        generate_narration: bool = False,
    ):
        return await self._world_runtime.navigate(
            world_id,
            session_id,
            destination=destination,
            direction=direction,
            generate_narration=generate_narration,
        )

    async def get_game_time(self, world_id: str, session_id: str):
        return await self._world_runtime.get_game_time(world_id, session_id)

    async def advance_time(self, world_id: str, session_id: str, minutes: int):
        return await self._world_runtime.advance_time(world_id, session_id, minutes)

    async def enter_sub_location(self, world_id: str, session_id: str, sub_location_id: str):
        return await self._world_runtime.enter_sub_location(world_id, session_id, sub_location_id)

    async def leave_sub_location(self, world_id: str, session_id: str):
        return await self._world_runtime.leave_sub_location(world_id, session_id)

    async def ingest_event(self, world_id: str, request):
        return await self.event_service.ingest_event(world_id, request)

    async def ingest_event_natural(self, world_id: str, request):
        return await self.event_service.ingest_event_natural(world_id, request)

    # ==================== 队伍管理 API ====================

    async def create_party(
        self,
        world_id: str,
        session_id: str,
        leader_id: str = "player",
    ) -> Dict[str, Any]:
        """创建队伍"""
        party = await self.party_service.create_party(world_id, session_id, leader_id)

        # 更新状态
        state = await self._state_manager.get_state(world_id, session_id)
        if state:
            state.party_id = party.party_id
            await self._state_manager.set_state(world_id, session_id, state)

        return {
            "party_id": party.party_id,
            "leader_id": party.leader_id,
            "members": [],
        }

    async def add_teammate(
        self,
        world_id: str,
        session_id: str,
        character_id: str,
        name: str,
        role: str = "support",
        personality: str = "",
        response_tendency: float = 0.5,
    ) -> Dict[str, Any]:
        """添加队友"""
        from app.models.party import TeammateRole

        # 确保队伍存在
        party = await self.party_service.get_or_create_party(world_id, session_id)

        try:
            teammate_role = TeammateRole(role)
        except ValueError:
            teammate_role = TeammateRole.SUPPORT

        member = await self.party_service.add_member(
            world_id=world_id,
            session_id=session_id,
            character_id=character_id,
            name=name,
            role=teammate_role,
            personality=personality,
            response_tendency=response_tendency,
        )

        if member:
            return {
                "success": True,
                "character_id": member.character_id,
                "name": member.name,
                "role": member.role.value,
            }
        return {
            "success": False,
            "error": "队伍已满或添加失败",
        }

    async def remove_teammate(
        self,
        world_id: str,
        session_id: str,
        character_id: str,
    ) -> Dict[str, Any]:
        """移除队友"""
        success = await self.party_service.remove_member(
            world_id, session_id, character_id
        )
        return {
            "success": success,
            "character_id": character_id,
        }

    async def get_party_info(
        self,
        world_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """获取队伍信息"""
        party = await self.party_service.get_party(world_id, session_id)
        if not party:
            return {
                "has_party": False,
                "party_id": None,
                "members": [],
            }

        return {
            "has_party": True,
            "party_id": party.party_id,
            "leader_id": party.leader_id,
            "current_location": party.current_location,
            "members": [
                {
                    "character_id": m.character_id,
                    "name": m.name,
                    "role": m.role.value,
                    "personality": m.personality,
                    "is_active": m.is_active,
                    "current_mood": m.current_mood,
                    "response_tendency": m.response_tendency,
                }
                for m in party.members
            ],
        }

    async def load_predefined_teammates(
        self,
        world_id: str,
        session_id: str,
        teammate_configs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """加载预定义队友"""
        # 确保队伍存在
        await self.party_service.get_or_create_party(world_id, session_id)

        members = await self.party_service.load_predefined_teammates(
            world_id, session_id, teammate_configs
        )

        return {
            "loaded_count": len(members),
            "members": [
                {
                    "character_id": m.character_id,
                    "name": m.name,
                    "role": m.role.value,
                }
                for m in members
            ],
        }

    # ==================== 好感度系统 ====================

    async def update_disposition(
        self,
        world_id: str,
        character_id: str,
        target_id: str,
        deltas: Dict[str, int],
        reason: str = "",
        game_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        """更新角色好感度并同步 approves 边权重到角色图谱。"""
        result = await self.graph_store.update_disposition(
            world_id=world_id,
            character_id=character_id,
            target_id=target_id,
            deltas=deltas,
            reason=reason,
            game_day=game_day,
        )

        # 同步 approves 边到角色图谱
        if "approval" in deltas:
            from app.models.graph import MemoryEdge

            approval = result.get("approval", 0)
            weight = (approval + 100) / 200.0
            weight = max(0.0, min(1.0, weight))

            char_node_id = f"character_{character_id}" if not character_id.startswith("character_") else character_id
            target_node_id = f"character_{target_id}" if not target_id.startswith("character_") else target_id
            edge_id = f"disposition_{character_id}_{target_id}_approves"

            edge = MemoryEdge(
                id=edge_id,
                source=char_node_id,
                target=target_node_id,
                relation="approves",
                weight=weight,
                properties={
                    "created_by": "disposition",
                    "approval": approval,
                    "trust": result.get("trust", 0),
                    "game_day": game_day,
                },
            )
            char_scope = GraphScope(scope_type="character", character_id=character_id)
            await self.graph_store.upsert_edge_v2(world_id, char_scope, edge)

        return result

    async def update_disposition_after_combat(
        self,
        world_id: str,
        session_id: str,
        combat_result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """战斗结束后根据结果更新队友好感度。

        规则：
        - 玩家保护了队友 → approval +5, trust +3
        - 玩家治疗了队友 → approval +3, trust +2
        - 队友被击败（玩家未保护）→ approval -3
        - 战斗胜利 → 所有参与队友 approval +2
        - 战斗失败 → 所有参与队友 approval -1
        """
        party = await self.party_service.get_party(world_id, session_id)
        if not party:
            return []

        state = await self._state_manager.get_state(world_id, session_id)
        game_day = state.game_time.day if state else 1

        updates = []
        is_victory = combat_result.get("result") == "victory"

        for member in party.get_active_members():
            char_id = member.character_id
            deltas: Dict[str, int] = {}
            reasons: List[str] = []

            # 战斗结果基础好感度
            if is_victory:
                deltas["approval"] = deltas.get("approval", 0) + 2
                reasons.append("combat_victory")
            else:
                deltas["approval"] = deltas.get("approval", 0) - 1
                reasons.append("combat_defeat")

            # 保护/治疗行为
            protected = combat_result.get("protected_allies", [])
            healed = combat_result.get("healed_allies", [])
            if char_id in protected:
                deltas["approval"] = deltas.get("approval", 0) + 5
                deltas["trust"] = deltas.get("trust", 0) + 3
                reasons.append("protected")
            if char_id in healed:
                deltas["approval"] = deltas.get("approval", 0) + 3
                deltas["trust"] = deltas.get("trust", 0) + 2
                reasons.append("healed")

            if deltas:
                result = await self.update_disposition(
                    world_id=world_id,
                    character_id=char_id,
                    target_id="player",
                    deltas=deltas,
                    reason=", ".join(reasons),
                    game_day=game_day,
                )
                updates.append({
                    "character_id": char_id,
                    "deltas": deltas,
                    "reason": ", ".join(reasons),
                    "new_approval": result.get("approval"),
                })

        return updates
