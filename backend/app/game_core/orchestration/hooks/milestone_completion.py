"""Auto-complete milestones whose success_conditions are all satisfied (P25-14 Phase 1)."""

from __future__ import annotations

import logging
from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
from app.game_core.rules.models import Command
from app.game_core.state import StateChange

logger = logging.getLogger(__name__)


class MilestoneCompletionHook(NoOpSettlementHook):
    """Check ACTIVE milestones and complete those whose success_conditions are all satisfied.

    Priority 54 — runs BEFORE MilestoneUnlockHook (55) so that
    COMPLETED → cascade unlock happens in the same tick.
    """

    HOOK_PRIORITY = 54
    HOOK_NAME = "milestone_completion"

    def should_skip(
        self,
        change_log: list[StateChange],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del change_log
        del action_log
        # Always run: conditions may be met from prior ticks without change_log entry
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("quests"):
            return HookResult(metadata={"status": "noop", "reason": "missing_quests_slice"})
        if not context.world.has_registry("quests"):
            return HookResult(metadata={"status": "noop", "reason": "missing_quests_registry"})

        evaluator = BasicEventConditionEvaluator()
        completed: list[str] = []
        sse_events: list[SSEEvent] = []

        for ms_id, ms in context.state.quests.milestone_states.items():
            if ms.state != "ACTIVE":
                continue
            template = context.world.quests.get_milestone(ms_id)
            if template is None:
                continue
            conditions = getattr(template, "success_conditions", None)
            if not conditions:
                continue

            # Evaluate ALL success_conditions; convert MilestoneCondition dataclasses to dict
            all_met = all(
                evaluator._condition_met(
                    context.state,
                    _condition_to_dict(cond),
                )[0]
                for cond in conditions
                if _has_condition_type(cond)
            )
            if not all_met:
                continue

            # ACTIVE → COMPLETED via advance_quest command
            cmd = Command(
                type="advance_quest",
                params={"quest_id": ms_id, "to_state": "COMPLETED"},
                source="system",
            )
            result = context.execute_command(cmd)
            if result.executed:
                completed.append(ms_id)
                sse_events.append(SSEEvent(
                    event_type="milestone_completed",
                    payload={"milestone_id": ms_id},
                ))
            else:
                logger.warning(
                    "MilestoneCompletionHook: failed to complete %s: %s",
                    ms_id,
                    result.errors,
                )

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "applied" if completed else "noop",
                "completed_count": len(completed),
                "completed": completed,
            },
        )


def _has_condition_type(cond: Any) -> bool:
    """Return True if the condition object has a usable type field."""
    if isinstance(cond, dict):
        return bool(cond.get("type"))
    # MilestoneCondition dataclass
    return bool(getattr(cond, "type", None))


def _condition_to_dict(cond: Any) -> dict[str, Any]:
    """Convert a condition (dict or MilestoneCondition dataclass) to a dict
    compatible with BasicEventConditionEvaluator._condition_met().

    MilestoneCondition stores its params in a nested "params" sub-dict, which
    matches the format that EventEngine._condition_params() reads.
    """
    if isinstance(cond, dict):
        return cond
    # MilestoneCondition dataclass: has .type and .params (nested dict)
    result: dict[str, Any] = {"type": str(getattr(cond, "type", ""))}
    params = getattr(cond, "params", None)
    if isinstance(params, dict):
        result["params"] = params
    return result
