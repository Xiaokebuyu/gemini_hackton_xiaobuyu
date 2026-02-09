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
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Any, List, Optional, Dict, Tuple

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
    ParsedIntent,
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
from app.services.character_store import CharacterStore
from app.services.character_service import CharacterService
from app.services.mcp_client_pool import MCPServiceUnavailableError
from app.services.admin.recall_orchestrator import RecallOrchestrator
from app.services.admin.agentic_enforcement import (
    AgenticToolExecutionRequiredError,
    evaluate_agentic_tool_usage,
)


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
        # 补充注入 character_service / character_store 到 flash_cpu
        self.flash_cpu.character_service = None  # placeholder, set after character_service created
        self.flash_cpu.character_store = None
        self.teammate_response_service = teammate_response_service or TeammateResponseService(
            instance_manager=self.instance_manager,
        )
        self.visibility_manager = visibility_manager or TeammateVisibilityManager()

        # 角色系统
        self.character_store = CharacterStore()
        self.character_service = CharacterService(store=self.character_store)
        # 补充注入到 flash_cpu
        self.flash_cpu.character_service = self.character_service
        self.flash_cpu.character_store = self.character_store

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
        self._character_ids_cache: Dict[str, set] = {}
        self._area_chapter_cache: Dict[str, Dict[str, str]] = {}

        self.recall_orchestrator = RecallOrchestrator(
            graph_store=self.graph_store,
            get_character_id_set=self._get_character_id_set,
            get_area_chapter_map=self._get_area_chapter_map,
        )
        self.flash_cpu.recall_orchestrator = self.recall_orchestrator

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
        """获取会话聊天历史（优先内存实时数据，回退 Firestore）。"""
        in_memory = self.session_history_manager.get(world_id, session_id)
        if in_memory:
            live_messages = in_memory.get_recent_messages_for_api(limit)
            if live_messages:
                return live_messages

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

            # 角色创建状态：以 character_store 为准（player_character 存在于 session 顶层字段）
            has_character = False
            try:
                has_character = bool(
                    await self.character_store.get_character(world_id, session.session_id)
                )
            except Exception:
                # 回退兼容：历史数据可能写在 metadata 中
                has_character = bool(metadata.get("player_character"))

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
                    "needs_character_creation": not has_character,
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
            "start_combat_v3",
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
            "resolve_combat_session_v3",
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

    def _infer_agentic_intent_type(
        self,
        player_input: str,
        flash_results: List[FlashResponse],
    ) -> IntentType:
        """Infer intent type from executed operations in agentic mode."""
        text = (player_input or "").strip()
        lowered = text.lower()
        if text.startswith("/"):
            return IntentType.SYSTEM_COMMAND

        # Prefer player-input semantics over read-tool outcomes to avoid
        # circular classification (e.g., model calls get_status -> intent becomes system).
        if any(k in text for k in ("组队", "队友", "加入队伍", "入队", "离队", "解散队伍")):
            return IntentType.TEAM_INTERACTION
        if any(k in text for k in ("对话", "交谈", "询问", "聊天", "聊聊", "对她说", "对他说")):
            return IntentType.NPC_INTERACTION
        if any(k in lowered for k in ("talk to", "speak to", "npc", "dialogue")):
            return IntentType.NPC_INTERACTION
        if any(k in text for k in ("攻击", "战斗", "开打", "交战", "冲锋")):
            return IntentType.START_COMBAT
        if any(k in text for k in ("进入", "前往", "移动", "赶往", "去")):
            return IntentType.NAVIGATION
        if any(k in text for k in ("等待", "休息", "睡", "快进", "过一会")):
            return IntentType.WAIT
        if any(k in text for k in ("状态", "进度", "任务", "目标", "地点", "时间", "当前位置", "背包", "血量")):
            return IntentType.SYSTEM_COMMAND

        for result in flash_results:
            if not result.success:
                continue
            if result.operation == FlashOperation.NAVIGATE:
                return IntentType.NAVIGATION
            if result.operation == FlashOperation.ENTER_SUBLOCATION:
                return IntentType.ENTER_SUB_LOCATION
            if result.operation == FlashOperation.NPC_DIALOGUE:
                return IntentType.NPC_INTERACTION
            if result.operation == FlashOperation.START_COMBAT:
                return IntentType.START_COMBAT
            if result.operation == FlashOperation.UPDATE_TIME:
                return IntentType.WAIT

        return IntentType.ROLEPLAY

    @staticmethod
    def _ensure_fixed_world(world_id: str) -> None:
        expected = settings.fixed_world_id
        if world_id != expected:
            raise ValueError(
                f"unsupported world_id='{world_id}', this environment only supports '{expected}'"
            )

    # ==================== Agentic 架构 (v3) ====================

    async def process_player_input_v3(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: Optional[str] = None,
    ) -> CoordinatorResponse:
        """
        v3 Agentic 流程。

        A) 预处理：上下文收集 + StoryDirector pre 机械评估
        B) 单次 agentic 会话：模型自主工具调用并生成叙述
        C) 后处理：StoryDirector post + 队友响应 + 事件分发 + 历史记录
        """
        self._ensure_fixed_world(world_id)
        if not settings.use_agentic_mode:
            return await self.process_player_input_v2(
                world_id,
                session_id,
                player_input,
                is_private=is_private,
                private_target=private_target,
            )
        await self.narrative_service.load_narrative_data(world_id, force_reload=True)

        pc = await self.character_store.get_character(world_id, session_id)
        if not pc:
            raise ValueError("请先创建角色后再开始冒险。")

        location_check = await self._world_runtime.get_current_location(world_id, session_id)
        if location_check.get("error"):
            raise ValueError(location_check["error"])

        party = await self.party_service.get_party(world_id, session_id)
        history = self.session_history_manager.get_or_create(world_id, session_id)
        base_context = await self._build_context(
            world_id,
            session_id,
            player_character=pc,
        )
        conversation_history = history.get_recent_history(max_tokens=settings.session_history_max_tokens)
        if conversation_history:
            base_context["conversation_history"] = conversation_history
        if is_private:
            base_context["is_private"] = True
            base_context["private_target"] = private_target

        progress = await self.narrative_service.get_progress(world_id, session_id)
        active_story_chapters = self._resolve_story_chapters(world_id, progress)
        current_chapter = active_story_chapters[0] if active_story_chapters else None
        game_ctx = self._build_game_context(base_context, progress, session_id, player_input)

        if len(active_story_chapters) > 1:
            pre_directive = self.story_director.pre_evaluate_multi(game_ctx, active_story_chapters)
        else:
            pre_directive = self.story_director.pre_evaluate(game_ctx, current_chapter)

        pre_side_effects: List[Dict[str, Any]] = []
        for event in pre_directive.auto_fired_events:
            await self.narrative_service.trigger_event(world_id, session_id, event.id, skip_advance=True)
            logger.info("[v3] StoryDirector pre-fire: %s", event.id)
            if event.side_effects:
                pre_side_effects.extend(event.side_effects)
        if pre_side_effects:
            await self._execute_side_effects(world_id, session_id, pre_side_effects)

        base_context["story_directives"] = pre_directive.narrative_injections
        base_context["pending_flash_conditions"] = [
            {"id": c.condition_id, "prompt": c.condition_prompt, "event_id": c.event_id}
            for c in pre_directive.pending_flash_conditions
        ]
        base_context["story_pacing"] = {
            "action": getattr(pre_directive, "pacing_action", None),
            "detail": getattr(pre_directive, "pacing_detail", ""),
        }

        # v3 增强：预热扩散图谱结果，充分利用多图谱召回与队友记忆组件。
        prefill_state = await self._state_manager.get_state(world_id, session_id)
        chapter_id_prefill = getattr(prefill_state, "chapter_id", None) if prefill_state else None
        area_id_prefill = getattr(prefill_state, "area_id", None) if prefill_state else None
        prefill_player_seeds = self._build_effective_seeds([], base_context, character_id="player")
        player_memory_task = asyncio.create_task(
            self._recall_memory(
                world_id=world_id,
                character_id="player",
                seed_nodes=prefill_player_seeds,
                intent_type="roleplay",
                chapter_id=chapter_id_prefill,
                area_id=area_id_prefill,
            )
        )
        teammate_prefill_tasks: Dict[str, asyncio.Task] = {}
        if party and party.get_active_members():
            for member in party.get_active_members():
                teammate_seeds = self._build_effective_seeds(
                    [],
                    base_context,
                    character_id=member.character_id,
                )
                teammate_prefill_tasks[member.character_id] = asyncio.create_task(
                    self._recall_memory(
                        world_id=world_id,
                        character_id=member.character_id,
                        seed_nodes=teammate_seeds,
                        intent_type="roleplay",
                        chapter_id=chapter_id_prefill,
                        area_id=area_id_prefill,
                    )
                )

        player_memory_result = None
        try:
            player_memory_result = await player_memory_task
            if player_memory_result and getattr(player_memory_result, "activated_nodes", None):
                base_context["memory_summary"] = self._summarize_memory(player_memory_result)
                if hasattr(player_memory_result, "model_dump"):
                    base_context["recalled_memory"] = player_memory_result.model_dump()
        except Exception:
            pending_teammate_tasks = [
                task for task in teammate_prefill_tasks.values()
                if task is not None and not task.done()
            ]
            for task in pending_teammate_tasks:
                task.cancel()
            if pending_teammate_tasks:
                await asyncio.gather(*pending_teammate_tasks, return_exceptions=True)
            raise

        logger.info("[v3] 步骤B: agentic 会话")
        try:
            agentic_result = await self.flash_cpu.agentic_process(
                world_id=world_id,
                session_id=session_id,
                player_input=player_input,
                context=base_context,
            )
        except Exception:
            pending_teammate_tasks = [
                task for task in teammate_prefill_tasks.values()
                if task is not None and not task.done()
            ]
            for task in pending_teammate_tasks:
                task.cancel()
            if pending_teammate_tasks:
                await asyncio.gather(*pending_teammate_tasks, return_exceptions=True)
            raise
        flash_results = agentic_result.flash_results
        gm_narration = agentic_result.narration
        intent_type = self._infer_agentic_intent_type(player_input, flash_results)
        skip_teammate = intent_type == IntentType.SYSTEM_COMMAND
        logger.info("[v3] agentic 完成: tools=%d ops=%d", len(agentic_result.tool_calls), len(flash_results))
        agentic_enforcement = evaluate_agentic_tool_usage(
            player_input=player_input,
            inferred_intent=intent_type,
            tool_calls=agentic_result.tool_calls,
        )
        repair_summary: Dict[str, Any] = {
            "attempted": False,
            "status": "not_needed" if agentic_enforcement.passed else "pending",
            "requested_tools": list(agentic_enforcement.repair_tool_names or []),
            "executed_tool_calls": 0,
            "executed_tools": [],
        }
        if settings.admin_agentic_strict_tools and not agentic_enforcement.passed:
            if agentic_enforcement.repair_allowed and agentic_enforcement.repair_tool_names:
                repair_summary["attempted"] = True
                repair_result = await self.flash_cpu.run_required_tool_repair(
                    world_id=world_id,
                    session_id=session_id,
                    player_input=player_input,
                    context=base_context,
                    missing_requirements=agentic_enforcement.missing_requirements,
                    repair_tool_names=agentic_enforcement.repair_tool_names,
                    enforcement_reason=agentic_enforcement.reason,
                )
                if repair_result.tool_calls:
                    agentic_result.tool_calls.extend(repair_result.tool_calls)
                if repair_result.flash_results:
                    agentic_result.flash_results.extend(repair_result.flash_results)
                if repair_result.story_condition_results:
                    agentic_result.story_condition_results.update(repair_result.story_condition_results)
                if repair_result.image_data:
                    agentic_result.image_data = repair_result.image_data
                repair_narration = (repair_result.narration or "").strip()
                if repair_narration:
                    gm_narration = repair_narration
                    repair_summary["narration_replaced"] = True
                else:
                    repair_summary["narration_replaced"] = False
                repair_summary["narration_length"] = len(repair_narration)

                repair_summary["executed_tool_calls"] = len(repair_result.tool_calls or [])
                repair_summary["executed_tools"] = [
                    call.name for call in (repair_result.tool_calls or []) if getattr(call, "name", "")
                ]
                repair_summary["tool_call_results"] = [
                    {
                        "name": str(getattr(call, "name", "") or ""),
                        "success": bool(getattr(call, "success", False)),
                        "error": str(getattr(call, "error", "") or ""),
                    }
                    for call in (repair_result.tool_calls or [])
                ]
                repair_summary["repair_usage"] = (
                    dict(repair_result.usage)
                    if isinstance(repair_result.usage, dict)
                    else {}
                )
                repair_summary["finalize_status"] = str(
                    repair_summary["repair_usage"].get("finalize_status", "")
                ).strip()
                if repair_result.finish_reason:
                    repair_summary["finalize_finish_reason"] = str(repair_result.finish_reason)
                agentic_enforcement = evaluate_agentic_tool_usage(
                    player_input=player_input,
                    inferred_intent=intent_type,
                    tool_calls=agentic_result.tool_calls,
                )
                repair_summary["status"] = "repaired" if agentic_enforcement.passed else "failed"
                repair_summary["post_check_passed"] = bool(agentic_enforcement.passed)
            else:
                repair_summary["status"] = "repair_not_allowed"

            if not agentic_enforcement.passed:
                pending_teammate_tasks = [
                    task for task in teammate_prefill_tasks.values()
                    if task is not None and not task.done()
                ]
                for task in pending_teammate_tasks:
                    task.cancel()
                if pending_teammate_tasks:
                    await asyncio.gather(*pending_teammate_tasks, return_exceptions=True)
                raise AgenticToolExecutionRequiredError(
                    reason=agentic_enforcement.reason,
                    expected_intent=agentic_enforcement.expected_intent,
                    missing_requirements=agentic_enforcement.missing_requirements,
                    called_tools=agentic_enforcement.called_tools,
                    repair_attempted=bool(repair_summary.get("attempted")),
                    repair_tool_names=agentic_enforcement.repair_tool_names,
                    repair_summary=repair_summary,
                )
        elif not agentic_enforcement.passed:
            # Non-strict mode fallback: preserve old behavior.
            logger.warning(
                "[v3] enforcement soft-fail: %s, missing=%s called=%s",
                agentic_enforcement.reason,
                agentic_enforcement.missing_requirements,
                agentic_enforcement.called_tools,
            )
            repair_summary["status"] = "soft_failed"

        agentic_trace = self._build_agentic_trace_payload(agentic_result)
        enforcement_metadata = agentic_enforcement.to_metadata()
        enforcement_metadata["strict_mode"] = bool(settings.admin_agentic_strict_tools)
        enforcement_metadata["repair"] = repair_summary
        agentic_trace["enforcement"] = enforcement_metadata

        context = await self._assemble_context(
            base_context=base_context,
            memory_result=player_memory_result,
            flash_results=flash_results,
            world_id=world_id,
            session_id=session_id,
        )
        if agentic_result.image_data:
            context["image_data"] = agentic_result.image_data
        if is_private:
            context["is_private"] = True
            context["private_target"] = private_target

        state_snapshot = await self._state_manager.get_state(world_id, session_id)
        current_chapter_id = getattr(state_snapshot, "chapter_id", None) if state_snapshot else None
        current_area_id = getattr(state_snapshot, "area_id", None) if state_snapshot else None

        agentic_memory_seeds: List[str] = []
        for tool_call in agentic_result.tool_calls:
            if tool_call.name != "recall_memory":
                continue
            seeds_raw = tool_call.args.get("seeds")
            if isinstance(seeds_raw, list):
                for seed in seeds_raw:
                    text = str(seed).strip()
                    if text:
                        agentic_memory_seeds.append(text)
        if agentic_memory_seeds:
            agentic_memory_seeds = list(dict.fromkeys(agentic_memory_seeds))

        # v3 对齐项：恢复队友记忆召回与上下文编排注入。
        agentic_intent = ParsedIntent(
            intent_type=intent_type,
            confidence=1.0,
            raw_input=player_input,
            interpretation="agentic_inferred",
            is_private=is_private,
            private_target=private_target,
        )
        player_curated: Optional[Dict[str, Any]] = None
        t_packages: Dict[str, Dict[str, Any]] = {}
        t_summaries: Dict[str, str] = {}
        try:
            player_curated, t_packages, t_summaries = await self._run_curation_pipeline(
                world_id=world_id,
                player_input=player_input,
                intent=agentic_intent,
                memory_result=player_memory_result,
                flash_results=flash_results,
                context=dict(context),
                party=party,
                skip_teammate=skip_teammate,
                analysis_memory_seeds=agentic_memory_seeds,
                base_context=base_context,
                chapter_id=current_chapter_id,
                area_id=current_area_id,
                teammate_recall_tasks=teammate_prefill_tasks,
            )
            if player_curated:
                merged_package = {}
                if isinstance(context.get("context_package"), dict):
                    merged_package.update(context["context_package"])
                merged_package.update(player_curated)
                context["context_package"] = merged_package
                if isinstance(player_curated.get("story_progression"), dict):
                    context["story_progression"] = player_curated["story_progression"]
            if t_packages:
                context["teammate_context_packages"] = t_packages
            if t_summaries:
                context["teammate_memory_summaries"] = t_summaries
        finally:
            pending_teammate_tasks = [
                task for task in teammate_prefill_tasks.values()
                if task is not None and not task.done()
            ]
            for task in pending_teammate_tasks:
                task.cancel()
            if pending_teammate_tasks:
                await asyncio.gather(*pending_teammate_tasks, return_exceptions=True)

        flash_condition_results = dict(agentic_result.story_condition_results or {})
        story_prog = context.get("story_progression")
        if not isinstance(story_prog, dict):
            story_prog = (context.get("context_package") or {}).get("story_progression", {})
        if isinstance(story_prog, dict):
            for eval_item in story_prog.get("condition_evaluations", []):
                if not isinstance(eval_item, dict):
                    continue
                cond_id = str(eval_item.get("id", "")).strip()
                if not cond_id or cond_id in flash_condition_results:
                    continue
                flash_condition_results[cond_id] = bool(eval_item.get("result", False))
        progress_needs_refresh = bool(pre_side_effects) or any(
            result.success and result.operation == FlashOperation.TRIGGER_NARRATIVE_EVENT
            for result in flash_results
        )
        if progress_needs_refresh:
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

        post_changed_party = False
        if final_directive.side_effects:
            await self._execute_side_effects(world_id, session_id, final_directive.side_effects)
            post_changed_party = any(
                effect.get("type") in ("add_teammate", "remove_teammate")
                for effect in final_directive.side_effects
            )

        flash_changed_party = any(
            result.success and result.operation in {
                FlashOperation.ADD_TEAMMATE,
                FlashOperation.REMOVE_TEAMMATE,
                FlashOperation.DISBAND_PARTY,
            }
            for result in flash_results
        )
        pre_changed_party = any(
            effect.get("type") in ("add_teammate", "remove_teammate")
            for effect in pre_side_effects
        ) if pre_side_effects else False
        if flash_changed_party or pre_changed_party or post_changed_party:
            party = await self.party_service.get_party(world_id, session_id)
            logger.info(
                "[v3] party 已刷新 (flash=%s, pre=%s, post=%s)",
                flash_changed_party, pre_changed_party, post_changed_party,
            )

        turn_story_events = list(pre_directive.auto_fired_events) + list(final_directive.fired_events)
        for event in final_directive.fired_events:
            if event.id not in progress.events_triggered:
                progress.events_triggered.append(event.id)
                logger.info("[v3] StoryDirector post-fire: %s", event.id)

        chapter_event_map: Dict[str, Any] = {}
        for chapter in active_story_chapters:
            for chapter_event in chapter.events:
                chapter_event_map[chapter_event.id] = chapter_event

        tool_trigger_event_ids: List[str] = []
        for call in agentic_result.tool_calls:
            if not call.success:
                continue
            if call.name != FlashOperation.TRIGGER_NARRATIVE_EVENT.value:
                continue
            event_id = str(call.args.get("event_id", "")).strip()
            if event_id:
                tool_trigger_event_ids.append(event_id)

        existing_event_ids = {event.id for event in turn_story_events}
        for event_id in dict.fromkeys(tool_trigger_event_ids):
            chapter_event = chapter_event_map.get(event_id)
            if chapter_event is None:
                logger.warning("[v3] agentic trigger_event ignored, unknown event_id=%s", event_id)
                continue
            if event_id in existing_event_ids:
                continue
            if event_id not in progress.events_triggered:
                progress.events_triggered.append(event_id)
            turn_story_events.append(chapter_event)
            existing_event_ids.add(event_id)
            logger.info("[v3] agentic trigger_event recorded: %s", event_id)

        all_narrative_directives = pre_directive.narrative_injections + final_directive.narrative_injections
        if all_narrative_directives:
            context["story_narrative_directives"] = all_narrative_directives

        if not final_directive.chapter_transition:
            reevaluated_transition = self._reevaluate_transition_after_progress(
                context=context,
                progress=progress,
                session_id=session_id,
                player_input=player_input,
                chapters=active_story_chapters,
                flash_condition_results=flash_condition_results,
                pre_auto_fired_ids=[e.id for e in pre_directive.auto_fired_events],
            )
            if reevaluated_transition:
                final_directive.chapter_transition = reevaluated_transition
                logger.info(
                    "[v3] StoryDirector transition re-evaluated: -> %s (%s)",
                    reevaluated_transition.target_chapter_id,
                    reevaluated_transition.transition_type,
                )

        progress.rounds_in_chapter += 1
        has_progress = bool(turn_story_events)
        progress.rounds_since_last_progress = 0 if has_progress else (progress.rounds_since_last_progress + 1)

        npc_interactions: Dict[str, int] = {}
        for call in agentic_result.tool_calls:
            if not call.success:
                continue
            if call.name not in (FlashOperation.NPC_DIALOGUE.value, "npc_dialogue"):
                continue
            npc_id = str(call.args.get("npc_id", "")).strip()
            if npc_id:
                npc_interactions[npc_id] = npc_interactions.get(npc_id, 0) + 1
        for npc_id, count in npc_interactions.items():
            progress.npc_interactions[npc_id] = progress.npc_interactions.get(npc_id, 0) + count

        for event in turn_story_events:
            progress.event_cooldowns[event.id] = progress.rounds_in_chapter

        await self.narrative_service.save_progress(world_id, session_id, progress)

        v3_transition_result: Optional[Dict[str, Any]] = None
        if final_directive.chapter_transition:
            trans = final_directive.chapter_transition
            logger.info(
                "[v3] StoryDirector 章节转换: → %s (%s)",
                trans.target_chapter_id,
                trans.transition_type,
            )
            if trans.narrative_hint:
                context.setdefault("story_narrative_directives", []).append(trans.narrative_hint)

            v3_transition_result = await self.narrative_service.transition_to_chapter(
                world_id=world_id,
                session_id=session_id,
                target_chapter_id=trans.target_chapter_id,
                transition_type=trans.transition_type,
            )
            state = await self._state_manager.get_state(world_id, session_id)
            if state and v3_transition_result.get("new_chapter"):
                state.chapter_id = v3_transition_result["new_chapter"]
                new_maps = v3_transition_result.get("new_maps_unlocked", [])
                if new_maps:
                    navigator = self._world_runtime._get_navigator_ready(world_id)
                    if navigator and new_maps[0] in navigator.maps:
                        state.area_id = new_maps[0]
                        state.player_location = new_maps[0]
                await self._state_manager.set_state(world_id, session_id, state)

        if v3_transition_result and v3_transition_result.get("new_chapter"):
            transition_text = await self._generate_chapter_transition(
                world_id,
                session_id,
                v3_transition_result.get("new_chapter"),
                v3_transition_result.get("new_maps_unlocked", []),
            )
            if transition_text:
                gm_narration += f"\n\n{transition_text}"

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

        await self._refresh_chapter_context(world_id, session_id, context)

        if any(result.success and result.operation == FlashOperation.NAVIGATE for result in flash_results) and party:
            new_location = context.get("location", {}).get("location_id")
            if new_location:
                await self.party_service.sync_locations(world_id, session_id, new_location)

        last_teammate_responses = history.get_last_teammate_responses()
        if last_teammate_responses:
            context["last_teammate_responses"] = last_teammate_responses

        execution_summary = self._build_execution_summary(flash_results)
        context["execution_summary"] = execution_summary
        context["gm_narration_full"] = gm_narration

        teammate_responses: List[Dict[str, Any]] = []
        teammate_debug = {"total": 0, "skipped": 0, "skip_reasons": []}
        if party and party.get_active_members() and not skip_teammate:
            teammate_result = await self.teammate_response_service.process_round(
                party=party,
                player_input=player_input,
                gm_response=execution_summary,
                context=context,
            )
            teammate_debug["total"] = len(teammate_result.responses)
            skip_reasons = set()
            for resp in teammate_result.responses:
                if resp.response:
                    teammate_responses.append({
                        "character_id": resp.character_id,
                        "name": resp.name,
                        "response": resp.response,
                        "reaction": resp.reaction,
                        "model_used": resp.model_used,
                        "thinking_level": resp.thinking_level,
                        "latency_ms": resp.latency_ms,
                    })
                else:
                    teammate_debug["skipped"] += 1
                    if resp.reaction:
                        skip_reasons.add(resp.reaction)
            teammate_debug["skip_reasons"] = sorted(skip_reasons)

        if party and party.share_events and not skip_teammate:
            await self._distribute_event_to_party(
                world_id=world_id,
                party=party,
                player_input=player_input,
                gm_response=gm_narration,
                context=context,
            )

        output_anomaly_meta = self._detect_output_anomalies(gm_narration)
        round_stats = history.record_round(
            player_input=player_input,
            gm_response=gm_narration,
            metadata={
                "intent_type": intent_type.value,
                "story_events": [event.id for event in turn_story_events],
                "transition": (
                    final_directive.chapter_transition.target_chapter_id
                    if final_directive.chapter_transition else ""
                ),
                "chapter_id": (
                    v3_transition_result.get("new_chapter")
                    if v3_transition_result and v3_transition_result.get("new_chapter")
                    else progress.current_chapter
                ),
                "visibility": "private" if is_private else "public",
                "private_target": private_target if is_private else None,
                **output_anomaly_meta,
            },
        )
        for teammate in teammate_responses:
            history.record_teammate_response(
                character_id=teammate["character_id"],
                name=teammate["name"],
                response=teammate["response"],
            )
        if round_stats["should_graphize"]:
            game_day = context.get("time", {}).get("day", 1)
            location_id = context.get("location", {}).get("location_id")
            asyncio.create_task(self._run_graphization(history, game_day, location_id))

        state_delta = self._merge_state_deltas(flash_results)
        available_actions = await self._get_available_actions(world_id, session_id, context)
        chapter_info_payload = self._build_chapter_response_payload(
            context=context,
            progress=progress,
            final_directive=final_directive,
        )
        story_director_meta = self._build_story_director_metadata(
            pre_directive=pre_directive,
            final_directive=final_directive,
            turn_story_events=turn_story_events,
        )

        return CoordinatorResponse(
            narration=gm_narration,
            speaker="GM",
            teammate_responses=teammate_responses,
            available_actions=available_actions,
            state_delta=state_delta,
            metadata={
                "intent_type": intent_type.value,
                "confidence": 1.0,
                "teammate_count": len(teammate_responses),
                "session_history": round_stats,
                "source": "flash_agentic_v3",
                "model": settings.admin_agentic_model,
                "story_director": story_director_meta,
                "teammate_debug": teammate_debug,
                "agentic_usage": agentic_result.usage,
                "agentic_finish_reason": agentic_result.finish_reason,
                "agentic_tool_calls": len(agentic_result.tool_calls),
                "agentic_trace": agentic_trace,
                "agentic_enforcement": {
                    **enforcement_metadata,
                },
                "agentic_mode": "strict_tools_v1",
                "agentic_config": {
                    "model": settings.admin_agentic_model,
                    "thinking_level": settings.admin_flash_thinking_level,
                    "max_remote_calls": settings.admin_agentic_max_remote_calls,
                    "tool_timeout_seconds": settings.admin_agentic_tool_timeout_seconds,
                    "cache_mode": "disabled",
                    "context_budget_chars": {
                        "history": settings.admin_agentic_history_max_chars,
                        "background": settings.admin_agentic_background_max_chars,
                        "memory_summary": settings.admin_agentic_memory_max_chars,
                    },
                },
                "teammate_signal_mode": "execution_summary+gm_narration",
                "event_accounting_mode": "tool_call_first",
                "player_curation_applied": bool(player_curated),
                "is_private": is_private,
                "private_target": private_target if is_private else None,
                **output_anomaly_meta,
            },
            story_events=[event.id for event in turn_story_events] if turn_story_events else [],
            pacing_action=final_directive.pacing_action if final_directive else None,
            chapter_info=chapter_info_payload,
            image_data=agentic_result.image_data,
        )

    async def process_player_input_v3_stream(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: Optional[str] = None,
    ):
        """v3 SSE 流：复用 v3 主流程并按事件输出。"""
        self._ensure_fixed_world(world_id)
        if not settings.use_agentic_mode:
            async for event in self.process_player_input_v2_stream(
                world_id=world_id,
                session_id=session_id,
                player_input=player_input,
                is_private=is_private,
                private_target=private_target,
            ):
                yield event
            return

        try:
            yield {"type": "phase", "phase": "generating"}
            response = await self.process_player_input_v3(
                world_id=world_id,
                session_id=session_id,
                player_input=player_input,
                is_private=is_private,
                private_target=private_target,
            )
            agentic_trace = {}
            if isinstance(response.metadata, dict):
                raw_trace = response.metadata.get("agentic_trace")
                if isinstance(raw_trace, dict):
                    agentic_trace = raw_trace
            if agentic_trace:
                yield {"type": "agentic_trace", "agentic_trace": agentic_trace}
            yield {"type": "gm_start"}
            full_text = response.narration or ""
            chunk_size = 120
            for idx in range(0, len(full_text), chunk_size):
                yield {"type": "gm_chunk", "text": full_text[idx:idx + chunk_size], "chunk_type": "answer"}
            yield {"type": "gm_end", "full_text": full_text}

            for teammate in response.teammate_responses:
                yield {"type": "teammate_response", **teammate}

            yield {
                "type": "complete",
                "state_delta": response.state_delta.model_dump() if response.state_delta else None,
                "metadata": response.metadata,
                "available_actions": response.available_actions,
                "story_events": response.story_events,
                "teammate_responses": response.teammate_responses,
                "pacing_action": response.pacing_action,
                "chapter_info": response.chapter_info,
                "image_data": response.image_data,
                "agentic_trace": agentic_trace,
            }
        except ValueError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:
            logger.exception("[v3-stream] 处理失败: %s", exc)
            yield {"type": "error", "error": str(exc)}

    # ==================== Flash-Only 架构 (v2) ====================

    async def process_player_input_v2(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: Optional[str] = None,
    ) -> CoordinatorResponse:
        """
        [LEGACY] Flash-Only 架构的玩家输入处理流程（两轮 Flash）。

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
        self._ensure_fixed_world(world_id)
        if settings.use_agentic_mode:
            return await self.process_player_input_v3(
                world_id=world_id,
                session_id=session_id,
                player_input=player_input,
                is_private=is_private,
                private_target=private_target,
            )

        # Phase 守卫：角色必须存在
        pc = await self.character_store.get_character(world_id, session_id)
        if not pc:
            raise ValueError("请先创建角色后再开始冒险。")

        # 预检查：位置上下文必须可用，避免进入"未知地点"伪成功叙述
        location_check = await self._world_runtime.get_current_location(world_id, session_id)
        if location_check.get("error"):
            raise ValueError(location_check["error"])

        # 0. 获取队伍信息 + 会话历史
        party = await self.party_service.get_party(world_id, session_id)
        history = self.session_history_manager.get_or_create(world_id, session_id)

        # 1. 收集上下文
        base_context = await self._build_context(
            world_id,
            session_id,
            player_character=pc,
        )

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
            await self.narrative_service.trigger_event(world_id, session_id, event.id, skip_advance=True)
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

        # 提前启动队友记忆召回（Phase 2 并行）
        teammate_recall_tasks: Dict[str, asyncio.Task] = {}
        skip_teammate = intent.intent_type == IntentType.SYSTEM_COMMAND
        if party and party.get_active_members() and not skip_teammate:
            for member in party.get_active_members():
                char_id = member.character_id
                seeds = self._build_effective_seeds(
                    analysis.memory_seeds, base_context, character_id=char_id
                )
                teammate_recall_tasks[char_id] = asyncio.create_task(
                    self._recall_memory(
                        world_id, char_id, seeds,
                        intent_type=intent.intent_type.value,
                        chapter_id=current_chapter_id,
                        area_id=current_area_id,
                    )
                )

        flash_requests = analysis.operations or self._generate_default_flash_requests(intent, base_context)
        try:
            flash_results = await self._execute_flash_requests(
                world_id, session_id, flash_requests, generate_narration=False
            )
        except Exception:
            pending_tasks: List[asyncio.Task] = [memory_task, *teammate_recall_tasks.values()]
            for task in pending_tasks:
                if task and not task.done():
                    task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            raise
        logger.info("[v2] Flash 结果: %d 个操作", len(flash_results))

        # 检测 Flash 操作是否改变了队伍
        party_ops = {FlashOperation.ADD_TEAMMATE, FlashOperation.REMOVE_TEAMMATE, FlashOperation.DISBAND_PARTY}
        flash_changed_party = any(r.success and r.operation in party_ops for r in flash_results)
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

        curation_task = asyncio.create_task(
            self._run_curation_pipeline(
                world_id=world_id,
                player_input=player_input,
                intent=intent,
                memory_result=memory_result,
                flash_results=flash_results,
                context=dict(context),
                party=party,
                skip_teammate=skip_teammate,
                analysis_memory_seeds=analysis.memory_seeds,
                base_context=base_context,
                chapter_id=current_chapter_id,
                area_id=current_area_id,
                teammate_recall_tasks=teammate_recall_tasks,
            )
        )
        curation_task.add_done_callback(self._consume_background_task_exception)

        # 4.8 StoryDirector Post-Flash 评估（sync，无 LLM，只需 Flash 分析结果）
        flash_condition_results: Dict[str, bool] = {}
        story_prog = context.get("story_progression")
        if isinstance(story_prog, dict):
            for eval_item in story_prog.get("condition_evaluations", []):
                if isinstance(eval_item, dict) and "id" in eval_item:
                    flash_condition_results[str(eval_item["id"])] = bool(eval_item.get("result", False))

        # 仅在可能产生进度变化时刷新进度（减少重复读取）
        progress_needs_refresh = bool(pre_side_effects) or any(
            result.success and result.operation == FlashOperation.TRIGGER_NARRATIVE_EVENT
            for result in flash_results
        )
        if progress_needs_refresh:
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
        post_changed_party = False
        if final_directive.side_effects:
            await self._execute_side_effects(world_id, session_id, final_directive.side_effects)
            post_changed_party = any(
                e.get("type") in ("add_teammate", "remove_teammate")
                for e in final_directive.side_effects
            )

        # 统一刷新 party（Flash ops / pre side_effects / post side_effects 任一改变了队伍）
        pre_changed_party = any(
            e.get("type") in ("add_teammate", "remove_teammate")
            for e in pre_side_effects
        ) if pre_side_effects else False

        if flash_changed_party or pre_changed_party or post_changed_party:
            party = await self.party_service.get_party(world_id, session_id)
            logger.info("[v2] party 已刷新 (flash=%s, pre=%s, post=%s)",
                        flash_changed_party, pre_changed_party, post_changed_party)

        turn_story_events = list(pre_directive.auto_fired_events) + list(final_directive.fired_events)

        # 落地 fired_events（直接操作本地 progress，由 save_progress 统一写入，避免中间 save 覆盖）
        for event in final_directive.fired_events:
            if event.id not in progress.events_triggered:
                progress.events_triggered.append(event.id)
            logger.info("[v2] StoryDirector post-fire: %s", event.id)

        # 注入叙述指令到 context 供 GM 叙述使用
        all_narrative_directives = pre_directive.narrative_injections + final_directive.narrative_injections
        if all_narrative_directives:
            context["story_narrative_directives"] = all_narrative_directives

        # 处理 Flash 自报的 story_events → 合并入 turn_story_events（在回合计数之前）
        flash_story_prog = context.get("story_progression")
        if not isinstance(flash_story_prog, dict):
            flash_story_prog = (context.get("context_package") or {}).get("story_progression", {})
        flash_story_events_raw = flash_story_prog.get("story_events", []) if isinstance(flash_story_prog, dict) else []
        # 构建当前章节事件 ID → StoryEvent 映射
        chapter_event_map: Dict[str, Any] = {}
        for ch in active_story_chapters:
            for e in ch.events:
                chapter_event_map[e.id] = e
        already_fired_ids = {e.id for e in turn_story_events}
        if isinstance(flash_story_events_raw, list):
            for ev_id in flash_story_events_raw:
                if isinstance(ev_id, str) and ev_id.strip():
                    ev_id = ev_id.strip()
                    if ev_id in chapter_event_map and ev_id not in already_fired_ids:
                        if ev_id not in progress.events_triggered:
                            progress.events_triggered.append(ev_id)
                        turn_story_events.append(chapter_event_map[ev_id])
                        logger.info("[v2] Flash story event recorded: %s", ev_id)

        # 关键修复：flash_story_events 合并后，基于更新后的进度再次评估章节转换。
        # 避免“本回合刚完成关键事件，但要下一回合才切章”的滞后。
        if not final_directive.chapter_transition:
            reevaluated_transition = self._reevaluate_transition_after_progress(
                context=context,
                progress=progress,
                session_id=session_id,
                player_input=player_input,
                chapters=active_story_chapters,
                flash_condition_results=flash_condition_results,
                pre_auto_fired_ids=[e.id for e in pre_directive.auto_fired_events],
            )
            if reevaluated_transition:
                final_directive.chapter_transition = reevaluated_transition
                logger.info(
                    "[v2] StoryDirector transition re-evaluated after event merge: -> %s (%s)",
                    reevaluated_transition.target_chapter_id,
                    reevaluated_transition.transition_type,
                )

        # 更新回合计数（现在 has_progress 包含 flash 自报事件）
        progress.rounds_in_chapter += 1
        has_progress = bool(turn_story_events)
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
        # 冷却时间记录（现在包含 flash 自报事件）
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
                # 更新玩家位置到新章节的首个可用区域
                new_maps = v2_transition_result.get("new_maps_unlocked", [])
                if new_maps:
                    navigator = self._world_runtime._get_navigator_ready(world_id)
                    if navigator and new_maps[0] in navigator.maps:
                        state.area_id = new_maps[0]
                        state.player_location = new_maps[0]
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

        # 章节可能在本回合推进/切换，刷新章节引导以驱动当回合 GM 叙述。
        await self._refresh_chapter_context(world_id, session_id, context)

        # 5. 导航后同步队友位置
        if intent.intent_type == IntentType.NAVIGATION and party:
            logger.info("[v2] 步骤5: 同步队友位置")
            new_location = context.get("location", {}).get("location_id")
            if new_location:
                await self.party_service.sync_locations(
                    world_id, session_id, new_location
                )

        # 6. 并行阶段：玩家curation + 队友curation + GM叙述
        logger.info("[v2] 步骤6: 并行 curation + GM 叙述")
        execution_summary = self._build_execution_summary(flash_results)

        # (a) GM 叙述——不依赖 curation 结果，提前启动
        gm_task = asyncio.create_task(
            self.flash_cpu.generate_gm_narration(
                player_input=player_input,
                execution_summary=execution_summary,
                context=context,
            )
        )

        # (b) 读取并行 curation 结果（任务已在 post-eval 前启动）
        player_curated: Optional[Dict[str, Any]] = None
        t_packages: Dict[str, Dict[str, Any]] = {}
        t_summaries: Dict[str, str] = {}
        try:
            player_curated, t_packages, t_summaries = await curation_task
        except Exception as exc:
            logger.error("[v2] curation 失败: %s", exc, exc_info=True)

        # 应用玩家 curation 结果到 context
        if player_curated:
            merged_package = {}
            if isinstance(context.get("context_package"), dict):
                merged_package.update(context["context_package"])
            merged_package.update(player_curated)
            context["context_package"] = merged_package
            if isinstance(player_curated.get("story_progression"), dict):
                context["story_progression"] = player_curated["story_progression"]
            logger.info("[v2] context_package 已生成（%d 个字段）", len(merged_package))

        # 应用队友 curation 结果到 context
        if t_packages:
            context["teammate_context_packages"] = t_packages
        if t_summaries:
            context["teammate_memory_summaries"] = t_summaries

        # 步骤 7: 队友响应
        # 注入上轮队友发言到 context，供 process_round 写入各自 ContextWindow
        last_teammate_responses = history.get_last_teammate_responses()
        if last_teammate_responses:
            context["last_teammate_responses"] = last_teammate_responses

        teammate_task = None
        if party and party.get_active_members() and not skip_teammate:
            logger.info("[v2] 步骤7: 队友响应（%d 个队友）", len(party.get_active_members()))
            teammate_task = asyncio.create_task(
                self.teammate_response_service.process_round(
                    party=party,
                    player_input=player_input,
                    gm_response=execution_summary,
                    context=context,
                )
            )
        else:
            logger.debug("[v2] 步骤7: 跳过（无队友或系统命令）")

        try:
            gm_narration = await gm_task
        except Exception as e:
            logger.error("[v2] GM 叙述生成失败: %s", e, exc_info=True)
            gm_narration = "（旁白沉默了片刻……你的行动已被记录，但叙述暂时无法生成。请继续你的冒险。）"
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

        output_anomaly_meta = self._detect_output_anomalies(gm_narration)
        if output_anomaly_meta["output_anomalies"]:
            logger.warning(
                "[v2] output anomaly detected world=%s session=%s anomalies=%s excerpt=%s",
                world_id,
                session_id,
                output_anomaly_meta["output_anomalies"],
                output_anomaly_meta["output_anomaly_excerpt"],
            )

        teammate_responses = []
        teammate_debug = {
            "total": 0,
            "skipped": 0,
            "skip_reasons": [],
        }
        if teammate_task:
            teammate_result = await teammate_task
            logger.info("[v2] 队友响应完成: %d 个响应", len(teammate_result.responses))
            teammate_debug["total"] = len(teammate_result.responses)
            teammate_skip_reasons = set()
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
                else:
                    teammate_debug["skipped"] += 1
                    if resp.reaction:
                        teammate_skip_reasons.add(resp.reaction)
            teammate_debug["skip_reasons"] = sorted(teammate_skip_reasons)

        # 8. 分发事件到队友图谱（如果有队友且启用事件共享，system_command 跳过）
        if party and party.share_events and not skip_teammate:
            logger.info("[v2] 步骤8: 分发事件到队友图谱")
            await self._distribute_event_to_party(
                world_id=world_id,
                party=party,
                player_input=player_input,
                gm_response=gm_narration,
                context=context,
            )
            logger.info("[v2] 事件分发完成")

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
                **output_anomaly_meta,
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
        chapter_info_payload = self._build_chapter_response_payload(
            context=context,
            progress=progress,
            final_directive=final_directive,
        )
        story_director_meta = self._build_story_director_metadata(
            pre_directive=pre_directive,
            final_directive=final_directive,
            turn_story_events=turn_story_events,
        )

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
                "story_director": story_director_meta,
                "teammate_debug": teammate_debug,
                **output_anomaly_meta,
            },
            story_events=[e.id for e in turn_story_events] if turn_story_events else [],
            pacing_action=final_directive.pacing_action if final_directive else None,
            chapter_info=chapter_info_payload,
        )

    async def process_player_input_v2_stream(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: Optional[str] = None,
    ):
        """
        [LEGACY] 流式 Flash-Only 玩家输入处理（异步生成器）。

        前置阶段（1-4.5）与 v2 相同，随后流式输出 GM 叙述和队友响应。

        Yields:
            dict: SSE 事件 (phase/gm_start/gm_chunk/gm_end/teammate_*/complete/error/time_event)
        """
        self._ensure_fixed_world(world_id)
        if settings.use_agentic_mode:
            async for event in self.process_player_input_v3_stream(
                world_id=world_id,
                session_id=session_id,
                player_input=player_input,
                is_private=is_private,
                private_target=private_target,
            ):
                yield event
            return

        gm_narration = ""
        teammate_responses = []
        history = None
        context = {}
        intent = None
        flash_results = []
        turn_story_events = []
        pre_directive = None
        final_directive = None
        v2_transition_result = None
        progress = None
        party = None
        round_stats = None
        output_anomaly_meta = {"output_anomalies": [], "output_anomaly_excerpt": None}
        teammate_debug = {
            "total": 0,
            "skipped": 0,
            "skip_reasons": [],
        }
        memory_task: Optional[asyncio.Task] = None
        curation_task: Optional[asyncio.Task] = None
        teammate_recall_tasks: Dict[str, asyncio.Task] = {}

        try:
            # Phase 守卫：角色必须存在
            pc = await self.character_store.get_character(world_id, session_id)
            if not pc:
                yield {"type": "error", "error": "请先创建角色后再开始冒险。"}
                return

            # === 预生成阶段（复用 v2 逻辑） ===
            location_check = await self._world_runtime.get_current_location(world_id, session_id)
            if location_check.get("error"):
                yield {"type": "error", "error": location_check["error"]}
                return

            party = await self.party_service.get_party(world_id, session_id)
            history = self.session_history_manager.get_or_create(world_id, session_id)

            base_context = await self._build_context(
                world_id,
                session_id,
                player_character=pc,
            )
            conversation_history = history.get_recent_history(max_tokens=settings.session_history_max_tokens)
            if conversation_history:
                base_context["conversation_history"] = conversation_history

            # 注入私密信息到上下文
            if is_private:
                base_context["is_private"] = True
                base_context["private_target"] = private_target

            # StoryDirector Pre-Flash
            progress = await self.narrative_service.get_progress(world_id, session_id)
            active_story_chapters = self._resolve_story_chapters(world_id, progress)
            current_chapter = active_story_chapters[0] if active_story_chapters else None
            game_ctx = self._build_game_context(base_context, progress, session_id, player_input)

            if len(active_story_chapters) > 1:
                pre_directive = self.story_director.pre_evaluate_multi(game_ctx, active_story_chapters)
            else:
                pre_directive = self.story_director.pre_evaluate(game_ctx, current_chapter)

            pre_side_effects: List[Dict[str, Any]] = []
            for event in pre_directive.auto_fired_events:
                await self.narrative_service.trigger_event(world_id, session_id, event.id, skip_advance=True)
                if event.side_effects:
                    pre_side_effects.extend(event.side_effects)
            if pre_side_effects:
                await self._execute_side_effects(world_id, session_id, pre_side_effects)

            base_context["story_directives"] = pre_directive.narrative_injections
            base_context["pending_flash_conditions"] = [
                {"id": c.condition_id, "prompt": c.condition_prompt, "event_id": c.event_id}
                for c in pre_directive.pending_flash_conditions
            ]

            # Flash 分析
            analysis = await self.flash_cpu.analyze_and_plan(player_input, base_context)
            intent = analysis.intent

            # 注入私密标记到 intent
            if is_private:
                intent.is_private = True
                intent.private_target = private_target

            # 记忆召回 + 操作执行
            state = await self._state_manager.get_state(world_id, session_id)
            current_chapter_id = getattr(state, "chapter_id", None) if state else None
            current_area_id = getattr(state, "area_id", None) if state else None

            effective_seeds = self._build_effective_seeds(
                analysis.memory_seeds, base_context, character_id="player",
            )
            memory_task = asyncio.create_task(
                self._recall_memory(
                    world_id, "player", effective_seeds,
                    intent_type=intent.intent_type.value,
                    chapter_id=current_chapter_id,
                    area_id=current_area_id,
                )
            )

            skip_teammate = intent.intent_type == IntentType.SYSTEM_COMMAND
            if party and party.get_active_members() and not skip_teammate:
                for member in party.get_active_members():
                    char_id = member.character_id
                    seeds = self._build_effective_seeds(
                        analysis.memory_seeds, base_context, character_id=char_id
                    )
                    teammate_recall_tasks[char_id] = asyncio.create_task(
                        self._recall_memory(
                            world_id, char_id, seeds,
                            intent_type=intent.intent_type.value,
                            chapter_id=current_chapter_id,
                            area_id=current_area_id,
                        )
                    )

            flash_requests = analysis.operations or self._generate_default_flash_requests(intent, base_context)
            flash_results = await self._execute_flash_requests(
                world_id, session_id, flash_requests, generate_narration=False
            )

            party_ops = {FlashOperation.ADD_TEAMMATE, FlashOperation.REMOVE_TEAMMATE, FlashOperation.DISBAND_PARTY}
            flash_changed_party = any(r.success and r.operation in party_ops for r in flash_results)

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
                    logger.error("[v2-stream] 离开子地点失败: %s", e)

            memory_result = await memory_task if memory_task else None

            context = await self._assemble_context(
                base_context, memory_result, flash_results, world_id, session_id
            )

            if isinstance(analysis.context_package, dict) and analysis.context_package:
                context["context_package"] = dict(analysis.context_package)
            if isinstance(analysis.story_progression, dict):
                context["story_progression"] = analysis.story_progression

            # 注入私密上下文
            if is_private:
                context["is_private"] = True
                context["private_target"] = private_target

            curation_task = asyncio.create_task(
                self._run_curation_pipeline(
                    world_id=world_id,
                    player_input=player_input,
                    intent=intent,
                    memory_result=memory_result,
                    flash_results=flash_results,
                    context=dict(context),
                    party=party,
                    skip_teammate=skip_teammate,
                    analysis_memory_seeds=analysis.memory_seeds,
                    base_context=base_context,
                    chapter_id=current_chapter_id,
                    area_id=current_area_id,
                    teammate_recall_tasks=teammate_recall_tasks,
                )
            )
            curation_task.add_done_callback(self._consume_background_task_exception)

            # StoryDirector Post-Flash
            flash_condition_results: Dict[str, bool] = {}
            story_prog = context.get("story_progression")
            if isinstance(story_prog, dict):
                for eval_item in story_prog.get("condition_evaluations", []):
                    if isinstance(eval_item, dict) and "id" in eval_item:
                        flash_condition_results[str(eval_item["id"])] = bool(eval_item.get("result", False))

            progress_needs_refresh = bool(pre_side_effects) or any(
                result.success and result.operation == FlashOperation.TRIGGER_NARRATIVE_EVENT
                for result in flash_results
            )
            if progress_needs_refresh:
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

            post_changed_party = False
            if final_directive.side_effects:
                await self._execute_side_effects(world_id, session_id, final_directive.side_effects)
                post_changed_party = any(
                    e.get("type") in ("add_teammate", "remove_teammate")
                    for e in final_directive.side_effects
                )

            pre_changed_party = any(
                e.get("type") in ("add_teammate", "remove_teammate")
                for e in pre_side_effects
            ) if pre_side_effects else False

            if flash_changed_party or pre_changed_party or post_changed_party:
                party = await self.party_service.get_party(world_id, session_id)

            turn_story_events = list(pre_directive.auto_fired_events) + list(final_directive.fired_events)
            for event in final_directive.fired_events:
                if event.id not in progress.events_triggered:
                    progress.events_triggered.append(event.id)

            all_narrative_directives = pre_directive.narrative_injections + final_directive.narrative_injections
            if all_narrative_directives:
                context["story_narrative_directives"] = all_narrative_directives

            # Flash story events
            flash_story_prog = context.get("story_progression")
            if not isinstance(flash_story_prog, dict):
                flash_story_prog = (context.get("context_package") or {}).get("story_progression", {})
            flash_story_events_raw = flash_story_prog.get("story_events", []) if isinstance(flash_story_prog, dict) else []
            chapter_event_map: Dict[str, Any] = {}
            for ch in active_story_chapters:
                for e in ch.events:
                    chapter_event_map[e.id] = e
            already_fired_ids = {e.id for e in turn_story_events}
            if isinstance(flash_story_events_raw, list):
                for ev_id in flash_story_events_raw:
                    if isinstance(ev_id, str) and ev_id.strip():
                        ev_id = ev_id.strip()
                        if ev_id in chapter_event_map and ev_id not in already_fired_ids:
                            if ev_id not in progress.events_triggered:
                                progress.events_triggered.append(ev_id)
                            turn_story_events.append(chapter_event_map[ev_id])

            # 关键修复：flash_story_events 合并后，再次评估章节转换（流式同回合生效）。
            if not final_directive.chapter_transition:
                reevaluated_transition = self._reevaluate_transition_after_progress(
                    context=context,
                    progress=progress,
                    session_id=session_id,
                    player_input=player_input,
                    chapters=active_story_chapters,
                    flash_condition_results=flash_condition_results,
                    pre_auto_fired_ids=[e.id for e in pre_directive.auto_fired_events],
                )
                if reevaluated_transition:
                    final_directive.chapter_transition = reevaluated_transition
                    logger.info(
                        "[v2-stream] transition re-evaluated after event merge: -> %s (%s)",
                        reevaluated_transition.target_chapter_id,
                        reevaluated_transition.transition_type,
                    )

            progress.rounds_in_chapter += 1
            has_progress = bool(turn_story_events)
            if has_progress:
                progress.rounds_since_last_progress = 0
            else:
                progress.rounds_since_last_progress += 1
            for req, res in zip(flash_requests, flash_results):
                if res.success and res.operation == FlashOperation.NPC_DIALOGUE:
                    npc_id = req.parameters.get("npc_id")
                    if npc_id:
                        progress.npc_interactions[npc_id] = progress.npc_interactions.get(npc_id, 0) + 1
            for event in turn_story_events:
                progress.event_cooldowns[event.id] = progress.rounds_in_chapter

            await self.narrative_service.save_progress(world_id, session_id, progress)

            # 章节转换
            v2_transition_result = None
            if final_directive.chapter_transition:
                trans = final_directive.chapter_transition
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

            if settings.story_event_graph_sync and (turn_story_events or (final_directive and final_directive.chapter_transition)):
                await self._sync_story_director_graph(
                    world_id=world_id,
                    session_id=session_id,
                    events=turn_story_events,
                    chapter_transition=final_directive.chapter_transition if final_directive else None,
                    context=context,
                    player_input=player_input,
                    party=party,
                    progress=progress,
                )

            # 章节可能在本回合推进/切换，刷新章节引导用于当回合流式叙述。
            await self._refresh_chapter_context(world_id, session_id, context)

            # 导航后同步队友位置
            if intent.intent_type == IntentType.NAVIGATION and party:
                new_location = context.get("location", {}).get("location_id")
                if new_location:
                    await self.party_service.sync_locations(world_id, session_id, new_location)

            # Curation
            execution_summary = self._build_execution_summary(flash_results)
            player_curated: Optional[Dict[str, Any]] = None
            t_packages: Dict[str, Dict[str, Any]] = {}
            t_summaries: Dict[str, str] = {}
            if curation_task:
                try:
                    player_curated, t_packages, t_summaries = await curation_task
                except Exception as exc:
                    logger.error("[v2-stream] curation 失败: %s", exc, exc_info=True)
            if player_curated:
                merged_package = {}
                if isinstance(context.get("context_package"), dict):
                    merged_package.update(context["context_package"])
                merged_package.update(player_curated)
                context["context_package"] = merged_package
            if t_packages:
                context["teammate_context_packages"] = t_packages
            if t_summaries:
                context["teammate_memory_summaries"] = t_summaries

            last_teammate_responses = history.get_last_teammate_responses()
            if last_teammate_responses:
                context["last_teammate_responses"] = last_teammate_responses

            # === 预处理完成，开始流式输出 ===
            yield {"type": "phase", "phase": "generating"}

            # --- 流式 GM 叙述 ---
            yield {"type": "gm_start"}
            gm_narration = ""
            try:
                async for chunk in self.flash_cpu.generate_gm_narration_stream(
                    player_input=player_input,
                    execution_summary=execution_summary,
                    context=context,
                ):
                    if chunk["type"] == "answer":
                        gm_narration += chunk["text"]
                        yield {"type": "gm_chunk", "text": chunk["text"], "chunk_type": "answer"}
                    elif chunk["type"] == "thought":
                        yield {"type": "gm_chunk", "text": chunk["text"], "chunk_type": "thought"}
            except Exception as e:
                logger.error("[v2-stream] GM 叙述流式生成失败: %s", e, exc_info=True)
                gm_narration = "（旁白沉默了片刻……你的行动已被记录。请继续你的冒险。）"

            if not gm_narration.strip():
                gm_narration = "……"

            # 章节转换叙述
            if v2_transition_result and v2_transition_result.get("new_chapter"):
                transition_text = await self._generate_chapter_transition(
                    world_id,
                    session_id,
                    v2_transition_result.get("new_chapter"),
                    v2_transition_result.get("new_maps_unlocked", []),
                )
                if transition_text:
                    gm_narration += f"\n\n{transition_text}"
                    yield {"type": "gm_chunk", "text": f"\n\n{transition_text}", "chunk_type": "answer"}

            output_anomaly_meta = self._detect_output_anomalies(gm_narration)
            if output_anomaly_meta["output_anomalies"]:
                logger.warning(
                    "[v2-stream] output anomaly detected world=%s session=%s anomalies=%s excerpt=%s",
                    world_id,
                    session_id,
                    output_anomaly_meta["output_anomalies"],
                    output_anomaly_meta["output_anomaly_excerpt"],
                )

            yield {"type": "gm_end", "full_text": gm_narration}

            # --- 对话/战斗时间推进 ---
            time_events = []
            has_npc_dialogue = any(
                r.success and r.operation == FlashOperation.NPC_DIALOGUE
                for r in flash_results
            )
            has_combat = any(
                r.success and r.operation == FlashOperation.START_COMBAT
                for r in flash_results
            )
            if has_npc_dialogue or has_combat or (intent and intent.intent_type in (IntentType.TEAM_INTERACTION, IntentType.ROLEPLAY)):
                advance_minutes = 6 if has_npc_dialogue else (2 if has_combat else 5)
                try:
                    state = await self._state_manager.get_state(world_id, session_id)
                    if state and state.game_time:
                        from app.services.time_manager import TimeManager as _TM, GameTime as _GT
                        gt = _GT(day=state.game_time.day, hour=state.game_time.hour, minute=state.game_time.minute)
                        tm = _TM(initial_time=gt)
                        time_events = tm.tick(advance_minutes)
                        from app.models.state_delta import GameTimeState
                        td = tm.to_dict()
                        state.game_time = GameTimeState(**td)
                        await self._state_manager.set_state(world_id, session_id, state)
                        await self._world_runtime.persist_state(state)
                        context["time"] = td
                except Exception as e:
                    logger.warning("[v2-stream] 时间推进失败: %s", e)

            # 时段切换事件
            for te in time_events:
                if te.event_type == "period_change":
                    yield {
                        "type": "time_event",
                        "event_type": te.event_type,
                        "description": te.description,
                        "data": te.data,
                    }

            # --- 流式队友响应 ---
            if party and party.get_active_members() and not skip_teammate:
                async for event in self.teammate_response_service.process_round_stream(
                    party=party,
                    player_input=player_input,
                    gm_response=execution_summary,
                    context=context,
                ):
                    yield event
                    if event["type"] == "teammate_skip":
                        teammate_debug["total"] += 1
                        teammate_debug["skipped"] += 1
                        reason = event.get("reason")
                        if isinstance(reason, str) and reason.strip():
                            teammate_debug["skip_reasons"].append(reason.strip())
                    elif event["type"] == "teammate_end":
                        teammate_debug["total"] += 1
                        if event.get("response"):
                            teammate_responses.append({
                                "character_id": event["character_id"],
                                "name": event["name"],
                                "response": event["response"],
                                "reaction": event.get("reaction", ""),
                            })
                        else:
                            teammate_debug["skipped"] += 1
                            reaction = event.get("reaction")
                            if isinstance(reaction, str) and reaction.strip():
                                teammate_debug["skip_reasons"].append(reaction.strip())

            # 事件分发
            if party and party.share_events and not skip_teammate:
                await self._distribute_event_to_party(
                    world_id=world_id,
                    party=party,
                    player_input=player_input,
                    gm_response=gm_narration,
                    context=context,
                )

        except asyncio.CancelledError:
            logger.info("[v2-stream] 流式处理被取消 world=%s session=%s", world_id, session_id)
            raise
        except Exception as e:
            logger.exception("[v2-stream] 流式处理失败: %s", e)
            yield {"type": "error", "error": str(e)}
            return
        finally:
            pending_tasks: List[asyncio.Task] = []
            if memory_task is not None:
                pending_tasks.append(memory_task)
            if curation_task is not None:
                pending_tasks.append(curation_task)
            pending_tasks.extend(teammate_recall_tasks.values())
            for task in pending_tasks:
                if task and not task.done():
                    task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            # 后处理：历史记录 + 图谱化（必须执行）
            if history and gm_narration and intent:
                try:
                    visibility = "private" if is_private else "public"
                    round_stats = history.record_round(
                        player_input=player_input,
                        gm_response=gm_narration,
                        metadata={
                            "intent_type": intent.intent_type.value,
                            "story_events": [event.id for event in turn_story_events] if turn_story_events else [],
                            "visibility": visibility,
                            "private_target": private_target if is_private else None,
                            **output_anomaly_meta,
                        },
                    )
                    for tr in teammate_responses:
                        history.record_teammate_response(
                            character_id=tr["character_id"],
                            name=tr["name"],
                            response=tr["response"],
                        )
                    if round_stats and round_stats.get("should_graphize"):
                        game_day = context.get("time", {}).get("day", 1)
                        location_id = context.get("location", {}).get("location_id")
                        asyncio.create_task(
                            self._run_graphization(history, game_day, location_id)
                        )
                except Exception as exc:
                    logger.error("[v2-stream] 后处理失败: %s", exc, exc_info=True)

        # 最终 complete 事件
        state_delta = self._merge_state_deltas(flash_results) if flash_results else None

        # 注入更新后的时间到 state_delta（对话/战斗时间推进更新了 context["time"]，但未进 state_delta）
        updated_time = context.get("time")
        if updated_time:
            if state_delta:
                state_delta.changes["game_time"] = updated_time
            else:
                from app.models.state_delta import StateDelta as _SD
                from datetime import datetime as _dt
                state_delta = _SD(
                    delta_id=f"time_{_dt.now().isoformat()}",
                    timestamp=_dt.now(),
                    operation="time_update",
                    changes={"game_time": updated_time},
                )

        available_actions = await self._get_available_actions(world_id, session_id, context)
        chapter_info_payload = self._build_chapter_response_payload(
            context=context,
            progress=progress,
            final_directive=final_directive,
        )
        story_director_meta = self._build_story_director_metadata(
            pre_directive=pre_directive,
            final_directive=final_directive,
            turn_story_events=turn_story_events,
        )
        teammate_debug["skip_reasons"] = sorted(set(teammate_debug["skip_reasons"]))

        yield {
            "type": "complete",
            "state_delta": state_delta.model_dump() if state_delta else None,
            "metadata": {
                "intent_type": intent.intent_type.value if intent else "unknown",
                "teammate_count": len(teammate_responses),
                "source": "flash_gm_stream",
                "is_private": is_private,
                "time": context.get("time"),
                "story_director": story_director_meta,
                "teammate_debug": teammate_debug,
                **output_anomaly_meta,
            },
            "available_actions": available_actions,
            "story_events": [e.id for e in turn_story_events] if turn_story_events else [],
            "teammate_responses": teammate_responses,
            "pacing_action": final_directive.pacing_action if final_directive else None,
            "chapter_info": chapter_info_payload,
        }

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

        # Determine phase based on character existence
        has_character = bool(await self.character_store.get_character(world_id, session_id))
        phase = "active" if has_character else "character_creation"

        return {
            "session_id": session_id,
            "restored": True,
            "phase": phase,
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
            event_directives = chapter_plan.get("event_directives", [])
            first_event_directive = event_directives[0] if event_directives else "探索当前环境"

            # {{user}} 占位符替换
            _sanitize = lambda t: (t or "").replace("{{user}}", "冒险者").replace("{{char}}", "")
            chapter_name = _sanitize(chapter_name)
            chapter_description = _sanitize(chapter_description)
            first_event_directive = _sanitize(first_event_directive)

            # 队友
            party = await self.party_service.get_party(world_id, session_id)
            teammates = "无"
            if party:
                names = [m.name for m in party.get_active_members()]
                if names:
                    teammates = ", ".join(names)

            # 玩家角色信息
            player_char = await self.character_store.get_character(world_id, session_id)
            player_character_text = player_char.to_summary_text() if player_char else "无玩家角色"

            # 加载 prompt
            prompt_path = Path("app/prompts/opening_narration.md")
            if prompt_path.exists():
                prompt_template = prompt_path.read_text(encoding="utf-8")
                prompt = prompt_template.format(
                    player_character=player_character_text,
                    world_background=world_background or "未知世界",
                    chapter_name=chapter_name,
                    chapter_description=chapter_description[:600] or "冒险的开始",
                    chapter_goals="、".join(
                        _sanitize(g) for g in chapter_goals[:3]
                    ) if chapter_goals else "探索世界",
                    first_event_directive=first_event_directive,
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
        result = "\n".join(blocks)
        return result.replace("{{user}}", "冒险者").replace("{{char}}", "")

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

    async def _get_character_id_set(self, world_id: str) -> set:
        """获取世界所有角色 ID 集合（缓存）"""
        if world_id in self._character_ids_cache:
            return self._character_ids_cache[world_id]

        char_ids: set = set()
        try:
            chars_ref = self.graph_store.db.collection("worlds").document(world_id).collection("characters")
            for doc in chars_ref.stream():
                char_ids.add(doc.id)
        except Exception as exc:
            logger.debug("[character_id_set] Firestore 读取失败: %s", exc)

        self._character_ids_cache[world_id] = char_ids
        return char_ids

    async def _get_area_chapter_map(self, world_id: str) -> Dict[str, str]:
        """获取 area→首次出现 chapter 映射（缓存）。"""
        if world_id in self._area_chapter_cache:
            return self._area_chapter_cache[world_id]

        mapping: Dict[str, str] = {}
        try:
            chapters_ref = self.graph_store.db.collection("worlds").document(world_id).collection("chapters")
            for doc in chapters_ref.stream():
                ch_data = doc.to_dict() or {}
                ch_id = ch_data.get("id") or doc.id
                areas = ch_data.get("available_areas") or ch_data.get("available_maps") or []
                for area_id in areas:
                    if area_id not in mapping:
                        mapping[area_id] = ch_id
        except Exception as exc:
            logger.debug("[area_chapter_map] Firestore 读取失败: %s", exc)

        self._area_chapter_cache[world_id] = mapping
        return mapping

    async def _build_context(
        self,
        world_id: str,
        session_id: str,
        player_character: Any = None,
    ) -> Dict[str, Any]:
        """构建当前上下文（并行读取只读依赖）。"""
        context = {"world_id": world_id}

        task_map: Dict[str, Any] = {
            "location": self._world_runtime.get_current_location(world_id, session_id),
            "time": self._world_runtime.get_game_time(world_id, session_id),
            "state": self._state_manager.get_state(world_id, session_id),
            "party": self.party_service.get_party(world_id, session_id),
            "world_background": self._get_world_background(world_id, session_id),
            "character_roster": self._get_character_roster(world_id),
        }
        if player_character is None:
            task_map["player_character"] = self.character_store.get_character(world_id, session_id)

        task_keys = list(task_map.keys())
        task_results = await asyncio.gather(
            *(task_map[key] for key in task_keys),
            return_exceptions=True,
        )
        result_map = dict(zip(task_keys, task_results))

        location = result_map.get("location")
        if isinstance(location, Exception):
            logger.debug("[_build_context] 位置信息加载失败: %s", location)
            location = {}
        if not isinstance(location, dict):
            location = {}
        context["location"] = location
        context["available_destinations"] = location.get("available_destinations", [])
        context["sub_locations"] = location.get("available_sub_locations", [])

        time_info = result_map.get("time")
        if isinstance(time_info, Exception):
            logger.debug("[_build_context] 时间信息加载失败: %s", time_info)
            time_info = {}
        context["time"] = time_info if isinstance(time_info, dict) else {}

        state = result_map.get("state")
        if isinstance(state, Exception):
            logger.debug("[_build_context] 状态加载失败: %s", state)
            state = None
        if state:
            context["state"] = "in_dialogue" if state.active_dialogue_npc else (
                "combat" if state.combat_id else "exploring"
            )
            context["active_npc"] = state.active_dialogue_npc
        else:
            context["state"] = "exploring"
            context["active_npc"] = None

        party = result_map.get("party")
        if isinstance(party, Exception):
            logger.debug("[_build_context] 队伍信息加载失败: %s", party)
            party = None
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

        try:
            player_char = player_character
            if player_char is None:
                player_char = result_map.get("player_character")
                if isinstance(player_char, Exception):
                    raise player_char
            if player_char:
                context["player_character"] = player_char
                context["player_character_summary"] = player_char.to_summary_text()
            else:
                context["player_character_summary"] = "无玩家角色"
        except Exception as exc:
            logger.debug("[_build_context] 玩家角色加载失败: %s", exc)
            context["player_character_summary"] = "无玩家角色"

        world_background = result_map.get("world_background")
        if isinstance(world_background, Exception):
            logger.debug("[_build_context] 世界背景加载失败: %s", world_background)
            world_background = "无"
        context["world_background"] = world_background

        character_roster = result_map.get("character_roster")
        if isinstance(character_roster, Exception):
            logger.debug("[_build_context] 角色花名册加载失败: %s", character_roster)
            character_roster = "无"
        context["character_roster"] = character_roster

        await self._refresh_chapter_context(world_id, session_id, context)
        return context

    async def _refresh_chapter_context(
        self,
        world_id: str,
        session_id: str,
        context: Dict[str, Any],
    ) -> None:
        """刷新上下文中的章节编排信息。"""
        try:
            chapter_plan = await self.narrative_service.get_current_chapter_plan(
                world_id, session_id
            )
            if not isinstance(chapter_plan, dict):
                context["chapter_info"] = {}
                return

            chapter_info = dict(chapter_plan)
            progress = await self.narrative_service.get_progress(world_id, session_id)

            triggered_events = [
                event_id
                for event_id in (getattr(progress, "events_triggered", []) or [])
                if isinstance(event_id, str) and event_id.strip()
            ]
            triggered_set = set(triggered_events)
            required_event_summaries_raw = chapter_info.get("required_event_summaries", [])
            required_event_summaries: List[Dict[str, Any]] = []
            if isinstance(required_event_summaries_raw, list):
                for item in required_event_summaries_raw:
                    if not isinstance(item, dict):
                        continue
                    event_id = item.get("id")
                    if not isinstance(event_id, str) or not event_id.strip():
                        continue
                    normalized = dict(item)
                    normalized["id"] = event_id.strip()
                    normalized["completed"] = normalized["id"] in triggered_set
                    required_event_summaries.append(normalized)

            required_events_raw = chapter_info.get("required_events", [])
            required_events_from_ids = [
                event_id
                for event_id in required_events_raw
                if isinstance(event_id, str) and event_id.strip()
            ]
            required_events_from_summaries = [
                item["id"] for item in required_event_summaries
                if isinstance(item.get("id"), str) and item["id"].strip()
            ]
            required_events = required_events_from_ids or required_events_from_summaries
            pending_required_events = [
                event_id for event_id in required_events if event_id not in triggered_set
            ]
            event_total = len(required_events)
            event_completed = event_total - len(pending_required_events)
            event_completion_pct = (
                round((event_completed / event_total) * 100, 2) if event_total > 0 else 0.0
            )
            all_required_events_completed = event_total > 0 and event_completed >= event_total
            waiting_transition = bool(
                all_required_events_completed and not chapter_info.get("current_event")
            )

            chapter_info["events_triggered"] = triggered_events
            chapter_info["required_event_summaries"] = required_event_summaries
            chapter_info["pending_required_events"] = pending_required_events
            chapter_info["event_total"] = event_total
            chapter_info["event_completed"] = event_completed
            chapter_info["event_completion_pct"] = event_completion_pct
            chapter_info["all_required_events_completed"] = all_required_events_completed
            chapter_info["waiting_transition"] = waiting_transition
            context["chapter_info"] = chapter_info
        except Exception as exc:
            logger.debug("[_refresh_chapter_context] 章节信息获取失败: %s", exc)
            context["chapter_info"] = {}

    @staticmethod
    def _build_chapter_response_payload(
        context: Dict[str, Any],
        progress: Any,
        final_directive: Any,
    ) -> Optional[Dict[str, Any]]:
        """构建返回给前端的章节信息摘要。"""
        payload: Dict[str, Any] = {}

        chapter_info = context.get("chapter_info") or {}
        chapter_obj = chapter_info.get("chapter") if isinstance(chapter_info, dict) else {}
        chapter_id = chapter_obj.get("id") if isinstance(chapter_obj, dict) else None
        chapter_name = chapter_obj.get("name") if isinstance(chapter_obj, dict) else None
        chapter_description = (
            chapter_obj.get("description") if isinstance(chapter_obj, dict) else None
        )

        if not chapter_id:
            chapter_id = getattr(progress, "current_chapter", None)

        if chapter_id:
            payload["id"] = chapter_id
        if chapter_name:
            payload["name"] = chapter_name
        if chapter_description:
            payload["description"] = chapter_description

        if isinstance(chapter_info, dict):
            for key in (
                "goals",
                "required_events",
                "required_event_summaries",
                "pending_required_events",
                "events_triggered",
                "suggested_maps",
                "event_directives",
                "current_event",
                "next_chapter",
                "event_total",
                "event_completed",
                "event_completion_pct",
                "all_required_events_completed",
                "waiting_transition",
            ):
                value = chapter_info.get(key)
                if value not in (None, "", []):
                    payload[key] = value

        transition = (
            getattr(getattr(final_directive, "chapter_transition", None), "target_chapter_id", None)
            if final_directive
            else None
        )
        if transition:
            payload["transition"] = transition

        pacing_action = (
            getattr(final_directive, "pacing_action", None)
            if final_directive
            else None
        )
        if pacing_action:
            payload["pacing_action"] = pacing_action

        return payload or None

    def _reevaluate_transition_after_progress(
        self,
        context: Dict[str, Any],
        progress: Any,
        session_id: str,
        player_input: str,
        chapters: List[Any],
        flash_condition_results: Dict[str, bool],
        pre_auto_fired_ids: List[str],
    ) -> Optional[Any]:
        """在进度更新后重新评估是否应当发生章节转换。"""
        if not chapters:
            return None

        game_ctx_updated = self._build_game_context(context, progress, session_id, player_input)
        if len(chapters) > 1:
            reevaluated = self.story_director.post_evaluate_multi(
                game_ctx_updated,
                chapters=chapters,
                flash_condition_results=flash_condition_results,
                pre_auto_fired_ids=pre_auto_fired_ids,
            )
        else:
            reevaluated = self.story_director.post_evaluate(
                game_ctx_updated,
                chapter=chapters[0],
                flash_condition_results=flash_condition_results,
                pre_auto_fired_ids=pre_auto_fired_ids,
            )
        return getattr(reevaluated, "chapter_transition", None)

    @staticmethod
    def _detect_output_anomalies(narration: str) -> Dict[str, Any]:
        """检测疑似 thought/草稿泄露，仅用于可观测性记录，不改写正文。"""
        if not isinstance(narration, str) or not narration.strip():
            return {"output_anomalies": [], "output_anomaly_excerpt": None}

        text = narration.strip()
        lowered = text.lower()
        markers = [
            "thought",
            "draft 1",
            "final polish",
            "self-correction",
            "refining",
            "revised narrative",
            "player character (pc)",
            "current scenario",
            "recent action",
            "context:",
            "follow him into",
        ]
        marker_hits = sum(1 for marker in markers if marker in lowered)
        bullet_lines = len(re.findall(r"(?m)^\s*[\*\-]\s{0,3}", text))
        has_thought_header = bool(re.search(r"(?im)^\s*thought\s*$", text))
        has_leak = has_thought_header or marker_hits >= 3 or (
            "player character (pc)" in lowered and bullet_lines >= 3
        )

        if not has_leak:
            return {"output_anomalies": [], "output_anomaly_excerpt": None}

        compact = re.sub(r"\s+", " ", text)
        return {
            "output_anomalies": ["thought_leak_suspected"],
            "output_anomaly_excerpt": compact[:240],
        }

    @staticmethod
    def _build_story_director_metadata(
        pre_directive: Any,
        final_directive: Any,
        turn_story_events: List[Any],
    ) -> Dict[str, Any]:
        """构建 StoryDirector 可观测性摘要。"""
        pre_auto_fired = [
            event.id
            for event in getattr(pre_directive, "auto_fired_events", []) or []
            if getattr(event, "id", None)
        ]
        post_fired = [
            event.id
            for event in getattr(final_directive, "fired_events", []) or []
            if getattr(event, "id", None)
        ]
        turn_story_event_ids = [
            event.id
            for event in turn_story_events or []
            if getattr(event, "id", None)
        ]

        transition = getattr(final_directive, "chapter_transition", None) if final_directive else None
        transition_target = getattr(transition, "target_chapter_id", None) if transition else None
        transition_type = getattr(transition, "transition_type", None) if transition else None

        pre_injections = len(getattr(pre_directive, "narrative_injections", []) or [])
        post_injections = len(getattr(final_directive, "narrative_injections", []) or [])

        return {
            "pre_auto_fired": pre_auto_fired,
            "post_fired": post_fired,
            "turn_story_events": turn_story_event_ids,
            "pacing_action": getattr(final_directive, "pacing_action", None) if final_directive else None,
            "transition_target": transition_target,
            "transition_type": transition_type,
            "narrative_directive_count": pre_injections + post_injections,
        }

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
        "enter_sublocation": {"depth": 1, "output_threshold": 0.3},
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
        if getattr(self, "recall_orchestrator", None) is not None:
            return await self.recall_orchestrator.recall(
                world_id=world_id,
                character_id=character_id,
                seed_nodes=seed_nodes,
                intent_type=intent_type,
                chapter_id=chapter_id,
                area_id=area_id,
            )

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
        scoped_data: List[Tuple[GraphScope, Any]] = []

        mandatory_scopes: List[GraphScope] = []
        mandatory_calls: List[Any] = []

        # 1) 角色个人图谱（必选）
        char_scope = GraphScope(scope_type="character", character_id=character_id)
        mandatory_scopes.append(char_scope)
        mandatory_calls.append(self.graph_store.load_graph_v2(world_id, char_scope))

        area_scope: Optional[GraphScope] = None
        area_data: Optional[Any] = None
        if chapter_id and area_id:
            area_scope = GraphScope(
                scope_type="area",
                chapter_id=chapter_id,
                area_id=area_id,
            )
            mandatory_scopes.append(area_scope)
            mandatory_calls.append(self.graph_store.load_graph_v2(world_id, area_scope))

        if chapter_id:
            chapter_scope = GraphScope(scope_type="chapter", chapter_id=chapter_id)
            mandatory_scopes.append(chapter_scope)
            mandatory_calls.append(self.graph_store.load_graph_v2(world_id, chapter_scope))

        camp_scope = GraphScope(scope_type="camp")
        mandatory_scopes.append(camp_scope)
        mandatory_calls.append(self.graph_store.load_graph_v2(world_id, camp_scope))

        world_scope = GraphScope(scope_type="world")
        mandatory_scopes.append(world_scope)
        mandatory_calls.append(self.graph_store.load_graph_v2(world_id, world_scope))

        mandatory_results = await asyncio.gather(*mandatory_calls, return_exceptions=True)
        for scope, result in zip(mandatory_scopes, mandatory_results):
            if isinstance(result, Exception):
                logger.warning("[v2] 图谱读取失败 scope=%s: %s", scope, result)
                continue
            scoped_data.append((scope, result))
            if area_scope is not None and scope == area_scope:
                area_data = result

        # 当前 chapter 下没区域图谱时，回退到 area 的原始 chapter
        if chapter_id and area_id and area_scope is not None and not getattr(area_data, "nodes", None):
            area_chapter_map = await self._get_area_chapter_map(world_id)
            original_chapter = area_chapter_map.get(area_id)
            if original_chapter and original_chapter != chapter_id:
                fallback_scope = GraphScope(
                    scope_type="area",
                    chapter_id=original_chapter,
                    area_id=area_id,
                )
                try:
                    fallback_data = await self.graph_store.load_graph_v2(world_id, fallback_scope)
                    scoped_data = [
                        (scope, data)
                        for scope, data in scoped_data
                        if not (
                            scope.scope_type == "area"
                            and getattr(scope, "area_id", None) == area_id
                        )
                    ]
                    scoped_data.append((fallback_scope, fallback_data))
                    logger.info(
                        "[v2] area scope 回退: %s:%s → %s:%s",
                        chapter_id,
                        area_id,
                        original_chapter,
                        area_id,
                    )
                except Exception as exc:
                    logger.warning("[v2] area scope 回退读取失败: %s", exc)

        # 6) 加载种子中引用的角色 scope（让角色节点参与扩散）
        known_char_ids = await self._get_character_id_set(world_id)
        loaded_chars = {character_id}  # 已加载的（避免重复）
        extra_character_ids: List[str] = []
        for seed in seed_nodes:
            # 尝试原始形式和去前缀形式
            candidates = [seed]
            for prefix in ("person_", "character_", "location_", "area_"):
                if seed.startswith(prefix):
                    candidates.append(seed[len(prefix):])
            for candidate in candidates:
                if candidate in known_char_ids and candidate not in loaded_chars:
                    loaded_chars.add(candidate)
                    extra_character_ids.append(candidate)
                    break  # 一个 seed 最多加载一个角色 scope

        if extra_character_ids:
            extra_scopes = [
                GraphScope(scope_type="character", character_id=candidate)
                for candidate in extra_character_ids
            ]
            extra_results = await asyncio.gather(
                *(self.graph_store.load_graph_v2(world_id, scope) for scope in extra_scopes),
                return_exceptions=True,
            )
            for scope, result in zip(extra_scopes, extra_results):
                if isinstance(result, Exception):
                    logger.warning("[v2] 额外角色图谱读取失败 scope=%s: %s", scope, result)
                    continue
                scoped_data.append((scope, result))

        if len(loaded_chars) > 1:
            logger.info("[v2] 记忆召回：额外加载 %d 个角色 scope: %s",
                        len(loaded_chars) - 1, loaded_chars - {character_id})

        # 7) 注入好感度动态边权重
        merged = MemoryGraph.from_multi_scope(scoped_data)
        await self._inject_disposition_edges(world_id, character_id, merged)

        logger.info(
            "[v2] 记忆图谱合并: %d 个 scope, %d 节点, %d 边",
            len(scoped_data),
            len(merged.graph.nodes),
            len(merged.graph.edges),
        )

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
        logger.info(
            "[v2] 记忆种子: original=%s → valid=%s (expanded %d, matched %d)",
            seed_nodes, valid_seeds, len(expanded_seeds), len(valid_seeds),
        )
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
        logger.info(
            "[v2] 扩散结果: %d 个激活节点, 子图 %d 节点 %d 边",
            len(activated),
            len(subgraph_graph.graph.nodes),
            len(subgraph_graph.graph.edges),
        )
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

    async def _run_curation_pipeline(
        self,
        world_id: str,
        player_input: str,
        intent: Any,
        memory_result: Any,
        flash_results: List[FlashResponse],
        context: Dict[str, Any],
        party: Any,
        skip_teammate: bool,
        analysis_memory_seeds: List[str],
        base_context: Dict[str, Any],
        chapter_id: Optional[str],
        area_id: Optional[str],
        teammate_recall_tasks: Dict[str, asyncio.Task],
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, str]]:
        """并行执行玩家/队友 curation。"""

        async def _curate_player() -> Optional[Dict[str, Any]]:
            if not memory_result or not getattr(memory_result, "activated_nodes", None):
                return None
            return await self.flash_cpu.curate_context(
                player_input=player_input,
                intent=intent,
                memory_result=memory_result,
                flash_results=flash_results,
                context=context,
            )

        async def _curate_all_teammates() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
            if not party or skip_teammate:
                return {}, {}
            active = party.get_active_members()
            if not active:
                return {}, {}

            async def _curate_one(member):
                char_id = member.character_id
                task = teammate_recall_tasks.get(char_id)
                if task:
                    mem = await task
                else:
                    seeds = self._build_effective_seeds(
                        analysis_memory_seeds,
                        base_context,
                        character_id=char_id,
                    )
                    mem = await self._recall_memory(
                        world_id,
                        char_id,
                        seeds,
                        intent_type=intent.intent_type.value,
                        chapter_id=chapter_id,
                        area_id=area_id,
                    )
                if not mem or not getattr(mem, "activated_nodes", None):
                    return char_id, None, ""

                view = dict(context)
                view["active_character_id"] = char_id
                curated = await self.flash_cpu.curate_context(
                    player_input=player_input,
                    intent=intent,
                    memory_result=mem,
                    flash_results=flash_results,
                    context=view,
                )
                summary = self._summarize_memory(mem)
                return char_id, curated, summary

            results = await asyncio.gather(
                *(_curate_one(member) for member in active),
                return_exceptions=True,
            )
            packages: Dict[str, Dict[str, Any]] = {}
            summaries: Dict[str, str] = {}
            for result in results:
                if isinstance(result, Exception):
                    logger.error("[v2] 队友上下文编排失败: %s", result, exc_info=True)
                    continue
                cid, cur, summ = result
                if isinstance(cur, dict) and cur:
                    packages[cid] = cur
                if isinstance(summ, str) and summ:
                    summaries[cid] = summ
            return packages, summaries

        player_curated, teammate_payload = await asyncio.gather(
            _curate_player(),
            _curate_all_teammates(),
        )
        teammate_packages, teammate_summaries = teammate_payload
        return player_curated, teammate_packages, teammate_summaries

    @staticmethod
    def _consume_background_task_exception(task: asyncio.Task) -> None:
        """避免后台任务在上层异常退出时产生未消费异常告警。"""
        try:
            task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            return

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
        refresh_time = bool(ops & {FlashOperation.UPDATE_TIME, FlashOperation.NAVIGATE})
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

    @classmethod
    def _compact_agentic_value(
        cls,
        value: Any,
        *,
        depth: int = 0,
        max_depth: int = 3,
        max_items: int = 6,
        max_str_len: int = 220,
    ) -> Any:
        """压缩工具参数/结果，避免前端 trace 负载过大。"""
        if depth >= max_depth:
            return "<truncated>"

        if isinstance(value, (int, float, bool)) or value is None:
            return value

        if isinstance(value, str):
            return value if len(value) <= max_str_len else (value[:max_str_len] + "...")

        if isinstance(value, list):
            compacted = [
                cls._compact_agentic_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_str_len=max_str_len,
                )
                for item in value[:max_items]
            ]
            if len(value) > max_items:
                compacted.append(f"...(+{len(value) - max_items})")
            return compacted

        if isinstance(value, dict):
            compacted: Dict[str, Any] = {}
            items = list(value.items())
            for key, item in items[:max_items]:
                key_str = str(key)
                lower_key = key_str.lower()
                if (
                    "base64" in lower_key
                    or lower_key in {"subgraph", "recalled_memory", "graph_data"}
                ):
                    compacted[key_str] = "<omitted>"
                    continue
                compacted[key_str] = cls._compact_agentic_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_str_len=max_str_len,
                )
            if len(items) > max_items:
                compacted["..."] = f"+{len(items) - max_items} keys"
            return compacted

        text = str(value)
        return text if len(text) <= max_str_len else (text[:max_str_len] + "...")

    def _build_agentic_trace_payload(self, agentic_result: Any) -> Dict[str, Any]:
        """构建可视化用的 agentic trace 数据。"""
        tool_calls = list(getattr(agentic_result, "tool_calls", []) or [])
        timeline: List[Dict[str, Any]] = []
        failed = 0
        for idx, call in enumerate(tool_calls, start=1):
            success = bool(getattr(call, "success", False))
            if not success:
                failed += 1
            timeline.append(
                {
                    "index": idx,
                    "name": str(getattr(call, "name", "")),
                    "success": success,
                    "duration_ms": int(getattr(call, "duration_ms", 0) or 0),
                    "error": str(getattr(call, "error", "") or ""),
                    "args": self._compact_agentic_value(getattr(call, "args", {}) or {}),
                    "result": self._compact_agentic_value(getattr(call, "result", {}) or {}),
                }
            )

        usage = getattr(agentic_result, "usage", {}) or {}
        thought_summary = str(getattr(agentic_result, "thinking_summary", "") or "").strip()
        if len(thought_summary) > 4000:
            thought_summary = thought_summary[:4000] + "..."

        return {
            "thinking": {
                "level": settings.admin_flash_thinking_level,
                "summary": thought_summary,
                "thoughts_token_count": int(usage.get("thoughts_token_count", 0) or 0),
                "output_token_count": int(usage.get("output_token_count", 0) or 0),
                "total_token_count": int(usage.get("total_token_count", 0) or 0),
                "finish_reason": str(getattr(agentic_result, "finish_reason", "") or ""),
            },
            "tool_calls": timeline,
            "stats": {
                "count": len(timeline),
                "failed": failed,
                "success": len(timeline) - failed,
            },
        }

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
            except asyncio.CancelledError:
                raise
            except MCPServiceUnavailableError:
                raise
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

        if intent_type == IntentType.TEAM_INTERACTION:
            teammates = (context or {}).get("teammates") or []
            # 双匹配：character_id + name（大小写归一）
            teammate_keys = set()
            for t in teammates:
                if isinstance(t, dict):
                    cid = (t.get("character_id") or "").lower()
                    cname = (t.get("name") or "").lower()
                    if cid:
                        teammate_keys.add(cid)
                    if cname:
                        teammate_keys.add(cname)

            target = (intent.target or "").strip()
            target_lower = target.lower()

            # 支持多目标: "A, B" / "A、B" / "A和B"
            target_parts = [p.strip().lower() for p in re.split(r'[,，、]|和', target) if p.strip()]
            if not target_parts:
                target_parts = [target_lower] if target_lower else []

            if target_parts and any(p not in teammate_keys for p in target_parts):
                # 至少一个目标不在队伍中 → fallback npc_dialogue
                logger.warning(
                    "team_interaction 目标 '%s' 不在队伍中，fallback 到 npc_dialogue",
                    target,
                )
                return [FlashRequest(
                    operation=FlashOperation.NPC_DIALOGUE,
                    parameters={
                        "npc_id": target,
                        "message": intent.raw_input,
                    },
                )]

            if not target:
                # target 为空，检查 active_npc + 招募关键词
                recruit_keywords = {"加入", "入队", "同行", "招募", "组队", "一起", "队伍", "同伴"}
                has_recruit_intent = any(kw in (intent.raw_input or "") for kw in recruit_keywords)
                active_npc = (context or {}).get("active_npc")
                if has_recruit_intent and active_npc:
                    logger.warning(
                        "team_interaction target 为空但检测到招募意图，fallback 到 active_npc '%s'",
                        active_npc,
                    )
                    return [FlashRequest(
                        operation=FlashOperation.NPC_DIALOGUE,
                        parameters={
                            "npc_id": active_npc,
                            "message": intent.raw_input,
                        },
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
            "start_combat_v3",
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
        actions_payload = await self.flash_cpu.call_combat_tool(
            "get_available_actions_v3",
            {"combat_id": combat_id},
        )
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
            "execute_action_v3",
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
            # Sync combat results (HP/XP/gold) to player character
            final_result = payload.get("final_result") or {}
            await self.flash_cpu.sync_combat_result_to_character(
                world_id, session_id, {"final_result": final_result},
            )
            await self.flash_cpu._apply_delta(world_id, session_id, self.flash_cpu._build_state_delta("end_combat", {"combat_id": None}))
            return {
                "type": "combat",
                "phase": "end",
                "result": payload.get("final_result"),
                "narration": payload.get("final_result", {}).get("summary", "战斗结束。"),
            }

        actions_payload = await self.flash_cpu.call_combat_tool(
            "get_available_actions_v3",
            {"combat_id": combat_id},
        )
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
        self._ensure_fixed_world(world_id)
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
            # Derive phase from state
            has_character = bool(await self.character_store.get_character(world_id, session_id))
            if not has_character:
                phase = GamePhase.CHARACTER_CREATION
            elif state.combat_id:
                phase = GamePhase.COMBAT
            elif state.active_dialogue_npc:
                phase = GamePhase.DIALOGUE
            else:
                phase = GamePhase.IDLE
            return self.AdminContextView(
                world_id=world_id,
                session_id=session_id,
                phase=phase,
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
    ) -> Optional[Dict[str, Any]]:
        """获取队伍信息"""
        party = await self.party_service.get_party(world_id, session_id)
        if not party:
            return None
        return party.model_dump(mode="json")

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

    # ==================== Private Chat ====================

    async def process_private_chat_stream(
        self,
        world_id: str,
        session_id: str,
        target_character_id: str,
        player_input: str,
    ):
        """
        私聊流式处理 -- 直接与角色对话，跳过 Flash 分析和 GM 叙述。

        Yields:
            dict: SSE 事件 (chat_start/chat_chunk/chat_end/error)
        """
        try:
            # Phase 守卫：角色必须存在
            pc = await self.character_store.get_character(world_id, session_id)
            if not pc:
                yield {"type": "error", "error": "请先创建角色后再开始冒险。"}
                return

            # 1. 获取或创建 NPC 实例
            instance = await self.instance_manager.get_or_create(
                target_character_id, world_id
            )
            character_name = (
                instance.config.name if instance.config else target_character_id
            )

            yield {
                "type": "chat_start",
                "character_id": target_character_id,
                "name": character_name,
            }

            # 2. 将玩家消息注入实例上下文
            instance.context_window.add_message("user", player_input)

            # 3. 构建 prompt：系统提示 + 对话历史
            system_prompt = instance.context_window.get_system_prompt()
            recent_messages = instance.context_window.get_all_messages()
            conversation_lines = []
            for msg in recent_messages[-20:]:
                role_label = "玩家" if msg.role == "user" else character_name
                conversation_lines.append(f"{role_label}: {msg.content}")
            conversation_text = "\n".join(conversation_lines)

            prompt = f"""{system_prompt}

---
以下是最近的对话：
{conversation_text}

---
请以 {character_name} 的身份，直接回复玩家。保持角色性格和说话风格。只输出对话内容，不要加任何前缀或标记。"""

            # 4. 流式生成
            from app.services.llm_service import LLMService
            llm = LLMService()
            full_text = ""

            async for chunk in llm.generate_simple_stream(prompt):
                if chunk["type"] == "answer":
                    full_text += chunk["text"]
                    yield {
                        "type": "chat_chunk",
                        "text": chunk["text"],
                    }
                elif chunk["type"] == "error":
                    yield {"type": "error", "error": chunk["text"]}
                    return

            # 5. 将角色回复写回上下文
            instance.context_window.add_message("assistant", full_text)

            yield {
                "type": "chat_end",
                "full_text": full_text,
            }

            # 6. 检查是否需要图谱化
            try:
                await self.instance_manager.maybe_graphize_instance(
                    world_id, target_character_id
                )
            except Exception as e:
                logger.debug("[私聊] 图谱化检查失败: %s", e)

        except Exception as exc:
            logger.exception("[私聊] 处理失败: %s", exc)
            yield {"type": "error", "error": str(exc)}
