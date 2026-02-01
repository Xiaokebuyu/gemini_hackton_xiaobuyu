"""
Flash CPU service - rules/execution/state management.
"""
from __future__ import annotations

import json
import shlex
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.config import settings
from app.models.admin_protocol import FlashOperation, FlashRequest, FlashResponse
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
    ) -> None:
        self.state_manager = state_manager or StateManager()
        self.event_service = event_service or AdminEventService()
        self.flash_service = flash_service or FlashService()
        self.world_runtime = world_runtime
        self.session_store = session_store or GameSessionStore()
        self.pro_service = pro_service or ProService()
        self.narrative_service = narrative_service
        self.passerby_service = passerby_service or PasserbyService()
        self._mcp_server_root = Path(__file__).resolve().parents[3]

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

        if state and state.active_dialogue_npc:
            response = await self._npc_respond(world_id, session_id, state.active_dialogue_npc, text)
            return {
                "type": "dialogue",
                "response": response.get("response", ""),
                "speaker": response.get("npc_id", state.active_dialogue_npc),
                "npc_id": state.active_dialogue_npc,
            }

        if state and state.chat_mode == "say" and self.world_runtime:
            try:
                location = await self.world_runtime.get_current_location(world_id, session_id)
                npcs = location.get("npcs_present", []) if isinstance(location, dict) else []
                responders = npcs[:3]
                responses = []
                for npc_id in responders:
                    npc_resp = await self._npc_respond(world_id, session_id, npc_id, text)
                    responses.append(
                        {
                            "speaker": npc_resp.get("npc_id", npc_id),
                            "response": npc_resp.get("response", ""),
                            "npc_id": npc_id,
                        }
                    )
                return {
                    "type": "dialogue" if responses else "narration",
                    "response": f"你说：{text}",
                    "speaker": "GM",
                    "responses": responses,
                }
            except Exception:
                pass

        # Combat-related inputs: route to combat tools
        combat_hints = ("attack", "fight", "战斗", "攻击", "施法")
        input_type_value = getattr(input_type, "value", input_type)
        if input_type_value in ("combat", "combat_action") or any(hint in text.lower() for hint in combat_hints):
            return await self._handle_combat_input(world_id, session_id, text)

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
    ) -> FlashResponse:
        """Execute a Flash operation using legacy services (transitional)."""
        op = request.operation
        params = request.parameters or {}

        try:
            if op == FlashOperation.NAVIGATE:
                result = await self._call_tool_with_fallback(
                    "navigate",
                    {
                        "world_id": world_id,
                        "session_id": session_id,
                        "destination": params.get("destination"),
                        "direction": params.get("direction"),
                    },
                    fallback=lambda: self.world_runtime.navigate(
                        world_id=world_id,
                        session_id=session_id,
                        destination=params.get("destination"),
                        direction=params.get("direction"),
                    )
                    if self.world_runtime
                    else (lambda: {"success": False, "error": "导航系统未初始化"})(),
                )
                if self.world_runtime:
                    await self.world_runtime.refresh_state(world_id, session_id)
                return FlashResponse(success=bool(result.get("success")), operation=op, result=result)

            if op == FlashOperation.UPDATE_TIME:
                minutes = int(params.get("minutes", 0))
                result = await self._call_tool_with_fallback(
                    "advance_time",
                    {"world_id": world_id, "session_id": session_id, "minutes": minutes},
                    fallback=lambda: self.world_runtime.advance_time(world_id, session_id, minutes)
                    if self.world_runtime
                    else {"error": "时间系统未初始化"},
                )
                if self.world_runtime:
                    await self.world_runtime.refresh_state(world_id, session_id)
                delta = self._build_state_delta("update_time", {"game_time": result.get("time")})
                return FlashResponse(success=bool(result.get("success", True)), operation=op, result=result, state_delta=delta)

            if op == FlashOperation.ENTER_SUBLOCATION:
                result = await self._call_tool_with_fallback(
                    "enter_sublocation",
                    {
                        "world_id": world_id,
                        "session_id": session_id,
                        "sub_location_id": params.get("sub_location_id"),
                    },
                    fallback=lambda: self.world_runtime.enter_sub_location(
                        world_id=world_id,
                        session_id=session_id,
                        sub_location_id=params.get("sub_location_id"),
                    )
                    if self.world_runtime
                    else {"success": False, "error": "子地点系统未初始化"},
                )
                if self.world_runtime:
                    await self.world_runtime.refresh_state(world_id, session_id)
                return FlashResponse(success=bool(result.get("success")), operation=op, result=result)

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
                result = await self._call_tool_with_fallback(
                    "recall_memory",
                    {
                        "world_id": world_id,
                        "character_id": params.get("character_id"),
                        "request": recall_request.model_dump(),
                    },
                    fallback=lambda: self.flash_service.recall_memory(
                        world_id=world_id,
                        character_id=params.get("character_id"),
                        request=recall_request,
                    ),
                )
                payload = result.model_dump() if hasattr(result, "model_dump") else result
                return FlashResponse(success=True, operation=op, result=payload)

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
                        "tier": params.get("tier", "main"),
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
                "tier": "main",
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
        """直接调用 fallback，跳过 MCP 子进程（MCP 工具和 fallback 做的是同样的事情）"""
        # MCP 客户端库有 bug，且 MCP 工具本质上只是调用同样的方法
        # 所以直接使用 fallback 更优雅、更快
        result = fallback()
        if hasattr(result, "__await__"):
            return await result
        return result

    async def _call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]):
        command = getattr(settings, "mcp_tools_command", "python")
        args_raw = getattr(settings, "mcp_tools_args", "-m app.mcp.game_tools_server")
        args = shlex.split(args_raw) if isinstance(args_raw, str) else list(args_raw)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            cwd=str(self._mcp_server_root),
        )

        async with stdio_client(server_params) as (read_stream, write_stream):
            session = ClientSession(read_stream, write_stream)
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return self._decode_tool_result(result)

    async def _call_combat_tool_with_fallback(self, tool_name: str, arguments: Dict[str, Any], fallback):
        try:
            result = await self._call_combat_tool(tool_name, arguments)
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(result.get("error"))
            return result
        except Exception:
            result = fallback()
            if hasattr(result, "__await__"):
                return await result
            return result

    async def _call_combat_tool(self, tool_name: str, arguments: Dict[str, Any]):
        command = getattr(settings, "mcp_combat_command", "python")
        args_raw = getattr(settings, "mcp_combat_args", "-m app.combat.combat_mcp_server")
        args = shlex.split(args_raw) if isinstance(args_raw, str) else list(args_raw)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            cwd=str(self._mcp_server_root),
        )

        async with stdio_client(server_params) as (read_stream, write_stream):
            session = ClientSession(read_stream, write_stream)
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return self._decode_tool_result(result)

    async def call_combat_tool(self, tool_name: str, arguments: Dict[str, Any]):
        return await self._call_combat_tool(tool_name, arguments)

    def _decode_tool_result(self, result):
        if getattr(result, "structuredContent", None) is not None:
            return result.structuredContent
        if not result.content:
            return {}
        first = result.content[0]
        text = getattr(first, "text", "")
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            return {"raw": text}
