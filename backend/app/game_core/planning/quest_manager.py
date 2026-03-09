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
from app.game_core.state import StateChange

if TYPE_CHECKING:
    from app.game_core.adapters.planner_system import PlannerAgentPort
    from app.game_core.orchestration.settlement import SettlementContext
    from app.game_core.planning.subsystem import PlannerDispatcher

logger = logging.getLogger(__name__)


class QuestManagerSubSystem:
    """PlannerSubSystem responsible for quest lifecycle directives."""

    _HANDLES: frozenset[str] = frozenset({"create_quest", "publish_bulletin", "retire_quest", "update_quest"})

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
    ) -> bool:
        if kind == "create_quest":
            return self._apply_create_quest(payload, context, current_tick=current_tick)
        if kind == "publish_bulletin":
            return self._apply_publish_bulletin(payload, context, current_tick=current_tick)
        if kind == "retire_quest":
            return self._apply_retire_quest(payload, context, current_tick=current_tick)
        if kind == "update_quest":
            return self._apply_update_quest(payload, context, current_tick=current_tick)
        return False

    # ------------------------------------------------------------------
    # Handler: create_quest
    # ------------------------------------------------------------------

    def _apply_create_quest(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool:
        quest_id = coerce_non_empty_string(payload.get("quest_id"))
        if quest_id is None:
            return False
        if quest_id in context.state.quests.dynamic_quests:
            return False
        status = coerce_non_empty_string(payload.get("status")) or "available"
        metadata = normalize_mapping(payload.get("metadata"))
        raw_objectives = payload.get("objectives")
        if not isinstance(raw_objectives, list):
            raw_objectives = metadata.get("objectives")
        objectives = raw_objectives if isinstance(raw_objectives, list) else []
        raw_rewards = payload.get("rewards")
        if not isinstance(raw_rewards, Mapping):
            raw_rewards = metadata.get("rewards")
        rewards = raw_rewards if isinstance(raw_rewards, Mapping) else {}
        raw_expiry_ticks = payload.get("expiry_ticks")
        if raw_expiry_ticks is None:
            raw_expiry_ticks = metadata.get("expiry_ticks")
        expiry_ticks: int | None = None
        if raw_expiry_ticks is not None:
            try:
                expiry_ticks = int(raw_expiry_ticks)
            except (TypeError, ValueError):
                return False
        urgency = coerce_non_empty_string(metadata.get("urgency")) or "medium"
        on_expire = coerce_non_empty_string(payload.get("on_expire"))
        if on_expire is None:
            on_expire = coerce_non_empty_string(metadata.get("on_expire"))
        if on_expire is None:
            on_expire = "ignore"
        on_expire = on_expire.strip().lower()
        if on_expire not in {"ignore", "escalate", "retire"}:
            on_expire = "ignore"
        generated_raw = metadata.get("generated_by_escalation")
        try:
            generated_by_escalation = int(generated_raw)
        except (TypeError, ValueError):
            generated_by_escalation = 0
        target_milestone = coerce_non_empty_string(metadata.get("source_milestone"))
        delivery_method = (
            coerce_non_empty_string(payload.get("delivery_method"))
            or coerce_non_empty_string(metadata.get("delivery_method"))
            or "board"
        )
        quest_payload = {
            "quest_id": quest_id,
            "status": status,
            "title": string_or_empty(payload.get("title")),
            "summary": string_or_empty(payload.get("summary")),
            "source": "narrative_planner",
            "created_at_tick": current_tick,
            "target_milestone": target_milestone,
            "urgency": urgency,
            "objectives": objectives,
            "rewards": rewards,
            "delivery_method": delivery_method,
            "expiry_ticks": expiry_ticks,
            "on_expire": on_expire,
            "generated_by_escalation": generated_by_escalation,
            "planner_reasoning": string_or_empty(metadata.get("planner_reasoning")),
            "metadata": metadata,
        }
        context.state.quests.add_dynamic_quest(quest_id, quest_payload)
        self._activate_source_milestone(
            context,
            source_milestone=target_milestone,
            current_tick=current_tick,
        )
        context.state.narrative_plan.add_history(
            {"kind": "create_quest", "quest_id": quest_id, "tick": current_tick}
        )
        context.record_change(StateChange(
            slice="quests",
            operation="set",
            path=f"dynamic_quests.{quest_id}",
            value=quest_payload,
        ))
        # Map milestone success/failure conditions to EventSlice events
        self._create_milestone_condition_events(quest_id, context, current_tick=current_tick)
        self._create_objective_events(
            quest_id,
            quest_payload,
            context,
            current_tick=current_tick,
        )
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
    ) -> bool:
        board_id = coerce_non_empty_string(payload.get("board_id"))
        if board_id is None:
            return False
        if not context.state.has_slice("areas"):
            return False
        area_id = coerce_non_empty_string(payload.get("area_id"))
        if area_id is None and context.state.has_slice("player"):
            area_id = context.state.player.current_area
        if area_id is None:
            return False
        location_payload = payload.get("location")
        resolved_area_id = coerce_non_empty_string(
            location_payload.get("area_id")) if isinstance(location_payload, Mapping) else None
        resolved_sub_location = coerce_non_empty_string(
            location_payload.get("sub_location")) if isinstance(location_payload, Mapping) else None
        area_id = resolved_area_id or area_id
        if not area_id:
            return False
        metadata = normalize_mapping(payload.get("metadata"))
        quest_id = coerce_non_empty_string(metadata.get("quest_id"))
        notify_resident_npcs = bool(payload.get("notify_resident_npcs", True))
        board_entry: dict[str, Any] = {
            "board_id": board_id,
            "quest_id": quest_id or "",
            "title": string_or_empty(payload.get("title")),
            "content": string_or_empty(payload.get("content")),
            "published_at_tick": current_tick,
            "source": "narrative_planner",
            "area_id": area_id,
        }
        if resolved_sub_location is not None:
            board_entry["sub_location"] = resolved_sub_location
            board_entry["location"] = {
                "area_id": area_id,
                "sub_location": resolved_sub_location,
            }
        context.state.areas.add_board_bulletin(
            area_id,
            board_id,
            board_entry,
        )
        context.record_change(StateChange(
            slice="areas",
            operation="set",
            path=f"{area_id}.board_bulletins.{board_id}",
            value=board_entry,
        ))
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
                            "source_milestone": coerce_non_empty_string(metadata.get("source_milestone")),
                            "notice": string_or_empty(payload.get("title")),
                            "metadata": metadata,
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
    ) -> bool:
        quest_id = coerce_non_empty_string(payload.get("quest_id"))
        if quest_id is None:
            return False
        if quest_id not in context.state.quests.dynamic_quests:
            return False
        context.state.quests.retire_dynamic_quest(quest_id)
        context.state.narrative_plan.add_history(
            {"kind": "retire_quest", "quest_id": quest_id, "tick": current_tick}
        )
        context.record_change(StateChange(
            slice="quests",
            operation="set",
            path=f"dynamic_quests.{quest_id}.status",
            value="retired",
        ))

        # ---- Cascade cleanup ----

        # 1. Despawn linked temporary NPCs
        if context.state.has_slice("areas"):
            for entry in list(context.state.narrative_plan.quest_history):
                if entry.get("kind") != "spawn_quest_npc":
                    continue
                if entry.get("linked_quest_id") != quest_id:
                    continue
                npc_id = entry.get("npc_id", "")
                if not npc_id:
                    continue
                for area_state in context.state.areas.areas.values():
                    if npc_id in area_state.npc_locations:
                        area_state.npc_locations.pop(npc_id, None)
                        context.state.areas._dirty = True
                context.state.narrative_plan.remove_temporary_npc(npc_id)
                context.record_change(StateChange(
                    slice="areas",
                    operation="set",
                    path=f"npc_location.{npc_id}",
                    value=None,
                ))

            # 2. Remove linked board bulletins
            for a_id, area_state in context.state.areas.areas.items():
                for board_id in list(area_state.board_bulletins.keys()):
                    context.state.areas.remove_board_bulletin(a_id, board_id, quest_id)

            # 3. Remove linked dynamic sub-areas
            for a_id, area_state in context.state.areas.areas.items():
                for sub_area in list(area_state.temporary_sub_areas):
                    if sub_area.get("linked_quest_id") == quest_id:
                        context.state.areas.remove_temporary_sub_area(
                            a_id, sub_area.get("id", "")
                        )

        # 4. Remove linked NPC directives
        directives = context.state.narrative_plan.npc_directives
        filtered = [d for d in directives if d.get("linked_quest_id") != quest_id]
        if len(filtered) != len(directives):
            context.state.narrative_plan.npc_directives = filtered
            context.state.narrative_plan._dirty = True

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
    ) -> bool:
        quest_id = coerce_non_empty_string(payload.get("quest_id"))
        if quest_id is None:
            return False
        quest = context.state.quests.dynamic_quests.get(quest_id)
        if not isinstance(quest, dict):
            return False
        if quest.get("status") != "active":
            return False

        updated = dict(quest)
        # Incremental merge — only update fields that are provided
        current_step = payload.get("current_step")
        if isinstance(current_step, str) and current_step.strip():
            updated["current_step"] = current_step.strip()
        next_steps = payload.get("next_steps")
        if isinstance(next_steps, list):
            updated["next_steps"] = [str(s).strip() for s in next_steps if str(s).strip()]
        hints = payload.get("hints")
        if isinstance(hints, list):
            updated["hints"] = [str(h).strip() for h in hints if str(h).strip()]
        completed_objectives = payload.get("completed_objectives")
        if isinstance(completed_objectives, list):
            updated["completed_objectives"] = [str(o).strip() for o in completed_objectives if str(o).strip()]

        context.state.quests.dynamic_quests[quest_id] = updated
        context.state.quests._dirty = True
        context.record_change(StateChange(
            slice="quests",
            operation="modify",
            path=f"dynamic_quests.{quest_id}",
            value=updated,
        ))

        # SSE notification to frontend
        if self._sse_collector is not None:
            from app.game_core.orchestration.models import SSEEvent
            self._sse_collector.append(SSEEvent(
                event_type="quest_progress_updated",
                payload={
                    "quest_id": quest_id,
                    "current_step": updated.get("current_step"),
                    "next_steps": updated.get("next_steps", []),
                    "hints": updated.get("hints", []),
                },
            ))

        context.state.narrative_plan.quest_history.append({
            "type": "update_quest",
            "quest_id": quest_id,
            "tick": current_tick,
        })
        context.state.narrative_plan._dirty = True
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
    # Helper: milestone + objective event creation
    # ------------------------------------------------------------------

    def _create_milestone_condition_events(
        self,
        milestone_id: str,
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> None:
        """Create dormant EventSlice events from a MilestoneTemplate's conditions."""
        if not context.state.has_slice("events"):
            return
        if not context.world.has_registry("quests"):
            return
        milestone = context.world.quests.get_milestone(milestone_id)
        if milestone is None:
            return

        specs: list[tuple[list[Any], str]] = []
        if milestone.success_conditions:
            specs.append((list(milestone.success_conditions), "COMPLETED"))
        if milestone.failure_conditions:
            specs.append((list(milestone.failure_conditions), "FAILED"))

        for conditions, outcome_state in specs:
            prefix = "sc" if outcome_state == "COMPLETED" else "fc"
            for idx, cond in enumerate(conditions):
                event_id = f"milestone_{milestone_id}_{prefix}_{idx}"
                if context.state.events.get_event(event_id) is not None:
                    continue
                context.state.events.activate(event_id, {
                    "id": event_id,
                    "event_id": event_id,
                    "state": "dormant",
                    "status": "dormant",
                    "conditions": [{"type": cond.type, "params": dict(cond.params)}],
                    "on_trigger": [{
                        "type": "advance_quest",
                        "params": {"quest_id": milestone_id, "to_state": outcome_state},
                    }],
                    "source": "narrative_planner",
                    "milestone_id": milestone_id,
                    "created_at_tick": current_tick,
                })
                context.record_change(StateChange(
                    slice="events",
                    operation="set",
                    path=f"active_events.{event_id}",
                    value={"state": "dormant"},
                ))

    def _create_objective_events(
        self,
        quest_id: str,
        quest_payload: Mapping[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> None:
        if not context.state.has_slice("events"):
            return

        raw_objectives = quest_payload.get("objectives")
        if not isinstance(raw_objectives, list):
            return

        for idx, objective in enumerate(raw_objectives):
            if not isinstance(objective, Mapping):
                continue
            obj_type = coerce_non_empty_string(objective.get("type"))
            if obj_type is None:
                continue
            obj_type = obj_type.lower()
            if self._coerce_optional_bool(objective.get("optional")):
                continue

            condition_type = self._objective_to_condition_type(obj_type)
            if condition_type is None:
                continue
            raw_target = objective.get("target")
            params = self._objective_target_to_params(condition_type, raw_target)
            if params is None:
                continue

            event_id = f"dq_{quest_id}_obj_{idx}"
            if context.state.events.get_event(event_id) is not None:
                continue

            context.state.events.activate(event_id, {
                "id": event_id,
                "event_id": event_id,
                "state": "dormant",
                "status": "dormant",
                "conditions": [{
                    "type": condition_type,
                    "params": params,
                }],
                "on_trigger": [{
                    "type": "complete_objective",
                    "params": {
                        "quest_id": quest_id,
                        "objective_index": idx,
                    },
                }],
                "source": "narrative_planner",
                "created_at_tick": current_tick,
            })
            context.record_change(StateChange(
                slice="events",
                operation="set",
                path=f"active_events.{event_id}",
                value={"state": "dormant"},
            ))

    @staticmethod
    def _objective_to_condition_type(obj_type: str) -> str | None:
        mapping = {
            "reach_location": "location_visited",
            "location_visited": "location_visited",
            "talk_to": "npc_talked",
            "collect": "item_obtained",
            "kill": "kill_count",
        }
        return mapping.get(obj_type)

    @staticmethod
    def _objective_target_to_params(
        condition_type: str,
        target: Any,
    ) -> dict[str, Any] | None:
        if isinstance(target, Mapping):
            if condition_type in {"item_obtained", "kill_count", "talk_to", "npc_talked"}:
                return {str(key): value for key, value in target.items()}
            if condition_type in {"location_entered", "location_visited"}:
                area_id = coerce_non_empty_string(target.get("area_id"))
                location_id = coerce_non_empty_string(target.get("location_id"))
                sub_location_id = coerce_non_empty_string(target.get("sub_location_id"))
                params = {
                    "area_id": area_id,
                    "location_id": location_id,
                    "sub_location_id": sub_location_id,
                }
                if area_id is None and location_id is None:
                    return None
                return params
            return None

        if condition_type == "item_obtained":
            if isinstance(target, str):
                return {"item_id": target}
            return None
        if condition_type == "kill_count":
            if isinstance(target, str):
                return {"monster_type": target}
            return None
        if condition_type in {"talk_to", "npc_talked"}:
            if isinstance(target, str):
                return {"npc_id": target}
            return None
        if condition_type in {"location_entered", "location_visited"}:
            if isinstance(target, str):
                return {"area_id": target}
            return None
        return None

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

    def _activate_source_milestone(
        self,
        context: SettlementContext,
        *,
        source_milestone: str | None,
        current_tick: int,
    ) -> None:
        if source_milestone is None:
            return
        if context.state.quests.get_milestone_state(source_milestone) != "AVAILABLE":
            return
        result = context.execute_command(
            Command(
                type="advance_quest",
                params={
                    "quest_id": source_milestone,
                    "to_state": "ACTIVE",
                    "tick": current_tick,
                },
                source="system",
            )
        )
        if not result.executed:
            logger.debug(
                "QuestManagerSubSystem: failed to activate source milestone %r",
                source_milestone,
            )

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {"true", "1", "yes", "y", "on"}
        return False
