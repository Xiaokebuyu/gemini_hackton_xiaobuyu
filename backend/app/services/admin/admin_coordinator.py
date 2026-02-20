"""
Admin coordinator - entrypoint for centralized admin layer.

V4 Runtime Pipeline 架构：
1. ContextAssembler 组装上下文
2. PipelineOrchestrator 驱动 Agentic 会话
3. 后处理（队友响应 + 历史记录 + 持久化）
"""
from __future__ import annotations

import asyncio
import logging
import re
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
    UpdateSceneRequest,
)
from app.models.admin_protocol import (
    CoordinatorResponse,
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
from app.models.graph_scope import GraphScope
from app.services.character_store import CharacterStore
from app.services.character_service import CharacterService
from app.services.admin.recall_orchestrator import RecallOrchestrator


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
    ) -> None:
        self._session_store = session_store or GameSessionStore()
        self._state_manager = state_manager or StateManager()
        self.event_service = event_service or AdminEventService()
        self.graph_store = graph_store or GraphStore()
        self.flash_service = flash_service or FlashService(self.graph_store)
        self.narrative_service = narrative_service or NarrativeService(self._session_store)
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
            flash_cpu=self.flash_cpu,
            graph_store=self.graph_store,
        )

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
        self.teammate_response_service.recall_orchestrator = self.recall_orchestrator

        # V4 Pipeline Orchestrator（唯一处理管线）
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator
        self._pipeline_orchestrator = PipelineOrchestrator(
            flash_cpu=self.flash_cpu,
            party_service=self.party_service,
            narrative_service=self.narrative_service,
            graph_store=self.graph_store,
            teammate_response_service=self.teammate_response_service,
            session_history_manager=self.session_history_manager,
            character_store=self.character_store,
            state_manager=self._state_manager,
            world_runtime=self._world_runtime,
            recall_orchestrator=self.recall_orchestrator,
            memory_graphizer=self.memory_graphizer,
        )
        logger.info("[AdminCoordinator] V4 PipelineOrchestrator 已初始化")

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

    @staticmethod
    def _ensure_fixed_world(world_id: str) -> None:
        expected = settings.fixed_world_id
        if world_id != expected:
            raise ValueError(
                f"unsupported world_id='{world_id}', this environment only supports '{expected}'"
            )

    # ==================== V4 Runtime Pipeline ====================

    async def process_player_input_v3(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: Optional[str] = None,
    ) -> CoordinatorResponse:
        """V4 管线主入口 — 委托给 PipelineOrchestrator。"""
        self._ensure_fixed_world(world_id)
        return await self._pipeline_orchestrator.process(
            world_id=world_id,
            session_id=session_id,
            player_input=player_input,
            is_private=is_private,
            private_target=private_target,
        )

    async def process_player_input_v3_stream(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: Optional[str] = None,
    ):
        """V4 SSE 流：实时推送工具调用事件 + 完整 trace。"""
        self._ensure_fixed_world(world_id)
        try:
            yield {"type": "phase", "phase": "thinking"}

            event_queue: asyncio.Queue = asyncio.Queue()

            # Run pipeline in background task so we can stream tool-call events
            pipeline_task = asyncio.create_task(
                self._pipeline_orchestrator.process(
                    world_id=world_id,
                    session_id=session_id,
                    player_input=player_input,
                    is_private=is_private,
                    private_target=private_target,
                    event_queue=event_queue,
                )
            )

            # Stream real-time tool-call events while pipeline runs
            while not pipeline_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.3)
                    yield event
                except asyncio.TimeoutError:
                    continue

            # Drain any remaining events in the queue
            while not event_queue.empty():
                yield event_queue.get_nowait()

            # Get the pipeline result (may raise)
            response = pipeline_task.result()

            # Extract agentic trace from metadata
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

            # NPC 对话 + 队友响应已在管线内通过 event_queue 即时推送，
            # 不再重复推送。complete 事件中的 npc_responses / teammate_responses
            # 字段作为 fallback 保证前端数据完整性。

            yield {
                "type": "complete",
                "state_delta": response.state_delta.model_dump() if response.state_delta else None,
                "metadata": response.metadata,
                "available_actions": response.available_actions,
                "story_events": response.story_events,
                "npc_responses": response.npc_responses,
                "teammate_responses": response.teammate_responses,
                "pacing_action": response.pacing_action,
                "chapter_info": response.chapter_info,
                "image_data": response.image_data,
                "agentic_trace": agentic_trace,
            }
        except ValueError as exc:
            yield {"type": "error", "error": str(exc)}
        except Exception as exc:
            logger.exception("[v4-stream] 处理失败: %s", exc)
            yield {"type": "error", "error": str(exc)}

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
            # Note: V3 legacy path — no session available, uses character_store fallback
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
        admin_state = await self._world_runtime.start_session(
            world_id=world_id,
            session_id=session_id,
            participants=participants,
            known_characters=known_characters,
            character_locations=character_locations,
            starting_location=starting_location,
            starting_time=starting_time,
        )

        # A3: 自动创建空队伍（队友通过游戏内邀请加入）
        try:
            await self.party_service.get_or_create_party(
                world_id, admin_state.session_id, leader_id="player"
            )
        except Exception as exc:
            logger.warning("[start_session] 队伍自动创建失败: %s", exc)

        return admin_state

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
