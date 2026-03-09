"""PacingController sub-system — narrative pacing directives.

Handles: escalate, adjust_pacing.

Decision record: D-P20b (narrative.md)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.rules.models import Command
from app.game_core.state import StateChange

if TYPE_CHECKING:
    from app.game_core.orchestration.settlement import SettlementContext

logger = logging.getLogger(__name__)


class PacingControllerSubSystem:
    """PlannerSubSystem responsible for narrative pacing directives."""

    _HANDLES: frozenset[str] = frozenset({"escalate", "adjust_pacing"})

    def __init__(self) -> None:
        pass  # No external dependencies

    # ------------------------------------------------------------------
    # PlannerSubSystem protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "pacing_controller"

    @property
    def handles(self) -> frozenset[str]:
        return self._HANDLES

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in {
            "tick_settlement",
            "stagnation_threshold_reached",
            "milestone_completed",
            "milestone_failed",
            "quest_accepted",
            "quest_completed",
            "quest_expired",
            "combat_resolved",
            "rest_completed",
        }

    async def evaluate(self, event: PlannerEvent, context: Any) -> SubSystemResult:
        return SubSystemResult()

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool:
        if kind == "escalate":
            return self._apply_escalate(payload, context, current_tick=current_tick)
        if kind == "adjust_pacing":
            return self._apply_adjust_pacing(payload, context, current_tick=current_tick)
        return False

    # ------------------------------------------------------------------
    # Handler: escalate
    # ------------------------------------------------------------------

    def _apply_escalate(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool:
        delta = payload.get("delta")
        if not isinstance(delta, int) or isinstance(delta, bool):
            return False
        if delta < -3 or delta > 3:
            return False
        context.state.narrative_plan.adjust_escalation(delta)
        context.record_change(StateChange(
            slice="narrative_plan",
            operation="set",
            path="escalation_level",
            value=context.state.narrative_plan.escalation_level,
        ))
        # Persist escalation to world state: bump area danger level
        area_id = ""
        if context.state.has_slice("player"):
            area_id = context.state.player.current_area or ""
        if area_id:
            context.execute_command(Command(
                type="adjust_danger",
                params={"area_id": area_id, "delta": 0.05 * delta},
                source="system",
            ))
        # Persist escalation level as a world flag for downstream consumers
        context.execute_command(Command(
            type="set_flag",
            params={
                "key": "narrative_escalation_level",
                "value": context.state.narrative_plan.escalation_level,
            },
            source="system",
        ))
        return True

    # ------------------------------------------------------------------
    # Handler: adjust_pacing
    # ------------------------------------------------------------------

    def _apply_adjust_pacing(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool:
        del current_tick  # unused
        frozen = payload.get("frozen")
        if not isinstance(frozen, bool):
            return False
        context.state.narrative_plan.set_pacing_frozen(frozen)
        context.record_change(StateChange(
            slice="narrative_plan",
            operation="set",
            path="pacing_frozen",
            value=frozen,
        ))
        return True
