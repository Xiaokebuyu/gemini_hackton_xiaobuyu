"""QuestManager sub-system — quest lifecycle directives.

Handles: create_quest, publish_bulletin, retire_quest.

Decision record: D-P20b (narrative.md)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping

from app.game_core.rules.models import Command
from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.planning.utils import coerce_non_empty_string, normalize_mapping, string_or_empty

if TYPE_CHECKING:
    from app.game_core.adapters.planner_system import PlannerAgentPort
    from app.game_core.orchestration.settlement import SettlementContext
    from app.game_core.planning.subsystem import PlannerDispatcher

logger = logging.getLogger(__name__)


class QuestManagerSubSystem:
    """PlannerSubSystem responsible for quest lifecycle directives."""

    _HANDLES: frozenset[str] = frozenset({"create_quest", "publish_bulletin", "retire_quest", "update_quest", "advance_milestone"})

    def __init__(
        self,
        dispatcher: PlannerDispatcher,
        *,
        agent: PlannerAgentPort | None = None,
        sse_collector: list | None = None,
    ) -> None:
        # Used by publish_bulletin to cross-route direct_npc to NpcDirector
        self._dispatcher = dispatcher
        self._agent = agent
        self._sse_collector = sse_collector

    # ------------------------------------------------------------------
    # PlannerSubSystem protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "quest_manager"

    @property
    def handles(self) -> frozenset[str]:
        return self._HANDLES

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in {
            "bootstrap",
            "milestone_available",
            "milestone_activated",
            "milestone_completed",
            "milestone_failed",
            "quest_accepted",
            "quest_created",
            "quest_updated",
            "quest_objective_completed",
            "quest_completed",
            "quest_retired",
            "quest_expired",
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
        if kind == "create_quest":
            return self._apply_create_quest(payload, context, current_tick=current_tick)
        if kind == "publish_bulletin":
            return self._apply_publish_bulletin(payload, context, current_tick=current_tick)
        if kind == "retire_quest":
            return self._apply_retire_quest(payload, context, current_tick=current_tick)
        if kind == "update_quest":
            return self._apply_update_quest(payload, context, current_tick=current_tick)
        if kind == "advance_milestone":
            return self._apply_advance_milestone(payload, context, current_tick=current_tick)
        return "unsupported_kind"

    # ------------------------------------------------------------------
    # Handler: create_quest
    # ------------------------------------------------------------------

    def _apply_create_quest(
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
                type="planner_create_quest",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        if self._sse_collector is not None:
            from app.game_core.orchestration.models import SSEEvent

            metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
            quest_id = (
                coerce_non_empty_string(metadata.get("quest_id"))
                or coerce_non_empty_string(params.get("quest_id"))
            )
            quest_snapshot = (
                context.state.quests.get_dynamic_quest(quest_id)
                if quest_id is not None and context.state.has_slice("quests")
                else None
            )
            if isinstance(quest_snapshot, Mapping):
                status = string_or_empty(quest_snapshot.get("status"))
                title = string_or_empty(quest_snapshot.get("title"))
                summary = string_or_empty(quest_snapshot.get("summary"))
                self._sse_collector.append(SSEEvent(
                    event_type="quest_created",
                    payload={
                        "quest_id": quest_id,
                        "status": status,
                        "title": title,
                        "summary": summary,
                    },
                ))
                self._sse_collector.append(SSEEvent(
                    event_type="quest_status_changed",
                    payload={
                        "quest_id": quest_id,
                        "new_status": status,
                        "title": title,
                        "summary": summary,
                    },
                ))
        return True

    # ------------------------------------------------------------------
    # Handler: publish_bulletin
    # ------------------------------------------------------------------

    def _apply_publish_bulletin(
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
                type="planner_publish_bulletin",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
        board_id = coerce_non_empty_string(metadata.get("board_id"))
        area_id = coerce_non_empty_string(metadata.get("area_id"))
        resolved_sub_location = coerce_non_empty_string(metadata.get("sub_location"))
        quest_id = coerce_non_empty_string(metadata.get("quest_id"))
        notify_resident_npcs = bool(metadata.get("notify_resident_npcs", True))
        if board_id is None or area_id is None:
            return "missing_board_or_area_id"
        if notify_resident_npcs:
            for npc_id in self._resident_npcs_for_board(
                context=context,
                area_id=area_id,
                board_id=board_id,
                sub_location=resolved_sub_location,
            ):
                # Cross-system routing: delegate to NpcDirector via dispatcher
                self._dispatcher.apply_directive(
                    "direct_npc",
                    {
                        "npc_id": npc_id,
                        "directive": {
                            "kind": "bulletin_awareness",
                            "board_id": board_id,
                            "quest_id": quest_id,
                            "source_milestone": coerce_non_empty_string(normalize_mapping(payload.get("metadata")).get("source_milestone")),
                            "notice": string_or_empty(payload.get("title")),
                            "metadata": normalize_mapping(payload.get("metadata")),
                        },
                    },
                    context,
                    current_tick=current_tick,
                )
        return True

    # ------------------------------------------------------------------
    # Handler: retire_quest
    # ------------------------------------------------------------------

    def _apply_retire_quest(
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
                type="planner_retire_quest",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    # ------------------------------------------------------------------
    # Handler: update_quest
    # ------------------------------------------------------------------

    def _apply_update_quest(
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
                type="planner_update_quest",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        if self._sse_collector is not None:
            from app.game_core.orchestration.models import SSEEvent

            metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
            self._sse_collector.append(SSEEvent(
                event_type="quest_progress_updated",
                payload={
                    "quest_id": metadata.get("quest_id"),
                    "current_step": metadata.get("current_step"),
                    "next_steps": metadata.get("next_steps", []),
                    "hints": metadata.get("hints", []),
                },
            ))
        return True

    # ------------------------------------------------------------------
    # Helper: resident NPCs for board
    # ------------------------------------------------------------------

    @staticmethod
    def _resident_npcs_for_board(
        *,
        context: SettlementContext,
        area_id: str,
        board_id: str,
        sub_location: str | None,
    ) -> list[str]:
        if not context.world.has_registry("maps"):
            return []
        if not board_id:
            return []
        area_template = context.world.maps.get(area_id)
        if area_template is None:
            return []
        raw_sub_locations = getattr(area_template, "sub_locations", None)
        if raw_sub_locations is None and isinstance(area_template, Mapping):
            raw_sub_locations = area_template.get("sub_locations")
        if not isinstance(raw_sub_locations, Mapping):
            return []
        resolved_sub = coerce_non_empty_string(sub_location) or None
        for raw_sub_id, raw_sub in raw_sub_locations.items():
            sub_id = coerce_non_empty_string(str(raw_sub_id))
            if sub_id is None:
                continue
            if resolved_sub is not None and sub_id != resolved_sub:
                continue
            raw_interactables = getattr(raw_sub, "interactables", None)
            if raw_interactables is None and isinstance(raw_sub, Mapping):
                raw_interactables = raw_sub.get("interactables")
            if not isinstance(raw_interactables, list):
                continue
            has_board = False
            for raw_interactable in raw_interactables:
                if isinstance(raw_interactable, Mapping):
                    interactable_id = coerce_non_empty_string(raw_interactable.get("id"))
                    raw_tags = raw_interactable.get("tags", [])
                else:
                    interactable_id = coerce_non_empty_string(
                        getattr(raw_interactable, "id", None)
                    )
                    raw_tags = getattr(raw_interactable, "tags", [])
                if interactable_id != board_id:
                    continue
                if isinstance(raw_tags, list):
                    board_tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
                    if "quest_source" not in board_tags:
                        continue
                has_board = True
                break
            if not has_board:
                continue
            raw_residents = getattr(raw_sub, "resident_npcs", None)
            if raw_residents is None and isinstance(raw_sub, Mapping):
                raw_residents = raw_sub.get("resident_npcs", [])
            if not isinstance(raw_residents, list):
                continue
            return [str(npc_id) for npc_id in raw_residents if coerce_non_empty_string(npc_id)]
        return []

    # ------------------------------------------------------------------
    # Handler: advance_milestone
    # ------------------------------------------------------------------

    def _apply_advance_milestone(
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
                type="planner_advance_milestone",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        if self._sse_collector is not None:
            from app.game_core.orchestration.models import SSEEvent

            milestone_id = coerce_non_empty_string(params.get("milestone_id")) or ""
            to_state = string_or_empty(params.get("to_state")) or "COMPLETED"
            self._sse_collector.append(SSEEvent(
                event_type="milestone_advanced_by_planner",
                payload={
                    "milestone_id": milestone_id,
                    "to_state": to_state,
                    "tick": current_tick,
                },
            ))
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
