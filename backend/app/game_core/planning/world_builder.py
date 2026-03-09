"""WorldBuilder sub-system — environmental directive handlers.

Handles: plant_environmental, fill_area.

Decision record: D-P20b (narrative.md)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping

from app.game_core.orchestration.models import SSEEvent
from app.game_core.planning.dynamic_sub_area import DynamicSubAreaManager
from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.planning.utils import coerce_non_empty_string, string_or_empty
from app.game_core.state import StateChange

if TYPE_CHECKING:
    from app.game_core.adapters.planner_system import PlannerAgentPort
    from app.game_core.orchestration.settlement import SettlementContext

logger = logging.getLogger(__name__)


class WorldBuilderSubSystem:
    """PlannerSubSystem responsible for environmental/world-building directives."""

    _HANDLES: frozenset[str] = frozenset({"plant_environmental", "fill_area"})

    def __init__(
        self,
        *,
        sub_area_manager: DynamicSubAreaManager | None = None,
        sse_collector: list[SSEEvent] | None = None,
        agent: PlannerAgentPort | None = None,
    ) -> None:
        self._sub_area_manager = sub_area_manager
        # Reference to Hook's _pending_sse scratch buffer. Directives append SSE
        # events here; Hook.execute() drains the list at the end of each call.
        self._sse_collector: list[SSEEvent] = sse_collector if sse_collector is not None else []
        self._agent = agent

    # ------------------------------------------------------------------
    # PlannerSubSystem protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "world_builder"

    @property
    def handles(self) -> frozenset[str]:
        return self._HANDLES

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in {
            "area_entered",
            "sub_location_entered",
            "scene_changed",
            "area_sparse",
            "milestone_completed",
            "quest_created",
            "world_event_available",
            "world_event_active",
            "world_event_resolved",
            "rest_completed",
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
        if kind == "plant_environmental":
            return self._apply_plant_environmental(payload, context, current_tick=current_tick)
        if kind == "fill_area":
            return self._apply_fill_area(payload, context, current_tick=current_tick)
        return False

    # ------------------------------------------------------------------
    # Handler: plant_environmental
    # ------------------------------------------------------------------

    def _apply_plant_environmental(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool:
        area_id = coerce_non_empty_string(payload.get("area_id"))
        if area_id is None:
            return False
        if not context.state.has_slice("areas"):
            return False
        if area_id not in context.state.areas.areas:
            return False
        clue_id = coerce_non_empty_string(payload.get("clue_id"))
        if clue_id is None:
            clue_id = f"clue_{current_tick}"
        raw_dc = payload.get("dc")
        try:
            discovery_dc = int(raw_dc)
        except (TypeError, ValueError):
            discovery_dc = 12
        spec = {
            "id": clue_id,
            "label": string_or_empty(payload.get("description")),
            "description": string_or_empty(payload.get("description")),
            "type": "discovery",
            "tier": "temporary",
            "discovery_mode": (
                coerce_non_empty_string(payload.get("discovery_mode")) or "check"
            ),
            "discovery_dc": discovery_dc,
            "linked_quest_id": coerce_non_empty_string(payload.get("linked_quest_id")),
            "linked_milestone": coerce_non_empty_string(payload.get("linked_milestone")),
            "source": "narrative_planner",
            "created_at_tick": current_tick,
            "expiry_ticks": payload.get("expiry_ticks", 12),
        }
        manager = self._resolve_sub_area_manager(context)
        created = manager.create(area_id, spec)
        if created is None:
            return False
        context.record_change(
            StateChange(
                slice="areas",
                operation="set",
                path=f"{area_id}.temporary_sub_areas.{created['id']}",
                value=created,
            )
        )
        self._sse_collector.append(SSEEvent(
            event_type="environment_changed",
            payload={
                "area_id": area_id,
                "sub_area_id": created["id"],
                "sub_area_label": created.get("label", ""),
                "change_type": "plant_environmental",
            },
        ))
        context.scene_bus.add_entry({
            "source": "ENGINE",
            "content": (
                f"[ENGINE:environment_changed] New discovery point appeared:"
                f" {created.get('label', created['id'])}"
            ),
            "visibility": "system",
            "tags": ["environment_changed", "narrative_planner"],
        })
        return True

    # ------------------------------------------------------------------
    # Handler: fill_area
    # ------------------------------------------------------------------

    def _apply_fill_area(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool:
        area_id = coerce_non_empty_string(payload.get("area_id"))
        if area_id is None:
            return False
        if not context.state.has_slice("areas"):
            return False
        if area_id not in context.state.areas.areas:
            return False
        sub_area_id = coerce_non_empty_string(payload.get("id"))
        if sub_area_id is None:
            sub_area_id = f"fill_{current_tick}"
        spec = {
            "id": sub_area_id,
            "label": string_or_empty(payload.get("label")),
            "description": string_or_empty(payload.get("description")),
            "type": coerce_non_empty_string(payload.get("type")) or "visit",
            "tier": "permanent",
            "source": "narrative_planner",
            "created_at_tick": current_tick,
            "expiry_ticks": payload.get("expiry_ticks", -1),
        }
        manager = self._resolve_sub_area_manager(context)
        created = manager.create(area_id, spec)
        if created is None:
            return False
        context.record_change(
            StateChange(
                slice="areas",
                operation="set",
                path=f"{area_id}.temporary_sub_areas.{created['id']}",
                value=created,
            )
        )
        self._sse_collector.append(SSEEvent(
            event_type="environment_changed",
            payload={
                "area_id": area_id,
                "sub_area_id": created["id"],
                "sub_area_label": created.get("label", ""),
                "change_type": "fill_area",
            },
        ))
        context.scene_bus.add_entry({
            "source": "ENGINE",
            "content": (
                f"[ENGINE:environment_changed] New area location added:"
                f" {created.get('label', created['id'])}"
            ),
            "visibility": "system",
            "tags": ["environment_changed", "narrative_planner"],
        })
        return True

    # ------------------------------------------------------------------
    # Helper: resolve sub-area manager
    # ------------------------------------------------------------------

    def _resolve_sub_area_manager(self, context: SettlementContext) -> DynamicSubAreaManager:
        if self._sub_area_manager is not None:
            return self._sub_area_manager
        return DynamicSubAreaManager(context.state.areas)

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
        metadata = {}
        if isinstance(raw, Mapping):
            raw_meta = raw.get("metadata")
            if isinstance(raw_meta, Mapping):
                metadata = dict(raw_meta)
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
