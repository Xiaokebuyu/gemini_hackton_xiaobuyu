"""
Admin coordinator - entrypoint for centralized admin layer.

Pro-First 架构（优化版）：
1. Flash 一次性分析 (analyze_and_plan)
2. Flash 执行操作 (execute_request)
3. Pro 生成叙述 (narrate)
"""
from __future__ import annotations

import asyncio
import warnings
from dataclasses import dataclass
from typing import Any, List, Optional, Dict

from app.config import settings
from app.services.admin.flash_cpu_service import FlashCPUService
from app.services.admin.pro_dm_service import ProDMService
from app.services.admin.state_manager import StateManager
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
from app.services.pro_service import ProService
from app.services.admin.world_runtime import AdminWorldRuntime
from app.services.party_service import PartyService
from app.services.party_store import PartyStore
from app.services.teammate_response_service import TeammateResponseService
from app.services.teammate_visibility_manager import TeammateVisibilityManager


class AdminCoordinator:
    """Coordinator that exposes legacy GM API while preparing new admin flow."""

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
        pro_service: Optional[ProService] = None,
        narrative_service: Optional[NarrativeService] = None,
        passerby_service: Optional[PasserbyService] = None,
        world_runtime: Optional[AdminWorldRuntime] = None,
        flash_cpu: Optional[FlashCPUService] = None,
        pro_dm: Optional[ProDMService] = None,
        party_service: Optional[PartyService] = None,
        teammate_response_service: Optional[TeammateResponseService] = None,
        visibility_manager: Optional[TeammateVisibilityManager] = None,
    ) -> None:
        self._session_store = session_store or GameSessionStore()
        self._state_manager = state_manager or StateManager()
        self.event_service = event_service or AdminEventService()
        self.graph_store = graph_store or GraphStore()
        self.flash_service = flash_service or FlashService(self.graph_store)
        self.pro_service = pro_service or ProService(self.graph_store, self.flash_service)
        self.narrative_service = narrative_service or NarrativeService(self._session_store)
        self.passerby_service = passerby_service or PasserbyService()
        self._world_runtime = world_runtime or AdminWorldRuntime(
            state_manager=self._state_manager,
            session_store=self._session_store,
            narrative_service=self.narrative_service,
            event_service=self.event_service,
        )
        self.flash_cpu = flash_cpu or FlashCPUService(
            state_manager=self._state_manager,
            world_runtime=self._world_runtime,
            session_store=self._session_store,
            event_service=self.event_service,
            pro_service=self.pro_service,
            narrative_service=self.narrative_service,
            passerby_service=self.passerby_service,
        )
        self.pro_dm = pro_dm or ProDMService()

        # 队友系统
        self.party_store = PartyStore()
        self.party_service = party_service or PartyService(self.graph_store, self.party_store)
        self.teammate_response_service = teammate_response_service or TeammateResponseService()
        self.visibility_manager = visibility_manager or TeammateVisibilityManager()

    @dataclass
    class AdminContextView:
        world_id: str
        session_id: str
        phase: GamePhase
        game_day: int
        current_scene: Any = None
        current_npc: Optional[str] = None
        known_characters: list = None

    # ==================== GameLoop compatible methods ====================

    async def create_session(self, world_id: str, request: CreateSessionRequest) -> CreateSessionResponse:
        state = await self._session_store.create_session(world_id, request.session_id, request.participants)
        return CreateSessionResponse(session=state)

    async def get_session(self, world_id: str, session_id: str) -> GameSessionState | None:
        return await self._session_store.get_session(world_id, session_id)

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

    # ==================== GM compatible methods ====================

    async def process_player_input(self, world_id: str, session_id: str, player_input: str, input_type=None, mode=None):
        warnings.warn(
            "AdminCoordinator.process_player_input is deprecated; use process_player_input_v2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        flash_result = await self.flash_cpu.process_player_input(
            world_id=world_id,
            session_id=session_id,
            player_input=player_input,
            input_type=input_type,
            mode=mode,
        )
        if isinstance(flash_result, dict):
            if not flash_result.get("response"):
                pro_response = await self.pro_dm.narrate_legacy(player_input, flash_result, flash_result.get("context"))
                flash_result["response"] = pro_response.narration
                flash_result["speaker"] = pro_response.speaker
                flash_result.setdefault("metadata", {}).update(pro_response.metadata)
        return flash_result

    # ==================== Pro-First 架构 (v2) ====================

    async def process_player_input_v2(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
    ) -> CoordinatorResponse:
        """
        Pro-First 架构的玩家输入处理流程。

        流程：
        1. 收集基础上下文
        2. Flash 一次性分析 (intent + operations + memory seeds)
        3. 并行召回记忆 + 顺序执行操作
        4. 组装完整上下文
        5. 导航后同步队友位置
        6. Pro 生成叙述
        7. 队友响应
        8. 分发事件到队友图谱

        Args:
            world_id: 世界ID
            session_id: 会话ID
            player_input: 玩家输入

        Returns:
            CoordinatorResponse: 完整响应
        """
        # 0. 获取队伍信息
        party = await self.party_service.get_party(world_id, session_id)

        # 1. 收集上下文
        base_context = await self._build_context(world_id, session_id)

        # 2. Flash 一次性分析
        print(f"[Coordinator] 步骤2: Flash 分析...")
        analysis = await self.flash_cpu.analyze_and_plan(player_input, base_context)
        intent = analysis.intent
        print(f"[Coordinator] 意图: {intent.intent_type.value}, 目标: {intent.target}")

        # 3. 并行召回记忆 + 顺序执行操作
        print(f"[Coordinator] 步骤3: 执行操作...")
        memory_task = None
        if analysis.memory_seeds:
            memory_task = asyncio.create_task(
                self._recall_memory(world_id, "player", analysis.memory_seeds)
            )

        flash_requests = analysis.operations or self._generate_default_flash_requests(intent, base_context)
        flash_results = await self._execute_flash_requests(
            world_id, session_id, flash_requests, generate_narration=False
        )
        print(f"[Coordinator] Flash 结果: {len(flash_results)} 个")
        for r in flash_results:
            print(f"  - {r.operation}: success={r.success}, error={r.error}")

        memory_result = await memory_task if memory_task else None

        # 4. 组装完整上下文
        context = await self._assemble_context(
            base_context, memory_result, flash_results, world_id, session_id
        )

        # 5. 导航后同步队友位置
        if intent.intent_type == IntentType.NAVIGATION and party:
            print(f"[Coordinator] 步骤5: 同步队友位置...")
            new_location = context.get("location", {}).get("location_id")
            if new_location:
                await self.party_service.sync_locations(
                    world_id, session_id, new_location
                )

        # 6. Pro 生成叙述
        print(f"[Coordinator] 步骤6: 生成叙述...")
        execution_summary = self._build_execution_summary(flash_results)
        pro_response = await self.pro_dm.narrate(
            context=context,
            execution_summary=execution_summary,
            player_input=player_input,
        )
        print(f"[Coordinator] 叙述完成: {pro_response.narration[:50]}...")

        # 7. 队友响应
        teammate_responses = []
        if party and party.get_active_members():
            print(f"[Coordinator] 步骤7: 队友响应 ({len(party.get_active_members())} 个队友)...")
            # 判断是否是显式队友交互（使用 Pro 模型）
            is_explicit_dialogue = intent.intent_type == IntentType.TEAM_INTERACTION

            teammate_result = await self.teammate_response_service.process_round(
                party=party,
                player_input=player_input,
                gm_response=pro_response.narration,
                context=context,
                use_pro_model=is_explicit_dialogue,
            )
            print(f"[Coordinator] 队友响应完成: {len(teammate_result.responses)} 个响应")

            # 转换为字典格式
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
            print(f"[Coordinator] 步骤7: 跳过（无队友）")

        # 8. 分发事件到队友图谱（如果有队友且启用事件共享）
        if party and party.share_events and teammate_responses:
            print(f"[Coordinator] 步骤8: 分发事件...")
            await self._distribute_event_to_party(
                world_id=world_id,
                party=party,
                player_input=player_input,
                gm_response=pro_response.narration,
                context=context,
            )
            print(f"[Coordinator] 事件分发完成")

        # 8. 构建最终响应
        state_delta = None
        for result in flash_results:
            if result.state_delta:
                state_delta = result.state_delta
                break

        available_actions = await self._get_available_actions(world_id, session_id, context)

        return CoordinatorResponse(
            narration=pro_response.narration,
            speaker=pro_response.speaker,
            teammate_responses=teammate_responses,
            available_actions=available_actions,
            state_delta=state_delta,
            metadata={
                "intent_type": intent.intent_type.value,
                "confidence": intent.confidence,
                "teammate_count": len(teammate_responses),
                "analysis_reasoning": analysis.reasoning,
                **pro_response.metadata,
            },
        )

    async def _distribute_event_to_party(
        self,
        world_id: str,
        party: "Party",
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
    ) -> None:
        """分发事件到队友图谱"""
        from app.models.party import Party
        def _format_context_snippet(filtered: Dict[str, Any]) -> str:
            lines = []
            location = filtered.get("location") or {}
            if location:
                loc_name = location.get("location_name") or location.get("location_id")
                if loc_name:
                    lines.append(f"地点: {loc_name}")
            time_info = filtered.get("time") or {}
            if time_info:
                time_text = time_info.get("formatted")
                if not time_text:
                    day = time_info.get("day")
                    hour = time_info.get("hour")
                    minute = time_info.get("minute")
                    if day is not None and hour is not None and minute is not None:
                        time_text = f"第{day}天 {hour:02d}:{minute:02d}"
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

        try:
            parsed_event = await self.event_service.llm_service.parse_event(
                event_description=base_description,
                known_characters=known_characters,
                known_locations=known_locations,
            )
        except Exception as exc:
            print(f"[Coordinator] 事件解析失败: {exc}")
            parsed_event = {
                "participants": ["player"],
                "witnesses": [m.character_id for m in party.get_active_members()],
                "location": location_name,
            }

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

        tasks = []
        for member in party.get_active_members():
            # 检查队友是否应该知道这个事件
            if not self.visibility_manager.should_teammate_know(member, event, party):
                continue

            # 过滤上下文
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

            tasks.append(
                self.event_service.ingest_for_character(
                    world_id=world_id,
                    character_id=member.character_id,
                    event_description=teammate_description,
                    parsed_event=parsed_event,
                    perspective=perspective,
                    game_day=int(game_day),
                )
            )

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    print(f"[Coordinator] 队友事件写入失败: {result}")

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

    async def _build_context(self, world_id: str, session_id: str) -> Dict[str, Any]:
        """构建当前上下文"""
        context = {}

        # 获取位置信息
        try:
            location = await self._world_runtime.get_current_location(world_id, session_id)
            context["location"] = location
            context["available_destinations"] = location.get("available_destinations", [])
            # 添加子地点信息（字段名是 available_sub_locations）
            context["sub_locations"] = location.get("available_sub_locations", [])
        except Exception:
            context["location"] = {}
            context["available_destinations"] = []
            context["sub_locations"] = []

        # 获取时间
        try:
            time_info = await self._world_runtime.get_game_time(world_id, session_id)
            context["time"] = time_info
        except Exception:
            context["time"] = {}

        # 获取状态
        try:
            state = await self._state_manager.get_state(world_id, session_id)
            if state:
                context["state"] = "in_dialogue" if state.active_dialogue_npc else (
                    "combat" if state.combat_id else "exploring"
                )
                context["active_npc"] = state.active_dialogue_npc
            else:
                context["state"] = "exploring"
                context["active_npc"] = None
        except Exception:
            context["state"] = "exploring"
            context["active_npc"] = None

        # 队友信息
        try:
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
        except Exception:
            context["teammates"] = []

        return context

    async def _recall_memory(
        self,
        world_id: str,
        character_id: str,
        seed_nodes: List[str],
    ):
        """召回记忆（异步）"""
        try:
            from app.models.flash import RecallRequest
            recall_req = RecallRequest(
                seed_nodes=seed_nodes,
                include_subgraph=True,
                use_subgraph=True,
                subgraph_depth=2,
            )
            return await self.flash_service.recall_memory(
                world_id=world_id,
                character_id=character_id,
                request=recall_req,
            )
        except Exception as exc:
            print(f"[Coordinator] 记忆召回失败: {exc}")
            return None

    async def _assemble_context(
        self,
        base_context: Dict[str, Any],
        memory_result: Any,
        flash_results: List[FlashResponse],
        world_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """组装完整上下文（基础 + 最新状态 + 记忆摘要）"""
        context = dict(base_context or {})
        context.update(await self._build_context(world_id, session_id))

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
        """将记忆结果转为自然语言摘要"""
        activated = getattr(memory_result, "activated_nodes", {}) or {}
        if not activated:
            return ""

        node_lookup = {}
        subgraph = getattr(memory_result, "subgraph", None)
        if subgraph and getattr(subgraph, "nodes", None):
            node_lookup = {node.id: node for node in subgraph.nodes}

        summaries = []
        for node_id, _score in sorted(activated.items(), key=lambda x: x[1], reverse=True)[:5]:
            node = node_lookup.get(node_id)
            if node:
                summaries.append(f"[{node.type}] {node.name}")
            else:
                summaries.append(node_id)

        return "\n".join(summaries)

    def _build_execution_summary(self, flash_results: List[FlashResponse]) -> str:
        """构建执行结果摘要"""
        if not flash_results:
            return "无系统操作执行"

        summaries = []
        for result in flash_results:
            op = result.operation.value if result.operation else "unknown"
            if result.success:
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

    async def narrate_state_change(
        self,
        world_id: str,
        session_id: str,
        change_type: str,
        change_details: Dict[str, Any],
    ) -> str:
        """状态变更后的统一叙述（供 navigate/time/dialogue 等端点使用）"""
        context = await self._build_context(world_id, session_id)
        summary = self._build_change_summary(change_type, change_details)
        response = await self.pro_dm.narrate(context=context, execution_summary=summary)
        return response.narration

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

        print(f"[Flash] 请求数量: {len(flash_requests)} 个")
        if not flash_requests:
            return results

        # 执行每个请求
        for i, request in enumerate(flash_requests):
            print(f"[Flash] 执行请求 {i+1}: {request.operation.value} params={request.parameters}")
            try:
                result = await self.flash_cpu.execute_request(
                    world_id, session_id, request, generate_narration=generate_narration
                )
                print(f"[Flash] 请求 {i+1} 完成: success={result.success}")
                results.append(result)
            except Exception as e:
                print(f"[Flash] 请求 {i+1} 异常: {e}")
                results.append(FlashResponse(
                    success=False,
                    operation=request.operation,
                    error=str(e),
                ))

        return results

    def _resolve_sub_location_id(
        self,
        target: str,
        context: Dict[str, Any] = None,
    ) -> str:
        """将子地点名称（中文或英文）解析为 sub_location_id"""
        if not context or "sub_locations" not in context:
            return target  # 没有上下文，直接返回原值

        sub_locations = context.get("sub_locations", [])
        target_lower = target.lower()

        for sub_loc in sub_locations:
            # sub_loc 格式: {"id": "tavern", "name": "酒馆", ...}
            if isinstance(sub_loc, dict):
                loc_id = sub_loc.get("id", "")
                loc_name = sub_loc.get("name", "")
                # 匹配 ID 或名称
                if target_lower == loc_id.lower() or target == loc_name:
                    return loc_id
            elif isinstance(sub_loc, str):
                # 如果是字符串格式 "tavern - 酒馆"
                if target in sub_loc or target_lower in sub_loc.lower():
                    parts = sub_loc.split(" - ")
                    if parts:
                        return parts[0].strip()

        return target  # 找不到匹配，返回原值

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
            # 尝试将中文名称映射到 sub_location_id
            sub_loc_id = self._resolve_sub_location_id(intent.target, context)
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

        if intent_type == IntentType.WAIT:
            minutes = intent.parameters.get("minutes", 30)
            return [FlashRequest(
                operation=FlashOperation.UPDATE_TIME,
                parameters={"minutes": minutes},
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
        直接执行操作（跳过意图解析）。

        用于按钮点击等确定性操作。

        Args:
            world_id: 世界ID
            session_id: 会话ID
            action_id: 操作ID (如 "enter_tavern", "go_forest", "talk_npc_id")

        Returns:
            CoordinatorResponse: 响应
        """
        import time as _time
        start = _time.time()

        # 解析 action_id
        parts = action_id.split("_", 1)
        action_type = parts[0] if parts else ""
        target = parts[1] if len(parts) > 1 else ""

        # 构建 Flash 请求
        flash_request = None
        action_description = ""

        if action_type == "enter":
            flash_request = FlashRequest(
                operation=FlashOperation.ENTER_SUBLOCATION,
                parameters={"sub_location_id": target},
            )
            action_description = f"进入{target}"

        elif action_type == "go":
            flash_request = FlashRequest(
                operation=FlashOperation.NAVIGATE,
                parameters={"destination": target},
            )
            action_description = f"前往{target}"

        elif action_type == "talk":
            flash_request = FlashRequest(
                operation=FlashOperation.NPC_DIALOGUE,
                parameters={"npc_id": target, "message": "你好"},
            )
            action_description = f"与{target}交谈"

        elif action_type == "look":
            action_description = "观察周围"
            # look_around 不需要 flash 请求，直接生成叙述

        # 执行 Flash 操作
        flash_results = []
        if flash_request:
            try:
                result = await self.flash_cpu.execute_request(
                    world_id, session_id, flash_request
                )
                flash_results.append(result)
            except Exception as e:
                flash_results.append(FlashResponse(
                    success=False,
                    operation=flash_request.operation,
                    error=str(e),
                ))

        # 更新上下文
        context = await self._build_context(world_id, session_id)

        # 生成叙述
        results_summary = self._summarize_action_results(flash_results, action_description)
        location = context.get("location") or {}

        narration_prompt = f"""你是游戏的 GM。玩家执行了操作，请生成简洁的叙述。

## 当前场景
- 位置: {location.get("location_name", "未知")}
- 氛围: {location.get("atmosphere", "")}

## 玩家操作
{action_description}

## 执行结果
{results_summary}

## 要求
- 2-3句话描述场景变化
- 如果进入新地点，描述环境和氛围
- 如果有 NPC 在场，可以提及
- 保持沉浸感
"""

        try:
            response = await self.pro_dm.llm_service.generate_simple(
                narration_prompt,
                model_override=settings.gemini_flash_model,
            )
            narration = response.strip()
        except Exception as e:
            print(f"[Coordinator] 叙述生成失败: {e}")
            narration = results_summary

        elapsed = (_time.time() - start) * 1000
        print(f"[Coordinator] execute_action 完成 ({elapsed:.0f}ms)")

        # 获取新的可用操作
        available_actions = await self._get_available_actions(world_id, session_id, context)

        return CoordinatorResponse(
            narration=narration,
            speaker="GM",
            available_actions=available_actions,
            metadata={
                "action_id": action_id,
                "elapsed_ms": elapsed,
            },
        )

    def _summarize_action_results(
        self,
        flash_results: List[FlashResponse],
        action_description: str,
    ) -> str:
        """汇总操作执行结果"""
        if not flash_results:
            return action_description

        summaries = []
        for result in flash_results:
            if result.success:
                if result.result.get("description"):
                    summaries.append(result.result["description"])
                elif result.result.get("sub_location"):
                    sub = result.result["sub_location"]
                    summaries.append(f"进入了{sub.get('name', '未知地点')}")
                else:
                    summaries.append(f"{action_description}成功")
            else:
                summaries.append(f"操作失败: {result.error or '未知错误'}")

        return "\n".join(summaries) if summaries else action_description

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
            narration = await self.pro_dm.narrate_legacy(
                f"进入场景：{scene.location or scene.scene_id}",
                context={"location": {"location_name": scene.location, "description": scene.description}},
            )
            description = narration.narration
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
        payload = await self.flash_cpu._call_combat_tool_with_fallback(
            "start_combat_session",
            {
                "world_id": world_id,
                "session_id": session_id,
                "enemies": enemies,
                "player_state": player_state,
                "environment": environment,
                "combat_context": CombatStartRequest(player_state=player_state, enemies=enemies).combat_context.model_dump(),
            },
            fallback=lambda: {"type": "error", "response": "战斗工具不可用"},
        )
        if isinstance(payload, dict):
            if payload.get("error"):
                return {"type": "error", "response": payload["error"]}
            if payload.get("type") == "error":
                return payload

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
        # Prefer admin state, fallback to legacy context
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
