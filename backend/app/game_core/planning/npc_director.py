"""NpcDirector sub-system — NPC directive and spawn directives.

Handles: direct_npc, spawn_quest_npc.

Decision record: D-P20b (narrative.md)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping

from app.game_core.narrative.instance_manager import InstanceManager
from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.planning.utils import coerce_non_empty_string, normalize_mapping, string_or_empty
from app.game_core.state import StateChange

if TYPE_CHECKING:
    from app.game_core.adapters.planner_system import PlannerAgentPort
    from app.game_core.orchestration.settlement import SettlementContext

logger = logging.getLogger(__name__)


class NpcDirectorSubSystem:
    """PlannerSubSystem responsible for NPC directive and spawn directives."""

    _HANDLES: frozenset[str] = frozenset({"direct_npc", "spawn_quest_npc"})

    def __init__(
        self,
        *,
        instance_manager: InstanceManager | None = None,
        agent: PlannerAgentPort | None = None,
    ) -> None:
        self._instance_manager = instance_manager
        self._agent = agent

    # ------------------------------------------------------------------
    # PlannerSubSystem protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "npc_director"

    @property
    def handles(self) -> frozenset[str]:
        return self._HANDLES

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in {
            "quest_accepted",
            "quest_created",
            "quest_updated",
            "quest_objective_completed",
            "quest_completed",
            "milestone_completed",
            "milestone_failed",
            "area_entered",
            "sub_location_entered",
            "scene_changed",
            "relationship_stage_changed",
            "world_event_active",
            "world_event_resolved",
            "combat_resolved",
        }

    async def evaluate(self, event: PlannerEvent, context: Any) -> SubSystemResult:
        if self._agent is not None:
            return await self._evaluate_with_agent(event, context)
        return SubSystemResult()

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool:
        if kind == "direct_npc":
            return self._apply_direct_npc(payload, context, current_tick=current_tick)
        if kind == "spawn_quest_npc":
            return self._apply_spawn_quest_npc(payload, context, current_tick=current_tick)
        return False

    # ------------------------------------------------------------------
    # Handler: direct_npc
    # ------------------------------------------------------------------

    def _apply_direct_npc(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool:
        npc_id = coerce_non_empty_string(payload.get("npc_id"))
        if npc_id is None:
            return False
        directive = payload.get("directive")
        if directive is not None and not isinstance(directive, Mapping):
            return False
        if not directive:
            return False
        expires_at_tick = current_tick + 24
        raw_expiry = payload.get("expires_at_tick")
        if raw_expiry is not None:
            try:
                expires_at_tick = int(raw_expiry)
            except (TypeError, ValueError):
                return False
        priority = payload.get("priority")
        if isinstance(priority, str):
            normalized_priority = priority.strip().lower() or "medium"
        else:
            normalized_priority = "medium"
        if normalized_priority not in {"high", "medium", "low"}:
            return False
        stored_directive = context.state.narrative_plan.add_directive(
            {
                "npc_id": npc_id,
                "directive": dict(directive) if isinstance(directive, Mapping) else {},
                "priority": normalized_priority,
                "issued_at_tick": current_tick,
                "expires_at_tick": expires_at_tick,
                "linked_quest_id": coerce_non_empty_string(payload.get("linked_quest_id")),
                "source": "narrative_planner",
                "consumed": False,
            }
        )
        context.record_change(StateChange(
            slice="narrative_plan",
            operation="add",
            path="npc_directives",
            value=stored_directive,
        ))
        if self._instance_manager is not None:
            self._instance_manager.inject_directive(
                npc_id,
                stored_directive,
                current_tick=current_tick,
            )
        return True

    # ------------------------------------------------------------------
    # Handler: spawn_quest_npc
    # ------------------------------------------------------------------

    def _apply_spawn_quest_npc(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool:
        npc_id = coerce_non_empty_string(payload.get("npc_id"))
        if not context.state.has_slice("areas"):
            return False
        area_id = coerce_non_empty_string(payload.get("area_id"))
        if not area_id or area_id not in context.state.areas.areas:
            return False
        auto_spawn = False
        if npc_id is None:
            npc_id = f"temp_npc_{current_tick}"
            auto_spawn = True
        if auto_spawn:
            npc_id = self._find_available_npc_id(context, npc_id, max_attempts=20)
        elif context.state.areas.find_npc_area(npc_id) is not None:
            return False
        location_id = coerce_non_empty_string(payload.get("location_id"))
        name = string_or_empty(payload.get("name")) or None
        appearance = string_or_empty(payload.get("appearance"))
        personality = string_or_empty(payload.get("personality"))
        dialogue_hook = string_or_empty(payload.get("dialogue_hook"))
        raw_tags = payload.get("tags")
        tags = [
            str(tag)
            for tag in raw_tags
            if isinstance(tag, str)
        ] if isinstance(raw_tags, list) else []
        linked_quest_id = coerce_non_empty_string(payload.get("linked_quest_id"))
        metadata = normalize_mapping(payload.get("metadata"))
        if linked_quest_id is None:
            linked_quest_id = coerce_non_empty_string(metadata.get("linked_quest_id"))
        raw_despawn_tick = payload.get("despawn_tick")
        if raw_despawn_tick is None:
            raw_despawn_tick = metadata.get("despawn_tick")
        try:
            despawn_tick = int(raw_despawn_tick) if raw_despawn_tick is not None else None
        except (TypeError, ValueError):
            despawn_tick = None
        if despawn_tick is None:
            raw_despawn_in_ticks = payload.get("despawn_in_ticks")
            if raw_despawn_in_ticks is None:
                raw_despawn_in_ticks = metadata.get("despawn_in_ticks")
            try:
                despawn_in_ticks = int(raw_despawn_in_ticks) if raw_despawn_in_ticks is not None else None
            except (TypeError, ValueError):
                despawn_in_ticks = None
            if despawn_in_ticks is None:
                despawn_in_ticks = 24
            despawn_tick = current_tick + max(0, despawn_in_ticks)
        npc_profile = {
            "npc_id": npc_id,
            "name": string_or_empty(name) or npc_id,
            "appearance": appearance,
            "personality": personality,
            "dialogue_hook": dialogue_hook,
            "description": string_or_empty(payload.get("description")),
            "role": string_or_empty(payload.get("role")),
            "tags": tags,
            "linked_quest_id": linked_quest_id,
            "area_id": area_id,
            "location_id": location_id,
            "despawn_tick": despawn_tick,
            "issued_at_tick": current_tick,
        }
        context.state.narrative_plan.add_history({
            "kind": "spawn_quest_npc",
            "npc_id": npc_id,
            "name": name,
            "appearance": appearance,
            "personality": personality,
            "dialogue_hook": dialogue_hook,
            "tags": tags,
            "linked_quest_id": linked_quest_id,
            "role": string_or_empty(payload.get("role")),
            "location_id": location_id,
            "area_id": area_id,
            "issued_at_tick": current_tick,
            "despawn_tick": despawn_tick,
        })
        context.state.narrative_plan.add_temporary_npc(npc_id, npc_profile)
        context.state.areas.move_npc(npc_id, area_id, location_id, source="planner")
        context.record_change(StateChange(
            slice="areas",
            operation="set",
            path=f"npc_location.{npc_id}",
            value=area_id,
        ))
        context.state.narrative_plan.add_directive({
            "npc_id": npc_id,
            "directive": {
                "kind": "spawn_quest_npc",
                "role": string_or_empty(payload.get("role")),
                "description": string_or_empty(payload.get("description")),
                "personality": personality,
                "dialogue_hook": dialogue_hook,
                "name": name or "",
                "area_id": area_id,
            },
            "issued_at_tick": current_tick,
            "source": "narrative_planner",
        })
        return True

    # ------------------------------------------------------------------
    # Helper: find available NPC ID
    # ------------------------------------------------------------------

    @staticmethod
    def _find_available_npc_id(
        context: SettlementContext,
        base_npc_id: str,
        *,
        max_attempts: int = 20,
    ) -> str:
        npc_id = base_npc_id
        for suffix in range(max_attempts):
            if suffix > 0:
                npc_id = f"{base_npc_id}_{suffix}"
            if context.state.areas.find_npc_area(npc_id) is None:
                return npc_id
        return f"{base_npc_id}_{max_attempts}"

    async def _evaluate_with_agent(
        self,
        event: PlannerEvent,
        context: SettlementContext,
    ) -> SubSystemResult:
        base_context = event.payload.get("planner_context", {})
        if not isinstance(base_context, Mapping):
            base_context = {}
        agent_context = dict(base_context)
        current_event = {
            "kind": event.kind,
            "tick": event.tick,
            "source": event.source,
            "emitter": event.emitter,
            "round_index": event.round_index,
            "payload": {
                key: value
                for key, value in event.payload.items()
                if key != "planner_context"
            },
        }
        agent_context["event"] = current_event
        agent_context["current_event"] = current_event
        raw = await self._agent.evaluate(agent_context)
        directives = raw.get("directives", []) if isinstance(raw, Mapping) else []
        story_facts = raw.get("story_facts", []) if isinstance(raw, Mapping) else []
        strategy_notes = (
            string_or_empty(raw.get("strategy_notes"))
            if isinstance(raw, Mapping)
            else ""
        )
        metadata = normalize_mapping(raw.get("metadata")) if isinstance(raw, Mapping) else {}
        return SubSystemResult(
            directives=list(directives) if isinstance(directives, list) else [],
            story_facts=(
                [dict(item) for item in story_facts if isinstance(item, Mapping)]
                if isinstance(story_facts, list)
                else []
            ),
            strategy_notes=strategy_notes,
            metadata=metadata,
        )
