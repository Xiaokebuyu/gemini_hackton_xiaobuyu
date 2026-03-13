"""Retire dynamic quests whose relative expiry_ticks have elapsed (A-6, S3-02)."""

from __future__ import annotations

from typing import Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command


class QuestExpiryHook(NoOpSettlementHook):
    """Check for dynamic quests past their relative expiry_ticks and retire them.

    Runs after TimeAdvanceHook (priority 70) so that the tick counter has
    already been incremented before we compare against expiry_ticks.
    """

    HOOK_PRIORITY = 72  # after TimeAdvanceHook (70), before other logic
    HOOK_NAME = "quest_expiry"

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("quests"):
            return HookResult(metadata={"status": "skipped", "reason": "no_quests_slice"})
        if not context.state.has_slice("time"):
            return HookResult(metadata={"status": "skipped", "reason": "no_time_slice"})

        current_tick = context.state.time.absolute_tick()
        expired: list[str] = []

        for qid, quest in list(context.state.quests.dynamic_quests.items()):
            if not isinstance(quest, dict):
                continue
            status = quest.get("status", "")
            if status not in {"available", "active"}:
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
            # Retire this quest via the planner command path
            cmd = Command(
                type="planner_retire_quest",
                params={"quest_id": qid, "current_tick": current_tick},
                source="narrative_planner",
            )
            result = context.execute_command(cmd)
            if result.executed:
                expired.append(qid)

        sse_events: list[SSEEvent] = [
            SSEEvent("quest_expired", {"quest_id": qid})
            for qid in expired
        ]
        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": "ok",
                "expired_count": len(expired),
                "expired_quest_ids": expired,
                "current_tick": current_tick,
            },
        )
