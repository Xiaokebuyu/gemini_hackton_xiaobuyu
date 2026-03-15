"""PacingController sub-system — narrative pacing directives.

DEPRECATED: escalate and adjust_pacing handlers have been merged into
NarrativeWeaverSubSystem (D-P20b refactor, Phase 4).
PacingControllerSubSystem is retained for backward compatibility with
existing tests and will be removed in a future cleanup pass.

Decision record: D-P20b (narrative.md)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.rules.models import Command

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
    ) -> bool | str:
        if kind == "escalate":
            return self._apply_escalate(payload, context, current_tick=current_tick)
        if kind == "adjust_pacing":
            return self._apply_adjust_pacing(payload, context, current_tick=current_tick)
        return "unsupported_kind"

    # ------------------------------------------------------------------
    # Handler: escalate
    # ------------------------------------------------------------------

    def _apply_escalate(
        self,
        payload: dict[str, Any],
        context: SettlementContext,
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(Command(
            type="planner_escalate",
            params=params,
            source="narrative_planner",
        ))
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
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
    ) -> bool | str:
        del current_tick
        result = context.execute_command(Command(
            type="planner_set_pacing_frozen",
            params=dict(payload),
            source="narrative_planner",
        ))
        if not result.executed:
            return "; ".join(result.errors) if result.errors else "command_failed"
        return True
