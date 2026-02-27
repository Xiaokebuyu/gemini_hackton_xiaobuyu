"""NarrativePlannerHook skeleton."""

from __future__ import annotations

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning import NarrativePlanner


class NarrativePlannerHook(NoOpSettlementHook):
    HOOK_PRIORITY = 35
    HOOK_NAME = "narrative_planner"

    def __init__(self, planner: NarrativePlanner | None = None) -> None:
        self.planner = planner or NarrativePlanner()

    async def execute(self, context: SettlementContext) -> HookResult:
        directives = self.planner.plan(context)
        return HookResult(
            skip_reason="no directives" if not directives else None,
            metadata={"directive_count": len(directives), "status": "stub"},
        )
