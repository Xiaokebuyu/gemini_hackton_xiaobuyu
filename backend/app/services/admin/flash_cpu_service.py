"""
Flash CPU service - rules/execution/state management.
"""
from __future__ import annotations

import json
import logging
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.admin_protocol import (
    AgenticResult,
    FlashOperation,
    FlashRequest,
    FlashResponse,
)
from app.models.state_delta import StateDelta
from app.services.admin.state_manager import StateManager
from app.services.flash_service import FlashService
from app.services.admin.event_service import AdminEventService
from app.services.admin.world_runtime import AdminWorldRuntime
from app.services.game_session_store import GameSessionStore
from app.services.narrative_service import NarrativeService
from app.services.passerby_service import PasserbyService
from app.services.llm_service import LLMService
from app.services.mcp_client_pool import MCPClientPool, MCPServiceUnavailableError
from app.services.image_generation_service import ImageGenerationService

logger = logging.getLogger(__name__)


class FlashCPUService:
    """Flash CPU service."""

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        event_service: Optional[AdminEventService] = None,
        flash_service: Optional[FlashService] = None,
        world_runtime: Optional[AdminWorldRuntime] = None,
        session_store: Optional[GameSessionStore] = None,
        narrative_service: Optional[NarrativeService] = None,
        passerby_service: Optional[PasserbyService] = None,
        llm_service: Optional[LLMService] = None,
        agentic_prompt_path: Optional[Path] = None,
        instance_manager: Optional[Any] = None,
        party_service: Optional[Any] = None,
        character_service: Optional[Any] = None,
        character_store: Optional[Any] = None,
        image_service: Optional[ImageGenerationService] = None,
    ) -> None:
        self.state_manager = state_manager or StateManager()
        self.event_service = event_service or AdminEventService()
        self.flash_service = flash_service or FlashService()
        self.world_runtime = world_runtime
        self.session_store = session_store or GameSessionStore()
        self.narrative_service = narrative_service
        self.passerby_service = passerby_service or PasserbyService()
        self.llm_service = llm_service or LLMService()
        self.agentic_prompt_path = agentic_prompt_path or Path("app/prompts/flash_agentic_system.md")
        self.instance_manager = instance_manager
        self.party_service = party_service
        self.character_service = character_service
        self.character_store = character_store
        self.image_service = image_service or ImageGenerationService()
        self.recall_orchestrator: Optional[Any] = None

    def _load_agentic_prompt(self) -> str:
        if self.agentic_prompt_path.exists():
            return self.agentic_prompt_path.read_text(encoding="utf-8")
        return (
            "你是一个 RPG GM。先使用工具执行必要操作，再输出2-4段中文叙述。"
            "叙述必须基于工具真实返回结果，不要编造。"
        )

    async def agentic_process_v4(
        self,
        *,
        session: Any,
        player_input: str,
        context: Dict[str, Any],
        graph_store: Any,
        recall_orchestrator: Any = None,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> AgenticResult:
        """V4 agentic process — uses V4AgenticToolRegistry + layered context.

        Args:
            session: SessionRuntime instance (must be restored).
            player_input: Player input text.
            context: Flat dict from LayeredContext.to_flat_dict().
            graph_store: GraphStore for memory/disposition tools.
            recall_orchestrator: Optional RecallOrchestrator.
        """
        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry

        system_prompt = self._load_agentic_prompt()
        model_name = settings.admin_agentic_model or settings.admin_flash_model

        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=self,
            graph_store=graph_store,
            recall_orchestrator=recall_orchestrator,
            image_service=self.image_service,
            event_queue=event_queue,
            engine_executed=context.get("engine_executed"),
        )

        context_json = json.dumps(context, ensure_ascii=False, default=str)
        user_prompt = (
            "以下是分层上下文(JSON)：\n"
            f"{context_json}\n\n"
            f"玩家输入：{player_input}\n\n"
            "请先调用必要工具，再输出最终 GM 叙述。"
        )

        tools = registry.get_tools()
        logger.info(
            "[agentic_v4] starting: model=%s tools=%d input=%.60s...",
            model_name, len(tools), player_input,
        )
        llm_resp = await self.llm_service.agentic_generate(
            user_prompt=user_prompt,
            system_instruction=system_prompt,
            tools=tools,
            model_override=model_name,
            thinking_level=settings.admin_flash_thinking_level,
            max_remote_calls=settings.admin_agentic_max_remote_calls,
        )
        narration = (llm_resp.text or "").strip()
        logger.info(
            "[agentic_v4] done: tool_calls=%d narration_len=%d",
            len(registry.tool_calls), len(narration),
        )
        if not narration:
            logger.warning("[agentic_v4] empty narration, tool_calls=%s", [c.name for c in registry.tool_calls])
            narration = "（你短暂沉默，观察着周围的动静。）"

        finish_reason: Optional[str] = None
        usage = {
            "tool_calls": len(registry.tool_calls),
            "thoughts_token_count": llm_resp.thinking.thoughts_token_count,
            "output_token_count": llm_resp.thinking.output_token_count,
            "total_token_count": llm_resp.thinking.total_token_count,
        }
        raw = llm_resp.raw_response
        try:
            candidates = getattr(raw, "candidates", None) or []
            if candidates:
                finish_reason = str(getattr(candidates[0], "finish_reason", None) or "")
        except Exception:
            finish_reason = None

        return AgenticResult(
            narration=narration,
            thinking_summary=(llm_resp.thinking.thoughts_summary or "").strip(),
            tool_calls=registry.tool_calls,
            image_data=registry.image_data,
            usage=usage,
            finish_reason=finish_reason,
        )

    async def execute_request(
        self,
        world_id: str,
        session_id: str,
        request: FlashRequest,
        generate_narration: bool = True,
    ) -> FlashResponse:
        """Execute a Flash operation."""
        op = request.operation
        params = request.parameters or {}

        if op == FlashOperation.UPDATE_TIME and self.world_runtime is None:
            return FlashResponse(
                success=False,
                operation=op,
                error="world_runtime not initialized",
            )

        try:
            if op == FlashOperation.UPDATE_TIME:
                minutes = int(params.get("minutes", 0))
                result = await self.world_runtime.advance_time(world_id, session_id, minutes)
                await self.world_runtime.refresh_state(world_id, session_id)
                payload = result if isinstance(result, dict) else {"raw": result}
                success = isinstance(payload, dict) and not payload.get("error") and payload.get("success") is not False
                delta = self._build_state_delta("update_time", {"game_time": payload.get("time")})
                error_message = payload.get("error") if not success else None
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=payload,
                    state_delta=delta,
                    error=error_message,
                )

            if op == FlashOperation.START_COMBAT:
                # Auto-fill player_state from character data if not provided
                player_state = params.get("player_state") or {}
                if not player_state and self.character_store:
                    try:
                        character = await self.character_store.get_character(world_id, session_id)
                        if character:
                            player_state = character.to_combat_player_state()
                    except Exception as exc:
                        logger.warning("[start_combat] Failed to auto-fill player_state: %s", exc)
                result = await self._call_combat_tool(
                    "start_combat_v3",
                    {
                        "world_id": world_id,
                        "session_id": session_id,
                        "enemies": params.get("enemies", []),
                        "player_state": player_state,
                        "environment": params.get("environment"),
                        "allies": params.get("allies"),
                        "combat_context": params.get("combat_context"),
                        "template_version": params.get("template_version"),
                    },
                )
                delta = None
                if isinstance(result, dict) and result.get("combat_id"):
                    delta = self._build_state_delta("start_combat", {"combat_id": result.get("combat_id")})
                    await self._apply_delta(world_id, session_id, delta)
                success = isinstance(result, dict) and result.get("type") != "error" and not result.get("error")
                error_message = None
                if not success and isinstance(result, dict):
                    error_message = result.get("error") or result.get("response")
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=result,
                    state_delta=delta,
                    error=error_message,
                )

            if op == FlashOperation.TRIGGER_NARRATIVE_EVENT:
                if not self.narrative_service:
                    return FlashResponse(success=False, operation=op, error="叙事系统未初始化")
                event_id = params.get("event_id")
                if not event_id:
                    return FlashResponse(success=False, operation=op, error="missing event_id")
                result = await self.narrative_service.trigger_event(
                    world_id,
                    session_id,
                    event_id,
                    skip_advance=True,
                )
                payload = result if isinstance(result, dict) else {"raw": result}
                success = isinstance(payload, dict) and not payload.get("error")
                error_message = payload.get("error") if not success else None
                delta = None
                if success:
                    delta = self._build_state_delta(
                        "trigger_narrative_event",
                        {
                            "story_event_update": {
                                "action": "trigger",
                                "event_id": str(event_id),
                                "success": True,
                            },
                            "story_events": [str(event_id)],
                            "last_story_event": str(event_id),
                        },
                    )
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=payload,
                    state_delta=delta,
                    error=error_message,
                )

            if op == FlashOperation.SPAWN_PASSERBY:
                if not self.passerby_service:
                    return FlashResponse(success=False, operation=op, error="路人系统未初始化")
                result = await self.passerby_service.get_or_spawn_passerby(
                    world_id,
                    params.get("map_id"),
                    params.get("sub_location_id"),
                )
                payload = result.model_dump() if hasattr(result, "model_dump") else result
                success = payload is not None and (not isinstance(payload, dict) or not payload.get("error"))
                error_message = payload.get("error") if isinstance(payload, dict) and not success else None
                return FlashResponse(success=success, operation=op, result=payload, error=error_message)

            if op == FlashOperation.NPC_DIALOGUE:
                npc_id = params.get("npc_id")
                message = params.get("message", "")
                # 通过 InstanceManager 维护 NPC 对话上下文
                if self.instance_manager is not None and npc_id:
                    response_text = await self._npc_dialogue_with_instance(
                        world_id, npc_id, message,
                    )
                    payload = {"response": response_text}
                elif npc_id:
                    response_text = await self._npc_dialogue_direct_flash(npc_id, message)
                    payload = {"response": response_text}
                else:
                    payload = {"error": "missing npc_id"}
                success = payload is not None and (not isinstance(payload, dict) or not payload.get("error"))
                error_message = payload.get("error") if isinstance(payload, dict) and not success else None
                return FlashResponse(success=success, operation=op, result=payload, error=error_message)

            if op == FlashOperation.ADD_TEAMMATE:
                if not self.party_service:
                    return FlashResponse(success=False, operation=op, error="party_service not initialized")
                from app.models.party import TeammateRole
                character_id = params.get("character_id")
                name = params.get("name", character_id or "未知")
                role_str = params.get("role", "support")
                personality = params.get("personality", "")
                response_tendency = float(params.get("response_tendency", 0.5))
                if not character_id:
                    return FlashResponse(success=False, operation=op, error="missing character_id")
                party = await self.party_service.get_or_create_party(world_id, session_id)
                if party.is_full():
                    return FlashResponse(success=False, operation=op, error="队伍已满")
                if party.get_member(character_id):
                    return FlashResponse(success=False, operation=op, error=f"{name} 已在队伍中")
                try:
                    teammate_role = TeammateRole(role_str)
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
                    party_after_add = await self.party_service.get_party(world_id, session_id)
                    delta = self._build_state_delta(
                        "add_teammate",
                        self._build_party_state_changes(
                            party_after_add,
                            update={
                                "action": "add_member",
                                "character_id": character_id,
                                "name": name,
                                "role": teammate_role.value,
                            },
                        ),
                    )
                    return FlashResponse(
                        success=True, operation=op,
                        result={"summary": f"{name} 加入了队伍", "character_id": character_id, "name": name, "role": teammate_role.value},
                        state_delta=delta,
                    )
                return FlashResponse(success=False, operation=op, error="添加队友失败")

            if op == FlashOperation.REMOVE_TEAMMATE:
                if not self.party_service:
                    return FlashResponse(success=False, operation=op, error="party_service not initialized")
                character_id = params.get("character_id")
                reason = params.get("reason", "")
                if not character_id:
                    return FlashResponse(success=False, operation=op, error="missing character_id")
                # 图谱化并持久化该队友的实例（保存对话记忆）
                if self.instance_manager is not None and self.instance_manager.has(world_id, character_id):
                    try:
                        await self.instance_manager.maybe_graphize_instance(world_id, character_id)
                        instance = self.instance_manager.get(world_id, character_id)
                        if instance and hasattr(instance, "persist") and self.instance_manager.graph_store:
                            await instance.persist(self.instance_manager.graph_store)
                    except Exception as exc:
                        logger.warning("[remove_teammate] 实例图谱化/持久化失败 (%s): %s", character_id, exc)
                success = await self.party_service.remove_member(world_id, session_id, character_id)
                party_after_remove = await self.party_service.get_party(world_id, session_id)
                name = params.get("name", character_id)
                summary = f"{name} 离开了队伍"
                if reason:
                    summary += f"（{reason}）"
                delta = None
                if success:
                    delta = self._build_state_delta(
                        "remove_teammate",
                        self._build_party_state_changes(
                            party_after_remove,
                            update={
                                "action": "remove_member",
                                "character_id": character_id,
                                "name": name,
                                "reason": reason,
                            },
                        ),
                    )
                return FlashResponse(
                    success=success, operation=op,
                    result={"summary": summary, "character_id": character_id, "reason": reason},
                    state_delta=delta,
                    error=None if success else "移除队友失败",
                )

            if op == FlashOperation.DISBAND_PARTY:
                if not self.party_service:
                    return FlashResponse(success=False, operation=op, error="party_service not initialized")
                reason = params.get("reason", "")
                party = await self.party_service.get_party(world_id, session_id)
                if not party:
                    return FlashResponse(success=False, operation=op, error="当前没有队伍")
                party_members_before_disband = self._extract_party_members(party)
                saved_members = []
                for member in party.get_active_members():
                    if self.instance_manager is not None and self.instance_manager.has(world_id, member.character_id):
                        try:
                            await self.instance_manager.maybe_graphize_instance(world_id, member.character_id)
                            inst = self.instance_manager.get(world_id, member.character_id)
                            if inst and hasattr(inst, "persist") and self.instance_manager.graph_store:
                                await inst.persist(self.instance_manager.graph_store)
                            saved_members.append(member.name)
                        except Exception as exc:
                            logger.warning("[disband_party] 实例图谱化失败 (%s): %s", member.character_id, exc)
                success = await self.party_service.disband_party(world_id, session_id)
                summary = "队伍已解散"
                if reason:
                    summary += f"（{reason}）"
                delta = None
                if success:
                    delta = self._build_state_delta(
                        "disband_party",
                        {
                            "party_update": {
                                "action": "disband",
                                "reason": reason,
                                "removed_members": party_members_before_disband,
                            },
                            "has_party": False,
                            "party_id": None,
                            "party_member_count": 0,
                            "party_members": [],
                        },
                    )
                return FlashResponse(
                    success=success, operation=op,
                    result={"summary": summary, "reason": reason, "saved_members": saved_members},
                    state_delta=delta,
                    error=None if success else "解散队伍失败",
                )

            if op == FlashOperation.ABILITY_CHECK:
                from app.services.ability_check_service import AbilityCheckService
                check_service = AbilityCheckService(store=self.character_store)
                result = await check_service.perform_check(
                    world_id=world_id,
                    session_id=session_id,
                    ability=params.get("ability"),
                    skill=params.get("skill"),
                    dc=int(params.get("dc", 10)),
                )
                op_success = "error" not in result
                return FlashResponse(
                    success=op_success, operation=op,
                    result=result,
                    error=result.get("error"),
                )

            return FlashResponse(success=False, operation=op, error=f"unsupported operation: {op}")
        except asyncio.CancelledError:
            raise
        except MCPServiceUnavailableError:
            raise
        except Exception as exc:
            return FlashResponse(success=False, operation=op, error=str(exc))

    def _build_state_delta(self, operation: str, changes: Dict[str, Any]) -> StateDelta:
        return StateDelta(
            delta_id=uuid.uuid4().hex,
            timestamp=datetime.utcnow(),
            operation=operation,
            changes=changes,
        )

    @staticmethod
    def _extract_party_members(party: Optional[Any]) -> List[Dict[str, Any]]:
        if not party or not hasattr(party, "get_active_members"):
            return []
        members: List[Dict[str, Any]] = []
        for member in party.get_active_members():
            role = getattr(member, "role", None)
            role_value = getattr(role, "value", role) if role is not None else ""
            members.append(
                {
                    "character_id": str(getattr(member, "character_id", "") or ""),
                    "name": str(getattr(member, "name", "") or ""),
                    "role": str(role_value or ""),
                    "current_mood": str(getattr(member, "current_mood", "") or ""),
                }
            )
        return members

    def _build_party_state_changes(
        self,
        party: Optional[Any],
        *,
        update: Dict[str, Any],
    ) -> Dict[str, Any]:
        members = self._extract_party_members(party)
        return {
            "party_update": update,
            "has_party": bool(party),
            "party_id": getattr(party, "party_id", None) if party else None,
            "party_member_count": len(members),
            "party_members": members,
        }

    async def _apply_delta(self, world_id: str, session_id: str, delta: StateDelta) -> None:
        state = await self.state_manager.apply_delta(world_id, session_id, delta)
        if self.world_runtime:
            await self.world_runtime.persist_state(state)

    async def sync_combat_result_to_character(
        self,
        world_id: str,
        session_id: str,
        combat_payload: Dict[str, Any],
        *,
        session: Optional[Any] = None,
    ) -> None:
        """Sync combat results (HP/XP/gold) back to player character.

        U2: 有 session 时走图节点适配器（V4 pipeline 主路径）；
        无 session 时降级旧 character_store 路径（V3 遗留）。
        """
        # U2: 图节点路径 — V4 pipeline 通过 v4_agentic_tools._sync_combat_to_graph() 处理
        if session is not None:
            player = session.player
            if player:
                try:
                    player_state = combat_payload.get("player_state") or {}
                    hp_remaining = player_state.get("hp_remaining")
                    if hp_remaining is not None:
                        player.current_hp = max(0, min(int(hp_remaining), player.max_hp))
                        session.mark_player_dirty()

                    final_result = combat_payload.get("final_result") or combat_payload.get("result") or {}
                    if isinstance(final_result, dict):
                        result_type = final_result.get("result", "")
                        rewards = final_result.get("rewards") or {}
                        if result_type == "victory" and rewards:
                            xp = int(rewards.get("xp", 0))
                            if xp > 0:
                                player.xp = (player.xp or 0) + xp
                                session.mark_player_dirty()
                            gold = int(rewards.get("gold", 0))
                            if gold > 0:
                                player.gold = (player.gold or 0) + gold
                                session.mark_player_dirty()
                            for item_id in rewards.get("items", []):
                                player.add_item(item_id, item_id, 1)
                                session.mark_player_dirty()
                    logger.info("[combat_sync] Synced combat results via graph node")
                except Exception as exc:
                    logger.error("[combat_sync] Graph sync failed: %s", exc, exc_info=True)
            return

    async def _npc_dialogue_with_instance(
        self, world_id: str, npc_id: str, message: str,
    ) -> str:
        """通过 InstanceManager 进行 NPC 对话（维护多轮上下文）"""
        instance = await self.instance_manager.get_or_create(npc_id, world_id)
        user_add = instance.context_window.add_message("user", message)
        if user_add.should_graphize:
            await self.instance_manager.maybe_graphize_instance(world_id, npc_id)

        # 构建最近对话历史
        recent = instance.context_window.get_recent_messages(count=20)
        history_lines = []
        for msg in recent[:-1]:  # 排除刚加入的当前消息
            history_lines.append(f"{msg.role}: {msg.content}")

        system_prompt = instance.context_window.get_system_prompt() or f"你是 {npc_id}。请保持角色一致性。"
        history_text = "\n".join(history_lines) if history_lines else "无"
        prompt = (
            f"{system_prompt}\n\n"
            f"## 近期对话\n{history_text}\n\n"
            f"## 当前玩家输入\n{message}\n\n"
            "请以角色身份简洁回复，保持与近期对话一致。"
        )
        result = await self.llm_service.generate_simple(
            prompt,
            model_override=settings.admin_flash_model,
            thinking_level=settings.admin_flash_thinking_level,
        )
        result = (result or "").strip()
        if not result:
            raise RuntimeError("npc dialogue empty response")

        assistant_add = instance.context_window.add_message("assistant", result)
        if assistant_add.should_graphize:
            await self.instance_manager.maybe_graphize_instance(world_id, npc_id)
        instance.state.conversation_turn_count += 1
        return result

    async def _npc_dialogue_direct_flash(self, npc_id: str, message: str) -> str:
        """无实例时的直接 Flash 对话。"""
        prompt = (
            f"你是 {npc_id}。\n\n"
            f"玩家说：{message}\n\n"
            "请以第一人称、符合角色身份回复1-3句。"
        )
        result = await self.llm_service.generate_simple(
            prompt,
            model_override=settings.admin_flash_model,
            thinking_level=settings.admin_flash_thinking_level,
        )
        text = (result or "").strip()
        if not text:
            raise RuntimeError("npc direct flash response empty")
        return text

    async def _call_combat_tool(self, tool_name: str, arguments: Dict[str, Any]):
        try:
            pool = await MCPClientPool.get_instance()
            return await pool.call_tool(MCPClientPool.COMBAT, tool_name, arguments)
        except asyncio.CancelledError as exc:
            logger.info("[FlashCPU] combat MCP 调用被取消: %s", exc)
            raise
        except MCPServiceUnavailableError as exc:
            logger.error("[FlashCPU] combat MCP 服务不可用: %s", exc)
            raise
        except Exception as exc:
            logger.error("[FlashCPU] combat MCP 调用失败: %s", exc)
            raise

    async def call_combat_tool(self, tool_name: str, arguments: Dict[str, Any]):
        return await self._call_combat_tool(tool_name, arguments)
