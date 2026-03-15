"""NarrativeWeaver sub-system — lifecycle maintenance.

Handles: directive GC, temporary NPC despawn,
and auto-escalation safety net.

Decision record: D-P20c (narrative.md)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Mapping

from app.game_core.orchestration.models import SSEEvent
from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.planning.utils import coerce_non_empty_string
from app.game_core.rules.models import Command

if TYPE_CHECKING:
    from app.game_core.adapters.planner_system import PlannerAgentPort
    from app.game_core.orchestration.settlement import SettlementContext

logger = logging.getLogger(__name__)


class NarrativeWeaverSubSystem:
    """PlannerSubSystem responsible for narrative lifecycle maintenance.

    Runs on every "tick_settlement" event via evaluate() and:
    1. Prunes consumed / expired directives from NarrativePlanSlice.
    2. Despawns temporary quest NPCs whose despawn_tick has passed.
    3. Emits an escalate directive when the auto-escalation safety net fires.
    """

    _HANDLES: ClassVar[frozenset[str]] = frozenset({"schedule_event"})  # evaluate-driven + schedule_event
    _AUTO_ESCALATION_THRESHOLDS: ClassVar[list[int]] = [4, 7, 10, 13, 16]
    _FALLBACK_ESCALATION_INTERVAL: ClassVar[int] = 6

    def __init__(
        self,
        *,
        sse_collector: list[SSEEvent] | None = None,
        agent: PlannerAgentPort | None = None,
    ) -> None:
        # Reference to Hook's _pending_sse scratch buffer.  Lifecycle operations
        # append SSE events here; Hook.execute() drains the list at the end of
        # each planning cycle.
        self._sse_collector: list[SSEEvent] = (
            sse_collector if sse_collector is not None else []
        )
        self._agent = agent

    # ------------------------------------------------------------------
    # PlannerSubSystem protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "narrative_weaver"

    @property
    def handles(self) -> frozenset[str]:
        return self._HANDLES

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in {
            "tick_settlement",
            "milestone_completed",
            "milestone_failed",
            "quest_expired",
            "relationship_stage_changed",
            "world_event_resolved",
            "stagnation_threshold_reached",
            "combat_resolved",
            "rest_completed",
        }

    async def evaluate(
        self, event: PlannerEvent, context: Any
    ) -> SubSystemResult:
        current_tick = event.tick
        directives: list[dict[str, Any]] = []

        # 1. Directive GC
        context.execute_command(
            Command(
                type="planner_prune_npc_directives",
                params={"current_tick": current_tick},
                source="narrative_planner",
            )
        )

        # 2. Dynamic quest expiry is handled by QuestExpiryHook; NarrativeWeaver
        # reacts to the resulting semantic events but no longer mutates quest state.

        # 3. Temporary NPC despawn
        self._despawn_expired_quest_npcs(context, current_tick=current_tick)

        # 4. Auto-escalation safety net
        escalate = self._check_auto_escalation(context)
        if escalate is not None:
            directives.append(escalate)

        result = SubSystemResult(directives=directives)
        if self._agent is not None:
            agent_result = await self._evaluate_with_agent(event, context)
            result.directives.extend(agent_result.directives)
            result.story_facts.extend(agent_result.story_facts)
            if agent_result.strategy_notes:
                result.strategy_notes = agent_result.strategy_notes
            if agent_result.metadata:
                result.metadata.update(agent_result.metadata)
        return result

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool | str:
        if kind == "schedule_event":
            return self._apply_schedule_event(payload, context, current_tick=current_tick)
        # NarrativeWeaver does not handle other directive kinds.
        return False

    # ------------------------------------------------------------------
    # Handler: schedule_event
    # ------------------------------------------------------------------

    def _apply_schedule_event(
        self,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="schedule_event",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True

    # ------------------------------------------------------------------
    # Dynamic quest expiry (migrated from NarrativePlannerHook)
    # ------------------------------------------------------------------

    def _expire_dynamic_quests(
        self,
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> None:
        for quest_id, quest in list(context.state.quests.dynamic_quests.items()):
            if not isinstance(quest, Mapping):
                continue
            status = str(quest.get("status", "")).strip().lower()
            if status in {"completed", "retired", "expired"}:
                continue

            raw_expiry_ticks = quest.get("expiry_ticks")
            if raw_expiry_ticks is None:
                continue
            try:
                expiry_ticks = int(raw_expiry_ticks)
            except (TypeError, ValueError):
                continue

            raw_created_at_tick = quest.get("created_at_tick")
            if raw_created_at_tick is None:
                continue
            try:
                created_at_tick = int(raw_created_at_tick)
            except (TypeError, ValueError):
                continue

            if current_tick - created_at_tick < expiry_ticks:
                continue

            on_expire = coerce_non_empty_string(quest.get("on_expire")) or "ignore"
            on_expire = on_expire.strip().lower()
            if on_expire not in {"ignore", "escalate", "retire"}:
                on_expire = "ignore"
            result = context.execute_command(
                Command(
                    type="planner_expire_dynamic_quest",
                    params={
                        "quest_id": quest_id,
                        "current_tick": current_tick,
                    },
                    source="narrative_planner",
                )
            )
            if not result.executed:
                continue
            metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
            self._sse_collector.append(SSEEvent(
                event_type="dynamic_quest_expired",
                payload={
                    "quest_id": metadata.get("quest_id", quest_id),
                    "on_expire": metadata.get("on_expire", on_expire),
                    "tick": metadata.get("tick", current_tick),
                    "status": metadata.get("status"),
                },
            ))

    # ------------------------------------------------------------------
    # Temporary NPC despawn (migrated from NarrativePlannerHook)
    # ------------------------------------------------------------------

    def _despawn_expired_quest_npcs(
        self,
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> None:
        for entry in list(context.state.narrative_plan.quest_history):
            if entry.get("kind") != "spawn_quest_npc":
                continue
            raw_despawn_tick = entry.get("despawn_tick")
            if raw_despawn_tick is None:
                continue
            try:
                despawn_tick = int(raw_despawn_tick)
            except (TypeError, ValueError):
                continue
            if current_tick < despawn_tick:
                continue

            npc_id = coerce_non_empty_string(entry.get("npc_id"))
            if npc_id is None:
                continue
            context.execute_command(
                Command(
                    type="planner_despawn_quest_npc",
                    params={"npc_id": npc_id},
                    source="narrative_planner",
                )
            )

    # ------------------------------------------------------------------
    # Auto-escalation safety net (new — design §5.2)
    # ------------------------------------------------------------------

    def _check_auto_escalation(
        self, context: SettlementContext
    ) -> dict[str, Any] | None:
        """Return an escalate directive when the safety net fires, else None."""
        np_state = context.state.narrative_plan
        if np_state.pacing_frozen:
            return None

        ticks = np_state.ticks_since_milestone_progress
        level = np_state.escalation_level

        # Pick threshold for the current escalation level (or fallback interval)
        if 0 <= level < len(self._AUTO_ESCALATION_THRESHOLDS):
            threshold = self._AUTO_ESCALATION_THRESHOLDS[level]
        else:
            threshold = self._FALLBACK_ESCALATION_INTERVAL

        if ticks >= threshold:
            return {"kind": "escalate", "payload": {"delta": 1}}
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
            str(raw.get("strategy_notes", "")) if isinstance(raw, Mapping) else ""
        )
        metadata = dict(raw.get("metadata")) if isinstance(raw, Mapping) and isinstance(raw.get("metadata"), Mapping) else {}
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
