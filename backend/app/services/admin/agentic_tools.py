"""
Agentic tool registry for admin GM flow.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Dict, List, Optional

from app.models.admin_protocol import (
    AgenticToolCall,
    FlashOperation,
    FlashRequest,
    FlashResponse,
)
from app.models.flash import RecallRequest
from app.config import settings
from app.services.image_generation_service import ImageGenerationService


class AgenticToolRegistry:
    """Register callable tools and keep tool execution traces."""

    def __init__(
        self,
        flash_cpu: Any,
        world_id: str,
        session_id: str,
        pending_condition_ids: Optional[List[str]] = None,
        image_service: Optional[ImageGenerationService] = None,
    ) -> None:
        self.flash_cpu = flash_cpu
        self.world_id = world_id
        self.session_id = session_id
        self.pending_condition_ids = set(pending_condition_ids or [])
        self.image_service = image_service or ImageGenerationService()

        self.tool_calls: List[AgenticToolCall] = []
        self.flash_results: List[FlashResponse] = []
        self.story_condition_results: Dict[str, bool] = {}
        self.image_data: Optional[Dict[str, Any]] = None

        self._lock = asyncio.Lock()
        self._image_generated_this_turn = False

    @staticmethod
    def _normalize_advance_minutes(raw_minutes: int) -> int:
        """Normalize requested minutes into allowed buckets."""
        allowed = [5, 10, 15, 30, 60, 120, 180, 240, 360, 480, 720]
        minutes = max(1, min(int(raw_minutes), 720))
        return min(allowed, key=lambda value: abs(value - minutes))

    def _wrap_tool_for_afc(self, tool_fn):
        """Wrap tool for AFC error recording.

        When SDK auto-FC invokes a tool with bad arguments, the exception
        is caught by the SDK and sent as ``{'error': ...}`` to the model,
        but our registry never sees it.  This wrapper ensures we log and
        record the failure before re-raising so the SDK flow continues.
        """
        _afc_logger = logging.getLogger(__name__)
        registry = self

        @functools.wraps(tool_fn)
        async def wrapper(**kwargs):
            started = time.perf_counter()
            try:
                return await tool_fn(**kwargs)
            except TypeError as exc:
                error_msg = f"argument error: {tool_fn.__name__}: {exc}"
                _afc_logger.warning("[agentic] AFC %s", error_msg)
                payload = {"success": False, "error": error_msg}
                await registry._record(tool_fn.__name__, kwargs, started, payload, False, error_msg)
                raise  # re-raise so SDK sends {'error': ...} to model

        wrapper.__annotations__ = tool_fn.__annotations__
        return wrapper

    def get_tools(self) -> List[Any]:
        """Return all tool callables exposed to model.

        IMPORTANT: Function names here become the tool names visible to the LLM.
        They MUST match the names in agentic_enforcement.py (SIDE_EFFECT_TOOLS,
        READ_ONLY_TOOLS, REPAIR_NAME_MAP) to avoid name mismatch bugs.
        """
        raw_tools = [
            self.recall_memory,
            self.navigate,
            self.update_time,
            self.enter_sublocation,
            self.npc_dialogue,
            self.start_combat,
            self.get_combat_options,
            self.choose_combat_action,
            self.trigger_narrative_event,
            self.get_progress,
            self.get_status,
            self.add_teammate,
            self.remove_teammate,
            self.disband_party,
            self.heal_player,
            self.damage_player,
            self.add_xp,
            self.add_item,
            self.remove_item,
            self.ability_check,
            self.evaluate_story_conditions,
            self.generate_scene_image,
        ]
        return [self._wrap_tool_for_afc(t) for t in raw_tools]

    def get_tool_name_map(self) -> Dict[str, Any]:
        """Return callable map keyed by function name exposed to the model."""
        return {tool.__name__: tool for tool in self.get_tools()}

    async def execute_tool_call(self, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute one tool call by function name."""
        started = time.perf_counter()
        tool_name = str(name or "").strip()
        safe_args = dict(args) if isinstance(args, dict) else {}
        if not tool_name:
            payload = {"success": False, "error": "missing tool name"}
            await self._record(
                "tool_dispatch",
                {"name": name, "args": safe_args},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload

        tool = self.get_tool_name_map().get(tool_name)
        if tool is None:
            payload = {"success": False, "error": f"unknown tool: {tool_name}"}
            await self._record(
                tool_name,
                safe_args,
                started,
                payload,
                False,
                payload["error"],
            )
            return payload

        try:
            result = await tool(**safe_args)
        except TypeError as exc:
            payload = {"success": False, "error": f"invalid args for {tool_name}: {exc}"}
            await self._record(
                tool_name,
                safe_args,
                started,
                payload,
                False,
                payload["error"],
            )
            return payload
        except Exception as exc:
            payload = {
                "success": False,
                "error": f"tool dispatch error: {tool_name}: {type(exc).__name__}: {str(exc)[:200]}",
            }
            await self._record(
                tool_name,
                safe_args,
                started,
                payload,
                False,
                payload["error"],
            )
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

    async def _execute_flash_operation(
        self,
        operation: FlashOperation,
        parameters: Dict[str, Any],
    ) -> Dict[str, Any]:
        import logging
        _logger = logging.getLogger(__name__)

        started = time.perf_counter()
        request = FlashRequest(operation=operation, parameters=parameters)
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
            payload = {
                "success": False,
                "error": f"tool timeout: {operation.value}",
            }
            await self._record(
                name=operation.value,
                args=parameters,
                started_at=started,
                result=payload,
                success=False,
                error=payload["error"],
            )
            return payload
        except Exception as exc:
            _logger.error("tool %s raised: %s", operation.value, exc, exc_info=True)
            payload = {
                "success": False,
                "error": f"tool error: {operation.value}: {type(exc).__name__}: {str(exc)[:200]}",
            }
            await self._record(
                name=operation.value,
                args=parameters,
                started_at=started,
                result=payload,
                success=False,
                error=payload["error"],
            )
            return payload

        payload = response.result if isinstance(response.result, dict) else {"raw": response.result}
        payload["success"] = response.success
        if response.error:
            payload["error"] = response.error
        if not response.success:
            _logger.warning(
                "[agentic_tool] %s failed: error=%s, args=%s",
                operation.value, response.error, parameters,
            )
        async with self._lock:
            self.flash_results.append(response)
        await self._record(
            name=operation.value,
            args=parameters,
            started_at=started,
            result=payload,
            success=response.success,
            error=response.error,
        )
        return payload

    async def recall_memory(
        self,
        seeds: List[str],
        character_id: str = "player",
        intent_type: str = "roleplay",
    ) -> Dict[str, Any]:
        """Recall memory subgraph for given concept seeds.

        Args:
            seeds: Concept seeds, e.g. ["森林", "哥布林"].
            character_id: Character id to query memory from.
            intent_type: Recall intent profile, e.g. roleplay/navigation/combat.

        Usage:
            - Use before major decisions (navigation/combat/event trigger) when history matters.
            - Prefer 2-6 focused seeds from player input + chapter objective.
            - If this returns empty activation, switch to `get_status` / `get_progress` instead of retry spam.
        """
        started = time.perf_counter()
        norm_seeds = [str(s).strip() for s in (seeds or []) if str(s).strip()]
        if not norm_seeds:
            payload = {"success": False, "error": "missing seeds"}
            await self._record(
                "recall_memory",
                {"seeds": seeds, "character_id": character_id, "intent_type": intent_type},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload

        state_manager = getattr(self.flash_cpu, "state_manager", None)
        state = await state_manager.get_state(self.world_id, self.session_id) if state_manager else None
        chapter_id = getattr(state, "chapter_id", None) if state else None
        area_id = getattr(state, "area_id", None) if state else None
        recall_orchestrator = getattr(self.flash_cpu, "recall_orchestrator", None)

        try:
            if recall_orchestrator is not None:
                recall = await asyncio.wait_for(
                    recall_orchestrator.recall(
                        world_id=self.world_id,
                        character_id=character_id,
                        seed_nodes=norm_seeds,
                        intent_type=str(intent_type or "roleplay"),
                        chapter_id=chapter_id,
                        area_id=area_id,
                    ),
                    timeout=settings.admin_agentic_tool_timeout_seconds,
                )
            else:
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
            await self._record(
                "recall_memory",
                {
                    "seeds": norm_seeds,
                    "character_id": character_id,
                    "intent_type": intent_type,
                },
                started,
                payload,
                False,
                payload["error"],
            )
            return payload
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("recall_memory raised: %s", exc, exc_info=True)
            payload = {"success": False, "error": f"recall_memory error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record(
                "recall_memory",
                {"seeds": norm_seeds, "character_id": character_id, "intent_type": intent_type},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload
        payload = recall.model_dump() if hasattr(recall, "model_dump") else {"result": recall}
        payload["success"] = True
        await self._record(
            "recall_memory",
            {
                "seeds": norm_seeds,
                "character_id": character_id,
                "intent_type": intent_type,
            },
            started,
            payload,
            True,
            None,
        )
        return payload

    async def navigate(self, destination: str, direction: str = "") -> Dict[str, Any]:
        """Navigate to destination.

        Usage:
            - Destination should come from context `available_destinations`.
            - Use for map-level movement; use `enter_sublocation` for in-map sub locations.
        """
        return await self._execute_flash_operation(
            FlashOperation.NAVIGATE,
            {"destination": destination, "direction": direction},
        )

    async def update_time(self, minutes: int = 30) -> Dict[str, Any]:
        """Advance game time in minutes.

        Rules:
            - Disallow time advance in combat.
            - Normalize requested minutes to allowed buckets.
            - Single call max is 720 minutes.
        """
        started = time.perf_counter()
        state_manager = getattr(self.flash_cpu, "state_manager", None)
        state = await state_manager.get_state(self.world_id, self.session_id) if state_manager else None
        if state and getattr(state, "combat_id", None):
            payload = {
                "success": False,
                "error": "cannot advance time during combat",
            }
            await self._record(
                "update_time",
                {"minutes": minutes},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload

        normalized_minutes = self._normalize_advance_minutes(int(minutes))
        payload = await self._execute_flash_operation(
            FlashOperation.UPDATE_TIME,
            {"minutes": normalized_minutes},
        )
        payload["requested_minutes"] = int(minutes)
        payload["applied_minutes"] = normalized_minutes
        return payload

    async def enter_sublocation(self, sub_location: str) -> Dict[str, Any]:
        """Enter a sub-location by id or name.

        Args:
            sub_location: Sub-location id or name. MUST come from context `sub_locations` field.
                          Use the exact `id` value from context for best results.
                          If the call fails, check `available_sub_locations` in the error response
                          and retry with the correct id.

        Usage:
            - Use when player explicitly enters a building/room/poi inside current map.
            - Candidate should come from context `sub_locations`.
        """
        return await self._execute_flash_operation(
            FlashOperation.ENTER_SUBLOCATION,
            {"sub_location": sub_location},
        )

    async def npc_dialogue(self, npc_id: str, message: str) -> Dict[str, Any]:
        """Talk to an NPC.

        Usage:
            - Use when narrative needs concrete NPC response before final narration.
            - `npc_id` should be from current location NPC list or memory recall.
        """
        return await self._execute_flash_operation(
            FlashOperation.NPC_DIALOGUE,
            {"npc_id": npc_id, "message": message},
        )

    async def start_combat(self, enemies: list[dict]) -> dict:
        """Start combat with structured enemy specs.

        Usage:
            - Use only when hostile conflict is explicitly initiated.
            - Prefer triggering combat once, then narrate based on combat result.
        """
        enemy_payload: List[Dict[str, Any]] = []
        for enemy in enemies or []:
            if isinstance(enemy, str) and enemy.strip():
                enemy_payload.append(
                    {"enemy_id": enemy.strip(), "count": 1, "level": 1}
                )
                continue
            if isinstance(enemy, dict):
                enemy_id = str(
                    enemy.get("enemy_id")
                    or enemy.get("type")
                    or ""
                ).strip()
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
                enemy_payload.append(
                    {
                        "enemy_id": enemy_id,
                        "count": max(1, min(count, 20)),
                        "level": max(1, min(level, 20)),
                        "variant": enemy.get("variant"),
                        "template_version": enemy.get("template_version"),
                        "tags": list(enemy.get("tags", []) or []),
                        "overrides": dict(enemy.get("overrides", {}) or {}),
                    }
                )
        return await self._execute_flash_operation(
            FlashOperation.START_COMBAT,
            {"enemies": enemy_payload},
        )

    async def _resolve_active_combat_id(self) -> Optional[str]:
        session_store = getattr(self.flash_cpu, "session_store", None)
        if not session_store:
            return None
        state = await session_store.get_session(self.world_id, self.session_id)
        if not state:
            return None
        return getattr(state, "active_combat_id", None)

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

    async def get_combat_options(
        self,
        actor_id: str = "player",
    ) -> Dict[str, Any]:
        """Get current combat actions and state for an actor."""
        started = time.perf_counter()
        combat_id = await self._resolve_active_combat_id()
        if not combat_id:
            payload = {"success": False, "error": "no active combat"}
            await self._record(
                "get_combat_options",
                {"actor_id": actor_id},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload

        try:
            actions_raw = await self.flash_cpu.call_combat_tool(
                "get_available_actions_for_actor",
                {"combat_id": combat_id, "actor_id": actor_id},
            )
            if actions_raw.get("error"):
                actions_raw = await self.flash_cpu.call_combat_tool(
                    "get_available_actions",
                    {"combat_id": combat_id},
                )
            state_payload = await self.flash_cpu.call_combat_tool(
                "get_combat_state",
                {"combat_id": combat_id},
            )
        except Exception as exc:
            payload = {"success": False, "error": f"combat options error: {exc}"}
            await self._record(
                "get_combat_options",
                {"actor_id": actor_id},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload

        actions = actions_raw.get("actions", []) if isinstance(actions_raw, dict) else []
        payload = {
            "success": True,
            "combat_id": combat_id,
            "actor_id": actor_id,
            "actions": actions,
            "actions_v3": [self._to_v3_action(action) for action in actions],
            "combat_state": state_payload if isinstance(state_payload, dict) else {},
        }
        await self._record(
            "get_combat_options",
            {"actor_id": actor_id},
            started,
            payload,
            True,
            None,
        )
        return payload

    async def choose_combat_action(
        self,
        action_id: str,
        actor_id: str = "player",
    ) -> Dict[str, Any]:
        """Execute combat action for actor in active combat."""
        started = time.perf_counter()
        combat_id = await self._resolve_active_combat_id()
        if not combat_id:
            payload = {"success": False, "error": "no active combat"}
            await self._record(
                "choose_combat_action",
                {"action_id": action_id, "actor_id": actor_id},
                started,
                payload,
                False,
                payload["error"],
            )
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
            await self._record(
                "choose_combat_action",
                {"action_id": action_id, "actor_id": actor_id},
                started,
                error_payload,
                False,
                error_payload["error"],
            )
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
                    self.world_id,
                    self.session_id,
                    sync_data,
                )
                apply_delta = getattr(self.flash_cpu, "_apply_delta", None)
                build_delta = getattr(self.flash_cpu, "_build_state_delta", None)
                if apply_delta and build_delta:
                    await apply_delta(
                        self.world_id,
                        self.session_id,
                        build_delta("end_combat", {"combat_id": None}),
                    )
                payload["resolve"] = resolve_payload
            except Exception as exc:
                payload["resolve_error"] = str(exc)

        payload["success"] = not bool(payload.get("error"))
        await self._record(
            "choose_combat_action",
            {"action_id": action_id, "actor_id": actor_id},
            started,
            payload,
            payload["success"],
            payload.get("error"),
        )
        return payload

    async def trigger_narrative_event(self, event_id: str) -> Dict[str, Any]:
        """Trigger a narrative event by id.

        Usage:
            - If you conclude "this chapter event has happened", you must call this tool.
            - `event_id` must come from chapter/task context (`current_event`, `pending_required_events`, or conditions).
            - Do not invent unknown event ids.
        """
        return await self._execute_flash_operation(
            FlashOperation.TRIGGER_NARRATIVE_EVENT,
            {"event_id": event_id},
        )

    async def get_progress(self) -> Dict[str, Any]:
        """Get story progress.

        Usage:
            - Call when player asks task/objective/chapter progress.
            - Call before deciding whether to trigger event or chapter transition.
        """
        return await self._execute_flash_operation(
            FlashOperation.GET_PROGRESS,
            {},
        )

    async def get_status(self) -> Dict[str, Any]:
        """Get current game status.

        Usage:
            - Call when player asks current state (time/location/team/player status).
            - Also use as grounding step when context is ambiguous.
        """
        return await self._execute_flash_operation(
            FlashOperation.GET_STATUS,
            {},
        )

    async def add_teammate(
        self,
        character_id: str,
        name: str,
        role: str = "support",
        personality: str = "",
        response_tendency: float = 0.65,
    ) -> Dict[str, Any]:
        """Add a teammate into party.

        Usage:
            - Use only when narrative establishes joining intent and identity is clear.
            - Keep `response_tendency` in [0, 1].
            - Recommended: silent/warrior 0.4, default 0.65, talkative/social 0.8.
        """
        return await self._execute_flash_operation(
            FlashOperation.ADD_TEAMMATE,
            {
                "character_id": character_id,
                "name": name,
                "role": role,
                "personality": personality,
                "response_tendency": float(response_tendency),
            },
        )

    async def remove_teammate(self, character_id: str, reason: str = "") -> Dict[str, Any]:
        """Remove teammate from party.

        Usage:
            - Use when departure is narratively confirmed (conflict/quest split/death).
        """
        return await self._execute_flash_operation(
            FlashOperation.REMOVE_TEAMMATE,
            {"character_id": character_id, "reason": reason},
        )

    async def disband_party(self, reason: str = "") -> Dict[str, Any]:
        """Disband current party.

        Usage:
            - Use only for explicit full-party split scenarios.
        """
        return await self._execute_flash_operation(
            FlashOperation.DISBAND_PARTY,
            {"reason": reason},
        )

    async def heal_player(self, amount: int) -> Dict[str, Any]:
        """Heal player hp.

        Usage:
            - Use after rest/heal item/spell/effect is narratively confirmed.
        """
        return await self._execute_flash_operation(
            FlashOperation.HEAL_PLAYER,
            {"amount": int(amount)},
        )

    async def damage_player(self, amount: int) -> Dict[str, Any]:
        """Damage player hp.

        Usage:
            - Use when hazard/combat/trap consequence is explicit.
        """
        return await self._execute_flash_operation(
            FlashOperation.DAMAGE_PLAYER,
            {"amount": int(amount)},
        )

    async def add_xp(self, amount: int) -> Dict[str, Any]:
        """Add xp to player.

        Usage:
            - Use for completed encounters/objectives or milestone rewards.
        """
        return await self._execute_flash_operation(
            FlashOperation.ADD_XP,
            {"amount": int(amount)},
        )

    async def add_item(self, item_id: str, item_name: str, quantity: int = 1) -> Dict[str, Any]:
        """Add item to inventory.

        Usage:
            - Use when loot/reward/purchase is confirmed in-story.
        """
        return await self._execute_flash_operation(
            FlashOperation.ADD_ITEM,
            {"item_id": item_id, "item_name": item_name, "quantity": int(quantity)},
        )

    async def remove_item(self, item_id: str, quantity: int = 1) -> Dict[str, Any]:
        """Remove item from inventory.

        Usage:
            - Use for consumption/crafting/payment after narratively confirmed action.
        """
        return await self._execute_flash_operation(
            FlashOperation.REMOVE_ITEM,
            {"item_id": item_id, "quantity": int(quantity)},
        )

    async def ability_check(
        self,
        ability: str = "",
        skill: str = "",
        dc: int = 10,
    ) -> Dict[str, Any]:
        """Perform an ability/skill check.

        Usage:
            - Use when outcome uncertainty matters and should affect narrative branch.
            - Typical DC bands: easy 8-10, normal 12-15, hard 16-20.
        """
        return await self._execute_flash_operation(
            FlashOperation.ABILITY_CHECK,
            {"ability": ability, "skill": skill, "dc": int(dc)},
        )

    async def evaluate_story_conditions(
        self,
        condition_id: str,
        result: bool,
        reasoning: str = "",
    ) -> Dict[str, Any]:
        """Evaluate pending story condition in semantic layer.

        Usage:
            - `condition_id` must be one of current `pending_flash_conditions`.
            - Call once per pending condition after reasoning from player input/context.
            - Returned result feeds StoryDirector post-evaluation.
        """
        started = time.perf_counter()
        normalized_id = str(condition_id).strip()
        allowed = (not self.pending_condition_ids) or (normalized_id in self.pending_condition_ids)
        success = bool(allowed and normalized_id)
        payload = {
            "condition_id": normalized_id,
            "result": bool(result),
            "reasoning": reasoning,
            "accepted": success,
        }
        if success:
            async with self._lock:
                self.story_condition_results[normalized_id] = bool(result)
        else:
            payload["error"] = "condition_id not pending"
        await self._record(
            "evaluate_story_conditions",
            {"condition_id": condition_id, "result": result, "reasoning": reasoning},
            started,
            payload,
            success,
            payload.get("error"),
        )
        return payload

    async def generate_scene_image(
        self,
        scene_description: str,
        style: str = "dark_fantasy",
    ) -> Dict[str, Any]:
        """Generate a scene image and return base64."""
        started = time.perf_counter()
        if self._image_generated_this_turn:
            payload = {
                "generated": False,
                "error": "image already generated this turn",
            }
            await self._record(
                "generate_scene_image",
                {"scene_description": scene_description, "style": style},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload

        style_value = style if style in {"dark_fantasy", "anime", "watercolor", "realistic"} else "dark_fantasy"
        try:
            image_data = await asyncio.wait_for(
                self.image_service.generate(
                    scene_description=scene_description,
                    style=style_value,
                ),
                timeout=settings.image_generation_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"generated": False, "error": "tool timeout: generate_scene_image"}
            await self._record(
                "generate_scene_image",
                {"scene_description": scene_description, "style": style_value},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("generate_scene_image raised: %s", exc, exc_info=True)
            payload = {"generated": False, "error": f"image error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record(
                "generate_scene_image",
                {"scene_description": scene_description, "style": style_value},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload
        if not image_data:
            payload = {"generated": False, "error": "image generation failed"}
            await self._record(
                "generate_scene_image",
                {"scene_description": scene_description, "style": style_value},
                started,
                payload,
                False,
                payload["error"],
            )
            return payload

        payload = {"generated": True, **image_data}
        async with self._lock:
            self.image_data = payload
            self._image_generated_this_turn = True
        await self._record(
            "generate_scene_image",
            {"scene_description": scene_description, "style": style_value},
            started,
            {"generated": True, "mime_type": image_data.get("mime_type", "image/png")},
            True,
            None,
        )
        return payload
