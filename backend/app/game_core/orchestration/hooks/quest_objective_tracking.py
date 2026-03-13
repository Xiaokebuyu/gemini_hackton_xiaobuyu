"""Auto-track quest objectives with structured conditions (P25-14 Phase 2)."""

from __future__ import annotations

import logging
from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
from app.game_core.state import StateChange

logger = logging.getLogger(__name__)


class QuestObjectiveTrackingHook(NoOpSettlementHook):
    """Evaluate structured conditions on active quest objectives.

    For each active dynamic quest, checks objectives that have a structured
    ``condition`` field. When an objective's condition is satisfied, marks
    ``completed=True`` on the objective in-place. If all objectives are
    completed, advances the quest status to ``ready_to_report`` (if
    ``requires_report=True``) or ``completed``.

    Priority 56 — runs after MilestoneCompletionHook (54) and
    MilestoneUnlockHook (55) so milestone-driven quests are resolved first.
    """

    HOOK_PRIORITY = 56
    HOOK_NAME = "quest_objective_tracking"

    def should_skip(
        self,
        change_log: list[StateChange],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del change_log
        del action_log
        # Always run: conditions may become satisfied outside of change_log
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("quests"):
            return HookResult(metadata={"status": "noop", "reason": "missing_quests_slice"})

        evaluator = BasicEventConditionEvaluator()
        updated_quests: list[str] = []
        completed_objectives: int = 0
        sse_events: list[SSEEvent] = []

        for quest_id, quest in context.state.quests.dynamic_quests.items():
            if not isinstance(quest, dict):
                continue
            status = str(quest.get("status", "")).strip().lower()
            if status != "active":
                continue

            objectives = quest.get("objectives")
            if not isinstance(objectives, list):
                continue

            quest_changed = False
            all_completed = True  # assume true until a non-completed obj is found

            for obj in objectives:
                if not isinstance(obj, dict):
                    all_completed = False
                    continue
                if obj.get("completed"):
                    continue  # already marked

                condition = obj.get("condition")
                if not isinstance(condition, dict) or not condition.get("type"):
                    # No condition → cannot auto-track; leave for manual completion
                    all_completed = False
                    continue

                met, _ = evaluator._condition_met(context.state, condition)
                if met:
                    obj["completed"] = True
                    quest_changed = True
                    completed_objectives += 1
                else:
                    all_completed = False

            if quest_changed:
                context.state.quests._dirty = True
                updated_quests.append(quest_id)
                sse_events.append(SSEEvent(
                    event_type="quest_objective_updated",
                    payload={"quest_id": quest_id},
                ))

            # If all objectives are now completed, advance quest status
            if all_completed and quest_changed:
                requires_report = bool(quest.get("requires_report"))
                new_status = "ready_to_report" if requires_report else "completed"
                quest["status"] = new_status
                sse_events.append(SSEEvent(
                    event_type="quest_status_changed",
                    payload={"quest_id": quest_id, "new_status": new_status},
                ))
                # A9e: mark outline step completed when quest links to one
                quest_meta = quest.get("metadata")
                if isinstance(quest_meta, dict):
                    raw_step_index = quest_meta.get("step_index")
                    if raw_step_index is not None:
                        try:
                            step_index = int(raw_step_index)
                        except (TypeError, ValueError):
                            step_index = None
                        if step_index is not None and context.state.has_slice("narrative_plan"):
                            context.state.narrative_plan.mark_outline_step_completed(step_index)

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "applied" if updated_quests else "noop",
                "updated_quest_count": len(updated_quests),
                "completed_objective_count": completed_objectives,
                "updated_quests": updated_quests,
            },
        )
