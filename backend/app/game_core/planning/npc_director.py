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

    _HANDLES: frozenset[str] = frozenset({"direct_npc", "spawn_quest_npc", "assign_capability", "revoke_capability", "assign_service", "revoke_service", "curate_shop", "create_rumor", "modify_location", "move_npc"})

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
        # Deterministic handling: generate assign_service for quest_completed
        # without requiring an LLM agent.
        if event.kind == "quest_completed":
            result = self._evaluate_quest_completed(event, context)
            if result is not None:
                return result

        if self._agent is not None:
            return await self._evaluate_with_agent(event, context)
        return SubSystemResult()

    # ------------------------------------------------------------------
    # Deterministic: quest_completed → assign_service for receptionist
    # ------------------------------------------------------------------

    def _evaluate_quest_completed(
        self,
        event: PlannerEvent,
        context: Any,
    ) -> "SubSystemResult | None":
        """Generate an assign_service directive for the receptionist NPC when a
        quest is completed.  Returns None when graceful degradation is needed
        (no quest_id, no rewards found).  Returns an empty SubSystemResult when
        the service is already assigned (idempotency guard)."""
        quest_id = coerce_non_empty_string(event.payload.get("quest_id"))
        if quest_id is None:
            return None

        # Resolve rewards from dynamic quest state or content quest registry.
        rewards = self._resolve_quest_rewards(quest_id, context)
        if not rewards:
            return None

        # Build effect atoms from rewards dict.
        effects = self._rewards_to_effects(rewards)
        if not effects:
            return None

        # Find receptionist NPC.
        receptionist_id = self._find_receptionist(context)
        if receptionist_id is None:
            logger.debug(
                "NpcDirectorSubSystem: no receptionist NPC found for quest %s; "
                "skipping reward service assignment",
                quest_id,
            )
            return None

        service_id = f"reward_{quest_id}"

        # Idempotency: do not create a duplicate service if already assigned.
        if context.state.has_slice("narrative_plan"):
            existing = context.state.narrative_plan.get_services(receptionist_id)
            if any(s.get("service_id") == service_id for s in existing):
                logger.debug(
                    "NpcDirectorSubSystem: reward service %s already assigned to %s; skipping",
                    service_id,
                    receptionist_id,
                )
                return SubSystemResult()

        directive = {
            "kind": "assign_service",
            "payload": {
                "npc_id": receptionist_id,
                "service_id": service_id,
                "label": "领取任务报酬",
                "price": 0,
                "effects": effects,
                "preconditions": {"quest_completed": quest_id},
                "one_shot": True,
                "notes": f"完成任务 {quest_id} 后可领取的报酬",
            },
        }
        return SubSystemResult(directives=[directive])

    def _resolve_quest_rewards(
        self, quest_id: str, context: Any,
    ) -> dict:
        """Return rewards dict for *quest_id*, or {} if not found.

        Checks dynamic_quests first, then content quest registry.
        """
        # Dynamic quest state
        if context.state.has_slice("quests"):
            quest = context.state.quests.get_dynamic_quest(quest_id)
            if isinstance(quest, dict):
                raw = quest.get("rewards")
                if isinstance(raw, dict) and raw:
                    return raw

        # Content layer quest registry
        if context.world.has_registry("quests"):
            milestone = context.world.quests.get(quest_id)
            if milestone is not None:
                raw = milestone.rewards
                if isinstance(raw, dict) and raw:
                    return raw

        return {}

    @staticmethod
    def _rewards_to_effects(rewards: dict) -> list[dict]:
        """Convert a rewards dict to a list of effect atoms."""
        effects: list[dict] = []
        gold = rewards.get("gold", 0)
        try:
            gold = int(gold)
        except (TypeError, ValueError):
            gold = 0
        if gold > 0:
            effects.append({"type": "modify_gold", "amount": gold})

        xp = rewards.get("xp", 0)
        try:
            xp = int(xp)
        except (TypeError, ValueError):
            xp = 0
        if xp > 0:
            effects.append({"type": "add_xp", "amount": xp})

        items = rewards.get("items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("item_id", "")
                if not isinstance(item_id, str) or not item_id.strip():
                    continue
                count = item.get("count", 1)
                try:
                    count = int(count)
                except (TypeError, ValueError):
                    count = 1
                effects.append({
                    "type": "grant_item",
                    "item_id": item_id.strip(),
                    "count": count,
                })

        return effects

    def _find_receptionist(self, context: Any) -> "str | None":
        """Return the id of the first NPC with a 'receptionist' tag, or None."""
        if not context.world.has_registry("characters"):
            return None
        for template in context.world.characters.list_all():
            if "receptionist" in (template.tags or []):
                return template.id
        return None

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
        if kind == "assign_service":
            return self._apply_assign_service(payload, context, current_tick=current_tick)
        if kind == "revoke_service":
            return self._apply_revoke_service(payload, context, current_tick=current_tick)
        if kind == "curate_shop":
            return self._apply_curate_shop(payload, context, current_tick=current_tick)
        if kind == "create_rumor":
            return self._apply_create_rumor(payload, context, current_tick=current_tick)
        if kind == "modify_location":
            return self._apply_modify_location(payload, context, current_tick=current_tick)
        if kind == "move_npc":
            return self._apply_move_npc(payload, context, current_tick=current_tick)
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

        # Write npc_goal/topic from directive into the NPC's blackboard.
        # - "goals" list: accumulates all goals across directives (for NPC's general awareness)
        # - "pending_topic": single string — the most recent planner-assigned topic the NPC
        #   should proactively bring up. Read by npc_interaction.py to inject into the system
        #   prompt; cleared after the NPC successfully speaks with the player.
        npc_id_for_bb = coerce_non_empty_string(payload.get("npc_id"))
        goal_text = None
        directive_inner = payload.get("directive")
        if isinstance(directive_inner, Mapping):
            goal_text = coerce_non_empty_string(
                directive_inner.get("topic") or directive_inner.get("npc_goal")
            )
        if goal_text is not None and npc_id_for_bb is not None and context.state.has_slice("relations"):
            bb = context.state.relations.get_blackboard(npc_id_for_bb)
            goals = list(bb.get("goals", []))
            if not isinstance(goals, list):
                goals = []
            if goal_text not in goals:
                goals.append(goal_text)
            context.state.relations.update_blackboard(
                npc_id_for_bb,
                {"goals": goals, "pending_topic": goal_text},
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

    # ------------------------------------------------------------------
    # Handler: assign_service
    # ------------------------------------------------------------------

    def _apply_assign_service(
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
                type="planner_assign_service",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    # ------------------------------------------------------------------
    # Handler: revoke_service
    # ------------------------------------------------------------------

    def _apply_revoke_service(
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
                type="planner_revoke_service",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    # ------------------------------------------------------------------
    # Handler: curate_shop
    # ------------------------------------------------------------------

    def _apply_curate_shop(
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
                type="planner_curate_shop",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    # ------------------------------------------------------------------
    # Handler: create_rumor
    # ------------------------------------------------------------------

    def _apply_create_rumor(
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
                type="create_rumor",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    # ------------------------------------------------------------------
    # Handler: modify_location
    # ------------------------------------------------------------------

    def _apply_modify_location(
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
                type="modify_location",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    def _apply_move_npc(
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
                type="planner_move_npc",
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
