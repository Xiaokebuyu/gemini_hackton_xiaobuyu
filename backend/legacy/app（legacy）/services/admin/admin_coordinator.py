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
from datetime import datetime

logger = logging.getLogger(__name__)
from typing import Any, List, Optional, Dict

from app.config import settings
from app.exceptions import CATCHABLE_EXCEPTIONS
from app.services.admin.state_manager import StateManager
from app.services.session_history import SessionHistoryManager
from app.models.game import (
    CombatResolveRequest,
    CombatResolveResponse,
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
from app.services.admin.event_service import AdminEventService
from app.world.narrative.narrative_service import NarrativeService
from app.services.passerby_service import PasserbyService
from app.services.admin.world_runtime import AdminWorldRuntime
from app.world.party.party_service import PartyService
from app.agentic.teammate.response_service import TeammateResponseService

from app.models.state_delta import GameState


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
        state_manager: Optional[StateManager] = None,
        event_service: Optional[AdminEventService] = None,
        narrative_service: Optional[NarrativeService] = None,
        passerby_service: Optional[PasserbyService] = None,
        world_runtime: Optional[AdminWorldRuntime] = None,
        party_service: Optional[PartyService] = None,
        teammate_response_service: Optional[TeammateResponseService] = None,
        **kwargs,  # 兼容旧调用方传入的 flash_cpu= / session_store= 等已废弃参数
    ) -> None:
        # ── 基础服务（无相互依赖） ──
        self._state_manager = state_manager or StateManager()
        self.event_service = event_service or AdminEventService()
        self.narrative_service = narrative_service or NarrativeService()
        self.passerby_service = passerby_service or PasserbyService()

        # ── 世界运行时 ──
        self._world_runtime = world_runtime or AdminWorldRuntime(
            state_manager=self._state_manager,
            narrative_service=self.narrative_service,
        )

        # ── 共享 InstanceManager（队友 + NPC 实例化） ──
        from app.world.npc.instance_manager import InstanceManager
        self.instance_manager = InstanceManager(
            max_instances=settings.instance_pool_max_instances,
            context_window_size=settings.instance_pool_context_window_size,
            graphize_threshold=settings.instance_pool_graphize_threshold,
            keep_recent_tokens=settings.instance_pool_keep_recent_tokens,
        )

        # ── 队伍 ──
        self.party_service = party_service or PartyService()

        # ── LLM 服务 ──
        from app.services.llm_service import LLMService
        self.llm_service = LLMService()

        # ── 队友响应 ──
        self.teammate_response_service = teammate_response_service or TeammateResponseService(
            instance_manager=self.instance_manager,
        )

        # ── 会话历史 + 图谱化 ──
        self.session_history_manager = SessionHistoryManager(
            max_tokens=settings.session_history_max_tokens,
            graphize_threshold=settings.session_history_graphize_threshold,
            keep_recent_tokens=settings.session_history_keep_recent_tokens,
        )
        # ── V4 Pipeline Orchestrator（主游戏循环） ──
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator
        from app.services.image_generation_service import ImageGenerationService
        self._pipeline_orchestrator = PipelineOrchestrator(
            party_service=self.party_service,
            narrative_service=self.narrative_service,
            teammate_response_service=self.teammate_response_service,
            session_history_manager=self.session_history_manager,
            state_manager=self._state_manager,
            llm_service=self.llm_service,
            image_service=ImageGenerationService(),
        )
        # ── NPC 交互协调器（interact + private_chat） ──
        from app.services.admin.npc_interaction_coordinator import NPCInteractionCoordinator
        self._npc_interaction_coord = NPCInteractionCoordinator(
            llm_service=self.llm_service,
            instance_manager=self.instance_manager,
            teammate_response_service=self.teammate_response_service,
            state_manager=self._state_manager,
            party_service=self.party_service,
            narrative_service=self.narrative_service,
            session_history_manager=self.session_history_manager,
        )
        # ── 域协调器（提取自 AdminCoordinator） ──
        from app.services.admin.combat_coordinator import CombatCoordinator
        from app.services.admin.narrative_coordinator import NarrativeCoordinator
        self._combat = CombatCoordinator(state_manager=self._state_manager)
        self._narrative_coord = NarrativeCoordinator(
            state_manager=self._state_manager,
            party_service=self.party_service,
            session_history_manager=self.session_history_manager,
            instance_manager=self.instance_manager,
            world_runtime=self._world_runtime,
            narrative_service=self.narrative_service,
            llm_service=self.llm_service,
        )
        logger.info("[AdminCoordinator] V4 PipelineOrchestrator + 域协调器已初始化")

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
        from google.cloud import firestore
        from app.config import settings
        db = firestore.Client(database=settings.firestore_database)
        worlds_ref = db.collection("worlds")
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
        """获取会话聊天历史（仅运行时内存）。"""
        in_memory = self.session_history_manager.get(world_id, session_id)
        if in_memory:
            return in_memory.get_recent_messages_for_api(limit)
        return []

    # ==================== GameLoop compatible methods ====================

    async def create_session(self, world_id: str, request: CreateSessionRequest) -> CreateSessionResponse:
        from app.runtime.session_runtime import SessionRuntime
        state = await SessionRuntime.create(world_id, request.session_id, request.participants)
        return CreateSessionResponse(session=state)

    async def get_session(self, world_id: str, session_id: str) -> GameSessionState | None:
        from app.runtime.session_runtime import SessionRuntime
        return await SessionRuntime.get_session_meta(world_id, session_id)

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
        # 优先使用可注入 session store（测试兼容），否则回退 SessionRuntime
        if hasattr(self, '_session_store') and self._session_store is not None:
            sessions = await self._session_store.list_sessions(world_id, user_id, limit=limit)
        else:
            from app.runtime.session_runtime import SessionRuntime
            sessions = await SessionRuntime.list_sessions(
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

            # 加载队伍信息（内存缓存，冷启动返回 0）
            party_member_count = 0
            party_members_names: List[str] = []
            try:
                party_obj = await self.party_service.get_party(world_id, session.session_id)
                if party_obj:
                    for m in party_obj.get_active_members():
                        party_member_count += 1
                        party_members_names.append(m.name)
            except (OSError, RuntimeError) as exc:
                logger.warning("[list_recoverable] 队伍信息加载失败: %s", exc)

            # 角色创建状态：优先 metadata.has_character，必要时以 SessionRuntime.player 校验。
            metadata_has_character = metadata.get("has_character")
            has_character = metadata_has_character is True
            should_verify_runtime = metadata_has_character is not True and not (
                hasattr(self, "_session_store") and self._session_store is not None
            )
            if should_verify_runtime:
                try:
                    from app.runtime.session_runtime import SessionRuntime
                    runtime_session = await SessionRuntime.get_or_restore(world_id, session.session_id)
                    has_character = runtime_session.player is not None
                except CATCHABLE_EXCEPTIONS as exc:
                    logger.debug(
                        "[list_recoverable] 运行时角色判定失败，回退 metadata: session=%s err=%s",
                        session.session_id,
                        exc,
                    )

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
        from google.cloud import firestore as _fs
        from datetime import datetime
        db = _fs.Client(database=settings.firestore_database)
        ref = db.collection("worlds").document(world_id).collection("sessions").document(session_id)
        ref.update({
            "current_scene": request.scene.model_dump(),
            "status": "scene",
            "updated_at": datetime.now(),
        })
        from app.runtime.session_runtime import SessionRuntime
        state = await SessionRuntime.get_session_meta(world_id, session_id)
        if not state:
            raise ValueError("session not found")
        return state

    async def start_combat(self, world_id, session_id, request) -> CombatStartResponse:
        return await self._combat.start_combat(world_id, session_id, request)

    async def resolve_combat(self, world_id, session_id, request) -> CombatResolveResponse:
        return await self._combat.resolve_combat(world_id, session_id, request)

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

    async def process_interact_stream(
        self,
        world_id: str,
        session_id: str,
        npc_id: str,
        player_input: str,
    ):
        """NPC 直接交互 SSE 流。"""
        self._ensure_fixed_world(world_id)
        async for event in self._npc_interaction_coord.process_interact_stream(
            world_id=world_id,
            session_id=session_id,
            npc_id=npc_id,
            player_input=player_input,
        ):
            yield event

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
        except CATCHABLE_EXCEPTIONS as exc:
            logger.exception("[v4-stream] 处理失败: %s", exc)
            yield {"type": "error", "error": str(exc)}

    async def resume_session(self, world_id, session_id, generate_narration=True):
        return await self._narrative_coord.resume_session(world_id, session_id, generate_narration)

    async def generate_opening_narration(self, world_id, session_id) -> str:
        return await self._narrative_coord.generate_opening_narration(world_id, session_id)

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


    async def trigger_combat(self, world_id, session_id, enemies, player_state,
                             combat_description="", environment=None) -> Dict[str, Any]:
        return await self._combat.trigger_combat(
            world_id, session_id, enemies, player_state, combat_description, environment)

    async def execute_combat_action(self, world_id, session_id, action_id) -> Dict[str, Any]:
        return await self._combat.execute_combat_action(world_id, session_id, action_id)

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
        except (OSError, RuntimeError) as exc:
            logger.error("[start_session] 队伍自动创建失败: %s", exc)

        return admin_state

    def get_context(self, world_id: str, session_id: str):
        # Prefer in-memory admin state
        state = self._state_manager.get_state_sync(world_id, session_id)
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
        state = await self._state_manager.get_state(world_id, session_id)
        if state:
            known_chars = state.metadata.get("known_characters", []) if state.metadata else []
            # 正确性优先：角色存在性以 SessionRuntime.player 为准；metadata 仅作失败兜底。
            has_character = False
            try:
                from app.runtime.session_runtime import SessionRuntime
                runtime_session = await SessionRuntime.get_or_restore(world_id, session_id)
                has_character = runtime_session.player is not None
            except CATCHABLE_EXCEPTIONS as exc:
                logger.warning(
                    "[get_context_async] SessionRuntime 角色判定失败，回退 metadata: session=%s err=%s",
                    session_id,
                    exc,
                )
                has_character = bool((state.metadata or {}).get("has_character"))
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
        state = await self._state_manager.get_state(world_id, session_id)
        if state and state.game_time:
            return state.game_time.model_dump()
        return {}

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

    # ==================== Private Chat ====================

    async def process_private_chat_stream(
        self,
        world_id: str,
        session_id: str,
        target_character_id: str,
        player_input: str,
    ):
        """私聊流式处理 — 委托到 PipelineOrchestrator。

        Yields:
            dict: SSE 事件 (interact_start/npc_response/dialogue_options/complete/error)
        """
        async for event in self._npc_interaction_coord.process_private_chat_stream(
            world_id=world_id,
            session_id=session_id,
            npc_id=target_character_id,
            player_input=player_input,
        ):
            yield event
