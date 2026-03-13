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
from app.game_core.rules.models import Command

if TYPE_CHECKING:
    from app.game_core.adapters.planner_system import PlannerAgentPort
    from app.game_core.orchestration.settlement import SettlementContext

logger = logging.getLogger(__name__)


class NpcDirectorSubSystem:
    """PlannerSubSystem responsible for NPC directive and spawn directives."""

    _HANDLES: frozenset[str] = frozenset({"direct_npc", "spawn_quest_npc", "assign_capability", "revoke_capability"})

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
    ) -> bool | str:
        if kind == "direct_npc":
            return self._apply_direct_npc(payload, context, current_tick=current_tick)
        if kind == "spawn_quest_npc":
            return self._apply_spawn_quest_npc(payload, context, current_tick=current_tick)
        if kind == "assign_capability":
            return self._apply_assign_capability(payload, context, current_tick=current_tick)
        if kind == "revoke_capability":
            return self._apply_revoke_capability(payload, context, current_tick=current_tick)
        return "unsupported_kind"

    # ------------------------------------------------------------------
    # Handler: direct_npc
    # ------------------------------------------------------------------

    def _apply_direct_npc(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_direct_npc",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        if self._instance_manager is not None:
            metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
            npc_id = coerce_non_empty_string(metadata.get("npc_id"))
            stored_directive = metadata.get("stored_directive")
            if npc_id is None or not isinstance(stored_directive, Mapping):
                return "missing_npc_or_directive_in_metadata"
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
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_spawn_quest_npc",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    # ------------------------------------------------------------------
    # Handler: assign_capability
    # ------------------------------------------------------------------

    def _apply_assign_capability(
        self,
        payload: dict[str, Any],
        context: "SettlementContext",
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_assign_capability",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    # ------------------------------------------------------------------
    # Handler: revoke_capability
    # ------------------------------------------------------------------

    def _apply_revoke_capability(
        self,
        payload: dict[str, Any],
        context: "SettlementContext",
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_revoke_capability",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

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
