"""MilestoneUnlockHook — automatically unlock downstream milestones (P1-C Phase 3).

Runs at HOOK_PRIORITY = 55 (after EventConditionHook=50, same settlement) to
ensure that when a milestone transitions to COMPLETED in the current tick, its
downstream milestones are unlocked within the same settlement so that
NarrativePlannerHook can see them on its next run.

Design reference: P1-C Phase 3 (P1-叙事推进价值链打通.md §补丁1)
Decision record: D-N25 (narrative.md)
"""

from __future__ import annotations

import logging
from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command
from app.game_core.state import StateChange

logger = logging.getLogger(__name__)


class MilestoneUnlockHook(NoOpSettlementHook):
    """Unlock downstream milestones when their prerequisites are all COMPLETED.

    Runs after EventConditionHook so that milestone state changes from
    automatic event triggers (on_trigger → advance_quest COMPLETED) are
    already reflected in state before this hook scans.

    Note: EventConditionHook's execute_command() calls _apply_delta() directly
    without record_change(), so milestone completions from that hook do NOT
    appear in change_log.  This hook therefore always runs (should_skip=False)
    and inspects state directly.
    """

    HOOK_PRIORITY = 55
    HOOK_NAME = "milestone_unlock"

    def should_skip(self, change_log: list[StateChange]) -> bool:
        del change_log
        # Always run: milestone completions from EventConditionHook bypass change_log.
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("quests"):
            return HookResult(metadata=self._noop("missing_quests_slice"))
        if not context.world.has_registry("quests"):
            return HookResult(metadata=self._noop("missing_quests_registry"))

        unlocked: list[str] = []
        sse_events: list[SSEEvent] = []

        for milestone_id, ms in context.state.quests.milestone_states.items():
            if ms.state != "COMPLETED":
                continue
            template = context.world.quests.get_milestone(milestone_id)
            if template is None:
                continue

            for next_id in template.next_milestones:
                next_ms = context.state.quests.get_milestone(next_id)
                if next_ms is None or next_ms.state != "LOCKED":
                    continue  # already unlocked or doesn't exist in state

                next_template = context.world.quests.get_milestone(next_id)
                if next_template is None:
                    continue

                # All prerequisites must be COMPLETED
                if not all(
                    context.state.quests.get_milestone_state(prereq) == "COMPLETED"
                    for prereq in next_template.prerequisites
                ):
                    continue

                # Unlock: LOCKED → AVAILABLE via advance_quest command
                cmd = Command(
                    type="advance_quest",
                    params={"quest_id": next_id, "to_state": "AVAILABLE"},
                    source="system",
                )
                result = context.execute_command(cmd)
                if result.success:
                    unlocked.append(next_id)
                    logger.debug(
                        "MilestoneUnlockHook: unlocked %s (prerequisite %s completed)",
                        next_id,
                        milestone_id,
                    )
                    sse_events.append(SSEEvent(
                        event_type="milestone_unlocked",
                        payload={"milestone_id": next_id, "unlocked_by": milestone_id},
                    ))
                else:
                    logger.warning(
                        "MilestoneUnlockHook: failed to unlock %s: %s",
                        next_id,
                        result.errors,
                    )

        status = "applied" if unlocked else "noop"
        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": status,
                "unlocked_count": len(unlocked),
                "unlocked": unlocked,
            },
        )

    @staticmethod
    def _noop(reason: str) -> dict[str, Any]:
        return {"status": "noop", "reason": reason, "unlocked_count": 0, "unlocked": []}
