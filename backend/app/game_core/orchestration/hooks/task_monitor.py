"""TaskMonitorHook — auto-complete or notify dynamic quests with task_monitor conditions.

Runs at priority 57 (after QuestObjectiveTrackingHook=56, before XpAdvancementHook=58).
When all conditions in a quest's task_monitor are met:
  - on_complete="auto"   → mark quest completed + apply rewards + SSE task_monitor_completed
  - on_complete="notify" → emit SSE task_monitor_triggered only
"""

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


class TaskMonitorHook(NoOpSettlementHook):
    """Check active dynamic quests with task_monitor and auto-complete or notify.

    Priority 57 — runs after QuestObjectiveTrackingHook (56), before XpAdvancementHook (58).
    """

    HOOK_PRIORITY = 57
    HOOK_NAME = "task_monitor"

    def should_skip(
        self,
        change_log: list[StateChange],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del change_log
        del action_log
        # Always run: conditions may be satisfied outside of change_log
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("quests"):
            return HookResult(metadata={"status": "noop", "reason": "missing_quests_slice"})

        evaluator = BasicEventConditionEvaluator()
        triggered: list[str] = []
        completed: list[str] = []
        sse_events: list[SSEEvent] = []

        for quest_id, quest in list(context.state.quests.dynamic_quests.items()):
            if not isinstance(quest, dict):
                continue
            if str(quest.get("status", "")).strip().lower() != "active":
                continue
            task_monitor = quest.get("task_monitor")
            if not isinstance(task_monitor, dict):
                continue
            conditions = task_monitor.get("conditions")
            if not isinstance(conditions, list) or not conditions:
                continue

            # Evaluate all conditions; each is a dict with "type" and optional "params"
            all_met = all(
                evaluator._condition_met(context.state, _normalize_condition(cond))[0]
                for cond in conditions
                if isinstance(cond, dict) and cond.get("type")
            )
            if not all_met:
                continue

            on_complete = str(task_monitor.get("on_complete", "auto")).strip().lower()

            if on_complete == "notify":
                triggered.append(quest_id)
                sse_events.append(SSEEvent(
                    event_type="task_monitor_triggered",
                    payload={"quest_id": quest_id},
                ))
            else:
                result = context.execute_command(Command(
                    type="advance_quest",
                    params={
                        "quest_id": quest_id,
                        "quest_kind": "dynamic",
                        "to_state": "completed",
                        "claim_rewards": True,
                    },
                    source="system",
                ))
                if result.executed:
                    completed.append(quest_id)
                    sse_events.append(SSEEvent(
                        event_type="task_monitor_completed",
                        payload={"quest_id": quest_id},
                    ))
                else:
                    logger.warning(
                        "TaskMonitorHook: failed to complete %s via advance_quest: %s",
                        quest_id,
                        result.errors,
                    )

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "applied" if (completed or triggered) else "noop",
                "completed_count": len(completed),
                "triggered_count": len(triggered),
                "completed": completed,
                "triggered": triggered,
            },
        )


def _normalize_condition(cond: dict[str, Any]) -> dict[str, Any]:
    """Ensure condition has a top-level 'type' key; pass through non-type keys as params."""
    normalized: dict[str, Any] = {"type": str(cond.get("type", ""))}
    raw_params = cond.get("params")
    if isinstance(raw_params, dict):
        normalized["params"] = raw_params
    else:
        # Inline params: collect non-type, non-params keys
        inline = {k: v for k, v in cond.items() if k not in {"type", "params"}}
        if inline:
            normalized["params"] = inline
    return normalized
