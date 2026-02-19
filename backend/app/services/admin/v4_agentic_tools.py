"""
V4 Agentic tool registry — tools operate via SessionRuntime/AreaRuntime API.

Changes from V3 (agentic_tools.py):
- navigate/enter_sublocation → SessionRuntime.enter_area/enter_sublocation
- player state tools → SessionRuntime.player direct modification
- update_time → SessionRuntime.update_time
- recall_memory → simplified to area + character scopes only
- New: activate_event, complete_event, advance_chapter, update_disposition, create_memory
- Removed: evaluate_story_conditions, get_progress, get_status (data in layered context)
"""
import asyncio
import functools
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.admin_protocol import AgenticToolCall
from app.models.graph import MemoryNode
from app.models.graph_scope import GraphScope
from app.services.image_generation_service import ImageGenerationService
from app.world.models import EventStatus

logger = logging.getLogger(__name__)

# 意图 → 排除工具映射（引擎已执行的操作对应的工具不暴露给 LLM）
_ENGINE_TOOL_EXCLUSIONS: Dict[str, set] = {
    "move_area": {"navigate", "enter_sublocation", "leave_sublocation"},
    "move_sublocation": {"enter_sublocation"},
    "leave": {"leave_sublocation"},
    "rest": {"update_time"},
    "talk": {"npc_dialogue"},
}


class V4AgenticToolRegistry:
    """V4 agentic tool registry — tools backed by SessionRuntime/AreaRuntime."""

    def __init__(
        self,
        *,
        session: Any,           # SessionRuntime
        flash_cpu: Any,         # FlashCpuService (for combat/NPC dialogue MCP calls)
        graph_store: Any,       # GraphStore (for memory/disposition)
        recall_orchestrator: Optional[Any] = None,
        image_service: Optional[ImageGenerationService] = None,
        event_queue: Optional[asyncio.Queue] = None,
        engine_executed: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.session = session
        self.flash_cpu = flash_cpu
        self.graph_store = graph_store
        self.recall_orchestrator = recall_orchestrator
        self.image_service = image_service or ImageGenerationService()
        self._event_queue = event_queue
        self._engine_executed = engine_executed

        self.tool_calls: List[AgenticToolCall] = []
        self.image_data: Optional[Dict[str, Any]] = None

        self._lock = asyncio.Lock()
        self._image_generated_this_turn = False

    # Convenience accessors
    @property
    def world_id(self) -> str:
        return self.session.world_id

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def area(self) -> Optional[Any]:
        """Current AreaRuntime."""
        return self.session.current_area

    # =========================================================================
    # Tool registration
    # =========================================================================

    @staticmethod
    def _normalize_advance_minutes(raw_minutes: int) -> int:
        allowed = [5, 10, 15, 30, 60, 120, 180, 240, 360, 480, 720]
        minutes = max(1, min(int(raw_minutes), 720))
        return min(allowed, key=lambda value: abs(value - minutes))

    @staticmethod
    def _parse_travel_time(travel_time: str) -> float:
        """Parse travel time string to minutes."""
        time_str = travel_time.lower()
        if "分钟" in time_str or "minutes" in time_str:
            try:
                return float("".join(filter(str.isdigit, time_str))) or 30
            except ValueError:
                return 30
        elif "小时" in time_str or "hour" in time_str:
            try:
                hours = float("".join(filter(str.isdigit, time_str))) or 1
                return hours * 60
            except ValueError:
                return 60
        elif "半天" in time_str or "half day" in time_str:
            return 360
        elif "一天" in time_str or "day" in time_str:
            return 720
        else:
            return 30

    @staticmethod
    def _get_period(hour: int) -> str:
        """Get time period from hour."""
        if 5 <= hour < 8:
            return "dawn"
        elif 8 <= hour < 18:
            return "day"
        elif 18 <= hour < 20:
            return "dusk"
        else:
            return "night"

    def _wrap_tool_for_afc(self, tool_fn):
        """Wrap tool for AFC error recording + engine-executed safety net."""
        registry = self

        @functools.wraps(tool_fn)
        async def wrapper(**kwargs):
            # 安全网：如果工具已被引擎执行但 LLM 仍然调用，短路返回
            if registry._engine_executed:
                excluded = _ENGINE_TOOL_EXCLUSIONS.get(
                    registry._engine_executed.get("type", ""), set()
                )
                if tool_fn.__name__ in excluded:
                    started = time.perf_counter()
                    payload = {"success": True, "already_executed_by_engine": True}
                    await registry._record(
                        tool_fn.__name__, kwargs, started, payload,
                        success=True, error="blocked_by_engine_filter",
                    )
                    return payload

            started = time.perf_counter()
            # Gemini 有时把参数名中的单下划线序列化为双下划线（如 event_id → event__id），
            # 在这里规范化，确保参数名与函数签名一致。
            normalized = {k.replace("__", "_"): v for k, v in kwargs.items()}
            try:
                return await tool_fn(**normalized)
            except TypeError as exc:
                error_msg = f"argument error: {tool_fn.__name__}: {exc}"
                logger.warning("[agentic] AFC %s", error_msg)
                payload = {"success": False, "error": error_msg}
                await registry._record(tool_fn.__name__, normalized, started, payload, False, error_msg)
                raise

        wrapper.__annotations__ = tool_fn.__annotations__
        return wrapper

    def get_tools(self) -> List[Any]:
        """Return all tool callables exposed to the LLM."""
        raw_tools = [
            # Navigation
            self.navigate,
            self.enter_sublocation,
            self.leave_sublocation,
            # NPC
            self.npc_dialogue,
            # Memory
            self.recall_memory,
            self.create_memory,
            # Time
            self.update_time,
            # Player state
            self.heal_player,
            self.damage_player,
            self.add_xp,
            self.add_item,
            self.remove_item,
            # Combat
            self.start_combat,
            self.get_combat_options,
            self.choose_combat_action,
            # Party
            self.add_teammate,
            self.remove_teammate,
            self.disband_party,
            # Skill check
            self.ability_check,
            # Events & chapters
            self.activate_event,
            self.complete_event,
            self.fail_event,
            self.advance_chapter,
            self.complete_objective,
            self.advance_stage,
            self.complete_event_objective,
            self.report_flash_evaluation,
            # Disposition
            self.update_disposition,
            # Image
            self.generate_scene_image,
        ]

        # 过滤引擎已执行的对应工具
        if self._engine_executed:
            exec_type = self._engine_executed.get("type", "")
            excluded = _ENGINE_TOOL_EXCLUSIONS.get(exec_type, set())
            if excluded:
                raw_tools = [t for t in raw_tools if t.__name__ not in excluded]

        return [self._wrap_tool_for_afc(t) for t in raw_tools]

    def get_tool_name_map(self) -> Dict[str, Any]:
        return {tool.__name__: tool for tool in self.get_tools()}

    # =========================================================================
    # Execution / recording helpers
    # =========================================================================

    async def execute_tool_call(self, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        started = time.perf_counter()
        tool_name = str(name or "").strip()
        safe_args = dict(args) if isinstance(args, dict) else {}
        if not tool_name:
            payload = {"success": False, "error": "missing tool name"}
            await self._record("tool_dispatch", {"name": name, "args": safe_args}, started, payload, False, payload["error"])
            return payload

        tool = self.get_tool_name_map().get(tool_name)
        if tool is None:
            payload = {"success": False, "error": f"unknown tool: {tool_name}"}
            await self._record(tool_name, safe_args, started, payload, False, payload["error"])
            return payload

        try:
            result = await tool(**safe_args)
        except TypeError as exc:
            payload = {"success": False, "error": f"invalid args for {tool_name}: {exc}"}
            await self._record(tool_name, safe_args, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            payload = {"success": False, "error": f"tool error: {tool_name}: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record(tool_name, safe_args, started, payload, False, payload["error"])
            return payload

        if isinstance(result, dict):
            return result
        return {"success": True, "result": result}

    async def _record(
        self,
        name: str,
        args: Dict[str, Any],
        started_at: float,
        result: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        call = AgenticToolCall(
            name=name,
            args=args,
            success=success,
            duration_ms=duration_ms,
            error=error,
            result=result or {},
        )
        async with self._lock:
            self.tool_calls.append(call)
            tool_index = len(self.tool_calls)
        # Push real-time event to SSE queue
        if self._event_queue is not None:
            event = {
                "type": "agentic_tool_call",
                "name": name,
                "success": success,
                "duration_ms": duration_ms,
                "error": error,
                "tool_index": tool_index,
            }
            # 好感度变更明细 → 前端实时通知
            if name == "update_disposition" and success and isinstance(result, dict):
                event["disposition_change"] = {
                    "npc_id": result.get("npc_id", args.get("npc_id", "")),
                    "deltas": result.get("applied_deltas", {}),
                    "current": {
                        k: v for k, v in result.get("current", {}).items()
                        if k in ("approval", "trust", "fear", "romance")
                    },
                }
            # 掷骰结果明细 → 前端实时骰子动画
            if name == "ability_check" and isinstance(result, dict) and "roll" in result:
                event["dice_result"] = {
                    "roll": result.get("roll"),
                    "ability": result.get("ability"),
                    "skill": result.get("skill"),
                    "modifier": result.get("modifier"),
                    "proficiency": result.get("proficiency"),
                    "total": result.get("total"),
                    "dc": result.get("dc"),
                    "success": result.get("success"),
                    "is_critical": result.get("is_critical"),
                    "is_fumble": result.get("is_fumble"),
                    "description": result.get("description"),
                }
            await self._event_queue.put(event)
            # NPC 对话结果 → 前端即时推送对话气泡
            if name == "npc_dialogue" and success and isinstance(result, dict) and result.get("response"):
                char_data = self.session.world.get_character(args.get("npc_id", "")) if self.session.world else None
                npc_name = (char_data or {}).get("name", result.get("npc_name", args.get("npc_id", "")))
                await self._event_queue.put({
                    "type": "npc_response",
                    "character_id": args.get("npc_id", ""),
                    "name": npc_name,
                    "dialogue": result["response"],
                    "message": args.get("message", ""),
                })

    async def _timed(self, name: str, args: Dict[str, Any], coro) -> Dict[str, Any]:
        """Execute coroutine with timing, recording, and error handling."""
        started = time.perf_counter()
        try:
            payload = await asyncio.wait_for(
                coro,
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": f"tool timeout: {name}"}
            await self._record(name, args, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("tool %s raised: %s", name, exc, exc_info=True)
            payload = {"success": False, "error": f"tool error: {name}: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record(name, args, started, payload, False, payload["error"])
            return payload

        if not isinstance(payload, dict):
            payload = {"success": True, "result": payload}
        payload.setdefault("success", True)
        await self._record(name, args, started, payload, payload.get("success", True), payload.get("error"))
        return payload

    # =========================================================================
    # Navigation tools
    # =========================================================================

    async def navigate(self, destination: str) -> Dict[str, Any]:
        """Navigate to a destination area.

        Args:
            destination: Area id or name from context `area_context.connections`.

        Usage:
            - Use for map-level movement between areas.
            - Use `enter_sublocation` for in-area sub-locations.
        """
        from app.models.state_delta import GameTimeState

        started = time.perf_counter()
        area_id = self._resolve_area_id(destination)
        args = {"destination": destination, "resolved_area_id": area_id}

        # --- Validation 1: chapter gate (available_maps) ---
        chapter_id = self.session.chapter_id
        world = self.session.world
        if chapter_id and world and hasattr(world, "chapter_registry"):
            chapter_data = world.chapter_registry.get(chapter_id)
            if chapter_data is not None:
                if isinstance(chapter_data, dict):
                    available_maps = chapter_data.get("available_maps", [])
                else:
                    available_maps = getattr(chapter_data, "available_maps", []) or []
                if available_maps and area_id not in available_maps:
                    payload = {
                        "success": False,
                        "error": f"area '{area_id}' not available in chapter '{chapter_id}'",
                        "available_areas": list(available_maps),
                    }
                    await self._record("navigate", args, started, payload, False, payload["error"])
                    return payload

        # --- Validation 2: connection check (P8: 从 WorldGraph CONNECTS 边读) ---
        from app.world.models import WorldEdgeType
        edge_props: Optional[Dict[str, Any]] = None
        current_area_id = self.session.player_location
        old_sub_location = self.session.sub_location  # P1: 保存旧子地点供 exit ctx 使用

        wg = getattr(self.session, "world_graph", None)
        if wg and not getattr(self.session, "_world_graph_failed", False) and current_area_id:
            for neighbor_id, edata in wg.get_neighbors(current_area_id, WorldEdgeType.CONNECTS.value):
                if neighbor_id == area_id:
                    edge_props = edata
                    break
            if edge_props is None:
                available_conns = [
                    {"area_id": nid, "travel_time": ed.get("travel_time", "30 minutes")}
                    for nid, ed in wg.get_neighbors(current_area_id, WorldEdgeType.CONNECTS.value)
                ]
                payload = {
                    "success": False,
                    "error": f"no connection from current area to '{area_id}'",
                    "available_connections": available_conns,
                }
                await self._record("navigate", args, started, payload, False, payload["error"])
                return payload

            # P8: blocked 检查骨架（等管线数据填充）
            if edge_props.get("blocked"):
                payload = {
                    "success": False,
                    "error": f"道路被封锁: {edge_props.get('blocked_reason', '前方不可通行')}",
                }
                await self._record("navigate", args, started, payload, False, payload["error"])
                return payload
        elif self.area and self.area.definition:
            # 降级：WorldGraph 不可用时回退旧路径
            for conn in self.area.definition.connections:
                if conn.target_area_id == area_id:
                    edge_props = {
                        "travel_time": conn.travel_time,
                        "connection_type": conn.connection_type,
                    }
                    break
            if edge_props is None:
                available_conns = [
                    {"area_id": c.target_area_id, "travel_time": c.travel_time}
                    for c in self.area.definition.connections
                ]
                payload = {
                    "success": False,
                    "error": f"no connection from current area to '{area_id}'",
                    "available_connections": available_conns,
                }
                await self._record("navigate", args, started, payload, False, payload["error"])
                return payload

        # --- Travel time advancement ---
        travel_minutes = 30
        if edge_props:
            raw = self._parse_travel_time(edge_props.get("travel_time") or "30 minutes")
            travel_minutes = self._normalize_advance_minutes(int(raw))

        # Advance game time
        current_time = self.session.time
        if current_time:
            total = current_time.hour * 60 + current_time.minute + travel_minutes
            new_day = current_time.day + total // (24 * 60)
            remaining = total % (24 * 60)
            new_hour = remaining // 60
            new_minute = remaining % 60
        else:
            new_day, new_hour, new_minute = 1, 8, 0

        period = self._get_period(new_hour)
        formatted = f"第{new_day}天 {new_hour:02d}:{new_minute:02d}"
        new_time = GameTimeState(
            day=new_day, hour=new_hour, minute=new_minute,
            period=period, formatted=formatted,
        )
        self.session.update_time(new_time)

        # --- Execute area switch ---
        try:
            result = await asyncio.wait_for(
                self.session.enter_area(area_id),
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": "tool timeout: navigate"}
            await self._record("navigate", args, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("navigate raised: %s", exc, exc_info=True)
            payload = {"success": False, "error": f"navigate error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record("navigate", args, started, payload, False, payload["error"])
            return payload

        if not isinstance(result, dict):
            result = {"success": True, "result": result}
        result.setdefault("success", True)
        result["travel_time_minutes"] = travel_minutes
        if edge_props:
            conn_type = edge_props.get("connection_type")
            if conn_type:
                result["connection_type"] = conn_type

        # C7c: 更新 WorldGraph 区域状态
        if result.get("success"):
            wg = getattr(self.session, "world_graph", None)
            if wg and not getattr(self.session, "_world_graph_failed", False) and wg.has_node(area_id):
                try:
                    wg.merge_state(area_id, {"visited": True})
                    old_count = wg.get_node(area_id).state.get("visit_count", 0)
                    wg.set_state(area_id, "visit_count", old_count + 1)
                except Exception:
                    pass  # 不阻塞导航

            # --- P1: ON_EXIT / ON_ENTER + HOSTS 边管理 ---
            engine = getattr(self.session, "_behavior_engine", None)
            is_area_change = current_area_id and current_area_id != area_id
            if engine and wg and not getattr(self.session, "_world_graph_failed", False) and is_area_change:
                try:
                    # 1. handle_exit（旧区域）— 覆写 ctx 为旧位置+旧子地点
                    exit_result = None
                    if wg.has_node(current_area_id):
                        ctx_exit = self.session.build_tick_context("post")
                        if ctx_exit:
                            ctx_exit.player_location = current_area_id
                            ctx_exit.player_sub_location = old_sub_location or ""
                            exit_result = engine.handle_exit("player", current_area_id, ctx_exit)

                    # 2. 更新 HOSTS 边（player + 队友）
                    self._update_hosts_edges(wg, current_area_id, area_id)
                    self._update_party_hosts_edges(wg, area_id)

                    # 3. handle_enter（新区域）
                    enter_result = None
                    if wg.has_node(area_id):
                        ctx_enter = self.session.build_tick_context("post")
                        if ctx_enter:
                            enter_result = engine.handle_enter("player", area_id, ctx_enter)

                    # 4. 处理 TickResult
                    hints = []
                    for tr in (exit_result, enter_result):
                        if tr:
                            hints.extend(tr.narrative_hints)
                            self.session._sync_tick_to_narrative(tr)
                            self.session._apply_tick_side_effects(tr)
                    if hints:
                        result["narrative_hints"] = hints
                except Exception as exc:
                    logger.warning("[navigate] P1 enter/exit handling failed: %s", exc)

        await self._record("navigate", args, started, result, result.get("success", True), result.get("error"))
        return result

    def _resolve_area_id(self, destination: str) -> str:
        """Resolve area name to area_id. Returns destination as-is if no match.

        P8: 优先从 WorldGraph CONNECTS 边查询，降级到旧注册表。
        """
        from app.world.models import WorldEdgeType

        current_area_id = self.session.player_location
        wg = getattr(self.session, "world_graph", None)

        # Priority 1: WorldGraph CONNECTS 边精确 ID 匹配
        if wg and not getattr(self.session, "_world_graph_failed", False) and current_area_id:
            for neighbor_id, _ in wg.get_neighbors(current_area_id, WorldEdgeType.CONNECTS.value):
                if neighbor_id == destination:
                    return destination

            # Priority 2: WorldGraph CONNECTS 边按节点 name 匹配
            for neighbor_id, _ in wg.get_neighbors(current_area_id, WorldEdgeType.CONNECTS.value):
                node = wg.get_node(neighbor_id)
                if node and node.name == destination:
                    return neighbor_id

            # Priority 3: WorldGraph 全局 area 节点按 ID/name 匹配
            from app.world.models import WorldNodeType
            for nid in wg.get_by_type(WorldNodeType.AREA.value):
                if nid == destination:
                    return nid
                node = wg.get_node(nid)
                if node and node.name == destination:
                    return nid

            return destination

        # 降级：WorldGraph 不可用时使用旧注册表
        world = self.session.world
        if self.area and self.area.definition:
            for conn in self.area.definition.connections:
                if conn.target_area_id == destination:
                    return destination

        if self.area and self.area.definition and world and hasattr(world, "area_registry"):
            for conn in self.area.definition.connections:
                area_def = world.area_registry.get(conn.target_area_id)
                if area_def and getattr(area_def, "name", "") == destination:
                    return conn.target_area_id

        if world and hasattr(world, "area_registry"):
            for aid, area_def in world.area_registry.items():
                if aid == destination:
                    return aid
                name = area_def.name if hasattr(area_def, "name") else ""
                if name == destination:
                    return aid
        return destination

    async def enter_sublocation(self, sub_location: str) -> Dict[str, Any]:
        """Enter a sub-location within the current area.

        Args:
            sub_location: Sub-location id or name from context `area_context.sub_locations`.

        Usage:
            - Use when player enters a building/room/POI inside current area.
        """
        args = {"sub_location": sub_location}
        return await self._timed("enter_sublocation", args, self.session.enter_sublocation(sub_location))

    async def leave_sublocation(self) -> Dict[str, Any]:
        """Leave the current sub-location, returning to area main map.

        Usage:
            - Use when player leaves a building/room/POI to return to the area.
        """
        return await self._timed("leave_sublocation", {}, self.session.leave_sublocation())

    # =========================================================================
    # NPC dialogue
    # =========================================================================

    async def npc_dialogue(self, npc_id: str, message: str) -> Dict[str, Any]:
        """Talk to an NPC.

        Args:
            npc_id: NPC character_id from `area_context.npcs` or memory.
            message: What to say / action to convey.

        Usage:
            - Use when narrative needs concrete NPC response.
            - Do NOT use for teammates (teammate system handles them automatically).
        """
        from app.models.admin_protocol import FlashOperation, FlashRequest, FlashResponse
        started = time.perf_counter()
        request = FlashRequest(operation=FlashOperation.NPC_DIALOGUE, parameters={"npc_id": npc_id, "message": message})
        try:
            response = await asyncio.wait_for(
                self.flash_cpu.execute_request(
                    world_id=self.world_id,
                    session_id=self.session_id,
                    request=request,
                    generate_narration=False,
                ),
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": "tool timeout: npc_dialogue"}
            await self._record("npc_dialogue", {"npc_id": npc_id, "message": message}, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("npc_dialogue raised: %s", exc, exc_info=True)
            payload = {"success": False, "error": f"npc_dialogue error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record("npc_dialogue", {"npc_id": npc_id, "message": message}, started, payload, False, payload["error"])
            return payload

        payload = response.result if isinstance(response.result, dict) else {"raw": response.result}
        payload["success"] = response.success
        if response.error:
            payload["error"] = response.error

        # Resolve NPC display name from character registry
        char_data = None
        if self.session.world:
            char_data = self.session.world.get_character(npc_id)
        npc_name = (char_data or {}).get("name", npc_id) if isinstance(char_data, dict) else npc_id
        payload["npc_name"] = npc_name

        # Fix #22: increment npc_interactions count
        if response.success and self.session.narrative:
            count = self.session.narrative.npc_interactions.get(npc_id, 0)
            self.session.narrative.npc_interactions[npc_id] = count + 1
            self.session.mark_narrative_dirty()

        await self._record("npc_dialogue", {"npc_id": npc_id, "message": message}, started, payload, response.success, response.error)
        return payload

    # =========================================================================
    # Memory tools
    # =========================================================================

    async def recall_memory(
        self,
        seeds: List[str],
        character_id: str = "player",
    ) -> Dict[str, Any]:
        """Recall memory subgraph for given concept seeds.

        Args:
            seeds: Concept seeds, e.g. ["forest", "goblin"]. 2-6 focused seeds.
            character_id: Character id to query memory from. Default "player".

        Usage:
            - Use before major decisions when history matters.
            - Simplified to area + character two scopes only.
        """
        started = time.perf_counter()
        norm_seeds = [str(s).strip() for s in (seeds or []) if str(s).strip()]
        if not norm_seeds:
            payload = {"success": False, "error": "missing seeds"}
            await self._record("recall_memory", {"seeds": seeds, "character_id": character_id}, started, payload, False, payload["error"])
            return payload

        chapter_id = self.session.chapter_id
        area_id = self.session.area_id

        try:
            if self.recall_orchestrator is not None:
                recall = await asyncio.wait_for(
                    self.recall_orchestrator.recall(
                        world_id=self.world_id,
                        character_id=character_id,
                        seed_nodes=norm_seeds,
                        intent_type="roleplay",
                        chapter_id=chapter_id,
                        area_id=area_id,
                    ),
                    timeout=settings.admin_agentic_tool_timeout_seconds,
                )
            else:
                from app.models.flash import RecallRequest
                request = RecallRequest(
                    seed_nodes=norm_seeds,
                    include_subgraph=True,
                    resolve_refs=True,
                    use_subgraph=True,
                    subgraph_depth=2,
                )
                recall = await asyncio.wait_for(
                    self.flash_cpu.flash_service.recall_memory(
                        world_id=self.world_id,
                        character_id=character_id,
                        request=request,
                    ),
                    timeout=settings.admin_agentic_tool_timeout_seconds,
                )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": "tool timeout: recall_memory"}
            await self._record("recall_memory", {"seeds": norm_seeds, "character_id": character_id}, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("recall_memory raised: %s", exc, exc_info=True)
            payload = {"success": False, "error": f"recall_memory error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record("recall_memory", {"seeds": norm_seeds, "character_id": character_id}, started, payload, False, payload["error"])
            return payload

        payload = recall.model_dump() if hasattr(recall, "model_dump") else {"result": recall}
        payload["success"] = True
        await self._record("recall_memory", {"seeds": norm_seeds, "character_id": character_id}, started, payload, True, None)
        return payload

    async def create_memory(
        self,
        content: str,
        importance: float = 0.5,
        scope: str = "area",
        related_entities: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a memory node in the knowledge graph.

        Args:
            content: Memory content text.
            importance: Importance score 0.0-1.0 (default 0.5).
            scope: "area" or "character" (default "area").
            related_entities: Optional list of entity ids to link.

        Usage:
            - Use to record significant plot developments, discoveries, or player decisions.
            - Prefer "area" scope for location-bound events, "character" for personal experiences.
        """
        started = time.perf_counter()
        args = {"content": content, "importance": importance, "scope": scope, "related_entities": related_entities}

        if not content or not content.strip():
            payload = {"success": False, "error": "empty content"}
            await self._record("create_memory", args, started, payload, False, payload["error"])
            return payload

        importance = max(0.0, min(1.0, float(importance)))
        node_id = f"mem_{uuid.uuid4().hex[:12]}"

        node = MemoryNode(
            id=node_id,
            type="memory",
            name=content[:80],
            importance=importance,
            properties={
                "content": content,
                "source": "gm_created",
                "related_entities": list(related_entities or []),
                "created_by": "agentic_tool",
            },
        )

        # Determine graph scope — only area + character are active scopes
        chapter_id = self.session.chapter_id
        area_id = self.session.area_id
        if scope == "character":
            graph_scope = GraphScope.character("player")
        elif chapter_id and area_id:
            graph_scope = GraphScope.area(chapter_id, area_id)
        else:
            # Missing area info → fall back to player personal memory
            # rather than leaking to chapter/world shared scopes
            graph_scope = GraphScope.character("player")

        try:
            await asyncio.wait_for(
                self.graph_store.upsert_node_v2(
                    world_id=self.world_id,
                    scope=graph_scope,
                    node=node,
                    merge=True,
                ),
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": "tool timeout: create_memory"}
            await self._record("create_memory", args, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("create_memory raised: %s", exc, exc_info=True)
            payload = {"success": False, "error": f"create_memory error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record("create_memory", args, started, payload, False, payload["error"])
            return payload

        payload = {"success": True, "node_id": node_id, "scope": scope}
        await self._record("create_memory", args, started, payload, True, None)
        return payload

    # =========================================================================
    # Time
    # =========================================================================

    async def update_time(self, minutes: int = 30) -> Dict[str, Any]:
        """Advance game time in minutes.

        Args:
            minutes: Minutes to advance (auto-normalized to allowed buckets, max 720).

        Rules:
            - Disallow in combat.
        """
        started = time.perf_counter()
        args = {"minutes": minutes}

        if self.session.game_state and getattr(self.session.game_state, "combat_id", None):
            payload = {"success": False, "error": "cannot advance time during combat"}
            await self._record("update_time", args, started, payload, False, payload["error"])
            return payload

        normalized = self._normalize_advance_minutes(int(minutes))

        # Use FlashCPU for time update (it handles Firestore persistence)
        from app.models.admin_protocol import FlashOperation, FlashRequest
        request = FlashRequest(operation=FlashOperation.UPDATE_TIME, parameters={"minutes": normalized})
        try:
            response = await asyncio.wait_for(
                self.flash_cpu.execute_request(
                    world_id=self.world_id,
                    session_id=self.session_id,
                    request=request,
                    generate_narration=False,
                ),
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": "tool timeout: update_time"}
            await self._record("update_time", args, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("update_time raised: %s", exc, exc_info=True)
            payload = {"success": False, "error": f"update_time error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record("update_time", args, started, payload, False, payload["error"])
            return payload

        payload = response.result if isinstance(response.result, dict) else {"raw": response.result}
        payload["success"] = response.success
        payload["requested_minutes"] = int(minutes)
        payload["applied_minutes"] = normalized
        if response.error:
            payload["error"] = response.error

        # Sync time to SessionRuntime
        if response.success and self.session.game_state:
            self.session.time = self.session.game_state.game_time
            self.session.mark_game_state_dirty()

        await self._record("update_time", args, started, payload, response.success, response.error)
        return payload

    # =========================================================================
    # Player state tools (via SessionRuntime.player)
    # =========================================================================

    async def heal_player(self, amount: int) -> Dict[str, Any]:
        """Heal player HP.

        Args:
            amount: HP to restore.

        Usage:
            - Use after rest/heal item/spell is narratively confirmed.
        """
        started = time.perf_counter()
        args = {"amount": int(amount)}
        player = self.session.player
        if not player:
            payload = {"success": False, "error": "player not loaded"}
            await self._record("heal_player", args, started, payload, False, payload["error"])
            return payload

        old_hp = player.current_hp
        player.current_hp = min(player.max_hp, player.current_hp + int(amount))
        self.session.mark_player_dirty()

        payload = {"success": True, "old_hp": old_hp, "new_hp": player.current_hp, "max_hp": player.max_hp}
        await self._record("heal_player", args, started, payload, True, None)
        return payload

    async def damage_player(self, amount: int) -> Dict[str, Any]:
        """Damage player HP.

        Args:
            amount: HP to remove.

        Usage:
            - Use when hazard/trap consequence is explicit.
        """
        started = time.perf_counter()
        args = {"amount": int(amount)}
        player = self.session.player
        if not player:
            payload = {"success": False, "error": "player not loaded"}
            await self._record("damage_player", args, started, payload, False, payload["error"])
            return payload

        old_hp = player.current_hp
        player.current_hp = max(0, player.current_hp - int(amount))
        self.session.mark_player_dirty()

        payload = {"success": True, "old_hp": old_hp, "new_hp": player.current_hp, "max_hp": player.max_hp}
        await self._record("damage_player", args, started, payload, True, None)
        return payload

    async def add_xp(self, amount: int) -> Dict[str, Any]:
        """Add XP to player.

        Args:
            amount: XP to grant.

        Usage:
            - Use for completed encounters/objectives/milestones.
        """
        started = time.perf_counter()
        args = {"amount": int(amount)}
        player = self.session.player
        if not player:
            payload = {"success": False, "error": "player not loaded"}
            await self._record("add_xp", args, started, payload, False, payload["error"])
            return payload

        old_xp = player.xp
        player.xp += int(amount)
        self.session.mark_player_dirty()

        payload = {"success": True, "old_xp": old_xp, "new_xp": player.xp, "xp_to_next": player.xp_to_next_level}
        await self._record("add_xp", args, started, payload, True, None)
        return payload

    async def add_item(self, item_id: str, item_name: str, quantity: int = 1) -> Dict[str, Any]:
        """Add item to player inventory.

        Args:
            item_id: Lowercase snake_case id, e.g. "healing_potion".
            item_name: Display name.
            quantity: How many (default 1).

        Usage:
            - Use when loot/reward/purchase is confirmed in story.
        """
        started = time.perf_counter()
        args = {"item_id": item_id, "item_name": item_name, "quantity": int(quantity)}
        player = self.session.player
        if not player:
            payload = {"success": False, "error": "player not loaded"}
            await self._record("add_item", args, started, payload, False, payload["error"])
            return payload

        item = player.add_item(item_id, item_name, int(quantity))
        self.session.mark_player_dirty()

        payload = {"success": True, "item": item}
        await self._record("add_item", args, started, payload, True, None)
        return payload

    async def remove_item(self, item_id: str, quantity: int = 1) -> Dict[str, Any]:
        """Remove item from player inventory.

        Args:
            item_id: Item id to remove.
            quantity: How many (default 1).

        Usage:
            - Use for consumption/crafting/payment after narratively confirmed.
        """
        started = time.perf_counter()
        args = {"item_id": item_id, "quantity": int(quantity)}
        player = self.session.player
        if not player:
            payload = {"success": False, "error": "player not loaded"}
            await self._record("remove_item", args, started, payload, False, payload["error"])
            return payload

        removed = player.remove_item(item_id, int(quantity))
        if not removed:
            payload = {"success": False, "error": f"item not found: {item_id}"}
            await self._record("remove_item", args, started, payload, False, payload["error"])
            return payload

        self.session.mark_player_dirty()
        payload = {"success": True, "removed": item_id, "quantity": int(quantity)}
        await self._record("remove_item", args, started, payload, True, None)
        return payload

    # =========================================================================
    # Combat tools (delegate to FlashCPU MCP calls)
    # =========================================================================

    async def start_combat(self, enemies: list[dict]) -> dict:
        """Start combat with structured enemy specs.

        Args:
            enemies: List of enemy dicts with enemy_id, count, level.

        Usage:
            - Use only when hostile conflict is explicitly initiated.
        """
        from app.models.admin_protocol import FlashOperation, FlashRequest
        enemy_payload: List[Dict[str, Any]] = []
        for enemy in enemies or []:
            if isinstance(enemy, str) and enemy.strip():
                enemy_payload.append({"enemy_id": enemy.strip(), "count": 1, "level": 1})
                continue
            if isinstance(enemy, dict):
                enemy_id = str(enemy.get("enemy_id") or enemy.get("type") or "").strip()
                if not enemy_id:
                    continue
                try:
                    count = int(enemy.get("count", 1) or 1)
                except (TypeError, ValueError):
                    count = 1
                try:
                    level = int(enemy.get("level", 1) or 1)
                except (TypeError, ValueError):
                    level = 1
                enemy_payload.append({
                    "enemy_id": enemy_id,
                    "count": max(1, min(count, 20)),
                    "level": max(1, min(level, 20)),
                    "variant": enemy.get("variant"),
                    "template_version": enemy.get("template_version"),
                    "tags": list(enemy.get("tags", []) or []),
                    "overrides": dict(enemy.get("overrides", {}) or {}),
                })

        # U2: 从图节点适配器获取 player_state，不再依赖 FlashCPU 从 character_store 读
        player_state = {}
        if self.session.player:
            try:
                player_state = self.session.player.to_combat_player_state()
            except Exception as exc:
                logger.warning("[start_combat] 从图节点获取 player_state 失败: %s", exc)
        args = {"enemies": enemy_payload, "player_state": player_state}
        request = FlashRequest(operation=FlashOperation.START_COMBAT, parameters=args)
        return await self._execute_flash_request("start_combat", args, request)

    async def _resolve_active_combat_id(self) -> Optional[str]:
        if self.session.game_state:
            return getattr(self.session.game_state, "combat_id", None)
        return None

    @staticmethod
    def _to_v3_action(action: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "action_id": action.get("action_id"),
            "action_type": action.get("type"),
            "display_name": action.get("display_name"),
            "description": action.get("description", ""),
            "target_id": action.get("target_id"),
            "requirements": {},
            "resource_cost": {"type": action.get("cost_type")},
            "hit_formula": None,
            "damage_formula": action.get("damage_type"),
            "effect_refs": [],
            "metadata": {
                "range_band": action.get("range_band"),
                "success_rate": action.get("success_rate"),
            },
        }

    async def get_combat_options(self, actor_id: str = "player") -> Dict[str, Any]:
        """Get current combat actions and state for an actor.

        Args:
            actor_id: Combatant id (default "player").
        """
        started = time.perf_counter()
        combat_id = await self._resolve_active_combat_id()
        if not combat_id:
            payload = {"success": False, "error": "no active combat"}
            await self._record("get_combat_options", {"actor_id": actor_id}, started, payload, False, payload["error"])
            return payload

        try:
            actions_raw = await self.flash_cpu.call_combat_tool(
                "get_available_actions_for_actor",
                {"combat_id": combat_id, "actor_id": actor_id},
            )
            if actions_raw.get("error"):
                actions_raw = await self.flash_cpu.call_combat_tool(
                    "get_available_actions", {"combat_id": combat_id},
                )
            state_payload = await self.flash_cpu.call_combat_tool(
                "get_combat_state", {"combat_id": combat_id},
            )
        except Exception as exc:
            payload = {"success": False, "error": f"combat options error: {exc}"}
            await self._record("get_combat_options", {"actor_id": actor_id}, started, payload, False, payload["error"])
            return payload

        actions = actions_raw.get("actions", []) if isinstance(actions_raw, dict) else []
        payload = {
            "success": True,
            "combat_id": combat_id,
            "actor_id": actor_id,
            "actions": actions,
            "actions_v3": [self._to_v3_action(a) for a in actions],
            "combat_state": state_payload if isinstance(state_payload, dict) else {},
        }
        await self._record("get_combat_options", {"actor_id": actor_id}, started, payload, True, None)
        return payload

    async def choose_combat_action(self, action_id: str, actor_id: str = "player") -> Dict[str, Any]:
        """Execute combat action for actor in active combat.

        Args:
            action_id: Action to execute.
            actor_id: Combatant id (default "player").
        """
        started = time.perf_counter()
        combat_id = await self._resolve_active_combat_id()
        if not combat_id:
            payload = {"success": False, "error": "no active combat"}
            await self._record("choose_combat_action", {"action_id": action_id, "actor_id": actor_id}, started, payload, False, payload["error"])
            return payload

        try:
            payload = await self.flash_cpu.call_combat_tool(
                "execute_action_for_actor",
                {"combat_id": combat_id, "actor_id": actor_id, "action_id": action_id},
            )
            if isinstance(payload, dict) and payload.get("error"):
                payload = await self.flash_cpu.call_combat_tool(
                    "execute_action",
                    {"combat_id": combat_id, "action_id": action_id},
                )
        except Exception as exc:
            error_payload = {"success": False, "error": f"combat execute error: {exc}"}
            await self._record("choose_combat_action", {"action_id": action_id, "actor_id": actor_id}, started, error_payload, False, error_payload["error"])
            return error_payload

        if not isinstance(payload, dict):
            payload = {"success": False, "error": "invalid combat response"}

        combat_state = payload.get("combat_state", {})
        if combat_state.get("is_ended"):
            try:
                resolve_payload = await self.flash_cpu.call_combat_tool(
                    "resolve_combat_session_v3",
                    {
                        "world_id": self.world_id,
                        "session_id": self.session_id,
                        "combat_id": combat_id,
                        "dispatch": True,
                    },
                )
                sync_data = dict(resolve_payload) if isinstance(resolve_payload, dict) else {}
                sync_data["final_result"] = payload.get("final_result")
                # U2: 通过图节点适配器同步战斗结果
                self._sync_combat_to_graph(sync_data)
                # Clear combat_id in session state
                if self.session.game_state:
                    self.session.game_state.combat_id = None
                    self.session.mark_game_state_dirty()
                payload["resolve"] = resolve_payload
            except Exception as exc:
                payload["resolve_error"] = str(exc)

            # --- P6-层2: 战斗结束发射 WorldEvent ---
            try:
                wg = getattr(self.session, "world_graph", None)
                engine = getattr(self.session, "_behavior_engine", None)
                if wg and engine and not getattr(self.session, "_world_graph_failed", False):
                    from app.world.models import WorldEvent as WE
                    player_location = self.session.player_location or ""
                    ctx = self.session.build_tick_context("post")
                    if ctx and player_location:
                        final = payload.get("final_result") or {}
                        combat_event = WE(
                            event_type="combat_ended",
                            origin_node=player_location,
                            actor="player",
                            data={
                                "result": final.get("result", "unknown"),
                                "enemies": final.get("enemies_defeated", []),
                                "rewards": final.get("rewards", {}),
                                "combat_id": combat_id,
                            },
                            visibility="scope",
                            game_day=ctx.game_day,
                            game_hour=ctx.game_hour,
                        )
                        tick_result = engine.handle_event(combat_event, ctx)
                        if tick_result.narrative_hints:
                            payload["combat_event_hints"] = tick_result.narrative_hints
                        self.session._sync_tick_to_narrative(tick_result)
                        self.session._apply_tick_side_effects(tick_result)
            except Exception as exc:
                logger.warning("[combat] P6 combat_ended event failed: %s", exc)

        payload["success"] = not bool(payload.get("error"))
        await self._record("choose_combat_action", {"action_id": action_id, "actor_id": actor_id}, started, payload, payload["success"], payload.get("error"))
        return payload

    def _sync_combat_to_graph(self, combat_payload: Dict[str, Any]) -> None:
        """U2: 通过图节点适配器同步战斗结果到 player 图节点。

        从 combat_payload 提取 HP/XP/gold/items，写入 session.player（PlayerNodeView）。
        """
        player = self.session.player
        if not player:
            return

        try:
            # Sync HP
            player_state = combat_payload.get("player_state") or {}
            hp_remaining = player_state.get("hp_remaining")
            if hp_remaining is not None:
                player.current_hp = max(0, min(int(hp_remaining), player.max_hp))
                self.session.mark_player_dirty()

            # Extract rewards from final_result
            final_result = combat_payload.get("final_result") or combat_payload.get("result") or {}
            if isinstance(final_result, dict):
                result_type = final_result.get("result", "")
                rewards = final_result.get("rewards") or {}

                if result_type == "victory" and rewards:
                    xp = int(rewards.get("xp", 0))
                    if xp > 0:
                        player.xp = (player.xp or 0) + xp
                        self.session.mark_player_dirty()

                    gold = int(rewards.get("gold", 0))
                    if gold > 0:
                        player.gold = (player.gold or 0) + gold
                        self.session.mark_player_dirty()

                    for item_id in rewards.get("items", []):
                        player.add_item(item_id, item_id, 1)
                        self.session.mark_player_dirty()

            logger.info("[combat_sync] Synced combat results to player graph node")
        except Exception as exc:
            logger.error("[combat_sync] Failed to sync to graph: %s", exc, exc_info=True)

    # =========================================================================
    # P1: HOSTS edge management helpers
    # =========================================================================

    def _update_hosts_edges(self, wg, old_area_id, new_area_id):
        from app.world.intent_executor import update_hosts_edges
        update_hosts_edges(wg, old_area_id, new_area_id)

    def _update_party_hosts_edges(self, wg, new_area_id):
        from app.world.intent_executor import update_party_hosts_edges
        update_party_hosts_edges(wg, new_area_id, self.session.party)

    # =========================================================================
    # Party tools (delegate to FlashCPU)
    # =========================================================================

    async def add_teammate(
        self,
        character_id: str,
        name: str,
        role: str = "support",
        personality: str = "",
        response_tendency: float = 0.65,
    ) -> Dict[str, Any]:
        """Add a teammate to the party.

        Args:
            character_id: Character id from `world_context.character_roster`.
            name: Character display name.
            role: warrior / mage / healer / support / scout.
            personality: Brief personality (under 20 chars).
            response_tendency: 0.0-1.0 (silent 0.4, default 0.65, talkative 0.8).

        Usage:
            - Use only when narrative establishes joining intent.
        """
        from app.models.admin_protocol import FlashOperation, FlashRequest
        args = {
            "character_id": character_id,
            "name": name,
            "role": role,
            "personality": personality,
            "response_tendency": float(response_tendency),
        }
        request = FlashRequest(operation=FlashOperation.ADD_TEAMMATE, parameters=args)
        return await self._execute_flash_request("add_teammate", args, request)

    async def remove_teammate(self, character_id: str, reason: str = "") -> Dict[str, Any]:
        """Remove teammate from party.

        Args:
            character_id: Character to remove.
            reason: Narrative reason.

        Usage:
            - Use when departure is narratively confirmed.
        """
        from app.models.admin_protocol import FlashOperation, FlashRequest
        args = {"character_id": character_id, "reason": reason}
        request = FlashRequest(operation=FlashOperation.REMOVE_TEAMMATE, parameters=args)
        return await self._execute_flash_request("remove_teammate", args, request)

    async def disband_party(self, reason: str = "") -> Dict[str, Any]:
        """Disband the current party.

        Args:
            reason: Narrative reason.

        Usage:
            - Use only for explicit full-party split scenarios.
        """
        from app.models.admin_protocol import FlashOperation, FlashRequest
        args = {"reason": reason}
        request = FlashRequest(operation=FlashOperation.DISBAND_PARTY, parameters=args)
        return await self._execute_flash_request("disband_party", args, request)

    # =========================================================================
    # Ability check
    # =========================================================================

    async def ability_check(self, ability: str = "", skill: str = "", dc: int = 10) -> Dict[str, Any]:
        """Perform an ability/skill check (d20 roll).

        Args:
            ability: Optional ability (str/dex/con/int/wis/cha).
            skill: Skill name (e.g. stealth, persuasion, athletics).
            dc: Difficulty class (easy 8-10, normal 12-15, hard 16-20).

        Usage:
            - Use when outcome uncertainty matters and should affect narrative.
        """
        from app.models.admin_protocol import FlashOperation, FlashRequest
        args = {"ability": ability, "skill": skill, "dc": int(dc)}
        request = FlashRequest(operation=FlashOperation.ABILITY_CHECK, parameters=args)
        return await self._execute_flash_request("ability_check", args, request)

    # =========================================================================
    # Event & chapter tools (NEW in V4)
    # =========================================================================

    async def activate_event(self, event_id: str) -> Dict[str, Any]:
        """Activate an available event (available -> active).

        Args:
            event_id: Event id from `area_context.events` with status "available".

        Usage:
            - When you narratively introduce an available event, call this to mark it active.
            - Only works on events with status "available".
        """
        started = time.perf_counter()
        args = {"event_id": event_id}

        wg = getattr(self.session, "world_graph", None)
        engine = getattr(self.session, "_behavior_engine", None)
        if not wg or getattr(self.session, "_world_graph_failed", False):
            payload = {"success": False, "error": "WorldGraph not available"}
            await self._record("activate_event", args, started, payload, False, payload["error"])
            return payload

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            # 收集可用事件供 LLM 参考
            available = [
                eid for eid in wg.find_events_in_scope(self.session.player_location or "")
                if (n := wg.get_node(eid)) and n.state.get("status") == EventStatus.AVAILABLE
            ]
            payload = {
                "success": False,
                "error": f"event not found: {event_id}",
                "available_events": available,
            }
            await self._record("activate_event", args, started, payload, False, payload["error"])
            return payload

        current_status = node.state.get("status", EventStatus.LOCKED)

        # 如果事件仍是 locked，补偿同轮时序：同一 agentic turn 内
        # 前置工具调用可能已满足 trigger_conditions，就地刷新一次。
        if current_status == EventStatus.LOCKED and engine:
            ctx = self.session.build_tick_context("post")
            if ctx:
                try:
                    engine.tick(ctx)
                except Exception:
                    pass
            # 重新读取状态
            node = wg.get_node(event_id)
            current_status = node.state.get("status", EventStatus.LOCKED) if node else EventStatus.LOCKED

        if current_status == EventStatus.LOCKED:
            from app.runtime.area_runtime import AreaRuntime
            hint = AreaRuntime._summarize_completion_conditions(
                node.properties.get("trigger_conditions") or node.properties.get("completion_conditions"),
            )
            payload = {
                "success": False,
                "event_id": event_id,
                "current_status": EventStatus.LOCKED,
                "message": f"事件 '{node.name}' 尚未解锁",
                "unmet_conditions": hint or "未知条件",
                "available_events": [
                    eid for eid in wg.find_events_in_scope(self.session.player_location or "")
                    if (n := wg.get_node(eid)) and n.state.get("status") == EventStatus.AVAILABLE
                ],
            }
            await self._record("activate_event", args, started, payload, False, payload["message"])
            return payload

        if current_status != EventStatus.AVAILABLE:
            payload = {
                "success": False,
                "event_id": event_id,
                "current_status": current_status,
                "message": f"事件 '{node.name}' 当前状态为 '{current_status}'，需要 'available'",
            }
            await self._record("activate_event", args, started, payload, False, payload["message"])
            return payload

        # 激活事件（E4 P0: 始终写入 activated_at_round，供超时/冷却计算使用）
        current_round = getattr(self.session.narrative, "rounds_in_chapter", 0) if self.session.narrative else 0
        wg.merge_state(event_id, {"status": EventStatus.ACTIVE, "activated_at_round": current_round})

        # U4: 如果事件有 stages，初始化 current_stage
        stages = node.properties.get("stages", [])
        if stages:
            first_stage_id = stages[0]["id"] if isinstance(stages[0], dict) else stages[0].id
            wg.merge_state(event_id, {
                "current_stage": first_stage_id,
            })

        # 传播事件
        if engine:
            try:
                from app.world.models import WorldEvent
                ctx = self.session.build_tick_context("post")
                if ctx:
                    evt = WorldEvent(
                        event_type="event_activated",
                        origin_node=event_id,
                        actor="player",   # U23: 玩家手动激活
                        game_day=ctx.game_day,
                        game_hour=ctx.game_hour,
                        data={"event_id": event_id},
                        visibility="scope",
                    )
                    engine.handle_event(evt, ctx)
            except Exception as exc:
                logger.warning("[v4_tool] 事件传播失败 '%s': %s", event_id, exc)

        if self.area:
            self.area.record_action(f"activated_event:{event_id}")

        payload = {
            "success": True,
            "event_id": event_id,
            "event_name": node.name,
            "new_status": EventStatus.ACTIVE,
            "narrative_directive": node.properties.get("narrative_directive", ""),
        }
        await self._record("activate_event", args, started, payload, True, None)
        return payload

    async def complete_event(self, event_id: str, outcome_key: str = "") -> Dict[str, Any]:
        """Complete an active event, triggering on_complete side effects.

        Args:
            event_id: Event id from `area_context.events` with status "active".
            outcome_key: Optional outcome key (e.g. "victory", "mercy"). If the event
                has defined outcomes in `available_outcomes`, pass the desired key.
                Leave empty for events without outcomes.

        Usage:
            - When the event's objective is fulfilled, call this to complete it.
            - Side effects (unlock_events, add_items, add_xp) are applied automatically.
            - For events with outcomes, pass outcome_key to select a specific ending.
        """
        started = time.perf_counter()
        args = {"event_id": event_id}
        if outcome_key:
            args["outcome_key"] = outcome_key

        wg = getattr(self.session, "world_graph", None)
        engine = getattr(self.session, "_behavior_engine", None)
        if not wg or getattr(self.session, "_world_graph_failed", False):
            payload = {"success": False, "error": "WorldGraph not available"}
            await self._record("complete_event", args, started, payload, False, payload["error"])
            return payload

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            active_events = [
                eid for eid in wg.find_events_in_scope(self.session.player_location or "")
                if (n := wg.get_node(eid)) and n.state.get("status") == EventStatus.ACTIVE
            ]
            payload = {
                "success": False,
                "error": f"event not found: {event_id}",
                "active_events": active_events,
            }
            await self._record("complete_event", args, started, payload, False, payload["error"])
            return payload

        current_status = node.state.get("status", EventStatus.LOCKED)
        if current_status != EventStatus.ACTIVE:
            payload = {
                "success": False,
                "error": f"event '{event_id}' status is '{current_status}', expected 'active'",
            }
            await self._record("complete_event", args, started, payload, False, payload["error"])
            return payload

        # 标记完成
        wg.merge_state(event_id, {"status": EventStatus.COMPLETED})

        # U5: outcome 处理
        outcome_applied = False
        if outcome_key:
            outcomes = node.properties.get("outcomes", {})
            outcome = outcomes.get(outcome_key)
            if not outcome:
                # 回滚
                wg.merge_state(event_id, {"status": EventStatus.ACTIVE})
                payload = {
                    "success": False,
                    "error": f"Unknown outcome: {outcome_key}",
                    "available_outcomes": list(outcomes.keys()),
                }
                await self._record("complete_event", args, started, payload, False, payload["error"])
                return payload

            # 验证 outcome 条件
            outcome_conditions = outcome.get("conditions") if isinstance(outcome, dict) else getattr(outcome, "conditions", None)
            if outcome_conditions:
                from app.models.narrative import ConditionGroup as CG
                ctx = self.session.build_tick_context("post")
                if ctx:
                    from app.world.behavior_engine import ConditionEvaluator
                    eval_result = ConditionEvaluator().evaluate(
                        CG(**outcome_conditions) if isinstance(outcome_conditions, dict) else outcome_conditions,
                        ctx,
                    )
                    if not eval_result.satisfied:
                        wg.merge_state(event_id, {"status": EventStatus.ACTIVE})
                        payload = {
                            "success": False,
                            "error": f"Outcome conditions not met: {outcome_key}",
                        }
                        await self._record("complete_event", args, started, payload, False, payload["error"])
                        return payload

            # 设置 outcome
            wg.merge_state(event_id, {"outcome": outcome_key})
            # 应用 outcome 特定奖励
            outcome_dict = outcome if isinstance(outcome, dict) else outcome.model_dump()
            self._apply_outcome_rewards(outcome_dict, event_id, node)
            outcome_applied = True
        else:
            # 现有逻辑: 应用 on_complete 通用奖励
            on_complete = node.properties.get("on_complete")
            self._apply_on_complete_from_graph(on_complete, event_id, node)

        # Track in narrative progress
        if self.session.narrative:
            triggered = self.session.narrative.events_triggered
            if event_id not in triggered:
                triggered.append(event_id)
                self.session.mark_narrative_dirty()

        # 级联解锁：先 handle_event 传播，再 tick 刷新 ON_TICK 解锁
        newly_available: List[str] = []
        if engine:
            try:
                from app.world.models import WorldEvent
                ctx = self.session.build_tick_context("post")
                if ctx:
                    # handle_event 传播 ON_EVENT behaviors
                    evt = WorldEvent(
                        event_type="event_completed",
                        origin_node=event_id,
                        actor="player",   # U23: 工具调用均为玩家触发
                        game_day=ctx.game_day,
                        game_hour=ctx.game_hour,
                        data={
                            "event_id": event_id,
                            "outcome": outcome_key or None,
                            "source": "manual" if outcome_key else "tool",  # 区分手动选择结局 vs 工具直接完成
                        },
                        visibility="scope",
                    )
                    cascade_result = engine.handle_event(evt, ctx)
                    # 只同步 narrative（不 apply 副作用，工具已手动处理）
                    self.session._sync_tick_to_narrative(cascade_result)

                    # tick 刷新 ON_TICK 条件 → 解锁依赖事件
                    tick_result = engine.tick(ctx)
                    self.session._sync_tick_to_narrative(tick_result)
                    # 收集新解锁事件
                    for nid, changes in tick_result.state_changes.items():
                        if changes.get("status") in ("available", "active"):
                            newly_available.append(nid)
                    for nid, changes in cascade_result.state_changes.items():
                        if changes.get("status") in ("available", "active") and nid not in newly_available:
                            newly_available.append(nid)
            except Exception as exc:
                logger.warning("[v4_tool] 级联解锁失败 '%s': %s", event_id, exc)

        # 分发到同伴（工具直接处理，不依赖 tick 副作用路径）
        self._dispatch_event_to_companions_from_graph(event_id, node)

        if self.area:
            self.area.record_action(f"completed_event:{event_id}")

        payload: Dict[str, Any] = {
            "success": True,
            "event_id": event_id,
            "event_name": node.name,
            "new_status": "completed",
            "newly_available_events": newly_available,
        }
        if outcome_key:
            payload["outcome"] = outcome_key
            payload["outcome_applied"] = outcome_applied
        else:
            payload["on_complete_applied"] = bool(node.properties.get("on_complete"))
        await self._record("complete_event", args, started, payload, True, None)
        return payload

    def _apply_on_complete_from_graph(
        self, on_complete: Optional[Dict[str, Any]], event_id: str, node: Any,
    ) -> None:
        """从 WorldGraph 节点的 on_complete 属性应用副作用。

        同时在 session._applied_side_effect_events 中标记，防止后续 tick 重复发放。
        """
        if not on_complete:
            return

        # add_xp
        add_xp = on_complete.get("add_xp", 0)
        if add_xp and self.session.player and hasattr(self.session.player, "xp"):
            self.session.player.xp = (self.session.player.xp or 0) + add_xp
            self.session.mark_player_dirty()
            self.session._applied_side_effect_events.add(f"xp_awarded:{event_id}")
            logger.info("[v4_tool] 副作用: +%d XP (event=%s)", add_xp, event_id)

        # add_items
        add_items = on_complete.get("add_items", [])
        if add_items and self.session.player:
            inventory = getattr(self.session.player, "inventory", None)
            if inventory is not None and hasattr(inventory, "append"):
                for item in add_items:
                    inventory.append(item)
                    logger.info("[v4_tool] 副作用: +物品 %s (event=%s)", item, event_id)
                self.session.mark_player_dirty()
                self.session._applied_side_effect_events.add(f"item_granted:{event_id}")

        # add_gold
        add_gold = on_complete.get("add_gold", 0)
        if add_gold and self.session.player and hasattr(self.session.player, "gold"):
            self.session.player.gold = (self.session.player.gold or 0) + add_gold
            self.session.mark_player_dirty()
            self.session._applied_side_effect_events.add(f"gold_awarded:{event_id}")
            logger.info("[v4_tool] 副作用: +%d 金币 (event=%s)", add_gold, event_id)

        # reputation_changes
        rep_changes = on_complete.get("reputation_changes") or {}
        if rep_changes:
            wg = getattr(self.session, "world_graph", None)
            if wg and wg.has_node("world_root"):
                root = wg.get_node("world_root")
                reps = dict(root.state.get("faction_reputations", {})) if root else {}
                for faction, delta in rep_changes.items():
                    reps[faction] = reps.get(faction, 0) + delta
                    logger.info("[v4_tool] 副作用: 声望 %s %+d (event=%s)", faction, delta, event_id)
                wg.merge_state("world_root", {"faction_reputations": reps})
                self.session._applied_side_effect_events.add(f"reputation_changed:{event_id}")

        # world_flags
        wf_changes = on_complete.get("world_flags") or {}
        if wf_changes:
            wg = getattr(self.session, "world_graph", None)
            if wg and wg.has_node("world_root"):
                root = wg.get_node("world_root")
                flags = dict(root.state.get("world_flags", {})) if root else {}
                for key, value in wf_changes.items():
                    flags[key] = value
                    logger.info("[v4_tool] 副作用: 世界标记 %s = %s (event=%s)", key, value, event_id)
                wg.merge_state("world_root", {"world_flags": flags})
                self.session._applied_side_effect_events.add(f"world_flag_set:{event_id}")

        # unlock_events — 由 BehaviorEngine cascade 自动处理，无需手动

    def _apply_outcome_rewards(
        self, outcome: Dict[str, Any], event_id: str, node: Any,
    ) -> None:
        """应用 EventOutcome 的特定奖励，与 _apply_on_complete_from_graph 类似。"""
        rewards = outcome.get("rewards", {})
        if not rewards and not outcome.get("reputation_changes") and not outcome.get("world_flags"):
            return

        add_xp = rewards.get("xp", 0)
        if add_xp and self.session.player and hasattr(self.session.player, "xp"):
            self.session.player.xp = (self.session.player.xp or 0) + add_xp
            self.session.mark_player_dirty()
            self.session._applied_side_effect_events.add(f"xp_awarded:{event_id}")
            logger.info("[v4_tool] outcome 奖励: +%d XP (event=%s)", add_xp, event_id)

        add_gold = rewards.get("gold", 0)
        if add_gold and self.session.player and hasattr(self.session.player, "gold"):
            self.session.player.gold = (self.session.player.gold or 0) + add_gold
            self.session.mark_player_dirty()
            self.session._applied_side_effect_events.add(f"gold_awarded:{event_id}")
            logger.info("[v4_tool] outcome 奖励: +%d 金币 (event=%s)", add_gold, event_id)

        for item in (rewards.get("items") or []):
            if self.session.player:
                inventory = getattr(self.session.player, "inventory", None)
                if inventory is not None and hasattr(inventory, "append"):
                    inventory.append({"item_id": item} if isinstance(item, str) else item)
                    self.session.mark_player_dirty()
                    self.session._applied_side_effect_events.add(f"item_granted:{event_id}")

        rep_changes = outcome.get("reputation_changes") or {}
        if rep_changes:
            wg = getattr(self.session, "world_graph", None)
            if wg and wg.has_node("world_root"):
                root = wg.get_node("world_root")
                reps = dict(root.state.get("faction_reputations", {})) if root else {}
                for faction, delta in rep_changes.items():
                    reps[faction] = reps.get(faction, 0) + delta
                wg.merge_state("world_root", {"faction_reputations": reps})
                self.session._applied_side_effect_events.add(f"reputation_changed:{event_id}")

        wf_changes = outcome.get("world_flags") or {}
        if wf_changes:
            wg = getattr(self.session, "world_graph", None)
            if wg and wg.has_node("world_root"):
                root = wg.get_node("world_root")
                flags = dict(root.state.get("world_flags", {})) if root else {}
                for key, value in wf_changes.items():
                    flags[key] = value
                wg.merge_state("world_root", {"world_flags": flags})
                self.session._applied_side_effect_events.add(f"world_flag_set:{event_id}")

        # unlock_events — 显式处理（outcome 已设置，自动行为不会再补发）
        unlock_events = outcome.get("unlock_events") or []
        if unlock_events:
            wg = getattr(self.session, "world_graph", None)
            if wg:
                for unlock_eid in unlock_events:
                    unlock_node = wg.get_node(unlock_eid)
                    if unlock_node and unlock_node.state.get("status") == EventStatus.LOCKED:
                        wg.merge_state(unlock_eid, {"status": EventStatus.AVAILABLE})
                        self.session._applied_side_effect_events.add(f"event_unlocked:{unlock_eid}")

    def _dispatch_event_to_companions_from_graph(
        self, event_id: str, node: Any,
    ) -> None:
        """将完成的事件分发到同伴（从 WorldGraph 节点）。

        同时在 session._applied_side_effect_events 中标记，防止后续 tick 重复分发。
        """
        companions = getattr(self.session, "companions", None)
        if not companions:
            return
        from app.runtime.models.companion_state import CompactEvent
        game_day = self.session.time.day if self.session.time else 1
        area_id = self.session.player_location or ""
        compact = CompactEvent(
            event_id=event_id,
            event_name=node.name,
            summary=node.properties.get("description", node.name),
            area_id=area_id,
            game_day=game_day,
            importance=node.properties.get("importance", "side"),
        )
        for companion in companions.values():
            if hasattr(companion, "add_event"):
                companion.add_event(compact)
        self.session._applied_side_effect_events.add(f"companion_dispatch:{event_id}")

    async def advance_chapter(
        self, target_chapter_id: str, transition_type: str = "normal"
    ) -> Dict[str, Any]:
        """Advance to a new chapter.

        Args:
            target_chapter_id: Target chapter id from `chapter_context.chapter_transition_available`.
            transition_type: Transition type — normal / branch / failure / skip.

        Usage:
            - When chapter transition conditions are met and player is ready.
            - Present the choice to the player first before calling this.
        """
        started = time.perf_counter()
        args = {"target_chapter_id": target_chapter_id, "transition_type": transition_type}

        narrative = self.session.narrative
        if not narrative:
            payload = {"success": False, "error": "narrative not loaded"}
            await self._record("advance_chapter", args, started, payload, False, payload["error"])
            return payload

        old_chapter = getattr(narrative, "current_chapter", None)

        # Validate target exists in world
        world = self.session.world
        if world and hasattr(world, "chapter_registry"):
            if target_chapter_id not in world.chapter_registry:
                payload = {
                    "success": False,
                    "error": f"unknown chapter: {target_chapter_id}",
                    "available_chapters": list(world.chapter_registry.keys()),
                }
                await self._record("advance_chapter", args, started, payload, False, payload["error"])
                return payload

        # Record old chapter completion
        if old_chapter and old_chapter != target_chapter_id:
            if old_chapter not in narrative.chapters_completed:
                narrative.chapters_completed.append(old_chapter)

        # Switch chapter + reset counters
        narrative.current_chapter = target_chapter_id
        narrative.events_triggered = []
        narrative.chapter_started_at = datetime.now()
        narrative.rounds_in_chapter = 0
        narrative.rounds_since_last_progress = 0

        # Branch history tracking
        if old_chapter and old_chapter != target_chapter_id:
            narrative.branch_history.append({
                "from": old_chapter,
                "to": target_chapter_id,
                "type": transition_type or "normal",
                "at": datetime.now().isoformat(),
            })

        # Clean up active_chapters
        if narrative.active_chapters:
            narrative.active_chapters = [
                cid for cid in narrative.active_chapters
                if cid and cid != old_chapter
            ]

        self.session.mark_narrative_dirty()

        # Sync game_state
        if self.session.game_state:
            self.session.game_state.chapter_id = target_chapter_id
            self.session.mark_game_state_dirty()

        # Collect new maps unlocked by this chapter
        new_maps: list = []
        if world and target_chapter_id in world.chapter_registry:
            chapter_data = world.chapter_registry[target_chapter_id]
            if isinstance(chapter_data, dict):
                new_maps = chapter_data.get("available_maps", [])
            elif hasattr(chapter_data, "available_maps"):
                new_maps = chapter_data.available_maps or []

        payload = {
            "success": True,
            "previous_chapter": old_chapter,
            "new_chapter": target_chapter_id,
            "transition_type": transition_type,
            "new_maps_unlocked": new_maps,
        }
        await self._record("advance_chapter", args, started, payload, True, None)
        return payload

    async def complete_objective(self, objective_id: str) -> Dict[str, Any]:
        """Mark a chapter objective as completed.

        Args:
            objective_id: Objective id from `chapter_context.objectives`.

        Usage:
            - When the conditions described by a chapter objective are fulfilled.
            - Only works on objectives not yet completed.
        """
        started = time.perf_counter()
        args = {"objective_id": objective_id}

        narrative = self.session.narrative
        if not narrative:
            payload = {"success": False, "error": "narrative not loaded"}
            await self._record("complete_objective", args, started, payload, False, payload["error"])
            return payload

        # Get chapter data for validation and description
        chapter_id = getattr(narrative, "current_chapter", None)
        world = self.session.world
        chapter_data = None
        if world and hasattr(world, "chapter_registry") and chapter_id:
            chapter_data = world.chapter_registry.get(chapter_id)

        # Find the objective in the chapter definition
        obj_description = ""
        if chapter_data:
            objectives = chapter_data.get("objectives", []) if isinstance(chapter_data, dict) else getattr(chapter_data, "objectives", [])
            for obj in objectives:
                obj_id = obj.get("id", "") if isinstance(obj, dict) else getattr(obj, "id", "")
                if obj_id == objective_id:
                    obj_description = obj.get("description", "") if isinstance(obj, dict) else getattr(obj, "description", "")
                    break
            else:
                payload = {
                    "success": False,
                    "error": f"objective not found: {objective_id}",
                    "available_objectives": [
                        (obj.get("id", "") if isinstance(obj, dict) else getattr(obj, "id", ""))
                        for obj in objectives
                    ],
                }
                await self._record("complete_objective", args, started, payload, False, payload["error"])
                return payload

        # Check if already completed
        completed = getattr(narrative, "objectives_completed", []) or []
        if objective_id in completed:
            payload = {"success": False, "error": f"objective already completed: {objective_id}"}
            await self._record("complete_objective", args, started, payload, False, payload["error"])
            return payload

        # Mark completed
        narrative.objectives_completed.append(objective_id)
        self.session.mark_narrative_dirty()

        payload = {
            "success": True,
            "objective_id": objective_id,
            "description": obj_description,
            "total_completed": len(narrative.objectives_completed),
        }
        await self._record("complete_objective", args, started, payload, True, None)
        return payload

    # =========================================================================
    # Event stage & objective tools (U6 + U11)
    # =========================================================================

    async def advance_stage(self, event_id: str, stage_id: str = "") -> Dict[str, Any]:
        """Manually advance an event to the next stage or a specific stage.

        Args:
            event_id: Active event id.
            stage_id: Target stage id. Empty string = advance to next sequential stage.

        Usage:
            - For stages without completion_conditions (narrative-driven progression).
            - Validates required objectives are completed before advancing.
        """
        started = time.perf_counter()
        args = {"event_id": event_id, "stage_id": stage_id}

        wg = getattr(self.session, "world_graph", None)
        engine = getattr(self.session, "_behavior_engine", None)
        if not wg or getattr(self.session, "_world_graph_failed", False):
            payload = {"success": False, "error": "WorldGraph not available"}
            await self._record("advance_stage", args, started, payload, False, payload["error"])
            return payload

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            payload = {"success": False, "error": f"event not found: {event_id}"}
            await self._record("advance_stage", args, started, payload, False, payload["error"])
            return payload

        if node.state.get("status") != EventStatus.ACTIVE:
            payload = {"success": False, "error": f"event '{event_id}' is not active"}
            await self._record("advance_stage", args, started, payload, False, payload["error"])
            return payload

        stages_raw = node.properties.get("stages", [])
        if not stages_raw:
            payload = {"success": False, "error": f"event '{event_id}' has no stages"}
            await self._record("advance_stage", args, started, payload, False, payload["error"])
            return payload

        current_stage_id = node.state.get("current_stage")

        # 找当前 stage 索引
        current_idx = -1
        for i, s in enumerate(stages_raw):
            sid = s["id"] if isinstance(s, dict) else s.id
            if sid == current_stage_id:
                current_idx = i
                break

        if current_idx < 0:
            payload = {"success": False, "error": f"current_stage '{current_stage_id}' not found in stages"}
            await self._record("advance_stage", args, started, payload, False, payload["error"])
            return payload

        # 校验当前 stage 的 required objectives 全部完成
        current_stage = stages_raw[current_idx]
        cs = current_stage if isinstance(current_stage, dict) else current_stage.model_dump()
        obj_progress = node.state.get("objective_progress", {})
        for obj in cs.get("objectives", []):
            if obj.get("required", True) and not obj_progress.get(obj["id"], False):
                payload = {
                    "success": False,
                    "error": f"Required objective '{obj['id']}' not completed",
                    "incomplete_objectives": [
                        o["id"] for o in cs.get("objectives", [])
                        if o.get("required", True) and not obj_progress.get(o["id"], False)
                    ],
                }
                await self._record("advance_stage", args, started, payload, False, payload["error"])
                return payload

        # 确定目标 stage
        if stage_id:
            target_idx = -1
            for i, s in enumerate(stages_raw):
                sid = s["id"] if isinstance(s, dict) else s.id
                if sid == stage_id:
                    target_idx = i
                    break
            if target_idx < 0:
                payload = {"success": False, "error": f"target stage '{stage_id}' not found"}
                await self._record("advance_stage", args, started, payload, False, payload["error"])
                return payload
        else:
            # 下一个 stage
            target_idx = current_idx + 1

        is_last = target_idx >= len(stages_raw)

        if is_last:
            # 最后一个 stage → 自动调 complete_event
            result = await self.complete_event(event_id)
            result["advanced_from_stage"] = current_stage_id
            result["auto_completed"] = True
            return result

        target_stage = stages_raw[target_idx]
        target_stage_id = target_stage["id"] if isinstance(target_stage, dict) else target_stage.id
        target_stage_name = target_stage.get("name", "") if isinstance(target_stage, dict) else getattr(target_stage, "name", "")

        # 更新 current_stage + stage_progress
        progress = dict(node.state.get("stage_progress", {}))
        progress[current_stage_id] = True
        wg.merge_state(event_id, {
            "current_stage": target_stage_id,
            "stage_progress": progress,
        })

        # 补偿 tick: 推进后可能满足新阶段的自动条件
        if engine:
            try:
                ctx = self.session.build_tick_context("post")
                if ctx:
                    tick_result = engine.tick(ctx)
                    self.session._sync_tick_to_narrative(tick_result)
            except Exception as exc:
                logger.warning("[v4_tool] advance_stage 补偿 tick 失败: %s", exc)

        ts = target_stage if isinstance(target_stage, dict) else target_stage.model_dump()
        payload = {
            "success": True,
            "event_id": event_id,
            "previous_stage": current_stage_id,
            "new_stage": target_stage_id,
            "stage_name": target_stage_name,
            "narrative_directive": ts.get("narrative_directive", ""),
        }
        await self._record("advance_stage", args, started, payload, True, None)
        return payload

    async def complete_event_objective(self, event_id: str, objective_id: str) -> Dict[str, Any]:
        """Mark an event objective as completed.

        Args:
            event_id: The active event containing this objective.
            objective_id: The objective id within the event's current stage.

        Usage:
            - When a player fulfills a specific objective within an event stage.
            - Different from complete_objective which is for chapter-level objectives.
        """
        started = time.perf_counter()
        args = {"event_id": event_id, "objective_id": objective_id}

        wg = getattr(self.session, "world_graph", None)
        engine = getattr(self.session, "_behavior_engine", None)
        if not wg or getattr(self.session, "_world_graph_failed", False):
            payload = {"success": False, "error": "WorldGraph not available"}
            await self._record("complete_event_objective", args, started, payload, False, payload["error"])
            return payload

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            payload = {"success": False, "error": f"event not found: {event_id}"}
            await self._record("complete_event_objective", args, started, payload, False, payload["error"])
            return payload

        if node.state.get("status") != EventStatus.ACTIVE:
            payload = {"success": False, "error": f"event '{event_id}' is not active"}
            await self._record("complete_event_objective", args, started, payload, False, payload["error"])
            return payload

        # 验证 objective 存在于当前 stage
        stages_raw = node.properties.get("stages", [])
        current_stage_id = node.state.get("current_stage")
        objective_found = False

        if stages_raw and current_stage_id:
            for s in stages_raw:
                sid = s["id"] if isinstance(s, dict) else s.id
                if sid == current_stage_id:
                    objectives = s.get("objectives", []) if isinstance(s, dict) else getattr(s, "objectives", [])
                    for obj in objectives:
                        oid = obj["id"] if isinstance(obj, dict) else obj.id
                        if oid == objective_id:
                            objective_found = True
                            break
                    break

        if not objective_found:
            payload = {
                "success": False,
                "error": f"objective '{objective_id}' not found in current stage '{current_stage_id}'",
            }
            await self._record("complete_event_objective", args, started, payload, False, payload["error"])
            return payload

        # 检查是否已完成
        obj_progress = dict(node.state.get("objective_progress", {}))
        if obj_progress.get(objective_id, False):
            payload = {"success": False, "error": f"objective '{objective_id}' already completed"}
            await self._record("complete_event_objective", args, started, payload, False, payload["error"])
            return payload

        # 写入 objective_progress
        obj_progress[objective_id] = True
        wg.merge_state(event_id, {"objective_progress": obj_progress})

        # 计算剩余 objectives
        remaining = []
        if stages_raw and current_stage_id:
            for s in stages_raw:
                sid = s["id"] if isinstance(s, dict) else s.id
                if sid == current_stage_id:
                    objectives = s.get("objectives", []) if isinstance(s, dict) else getattr(s, "objectives", [])
                    for obj in objectives:
                        oid = obj["id"] if isinstance(obj, dict) else obj.id
                        if not obj_progress.get(oid, False):
                            remaining.append(oid)
                    break

        # 补偿 tick: objective 完成可能满足 stage.completion_conditions
        # 注: stage 条件通常用标准条件类型 (LOCATION, NPC_INTERACTED 等)，
        #     不直接检查 objective_progress。但补偿 tick 仍然有用——
        #     objective 完成后其他条件可能恰好满足。
        if engine:
            try:
                ctx = self.session.build_tick_context("post")
                if ctx:
                    tick_result = engine.tick(ctx)
                    self.session._sync_tick_to_narrative(tick_result)
            except Exception as exc:
                logger.warning("[v4_tool] complete_event_objective 补偿 tick 失败: %s", exc)

        payload = {
            "success": True,
            "event_id": event_id,
            "objective_id": objective_id,
            "remaining_objectives": remaining,
        }
        await self._record("complete_event_objective", args, started, payload, True, None)
        return payload

    # =========================================================================
    # E4: fail_event 工具 (U10)
    # =========================================================================

    async def fail_event(self, event_id: str, reason: str = "") -> Dict[str, Any]:
        """标记事件失败（ACTIVE → FAILED）。

        主路径靠 timeout behavior 自动触发；本工具供 LLM 手动覆盖。
        对 is_repeatable 事件，失败后会通过 _sync_tick_to_narrative 进入 COOLDOWN 路径。

        Args:
            event_id: 要标记失败的事件 ID（必须处于 active 状态）。
            reason: 失败原因描述（可选，便于调试/叙事追踪）。
        """
        started = time.perf_counter()
        args = {"event_id": event_id, "reason": reason}

        wg = getattr(self.session, "world_graph", None)
        engine = getattr(self.session, "_behavior_engine", None)
        if not wg or getattr(self.session, "_world_graph_failed", False):
            payload = {"success": False, "error": "WorldGraph 不可用"}
            await self._record("fail_event", args, started, payload, False, payload["error"])
            return payload

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            payload = {"success": False, "error": f"事件节点不存在: {event_id}"}
            await self._record("fail_event", args, started, payload, False, payload["error"])
            return payload

        current_status = node.state.get("status")
        if current_status != EventStatus.ACTIVE:
            payload = {
                "success": False,
                "error": f"事件 {event_id} 不处于 ACTIVE 状态（当前: {current_status}）",
            }
            await self._record("fail_event", args, started, payload, False, payload["error"])
            return payload

        # 设置 FAILED
        wg.merge_state(event_id, {
            "status": EventStatus.FAILED,
            "failure_reason": reason or "manual_fail",
        })

        # 发射 WorldEvent + cascade（与 complete_event 对称）
        if engine:
            try:
                from app.world.models import WorldEvent
                ctx = self.session.build_tick_context("post")
                if ctx:
                    evt = WorldEvent(
                        event_type="event_failed",
                        origin_node=event_id,
                        actor="player",
                        game_day=ctx.game_day,
                        game_hour=ctx.game_hour,
                        data={"event_id": event_id, "reason": reason},
                        visibility="scope",
                    )
                    fail_result = engine.handle_event(evt, ctx)
                    # 触发 COOLDOWN 转换（is_repeatable 事件）
                    self.session._sync_tick_to_narrative(fail_result)
                    self.session._apply_tick_side_effects(fail_result)
            except Exception as exc:
                logger.warning("[v4_tool] fail_event 事件传播失败 '%s': %s", event_id, exc)

        if self.area:
            self.area.record_action(f"failed_event:{event_id}")

        payload = {
            "success": True,
            "event_id": event_id,
            "status": "failed",
            "reason": reason or "manual_fail",
        }
        await self._record("fail_event", args, started, payload, True, None)
        return payload

    # =========================================================================
    # E3: pending_flash 回报工具 (U12)
    # =========================================================================

    async def report_flash_evaluation(
        self,
        prompt: str,
        result: bool,
        reason: str = "",
    ) -> Dict[str, Any]:
        """回报语义条件（FLASH_EVALUATE）的 LLM 评估结果。

        当上下文中出现 pending_flash_evaluations 时，对每个条件判断后调用本工具。
        结果在本回合 post-tick 中被 BehaviorEngine 消费，用于触发条件满足的行为。

        Args:
            prompt: 条件提示文本（与 pending_flash_evaluations[].prompt 对应）
            result: 评估结果（True=条件满足, False=条件不满足）
            reason: 评估依据（可选，便于调试追踪）
        """
        started = time.perf_counter()
        args = {"prompt": prompt, "result": result, "reason": reason}

        if not prompt:
            payload = {"success": False, "error": "prompt 不能为空"}
            await self._record("report_flash_evaluation", args, started, payload, False, payload["error"])
            return payload

        if not hasattr(self.session, "flash_results"):
            payload = {"success": False, "error": "session 不支持 flash_results"}
            await self._record("report_flash_evaluation", args, started, payload, False, payload["error"])
            return payload

        self.session.flash_results[prompt] = bool(result)
        logger.info("[V4Tools] flash_evaluation 回报: prompt=%r result=%s reason=%s",
                    prompt[:80], result, reason)

        payload = {"success": True, "prompt": prompt, "result": result, "stored": True}
        await self._record("report_flash_evaluation", args, started, payload, True, None)
        return payload

    # =========================================================================
    # Disposition tool (NEW in V4)
    # =========================================================================

    async def update_disposition(
        self,
        npc_id: str,
        deltas: Dict[str, int],
        reason: str = "",
    ) -> Dict[str, Any]:
        """Update NPC disposition (approval/trust/fear/romance).

        Args:
            npc_id: NPC character_id.
            deltas: Dict of dimension changes, e.g. {"approval": 10, "trust": -5}.
                    Valid dimensions: approval, trust, fear, romance.
                    Single call max +/-20 per dimension. Per-turn max +/-30 total.
            reason: Brief reason for the change.

        Usage:
            - Use after meaningful interactions that shift NPC feelings.
            - Positive approval: helped, impressed, agreed with values.
            - Negative approval: offended, failed, acted against values.
            - Trust: kept/broke promises, reliable/unreliable behavior.
        """
        started = time.perf_counter()
        args = {"npc_id": npc_id, "deltas": deltas, "reason": reason}

        valid_dims = {"approval", "trust", "fear", "romance"}
        cleaned: Dict[str, int] = {}
        for dim, val in (deltas or {}).items():
            if dim not in valid_dims:
                continue
            clamped = max(-20, min(20, int(val)))
            if clamped != 0:
                cleaned[dim] = clamped

        if not cleaned:
            payload = {"success": False, "error": "no valid disposition deltas"}
            await self._record("update_disposition", args, started, payload, False, payload["error"])
            return payload

        # P2: 直接写 WorldGraph NPC 节点 state，替代旧 graph_store Firestore 路径
        wg = getattr(self.session, "world_graph", None)
        if not wg or getattr(self.session, "_world_graph_failed", False):
            payload = {"success": False, "error": "WorldGraph not available"}
            await self._record("update_disposition", args, started, payload, False, payload["error"])
            return payload

        node = wg.get_node(npc_id)
        if not node:
            payload = {"success": False, "error": f"NPC node not found: {npc_id}"}
            await self._record("update_disposition", args, started, payload, False, payload["error"])
            return payload

        # 读取当前好感度
        dispositions = node.state.get("dispositions", {})
        current = dispositions.get("player", {
            "approval": 0, "trust": 0, "fear": 0, "romance": 0, "history": [],
        })

        # 应用 deltas + clamp
        clamp_ranges = {
            "approval": (-100, 100),
            "trust": (-100, 100),
            "fear": (0, 100),
            "romance": (0, 100),
        }
        game_day = getattr(self.session.time, "day", None) if self.session.time else None
        history_entry = {"reason": reason, "day": game_day}
        for dim, delta in cleaned.items():
            lo, hi = clamp_ranges.get(dim, (-100, 100))
            old_val = current.get(dim, 0)
            current[dim] = max(lo, min(hi, old_val + delta))
            history_entry[f"delta_{dim}"] = delta

        # 追加历史（保留最近 50 条）
        history = current.get("history", [])
        if not isinstance(history, list):
            history = []
        history.append(history_entry)
        if len(history) > 50:
            history = history[-50:]
        current["history"] = history

        # 写回图节点（快照系统自动持久化）
        wg.merge_state(npc_id, {"dispositions": {"player": current}})

        # 返回不含 history 的精简视图
        result_view = {dim: current.get(dim, 0) for dim in ("approval", "trust", "fear", "romance")}
        payload = {"success": True, "npc_id": npc_id, "applied_deltas": cleaned, "current": result_view}
        await self._record("update_disposition", args, started, payload, True, None)
        return payload

    # =========================================================================
    # Image generation
    # =========================================================================

    async def generate_scene_image(self, scene_description: str, style: str = "dark_fantasy") -> Dict[str, Any]:
        """Generate a scene image.

        Args:
            scene_description: Visual-only scene description (1-3 sentences).
                Should include subject + environment + lighting/mood.
                Avoid dialogue, game mechanics, options list, and meta instructions.
            style: dark_fantasy / anime / watercolor / realistic.

        Usage:
            - Only at key moments: new key locations, boss fights, major plot twists.
            - Max 1 per 3-5 turns.
        """
        started = time.perf_counter()
        if self._image_generated_this_turn:
            payload = {"generated": False, "error": "image already generated this turn"}
            await self._record("generate_scene_image", {"scene_description": scene_description, "style": style}, started, payload, False, payload["error"])
            return payload

        style_value = style if style in {"dark_fantasy", "anime", "watercolor", "realistic"} else "dark_fantasy"
        scene_preview = " ".join(str(scene_description or "").split())
        if len(scene_preview) > 220:
            scene_preview = f"{scene_preview[:220]}..."
        logger.info("[generate_scene_image] style=%s scene=%.220s", style_value, scene_preview)
        try:
            image_data = await asyncio.wait_for(
                self.image_service.generate(scene_description=scene_description, style=style_value),
                timeout=settings.image_generation_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"generated": False, "error": "tool timeout: generate_scene_image"}
            await self._record("generate_scene_image", {"scene_description": scene_description, "style": style_value}, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("generate_scene_image raised: %s", exc, exc_info=True)
            payload = {"generated": False, "error": f"image error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record("generate_scene_image", {"scene_description": scene_description, "style": style_value}, started, payload, False, payload["error"])
            return payload

        if not image_data:
            payload = {"generated": False, "error": "image generation failed"}
            await self._record("generate_scene_image", {"scene_description": scene_description, "style": style_value}, started, payload, False, payload["error"])
            return payload

        full_payload = {"generated": True, **image_data}
        # Keep large binary payload (base64) out of AFC loop context.
        prompt_preview = str(image_data.get("prompt", scene_description) or "")
        if len(prompt_preview) > 300:
            prompt_preview = f"{prompt_preview[:300]}..."
        payload = {
            "generated": True,
            "mime_type": image_data.get("mime_type", "image/png"),
            "model": image_data.get("model"),
            "style": image_data.get("style", style_value),
            "prompt": prompt_preview,
        }
        async with self._lock:
            self.image_data = full_payload
            self._image_generated_this_turn = True
        await self._record(
            "generate_scene_image",
            {"scene_description": scene_description, "style": style_value},
            started,
            payload,
            True, None,
        )
        return payload

    # =========================================================================
    # Flash request helper (for tools that still delegate to FlashCPU)
    # =========================================================================

    async def _execute_flash_request(
        self, tool_name: str, args: Dict[str, Any], request: Any
    ) -> Dict[str, Any]:
        """Execute a FlashRequest through FlashCPU with standard error handling."""
        from app.models.admin_protocol import FlashResponse
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                self.flash_cpu.execute_request(
                    world_id=self.world_id,
                    session_id=self.session_id,
                    request=request,
                    generate_narration=False,
                ),
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": f"tool timeout: {tool_name}"}
            await self._record(tool_name, args, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("tool %s raised: %s", tool_name, exc, exc_info=True)
            payload = {"success": False, "error": f"tool error: {tool_name}: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record(tool_name, args, started, payload, False, payload["error"])
            return payload

        payload = response.result if isinstance(response.result, dict) else {"raw": response.result}
        payload["success"] = response.success
        if response.error:
            payload["error"] = response.error
        if not response.success:
            logger.warning("[v4_tool] %s failed: error=%s, args=%s", tool_name, response.error, args)
        await self._record(tool_name, args, started, payload, response.success, response.error)
        return payload
