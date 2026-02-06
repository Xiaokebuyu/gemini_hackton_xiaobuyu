"""
Flash CPU service - rules/execution/state management.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.admin_protocol import (
    AnalysisPlan,
    FlashOperation,
    FlashRequest,
    FlashResponse,
    IntentType,
    ParsedIntent,
)
from app.models.flash import RecallRequest
from app.models.state_delta import StateDelta
from app.services.admin.state_manager import StateManager
from app.services.flash_service import FlashService
from app.services.admin.event_service import AdminEventService
from app.services.admin.world_runtime import AdminWorldRuntime
from app.services.game_session_store import GameSessionStore
from app.services.pro_service import ProService
from app.services.narrative_service import NarrativeService
from app.services.passerby_service import PasserbyService
from app.services.llm_service import LLMService
from app.services.mcp_client_pool import MCPClientPool

logger = logging.getLogger(__name__)


class FlashCPUService:
    """Flash CPU service (transitional implementation)."""

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        event_service: Optional[AdminEventService] = None,
        flash_service: Optional[FlashService] = None,
        world_runtime: Optional[AdminWorldRuntime] = None,
        session_store: Optional[GameSessionStore] = None,
        pro_service: Optional[ProService] = None,
        narrative_service: Optional[NarrativeService] = None,
        passerby_service: Optional[PasserbyService] = None,
        llm_service: Optional[LLMService] = None,
        analysis_prompt_path: Optional[Path] = None,
    ) -> None:
        self.state_manager = state_manager or StateManager()
        self.event_service = event_service or AdminEventService()
        self.flash_service = flash_service or FlashService()
        self.world_runtime = world_runtime
        self.session_store = session_store or GameSessionStore()
        self.pro_service = pro_service or ProService()
        self.narrative_service = narrative_service
        self.passerby_service = passerby_service or PasserbyService()
        self.llm_service = llm_service or LLMService()
        self.analysis_prompt_path = analysis_prompt_path or Path("app/prompts/flash_analysis.md")

    def _load_analysis_prompt(self) -> str:
        if self.analysis_prompt_path.exists():
            return self.analysis_prompt_path.read_text(encoding="utf-8")
        return (
            "你是游戏系统的分析引擎。一次性完成意图解析、操作规划与记忆召回建议，并返回严格 JSON。"
        )

    def _default_plan(self, player_input: str) -> AnalysisPlan:
        intent = ParsedIntent(
            intent_type=IntentType.ROLEPLAY,
            confidence=0.5,
            raw_input=player_input,
            interpretation="无法解析，作为角色扮演处理",
        )
        return AnalysisPlan(intent=intent)

    def _handle_system_command(self, player_input: str) -> AnalysisPlan:
        cmd_parts = player_input[1:].split(maxsplit=1)
        cmd = cmd_parts[0].lower() if cmd_parts else ""
        arg = cmd_parts[1] if len(cmd_parts) > 1 else ""

        if cmd in ("go", "navigate") and arg:
            intent = ParsedIntent(
                intent_type=IntentType.NAVIGATION,
                confidence=1.0,
                target=arg,
                action=cmd,
                parameters={"destination": arg},
                raw_input=player_input,
                interpretation="系统指令导航",
            )
            return AnalysisPlan(
                intent=intent,
                operations=[
                    FlashRequest(
                        operation=FlashOperation.NAVIGATE,
                        parameters={"destination": arg},
                    )
                ],
                reasoning="system_command",
            )

        if cmd == "talk" and arg:
            intent = ParsedIntent(
                intent_type=IntentType.NPC_INTERACTION,
                confidence=1.0,
                target=arg,
                action=cmd,
                parameters={"npc_id": arg},
                raw_input=player_input,
                interpretation="系统指令对话",
            )
            return AnalysisPlan(
                intent=intent,
                operations=[
                    FlashRequest(
                        operation=FlashOperation.NPC_DIALOGUE,
                        parameters={"npc_id": arg, "message": "你好"},
                    )
                ],
                reasoning="system_command",
            )

        if cmd == "wait":
            minutes = 30
            if arg.isdigit():
                minutes = int(arg)
            intent = ParsedIntent(
                intent_type=IntentType.WAIT,
                confidence=1.0,
                action=cmd,
                parameters={"minutes": minutes},
                raw_input=player_input,
                interpretation="系统指令等待",
            )
            return AnalysisPlan(
                intent=intent,
                operations=[
                    FlashRequest(
                        operation=FlashOperation.UPDATE_TIME,
                        parameters={"minutes": minutes},
                    )
                ],
                reasoning="system_command",
            )

        intent = ParsedIntent(
            intent_type=IntentType.SYSTEM_COMMAND,
            confidence=1.0,
            target=cmd,
            action=cmd,
            parameters={"command": cmd, "argument": arg},
            raw_input=player_input,
            interpretation="系统命令",
        )
        return AnalysisPlan(intent=intent, reasoning="system_command")

    def _parse_analysis_result(
        self,
        parsed: Dict[str, Any],
        player_input: str,
        context: Dict[str, Any],
    ) -> AnalysisPlan:
        intent_type_str = str(parsed.get("intent_type", "roleplay")).lower()
        intent_aliases = {
            "enter_sublocation": "enter_sub_location",
            "leave_sublocation": "leave_sub_location",
            "npc_interaction": "npc_interaction",
            "team_interaction": "team_interaction",
            "start_combat": "start_combat",
        }
        intent_type_str = intent_aliases.get(intent_type_str, intent_type_str)
        try:
            intent_type = IntentType(intent_type_str)
        except ValueError:
            intent_type = IntentType.ROLEPLAY

        intent = ParsedIntent(
            intent_type=intent_type,
            confidence=float(parsed.get("confidence", 0.8)),
            target=parsed.get("target"),
            action=parsed.get("action"),
            parameters=parsed.get("parameters", {}),
            raw_input=player_input,
            interpretation=parsed.get("interpretation"),
            player_emotion=parsed.get("player_emotion"),
        )

        operations: List[FlashRequest] = []
        for req_data in parsed.get("operations", []):
            if not isinstance(req_data, dict):
                continue
            op_raw = str(req_data.get("operation", "")).strip()
            if not op_raw:
                continue
            op_key = op_raw.lower()
            op_aliases = {
                "enter_sub_location": "enter_sublocation",
                "leave_sub_location": "leave_sublocation",
                "enter_sublocation": "enter_sublocation",
                "start_combat": "start_combat",
                "npc_dialogue": "npc_dialogue",
                "spawn_passerby": "spawn_passerby",
                "trigger_narrative_event": "trigger_narrative_event",
                "broadcast_event": "broadcast_event",
                "graphize_event": "graphize_event",
                "recall_memory": "recall_memory",
                "update_time": "update_time",
                "navigate": "navigate",
            }
            op_key = op_aliases.get(op_key, op_key)
            operation = None
            try:
                operation = FlashOperation(op_key)
            except ValueError:
                operation = None
            if not operation:
                continue
            operations.append(
                FlashRequest(
                    operation=operation,
                    parameters=req_data.get("parameters", {}),
                    priority=req_data.get("priority", "normal"),
                )
            )

        memory_seeds = [str(s) for s in parsed.get("memory_seeds", []) if s]
        reasoning = parsed.get("reasoning") or ""
        return AnalysisPlan(
            intent=intent,
            operations=operations,
            memory_seeds=memory_seeds,
            reasoning=reasoning,
        )

    async def analyze_and_plan(
        self,
        player_input: str,
        context: Dict[str, Any],
    ) -> AnalysisPlan:
        if player_input.strip().startswith("/"):
            return self._handle_system_command(player_input)

        prompt = self._load_analysis_prompt()
        location = context.get("location") or {}
        time_info = context.get("time") or {}
        teammates = context.get("teammates") or []
        available_destinations = context.get("available_destinations") or []
        sub_locations = context.get("sub_locations") or location.get("sub_locations") or []

        filled_prompt = prompt.format(
            location_name=location.get("location_name", "未知地点"),
            available_destinations=", ".join(
                d.get("name", d.get("id", str(d)))
                if isinstance(d, dict) else str(d)
                for d in available_destinations
            ) or "无",
            sub_locations=", ".join(
                f"{s.get('name', s.get('id', str(s)))}"
                if isinstance(s, dict) else str(s)
                for s in sub_locations
            ) or "无",
            npcs_present=", ".join(location.get("npcs_present", [])) or "无",
            teammates=", ".join(
                t.get("name", t) if isinstance(t, dict) else str(t)
                for t in teammates
            ) or "无",
            time=time_info.get("formatted") or time_info.get("formatted_time") or "未知",
            current_state=context.get("state", "exploring"),
            active_npc=context.get("active_npc") or "无",
            player_input=player_input,
        )

        try:
            result = await self.llm_service.generate_simple(
                filled_prompt,
                model_override=settings.gemini_flash_model,
            )
            parsed = self.llm_service.parse_json(result)
            if parsed:
                return self._parse_analysis_result(parsed, player_input, context)
        except Exception as exc:
            print(f"[FlashCPU] analyze_and_plan 失败: {exc}")

        return self._default_plan(player_input)

    async def process_player_input(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        input_type: Optional[Any] = None,
        mode: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Process player input with simple rules and tool execution."""
        state = None
        if self.world_runtime:
            try:
                state = await self.world_runtime.get_state(world_id, session_id)
            except Exception:
                state = None

        text = player_input.strip()

        # System commands (simple rule-based)
        if text.startswith("/think") or text.startswith("/mode think"):
            delta = self._build_state_delta("chat_mode", {"chat_mode": "think"})
            await self._apply_delta(world_id, session_id, delta)
            return {
                "type": "system",
                "response": "已切换到 THINK 模式。",
                "speaker": "系统",
                "state_changes": {"chat_mode": "think"},
                "state_delta": delta.model_dump(),
            }

        if text.startswith("/say") or text.startswith("/mode say"):
            delta = self._build_state_delta("chat_mode", {"chat_mode": "say"})
            await self._apply_delta(world_id, session_id, delta)
            return {
                "type": "system",
                "response": "已切换到 SAY 模式。",
                "speaker": "系统",
                "state_changes": {"chat_mode": "say"},
                "state_delta": delta.model_dump(),
            }

        if text.startswith("/time"):
            fallback = (
                (lambda: self.world_runtime.get_game_time(world_id, session_id))
                if self.world_runtime
                else (lambda: {"error": "时间系统未初始化"})
            )
            result = await self._call_tool_with_fallback(
                "get_time",
                {"world_id": world_id, "session_id": session_id},
                fallback=fallback,
            )
            response_text = result.get("formatted") if isinstance(result, dict) else None
            return {
                "type": "system",
                "response": response_text or "时间信息已获取。",
                "speaker": "系统",
                "state_changes": {},
                "time": result,
            }

        if text.startswith("/where") or text.startswith("/location"):
            fallback = (
                (lambda: self.world_runtime.get_current_location(world_id, session_id))
                if self.world_runtime
                else (lambda: {"error": "位置系统未初始化"})
            )
            result = await self._call_tool_with_fallback(
                "get_location",
                {"world_id": world_id, "session_id": session_id},
                fallback=fallback,
            )
            location_name = result.get("location_name") if isinstance(result, dict) else None
            return {
                "type": "system",
                "response": f"当前位置：{location_name}" if location_name else "已获取位置信息。",
                "speaker": "系统",
                "state_changes": {},
                "location": result,
            }

        if text.startswith("/go ") or text.startswith("/navigate "):
            destination = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
            request = FlashRequest(
                operation=FlashOperation.NAVIGATE,
                parameters={"destination": destination},
            )
            result = await self.execute_request(world_id, session_id, request)
            return {
                "type": "narration" if result.success else "error",
                "response": result.result.get("narration", "") if result.success else (result.error or ""),
                "speaker": "GM",
                "state_changes": result.state_delta.model_dump() if result.state_delta else {},
            }

        if text.startswith("/talk "):
            npc_id = text.split(maxsplit=1)[1].strip()
            if not npc_id:
                return {"type": "error", "response": "请指定NPC ID", "speaker": "系统"}
            delta = self._build_state_delta("dialogue_start", {"active_dialogue_npc": npc_id})
            await self._apply_delta(world_id, session_id, delta)
            response = await self._npc_respond(world_id, session_id, npc_id, "你好")
            return {
                "type": "dialogue",
                "response": response.get("response", ""),
                "speaker": response.get("npc_id", npc_id),
                "npc_id": npc_id,
                "state_changes": {"active_dialogue_npc": npc_id},
                "state_delta": delta.model_dump(),
            }

        if text.startswith("/end") or text.startswith("/leave"):
            if state and state.active_dialogue_npc:
                delta = self._build_state_delta("dialogue_end", {"active_dialogue_npc": None})
                await self._apply_delta(world_id, session_id, delta)
                return {
                    "type": "system",
                    "response": "结束对话。",
                    "speaker": "系统",
                    "state_changes": {"active_dialogue_npc": None},
                    "state_delta": delta.model_dump(),
                }

        # Default admin narration path (Pro DM will fill response)
        context_payload: Dict[str, Any] = {}
        if self.world_runtime:
            try:
                location = await self.world_runtime.get_current_location(world_id, session_id)
                time_info = await self.world_runtime.get_game_time(world_id, session_id)
                context_payload = {
                    "location": location,
                    "time": time_info,
                    "chat_mode": state.chat_mode if state else None,
                }
            except Exception:
                context_payload = {}

        return {
            "type": "narration",
            "response": "",
            "speaker": "GM",
            "state_changes": {},
            "context": context_payload,
        }


    async def execute_request(
        self,
        world_id: str,
        session_id: str,
        request: FlashRequest,
        generate_narration: bool = True,
    ) -> FlashResponse:
        """Execute a Flash operation using legacy services (transitional)."""
        op = request.operation
        params = request.parameters or {}

        runtime_required_ops = {
            FlashOperation.NAVIGATE,
            FlashOperation.UPDATE_TIME,
            FlashOperation.ENTER_SUBLOCATION,
        }
        if op in runtime_required_ops and self.world_runtime is None:
            return FlashResponse(
                success=False,
                operation=op,
                error="world_runtime not initialized",
            )

        try:
            if op == FlashOperation.NAVIGATE:
                result = await self._call_tool_with_fallback(
                    "navigate",
                    {
                        "world_id": world_id,
                        "session_id": session_id,
                        "destination": params.get("destination"),
                        "direction": params.get("direction"),
                        "generate_narration": generate_narration,
                    },
                    fallback=lambda: self.world_runtime.navigate(
                        world_id=world_id,
                        session_id=session_id,
                        destination=params.get("destination"),
                        direction=params.get("direction"),
                        generate_narration=generate_narration,
                    )
                    if self.world_runtime
                    else (lambda: {"success": False, "error": "导航系统未初始化"})(),
                )
                if self._is_tool_result_failure(result) and self.world_runtime:
                    direct_result = await self.world_runtime.navigate(
                        world_id=world_id,
                        session_id=session_id,
                        destination=params.get("destination"),
                        direction=params.get("direction"),
                        generate_narration=generate_narration,
                    )
                    if isinstance(direct_result, dict):
                        result = direct_result
                if self.world_runtime:
                    await self.world_runtime.refresh_state(world_id, session_id)
                payload = result if isinstance(result, dict) else {"raw": result}
                success = bool(payload.get("success"))
                error_message = payload.get("error") if not success else None
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=payload,
                    error=error_message,
                )

            if op == FlashOperation.UPDATE_TIME:
                minutes = int(params.get("minutes", 0))
                result = await self._call_tool_with_fallback(
                    "advance_time",
                    {"world_id": world_id, "session_id": session_id, "minutes": minutes},
                    fallback=lambda: self.world_runtime.advance_time(world_id, session_id, minutes)
                    if self.world_runtime
                    else {"error": "时间系统未初始化"},
                )
                if self._is_tool_result_failure(result) and self.world_runtime:
                    direct_result = await self.world_runtime.advance_time(world_id, session_id, minutes)
                    if isinstance(direct_result, dict):
                        result = direct_result
                if self.world_runtime:
                    await self.world_runtime.refresh_state(world_id, session_id)
                payload = result if isinstance(result, dict) else {"raw": result}
                success = not self._is_tool_result_failure(payload)
                delta = self._build_state_delta("update_time", {"game_time": payload.get("time")})
                error_message = payload.get("error") if not success else None
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=payload,
                    state_delta=delta,
                    error=error_message,
                )

            if op == FlashOperation.ENTER_SUBLOCATION:
                sub_location_id = await self._resolve_enter_sub_location_id(
                    world_id=world_id,
                    session_id=session_id,
                    params=params,
                )
                if not sub_location_id:
                    return FlashResponse(
                        success=False,
                        operation=op,
                        error="missing sub_location_id",
                    )

                result = await self._call_tool_with_fallback(
                    "enter_sublocation",
                    {
                        "world_id": world_id,
                        "session_id": session_id,
                        "sub_location_id": sub_location_id,
                    },
                    fallback=lambda: self.world_runtime.enter_sub_location(
                        world_id=world_id,
                        session_id=session_id,
                        sub_location_id=sub_location_id,
                    )
                    if self.world_runtime
                    else {"success": False, "error": "子地点系统未初始化"},
                )
                # MCP 语义失败时（error/success=false），再尝试本地 runtime，
                # 避免 MCP 子进程状态与主进程短暂不一致导致的误失败。
                mcp_failed = self._is_tool_result_failure(result)
                if mcp_failed and self.world_runtime:
                    direct_result = await self.world_runtime.enter_sub_location(
                        world_id=world_id,
                        session_id=session_id,
                        sub_location_id=sub_location_id,
                    )
                    if isinstance(direct_result, dict):
                        result = direct_result

                if self.world_runtime:
                    await self.world_runtime.refresh_state(world_id, session_id)
                payload = result if isinstance(result, dict) else {"raw": result}
                success = bool(payload.get("success"))
                error_message = payload.get("error") if not success else None
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=payload,
                    error=error_message,
                )

            if op == FlashOperation.START_COMBAT:
                result = await self._call_combat_tool_with_fallback(
                    "start_combat_session",
                    {
                        "world_id": world_id,
                        "session_id": session_id,
                        "enemies": params.get("enemies", []),
                        "player_state": params.get("player_state", {}),
                        "environment": params.get("environment"),
                        "allies": params.get("allies"),
                        "combat_context": params.get("combat_context"),
                    },
                    fallback=lambda: {"type": "error", "response": "战斗工具不可用"},
                )
                if isinstance(result, dict) and result.get("combat_id"):
                    delta = self._build_state_delta("start_combat", {"combat_id": result.get("combat_id")})
                    await self._apply_delta(world_id, session_id, delta)
                success = True
                if isinstance(result, dict) and result.get("type") == "error":
                    success = False
                return FlashResponse(success=success, operation=op, result=result)

            if op == FlashOperation.RECALL_MEMORY:
                recall_payload = params.get("request") or {}
                recall_request = (
                    recall_payload
                    if isinstance(recall_payload, RecallRequest)
                    else RecallRequest(**recall_payload)
                )
                chapter_id = params.get("chapter_id")
                area_id = params.get("area_id")
                tool_args = {
                    "world_id": world_id,
                    "character_id": params.get("character_id"),
                    "request": recall_request.model_dump(),
                }
                if chapter_id:
                    tool_args["chapter_id"] = chapter_id
                if area_id:
                    tool_args["area_id"] = area_id
                result = await self._call_tool_with_fallback(
                    "recall_memory",
                    tool_args,
                    # 不再回退旧的 character-only recall 逻辑，MCP 不可用时显式失败。
                    fallback=lambda: {"error": "recall_memory MCP tool unavailable"},
                )
                payload = result.model_dump() if hasattr(result, "model_dump") else result
                payload_dict = payload if isinstance(payload, dict) else {"raw": payload}
                error_message = payload_dict.get("error")
                return FlashResponse(
                    success=not bool(error_message),
                    operation=op,
                    result=payload_dict,
                    error=error_message,
                )

            if op == FlashOperation.BROADCAST_EVENT:
                # Expect caller to pass a GMEventIngestRequest-compatible dict.
                payload = params.get("request")
                if not payload:
                    return FlashResponse(success=False, operation=op, error="missing request payload")
                if not hasattr(payload, "event"):
                    from app.models.event import GMEventIngestRequest
                    payload = GMEventIngestRequest(**payload)
                result = await self.event_service.ingest_event(world_id, payload)
                return FlashResponse(success=True, operation=op, result=result.model_dump() if hasattr(result, "model_dump") else result)

            if op == FlashOperation.GRAPHIZE_EVENT:
                return FlashResponse(success=False, operation=op, error="graphize_event not implemented yet")

            if op == FlashOperation.TRIGGER_NARRATIVE_EVENT:
                result = await self._call_tool_with_fallback(
                    "trigger_event",
                    {"world_id": world_id, "session_id": session_id, "event_id": params.get("event_id")},
                    fallback=lambda: self.narrative_service.trigger_event(world_id, session_id, params.get("event_id"))
                    if self.narrative_service
                    else {"error": "叙事系统未初始化"},
                )
                return FlashResponse(success=True, operation=op, result=result)

            if op == FlashOperation.SPAWN_PASSERBY:
                result = await self._call_tool_with_fallback(
                    "spawn_passerby",
                    {
                        "world_id": world_id,
                        "map_id": params.get("map_id"),
                        "sub_location_id": params.get("sub_location_id"),
                        "spawn_hint": params.get("spawn_hint"),
                    },
                    fallback=lambda: self.passerby_service.get_or_spawn_passerby(
                        world_id,
                        params.get("map_id"),
                        params.get("sub_location_id"),
                    )
                    if self.passerby_service
                    else {"error": "路人系统未初始化"},
                )
                payload = result.model_dump() if hasattr(result, "model_dump") else result
                return FlashResponse(success=True, operation=op, result=payload)

            if op == FlashOperation.NPC_DIALOGUE:
                result = await self._call_tool_with_fallback(
                    "npc_respond",
                    {
                        "world_id": world_id,
                        "npc_id": params.get("npc_id"),
                        "message": params.get("message", ""),
                        "tier": params.get("tier", "secondary"),
                        "scene": params.get("scene"),
                        "conversation_history": params.get("conversation_history"),
                    },
                    fallback=lambda: self.pro_service.chat_simple(
                        world_id,
                        params.get("npc_id"),
                        params.get("message", ""),
                    ),
                )
                return FlashResponse(success=True, operation=op, result=result if isinstance(result, dict) else {"response": result})

            return FlashResponse(success=False, operation=op, error=f"unsupported operation: {op}")
        except Exception as exc:
            return FlashResponse(success=False, operation=op, error=str(exc))

    @staticmethod
    def _is_tool_result_failure(result: Any) -> bool:
        return (
            not isinstance(result, dict)
            or bool(result.get("error"))
            or result.get("success") is False
        )

    @staticmethod
    def _matches_location_candidate(candidate: str, value: Optional[str]) -> bool:
        if not candidate or not value:
            return False
        lhs = candidate.strip().lower()
        rhs = str(value).strip().lower()
        if not lhs or not rhs:
            return False
        return lhs == rhs or lhs in rhs or rhs in lhs

    def _match_sub_location_from_context(
        self,
        candidate: str,
        sub_locations: List[Dict[str, Any]],
    ) -> Optional[str]:
        for item in sub_locations:
            if not isinstance(item, dict):
                continue
            sub_id = item.get("id")
            sub_name = item.get("name")
            if self._matches_location_candidate(candidate, sub_id) or self._matches_location_candidate(candidate, sub_name):
                return str(sub_id) if sub_id else None
        return None

    async def _resolve_enter_sub_location_id(
        self,
        world_id: str,
        session_id: str,
        params: Dict[str, Any],
    ) -> Optional[str]:
        """
        兼容两种参数：
        - sub_location_id: 规范 ID
        - sub_location: 名称或ID（LLM 常返回中文名称）
        """
        raw_id = params.get("sub_location_id")
        if raw_id is not None and str(raw_id).strip():
            return str(raw_id).strip()

        raw_name = params.get("sub_location")
        if raw_name is None and params.get("target") is not None:
            raw_name = params.get("target")
        if raw_name is None and params.get("destination") is not None:
            raw_name = params.get("destination")
        if raw_name is None and params.get("location") is not None:
            raw_name = params.get("location")
        if raw_name is None:
            return None

        candidate = str(raw_name).strip()
        if not candidate:
            return None

        if not self.world_runtime:
            return candidate

        current_map_id: Optional[str] = None
        try:
            state = await self.world_runtime.get_state(world_id, session_id)
            current_map_id = getattr(state, "player_location", None)
        except Exception:
            current_map_id = None

        try:
            location = await self.world_runtime.get_current_location(world_id, session_id)
            if isinstance(location, dict):
                current_map_id = current_map_id or location.get("location_id")
                sub_locations = (
                    location.get("available_sub_locations")
                    or location.get("sub_locations")
                    or []
                )
                resolved = self._match_sub_location_from_context(candidate, sub_locations)
                if resolved:
                    return resolved
        except Exception:
            pass

        # 再用导航器做兜底匹配（支持名称/ID模糊匹配）
        try:
            navigator = self.world_runtime._get_navigator(world_id)

            if current_map_id:
                if navigator.get_sub_location(current_map_id, candidate):
                    return candidate
                for sub_loc in navigator.get_sub_locations(current_map_id):
                    if (
                        self._matches_location_candidate(candidate, sub_loc.id)
                        or self._matches_location_candidate(candidate, sub_loc.name)
                    ):
                        return sub_loc.id

            map_id, sub_location_id = navigator.resolve_location(candidate)
            if sub_location_id and (not current_map_id or map_id == current_map_id):
                return sub_location_id
        except Exception:
            pass

        # 最终兜底：按 ID 原样传递给 runtime，让 runtime 给出明确报错
        return candidate

    def _build_state_delta(self, operation: str, changes: Dict[str, Any]) -> StateDelta:
        return StateDelta(
            delta_id=uuid.uuid4().hex,
            timestamp=datetime.utcnow(),
            operation=operation,
            changes=changes,
        )

    async def _apply_delta(self, world_id: str, session_id: str, delta: StateDelta) -> None:
        state = await self.state_manager.apply_delta(world_id, session_id, delta)
        if self.world_runtime:
            try:
                await self.world_runtime.persist_state(state)
            except Exception:
                pass

    async def _npc_respond(self, world_id: str, session_id: str, npc_id: str, message: str) -> Dict[str, Any]:
        scene = None
        if self.world_runtime:
            location = await self.world_runtime.get_current_location(world_id, session_id)
            if isinstance(location, dict):
                scene = {
                    "description": location.get("description", ""),
                    "location": location.get("location_name"),
                    "present_characters": location.get("npcs_present", []),
                    "environment": location.get("atmosphere"),
                }
        result = await self._call_tool_with_fallback(
            "npc_respond",
            {
                "world_id": world_id,
                "npc_id": npc_id,
                "message": message,
                "tier": "secondary",
                "scene": scene,
            },
            fallback=lambda: self.pro_service.chat_simple(world_id, npc_id, message),
        )
        return result if isinstance(result, dict) else {"response": result}

    async def _handle_combat_input(self, world_id: str, session_id: str, player_input: str) -> Dict[str, Any]:
        session_state = await self.session_store.get_session(world_id, session_id)
        if not session_state or not session_state.active_combat_id:
            return {"type": "error", "response": "没有活跃的战斗"}

        combat_id = session_state.active_combat_id
        actions_payload = await self.call_combat_tool("get_available_actions", {"combat_id": combat_id})
        available_actions = actions_payload.get("actions", [])
        action_id = self._match_combat_action(player_input, available_actions)

        if not action_id:
            return {
                "type": "combat",
                "phase": "input",
                "response": "无法识别的行动。",
                "available_actions": available_actions,
            }

        payload = await self.call_combat_tool("execute_action", {"combat_id": combat_id, "action_id": action_id})
        if payload.get("error"):
            return {"type": "error", "response": payload["error"]}

        combat_state = payload.get("combat_state", {})
        if combat_state.get("is_ended"):
            resolve_payload = await self.call_combat_tool(
                "resolve_combat_session",
                {"world_id": world_id, "session_id": session_id, "combat_id": combat_id, "dispatch": True},
            )
            await self._apply_delta(world_id, session_id, self._build_state_delta("end_combat", {"combat_id": None}))
            return {
                "type": "combat",
                "phase": "end",
                "result": payload.get("final_result"),
                "narration": payload.get("final_result", {}).get("summary", "战斗结束。"),
                "event_id": resolve_payload.get("event_id"),
            }

        return {
            "type": "combat",
            "phase": "action",
            "action_result": payload.get("action_result"),
            "narration": payload.get("action_result", {}).get("display_text", ""),
            "available_actions": available_actions,
        }

    def _match_combat_action(self, player_input: str, available_actions: list) -> Optional[str]:
        input_lower = player_input.lower()
        for action in available_actions:
            action_id = action.get("action_id", "").lower()
            display_name = action.get("display_name", "").lower()
            action_type = str(action.get("action_type", "")).lower()
            if action_id and action_id == input_lower:
                return action.get("action_id")
            if display_name and display_name in input_lower:
                return action.get("action_id")
            if action_type and action_type in input_lower:
                return action.get("action_id")
        return None

    async def _call_tool_with_fallback(self, tool_name: str, arguments: Dict[str, Any], fallback):
        """Call MCP tool with fallback on failure."""
        try:
            pool = await MCPClientPool.get_instance()
            return await pool.call_tool(MCPClientPool.GAME_TOOLS, tool_name, arguments)
        except Exception as exc:
            logger.warning("[FlashCPU] MCP call failed, using fallback: %s", exc)
            result = fallback()
            if hasattr(result, "__await__"):
                return await result
            return result

    async def _call_combat_tool_with_fallback(self, tool_name: str, arguments: Dict[str, Any], fallback):
        try:
            result = await self._call_combat_tool(tool_name, arguments)
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(result.get("error"))
            return result
        except Exception as exc:
            logger.warning("[FlashCPU] Combat MCP call failed, using fallback: %s", exc)
            result = fallback()
            if hasattr(result, "__await__"):
                return await result
            return result

    async def _call_combat_tool(self, tool_name: str, arguments: Dict[str, Any]):
        pool = await MCPClientPool.get_instance()
        return await pool.call_tool(MCPClientPool.COMBAT, tool_name, arguments)

    async def call_combat_tool(self, tool_name: str, arguments: Dict[str, Any]):
        return await self._call_combat_tool(tool_name, arguments)
