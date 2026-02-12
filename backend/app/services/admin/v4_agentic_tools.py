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

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.session = session
        self.flash_cpu = flash_cpu
        self.graph_store = graph_store
        self.recall_orchestrator = recall_orchestrator
        self.image_service = image_service or ImageGenerationService()
        self._event_queue = event_queue

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
        """Wrap tool for AFC error recording."""
        registry = self

        @functools.wraps(tool_fn)
        async def wrapper(**kwargs):
            started = time.perf_counter()
            try:
                return await tool_fn(**kwargs)
            except TypeError as exc:
                error_msg = f"argument error: {tool_fn.__name__}: {exc}"
                logger.warning("[agentic] AFC %s", error_msg)
                payload = {"success": False, "error": error_msg}
                await registry._record(tool_fn.__name__, kwargs, started, payload, False, error_msg)
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
            self.advance_chapter,
            self.complete_objective,
            # Disposition
            self.update_disposition,
            # Image
            self.generate_scene_image,
        ]
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
            await self._event_queue.put(event)

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

        # --- Validation 2: connection check ---
        connection = None
        if self.area and self.area.definition:
            for conn in self.area.definition.connections:
                if conn.target_area_id == area_id:
                    connection = conn
                    break
            if connection is None:
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
        if connection:
            raw = self._parse_travel_time(connection.travel_time or "30 minutes")
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
        if connection:
            result["connection_type"] = connection.connection_type
        await self._record("navigate", args, started, result, result.get("success", True), result.get("error"))
        return result

    def _resolve_area_id(self, destination: str) -> str:
        """Resolve area name to area_id. Returns destination as-is if no match."""
        world = self.session.world

        # Priority 1: exact match on connection target_area_id
        if self.area and self.area.definition:
            for conn in self.area.definition.connections:
                if conn.target_area_id == destination:
                    return destination

        # Priority 2: match connection target by area name
        if self.area and self.area.definition and world and hasattr(world, "area_registry"):
            for conn in self.area.definition.connections:
                area_def = world.area_registry.get(conn.target_area_id)
                if area_def and getattr(area_def, "name", "") == destination:
                    return conn.target_area_id

        # Priority 3: global area_registry name/id match
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

        args = {"enemies": enemy_payload}
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
                await self.flash_cpu.sync_combat_result_to_character(
                    self.world_id, self.session_id, sync_data,
                )
                # Clear combat_id in session state
                if self.session.game_state:
                    self.session.game_state.combat_id = None
                    self.session.mark_game_state_dirty()
                payload["resolve"] = resolve_payload
            except Exception as exc:
                payload["resolve_error"] = str(exc)

        payload["success"] = not bool(payload.get("error"))
        await self._record("choose_combat_action", {"action_id": action_id, "actor_id": actor_id}, started, payload, payload["success"], payload.get("error"))
        return payload

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

        area_rt = self.area
        if not area_rt:
            payload = {"success": False, "error": "no current area"}
            await self._record("activate_event", args, started, payload, False, payload["error"])
            return payload

        target_event = None
        for event in area_rt.events:
            if event.id == event_id:
                target_event = event
                break

        if not target_event:
            payload = {
                "success": False,
                "error": f"event not found: {event_id}",
                "available_events": [e.id for e in area_rt.events if e.status == "available"],
            }
            await self._record("activate_event", args, started, payload, False, payload["error"])
            return payload

        if target_event.status != "available":
            payload = {
                "success": False,
                "error": f"event '{event_id}' status is '{target_event.status}', expected 'available'",
            }
            await self._record("activate_event", args, started, payload, False, payload["error"])
            return payload

        target_event.status = "active"
        area_rt.record_action(f"activated_event:{event_id}")

        payload = {
            "success": True,
            "event_id": event_id,
            "event_name": target_event.name,
            "new_status": "active",
            "narrative_directive": target_event.narrative_directive,
        }
        await self._record("activate_event", args, started, payload, True, None)
        return payload

    async def complete_event(self, event_id: str) -> Dict[str, Any]:
        """Complete an active event, triggering on_complete side effects.

        Args:
            event_id: Event id from `area_context.events` with status "active".

        Usage:
            - When the event's objective is fulfilled, call this to complete it.
            - Side effects (unlock_events, add_items, add_xp) are applied automatically.
        """
        started = time.perf_counter()
        args = {"event_id": event_id}

        area_rt = self.area
        if not area_rt:
            payload = {"success": False, "error": "no current area"}
            await self._record("complete_event", args, started, payload, False, payload["error"])
            return payload

        target_event = None
        for event in area_rt.events:
            if event.id == event_id:
                target_event = event
                break

        if not target_event:
            payload = {
                "success": False,
                "error": f"event not found: {event_id}",
                "active_events": [e.id for e in area_rt.events if e.status == "active"],
            }
            await self._record("complete_event", args, started, payload, False, payload["error"])
            return payload

        if target_event.status != "active":
            payload = {
                "success": False,
                "error": f"event '{event_id}' status is '{target_event.status}', expected 'active'",
            }
            await self._record("complete_event", args, started, payload, False, payload["error"])
            return payload

        target_event.status = "completed"
        area_rt._apply_on_complete(target_event.on_complete, self.session)
        area_rt.record_action(f"completed_event:{event_id}")

        # Track in narrative progress
        if self.session.narrative:
            triggered = getattr(self.session.narrative, "events_triggered", None)
            if triggered is not None and event_id not in triggered:
                triggered.append(event_id)
                self.session.mark_narrative_dirty()

        payload = {
            "success": True,
            "event_id": event_id,
            "event_name": target_event.name,
            "new_status": "completed",
            "on_complete_applied": bool(target_event.on_complete),
        }
        await self._record("complete_event", args, started, payload, True, None)
        return payload

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

        game_day = None
        if self.session.time:
            game_day = getattr(self.session.time, "day", None)

        try:
            result = await asyncio.wait_for(
                self.graph_store.update_disposition(
                    world_id=self.world_id,
                    character_id=npc_id,
                    target_id="player",
                    deltas=cleaned,
                    reason=reason,
                    game_day=game_day,
                ),
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": "tool timeout: update_disposition"}
            await self._record("update_disposition", args, started, payload, False, payload["error"])
            return payload
        except Exception as exc:
            logger.error("update_disposition raised: %s", exc, exc_info=True)
            payload = {"success": False, "error": f"update_disposition error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record("update_disposition", args, started, payload, False, payload["error"])
            return payload

        payload = {"success": True, "npc_id": npc_id, "applied_deltas": cleaned, "current": result}
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
